"""FastAPI Web Server providing REST API and real-time WebSockets for QuantTradingBot."""
import os
import asyncio
import logging
import secrets
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import subprocess
import time
import re
import urllib.error
import urllib.request
import pandas as pd

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from core.config_loader import load_config, AppConfig
from core.constants import BotMode, Direction, TradeState
from database.models import TradeDB, SignalLogDB
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from database.database import Database
from database.repository import TradeRepository, SignalRepository, DailyStatsRepository, DecisionContextRepository
from data.downloader import DataDownloader
from data.cleaner import DataCleaner
from indicators.trend import calculate_all_trend_indicators
from indicators.momentum import calculate_all_momentum_indicators
from indicators.volatility import calculate_all_volatility_indicators
from indicators.volume import calculate_all_volume_indicators
from indicators.structure import get_market_structure
from strategy.scorer import ScoreEngine
from strategy.decision_context import build_decision_context
from strategy.sweep_strategy import SweepReversalStrategy
from monitoring.voice_assistant import VoiceAssistant
from monitoring.copilot_engine import TradingCopilot

logger = logging.getLogger(__name__)

# Erroneous trades purged by operator directive (calibrated rules fix)
EXCLUDED_POSITION_IDS = {
    1947759262, 1947760301, 1947762802, 1947764305,
    1949200419, 1949201162, 1949201416, 1949201596,
}

# Base directory for web assets
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="QuantTradingBot Web Dashboard", version="1.0.0")

@app.middleware("http")
async def add_security_and_cache_headers(request, call_next):
    response = await call_next(request)
    csp = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: ws: wss:; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' ws: wss: https: http:; "
        "frame-ancestors 'self';"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Mount static files
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Shared state
class AppState:
    def __init__(self):
        active_config_dir = "config/demo_gold" if Path("config/demo_gold/settings.yaml").exists() else "config"
        self.config: AppConfig = load_config(active_config_dir)
        # This profile has its own magic number and local journal.  It is
        # intentionally not merged into the base bot's runtime state.
        self.intraday_config: AppConfig = load_config("config/intraday") if Path("config/intraday/settings.yaml").exists() else self.config
        self.db = Database(self.config.bot.journal_path)
        self.db.create_tables()
        self.trade_repo = TradeRepository(self.db)
        self.signal_repo = SignalRepository(self.db)
        self.decision_context_repo = DecisionContextRepository(self.db)
        self.stats_repo = DailyStatsRepository(self.db)
        
        # Unified support for Intraday bot DB
        self.intraday_db = Database(self.intraday_config.bot.journal_path)
        try:
            self.intraday_db.create_tables()
        except Exception:
            pass
        self.intraday_trade_repo = TradeRepository(self.intraday_db)
        self.intraday_signal_repo = SignalRepository(self.intraday_db)
        self.intraday_decision_context_repo = DecisionContextRepository(self.intraday_db)
        
        self.connector = MT5Connector()
        self.order_mgr = OrderManager(self.connector, self.config.bot)
        
        self.downloader = DataDownloader()
        self.cleaner = DataCleaner()
        self.score_engine = ScoreEngine(self.config)
        self.sweep_strategy = SweepReversalStrategy(self.config)
        
        from ml.predictor import MLPredictor
        self.ml_predictor = MLPredictor(self.config.ml.model_path)
        
        self.copilot = TradingCopilot(trade_repo=self.trade_repo)
        self.cached_copilot: Dict[str, Any] = {}
        self.cached_copilot_time: float = 0.0

        from learning.trade_autopsy import TradeAutopsyEngine
        self.autopsy_engine = TradeAutopsyEngine()
        self.cached_learning: Dict[str, Any] = {}
        self.cached_learning_time: float = 0.0
        
        self.cached_scores: Dict[str, Any] = {}
        self.last_analysis_time: float = 0.0
        # The dashboard is a separate process; it cannot truthfully infer the
        # lifecycle of bot processes without an explicit heartbeat IPC.
        self.bot_status = "UNKNOWN"
        configured_mode = self.config.bot.mode.value.upper()
        runtime_mode = os.getenv("BOT_RUNTIME_MODE", configured_mode).upper()
        self.bot_mode = runtime_mode if runtime_mode in {"PAPER", "DEMO"} else configured_mode

state = AppState()

# Runtime cohort marker for the currently launched demo profiles. Older
# journal rows belong to previous bot generations and must not be mixed into
# current performance statistics.
FLEET_RUNTIME_FILE = Path("data/fleet_runtime.json")
FLEET_PROFILE_CONFIGS = [
    ("BOT-A", "config/bot_a_gold", "#38bdf8"),
    ("BOT-B", "config/bot_b_gold", "#34d399"),
    ("BOT-C", "config/bot_c_gold", "#c084fc"),
    ("BOT-D", "config/bot_d_gold", "#fbbf24"),
    ("BOT-E", "config/bot_e", "#fb7185"),
    ("BOT-F", "config/bot_f_gold", "#10b981"),
    ("BOT-G", "config/bot_g_alchemist", "#f59e0b"),
    ("BOT-A2", "config/bot_a2_gold", "#0ea5e9"),
    ("BOT-B2", "config/bot_b2_gold", "#10b981"),
    ("BOT-C2", "config/bot_c2_gold", "#a855f7"),
    ("BOT-D2", "config/bot_d2_gold", "#eab308"),
]
_fleet_process_cache: Dict[str, Any] = {"at": 0.0, "rows": {}}


def _fleet_runtime() -> Dict[str, Any]:
    try:
        return json.loads(FLEET_RUNTIME_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _current_run_start() -> datetime:
    marker = _fleet_runtime()
    try:
        if marker.get("started_local"):
            return datetime.fromisoformat(marker["started_local"])
        value = datetime.fromisoformat(marker["started_at"])
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    except (KeyError, TypeError, ValueError):
        # No marker means there is no defensible way to label old journal data
        # as belonging to this run.
        return datetime.max


def _as_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo:
        return value.astimezone().replace(tzinfo=None)
    return value


def _fleet_process_status() -> Dict[str, str]:
    """Discover each running bot by its exact profile, surviving supervisor restarts."""
    now = time.monotonic()
    if now - _fleet_process_cache["at"] < 5:
        return _fleet_process_cache["rows"]
    marker = _fleet_runtime()
    pids = marker.get("processes", {})
    result = {bot_id: "UNKNOWN" for bot_id, _, _ in FLEET_PROFILE_CONFIGS}
    try:
        rows = []
        try:
            import psutil
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    pname = (p.info.get('name') or '').lower()
                    if 'python' in pname:
                        cmd = ' '.join(p.info.get('cmdline') or [])
                        if 'main.py' in cmd or 'run_deriv_bot' in cmd or 'run_demo_bots' in cmd:
                            rows.append({'ProcessId': p.info['pid'], 'CommandLine': cmd})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            command = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -match 'main\\.py' -or $_.CommandLine -match 'run_deriv_bot') } | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
            )
            output = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True, text=True, timeout=3, check=False,
            ).stdout.strip()
            rows = json.loads(output) if output else []
            if isinstance(rows, dict):
                rows = [rows]
        for bot_id, config_dir, _ in FLEET_PROFILE_CONFIGS:
            try:
                profile = load_config(config_dir)
            except Exception:
                result[bot_id] = "UNKNOWN"
                continue
            if profile.bot.mode in (BotMode.DEMO, BotMode.LIVE) and not profile.bot.execution_authorized:
                # A transient startup attempt that the bot's own validation
                # gate immediately rejects is not an operationally online bot.
                result[bot_id] = "OFFLINE"
                continue
            expected_pid = str(pids.get(bot_id, ""))
            normalized_profile = config_dir.replace("\\", "/").lower()
            profile_pattern = re.compile(
                rf"(?<!\w){re.escape(normalized_profile)}(?!\w)"
            )
            matches = [
                row for row in rows
                if profile_pattern.search(str(row.get("CommandLine", "")).replace("\\", "/").lower())
            ]
            # Prefer the recorded PID when valid; otherwise any exact-profile
            # process is authoritative after an automatic supervisor restart.
            row = next((item for item in matches if str(item.get("ProcessId")) == expected_pid), None)
            result[bot_id] = "ONLINE" if row or matches else "OFFLINE"
        deriv_online = any("run_deriv_bot" in str(row.get("CommandLine", "")).lower() for row in rows)
        result["BOT-DERIV"] = "ONLINE" if deriv_online else "OFFLINE"
    except Exception as exc:
        logger.debug("Fleet process status unavailable: %s", exc)
        result["BOT-DERIV"] = "UNKNOWN"
    _fleet_process_cache.update(at=now, rows=result)
    return result


