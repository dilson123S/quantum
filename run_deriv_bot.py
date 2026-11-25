"""Quantum-Deriv: Automated Trading Bot for Deriv Synthetic Indices.

Operates 24/7 on:
- Volatility Indices (V75, V100, V50, V25) via SMC & Volatility Breakout
- Boom & Crash Indices (B1000, C1000, B500, C500) via Asymmetric Spike Sniper
"""
import argparse
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml
import pandas as pd

from core.constants import Direction
from execution.deriv_connector import DerivConnector, DerivSymbolSpec
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal
from strategy.synthetic_volatility import SyntheticVolatilityStrategy, SyntheticVolatilitySignal
from risk.synthetic_risk import SyntheticRiskManager, SyntheticRiskConfig

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Setup logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "quanttradingbot-deriv.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("QuantumDeriv")


class DerivJournal:
    """Lightweight SQLite journal for Deriv Synthetic trades."""

    def __init__(self, db_path: str = "data/journal_deriv.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket INTEGER,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    sl_price REAL,
                    tp_price REAL,
                    volume REAL,
                    profit REAL DEFAULT 0.0,
                    status TEXT,
                    strategy TEXT,
                    confluences TEXT,
                    open_time TEXT,
                    close_time TEXT
                )
            """)
            conn.commit()

    def record_entry(self, ticket: int, symbol: str, direction: str, entry_price: float,
                     sl_price: float, tp_price: float, volume: float, strategy: str, confluences: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trades (ticket, symbol, direction, entry_price, sl_price, tp_price, volume, status, strategy, confluences, open_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """, (ticket, symbol, direction, entry_price, sl_price, tp_price, volume, strategy, confluences, datetime.now(timezone.utc).isoformat()))
            conn.commit()

    def record_exit(self, ticket: int, exit_price: float, profit: float, reason: str = "CLOSED"):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE trades
                SET status = ?, profit = ?, close_time = ?
                WHERE ticket = ?
            """, (reason, profit, datetime.now(timezone.utc).isoformat(), ticket))
            conn.commit()


class QuantumDerivBot:
    """Master controller for Deriv Synthetic Trading."""

    def __init__(self, config_path: str = "config/deriv_synthetic/settings.yaml", dry_run: bool = False):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.dry_run = dry_run or self.config.get("execution", {}).get("dry_run", False)
        self.running = False
        
        # Connectors & Components
        self.connector = DerivConnector(mock_mode=self.dry_run, server=self.config.get("bot", {}).get("server", "Deriv-Demo"))
        risk_dict = self.config.get("risk", {})
        self.risk_manager = SyntheticRiskManager(
            self.connector,
            SyntheticRiskConfig(
                risk_mode=risk_dict.get("mode", "percent"),
                risk_percent=float(risk_dict.get("risk_percent", 0.75)),
                risk_money=float(risk_dict.get("risk_money", 25.0)),
                max_daily_loss_pct=float(risk_dict.get("max_daily_loss", 5.0)),
                max_account_drawdown_pct=float(risk_dict.get("max_drawdown", 12.0)),
                max_open_positions=int(risk_dict.get("max_open_positions", 8)),
                max_positions_per_symbol=int(risk_dict.get("max_positions_per_symbol", 4)),
            )
        )
        self.journal = DerivJournal(self.config.get("bot", {}).get("journal_path", "data/journal_deriv.db"))
        
        # Strategies
        spike_cfg = self.config.get("strategies", {}).get("spike_sniper", {})
        self.spike_strategy = SpikeSniperStrategy(
            max_holding_bars=int(spike_cfg.get("max_holding_bars", 12)),
            target_rr=float(spike_cfg.get("target_rr", 2.0)),
            min_rr=float(spike_cfg.get("min_rr", 1.40)),
            sl_buffer_atr=float(spike_cfg.get("sl_buffer_atr", 1.0)),
            touch_atr=float(spike_cfg.get("touch_atr", 1.00)),
            close_near_atr=float(spike_cfg.get("close_near_atr", 1.35)),
            boom_rsi_oversold=float(spike_cfg.get("boom_rsi_oversold", 48.0)),
            crash_rsi_overbought=float(spike_cfg.get("crash_rsi_overbought", 52.0)),
            min_wick_ratio=float(spike_cfg.get("min_wick_ratio", 0.15)),
        )

        # Portfolio allocation
        port_cfg = self.config.get("portfolio", {})
        self.vol_max_positions = int(port_cfg.get("volatility_max_positions", 4))
        self.bc_max_positions = int(port_cfg.get("boom_crash_max_positions", 4))

        vol_cfg = self.config.get("strategies", {}).get("volatility_trend", {})
        self.vol_strategy = SyntheticVolatilityStrategy(
            ema_fast=int(vol_cfg.get("ema_fast", 20)),
            ema_medium=int(vol_cfg.get("ema_medium", 50)),
            ema_slow=int(vol_cfg.get("ema_slow", 200)),
            atr_period=int(vol_cfg.get("atr_period", 14)),
            sl_buffer_atr=float(vol_cfg.get("sl_buffer_atr", 0.25)),
            target_rr=float(vol_cfg.get("target_rr", 2.20)),
            partial_rr=float(vol_cfg.get("partial_rr", 1.35)),
            min_rr=float(vol_cfg.get("min_rr", 1.40)),
            enable_trend_pullback=bool(vol_cfg.get("enable_trend_pullback", True)),
            enable_trap_reversal=bool(vol_cfg.get("enable_trap_reversal", True)),
            enable_squeeze_expansion=bool(vol_cfg.get("enable_squeeze_expansion", True)),
            enable_pullback_sweep=bool(vol_cfg.get("enable_pullback_sweep", True)),
            enable_mean_reversion=bool(vol_cfg.get("enable_mean_reversion", False)),
            enable_breakout=bool(vol_cfg.get("enable_breakout", False)),
        )

        self.last_bars: Dict[str, datetime] = {}
        self.bars_held: Dict[int, int] = {}  # ticket -> bars count

        # Open-Source System 1 Decision Hub (Laya, NanoJev, Kev)
        from filters.system_one.hub import SystemOneHub
        from strategy.synthetic_system_one import SyntheticSystemOneEvaluator

        s1_cfg = self.config.get("system_one", {})
        self.system_one_hub = SystemOneHub(
            enabled=bool(s1_cfg.get("enabled", True)),
            provider_name=s1_cfg.get("provider", "nanojev"),
            shadow_mode=bool(s1_cfg.get("shadow_mode", True)),
            device=s1_cfg.get("device", "cuda"),
            model_name=s1_cfg.get("model", "nanojev-0.6b"),
            timeout_seconds=float(s1_cfg.get("timeout_seconds", 2.0)),
        )
        self.synthetic_s1_evaluator = SyntheticSystemOneEvaluator(
            hub=self.system_one_hub,
            shadow_mode=bool(s1_cfg.get("shadow_mode", True)),
            min_evidence_score=int(s1_cfg.get("min_evidence_score", 3)),
            min_confidence=float(s1_cfg.get("min_confidence", 0.60)),
        )

    def _load_config(self, path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def start(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        max_iterations: Optional[int] = None,
    ):
        """Start trading operations."""
        logger.info("=" * 65)
        mode_label = "SIMULATION / DRY-RUN" if self.dry_run else "LIVE / DEMO MT5"
        logger.info(f"   QUANTUM-DERIV: SYNTHETIC INDICES ENGINE [{mode_label}]")
        logger.info("=" * 65)

        # Connection: ensure MT5 GUI is open before connecting to prevent headless locking
        if not self.dry_run:
            if not self.connector.is_terminal_running():
                logger.info("[ESPERA MT5] Esperando a que abras MetaTrader 5 en tu escritorio...")
                while not self.connector.is_terminal_running():
                    time.sleep(3)
                logger.info("[MT5 DETECTADO] MetaTrader 5 detectado en pantalla. Conectando...")

        ok = self.connector.connect_deriv(login=login, password=password, server=server)
        if not ok:
            logger.warning("Could not establish direct Deriv session. Running in simulated fallback mode.")

        account = self.connector.get_account_info()
        starting_bal = account.balance if account else 10000.0
        self.risk_manager.reset_day(starting_bal)

        logger.info(f"Target Symbols: {', '.join(self.config['market']['symbols'])}")
        logger.info(f"Starting Baseline Balance: ${starting_bal:.2f} USD")
        logger.info("Bot is active and scanning 24/7. Press Ctrl+C to stop.")

        self.running = True
        iterations_run = 0
        try:
            while self.running:
                self._iteration()
                iterations_run += 1
                if max_iterations and iterations_run >= max_iterations:
                    logger.info(f"Completed {iterations_run} scan iterations. Exiting.")
                    break
                time.sleep(3)
        except KeyboardInterrupt:
            logger.info("Shutting down Quantum-Deriv Bot...")
        finally:
            self.stop()

    def _iteration(self):
        """Execute one scan loop across all configured synthetic indices."""
        account = self.connector.get_account_info()
        if account is None:
            now = time.time()
            if not hasattr(self, "_last_reconnect_attempt") or now - self._last_reconnect_attempt >= 5.0:
                self._last_reconnect_attempt = now
                logger.warning("Conexion con terminal MT5 interrumpida. Re-inicializando sesion...")
                self.connector.initialize()
                account = self.connector.get_account_info()
            if account is None:
                return

        balance = account.balance if account else 10000.0
        equity = account.equity if account else balance

        open_positions = self.connector.get_positions(magic=self.config["bot"]["magic_number"])
        open_count = len(open_positions)

        # 1. Manage Active Positions (Break-even, partial close, time-decay exit)
        self._manage_open_positions(open_positions)

        # Heartbeat log every 60 seconds
        now = time.time()
        if not hasattr(self, "_last_heartbeat") or now - self._last_heartbeat >= 60.0:
            self._last_heartbeat = now
            logger.info(f"[HEARTBEAT] Analizando {len(self.config['market']['symbols'])} pares sinteticos | Balance: ${balance:.2f} | Posiciones: {open_count}")

        # 2. Scan each symbol for new setups (Volatility indices FIRST for priority)
        vol_positions = sum(1 for p in open_positions if self.connector.get_symbol_specs(getattr(p, "symbol", "")).category == "volatility")
        bc_positions = sum(1 for p in open_positions if self.connector.get_symbol_specs(getattr(p, "symbol", "")).category == "boom_crash")

        # Sort symbols: volatility indices first (70% priority), then boom/crash (30%)
        sorted_symbols = sorted(
            self.config["market"]["symbols"],
            key=lambda s: 0 if self.connector.get_symbol_specs(self.connector.resolve_symbol(s)).category == "volatility" else 1,
        )

        for symbol_raw in sorted_symbols:
            symbol = self.connector.resolve_symbol(symbol_raw)
            sym_positions = [p for p in open_positions if getattr(p, "symbol", "") == symbol]

            can_trade, reason = self.risk_manager.can_open_trade(
                symbol, balance, equity, open_count, len(sym_positions)
            )
            if not can_trade:
                continue

            # Portfolio allocation enforcement
            spec = self.connector.get_symbol_specs(symbol)
            if spec.category == "volatility" and vol_positions >= self.vol_max_positions:
                continue
            if spec.category == "boom_crash" and bc_positions >= self.bc_max_positions:
                continue

            # Download rates
            df_m5 = self.connector.get_rates(symbol, "M5", count=100)
            df_m15 = self.connector.get_rates(symbol, "M15", count=100)
            df_h1 = self.connector.get_rates(symbol, "H1", count=100)

            if df_m5 is None or len(df_m5) < 35:
                continue

            # Check for bar freshness and new bar
            last_bar_time = df_m5.iloc[-1].name if isinstance(df_m5.index, pd.DatetimeIndex) else df_m5.iloc[-1].get("time")
            if last_bar_time is not None:
                try:
                    bar_ts = pd.to_datetime(last_bar_time)
                    age_minutes = (time.time() - bar_ts.timestamp()) / 60.0
                    if age_minutes > 25.0:
                        logger.warning(
                            f"[STALE DATA GUARD] {symbol}: Last M5 bar is {age_minutes:.1f}m old ({last_bar_time}). "
                            "Waiting for MT5 terminal to synchronize real-time data with broker."
                        )
                        continue
                except Exception:
                    pass

            if self.last_bars.get(symbol) == last_bar_time:
                continue

            # Scale-In Guardrail: If a position already exists for this symbol, ensure eligibility
            if len(sym_positions) > 0:
                can_add, scale_mode, scale_msg = self._evaluate_scale_in_eligibility(
                    symbol, sym_positions, df_m5, spec.point
                )
                if not can_add:
                    continue
                logger.info(f"[{scale_mode}] {symbol}: {scale_msg}")

            self.last_bars[symbol] = last_bar_time

            # Route to specialized strategy
            if spec.category == "boom_crash":
                signal = self.spike_strategy.detect_spike_setup(symbol, df_m5, df_higher=df_m15, df_h1=df_h1, point=spec.point)
                if signal:
                    # Evaluate setup through Open-Source System 1 Decision Engine
                    s1_res = self.synthetic_s1_evaluator.evaluate_setup(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        entry_price=signal.entry_price,
                        sl_price=signal.sl_price,
                        tp_price=signal.tp_price,
                        target_rr=signal.target_rr,
                        df_m5=df_m5,
                        df_m15=df_m15,
                        df_h1=df_h1,
                        confluences=signal.confluences,
                        point=spec.point,
                    )
                    if s1_res.approved:
                        layer = len(sym_positions) + 1
                        self._execute_spike_trade(signal, balance, layer=layer, s1_res=s1_res)
                        bc_positions += 1
                        open_count += 1
                    else:
                        logger.warning(
                            f"[SYSTEM-1 BLOCKED] {signal.symbol} {signal.direction.value}: %s",
                            " | ".join(s1_res.rejection_reasons)
                        )
                else:
                    df_ind = self.spike_strategy.calculate_indicators(df_m5)
                    curr = df_ind.iloc[-1]
                    logger.info(f"[ANALISIS M5] {symbol}: Precio {curr['close']:.2f} | ATR: {curr['atr']:.2f}")

            elif spec.category == "volatility":
                signal = self.vol_strategy.evaluate_volatility_setup(symbol, df_m5, df_m15, df_h1, point=spec.point)
                if signal:
                    # Evaluate setup through Open-Source System 1 Decision Engine
                    s1_res = self.synthetic_s1_evaluator.evaluate_setup(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        entry_price=signal.entry_price,
                        sl_price=signal.sl_price,
                        tp_price=signal.tp_price,
                        target_rr=signal.risk_reward,
                        df_m5=df_m5,
                        df_m15=df_m15,
                        df_h1=df_h1,
                        confluences=signal.confluences,
                        point=spec.point,
                    )
                    if s1_res.approved:
                        layer = len(sym_positions) + 1
                        self._execute_volatility_trade(signal, balance, layer=layer, s1_res=s1_res)
                        vol_positions += 1
                        open_count += 1
                    else:
                        logger.warning(
                            f"[SYSTEM-1 BLOCKED] {signal.symbol} {signal.direction.value}: %s",
                            " | ".join(s1_res.rejection_reasons)
                        )
                else:
                    df_ind = self.vol_strategy.calculate_indicators(df_m5)
                    curr = df_ind.iloc[-1]
                    logger.info(f"[ANALISIS M5] {symbol}: Precio {curr['close']:.2f} | RSI: {curr['rsi']:.1f}")

    def _validate_execution_safety(self, sig: Any, lot: float, balance: float) -> Tuple[bool, str]:
        """Verify that live price has not drifted away from setup and real dollar risk is within limits."""
        tick = self.connector.get_symbol_tick(sig.symbol)
        if not tick:
            return False, "Live tick unavailable from broker"

        is_buy = sig.direction == Direction.BUY
        live_price = float(tick.ask if is_buy else tick.bid)
        price_drift = abs(live_price - sig.entry_price)
        atr_val = float(getattr(sig, "atr", 0.0) or 0.0)
        max_drift = max(atr_val * 1.5, sig.entry_price * 0.005) if atr_val > 0 else (sig.entry_price * 0.005)

        if price_drift > max_drift:
            return False, f"Live price {live_price:.2f} drifted by {price_drift:.2f} pts from setup price {sig.entry_price:.2f} (max allowed {max_drift:.2f})"

        real_sl_dist = abs(sig.sl_price - live_price)
        spec = self.connector.get_symbol_specs(sig.symbol)
        val_per_pt = spec.contract_size if spec and spec.contract_size > 0 else 1.0
        real_risk_usd = real_sl_dist * lot * val_per_pt
        max_budget = balance * (float(self.config.get("risk", {}).get("risk_percent", 0.75)) / 100.0)

        if real_risk_usd > max_budget * 1.40:
            return False, f"Real risk ${real_risk_usd:.2f} exceeds configured budget ${max_budget:.2f} by >40% (SL distance: {real_sl_dist:.2f} pts)"

        return True, "Safe"

    def _execute_spike_trade(self, sig: SpikeSignal, balance: float, layer: int = 1, s1_res: Optional[Any] = None):
        """Execute a Boom or Crash spike sniper trade (supports pyramiding up to 4 layers)."""
        lot, risk = self.risk_manager.calculate_trade_risk(sig.symbol, sig.entry_price, sig.sl_price, balance)
        
        tier = getattr(s1_res, "tier", "TIER_A_ALPHA") if s1_res else "TIER_A_ALPHA"
        multiplier = getattr(s1_res, "risk_multiplier", 1.0) if s1_res else 1.0
        target_rr_cap = getattr(s1_res, "target_rr_cap", None) if s1_res else None

        if layer > 1:
            multiplier = min(multiplier, 0.50)

        if multiplier < 1.0 and lot > 0:
            spec = self.connector.get_symbol_specs(sig.symbol)
            min_vol = spec.min_volume if spec else 0.001
            vol_step = spec.volume_step if spec else 0.001
            lot = max(min_vol, round(round((lot * multiplier) / vol_step) * vol_step, 4))
            risk = round(risk * multiplier, 2)

        # Real-time price consistency and maximum risk safety check
        is_safe, safety_msg = self._validate_execution_safety(sig, lot, balance)
        if not is_safe:
            logger.error(f"[EXECUTION VETO] {sig.symbol} {sig.direction.value}: {safety_msg}. Aborting trade to protect capital!")
            return

        tp_price = sig.tp_price
        if target_rr_cap:
            risk_dist = abs(sig.entry_price - sig.sl_price)
            if sig.direction == Direction.BUY:
                capped_tp = sig.entry_price + (risk_dist * target_rr_cap)
                if capped_tp < tp_price:
                    tp_price = round(capped_tp, 4)
            else:
                capped_tp = sig.entry_price - (risk_dist * target_rr_cap)
                if capped_tp > tp_price:
                    tp_price = round(capped_tp, 4)

        pyramid_tag = f"[PIRAMIDACIÓN CAPA {layer}/4]" if layer > 1 else "[ENTRADA BASE 1/4]"
        logger.info(
            f"[SPIKE SNIPER] {pyramid_tag} {sig.symbol} {sig.direction.value} @ {sig.entry_price:.4f} | "
            f"SL: {sig.sl_price:.4f} | TP: {tp_price:.4f} | Lot: {lot} | Risk: ${risk:.2f} | Tier: {tier}"
        )
        logger.info(f"   Confluences: {', '.join(sig.confluences)}")

        order_res = self.connector.send_order(
            symbol=sig.symbol,
            order_type=sig.direction.value,
            volume=lot,
            price=sig.entry_price,
            sl=sig.sl_price,
            tp=tp_price,
            magic=self.config["bot"]["magic_number"],
            comment=f"QSYN:{tier[:6]}-L{layer}",
        )
        if not order_res or getattr(order_res, "retcode", None) not in (10009, 10008):
            logger.warning(f"Order not filled by broker: retcode={getattr(order_res, 'retcode', 'None')} ({getattr(order_res, 'comment', '')})")
            return

        ticket = getattr(order_res, "order", int(time.time()))
        self.journal.record_entry(
            ticket=ticket,
            symbol=sig.symbol,
            direction=sig.direction.value,
            entry_price=sig.entry_price,
            sl_price=sig.sl_price,
            tp_price=tp_price,
            volume=lot,
            strategy=f"{sig.setup_name}-{tier}-L{layer}",
            confluences=", ".join(sig.confluences),
        )
        self.bars_held[ticket] = 0

    def _execute_volatility_trade(self, sig: SyntheticVolatilitySignal, balance: float, layer: int = 1, s1_res: Optional[Any] = None):
        """Execute a Volatility Index trade (supports pyramiding up to 4 layers)."""
        lot, risk = self.risk_manager.calculate_trade_risk(sig.symbol, sig.entry_price, sig.sl_price, balance)
        
        tier = getattr(s1_res, "tier", "TIER_A_ALPHA") if s1_res else "TIER_A_ALPHA"
        multiplier = getattr(s1_res, "risk_multiplier", 1.0) if s1_res else 1.0
        target_rr_cap = getattr(s1_res, "target_rr_cap", None) if s1_res else None

        if layer > 1:
            multiplier = min(multiplier, 0.50)

        if multiplier < 1.0 and lot > 0:
            spec = self.connector.get_symbol_specs(sig.symbol)
            min_vol = spec.min_volume if spec else 0.001
            vol_step = spec.volume_step if spec else 0.001
            lot = max(min_vol, round(round((lot * multiplier) / vol_step) * vol_step, 4))
            risk = round(risk * multiplier, 2)

        tp_price = sig.tp_price
        if target_rr_cap:
            risk_dist = abs(sig.entry_price - sig.sl_price)
            if sig.direction == Direction.BUY:
                capped_tp = sig.entry_price + (risk_dist * target_rr_cap)
                if capped_tp < tp_price:
                    tp_price = round(capped_tp, 4)
            else:
                capped_tp = sig.entry_price - (risk_dist * target_rr_cap)
                if capped_tp > tp_price:
                    tp_price = round(capped_tp, 4)

        # Real-time price consistency and maximum risk safety check
        is_safe, safety_msg = self._validate_execution_safety(sig, lot, balance)
        if not is_safe:
            logger.error(f"[EXECUTION VETO] {sig.symbol} {sig.direction.value}: {safety_msg}. Aborting trade to protect capital!")
            return

        pyramid_tag = f"[PIRAMIDACIÓN CAPA {layer}/4]" if layer > 1 else "[ENTRADA BASE 1/4]"
        logger.info(
            f"[VOLATILITY TREND] {pyramid_tag} {sig.symbol} {sig.direction.value} @ {sig.entry_price:.4f} | "
            f"SL: {sig.sl_price:.4f} | TP: {tp_price:.4f} | Lot: {lot} | Risk: ${risk:.2f} | Tier: {tier}"
        )
        logger.info(f"   Model: {sig.setup_model} | Confluences: {', '.join(sig.confluences)}")

        order_res = self.connector.send_order(
            symbol=sig.symbol,
            order_type=sig.direction.value,
            volume=lot,
            price=sig.entry_price,
            sl=sig.sl_price,
            tp=tp_price,
            magic=self.config["bot"]["magic_number"],
            comment=f"QVOL:{tier[:6]}-L{layer}",
        )
        if not order_res or getattr(order_res, "retcode", None) not in (10009, 10008):
            logger.warning(f"Order not filled by broker: retcode={getattr(order_res, 'retcode', 'None')} ({getattr(order_res, 'comment', '')})")
            return

        ticket = getattr(order_res, "order", int(time.time()))
        self.journal.record_entry(
            ticket=ticket,
            symbol=sig.symbol,
            direction=sig.direction.value,
            entry_price=sig.entry_price,
            sl_price=sig.sl_price,
            tp_price=tp_price,
            volume=lot,
            strategy=f"{sig.setup_model}-{tier}-L{layer}",
            confluences=", ".join(sig.confluences),
        )
        self.bars_held[ticket] = 0

    def _evaluate_scale_in_eligibility(
        self,
        symbol: str,
        sym_positions: List[Any],
        df_m5: pd.DataFrame,
        point: float = 0.01,
    ) -> Tuple[bool, str, str]:
        """
        Evaluate if an open symbol is eligible for institutional scale-in:
        1. Risk-Free Pyramiding: existing position is at Break-Even or in profit >= 1.0R.
        2. Two-Stage Structural Retest: exactly 1 position, pullback testing EMA-50 with swing intact.
        """
        if len(sym_positions) >= 2:
            return False, "MAX_LAYERS_REACHED", "Symbol already has 2 active layers (base + scale-in)."

        pos = sym_positions[0]
        price_open = float(getattr(pos, "price_open", 0.0))
        sl = float(getattr(pos, "sl", 0.0))
        profit = float(getattr(pos, "profit", 0.0))
        direction = Direction.BUY if getattr(pos, "type", 0) == 0 else Direction.SELL

        c_close = float(df_m5["close"].iloc[-1])
        gain = (c_close - price_open) if direction == Direction.BUY else (price_open - c_close)
        sl_dist = abs(price_open - sl) if sl > 0 else 1.0

        # Check 1: Risk-Free Pyramiding
        # Existing SL is at Break-Even or better, or trade has captured >= 0.90 R
        sl_at_be = (direction == Direction.BUY and sl >= price_open - (point * 5.0)) or \
                   (direction == Direction.SELL and sl > 0 and sl <= price_open + (point * 5.0))
        is_risk_free = sl_at_be or (gain >= sl_dist * 0.90)

        if is_risk_free and profit >= 0:
            return True, "PYRAMID_RISK_FREE", f"Base position is risk-free at BE (gain={gain:.2f} pts). Adding continuation pyramid."

        # Check 2: Two-Stage Deep Retest Scale-In
        # Price has dipped deeper into Value Zone (between EMA-20 and EMA-50),
        # but swing low/high is fully intact (drawdown <= 0.65R) and M5 shows absorption.
        spec = self.connector.get_symbol_specs(symbol)
        if spec.category == "volatility" and gain < 0:
            drawdown_r = abs(gain) / sl_dist
            if drawdown_r <= 0.65:
                curr_bar = df_m5.iloc[-1]
                bar_range = max(float(curr_bar["high"]) - float(curr_bar["low"]), 1e-5)
                if direction == Direction.BUY:
                    wick = (min(float(curr_bar["open"]), float(curr_bar["close"])) - float(curr_bar["low"])) / bar_range
                else:
                    wick = (float(curr_bar["high"]) - max(float(curr_bar["open"]), float(curr_bar["close"]))) / bar_range

                if wick >= 0.12 or float(curr_bar["close"]) > float(curr_bar["open"]):
                    return True, "DEEP_RETEST_STAGE_2", f"Healthy Value Zone retest ({drawdown_r:.2f}R drawdown with absorption wick). Adding Stage 2 entry."

        return False, "BLOCKED", f"Existing position has active risk ({gain:.2f} pts, SL not at BE). Scale-in blocked to protect capital."

    def _manage_open_positions(self, open_positions: List[Any]):
        """Manage active trades: break-even, time-decay exit, and profit lock."""
        for pos in open_positions:
            ticket = int(getattr(pos, "ticket", 0))
            if ticket == 0:
                continue

            symbol = getattr(pos, "symbol", "")
            profit = float(getattr(pos, "profit", 0.0))
            price_open = float(getattr(pos, "price_open", 0.0))
            price_current = float(getattr(pos, "price_current", 0.0))
            sl = float(getattr(pos, "sl", 0.0))
            direction = Direction.BUY if getattr(pos, "type", 0) == 0 else Direction.SELL

            # Calculate real elapsed holding time in minutes
            open_timestamp = float(getattr(pos, "time", 0))
            holding_minutes = (time.time() - open_timestamp) / 60.0 if open_timestamp > 0 else 0.0
            holding_bars = int(holding_minutes / 5.0)

            # Time-decay exit for Boom/Crash: if no spike after 8 candles (40 real minutes), close to protect capital
            spec = self.connector.get_symbol_specs(symbol)
            max_holding_minutes = self.spike_strategy.max_holding_bars * 5.0  # 8 M5 bars = 40 minutes

            if spec.category == "boom_crash" and self.config.get("execution", {}).get("time_decay_exit_enabled", True):
                # Structural evaluation: Check if price action is still respecting the key ceiling (Crash) or floor (Boom)
                df_m5_pos = self.connector.get_rates(symbol, "M5", count=45)
                is_holding, struct_msg, touches, key_level = self.spike_strategy.is_structure_holding(
                    symbol, direction, price_open, df_m5_pos, point=spec.point
                )

                # Hard cap on holding time: Even if structure holds, exit if holding >= max_holding_minutes
                time_expired = holding_minutes >= max_holding_minutes and profit <= 0
                structure_broken = (not is_holding) and holding_bars >= 2 and profit <= 0

                if time_expired or structure_broken:
                    reason_tag = "TIEMPO_MAXIMO_AGOTADO" if time_expired else "ESTRUCTURA_INVALIDADA"
                    logger.info(
                        f"[TIME-DECAY EXIT] Cerrando {symbol} ticket {ticket} tras {holding_minutes:.1f} minutos ({holding_bars} velas M5). "
                        f"Motivo: {reason_tag} ({struct_msg})"
                    )
                    self.connector.close_position(ticket)
                    self.journal.record_exit(ticket, price_current, profit, reason="TIME_DECAY_EXIT")
                    continue
                elif is_holding:
                    level_name = "techo" if direction == Direction.SELL else "piso"
                    logger.info(
                        f"[ESTRUCTURA RESPETADA] {symbol} ticket {ticket} | {level_name.capitalize()} en {key_level:.2f} "
                        f"respetado ({touches} testeos/rechazos, {holding_bars}/{self.spike_strategy.max_holding_bars} velas). Manteniendo posición."
                    )

            # Dynamic Risk Protection: Universal 2-Stage Bracket Management
            tp = float(getattr(pos, "tp", 0.0))
            tp_dist = abs(tp - price_open) if tp > 0 else 0.0
            sl_dist = abs(price_open - sl) if sl > 0 else 0.0
            gain = (price_current - price_open) if direction == Direction.BUY else (price_open - price_current)

            # NOTE: For Boom & Crash indices, Break-Even is disabled because deterministic tick drift
            # naturally walks price back down to re-test the entry floor before firing the continuation spike.
            # Moving SL to BE causes premature stops right before the winning spike.
            if spec.category == "boom_crash":
                # For Boom & Crash: lock profit as soon as a spike delivers >= 0.75 R
                # This protects cash against the slow deterministic tick drift retracement
                if sl_dist > 0 and gain >= sl_dist * 0.75:
                    lock_dist = max(gain * 0.40, spec.point * 20.0)
                    target_sl = round(price_open + (lock_dist if direction == Direction.BUY else -lock_dist), spec.digits)
                    if (direction == Direction.BUY and target_sl > sl) or (direction == Direction.SELL and (sl == 0 or target_sl < sl)):
                        logger.info(f"[SPIKE PROFIT LOCK] Securing gain for {symbol} (ticket {ticket}) @ {target_sl} (Gain: {gain:.2f} pts).")
                        self.connector.modify_order(ticket, sl=target_sl, tp=tp)
                continue

            # Stage 2: Lock 50% of the gain when price reaches 75% progress towards TP (Volatility indices only)
            if tp_dist > 0 and gain >= tp_dist * 0.75:
                lock_dist = gain * 0.50
                target_sl = round(price_open + (lock_dist if direction == Direction.BUY else -lock_dist), spec.digits)
                if (direction == Direction.BUY and target_sl > sl) or (direction == Direction.SELL and (sl == 0 or target_sl < sl)):
                    logger.info(f"[PROFIT LOCK 50%] Securing gain for {symbol} (ticket {ticket}) @ {target_sl}.")
                    self.connector.modify_order(ticket, sl=target_sl, tp=tp)
                    continue

            # Stage 1: Move to Break-even (+ small buffer) when reaching 50% progress towards TP or 1.0R (Volatility indices only)
            be_threshold = min(tp_dist * 0.50, sl_dist * 1.0) if tp_dist > 0 and sl_dist > 0 else (sl_dist * 1.0 if sl_dist > 0 else 0.0)
            if be_threshold > 0 and gain >= be_threshold:
                buffer_pts = spec.point * 3.0
                new_sl = round(price_open + (buffer_pts if direction == Direction.BUY else -buffer_pts), spec.digits)
                if (direction == Direction.BUY and new_sl > sl) or (direction == Direction.SELL and (sl == 0 or new_sl < sl)):
                    logger.info(f"[BREAK-EVEN] Moving SL to entry for {symbol} (ticket {ticket}) @ {new_sl}.")
                    self.connector.modify_order(ticket, sl=new_sl, tp=tp)

    def stop(self):
        """Stop bot and disconnect safely."""
        self.running = False
        logger.info("Quantum-Deriv Bot stopped cleanly.")


def acquire_singleton_lock(port: int = 28991) -> Optional[Any]:
    """Ensure strictly one instance of run_deriv_bot runs at any time."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return s
    except socket.error:
        logger.error(
            f"[SINGLETON LOCK] Ya existe otra instancia de run_deriv_bot activa (puerto {port} ocupado). "
            f"Terminando este proceso para evitar duplicacion de ordenes."
        )
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Quantum-Deriv Synthetic Indices Bot")
    parser.add_argument("--config", default="config/deriv_synthetic/settings.yaml", help="Path to config file")
    parser.add_argument("--server", default=None, help="Deriv MT5 Server (e.g. Deriv-Demo or Deriv-Server)")
    parser.add_argument("--login", type=int, default=None, help="Deriv MT5 account login number")
    parser.add_argument("--password", type=str, default=None, help="Deriv MT5 account password")
    parser.add_argument("--dry-run", action="store_true", help="Run in mock/dry-run mode without sending real orders")
    parser.add_argument("--iterations", type=int, default=None, help="Number of scan iterations to run (optional, default runs indefinitely)")
    args = parser.parse_args()

    # Enforce strictly 1 instance running in Windows
    _lock_socket = acquire_singleton_lock()

    bot = QuantumDerivBot(config_path=args.config, dry_run=args.dry_run)
    bot.start(login=args.login, password=args.password, server=args.server, max_iterations=args.iterations)


if __name__ == "__main__":
    main()