def _fleet_profiles() -> List[Dict[str, Any]]:
    statuses = _fleet_process_status()
    rows = []
    labels = {
        "BOT-A": "Alta Frecuencia", "BOT-B": "Máximo Beneficio",
        "BOT-C": "Francotirador Sniper", "BOT-D": "Control Estancamiento",
        "BOT-E": "USDJPY M15 · Confirmación H1",
        "BOT-F": "Permisivo · Liquidity 0.3 ATR · ML Shadow",
        "BOT-G": "Alchemist Puro · MSNR + CRT",
        "BOT-A2": "Alta Frecuencia · Sep 17 Recreado",
        "BOT-B2": "Máximo Beneficio · Sep 17 Recreado",
        "BOT-C2": "Francotirador Sniper · Sep 17 Recreado",
        "BOT-D2": "Control Estancamiento · Sep 17 Recreado",
    }
    for bot_id, config_dir, color in FLEET_PROFILE_CONFIGS:
        try:
            cfg = load_config(config_dir)
            symbols = list(cfg.market.symbols)
            ml_enabled = bool(getattr(cfg.ml, "enabled", True))
            shadow = bool(cfg.ml.shadow_mode or not cfg.ml.allow_active_filter)
            ml_mode = "DESACTIVADO" if not ml_enabled else ("SHADOW" if shadow else "ACTIVO")
            kronos_cfg = getattr(cfg, "kronos", None)
            kronos_enabled = bool(kronos_cfg and kronos_cfg.enabled)
            kronos_shadow = bool(kronos_cfg.shadow_mode if kronos_cfg else True)
            kronos_mode = "DESACTIVADO" if not kronos_enabled else ("SHADOW" if kronos_shadow else "ACTIVO")
            stagnation_bars = int(getattr(cfg.trade, "stagnation_exit_bars", 0) or 0)
            rows.append({
                "id": bot_id, "name": f"{bot_id.replace('BOT-', 'Bot ')} [{labels[bot_id]}]",
                "symbol": symbols[0] if symbols else "XAUUSD", "symbols": symbols,
                "timeframe": cfg.timeframes.entry, "magic": cfg.bot.magic_number,
                "profile": cfg.bot.name, "ml_mode": ml_mode,
                "ml_enabled": ml_enabled,
                "ml_shadow": shadow if ml_enabled else False,
                "min_confidence": float(cfg.ml.min_confidence) if (ml_enabled and cfg.ml.min_confidence is not None) else None,
                "kronos_mode": kronos_mode,
                "kronos_enabled": kronos_enabled,
                "kronos_model": getattr(kronos_cfg, "model_name", "NeoQuasar/Kronos-small") if kronos_enabled else None,
                "stagnation": f"Salida a {stagnation_bars} velas" if stagnation_bars else "Desactivado",
                "risk_percent": float(cfg.risk.risk_percent), "color": color,
                "daily_loss_limit": float(cfg.risk.max_daily_loss),
                "max_drawdown": float(cfg.risk.max_drawdown),
                "max_positions": int(cfg.risk.max_open_positions),
                "status": statuses.get(bot_id, "UNKNOWN"),
                "score_minimum": float(cfg.score.minimum),
                "score_min_structure": float(cfg.score.min_structure),
                "score_min_trend": float(cfg.score.min_trend),
                "break_even_r": float(cfg.trade.break_even_r),
                "partial_r": float(cfg.trade.partial_r),
                "partial_percent": float(cfg.trade.partial_percent),
                "entry_mode": str(getattr(cfg.trade.entry_mode, "value", cfg.trade.entry_mode)).lower(),
                "journal_path": cfg.bot.journal_path,
                "news_filter": bool(cfg.filters.news_filter),
                "session_filter": bool(cfg.filters.session_filter),
                "session_start": str(cfg.filters.trading_start),
                "session_end": str(cfg.filters.trading_end),
                "news_blackout_before": int(cfg.filters.news_blackout_before),
                "news_blackout_after": int(cfg.filters.news_blackout_after),
                "sweep_enabled": bool(cfg.sweep_reversal.enabled),
                "sweep_min_rr": float(cfg.sweep_reversal.min_rr),
                "liquidity_guard_enabled": bool(cfg.liquidity_guard.enabled),
                "liquidity_zone_atr": float(cfg.liquidity_guard.pdx_proximity_atr),
                "pending_expiry_hours": float(cfg.liquidity_guard.max_pending_hours),
                "allow_live_trading": bool(cfg.bot.allow_live_trading),
                "execution_authorized": bool(cfg.bot.execution_authorized),
                "fleet_category": "forex",
            })
        except Exception as exc:
            logger.warning("Cannot load active profile %s: %s", bot_id, exc)

    # -------------------------------------------------------------------------
    # DERIV SINTÉTICOS FLEET (M5 · 24/7 Spike Sniper & SMC Volatility)
    # -------------------------------------------------------------------------
    deriv_status = statuses.get("BOT-DERIV", "ONLINE")
    syn_bots = [
        ("BOT-SYN-B1000", "Boom 1000 Index", "Spike Sniper BUY · PyTorch NanoJev", "#22c55e", "BUY", "spike_sniper"),
        ("BOT-SYN-C1000", "Crash 1000 Index", "Spike Sniper SELL · PyTorch NanoJev", "#ef4444", "SELL", "spike_sniper"),
        ("BOT-SYN-B500", "Boom 500 Index", "Spike Sniper BUY · PyTorch NanoJev", "#10b981", "BUY", "spike_sniper"),
        ("BOT-SYN-C500", "Crash 500 Index", "Spike Sniper SELL · PyTorch NanoJev", "#f87171", "SELL", "spike_sniper"),
        ("BOT-SYN-V75", "Volatility 75 Index", "SMC Reversal & Trend M5", "#a855f7", "SMC", "volatility_smc"),
        ("BOT-SYN-V100", "Volatility 100 Index", "SMC Reversal & Trend M5", "#06b6d4", "SMC", "volatility_smc"),
    ]
    for syn_id, syn_sym, syn_label, syn_color, syn_dir, syn_mode in syn_bots:
        rows.append({
            "id": syn_id,
            "name": f"{syn_id.replace('BOT-SYN-', 'Bot Deriv ')} [{syn_label}]",
            "symbol": syn_sym,
            "symbols": [syn_sym],
            "timeframe": "M5",
            "magic": 26099901,
            "profile": "QuantTradingBot-DerivSynthetic",
            "fleet_category": "deriv",
            "ml_mode": "ACTIVO (PyTorch NanoJev)",
            "ml_enabled": True,
            "ml_shadow": False,
            "min_confidence": 0.65,
            "stagnation": "Time Decay 12 velas",
            "risk_percent": 0.75,
            "color": syn_color,
            "daily_loss_limit": 5.0,
            "max_drawdown": 12.0,
            "max_positions": 4,
            "pyramiding": "Hasta 4 capas M5 (0.75% c/u)",
            "status": deriv_status,
            "score_minimum": 3.0,
            "score_min_structure": 1.0,
            "score_min_trend": 1.0,
            "break_even_r": 1.0,
            "partial_r": 1.5,
            "partial_percent": 50.0,
            "entry_mode": syn_mode,
            "journal_path": "data/journal_deriv.db",
            "news_filter": False,
            "session_filter": False,
            "session_start": "00:00",
            "session_end": "24:00",
            "news_blackout_before": 0,
            "news_blackout_after": 0,
            "sweep_enabled": True,
            "sweep_min_rr": 2.0,
            "liquidity_guard_enabled": True,
            "liquidity_zone_atr": 1.5,
            "pending_expiry_hours": 2.0,
            "allow_live_trading": False,
            "execution_authorized": True,
        })
    return rows


def require_control_token(
    x_dashboard_token: Optional[str] = Header(default=None),
) -> None:
    """Protect every state-changing dashboard endpoint.

    An unset token is intentionally not permissive: the server may still show
    read-only local telemetry, but cannot close positions, change settings, or
    trigger a retrain until the operator configures DASHBOARD_API_TOKEN.
    """
    expected = os.getenv("DASHBOARD_API_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Dashboard controls are disabled: DASHBOARD_API_TOKEN is not configured.",
        )
    if not x_dashboard_token or not secrets.compare_digest(x_dashboard_token, expected):
        raise HTTPException(status_code=401, detail="Invalid dashboard control token.")


# --- Connection Manager for WebSockets ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()


# --- Helper Methods ---
_account_summary_cache = {"at": 0.0, "data": None}

def get_account_summary() -> Dict[str, Any]:
    import time
    now_m = time.monotonic()
    if _account_summary_cache["data"] and (now_m - _account_summary_cache["at"] < 2.0):
        return _account_summary_cache["data"]
    """Fetch live account details and current-run results for the demo fleet."""
    if not state.connector.is_connected():
        state.connector.initialize()

    account = state.connector.get_account_info()
    active_magics = {
        26082511, 26082512, 26082513, 26082514, 26092655, 26082516, 26082517,
        26082521, 26082522, 26082523, 26082524, 26099901,
    }
    open_positions = [
        p for p in state.connector.get_positions()
        if getattr(p, "magic", 0) in active_magics
    ]
    
    balance = account.balance if account else None
    equity = account.equity if account else None
    margin_free = account.margin_free if account else None
    margin = account.margin if account else None
    leverage = account.leverage if account else None
    server = account.server if account else None
    login = account.login if account else None

    # Drawdown calculation
    drawdown_pct = max(0.0, ((balance - equity) / balance * 100.0)) if balance and equity is not None and balance > 0 else None
    target_symbols = {
        "XAUUSD", "USDJPY",
        "Volatility 75 Index", "Volatility 100 Index",
        "Boom 1000 Index", "Crash 1000 Index",
        "Boom 500 Index", "Crash 500 Index",
    }

    # 1. Fetch today's realized closed deals directly from MT5 (authoritative broker source)
    daily_pnl = 0.0
    today_closed_count = 0
    winning_trades = 0
    losing_trades = 0
    try:
        import MetaTrader5 as mt5
        now_dt = datetime.now()
        run_start = _current_run_start()
        start_24h = max(now_dt - timedelta(hours=24), run_start)
        deals = mt5.history_deals_get(start_24h, now_dt + timedelta(days=1))
        if deals:
            closed_deals = [
                d for d in deals
                if getattr(d, "entry", 0) == getattr(mt5, "DEAL_ENTRY_OUT", 1)
                and getattr(d, "symbol", "") in target_symbols
                and getattr(d, "position_id", 0) not in EXCLUDED_POSITION_IDS
                and getattr(d, "magic", 0) in active_magics
            ]
            daily_pnl = sum(float(getattr(d, "profit", 0.0)) + float(getattr(d, "commission", 0.0)) + float(getattr(d, "swap", 0.0)) for d in closed_deals)
            today_closed_count = len(closed_deals)
            winning_trades = sum(1 for d in closed_deals if float(getattr(d, "profit", 0.0)) > 0)
            losing_trades = sum(1 for d in closed_deals if float(getattr(d, "profit", 0.0)) < 0)
    except Exception as exc:
        logger.debug("Error computing MT5 daily deals: %s", exc)

    # 2. Parallel Bot DBs fallback and cumulative metrics
    parallel_journals = [
        "data/journal_bot_a.db",
        "data/journal_bot_b.db",
        "data/journal_bot_c.db",
        "data/journal_bot_d.db",
        "data/journal_bot_e.db",
        "data/journal_bot_f.db",
        "data/journal_bot_g.db",
    ]
    run_start = _current_run_start()
    seen_tickets = set()
    all_closed_trades = []
    for j_path in parallel_journals:
        if Path(j_path).exists():
            try:
                db_inst = Database(j_path)
                with db_inst.session() as s:
                    t_list = s.query(TradeDB).filter(TradeDB.symbol.in_(target_symbols)).all()
                    for t in t_list:
                        if t.ticket and t.ticket in seen_tickets:
                            continue
                        if t.ticket:
                            seen_tickets.add(t.ticket)
                        closed_at = _as_naive_utc(t.exited_at or t.created_at)
                        if (t.state == TradeState.CLOSED.value and t.profit is not None
                                and closed_at and closed_at >= run_start):
                            all_closed_trades.append(t)
            except Exception as e:
                logger.debug("Error reading journal %s: %s", j_path, e)

    if today_closed_count == 0 and all_closed_trades:
        since_24h = datetime.now() - timedelta(hours=24)
        today_db_closed = [
            t for t in all_closed_trades
            if t.exited_at and (
                (t.exited_at.replace(tzinfo=None) if hasattr(t.exited_at, 'tzinfo') and t.exited_at.tzinfo else t.exited_at) >= since_24h
            )
        ]
        daily_pnl = sum(float(t.profit) for t in today_db_closed)
        today_closed_count = len(today_db_closed)
        winning_trades = sum(1 for t in today_db_closed if float(t.profit) > 0)
        losing_trades = sum(1 for t in today_db_closed if float(t.profit) < 0)

    cum_wins = sum(1 for t in all_closed_trades if float(t.profit) > 0)
    cum_losses = sum(1 for t in all_closed_trades if float(t.profit) < 0)
    gross_win = sum(float(t.profit) for t in all_closed_trades if float(t.profit) > 0)
    gross_loss = abs(sum(float(t.profit) for t in all_closed_trades if float(t.profit) < 0))
    cum_pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    cum_pnl = round(sum(float(t.profit) for t in all_closed_trades), 2)

    total_cum_trades = len(all_closed_trades)

    cum_wr = round((cum_wins / total_cum_trades * 100.0), 1) if total_cum_trades > 0 else None
    r_list = [float(t.profit_r) for t in all_closed_trades if t.profit_r is not None]
    avg_r = round(sum(r_list) / len(r_list), 2) if r_list else None

    total_today_count = today_closed_count
    win_rate = (winning_trades / today_closed_count * 100.0) if today_closed_count > 0 else None

    is_demo = state.bot_mode == "DEMO"
    is_paper = state.bot_mode == "PAPER"
    current_profiles = _fleet_profiles()
    profile_markets = " · ".join(f"{p['id'][-1]} {p['symbol']} {p['timeframe']}" for p in current_profiles)
    mode_desc = (
        f"Modo demo: órdenes enviadas a la cuenta demo de MT5 · {profile_markets}"
        if is_demo else f"Modo paper: simulación virtual · {profile_markets}"
    )

    return {
        "status": ("RUNNING" if all(p["status"] == "ONLINE" for p in _fleet_profiles()) else
                   "PARTIAL" if any(p["status"] == "ONLINE" for p in _fleet_profiles()) else "OFFLINE"),
        "mode": state.bot_mode,
        "is_demo": is_demo,
        "is_paper": is_paper,
        "mode_description": mode_desc,
        "mt5_connected": account is not None,
        "login": login,
        "server": server,
        "balance": round(balance, 2) if balance is not None else None,
        "equity": round(equity, 2) if equity is not None else None,
        "margin_free": round(margin_free, 2) if margin_free is not None else None,
        "margin": round(margin, 2) if margin is not None else None,
        "leverage": leverage,
        "drawdown_pct": round(drawdown_pct, 2) if drawdown_pct is not None else None,
        "daily_pnl": round(daily_pnl, 2),
        "cumulative_pnl": cum_pnl,
        "profit_factor": cum_pf,
        "cumulative_trades": total_cum_trades,
        "cumulative_wins": cum_wins,
        "cumulative_losses": cum_losses,
        "cumulative_win_rate": cum_wr,
        "avg_r": avg_r,
        "best_trade": max([float(t.profit) for t in all_closed_trades], default=0.0),
        "worst_trade": min([float(t.profit) for t in all_closed_trades], default=0.0),
        "open_positions_count": len(open_positions),
        "today_trades": total_today_count,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "consecutive_losses": 0,
        "fleet_risk_percent": {profile["id"]: profile["risk_percent"] for profile in current_profiles},
        "fleet_profiles": current_profiles,
        "score_thresholds": {
            "minimum": current_profiles[0]["score_minimum"] if current_profiles else None,
            "min_structure": current_profiles[0]["score_min_structure"] if current_profiles else None,
            "min_trend": current_profiles[0]["score_min_trend"] if current_profiles else None,
            "by_bot": {p["id"]: {"minimum": p["score_minimum"], "min_structure": p["score_min_structure"], "min_trend": p["score_min_trend"]} for p in current_profiles},
        },
        "trade_management_by_bot": {p["id"]: {
            "break_even_r": p["break_even_r"], "partial_r": p["partial_r"],
            "partial_percent": p["partial_percent"], "stagnation": p["stagnation"],
        } for p in current_profiles},
        "news_filter": _get_news_filter_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "current_run_started_at": _fleet_runtime().get("started_at"),
        "current_run_closed_trades": len(all_closed_trades),
        "legacy_history_excluded": True,
    }


_shared_news_provider = None
_shared_news_filter = None

def _get_news_filter_status() -> Dict[str, Any]:
    """Inspect news blackout and upcoming high impact events."""
    global _shared_news_provider, _shared_news_filter
    info = {
        "enabled": state.config.filters.news_filter,
        "session_filter_enabled": state.config.filters.session_filter,
        "trading_session": f"{state.config.filters.trading_start} - {state.config.filters.trading_end} {state.config.filters.timezone}",
        "profile_settings": {
            profile["id"]: {
                "enabled": profile["news_filter"],
                "session_filter_enabled": profile["session_filter"],
                "session": f"{profile['session_start']} - {profile['session_end']} UTC",
            }
            for profile in _fleet_profiles()
        },
        "in_blackout": False,
        "blackout_reason": "",
        "available": not state.config.filters.news_filter,
        "events_status": "Filtro desactivado" if not state.config.filters.news_filter else "Proveedor sin confirmar",
        "upcoming_count": 0,
        "next_event": None
    }
    if state.config.filters.news_filter:
        try:
            from filters.news_filter import ForexFactoryNewsProvider, NewsFilter
            if _shared_news_provider is None:
                _shared_news_provider = ForexFactoryNewsProvider()
                _shared_news_filter = NewsFilter(_shared_news_provider)
            provider = _shared_news_provider
            nf = _shared_news_filter
            now_utc = datetime.now(timezone.utc)
            res = nf.is_news_blackout(now_utc, "XAUUSD", state.config.filters)
            info["in_blackout"] = not res.approved
            if not res.approved and res.reasons:
                info["blackout_reason"] = res.reasons[0]
            upcoming = provider.get_upcoming_events("XAUUSD", hours_ahead=24)
            info["upcoming_count"] = len(upcoming)
            if upcoming:
                info["available"] = True
                info["events_status"] = "Eventos recibidos"
                ev = upcoming[0]
                info["next_event"] = {
                    "title": ev.get("title"),
                    "impact": ev.get("impact"),
                    "date_utc": ev.get("date_utc"),
                    "timestamp_utc": ev.get("timestamp_utc"),
                }
            else:
                info["available"] = False
                info["events_status"] = "El proveedor no devolvió eventos; disponibilidad no confirmada"
        except Exception as exc:
            info["available"] = False
            info["events_status"] = "No se pudo consultar el proveedor"
            logger.debug("Error computing news filter status: %s", exc)
    return info


def get_live_symbols_analysis() -> List[Dict[str, Any]]:
    """Compute and fetch the 5-confirmation scores for configured symbols with a 15s TTL."""
    import time
    now_ts = time.time()
    results = []

    if not state.connector.is_connected():
        state.connector.initialize()
    
    profiles = []
    for cfg_profile in _fleet_profiles():
        config_path = next((path for bot_id, path, _ in FLEET_PROFILE_CONFIGS if bot_id == cfg_profile["id"]), None)
        if not config_path:
            continue
        cfg = load_config(config_path)
        profiles.append({
            **cfg_profile,
            "config": cfg,
            "bot_id": cfg_profile["id"],
            "setup_tf": cfg.timeframes.setup,
            "higher_tf": cfg.timeframes.higher,
            "min_confidence": cfg_profile["min_confidence"],
            "is_active_bot": True,
            "bot_status": cfg_profile["status"],
        })
    
    for prof in profiles:
        symbol = prof["symbol"]
        tf = prof["timeframe"]
        setup_tf = prof["setup_tf"]
        higher_tf = prof["higher_tf"]
        # A profile can use different score/risk/filter settings from another
        # bot on the same market and timeframe. Keep both its config and cache
        # isolated so the dashboard does not display another profile's score.
        cfg = prof["config"]
        cache_key = f"{prof['bot_id']}_{symbol}_{tf}"

        state.connector.select_symbol(symbol, True)
        tick = state.connector.get_symbol_tick(symbol)
        sym_info = state.connector.get_symbol_info(symbol)

        point = sym_info.point if sym_info and sym_info.point > 0 else 0.00001
        bid = tick.bid if tick else 0.0
        ask = tick.ask if tick else 0.0
        spread_pts = round((ask - bid) / point, 1) if (ask and bid) else 0.0

        last_calc = state.cached_scores_time.get(cache_key, 0.0) if hasattr(state, "cached_scores_time") else 0.0
        score_data = state.cached_scores.get(cache_key)

        # Recalculate if no cache or if older than 15 seconds
        if not score_data or (now_ts - last_calc > 15.0):
            try:
                df_entry_raw = state.downloader.download_rates(symbol, tf, count=150)
                if df_entry_raw is not None and len(df_entry_raw) >= 50:
                    df_entry, quality = state.cleaner.clean(df_entry_raw)
                    if not quality.get("quality_ok", True):
                        continue
                    df_entry = _add_configured_indicators(df_entry, cfg)
                    setup_raw = state.downloader.download_rates(symbol, setup_tf, count=250, closed_only=True)
                    htf_raw = state.downloader.download_rates(symbol, higher_tf, count=250, closed_only=True)
                    df_setup = _clean_and_indicate(setup_raw, cfg)
                    df_htf = _clean_and_indicate(htf_raw, cfg)
                    structure = get_market_structure(
                        df_entry, df_entry['atr'], cfg.structure.swing_lookback,
                        cfg.structure.swing_confirmation, cfg.structure.bos_min_distance_atr,
                        cfg.structure.bos_close_confirmation, cfg.structure.use_bos,
                        cfg.structure.use_choch, cfg.structure.max_swing_age,
                    )
                    score_res, signal = ScoreEngine(cfg).calculate_score(
                        symbol=symbol,
                        timeframe=tf,
                        df_entry=df_entry,
                        df_setup=df_setup,
                        df_htf=df_htf,
                        structure_analysis=structure,
                        config=cfg
                    )
                    if score_res:
                        ml_prob = None
                        if hasattr(state, "ml_predictor") and state.ml_predictor.is_ready:
                            try:
                                p = state.ml_predictor.predict_probability(
                                    df=df_entry,
                                    structure_score=score_res.structure.score,
                                    trend_score=score_res.trend.score,
                                    spread_points=spread_pts,
                                    atr_value=float(df_entry['atr'].iloc[-1]) if 'atr' in df_entry.columns else 1.0
                                )
                                if p is not None:
                                    ml_prob = round(p * 100.0, 1)
                            except Exception:
                                pass

                        sweep_info = {
                            "detected": False,
                            "level": None,
                            "direction": None,
                            "score": 0.0,
                            "penetration_atr": 0.0,
                            "rr": 0.0,
                            "entry_price": 0.0,
                            "sl_price": 0.0,
                            "tp_price": 0.0,
                        }
                        try:
                            ctx = build_decision_context(
                                df_entry, structure, Direction.BUY,
                                min_atr_ratio=cfg.volatility.min_atr_ratio,
                                max_atr_ratio=cfg.volatility.max_atr_ratio,
                                adx_min=cfg.trend.adx_min,
                                equal_level_tolerance_atr=cfg.decision_context.equal_level_tolerance_atr,
                                fib_zone_atr=cfg.decision_context.fib_zone_atr,
                                smc_max_age_bars=cfg.decision_context.smc_max_age_bars,
                                smc_min_gap_atr=cfg.decision_context.smc_min_gap_atr,
                                sweep_min_penetration_atr=cfg.decision_context.sweep_min_penetration_atr,
                                min_bb_width_ratio=cfg.regime_filter.min_bb_width_ratio,
                                max_range_bb_width_ratio=cfg.regime_filter.max_range_bb_width_ratio,
                            )
                            sweep_sig = SweepReversalStrategy(cfg).detect_sweep_reversal(
                                    symbol=symbol,
                                    timeframe=tf,
                                    df=df_entry,
                                    decision_context=ctx,
                                    digits=sym_info.digits if sym_info else 2,
                            )
                            if sweep_sig is not None:
                                sweep_info = {
                                        "detected": True,
                                        "level": sweep_sig.level_name,
                                        "direction": sweep_sig.direction.value,
                                        "score": 90.0,
                                        "penetration_atr": round(float(sweep_sig.penetration_atr), 2),
                                        "rr": round(float(sweep_sig.risk_reward), 2),
                                        "entry_price": sweep_sig.entry_price,
                                        "sl_price": sweep_sig.sl_price,
                                        "tp_price": sweep_sig.tp_price,
                                    }
                        except Exception as ex:
                            logger.debug(f"Sweep check error: {ex}")

                        score_data = {
                            "direction": score_res.direction.value,
                            "total_score": score_res.total_score,
                            "classification": score_res.classification.value,
                            "structure": score_res.structure.score,
                            "trend": score_res.trend.score,
                            "momentum": score_res.momentum.score,
                            "volume": score_res.volume.score,
                            "volatility": score_res.volatility.score,
                            "is_tradeable": bool(
                                score_res.total_score >= cfg.score.minimum and
                                score_res.structure.score >= cfg.score.min_structure and
                                score_res.trend.score >= cfg.score.min_trend
                            ),
                            "ml_probability": ml_prob,
                            "sweep": sweep_info,
                        }
                        state.cached_scores[cache_key] = score_data
                        if not hasattr(state, "cached_scores_time"):
                            state.cached_scores_time = {}
                        state.cached_scores_time[cache_key] = now_ts
            except Exception as e:
                logger.debug(f"Score calculation error for {symbol} {tf}: {e}")

        if not score_data:
            score_data = {
                "direction": "NEUTRAL",
                "total_score": 0.0,
                "classification": "VERY_WEAK",
                "structure": 0.0,
                "trend": 0.0,
                "momentum": 0.0,
                "volume": 0.0,
                "volatility": 0.0,
                "is_tradeable": False,
                "sweep": {
                    "detected": False,
                    "level": None,
                    "direction": None,
                    "score": 0.0,
                    "penetration_atr": 0.0,
                    "rr": 0.0,
                    "entry_price": 0.0,
                    "sl_price": 0.0,
                    "tp_price": 0.0,
                },
            }

        base_tradeable = score_data.get("is_tradeable", False)
        ml_enabled = prof.get("ml_enabled", True)
        ml_prob = score_data.get("ml_probability") if ml_enabled else None
        min_conf = prof.get("min_confidence")
        bot_sweep = score_data.get("sweep", {})

        if ml_enabled and min_conf is not None and not prof.get("ml_shadow", False):
            is_bot_tradeable = base_tradeable and ml_prob is not None and ml_prob >= (min_conf * 100.0)
        else:
            is_bot_tradeable = base_tradeable

        bot_magic = prof.get("magic", 26082511)
        open_pos_for_bot = []
        if state.connector.is_connected():
            try:
                open_pos_for_bot = [p for p in state.connector.get_positions(symbol=symbol) if getattr(p, "magic", None) == bot_magic]
            except Exception:
                pass
        has_pos = len(open_pos_for_bot) > 0
        pos_dir = ("BUY" if open_pos_for_bot[0].type == 0 else "SELL") if has_pos else None
        pos_profit = float(open_pos_for_bot[0].profit) if has_pos else 0.0

        if has_pos:
            ready_label = f"EN TRADE ({pos_dir} {pos_profit:+.2f}$)"
        elif bot_sweep.get("detected"):
            ready_label = f"RADAR: SWEEP ({bot_sweep['direction']} {bot_sweep['level']})"
        elif prof.get("bot_id") == "BOT-G":
            ready_label = "ALCHEMIST PURO (MSNR + CRT ACTIVO)"
        elif is_bot_tradeable:
            ready_label = "RADAR SUPERA UMBRAL (no confirma orden)"
        elif ml_enabled and base_tradeable and not is_bot_tradeable:
            ready_label = ("RADAR ML SIN DATO" if ml_prob is None else
                           f"RADAR ML BAJO UMBRAL (< {int(min_conf * 100)}%)")
        else:
            ready_label = f"RADAR BAJO UMBRAL ({cfg.score.minimum})"

        results.append({
            "bot_id": prof.get("bot_id", "BOT-A"),
            "symbol": symbol,
            "timeframe": tf,
            "label": prof["name"],
            "name": prof["name"],
            "magic": prof.get("magic", 26082511),
            "min_confidence": min_conf,
            "ml_mode": prof.get("ml_mode"),
            "ml_enabled": ml_enabled,
            "stagnation": prof.get("stagnation", "Dejar correr"),
            "color": prof.get("color", "#38bdf8"),
            "liquidity_guard_enabled": prof.get("liquidity_guard_enabled", False),
            "liquidity_zone_atr": prof.get("liquidity_zone_atr"),
            "pending_expiry_hours": prof.get("pending_expiry_hours"),
            "score_minimum": prof.get("score_minimum", cfg.score.minimum),
            "entry_mode": prof.get("entry_mode", "breakout"),
            "is_active_bot": prof.get("is_active_bot", True),
            "bot_status": prof.get("bot_status", "ONLINE"),
            "bid": bid,
            "ask": ask,
            "spread_points": spread_pts,
            "digits": sym_info.digits if sym_info else 5,
            **score_data,
            "ml_probability": ml_prob,
            "is_tradeable": is_bot_tradeable,
            "ready_label": ready_label,
            "analysis_source": "Dashboard market-score preview; no internal bot decision telemetry",
        })

    return results


def _add_configured_indicators(df, cfg):
    df = calculate_all_trend_indicators(df, cfg.trend.ema_fast, cfg.trend.ema_medium, cfg.trend.ema_slow, cfg.trend.adx_period)
    df = calculate_all_momentum_indicators(df, cfg.momentum.rsi_period, cfg.momentum.macd_fast, cfg.momentum.macd_slow, cfg.momentum.macd_signal)
    df = calculate_all_volatility_indicators(df, cfg.volatility.atr_period, cfg.volatility.atr_avg_period)
    volume_col = "real_volume" if str(getattr(cfg.volume.volume_mode, "value", cfg.volume.volume_mode)).lower() == "real" else "tick_volume"
    return calculate_all_volume_indicators(df, volume_period=cfg.volume.period, volume_col=volume_col, use_obv=cfg.volume.use_obv)


def _clean_and_indicate(raw, cfg):
    if raw is None or len(raw) < 50:
        return None
    df, quality = state.cleaner.clean(raw)
    if not quality.get("quality_ok", True):
        return None
    return _add_configured_indicators(df, cfg)


def get_active_positions() -> List[Dict[str, Any]]:
    """Fetch active MT5 positions and open orders with strict field mappings."""
    if not state.connector.is_connected():
        state.connector.initialize()

    # Magic number -> label mapping for all bots
    MAGIC_LABELS = {
        26082498: "GOLD (H4)",
        26082499: "GOLD (H1)",
        26082501: "BTC (H1)",
        26082502: "BTC (H4)",
        26082511: "BOT A (Alta Frecuencia)",
        26082512: "BOT B (Máximo Beneficio)",
        26082513: "BOT C (Sniper >= 0.65)",
        26082514: "BOT D (Control Estancamiento)",
        26092655: "BOT E (USDJPY M15)",
        26082516: "BOT F (Permisivo Liquidity)",
        26082517: "BOT G (Alchemist MSNR+CRT)",
        26082521: "BOT A2 (Sep 17 Recreado)",
        26082522: "BOT B2 (Sep 17 Recreado)",
        26082523: "BOT C2 (Sep 17 Recreado)",
        26082524: "BOT D2 (Sep 17 Recreado)",
        26099901: "BOT DERIV (Sintéticos)",
    }
    # Also include dynamically configured magics
    if hasattr(state, 'config') and state.config:
        MAGIC_LABELS[state.config.bot.magic_number] = f"GOLD ({state.config.timeframes.entry})"
    if hasattr(state, 'intraday_config') and state.intraday_config:
        MAGIC_LABELS[state.intraday_config.bot.magic_number] = "INTRADAY (M5)"

    valid_magics = set(MAGIC_LABELS.keys())

    # Get positions across the configured fleet, including non-gold symbols.
    positions = state.connector.get_positions()
    results = []
    
    for pos in positions:
        magic = getattr(pos, "magic", 0)
        if magic not in valid_magics:
            continue
        direction = "BUY" if pos.type == 0 else "SELL"
        bot_label = MAGIC_LABELS.get(magic, f"BOT ({magic})")
        results.append({
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "direction": direction,
            "volume": pos.volume,
            "price_open": pos.price_open,
            "price_current": pos.price_current,
            "sl": pos.sl,
            "tp": pos.tp,
            "profit": round(pos.profit, 2),
            "swap": pos.swap,
            "comment": f"[{bot_label}] {pos.comment or ''}".strip(),
            "time": datetime.fromtimestamp(pos.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        })

    # Also fetch pending orders (Buy Stop / Sell Stop breakout orders).
    try:
        pending = state.connector.get_pending_orders()
        for order in pending:
            magic = getattr(order, "magic", 0)
            if magic not in valid_magics:
                continue
            direction = "BUY STOP" if getattr(order, "type", 0) == 4 else "SELL STOP" if getattr(order, "type", 0) == 5 else f"ORDER({getattr(order, 'type', '?')})"
            bot_label = MAGIC_LABELS.get(magic, f"BOT ({magic})")
            results.append({
                "ticket": getattr(order, "ticket", 0),
                "symbol": getattr(order, "symbol", ""),
                "direction": direction,
                "volume": getattr(order, "volume_current", getattr(order, "volume_initial", 0)),
                "price_open": getattr(order, "price_open", 0),
                "price_current": getattr(order, "price_current", 0),
                "sl": getattr(order, "sl", 0),
                "tp": getattr(order, "tp", 0),
                "profit": 0.0,
                "swap": 0.0,
                "comment": f"[{bot_label} PENDING] {getattr(order, 'comment', '')}".strip(),
                "time": datetime.fromtimestamp(getattr(order, "time_setup", 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if getattr(order, "time_setup", 0) > 0 else ""
            })
    except Exception:
        pass  # Pending orders are optional

    return results


def get_copilot_radar_summary(target_symbol: Optional[str] = None) -> Dict[str, Any]:
    """Compute and cache the support/resistance radar and trade copilot advice."""
    import time
    now_ts = time.time()

    if not state.connector.is_connected():
        state.connector.initialize()

    active_positions = get_active_positions()

    # Prioritize: explicitly requested symbol -> symbol of first active position -> XAUUSD default
    if target_symbol:
        symbol = str(target_symbol).upper()
    elif active_positions:
        # Find active position (prefer filled market positions over pending orders)
        market_pos = [p for p in active_positions if "PENDING" not in str(p.get("comment", ""))]
        first_p = market_pos[0] if market_pos else active_positions[0]
        symbol = str(first_p.get("symbol", "XAUUSD")).upper()
    else:
        symbol = "XAUUSD"

    cache_key = f"{symbol}_{len(active_positions)}"
    last_key = getattr(state, "cached_copilot_key", None)
    if state.cached_copilot and last_key == cache_key and (now_ts - getattr(state, "cached_copilot_time", 0.0) < 3.0):
        return state.cached_copilot

    tf = "H1"

    state.connector.select_symbol(symbol, True)
    tick = state.connector.get_symbol_tick(symbol)
    sym_info = state.connector.get_symbol_info(symbol)

    point = sym_info.point if sym_info and sym_info.point > 0 else 0.01
    digits = sym_info.digits if sym_info else 2
    current_price = tick.bid if tick else (77000.0 if "BTC" in symbol else 4330.0)

    df_entry = None
    structure = None
    try:
        raw = state.downloader.download_rates(symbol, tf, count=150, closed_only=False)
        if raw is not None and len(raw) >= 50:
            df, quality = state.cleaner.clean(raw)
            if quality.get("quality_ok", True):
                df_entry = _add_configured_indicators(df, state.config)
                structure = get_market_structure(
                    df_entry, df_entry['atr'], state.config.structure.swing_lookback,
                    state.config.structure.swing_confirmation, state.config.structure.bos_min_distance_atr,
                    state.config.structure.bos_close_confirmation, state.config.structure.use_bos,
                    state.config.structure.use_choch, state.config.structure.max_swing_age,
                )
    except Exception as exc:
        logger.debug(f"Error extracting structure for copilot ({symbol}): {exc}")

    snapshot = state.copilot.build_complete_snapshot(
        symbol=symbol,
        current_price=current_price,
        df_entry=df_entry if df_entry is not None else pd.DataFrame(),
        structure=structure,
        active_positions=active_positions,
        point=point,
        digits=digits,
    )

    state.cached_copilot = snapshot
    state.cached_copilot_key = cache_key
    state.cached_copilot_time = now_ts
    return snapshot


def get_intraday_dashboard() -> Dict[str, Any]:
    """Return a read-only, isolated view of the M5 profile.

    The account is shared with MT5, but signals, journal counts and positions
    are scoped to the intraday database/magic number. This prevents its
    exploratory telemetry from being mistaken for baseline bot performance.
    """
    from database.models import SignalLogDB, TradeDB, DecisionContextDB

    cfg = state.intraday_config
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    signals_today: List[Any] = []
    closed_today: List[Any] = []
    contexts_by_signal: Dict[str, Any] = {}
    try:
        with state.intraday_db.session() as session:
            signals_today = session.query(SignalLogDB).filter(
                SignalLogDB.created_at >= today_start
            ).order_by(SignalLogDB.created_at.desc()).all()
            closed_today = session.query(TradeDB).filter(
                TradeDB.created_at >= today_start,
                TradeDB.profit.isnot(None),
            ).all()
            contexts_by_signal = {
                item.signal_id: item
                for item in session.query(DecisionContextDB).filter(
                    DecisionContextDB.created_at >= today_start
                ).all()
            }
    except Exception as exc:
        logger.warning("Could not read intraday dashboard journal: %s", exc)

    if not state.connector.is_connected():
        state.connector.initialize()
    positions = state.connector.get_positions(magic=cfg.bot.magic_number)
    trades_today = len(closed_today)
    winners = sum(1 for trade in closed_today if (trade.profit or 0) > 0)
    losers = sum(1 for trade in closed_today if (trade.profit or 0) < 0)
    realized_pnl = round(sum(float(trade.profit or 0.0) for trade in closed_today), 2)
    trade_signal_ids = {trade.signal_id for trade in closed_today}
    trade_signal_ids.update(trade.signal_id for trade in state.intraday_trade_repo.get_open_trades())
    rejected = sum(_signal_status(signal, signal.signal_id in trade_signal_ids) == "REJECTED" for signal in signals_today)
    latest_signal = signals_today[0] if signals_today else None
    recent = []
    for signal in signals_today[:6]:
        item = contexts_by_signal.get(signal.signal_id)
        payload = item.context if item and isinstance(item.context, dict) else {}
        nearest = payload.get("liquidity", {}).get("nearest_level") or {}
        fibonacci = payload.get("fibonacci", {})
        recent.append({
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "direction": signal.direction,
            "score": round(float(signal.total_score), 1),
            "traded": bool(signal.was_traded),
            "execution_status": _signal_status(signal, signal.signal_id in trade_signal_ids),
            "reason": signal.rejection_reasons or "",
            "created_at": signal.created_at.isoformat() if signal.created_at else None,
            "market_context": {
                "regime": item.regime if item else None,
                "session": item.session if item else None,
                "nearest_liquidity": nearest.get("name"),
                "fibonacci_zone": bool(fibonacci.get("in_zone", False)),
            },
        })
    validation = cfg.validation
    return {
        "profile": {
            "name": cfg.bot.name,
            "mode": cfg.bot.mode.value.upper(),
            "live_trading_allowed": cfg.bot.allow_live_trading,
            "magic_number": cfg.bot.magic_number,
            "timeframes": {
                "entry": cfg.timeframes.entry,
                "setup": cfg.timeframes.setup,
                "higher": cfg.timeframes.higher,
            },
            "symbols": cfg.market.symbols,
            "score_minimum": cfg.score.minimum,
            "setup_trend_minimum": cfg.confirmation.setup_min_trend_score,
            "risk_per_trade": cfg.risk.max_risk_per_trade,
            "max_daily_loss": cfg.risk.max_daily_loss,
            "max_drawdown": cfg.risk.max_drawdown,
            "max_open_positions": cfg.risk.max_open_positions,
            "target_daily_trades": cfg.telemetry.target_daily_trades,
            "ml_shadow_mode": cfg.ml.shadow_mode or not cfg.ml.allow_active_filter,
            "decision_context_shadow_mode": cfg.decision_context.shadow_mode,
        },
        "validation": {
            "status": "NOT_IN_CURRENT_FLEET",
            "historical_status": validation.status,
            "summary": f"Perfil intradía separado de A-E. Validación histórica ({validation.status}): {validation.summary}",
            "experiment_id": validation.experiment_id,
            "exploratory_trades": validation.exploratory_trades,
            "exploratory_total_r": validation.exploratory_total_r,
        },
        "today": {
            "signals": len(signals_today),
            "rejected": rejected,
            "trades": trades_today,
            "winners": winners,
            "losers": losers,
            "realized_pnl": realized_pnl,
            "open_positions": len(positions),
            "recent_signals": recent,
            "journal_state": "ACTIVO" if latest_signal else "SIN TELEMETRÍA",
        },
        "timestamp": now.isoformat(),
    }


def _signal_status(signal, has_broker_trade: Optional[bool] = None) -> str:
    """Expose signal lifecycle without calling a PAPER candidate a trade."""
    if has_broker_trade is True:
        return "EXECUTED_MT5"
    if bool(signal.was_traded):
        # Historical logs from old PAPER versions used this field for a
        # candidate. Do not rebrand that evidence as a broker fill.
        return "UNRECONCILED_ROUTED"
    reason = str(getattr(signal, "rejection_reasons", "") or "")
    if "PAPER_CANDIDATE_NOT_ROUTED" in reason:
        return "PAPER_CANDIDATE"
    if "PENDING_PLACED" in reason:
        return "PENDING_MT5"
    if "PENDING_EXECUTION" in reason:
        return "ROUTING_PENDING"
    return "REJECTED"


def _voice_signal_rows(limit: int = 80) -> List[Dict[str, Any]]:
    """Read the latest audit rows without making an HTTP call to ourselves."""
    from database.models import SignalLogDB, TradeDB

    result: List[Dict[str, Any]] = []
    for db, label in ((state.db, "CONSERVATIVE"), (state.intraday_db, "INTRADAY")):
        try:
            with db.session() as session:
                rows = session.query(SignalLogDB).order_by(SignalLogDB.id.desc()).limit(limit).all()
                ids = [row.signal_id for row in rows]
                trade_ids = {
                    item[0] for item in session.query(TradeDB.signal_id).filter(
                        TradeDB.signal_id.in_(ids)
                    ).all()
                } if ids else set()
                result.extend({
                    "signal_id": row.signal_id,
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "direction": row.direction,
                    "total_score": row.total_score,
                    "rejection_reasons": row.rejection_reasons,
                    "execution_status": _signal_status(row, row.signal_id in trade_ids),
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                    "bot_type": label,
                } for row in rows)
        except Exception as exc:
            logger.warning("Voice audit read failed for %s: %s", label, exc)
    return sorted(result, key=lambda item: item["created_at"], reverse=True)[:limit]


def _optional_voice_ai_answer(question: str, snapshot: Dict[str, Any]) -> Optional[str]:
    """Optional natural-language layer; it receives facts, never control tools."""
    api_key = os.getenv("OPENAI_API_KEY")
    enabled = os.getenv("VOICE_ASSISTANT_USE_OPENAI", "false").lower() == "true"
    if not api_key or not enabled:
        return None
    payload = {
        "model": os.getenv("VOICE_ASSISTANT_MODEL", "gpt-5-mini"),
        "store": False,
        "instructions": (
            "Eres el asistente de voz de un bot de trading en PAPER. Responde en español, "
            "con precisión y brevedad, exclusivamente usando el snapshot adjunto. No inventes precios, "
            "noticias, resultados ni operaciones. Nunca des instrucciones para abrir, cerrar o modificar "
            "operaciones; explica que las decisiones de ejecución permanecen bloqueadas por seguridad."
        ),
        "input": f"Pregunta del operador: {question}\n\nSnapshot verificado:\n{json.dumps(snapshot, ensure_ascii=False)}",
        "max_output_tokens": 250,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        direct = str(body.get("output_text") or "").strip()
        if direct:
            return direct
        for item in body.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return str(content["text"]).strip()
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        logger.warning("Optional voice AI answer unavailable: %s", exc)
    return None


_ml_status_cache = {"at": 0.0, "data": None}


def get_ml_status() -> Dict[str, Any]:
    """Get ML Meta-Filter metadata, training metrics, and status."""
    now_m = time.monotonic()
    if _ml_status_cache["data"] and (now_m - _ml_status_cache["at"] < 5.0):
        return _ml_status_cache["data"]
    fleet = _fleet_profiles()
    modes = {item["ml_mode"] for item in fleet}
    shadow_mode = modes == {"SHADOW"}
    is_ready = bool(hasattr(state, "ml_predictor") and state.ml_predictor.is_ready)
    report_data = {}

    res = {
        "enabled": any(load_config(path).ml.enabled for _, path, _ in FLEET_PROFILE_CONFIGS),
        "is_ready": is_ready,
        "shadow_mode": shadow_mode,
        "active_filter_locked": all(load_config(path).ml.shadow_mode or not load_config(path).ml.allow_active_filter for _, path, _ in FLEET_PROFILE_CONFIGS),
        "status_label": next(iter(modes)) if len(modes) == 1 else "MIXTO POR BOT",
        "min_confidence": None,
        "model_path": None,
        "avg_auc_roc": None,
        "avg_precision": None,
        "avg_accuracy": None,
        "avg_f1": None,
        "dataset_rows": None,
        "positive_samples": None,
        "negative_samples": None,
        "n_folds": None,
        "fold_metrics": [],
        "feature_importance": [],
        "features_used": [],
        "profiles": [{"id": p["id"], "mode": p["ml_mode"], "min_confidence": p["min_confidence"]} for p in fleet],
        "metrics_status": "Sin métricas verificadas para la versión actual",
    }
    _ml_status_cache.update(at=now_m, data=res)
    return res


# --- REST API Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the single-page application dashboard."""
    template_path = TEMPLATES_DIR / "index.html"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Dynamic cache-busting timestamp
            ts = int(time.time())
            content = re.sub(r'app\.js\?v=[^\s"\'<>]+', f'app.js?v=16.4.0_{ts}', content)
            return HTMLResponse(
                content=content,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )
    return HTMLResponse(content="<h1>Dashboard template not found</h1>", status_code=404)


@app.get("/api/status")
def api_status():
    """Get account metrics and bot status."""
    return get_account_summary()


@app.get("/api/bots/fleet")
def api_bots_fleet():
    """Get active multi-bot fleet configuration and status."""
    fleet = _fleet_profiles()
    started_at = _fleet_runtime().get("started_at")
    return {
        "fleet": fleet,
        "count": len(fleet),
        "started_at": started_at,
        "cohort": "demo_current",
        "drift_guard": "Sin guard anti-drift por monto; controles de liquidez por perfil",
        "news_guard": "Por perfil",
    }




@app.get("/api/bots/proximity")
def api_bots_proximity():
    """Get live trade readiness and proximity radar across all bots (Deriv & Forex)."""
    try:
        from monitoring.proximity_radar import get_proximity_radar
        return get_proximity_radar()
    except Exception as exc:
        logger.error(f"Error in proximity radar endpoint: {exc}")
        return {"status": "error", "error": str(exc), "ranking": []}

@app.get("/api/news/calendar")
async def api_news_calendar():
    """Get upcoming high-impact economic events and blackout status."""
    try:
        from filters.news_filter import ForexFactoryNewsProvider, NewsFilter
        provider = ForexFactoryNewsProvider()
        nf = NewsFilter(provider)
        now_utc = datetime.now(timezone.utc)
        res = nf.is_news_blackout(now_utc, "XAUUSD", state.config.filters)
        events = provider.get_upcoming_events("XAUUSD", hours_ahead=48)
        
        clean_events = []
        for e in events:
            clean_events.append({
                "title": e.get("title"),
                "country": e.get("country"),
                "impact": e.get("impact"),
                "date_utc": e.get("date_utc"),
                "timestamp_utc": e.get("timestamp_utc"),
                "forecast": e.get("forecast"),
                "previous": e.get("previous"),
            })
            
        return {
            "status": "success" if clean_events else "empty",
            "events_status": "Eventos recibidos" if clean_events else "El proveedor no devolvió eventos; disponibilidad no confirmada",
            "news_filter_enabled": state.config.filters.news_filter,
            "session_filter_enabled": state.config.filters.session_filter,
            "trading_session": f"{state.config.filters.trading_start} - {state.config.filters.trading_end} {state.config.filters.timezone}",
            "in_blackout": not res.approved,
            "blackout_reason": res.reasons[0] if not res.approved and res.reasons else "",
            "blackout_window_minutes": state.config.filters.news_blackout_before,
            "events": clean_events
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "events": []}



class VoiceQuery(BaseModel):
    text: str


@app.get("/api/voice/status")
async def api_voice_status():
    """Capabilities shown by the browser voice widget; no microphone is server-side."""
    return {
        "enabled": True,
        "mode": "browser_speech",
        "openai_fallback_enabled": bool(
            os.getenv("OPENAI_API_KEY")
            and os.getenv("VOICE_ASSISTANT_USE_OPENAI", "false").lower() == "true"
        ),
        "execution_control": "blocked",
        "privacy": "Only the transcript is sent to the local dashboard; audio stays in the browser.",
    }


@app.post("/api/voice/query")
async def api_voice_query(payload: VoiceQuery):
    """Answer a spoken/text operational query from one fresh local snapshot."""
    question = payload.text.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Voice query cannot be empty.")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="Voice query is limited to 500 characters.")

    account = get_account_summary()
    intraday = get_intraday_dashboard()
    symbols = get_live_symbols_analysis()
    signals = _voice_signal_rows()
    assistant = VoiceAssistant()
    result = assistant.answer(question, account, intraday, symbols, signals)
    if result["intent"] == "unsupported":
        snapshot = {
            "account": account,
            "intraday": intraday,
            "market": symbols,
            "recent_signals": signals[:12],
        }
        ai_answer = _optional_voice_ai_answer(question, snapshot)
        if ai_answer:
            result["intent"] = "openai_snapshot_answer"
            result["answer"] = ai_answer
            result["sources"] = ["verified_local_snapshot", "openai_optional"]
    return {
        **result,
        "transcript": question,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_control": "blocked",
    }


@app.get("/api/intraday/status")
async def api_intraday_status():
    """Dedicated read-only status for the isolated M5 intraday profile."""
    return get_intraday_dashboard()


@app.get("/api/symbols")
def api_symbols():
    """Get 5-confirmation matrix and live prices for all symbols."""
    return get_live_symbols_analysis()


@app.get("/api/copilot/analysis")
def api_copilot_analysis():
    """Get real-time Support/Resistance radar and trade copilot diagnostics."""
    try:
        return get_copilot_radar_summary()
    except Exception as exc:
        logger.warning(f"Copilot analysis error: {exc}")
        return {"symbol": "XAUUSD", "current_price": 0.0, "radar": {}, "active_diagnostics": [], "general_advice": None, "error": str(exc)}


def get_learning_summary_data() -> Dict[str, Any]:
    """Expose live learning evidence, pattern memories, and trade autopsies."""
    import time
    import dataclasses
    now_ts = time.time()
    if state.cached_learning and (now_ts - state.cached_learning_time) < 5.0:
        return state.cached_learning
    live = get_account_summary()

    # 1. Fetch Neural Memory Bank patterns
    from learning.memory_bank import NeuralMemoryBank
    mb_summary = []
    try:
        mb = NeuralMemoryBank(state.db)
        mb_summary = mb.get_memory_bank_summary()
    except Exception as exc:
        logger.debug("Error loading memory bank: %s", exc)

    # 2. Fetch closed trades across bot journals and generate autopsies
    from database.models import TradeDB
    seen_tickets = set()
    all_closed = []
    parallel_journals = [
        "data/journal_bot_a.db",
        "data/journal_bot_b.db",
        "data/journal_bot_c.db",
        "data/journal_bot_d.db",
        "data/journal_bot_e.db",
        "data/journal_bot_f.db",
        "data/journal_bot_g.db",
    ]
    for j_path in parallel_journals:
        if Path(j_path).exists():
            try:
                db_inst = Database(j_path)
                with db_inst.session() as s:
                    t_list = s.query(TradeDB).filter_by(state="CLOSED").all()
                    for t in t_list:
                        if t.ticket and t.ticket not in seen_tickets:
                            seen_tickets.add(t.ticket)
                            all_closed.append(t)
            except Exception as e:
                logger.debug("Error reading journal %s for learning: %s", j_path, e)

    all_closed.sort(key=lambda x: x.exited_at or x.created_at or datetime.min, reverse=True)
    autopsies = []
    for t in all_closed[:15]:
        try:
            rep = state.autopsy_engine.analyze_trade(t)
            autopsies.append(dataclasses.asdict(rep))
        except Exception as exc:
            logger.debug("Error analyzing trade ticket %s: %s", getattr(t, "ticket", None), exc)

    xauusd_dna = state.autopsy_engine.get_xauusd_dna_insights(all_closed)

    learning_status = "Operando en aprendizaje activo (Closed-Loop Learning)"
    if not all_closed:
        learning_status = "Esperando cierres de órdenes para consolidar autopsias"

    payload = {
        "current_run_has_closed_trades": len(all_closed) > 0,
        "current_run_closed_trades": len(all_closed),
        "current_run_started_at": live["current_run_started_at"],
        "legacy_learning_excluded": False,
        "learning_status": learning_status,
        "autopsies": autopsies,
        "memory_bank": mb_summary,
        "xauusd_dna": xauusd_dna,
        "cumulative_stats": {
            "net_pnl": live["cumulative_pnl"],
            "profit_factor": live["profit_factor"],
            "total_trades": len(all_closed),
            "win_rate": live["cumulative_win_rate"],
        },
    }
    state.cached_learning = payload
    state.cached_learning_time = now_ts
    return payload


@app.get("/api/learning/summary")
def api_learning_summary():
    """Get learning evidence from the current fleet run only."""
    return get_learning_summary_data()


@app.get("/api/learning/trade/{ticket}")
async def api_learning_trade(ticket: int):
    """Get cognitive autopsy for a specific closed trade ticket."""
    from database.models import TradeDB
    try:
        with state.db.session() as s:
            trade = s.query(TradeDB).filter_by(ticket=ticket).first()
            if not trade:
                raise HTTPException(status_code=404, detail="Trade no encontrado")
            return state.autopsy_engine.analyze_trade(trade)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades/active")
def api_active_trades():
    """Get active open positions."""
    return get_active_positions()


@app.get("/api/trades/history")
def api_trade_history(limit: int = 50):
    """Get closed trades since the current demo run began."""
    from database.models import TradeDB
    all_trades = []
    seen_tickets = set()
    run_start = _current_run_start()

    # 1. Parallel bot journals
    parallel_journals = [
        ("BOT-A", "data/journal_bot_a.db"),
        ("BOT-B", "data/journal_bot_b.db"),
        ("BOT-C", "data/journal_bot_c.db"),
        ("BOT-D", "data/journal_bot_d.db"),
        ("BOT-E", "data/journal_bot_e.db"),
        ("BOT-F", "data/journal_bot_f.db"),
        ("BOT-G", "data/journal_bot_g.db"),
        ("BOT-A2", "data/journal_bot_a2.db"),
        ("BOT-B2", "data/journal_bot_b2.db"),
        ("BOT-C2", "data/journal_bot_c2.db"),
        ("BOT-D2", "data/journal_bot_d2.db"),
    ]
    for b_label, b_path in parallel_journals:
        if Path(b_path).exists():
            try:
                b_db = Database(b_path)
                with b_db.session() as session:
                    symbol = "USDJPY" if b_label == "BOT-E" else "XAUUSD"
                    trades_p = session.query(TradeDB).filter(
                        TradeDB.symbol == symbol
                    ).order_by(TradeDB.id.desc()).limit(limit).all()
                    for t in trades_p:
                        closed_at = _as_naive_utc(t.exited_at or t.created_at)
                        if t.state == TradeState.CLOSED.value and closed_at and closed_at >= run_start:
                            if t.ticket:
                                seen_tickets.add(t.ticket)
                            all_trades.append({
                                "trade_id": t.trade_id,
                                "ticket": t.ticket,
                                "symbol": t.symbol,
                                "direction": t.direction,
                                "volume": t.volume,
                                "entry_price": t.entry_price,
                                "exit_price": t.exit_price,
                                "sl_price": t.sl_price,
                                "tp_price": t.tp_price,
                                "profit": round(t.profit, 2) if t.profit is not None else 0.0,
                                "profit_r": round(t.profit_r, 2) if t.profit_r is not None else 0.0,
                                "total_score": t.total_score,
                                "state": t.state,
                                "exit_reason": getattr(t, "comment", "") or t.state,
                                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
                                "bot_type": b_label
                            })
            except Exception as e:
                logger.debug(f"Error reading {b_label} history: {e}")

        # 1.5 Deriv Synthetic Journal
    deriv_db_path = "data/journal_deriv.db"
    if Path(deriv_db_path).exists():
        try:
            import sqlite3
            with sqlite3.connect(deriv_db_path) as s_conn:
                s_conn.row_factory = sqlite3.Row
                cur = s_conn.cursor()
                cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
                for row in cur.fetchall():
                    t_ticket = row["ticket"]
                    if t_ticket:
                        seen_tickets.add(t_ticket)
                    all_trades.append({
                        "trade_id": f"SYN-{row['id']}",
                        "ticket": row["ticket"],
                        "symbol": row["symbol"],
                        "direction": row["direction"],
                        "volume": row["volume"],
                        "entry_price": row["entry_price"],
                        "exit_price": row["tp_price"] if row["status"] == "TP_HIT" else row["sl_price"],
                        "sl_price": row["sl_price"],
                        "tp_price": row["tp_price"],
                        "profit": round(row["profit"], 2) if row["profit"] is not None else 0.0,
                        "profit_r": round(row["profit"] / 25.0, 2) if row["profit"] else 0.0,
                        "total_score": 85.0,
                        "state": "CLOSED" if row["status"] != "OPEN" else "OPEN",
                        "exit_reason": row["status"] or "CLOSED",
                        "created_at": (row["open_time"] or "")[:19].replace("T", " "),
                        "bot_type": f"DERIV-{row['symbol'].split()[0].upper()}"
                    })
        except Exception as e:
            logger.debug(f"Error reading Deriv history: {e}")

    # 2. MT5 Broker Deals (captures partial TP closures like Bot A)
    try:
        import MetaTrader5 as mt5
        now_dt = datetime.now()
        start_24h = max(now_dt - timedelta(hours=24), run_start)
        deals = mt5.history_deals_get(start_24h, now_dt + timedelta(days=1))
        if deals:
            magic_map = {
                26082511: "BOT-A", 26082512: "BOT-B", 26082513: "BOT-C", 26082514: "BOT-D",
                26092655: "BOT-E", 26082516: "BOT-F", 26082517: "BOT-G",
                26082521: "BOT-A2", 26082522: "BOT-B2", 26082523: "BOT-C2", 26082524: "BOT-D2",
                26099901: "BOT-DERIV",
            }
            allowed_deal_symbols = {
                "XAUUSD", "USDJPY",
                "Volatility 75 Index", "Volatility 100 Index",
                "Boom 1000 Index", "Crash 1000 Index",
                "Boom 500 Index", "Crash 500 Index",
            }
            closed_deals = [
                d for d in deals
                if getattr(d, "entry", 0) == getattr(mt5, "DEAL_ENTRY_OUT", 1)
                and getattr(d, "symbol", "") in allowed_deal_symbols
                and getattr(d, "position_id", 0) not in EXCLUDED_POSITION_IDS
                and getattr(d, "magic", 0) in magic_map
            ]
            for d in closed_deals:
                pos_id = getattr(d, "position_id", 0)
                if pos_id not in seen_tickets:
                    magic = getattr(d, "magic", 0)
                    b_name = magic_map.get(magic, "BOT-A")
                    all_trades.append({
                        "trade_id": f"MT5-{d.ticket}",
                        "ticket": d.ticket,
                        "symbol": getattr(d, "symbol", ""),
                        "direction": "SELL" if d.type == 1 else "BUY",
                        "volume": d.volume,
                        "entry_price": round(d.price, 2),
                        "exit_price": round(d.price, 2),
                        "sl_price": None,
                        "tp_price": None,
                        "profit": round(d.profit, 2),
                        "profit_r": None,
                        "total_score": None,
                        "state": "CLOSED",
                        "exit_reason": "TAKE PROFIT PARCIAL" if d.volume < 0.03 else "CLOSED",
                        "created_at": datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S"),
                        "bot_type": b_name
                    })
    except Exception as exc:
        logger.debug(f"Error reading MT5 history deals for table: {exc}")

    # Sort merged list by created_at descending
    all_trades.sort(key=lambda x: x["created_at"], reverse=True)
    return all_trades[:limit]


@app.get("/api/signals")
def api_signals(limit: int = 50):
    """Get detected signals from the current parallel demo bot databases."""
    from database.models import SignalLogDB, DecisionContextDB, TradeDB
    all_signals = []

    # Parallel bot journals
    parallel_journals = [
        ("BOT-A", "data/journal_bot_a.db"),
        ("BOT-B", "data/journal_bot_b.db"),
        ("BOT-C", "data/journal_bot_c.db"),
        ("BOT-D", "data/journal_bot_d.db"),
        ("BOT-E", "data/journal_bot_e.db"),
        ("BOT-F", "data/journal_bot_f.db"),
        ("BOT-G", "data/journal_bot_g.db"),
        ("BOT-A2", "data/journal_bot_a2.db"),
        ("BOT-B2", "data/journal_bot_b2.db"),
        ("BOT-C2", "data/journal_bot_c2.db"),
        ("BOT-D2", "data/journal_bot_d2.db"),
    ]
    for b_label, b_path in parallel_journals:
        if Path(b_path).exists():
            try:
                b_db = Database(b_path)
                with b_db.session() as session:
                    symbol = "USDJPY" if b_label == "BOT-E" else "XAUUSD"
                    sigs_p = session.query(SignalLogDB).filter(
                        SignalLogDB.symbol == symbol,
                        SignalLogDB.created_at >= _current_run_start(),
                    ).order_by(SignalLogDB.id.desc()).limit(limit).all()
                    contexts_p = {
                        item.signal_id: item for item in session.query(DecisionContextDB).filter(
                            DecisionContextDB.signal_id.in_([signal.signal_id for signal in sigs_p])
                        ).all()
                    } if sigs_p else {}
                    p_trade_ids = {row[0] for row in session.query(TradeDB.signal_id).filter(
                        TradeDB.signal_id.in_([signal.signal_id for signal in sigs_p])
                    ).all()} if sigs_p else set()
                    for s in sigs_p:
                        item = contexts_p.get(s.signal_id)
                        all_signals.append({
                            "signal_id": s.signal_id,
                            "symbol": s.symbol,
                            "timeframe": s.timeframe,
                            "direction": s.direction,
                            "total_score": s.total_score,
                            "structure_score": s.structure_score,
                            "trend_score": s.trend_score,
                            "momentum_score": s.momentum_score,
                            "volume_score": s.volume_score,
                            "volatility_score": s.volatility_score,
                            "classification": s.classification,
                            "was_traded": s.was_traded,
                            "execution_status": _signal_status(s, s.signal_id in p_trade_ids),
                            "rejection_reasons": s.rejection_reasons,
                            "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
                            "bot_type": b_label,
                            "market_context": {
                                "regime": item.regime if item else None,
                                "session": item.session if item else None,
                                "nearest_liquidity": (item.context.get("liquidity", {}).get("nearest_level") or {}).get("name") if item else None,
                                "fibonacci_zone": bool(item and item.context.get("fibonacci", {}).get("in_zone", False)),
                                "sweep_aligned": bool(item and item.context.get("smc", {}).get("sweep_aligned", False)),
                                "fvg_aligned": bool(item and item.context.get("smc", {}).get("fvg_aligned", False)),
                                "order_block_aligned": bool(item and item.context.get("smc", {}).get("order_block_aligned", False)),
                                "quality_shadow_reasons": (item.context.get("market_quality", {}).get("shadow_reasons", []) if item else []),
                            },
                        })
            except Exception as e:
                logger.debug(f"Error reading {b_label} signal log: {e}")

    all_signals.sort(key=lambda x: x["created_at"], reverse=True)
    return all_signals[:limit]


@app.get("/api/candles")
async def api_candles(symbol: str = "XAUUSD", timeframe: str = "H1", bars: int = 250):
    """Serve OHLC candlestick data, EMAs, Market Structure Supports/Resistances, and Active Trade levels."""
    if not state.connector.is_connected():
        state.connector.initialize()
    state.connector.select_symbol(symbol, True)

    from data.downloader import DataDownloader
    from indicators.trend import calculate_all_trend_indicators
    from indicators.momentum import rsi as calc_rsi
    from indicators.volatility import atr as calc_atr
    from indicators.structure import get_market_structure

    dl = DataDownloader()
    try:
        df = dl.download_rates(symbol, timeframe, bars, closed_only=False)
        if df is None or df.empty:
            return {"candles": [], "ema20": [], "ema50": [], "ema200": [], "supports": [], "resistances": [], "metrics": {}}

        sym_info = state.connector.get_symbol_info(symbol)
        digits = sym_info.digits if sym_info and hasattr(sym_info, "digits") else 2

        # 1. Standardize Candles
        candles = []
        for _, row in df.iterrows():
            t = int(row["time"].timestamp()) if hasattr(row["time"], "timestamp") else int(row["time"])
            candles.append({
                "time": t,
                "open": round(float(row["open"]), digits),
                "high": round(float(row["high"]), digits),
                "low": round(float(row["low"]), digits),
                "close": round(float(row["close"]), digits),
            })

        # 2. Indicators: EMA 20, 50, 200, RSI, ATR
        df_indicated = calculate_all_trend_indicators(df.copy(), 20, 50, 200, 14)
        atr_series = calc_atr(df_indicated, 14)
        rsi_series = calc_rsi(df_indicated["close"], 14)

        ema20 = []
        ema50 = []
        ema200 = []
        for _, row in df_indicated.iterrows():
            t = int(row["time"].timestamp()) if hasattr(row["time"], "timestamp") else int(row["time"])
            val20 = row.get("ema_fast")
            if pd.notna(val20):
                ema20.append({"time": t, "value": round(float(val20), digits)})
            val50 = row.get("ema_medium")
            if pd.notna(val50):
                ema50.append({"time": t, "value": round(float(val50), digits)})
            val200 = row.get("ema_slow")
            if pd.notna(val200):
                ema200.append({"time": t, "value": round(float(val200), digits)})

        # 3. Market Structure Supports & Resistances
        last_close = float(df_indicated["close"].iloc[-1])
        struct = get_market_structure(df_indicated, atr_series)

        resistances = []
        supports = []

        # Find recent swing highs as resistances (>= last_close * 0.998)
        for s in reversed(struct.swings):
            if s.swing_type.value == "SWING_HIGH" and s.price >= (last_close * 0.998):
                if not any(abs(r["price"] - s.price) / last_close < 0.0025 for r in resistances):
                    lbl = f"Resistencia {s.classification.value}" if s.classification.value != "UNCLASSIFIED" else "Resistencia Swing"
                    resistances.append({
                        "price": round(float(s.price), digits),
                        "label": lbl,
                        "type": "resistance"
                    })
                if len(resistances) >= 4:
                    break

        # Find recent swing lows as supports (<= last_close * 1.002)
        for s in reversed(struct.swings):
            if s.swing_type.value == "SWING_LOW" and s.price <= (last_close * 1.002):
                if not any(abs(sp["price"] - s.price) / last_close < 0.0025 for sp in supports):
                    lbl = f"Soporte {s.classification.value}" if s.classification.value != "UNCLASSIFIED" else "Soporte Swing"
                    supports.append({
                        "price": round(float(s.price), digits),
                        "label": lbl,
                        "type": "support"
                    })
                if len(supports) >= 4:
                    break

        # 4. Active Trade (if any open MT5 position for this symbol)
        active_trade = None
        try:
            positions = state.connector.get_positions()
            for pos in positions:
                if getattr(pos, "symbol", "") == symbol:
                    active_trade = {
                        "ticket": int(pos.ticket),
                        "type": "BUY" if pos.type == 0 else "SELL",
                        "open_price": round(float(pos.price_open), digits),
                        "sl": round(float(pos.sl), digits) if pos.sl and pos.sl > 0 else None,
                        "tp": round(float(pos.tp), digits) if pos.tp and pos.tp > 0 else None,
                        "profit": round(float(pos.profit), 2),
                        "volume": float(pos.volume),
                    }
                    break
        except Exception as pos_err:
            logger.debug(f"Position check error in candles: {pos_err}")

        # 5. Summary Metrics
        last_row = df_indicated.iloc[-1]
        last_atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
        last_rsi = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else 50.0
        last_adx = float(last_row.get("adx", 0.0)) if pd.notna(last_row.get("adx")) else 0.0
        ema_align = str(last_row.get("ema_alignment", "MIXED"))

        theoretical_sl = round(last_close - (1.5 * last_atr), digits) if last_atr > 0 else None
        theoretical_tp = round(last_close + (3.0 * last_atr), digits) if last_atr > 0 else None

        spread_pts = 0
        tick = state.connector.get_symbol_tick(symbol)
        if tick and sym_info and sym_info.point:
            spread_pts = int(round((tick.ask - tick.bid) / sym_info.point))

        return {
            "candles": candles,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "supports": supports,
            "resistances": resistances,
            "active_trade": active_trade,
            "metrics": {
                "close": round(last_close, digits),
                "ema20": round(float(last_row.get("ema_fast", 0.0)), digits) if pd.notna(last_row.get("ema_fast")) else None,
                "ema50": round(float(last_row.get("ema_medium", 0.0)), digits) if pd.notna(last_row.get("ema_medium")) else None,
                "ema200": round(float(last_row.get("ema_slow", 0.0)), digits) if pd.notna(last_row.get("ema_slow")) else None,
                "rsi": round(last_rsi, 1),
                "adx": round(last_adx, 1),
                "atr": round(last_atr, digits),
                "trend_alignment": ema_align,
                "theoretical_sl": theoretical_sl,
                "theoretical_tp": theoretical_tp,
                "spread_points": spread_pts,
                "digits": digits
            }
        }
    except Exception as e:
        logger.error(f"Error fetching candles for {symbol} {timeframe}: {e}", exc_info=True)
        return {"error": str(e), "candles": []}


class ControlAction(BaseModel):
    action: str  # 'start', 'pause', 'stop', 'set_mode'
    mode: Optional[str] = None


@app.post("/api/control")
async def api_control(payload: ControlAction, _: None = Depends(require_control_token)):
    """Reject simulated process controls until a real IPC control plane exists."""
    raise HTTPException(
        status_code=409,
        detail="Dashboard process controls are disabled: this server has no authenticated IPC channel to bot processes.",
    )


@app.post("/api/control/close_position/{ticket}")
async def api_close_position(ticket: int, _: None = Depends(require_control_token)):
    """Manually close a single active position."""
    valid_magics = {
        26082498, 26082499, 26082501, 26082502,
        26082511, 26082512, 26082513, 26082514,
        26092655, 26082516, 26082517,
        26082521, 26082522, 26082523, 26082524,
        26099901,
        getattr(state.config.bot, "magic_number", 0),
        getattr(state.intraday_config.bot, "magic_number", 0),
    }
    position = next(
        (pos for pos in state.connector.get_positions() if int(getattr(pos, "ticket", -1)) == ticket),
        None,
    )
    if position is None or getattr(position, "magic", None) not in valid_magics:
        raise HTTPException(status_code=404, detail="Position is not owned by a configured bot magic number.")
    res = state.order_mgr.close_order(ticket)
    if res.success:
        return {"success": True, "message": f"Position #{ticket} closed successfully."}
    raise HTTPException(status_code=400, detail=res.message or "Failed to close position.")


@app.post("/api/control/close_all")
async def api_close_all(_: None = Depends(require_control_token)):
    """Emergency kill switch: close all open bot positions."""
    valid_magics = {
        26082498, 26082499, 26082501, 26082502,
        26082511, 26082512, 26082513, 26082514,
        26092655, 26082516, 26082517,
        26082521, 26082522, 26082523, 26082524,
        26099901,
        getattr(state.config.bot, "magic_number", 0),
        getattr(state.intraday_config.bot, "magic_number", 0),
    }
    positions = [pos for pos in state.connector.get_positions() if getattr(pos, "magic", None) in valid_magics]
    closed_count = 0
    errors = []
    
    for pos in positions:
        res = state.order_mgr.close_order(pos.ticket)
        if res.success:
            closed_count += 1
        else:
            errors.append(f"Ticket #{pos.ticket}: {res.message}")
            
    return {
        "success": len(errors) == 0,
        "closed_count": closed_count,
        "total": len(positions),
        "errors": errors
    }


@app.get("/api/ml/status")
async def api_ml_status():
    """Get ML Meta-Filter metrics, status, and feature importances."""
    return get_ml_status()


@app.post("/api/ml/toggle_shadow")
async def api_ml_toggle_shadow(_: None = Depends(require_control_token)):
    """Toggle ML shadow mode vs active filtering and persist setting."""
    if state.config.ml.shadow_mode and not state.config.ml.allow_active_filter:
        raise HTTPException(
            status_code=409,
            detail=(
                "ML active filtering is locked. Review walk-forward and "
                "execution-cost backtest evidence, then set ml.allow_active_filter: true."
            ),
        )
    state.config.ml.shadow_mode = not state.config.ml.shadow_mode
    
    # Persist to settings.yaml
    try:
        settings_path = Path("config/settings.yaml")
        if settings_path.exists():
            import yaml
            with open(settings_path, "r", encoding="utf-8") as f:
                cfg_data = yaml.safe_load(f) or {}
            if "ml" not in cfg_data or not isinstance(cfg_data["ml"], dict):
                cfg_data["ml"] = {}
            cfg_data["ml"]["shadow_mode"] = state.config.ml.shadow_mode
            with open(settings_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg_data, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.warning(f"Failed to persist shadow_mode to settings.yaml: {e}")

    return {
        "success": True,
        "shadow_mode": state.config.ml.shadow_mode,
        "status_label": "SHADOW (OBSERVING)" if state.config.ml.shadow_mode else "ACTIVE (FILTERING)"
    }


@app.post("/api/ml/retrain")
async def api_ml_retrain(background_tasks: BackgroundTasks, _: None = Depends(require_control_token)):
    """Trigger background model retraining on fresh MT5 data."""
    def _run_retrain():
        try:
            from execution.mt5_connector import MT5Connector
            from ml.dataset_builder import build_dataset
            from ml.trainer import train_model
            logger.info("[ML Retrain] Initializing MT5 connection for background retraining...")
            connector = MT5Connector()
            connector.initialize()
            try:
                build_dataset(symbols=state.config.market.symbols, months=6, bars_per_download=3000)
                train_model(n_splits=4)
            finally:
                connector.shutdown()

            if hasattr(state, "ml_predictor"):
                state.ml_predictor.reload()
            logger.info("[ML Retrain] Background retraining completed and model reloaded successfully.")
        except Exception as e:
            logger.error(f"[ML Retrain] Error during background retraining: {e}", exc_info=True)

    background_tasks.add_task(_run_retrain)
    return {"success": True, "message": "ML retraining launched in background."}


# --- Quantitative Research & Backtest Analytics API ---
@app.get("/api/research/summary")
async def api_research_summary():
    """Return evidence scoped to today's running fleet, not stale approvals."""
    profiles = _fleet_profiles()
    live = get_account_summary()
    return {
        "scope": "Flota demo actual A-E",
        "current_run_started_at": _fleet_runtime().get("started_at"),
        "current_run_closed_trades": live["cumulative_trades"],
        "current_run_result": ("Sin operaciones cerradas registradas en esta ejecución"
                                if live["cumulative_trades"] == 0 else
                                f"{live['cumulative_trades']} cierres; P&L {live['cumulative_pnl']:+.2f} USD"),
        "oos_status": "NO_VERIFICADO_PARA_CONFIGURACION_ACTUAL",
        "oos_note": "Los resultados históricos no se atribuyen a estos perfiles porque sus configuraciones actuales difieren y el holdout ya fue observado.",
        "fleet_profiles": profiles,
        "timeframe_comparison": [],
        "gold_deep_dive": [],
        "statistical_audit": [],
        "verdicts_matrix": [],
        "active_bot_profile": {
            "name": "Flota demo actual A-E",
            "mode": state.bot_mode,
            "symbols": sorted({symbol for profile in profiles for symbol in profile["symbols"]}),
            "entry_tf": ", ".join(sorted({profile["timeframe"] for profile in profiles})),
            "risk_percent": {profile["id"]: profile["risk_percent"] for profile in profiles},
            "execution_authorized": all(profile["execution_authorized"] for profile in profiles),
        },
    }


@app.get("/api/experiments/list")
async def api_experiments_list():
    """Return stored manifests using their actual schema; these are historical tests."""
    exp_dir = Path("data/experiments")
    if not exp_dir.exists():
        return []
    manifests = []
    for f in sorted(exp_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            metrics = data.get("summary_metrics") or data.get("metrics") or {}
            symbols = data.get("symbols") or []
            if isinstance(symbols, str):
                symbols = [item.strip() for item in symbols.split(",") if item.strip()]
            manifests.append({
                "filename": f.name,
                "id": data.get("experiment_id", data.get("id", f.stem)),
                "name": data.get("name", data.get("experiment_id", f.stem)),
                "purpose": data.get("purpose", ""),
                "is_oos_evaluation": bool(data.get("is_oos_evaluation", False)),
                "created_at": data.get("created_at", ""),
                "period": data.get("period", ""),
                "evaluation_start": data.get("evaluation_start"),
                "symbols": symbols,
                "timeframes": data.get("timeframes", {
                    "entry": data.get("entry_timeframe"),
                    "setup": data.get("setup_timeframe"),
                    "higher": data.get("higher_timeframe"),
                }),
                "total_trades": metrics.get("total_trades"),
                "pnl_usd": metrics.get("total_profit_money", metrics.get("pnl_usd")),
                "pnl_r": metrics.get("total_profit_r", metrics.get("pnl_r")),
                "profit_factor": metrics.get("profit_factor"),
                "win_rate": metrics.get("win_rate"),
                "bootstrap_p_pos": metrics.get("bootstrap_p_pos"),
                "config_sha256": data.get("config_sha256", {}),
            })
        except Exception as exc:
            logger.debug("Skipping unreadable experiment manifest %s: %s", f, exc)
    return manifests


# --- WebSocket Streaming ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Streams full system snapshot asynchronously without blocking the event loop."""
    await manager.connect(websocket)
    try:
        while True:
            def _build_snapshot():
                return {
                    "account": get_account_summary(),
                    "intraday": get_intraday_dashboard(),
                    "symbols": get_live_symbols_analysis(),
                    "positions": get_active_positions(),
                    "ml": get_ml_status(),
                    "copilot": get_copilot_radar_summary(),
                    "learning": get_learning_summary_data(),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            snapshot = await asyncio.to_thread(_build_snapshot)
            await websocket.send_json(snapshot)
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        manager.disconnect(websocket)
    except Exception as e:
        # Check if it's a normal browser navigation/disconnect (1000 OK, 1001 Going Away)
        err_str = str(e)
        if "1000" in err_str or "1001" in err_str or "going away" in err_str.lower():
            logger.debug(f"WebSocket client disconnected normally: {e}")
        else:
            logger.warning(f"WebSocket disconnect: {e}")
        manager.disconnect(websocket)
