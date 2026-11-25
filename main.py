"""QuantTradingBot main execution loop and orchestrator."""
import argparse
import math
import signal
import sys
import time
import logging
from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta, timezone
import pandas as pd

from core.config_loader import load_config, AppConfig, apply_execution_authorization
from core.constants import BotMode, Direction, TradeState, DrawdownState, MarketRegime
from core.models import TradeSetup, Signal, MarketSnapshot, TradeRecord
from data.downloader import DataDownloader
from data.cleaner import DataCleaner
from data.cache import DataCache
from indicators.trend import calculate_all_trend_indicators
from indicators.momentum import calculate_all_momentum_indicators
from indicators.volatility import calculate_all_volatility_indicators
from indicators.volume import calculate_all_volume_indicators
from indicators.structure import get_market_structure
from strategy.scorer import ScoreEngine
from filters.context_filter import ContextFilter
from filters.market_quality_filter import MarketQualityFilter
from risk.position_size import PositionSizeCalculator
from risk.stop_loss import StopLossCalculator
from risk.take_profit import TakeProfitCalculator
from risk.drawdown import DrawdownTracker
from risk.exposure import ExposureManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from execution.position_manager import PositionManager
from execution.execution_guard import ExecutionGuard
from database.database import Database
from database.repository import TradeRepository, SignalRepository, DailyStatsRepository, DecisionContextRepository, RuntimeStateRepository
from strategy.decision_context import build_decision_context
from strategy.sweep_strategy import SweepReversalStrategy, SweepReversalSignal
from strategy.asian_breakdown import AsianBreakdownStrategy, AsianBreakdownSignal
from strategy.alchemist import detect_alchemist_setup
from monitoring.logger import setup_logging, get_logger
from monitoring.alerts import AlertManager, ConsoleAlertProvider, TelegramAlertProvider
from monitoring.dashboard import Dashboard

logger = get_logger("QuantTradingBot")


class QuantTradingBot:
    """Master orchestrator for QuantTradingBot V1."""

    def __init__(self, config_dir: str = "config", mode_override: Optional[str] = None):
        self.config: AppConfig = load_config(config_dir)
        if mode_override:
            self.config.bot.mode = BotMode(mode_override.lower())
            apply_execution_authorization(self.config)

        self.running = False
        self.last_bar_time: Dict[str, Optional[datetime]] = {}

        # 1. Logger
        setup_logging(log_dir="logs", log_level="INFO", bot_name=self.config.bot.name)
        logger.info(f"Initializing {self.config.bot.name} (Mode: {self.config.bot.mode.value.upper()})")

        # 2. Database & Repositories
        self.db = Database(db_path=self.config.bot.journal_path)
        self.db.create_tables()
        self.trade_repo = TradeRepository(self.db)
        self.signal_repo = SignalRepository(self.db)
        self.decision_context_repo = DecisionContextRepository(self.db)
        self.stats_repo = DailyStatsRepository(self.db)
        self.runtime_state_repo = RuntimeStateRepository(self.db)

        # 3. Alerts & Dashboard
        self.alerts = AlertManager()
        self.alerts.add_provider(ConsoleAlertProvider())
        self.alerts.add_provider(TelegramAlertProvider())
        self.dashboard = Dashboard(bot_name=self.config.bot.name)

        # 4. Data Pipeline
        self.downloader = DataDownloader()
        self.cleaner = DataCleaner()
        self.cache = DataCache(default_ttl_seconds=30)

        # 5. Core Engines
        self.score_engine = ScoreEngine(self.config)
        self.context_filter = ContextFilter(self.config)
        self.market_quality_filter = MarketQualityFilter(self.config)
        self.sweep_strategy = SweepReversalStrategy(self.config)
        self.asian_breakdown = AsianBreakdownStrategy(self.config)

        # 6. Risk Engine
        self.pos_sizer = PositionSizeCalculator()
        self.sl_calc = StopLossCalculator()
        self.tp_calc = TakeProfitCalculator()
        self.drawdown_tracker = DrawdownTracker(
            max_daily_loss_pct=self.config.risk.max_daily_loss,
            max_account_drawdown_pct=self.config.risk.max_drawdown,
            max_consecutive_losses=self.config.risk.max_consecutive_losses
        )
        self.exposure_mgr = ExposureManager(
            max_open_positions=self.config.risk.max_open_positions,
            max_positions_per_symbol=self.config.risk.max_positions_per_symbol
        )

        # 7. Execution Engine
        self.connector = MT5Connector()
        self.order_mgr = OrderManager(self.connector, self.config.bot)
        self.pos_mgr = PositionManager(self.connector, self.order_mgr, self.config, self.trade_repo)
        # SignalRepository owns the durable, unique signal id used for
        # idempotency.  Passing TradeRepository here previously disabled the
        # duplicate check altogether.
        self.guard = ExecutionGuard(self.config, self.signal_repo)
        self._drawdown_day = None
        self._last_reconciliation_time = 0.0
        # Closed-bar contexts are reused by position and pending-order
        # management, avoiding a trailing stop that runs blind between signals.
        self.market_contexts: Dict[str, Dict[str, object]] = {}
        self._clock_safe = True
        self._last_server_time: Optional[datetime] = None
        self._last_market_closed_log: Dict[str, float] = {}
        self._pending_cancel_cooldown: Dict[int, float] = {}

        # 8. ML Meta-Filter (Shadow Mode by default)
        from filters.ml_filter import MLFilter
        effective_shadow_mode = (
            self.config.ml.shadow_mode or not self.config.ml.allow_active_filter
        )
        self.ml_filter = MLFilter(
            enabled=self.config.ml.enabled,
            shadow_mode=effective_shadow_mode,
            model_path=self.config.ml.model_path,
            min_confidence=self.config.ml.min_confidence,
        )
        if self.ml_filter.is_active:
            logger.info(f"ML Filter loaded ({'SHADOW' if effective_shadow_mode else 'ACTIVE'} mode)")
        else:
            logger.info("ML Filter: No model loaded — operating without ML filtering.")

        # Jev is an optional, asynchronous observer. It stays disabled unless
        # a profile explicitly opts in and a server-side API key is configured.
        # 8. Open-Source System 1 Decision Hub (Laya, Kev, NanoJev, or Jev)
        from filters.system_one import SystemOneHub
        self.system_one_hub = SystemOneHub(
            enabled=self.config.jev.enabled,
            provider_name=getattr(self.config.jev, "provider", "laya"),
            shadow_mode=self.config.jev.shadow_mode,
            device=getattr(self.config.jev, "device", "cuda"),
            model_name=self.config.jev.model,
            timeout_seconds=self.config.jev.timeout_seconds,
        )
        from filters.jev_filter import JevShadowFilter
        self.jev_filter = JevShadowFilter(
            enabled=self.config.jev.enabled,
            shadow_mode=self.config.jev.shadow_mode,
            model=self.config.jev.model,
            timeout_seconds=self.config.jev.timeout_seconds,
        )
        if self.config.jev.enabled:
            logger.info(
                "System 1 Decision Hub configured (Provider: %s, Device: %s, Mode: %s)",
                getattr(self.config.jev, "provider", "laya").upper(),
                getattr(self.config.jev, "device", "cuda"),
                "SHADOW" if self.config.jev.shadow_mode else "ACTIVE",
            )

        # 8.5 Kronos Foundation Model Meta-Filter (K-line autoregressive trajectory forecasting)
        from filters.kronos_filter import KronosFilter
        kronos_cfg = getattr(self.config, "kronos", None)
        self.kronos_filter = KronosFilter(
            enabled=bool(kronos_cfg and kronos_cfg.enabled),
            shadow_mode=bool(kronos_cfg.shadow_mode if kronos_cfg else True),
            model_name=getattr(kronos_cfg, "model_name", "NeoQuasar/Kronos-small"),
            tokenizer_name=getattr(kronos_cfg, "tokenizer_name", "NeoQuasar/Kronos-Tokenizer-base"),
            device=getattr(kronos_cfg, "device", "cpu"),
            lookback_bars=getattr(kronos_cfg, "lookback_bars", 200),
            forecast_horizon=getattr(kronos_cfg, "forecast_horizon", 12),
            temperature=getattr(kronos_cfg, "temperature", 0.7),
            sample_count=getattr(kronos_cfg, "sample_count", 2),
            min_confidence=getattr(kronos_cfg, "min_confidence", 0.60),
        )
        if self.kronos_filter.is_active:
            logger.info(
                "Kronos Foundation Filter loaded (%s mode, Model: %s)",
                "SHADOW" if self.kronos_filter.shadow_mode else "ACTIVE",
                self.kronos_filter.model_name,
            )
        else:
            logger.info("Kronos Filter: Operating without Kronos trajectory filtering.")

        # 9. Neural Memory Bank (Closed-Loop Learning Engine)
        from learning.memory_bank import NeuralMemoryBank
        self.memory_bank = NeuralMemoryBank(self.db)
        logger.info("Neural Memory Bank initialized (Closed-Loop Learning active).")

        # Signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.warning(f"Received termination signal {signum}. Initiating graceful shutdown...")
        self.running = False

    def connect(self) -> bool:
        """Establish MT5 connection if in DEMO, LIVE, or PAPER mode."""
        if self.config.bot.mode == BotMode.BACKTEST:
            return True

        success = self.connector.initialize()
        if not success:
            logger.error("Failed to connect to MetaTrader 5 terminal.")
            return False

        account = self.connector.get_account_info()
        if account is None and self.config.bot.mode in (BotMode.DEMO, BotMode.LIVE):
            logger.critical("Startup blocked: MT5 account information is unavailable.")
            self.connector.shutdown()
            return False
        if self.config.bot.mode in (BotMode.DEMO, BotMode.LIVE) and not self.config.bot.execution_authorized:
            logger.critical(
                "Startup blocked: validation status '%s' does not authorise %s execution.",
                self.config.validation.status,
                self.config.bot.mode.value.upper(),
            )
            self.connector.shutdown()
            return False
        if account:
            if self.config.bot.mode in (BotMode.DEMO, BotMode.LIVE):
                account_ok, account_reason = self.connector.validate_execution_account(
                    mode=self.config.bot.mode,
                    allow_live_trading=self.config.bot.allow_live_trading,
                )
                if not account_ok:
                    logger.critical("Startup blocked: %s", account_reason)
                    self.connector.shutdown()
                    return False
            logger.info(f"Connected to MT5 - Account #{account.login} (${account.balance:,.2f} {account.currency})")
            now = self._server_now()
            self._clock_safe = self._check_clock_skew(now)
            if not self._clock_safe and self.config.bot.mode in (BotMode.DEMO, BotMode.LIVE):
                logger.critical("Startup blocked: local clock and MT5 tick clock are not synchronized.")
                return False
            day = now.strftime("%Y-%m-%d")
            starting_balance = self.stats_repo.ensure_daily_start_balance(day, account.balance)
            self.drawdown_tracker.reset_day(starting_balance=starting_balance)
            stored_peak = self.runtime_state_repo.get("account_peak_equity", account.equity)
            self.drawdown_tracker.peak_equity = max(float(stored_peak or 0.0), float(account.equity))
            self._drawdown_day = now.date()
            self.reconcile_mt5_state()
        return True

    @staticmethod
    def _jev_number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _jev_context_snapshot(cls, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Whitelist compact derived facts; omit raw prices and account data."""
        if not isinstance(context, dict):
            return {}

        def fields(source: Any, names: tuple[str, ...]) -> Dict[str, Any]:
            if not isinstance(source, dict):
                return {}
            result: Dict[str, Any] = {}
            for name in names:
                value = source.get(name)
                if isinstance(value, (str, bool, int)):
                    result[name] = value
                elif isinstance(value, (float,)):
                    number = cls._jev_number(value)
                    if number is not None:
                        result[name] = number
            return result

        liquidity = context.get("liquidity", {})
        smc = context.get("smc", {})
        fib = context.get("fibonacci", {})
        return {
            "regime": fields(context.get("regime"), ("label", "atr_ratio", "adx", "bb_width_ratio")),
            "session": fields(context.get("session"), ("label",)),
            "liquidity": {
                "sweep": fields(
                    liquidity.get("sweep") if isinstance(liquidity, dict) else None,
                    ("detected", "direction", "level", "penetration_atr"),
                ),
                "nearest_level": fields(
                    liquidity.get("nearest_level") if isinstance(liquidity, dict) else None,
                    ("name", "side", "distance_atr"),
                ),
            },
            "smc": {
                **fields(smc, ("sweep_aligned", "fvg_aligned", "order_block_aligned")),
                "fvg": fields(
                    smc.get("fair_value_gap") if isinstance(smc, dict) else None,
                    ("available", "direction", "age_bars"),
                ),
                "order_block": fields(
                    smc.get("order_block") if isinstance(smc, dict) else None,
                    ("available", "direction", "age_bars"),
                ),
            },
            "fibonacci": fields(
                fib,
                ("available", "direction", "nearest_level", "nearest_distance_atr", "in_zone"),
            ),
        }

    def _observe_jev_shadow(
        self,
        trade_setup: TradeSetup,
        *,
        strategy: str,
        df_entry: pd.DataFrame,
        spread_points: float,
        point_size: float,
        decision_context: Optional[Dict[str, Any]] = None,
        strategy_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send a small closed-bar candidate snapshot to Jev asynchronously."""
        if not getattr(self, "jev_filter", None) or not self.config.jev.enabled:
            return
        signal_obj = trade_setup.signal
        score = signal_obj.score_result
        atr_value = self._jev_number(df_entry["atr"].iloc[-1]) if "atr" in df_entry.columns and not df_entry.empty else None
        close_value = self._jev_number(df_entry["close"].iloc[-1]) if "close" in df_entry.columns and not df_entry.empty else None
        entry_value = self._jev_number(trade_setup.entry_price)
        sl_value = self._jev_number(trade_setup.sl_price)
        tp_value = self._jev_number(trade_setup.tp_price)
        spread_value = self._jev_number(spread_points)
        point_value = self._jev_number(point_size)

        def distance_atr(left: Optional[float], right: Optional[float]) -> Optional[float]:
            if left is None or right is None or not atr_value or atr_value <= 0:
                return None
            return round(abs(left - right) / atr_value, 4)

        bar_time: Optional[str] = None
        if not df_entry.empty:
            raw_bar_time = df_entry["time"].iloc[-1] if "time" in df_entry.columns else None
            if raw_bar_time is not None:
                bar_time = str(raw_bar_time)
        state = {
            "strategy": strategy,
            "symbol": signal_obj.symbol,
            "timeframe": signal_obj.timeframe,
            "direction": signal_obj.direction.value,
            "closed_bar_time": bar_time,
            "score": {
                "total": self._jev_number(score.total_score),
                "classification": score.classification.value,
                "structure": self._jev_number(score.structure.score),
                "trend": self._jev_number(score.trend.score),
                "momentum": self._jev_number(score.momentum.score),
                "volume": self._jev_number(score.volume.score),
                "volatility": self._jev_number(score.volatility.score),
            },
            "plan": {
                "reward_risk": self._jev_number(trade_setup.risk_reward_ratio),
                "stop_distance_atr": distance_atr(entry_value, sl_value),
                "target_distance_atr": distance_atr(entry_value, tp_value),
                "entry_displacement_from_close_atr": (
                    round((entry_value - close_value) / atr_value, 4)
                    if entry_value is not None and close_value is not None and atr_value and atr_value > 0
                    else None
                ),
                "spread_to_atr": (
                    round((spread_value * point_value) / atr_value, 6)
                    if spread_value is not None and point_value is not None and atr_value and atr_value > 0
                    else None
                ),
            },
            "market_evidence": self._jev_context_snapshot(decision_context),
            "strategy_evidence": strategy_context or {},
        }
        stable_bar_key = bar_time or signal_obj.created_at.isoformat()
        dedupe_key = "|".join((signal_obj.symbol, signal_obj.timeframe, signal_obj.direction.value, strategy, stable_bar_key))
        if hasattr(self, "system_one_hub") and self.system_one_hub.enabled:
            self.system_one_hub.observe(
                dedupe_key=dedupe_key,
                signal_id=signal_obj.signal_id,
                state=state,
            )
        if getattr(self, "jev_filter", None) and self.jev_filter.is_configured:
            self.jev_filter.observe(
                dedupe_key=dedupe_key,
                signal_id=signal_obj.signal_id,
                state=state,
            )

    def _server_now(self, symbol: Optional[str] = None) -> datetime:
        """Return UTC wall time and sample MT5 time for a look-ahead check.

        A last quote naturally becomes stale while the market is closed, so
        it cannot be used as the process clock. A future quote, on the other
        hand, indicates an unsafe timestamp mismatch and blocks execution.
        """
        symbols = [symbol] if symbol else list(self.config.market.symbols)
        candidate_symbols = list(symbols) + ["BTCUSD", "EURUSD", "GBPUSD", "USDJPY"]
        local_now = datetime.now(timezone.utc)
        max_skew = float(self.config.bot.max_clock_skew_seconds)

        for name in candidate_symbols:
            if not name:
                continue
            try:
                tick = self.connector.get_symbol_tick(name)
                stamp = getattr(tick, "time", None) if tick else None
                if stamp:
                    candidate_time = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
                    raw_skew = (candidate_time - local_now).total_seconds()
                    offset_hours = round(raw_skew / 3600.0)
                    residual_skew = abs(raw_skew - (offset_hours * 3600.0))
                    
                    if self._last_server_time is None or residual_skew <= max_skew:
                        self._last_server_time = candidate_time
                        if residual_skew <= max_skew:
                            break
            except Exception:
                logger.debug("Unable to obtain MT5 clock from %s", name, exc_info=True)
        return datetime.now(timezone.utc)

    def _check_clock_skew(self, server_now: datetime) -> bool:
        local_now = datetime.now(timezone.utc)
        if self._last_server_time is None:
            logger.warning("No MT5 tick timestamp available for clock sanity check.")
            return False
        raw_skew = (self._last_server_time - local_now).total_seconds()
        # Broker server clocks operate on an integer hour offset relative to UTC (e.g. UTC+3 for EET/EEST).
        offset_hours = round(raw_skew / 3600.0)
        adjusted_skew = abs(raw_skew - (offset_hours * 3600.0))
        max_skew = float(self.config.bot.max_clock_skew_seconds)

        # Look-ahead check: Si el tick está en el FUTURO más allá de max_skew, es peligroso
        future_skew = raw_skew - (offset_hours * 3600.0)
        if future_skew > max_skew:
            logger.error(
                "MT5 quote clock is %.1fs in the FUTURE compared to UTC (after %+dh broker offset); execution blocked.",
                future_skew, offset_hours,
            )
            return False

        if adjusted_skew > max_skew:
            # El tick está en el pasado (mercado inactivo/cerrado o símbolo específico pausado)
            # Intentar verificar contra símbolos de referencia 24/7 (BTC/EURUSD)
            verified_ref = False
            for ref_symbol in ["BTCUSD", "EURUSD", "GBPUSD", "USDJPY"]:
                if ref_symbol in self.config.market.symbols:
                    continue
                try:
                    ref_tick = self.connector.get_symbol_tick(ref_symbol)
                    ref_stamp = getattr(ref_tick, "time", None) if ref_tick else None
                    if ref_stamp:
                        ref_time = datetime.fromtimestamp(int(ref_stamp), tz=timezone.utc)
                        r_skew = (ref_time - local_now).total_seconds()
                        r_offset = round(r_skew / 3600.0)
                        r_residual = abs(r_skew - (r_offset * 3600.0))
                        if r_residual <= max_skew:
                            logger.info(
                                "MT5 broker clock verified via %s (offset: %+dh, residual skew: %.2fs). "
                                "Local symbol quotes are currently dormant/closed (last tick age: %.1fs).",
                                ref_symbol, r_offset, r_residual, adjusted_skew,
                            )
                            verified_ref = True
                            self._last_server_time = ref_time
                            offset_hours = r_offset
                            adjusted_skew = r_residual
                            break
                except Exception:
                    pass
            if not verified_ref:
                logger.error(
                    "MT5 quote clock is %.1fs desynchronized from UTC (after %+dh broker offset); execution blocked.",
                    adjusted_skew, offset_hours,
                )
                return False

        logger.info("MT5 quote clock synchronized (broker offset: %+dh, residual skew: %.2fs).", offset_hours, adjusted_skew)
        return True

    def _market_regime(self, df_entry: pd.DataFrame) -> MarketRegime:
        """Classify the stored entry context without inventing a new signal."""
        last = df_entry.iloc[-1]
        atr_ratio = float(last.get("atr_ratio", 1.0) or 1.0)
        adx = float(last.get("adx", 0.0) or 0.0)
        if atr_ratio >= self.config.volatility.max_atr_ratio:
            return MarketRegime.HIGH_VOLATILITY
        if atr_ratio < self.config.volatility.min_atr_ratio:
            return MarketRegime.LOW_VOLATILITY
        if adx >= self.config.trend.adx_min:
            return MarketRegime.TRENDING
        return MarketRegime.RANGING

    def _entry_history_bars(self) -> int:
        """Keep sufficient closed history to describe a completed prior session."""
        return max(250, self.config.decision_context.history_bars)

    def _build_decision_context(
        self,
        df_entry: pd.DataFrame,
        structure_analysis,
        direction: Direction,
        df_setup: Optional[pd.DataFrame] = None,
        structure_score: Optional[float] = None,
    ) -> Optional[Dict[str, object]]:
        if not self.config.decision_context.enabled:
            return None
        try:
            ctx = build_decision_context(
                df_entry, structure_analysis, direction,
                min_atr_ratio=self.config.volatility.min_atr_ratio,
                max_atr_ratio=self.config.volatility.max_atr_ratio,
                adx_min=self.config.trend.adx_min,
                equal_level_tolerance_atr=self.config.decision_context.equal_level_tolerance_atr,
                fib_zone_atr=self.config.decision_context.fib_zone_atr,
                smc_max_age_bars=self.config.decision_context.smc_max_age_bars,
                smc_min_gap_atr=self.config.decision_context.smc_min_gap_atr,
                sweep_min_penetration_atr=self.config.decision_context.sweep_min_penetration_atr,
                min_bb_width_ratio=self.config.regime_filter.min_bb_width_ratio,
                max_range_bb_width_ratio=self.config.regime_filter.max_range_bb_width_ratio,
            )
            if ctx is not None:
                if structure_score is not None:
                    ctx["structure_score"] = float(structure_score)
                elif structure_analysis and hasattr(structure_analysis, "score"):
                    ctx["structure_score"] = float(structure_analysis.score)

                if df_setup is not None and not df_setup.empty and len(df_setup) >= 20:
                    last_setup = df_setup.iloc[-1]
                    ema_fast_h4 = float(last_setup.get("ema_fast", 0.0) or 0.0)
                    ema_med_h4 = float(last_setup.get("ema_medium", 0.0) or 0.0)
                    ema_slow_h4 = float(last_setup.get("ema_slow", 0.0) or 0.0)
                    close_h4 = float(last_setup.get("close", 0.0) or 0.0)
                    if close_h4 < ema_slow_h4 and ema_fast_h4 < ema_med_h4:
                        ctx["macro_direction"] = "SELL"
                    elif close_h4 > ema_slow_h4 and ema_fast_h4 > ema_med_h4:
                        ctx["macro_direction"] = "BUY"
            return ctx
        except Exception as exc:
            # Observation must never make the execution path unavailable.
            logger.warning("Could not build decision context: %s", exc)
            return None

    @staticmethod
    def _entry_features(df_entry: pd.DataFrame) -> Dict[str, float]:
        """Take a serialisable indicator snapshot for trade auditability."""
        if df_entry is None or df_entry.empty:
            return {}
        last = df_entry.iloc[-1]
        columns = (
            "close", "ema_fast", "ema_medium", "ema_slow", "adx", "rsi",
            "macd", "macd_histogram", "atr", "atr_ratio", "volume_ratio", "obv",
        )
        features: Dict[str, float] = {}
        for column in columns:
            value = last.get(column)
            if value is not None and pd.notna(value):
                features[column] = float(value)
        return features

    def _persist_open_trade(
        self,
        trade_setup: TradeSetup,
        execution_result,
        df_entry: pd.DataFrame,
        spread_points: float,
    ) -> None:
        """Write the broker-confirmed opening record immediately after fill."""
        now = datetime.now(timezone.utc)
        last = df_entry.iloc[-1]
        entry_price = float(execution_result.price or trade_setup.entry_price)
        actual_risk_pct = (trade_setup.risk_money / self.connector.get_account_info().balance * 100.0) if self.connector.get_account_info() else 0.0
        trade = TradeRecord(
            state=TradeState.OPEN,
            signal_id=trade_setup.signal.signal_id,
            symbol=trade_setup.signal.symbol,
            timeframe=trade_setup.signal.timeframe,
            direction=trade_setup.signal.direction,
            entry_price=entry_price,
            sl_price=trade_setup.sl_price,
            tp_price=trade_setup.tp_price,
            volume=float(execution_result.volume or trade_setup.volume),
            spread_at_entry=spread_points,
            created_at=now,
            entered_at=now,
            risk_percent=round(actual_risk_pct, 4),
            risk_money=trade_setup.risk_money,
            market_regime=self._market_regime(df_entry),
            all_scores=trade_setup.signal.score_result,
            features_at_entry=self._entry_features(df_entry),
            atr_value=float(last.get("atr")) if pd.notna(last.get("atr")) else None,
            rsi_value=float(last.get("rsi")) if pd.notna(last.get("rsi")) else None,
            adx_value=float(last.get("adx")) if pd.notna(last.get("adx")) else None,
            macd_value=float(last.get("macd")) if pd.notna(last.get("macd")) else None,
            ticket=execution_result.ticket,
            magic_number=self.config.bot.magic_number,
            comment=f"QTB:{trade_setup.signal.signal_id}",
        )
        self.trade_repo.save_trade(trade)

    def _persist_pending_trade(
        self,
        trade_setup: TradeSetup,
        execution_result,
        df_entry: pd.DataFrame,
        spread_points: float,
    ) -> None:
        """Journal an acknowledged MT5 pending order before it can fill."""
        now = datetime.now(timezone.utc)
        last = df_entry.iloc[-1]
        account = self.connector.get_account_info()
        actual_risk_pct = (trade_setup.risk_money / account.balance * 100.0) if account else 0.0
        trade = TradeRecord(
            state=TradeState.ORDER_PENDING,
            signal_id=trade_setup.signal.signal_id,
            symbol=trade_setup.signal.symbol,
            timeframe=trade_setup.signal.timeframe,
            direction=trade_setup.signal.direction,
            entry_price=trade_setup.entry_price,
            sl_price=trade_setup.sl_price,
            tp_price=trade_setup.tp_price,
            volume=float(execution_result.volume or trade_setup.volume),
            spread_at_entry=spread_points,
            created_at=now,
            risk_percent=round(actual_risk_pct, 4),
            risk_money=trade_setup.risk_money,
            market_regime=self._market_regime(df_entry),
            all_scores=trade_setup.signal.score_result,
            features_at_entry=self._entry_features(df_entry),
            atr_value=float(last.get("atr")) if pd.notna(last.get("atr")) else None,
            rsi_value=float(last.get("rsi")) if pd.notna(last.get("rsi")) else None,
            adx_value=float(last.get("adx")) if pd.notna(last.get("adx")) else None,
            macd_value=float(last.get("macd")) if pd.notna(last.get("macd")) else None,
            ticket=execution_result.ticket,
            magic_number=self.config.bot.magic_number,
            comment=f"QTB:{trade_setup.signal.signal_id}",
        )
        self.trade_repo.save_trade(trade)

    def _build_entry_context(self, symbol: str) -> Optional[Dict[str, object]]:
        """Build indicators/structure exclusively from confirmed M15 bars."""
        df_raw = self.downloader.download_rates(
            symbol, self.config.timeframes.entry, count=self._entry_history_bars(), closed_only=True
        )
        if df_raw is None or len(df_raw) < 50:
            return None
        df, quality = self.cleaner.clean(df_raw)
        if not quality.get("quality_ok", True):
            return None
        df = calculate_all_trend_indicators(
            df, self.config.trend.ema_fast, self.config.trend.ema_medium,
            self.config.trend.ema_slow, self.config.trend.adx_period,
        )
        df = calculate_all_momentum_indicators(
            df, self.config.momentum.rsi_period, self.config.momentum.macd_fast,
            self.config.momentum.macd_slow, self.config.momentum.macd_signal,
        )
        df = calculate_all_volatility_indicators(
            df, self.config.volatility.atr_period, self.config.volatility.atr_avg_period,
        )
        df = calculate_all_volume_indicators(
            df,
            volume_period=self.config.volume.period,
            volume_col="real_volume" if str(getattr(self.config.volume.volume_mode, "value", self.config.volume.volume_mode)).lower() == "real" else "tick_volume",
            use_obv=self.config.volume.use_obv,
        )
        structure = get_market_structure(
            df=df,
            atr_series=df["atr"],
            swing_lookback=self.config.structure.swing_lookback,
            swing_confirmation=self.config.structure.swing_confirmation,
            bos_min_distance_atr=self.config.structure.bos_min_distance_atr,
            bos_close_confirmation=self.config.structure.bos_close_confirmation,
            use_bos=self.config.structure.use_bos,
            use_choch=self.config.structure.use_choch,
            max_swing_age=self.config.structure.max_swing_age,
        )
        context: Dict[str, object] = {"df_entry": df, "structure": structure}
        self.market_contexts[symbol] = context
        return context

    def _position_management_contexts(self, positions: List[object]) -> tuple[Dict[str, pd.DataFrame], Dict[str, object], Dict[int, object]]:
        """Supply trailing/BE/partial logic with closed-bar and original-risk data."""
        df_map: Dict[str, pd.DataFrame] = {}
        structure_map: Dict[str, object] = {}
        for symbol in {getattr(position, "symbol", None) for position in positions} - {None}:
            context = self.market_contexts.get(symbol) or self._build_entry_context(symbol)
            if context:
                df_map[symbol] = context["df_entry"]
                structure_map[symbol] = context["structure"]
        trade_context = {
            int(trade.ticket): trade for trade in self.trade_repo.get_open_trades()
            if trade.ticket is not None
        }
        return df_map, structure_map, trade_context

    def _plan_breakout_entry(
        self, direction: Direction, structure, tick, symbol_info, atr_val: float = 2.5
    ) -> tuple[Optional[float], Optional[str], bool]:
        """Choose an entry price for a confirmed structural breakout.

        Returns (entry_price, reason, is_pending).
        If the trigger has not been crossed yet, schedules an expiring pending stop (is_pending=True).
        If the closed bar just confirmed the breakout and price is within tight tolerance (<=0.35 ATR),
        permits a controlled fresh breakout market entry (is_pending=False).
        If the price is overextended (>0.35 ATR), strictly refuses to chase.
        """
        point = float(symbol_info.point)
        min_broker_distance = float(getattr(symbol_info, "trade_stops_level", 0)) + 1.0
        buffer_points = max(float(self.config.trade.breakout_buffer_points), min_broker_distance)

        # Consult Neural Memory Bank for statistically verified empirical adjustments & vetos
        if hasattr(self, "memory_bank") and self.memory_bank:
            # Check negative rule vetos (learning when NOT to trade)
            veto_reason = self.memory_bank.check_veto(getattr(symbol_info, "name", "XAUUSD"))
            if veto_reason:
                return None, veto_reason, True

            active_mods = self.memory_bank.get_active_modifications(getattr(symbol_info, "name", "XAUUSD"), category="PULLBACK")
            buffer_offset = float(active_mods.get("buffer_points_offset", 0.0))
            if buffer_offset > 0:
                buffer_points += buffer_offset
                logger.info(
                    "[%s] [NEURAL MEMORY ACTIVE] Applied +%.1f pts breakout buffer adjustment from verified patterns.",
                    symbol_info.name, buffer_offset
                )

        buffer = buffer_points * point
        max_chase_distance = max(1.5, atr_val * 0.35)

        if direction == Direction.BUY:
            swing = structure.last_swing_high
            if swing is None:
                return None, "No confirmed swing high available for BUY breakout.", True
            trigger = round(float(swing.price) + buffer, symbol_info.digits)
            if trigger > tick.ask:
                return trigger, None, True
            drift = tick.ask - trigger
            if drift <= max_chase_distance:
                # Fresh breakout candle confirmed -> enter at live market quote
                return round(tick.ask, symbol_info.digits), None, False
            return None, f"BUY breakout overextended by {drift:.2f} pts (> {max_chase_distance:.2f} max allowed).", True

        swing = structure.last_swing_low
        if swing is None:
            return None, "No confirmed swing low available for SELL breakout.", True
        trigger = round(float(swing.price) - buffer, symbol_info.digits)
        if trigger < tick.bid:
            return trigger, None, True
        drift = trigger - tick.bid
        if drift <= max_chase_distance:
            # Fresh breakout candle confirmed -> enter at live market quote
            return round(tick.bid, symbol_info.digits), None, False
        return None, f"SELL breakout overextended by {drift:.2f} pts (> {max_chase_distance:.2f} max allowed).", True

    def _plan_pullback_entry(
        self,
        direction: Direction,
        structure,
        tick,
        symbol_info,
        atr_val: float = 2.5,
        df_entry: Optional[pd.DataFrame] = None,
    ) -> tuple[Optional[float], Optional[str], bool]:
        """Choose an entry price for a confirmed structural pullback/retest.

        Returns (entry_price, reason, is_pending).
        Instead of placing a blind stop order ahead of the move:
        1. Checks that a structural level was already breached.
        2. Ensures price is currently retesting the broken level (support flipped to resistance for SELL, or vice versa).
        3. Confirms rejection on the closed bar, entering at market within the retest zone.
        """
        point = float(symbol_info.point)
        digits = symbol_info.digits
        tolerance = max(point * 10, atr_val * 0.40)

        if direction == Direction.BUY:
            swing = structure.last_swing_high
            if swing is None:
                return None, "No confirmed swing high available for BUY pullback.", False
            level = float(swing.price)
            # Re-test zone: around the broken swing high
            if tick.bid < level - tolerance:
                return None, f"BUY pullback invalid: price {tick.bid:.2f} fell too deep below broken level {level:.2f}.", False
            if tick.ask <= level + tolerance * 1.5:
                if df_entry is not None and len(df_entry) >= 2:
                    last_c = df_entry.iloc[-1]
                    if float(last_c["close"]) < level - tolerance * 0.5:
                        return None, "BUY pullback awaiting bullish close back above retest level.", False
                return round(tick.ask, digits), None, False
            return None, f"Awaiting BUY pullback down to {level:.2f} (current ask {tick.ask:.2f} is extended).", False

        # Direction.SELL
        swing = structure.last_swing_low
        if swing is None:
            return None, "No confirmed swing low available for SELL pullback.", False
        level = float(swing.price)
        # Re-test zone: around the broken swing low (now resistance)
        if tick.ask > level + tolerance:
            return None, f"SELL pullback invalid: price {tick.ask:.2f} rallied too high above breakdown level {level:.2f}.", False
        if tick.bid >= level - tolerance * 1.5:
            if df_entry is not None and len(df_entry) >= 2:
                last_c = df_entry.iloc[-1]
                if float(last_c["close"]) > level + tolerance * 0.5:
                    return None, "SELL pullback awaiting bearish close back below retest level.", False
            return round(tick.bid, digits), None, False
        return None, f"Awaiting SELL pullback up to {level:.2f} (current bid {tick.bid:.2f} is extended in the hole).", False

    def _check_trend_exhaustion_and_support(
        self,
        direction: Direction,
        symbol: str,
        current_price: float,
        df_entry: pd.DataFrame,
        df_setup: Optional[pd.DataFrame],
        df_htf: Optional[pd.DataFrame],
        atr_val: float,
    ) -> tuple[bool, Optional[str]]:
        """
        Mathematical institutional guard against selling into major support or buying into major resistance
        after an exhausted impulse wave.

        1. Extension Check:
           Measures price distance from the macro pivot on H1/D1 against Daily ATR.
           If impulse >= 2.5 * ATR_D1 without a >= 38.2% Fibonacci retracement, veto continuation.
        2. Major Support/Resistance Proximity Zone:
           Vetoes continuation entries within 0.35 * ATR of major daily structural levels.
        """
        if df_setup is None or len(df_setup) < 20:
            return True, None

        daily_atr = atr_val * 2.5
        if df_htf is not None and len(df_htf) >= 14 and "atr" in df_htf.columns:
            try:
                daily_atr = float(df_htf["atr"].iloc[-1])
            except Exception:
                pass
        elif "atr" in df_setup.columns and len(df_setup) >= 24:
            try:
                daily_atr = float(df_setup["atr"].iloc[-1]) * 2.0
            except Exception:
                pass

        lookback = min(len(df_setup), 48)
        recent_setup = df_setup.iloc[-lookback:]

        if direction == Direction.SELL:
            pivot_high = float(recent_setup["high"].max())
            impulse_drop = pivot_high - current_price
            if daily_atr > 0 and impulse_drop >= 2.5 * daily_atr:
                lowest_low = float(recent_setup["low"].min())
                total_range = pivot_high - lowest_low
                if total_range > 0:
                    recent_bounce = current_price - lowest_low
                    fib_retracement = recent_bounce / total_range
                    if fib_retracement < 0.382:
                        return False, f"Downward impulse exhausted: dropped {impulse_drop:.1f} pts ({impulse_drop/daily_atr:.1f}x Daily ATR) with only {fib_retracement*100:.1f}% pullback (<38.2% Fib)."

            major_support = float(recent_setup["low"].min())
            dist_to_support = current_price - major_support
            if 0.0 <= dist_to_support <= (atr_val * 0.35):
                return False, f"Price {current_price:.2f} is within {dist_to_support:.2f} pts (<= 0.35 ATR) of major structural support ({major_support:.2f}). Vetoing SELL into support."

        elif direction == Direction.BUY:
            pivot_low = float(recent_setup["low"].min())
            impulse_rally = current_price - pivot_low
            if daily_atr > 0 and impulse_rally >= 2.5 * daily_atr:
                highest_high = float(recent_setup["high"].max())
                total_range = highest_high - pivot_low
                if total_range > 0:
                    recent_pullback = highest_high - current_price
                    fib_retracement = recent_pullback / total_range
                    if fib_retracement < 0.382:
                        return False, f"Upward impulse exhausted: rallied {impulse_rally:.1f} pts ({impulse_rally/daily_atr:.1f}x Daily ATR) with only {fib_retracement*100:.1f}% pullback (<38.2% Fib)."

            major_resistance = float(recent_setup["high"].max())
            dist_to_resistance = major_resistance - current_price
            if 0.0 <= dist_to_resistance <= (atr_val * 0.35):
                return False, f"Price {current_price:.2f} is within {dist_to_resistance:.2f} pts (<= 0.35 ATR) of major structural resistance ({major_resistance:.2f}). Vetoing BUY into resistance."

        return True, None

    def _execute_sweep_reversal(
        self,
        symbol: str,
        timeframe: str,
        df_entry: pd.DataFrame,
        sweep_sig: SweepReversalSignal,
        open_pos: List[object],
        pending_trades: List[object],
        strategy_name: str = "SWEEP_REVERSAL",
        decision_context: Optional[Dict[str, Any]] = None,
        strategy_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Execute a market reversal order exploiting an institutional liquidity sweep."""
        sym_info = self.connector.get_symbol_info(symbol)
        tick_info = self.connector.get_symbol_tick(symbol)
        if sym_info is None or tick_info is None:
            return

        # 1. News filter check
        if getattr(self.config.filters, "news_filter", False) and hasattr(self, "context_filter") and getattr(self.context_filter, "news_filter", None):
            now_utc = datetime.now(timezone.utc)
            news_res = self.context_filter.news_filter.is_news_blackout(
                current_time_utc=now_utc,
                symbol=symbol,
                config=self.config.filters,
            )
            if not news_res.approved:
                logger.info("[%s] Sweep reversal blocked by News Filter: %s", symbol, ", ".join(news_res.reasons))
                return

        # 1b. Session filter check
        if getattr(self.config.filters, "session_filter", False) and hasattr(self, "context_filter") and getattr(self.context_filter, "session_filter", None):
            now_utc = datetime.now(timezone.utc)
            sym_cfg = self.config.symbols.get(symbol)
            session_res = self.context_filter.session_filter.check(
                current_time_utc=now_utc,
                symbol=symbol,
                config=self.config.filters,
                symbol_config=sym_cfg,
            )
            if not session_res.approved:
                logger.info("[%s] Sweep reversal blocked by Session Filter: %s", symbol, ", ".join(session_res.reasons))
                return

        # 2. Anti-Chasing Drift Guard: prevent entering if price drifted away from trigger
        current_price = tick_info.bid if sweep_sig.direction == Direction.SELL else tick_info.ask
        atr_val = 2.5
        if df_entry is not None and not df_entry.empty and 'atr' in df_entry.columns:
            try:
                atr_val = float(df_entry['atr'].iloc[-1])
            except Exception:
                atr_val = 2.5
        max_allowed_drift = max(1.5, min(3.0, atr_val * 0.25))
        drift = abs(current_price - sweep_sig.entry_price)
        if drift > max_allowed_drift:
            logger.warning(
                "[%s] Sweep reversal BLOCKED by Anti-Chasing Drift Guard: current price %.2f drifted %.2f from trigger %.2f (max allowed: %.2f).",
                symbol, current_price, drift, sweep_sig.entry_price, max_allowed_drift,
            )
            return

        if len(open_pos) + len(pending_trades) >= self.config.risk.max_open_positions:
            logger.info("[%s] Sweep reversal blocked: max open positions reached.", symbol)
            return

        # Check symbol-level exposure account-wide before executing sweep reversal
        sym_positions = self.connector.get_positions(symbol=symbol) or []
        mt5_pending = self.connector.get_pending_orders() if hasattr(self.connector, "get_pending_orders") else []
        mt5_pending_symbol = [o for o in mt5_pending if getattr(o, "symbol", None) == symbol]
        pending_for_sym = [t for t in pending_trades if t.symbol == symbol]
        total_account_exposure = len(sym_positions) + max(len(pending_for_sym), len(mt5_pending_symbol))
        if total_account_exposure >= self.config.risk.max_positions_per_symbol:
            logger.info("[%s] Sweep reversal blocked: account-wide symbol position/pending limit reached (%d/%d).",
                        symbol, total_account_exposure, self.config.risk.max_positions_per_symbol)
            return

        trades_today = self.trade_repo.get_today_trades()
        max_daily = self.config.risk.max_daily_trades
        if max_daily > 0 and len(trades_today) >= max_daily:
            logger.info("[%s] Sweep reversal blocked: daily trade limit reached (%d/%d).", symbol, len(trades_today), max_daily)
            return

        signal_obj = self.sweep_strategy.build_sweep_signal(symbol, timeframe, sweep_sig)

        account_info = self.connector.get_account_info()
        curr_balance = account_info.balance if account_info else 10000.0
        consec_losses = self.trade_repo.get_consecutive_losses(symbol)
        risk_money = self.pos_sizer.calculate_risk_money(
            balance=curr_balance,
            risk_config=self.config.risk,
            score_classification=signal_obj.score_result.classification,
            consecutive_losses=consec_losses,
        )
        point_val = sym_info.point or 0.00001
        sl_points = abs(current_price - sweep_sig.sl_price) / point_val
        if sl_points <= 0:
            return

        lot_size = self.pos_sizer.calculate_lot(
            symbol=symbol,
            risk_money=risk_money,
            sl_distance_points=sl_points,
            tick_value=sym_info.trade_tick_value if sym_info else 1.0,
            tick_size=sym_info.trade_tick_size if sym_info else 0.00001,
            point=point_val,
            volume_min=sym_info.volume_min if sym_info else 0.01,
            volume_max=sym_info.volume_max if sym_info else 100.0,
            volume_step=sym_info.volume_step if sym_info else 0.01,
            min_trade_volume=float(getattr(self.config.risk, "min_trade_volume", 0.01)),
        )
        if lot_size <= 0:
            logger.warning("[%s] Sweep reversal: calculated lot size is 0.0.", symbol)
            return

        trade_setup = TradeSetup(
            signal=signal_obj,
            entry_price=current_price,
            sl_price=sweep_sig.sl_price,
            tp_price=sweep_sig.tp_price,
            volume=lot_size,
            risk_percent=round(risk_money / curr_balance * 100.0, 4),
            risk_money=risk_money,
            risk_reward_ratio=sweep_sig.risk_reward,
            is_pending=False,
        )

        jev_strategy_context: Dict[str, Any] = {
            "liquidity_level": sweep_sig.level_name,
            "sweep_penetration_atr": self._jev_number(sweep_sig.penetration_atr),
        }
        if strategy_context:
            jev_strategy_context.update(strategy_context)
        self._observe_jev_shadow(
            trade_setup,
            strategy=strategy_name,
            df_entry=df_entry,
            spread_points=(tick_info.ask - tick_info.bid) / point_val,
            point_size=point_val,
            decision_context=decision_context,
            strategy_context=jev_strategy_context,
        )

        logger.info(
            "⚡ [%s] %s: %s %s @ %.5f (SL: %.5f, TP: %.5f, Lot: %.2f, R:R 1:%.1f, Level: %s)",
            symbol, strategy_name, timeframe, sweep_sig.direction.value, sweep_sig.entry_price,
            sweep_sig.sl_price, sweep_sig.tp_price, lot_size, sweep_sig.risk_reward, sweep_sig.level_name,
        )

        if self.config.bot.mode in (BotMode.DEMO, BotMode.LIVE):
            if not self.signal_repo.reserve_signal(signal_obj):
                logger.warning("[%s] Duplicate sweep signal %s blocked.", symbol, signal_obj.signal_id)
                return
            exec_res = self.order_mgr.create_market_order(trade_setup)
            if exec_res.success:
                self.signal_repo.update_signal_status(signal_obj.signal_id, was_traded=True, reasons=[f"{strategy_name}_EXECUTED"])
                spread_pts = (tick_info.ask - tick_info.bid) / point_val
                try:
                    self._persist_open_trade(trade_setup, exec_res, df_entry, spread_pts)
                except Exception as exc:
                    logger.critical("[%s] Sweep trade filled but local persistence failed: %s", symbol, exc, exc_info=True)
                logger.info(
                    "[%s] Sweep Reversal executed at %.2f (ticket %s, R:R 1:%.1f).",
                    symbol, exec_res.price or sweep_sig.entry_price, exec_res.ticket, sweep_sig.risk_reward,
                )
            else:
                self.signal_repo.update_signal_status(
                    signal_obj.signal_id, was_traded=False, reasons=[f"ORDER_FAILED: {exec_res.message}"]
                )
                logger.error("[%s] Sweep Reversal order placement failed: %s", symbol, exec_res.message)
        elif self.config.bot.mode == BotMode.PAPER:
            logger.info(
                "📝 PAPER SWEEP REVERSAL: %s %s @ %.2f (SL: %.2f, TP: %.2f, Lot: %.2f)",
                symbol, sweep_sig.direction.value, sweep_sig.entry_price, sweep_sig.sl_price, sweep_sig.tp_price, lot_size,
            )
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=["PAPER_SWEEP_CANDIDATE"])

    def _execute_asian_breakdown(
        self,
        symbol: str,
        timeframe: str,
        df_entry: pd.DataFrame,
        ab_sig: AsianBreakdownSignal,
        open_pos: List[object],
        pending_trades: List[object],
        decision_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Execute a market order exploiting an institutional Asian range expansion."""
        sym_info = self.connector.get_symbol_info(symbol)
        tick_info = self.connector.get_symbol_tick(symbol)
        if sym_info is None or tick_info is None:
            return

        # 1. News filter check
        if getattr(self.config.filters, "news_filter", False) and hasattr(self, "context_filter") and getattr(self.context_filter, "news_filter", None):
            now_utc = datetime.now(timezone.utc)
            news_res = self.context_filter.news_filter.is_news_blackout(
                current_time_utc=now_utc,
                symbol=symbol,
                config=self.config.filters,
            )
            if not news_res.approved:
                logger.info("[%s] Asian breakdown blocked by News Filter: %s", symbol, ", ".join(news_res.reasons))
                return

        # 1b. Session filter check
        if getattr(self.config.filters, "session_filter", False) and hasattr(self, "context_filter") and getattr(self.context_filter, "session_filter", None):
            now_utc = datetime.now(timezone.utc)
            sym_cfg = self.config.symbols.get(symbol)
            session_res = self.context_filter.session_filter.check(
                current_time_utc=now_utc,
                symbol=symbol,
                config=self.config.filters,
                symbol_config=sym_cfg,
            )
            if not session_res.approved:
                logger.info("[%s] Asian breakdown blocked by Session Filter: %s", symbol, ", ".join(session_res.reasons))
                return

        # 2. Anti-Chasing Drift Guard: prevent entering if price drifted away from trigger
        current_price = tick_info.bid if ab_sig.direction == Direction.SELL else tick_info.ask
        atr_val = 2.5
        if df_entry is not None and not df_entry.empty and 'atr' in df_entry.columns:
            try:
                atr_val = float(df_entry['atr'].iloc[-1])
            except Exception:
                atr_val = 2.5
        max_allowed_drift = max(1.5, min(3.0, atr_val * 0.25))
        drift = abs(current_price - ab_sig.entry_price)
        if drift > max_allowed_drift:
            logger.warning(
                "[%s] Asian breakdown BLOCKED by Anti-Chasing Drift Guard: current price %.2f drifted %.2f from trigger %.2f (max allowed: %.2f).",
                symbol, current_price, drift, ab_sig.entry_price, max_allowed_drift,
            )
            return

        if len(open_pos) + len(pending_trades) >= self.config.risk.max_open_positions:
            logger.info("[%s] Asian breakdown blocked: max open positions reached.", symbol)
            return

        sym_positions = self.connector.get_positions(symbol=symbol)
        pending_for_sym = [t for t in pending_trades if t.symbol == symbol]
        if len(sym_positions) + len(pending_for_sym) >= self.config.risk.max_positions_per_symbol:
            logger.info("[%s] Asian breakdown blocked: symbol position limit reached (%d/%d).",
                        symbol, len(sym_positions) + len(pending_for_sym), self.config.risk.max_positions_per_symbol)
            return

        trades_today = self.trade_repo.get_today_trades()
        max_daily = self.config.risk.max_daily_trades
        if max_daily > 0 and len(trades_today) >= max_daily:
            logger.info("[%s] Asian breakdown blocked: daily trade limit reached (%d/%d).", symbol, len(trades_today), max_daily)
            return

        signal_obj = self.asian_breakdown.build_breakdown_signal(symbol, timeframe, ab_sig)

        account_info = self.connector.get_account_info()
        curr_balance = account_info.balance if account_info else 10000.0
        consec_losses = self.trade_repo.get_consecutive_losses(symbol)
        risk_money = self.pos_sizer.calculate_risk_money(
            balance=curr_balance,
            risk_config=self.config.risk,
            score_classification=signal_obj.score_result.classification,
            consecutive_losses=consec_losses,
        )
        point_val = sym_info.point or 0.00001
        sl_points = abs(current_price - ab_sig.sl_price) / point_val
        if sl_points <= 0:
            return

        lot_size = self.pos_sizer.calculate_lot(
            symbol=symbol,
            risk_money=risk_money,
            sl_distance_points=sl_points,
            tick_value=sym_info.trade_tick_value if sym_info else 1.0,
            tick_size=sym_info.trade_tick_size if sym_info else 0.00001,
            point=point_val,
            volume_min=sym_info.volume_min if sym_info else 0.01,
            volume_max=sym_info.volume_max if sym_info else 100.0,
            volume_step=sym_info.volume_step if sym_info else 0.01,
            min_trade_volume=float(getattr(self.config.risk, "min_trade_volume", 0.01)),
        )
        if lot_size <= 0:
            logger.warning("[%s] Asian breakdown: calculated lot size is 0.0.", symbol)
            return

        trade_setup = TradeSetup(
            signal=signal_obj,
            entry_price=current_price,
            sl_price=ab_sig.sl_price,
            tp_price=ab_sig.tp_price,
            volume=lot_size,
            risk_percent=round(risk_money / curr_balance * 100.0, 4),
            risk_money=risk_money,
            risk_reward_ratio=ab_sig.risk_reward,
            is_pending=False,
        )

        self._observe_jev_shadow(
            trade_setup,
            strategy="ASIAN_RANGE_EXPANSION",
            df_entry=df_entry,
            spread_points=(tick_info.ask - tick_info.bid) / point_val,
            point_size=point_val,
            decision_context=decision_context,
            strategy_context={
                "trigger_level": ab_sig.level_name,
                "target_level": ab_sig.target_name,
                "declared_reward_risk": self._jev_number(ab_sig.risk_reward),
            },
        )

        logger.info(
            "🚀 [%s] ASIAN EXPANSION EXECUTION: %s %s @ %.2f (SL: %.2f, TP: %.2f, Lot: %.2f, R:R 1:%.1f, Target: %s)",
            symbol, timeframe, ab_sig.direction.value, ab_sig.entry_price,
            ab_sig.sl_price, ab_sig.tp_price, lot_size, ab_sig.risk_reward, ab_sig.target_name,
        )

        if self.config.bot.mode in (BotMode.DEMO, BotMode.LIVE):
            if not self.signal_repo.reserve_signal(signal_obj):
                logger.warning("[%s] Duplicate Asian breakdown signal %s blocked.", symbol, signal_obj.signal_id)
                return
            exec_res = self.order_mgr.create_market_order(trade_setup)
            if exec_res.success:
                self.signal_repo.update_signal_status(signal_obj.signal_id, was_traded=True, reasons=["ASIAN_BREAKDOWN_EXECUTED"])
                spread_pts = (tick_info.ask - tick_info.bid) / point_val
                try:
                    self._persist_open_trade(trade_setup, exec_res, df_entry, spread_pts)
                except Exception as exc:
                    logger.critical("[%s] Asian breakdown trade filled but local persistence failed: %s", symbol, exc, exc_info=True)
                logger.info(
                    "[%s] Asian breakdown executed at %.2f (ticket %s, R:R 1:%.1f).",
                    symbol, exec_res.price or ab_sig.entry_price, exec_res.ticket, ab_sig.risk_reward,
                )
            else:
                self.signal_repo.update_signal_status(
                    signal_obj.signal_id, was_traded=False, reasons=[f"ORDER_FAILED: {exec_res.message}"]
                )
                logger.error("[%s] Asian breakdown order placement failed: %s", symbol, exec_res.message)
        elif self.config.bot.mode == BotMode.PAPER:
            logger.info(
                "📝 PAPER ASIAN BREAKDOWN: %s %s @ %.2f (SL: %.2f, TP: %.2f, Lot: %.2f)",
                symbol, ab_sig.direction.value, ab_sig.entry_price, ab_sig.sl_price, ab_sig.tp_price, lot_size,
            )
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=["PAPER_ASIAN_BREAKDOWN_CANDIDATE"])

    def reconcile_mt5_state(self, open_positions: Optional[List[object]] = None) -> None:
        """Synchronise local state with MT5 after start and while running."""
        if not self.connector.is_connected():
            return
        positions = open_positions
        if positions is None:
            positions = self.connector.get_positions(magic=self.config.bot.magic_number)

        broker_pending = {
            int(order.ticket): order
            for order in self.connector.get_pending_orders(magic=self.config.bot.magic_number)
            if getattr(order, "ticket", None) is not None
        }
        imported_pending = self.trade_repo.reconcile_pending_orders(
            list(broker_pending.values()), self.config.bot.magic_number
        )
        for signal_id in imported_pending:
            logger.critical(
                "Recovered MT5 pending order for signal %s after missing local persistence.",
                signal_id,
            )
        for trade in self.trade_repo.get_pending_trades():
            if trade.ticket is None:
                continue
            comment = trade.comment or f"QTB:{trade.signal_id}"
            position = self.connector.find_position_by_comment(
                trade.symbol, self.config.bot.magic_number, comment
            )
            if position is not None:
                if self.trade_repo.mark_pending_filled(int(trade.ticket), position):
                    logger.info("Pending breakout %s filled as MT5 position %s.", trade.ticket, position.ticket)
                continue
            if int(trade.ticket) in broker_pending:
                continue
            terminal_status = self.connector.get_historical_order_status(int(trade.ticket))
            if terminal_status in {"CANCELLED", "EXPIRED", "REJECTED"}:
                if self.trade_repo.mark_pending_cancelled(int(trade.ticket), terminal_status):
                    self.signal_repo.update_signal_status(
                        trade.signal_id, was_traded=False,
                        reasons=[f"PENDING_{terminal_status}"],
                    )
            elif terminal_status == "FILLED":
                closed = self.connector.get_closed_position_result(int(trade.ticket))
                if closed:
                    self.trade_repo.mark_trade_closed_by_ticket(
                        ticket=int(trade.ticket),
                        exit_price=closed["exit_price"],
                        profit=closed["profit"],
                        exited_at=closed["exited_at"],
                    )
                    self.stats_repo.rebuild_for_date(closed["exited_at"])
                    logger.info("Reconciled offline filled&closed trade %s: P&L %.2f", trade.ticket, closed["profit"])

        imported_signals = self.trade_repo.reconcile_open_positions(
            positions, self.config.bot.magic_number
        )
        for signal_id in imported_signals:
            self.signal_repo.update_signal_status(signal_id, was_traded=True)

        open_tickets = {
            int(position.ticket) for position in positions
            if getattr(position, "ticket", None) is not None
        }
        for trade in self.trade_repo.get_open_trades():
            if trade.ticket is None or int(trade.ticket) in open_tickets:
                continue
            closed = self.connector.get_closed_position_result(int(trade.ticket))
            if not closed:
                continue
            if self.trade_repo.mark_trade_closed_by_ticket(
                ticket=int(trade.ticket),
                exit_price=closed["exit_price"],
                profit=closed["profit"],
                exited_at=closed["exited_at"],
            ):
                self.stats_repo.rebuild_for_date(closed["exited_at"])
                logger.info(
                    "Reconciled closed trade %s: P&L %.2f",
                    trade.ticket, closed["profit"],
                )
                # Feed closed trade into Neural Memory Bank to close the learning loop
                if hasattr(self, "memory_bank") and self.memory_bank:
                    try:
                        updated_t = self.trade_repo.get_trade_by_id(trade.trade_id) or trade
                        self.memory_bank.record_trade_observation(updated_t)
                    except Exception as mb_exc:
                        logger.warning("Error updating Neural Memory Bank on trade close: %s", mb_exc)

    def manage_pending_breakouts(self) -> None:
        """Cancel an unfilled structural breakout once its thesis is invalid.

        Broker-side expiration is the primary timeout. This extra guard
        cancels sooner if price reaches the planned protective-stop level
        *before* the breakout trigger is filled.
        """
        pending_by_ticket = {
            int(order.ticket): order
            for order in self.connector.get_pending_orders(magic=self.config.bot.magic_number)
            if getattr(order, "ticket", None) is not None
        }
        bar_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}.get(
            self.config.timeframes.entry, 15
        )
        for trade in self.trade_repo.get_pending_trades():
            if trade.ticket is None or int(trade.ticket) not in pending_by_ticket:
                continue
            context = self.market_contexts.get(trade.symbol) or self._build_entry_context(trade.symbol)
            if not context:
                continue
            df_entry = context["df_entry"]
            last_bar = df_entry.iloc[-1]
            tick = self.connector.get_symbol_tick(trade.symbol)
            bar_time = last_bar.get("time")
            if hasattr(bar_time, "to_pydatetime"):
                bar_time = bar_time.to_pydatetime()
            if isinstance(bar_time, datetime) and bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)
            created_at = trade.created_at
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            bar_closed_after_order = bool(
                isinstance(bar_time, datetime) and created_at
                and (bar_time + timedelta(minutes=bar_minutes)) > created_at
            )

            is_buy = trade.direction == Direction.BUY.value
            invalidated_by_tick = bool(
                tick and ((is_buy and tick.bid <= trade.sl_price) or (not is_buy and tick.ask >= trade.sl_price))
            )
            invalidated_by_bar = bool(
                bar_closed_after_order and (
                    (is_buy and float(last_bar["low"]) <= trade.sl_price)
                    or (not is_buy and float(last_bar["high"]) >= trade.sl_price)
                )
            )
            if not (invalidated_by_tick or invalidated_by_bar):
                continue
            now_ts = time.time()
            if now_ts < self._pending_cancel_cooldown.get(int(trade.ticket), 0.0):
                continue

            result = self.order_mgr.cancel_pending_order(int(trade.ticket))
            if result.success:
                reason = "INVALIDATED_BEFORE_TRIGGER"
                self.trade_repo.mark_pending_cancelled(int(trade.ticket), reason)
                self.signal_repo.update_signal_status(trade.signal_id, was_traded=False, reasons=[reason])
                logger.warning("[%s] Cancelled pending breakout %s: %s.", trade.symbol, trade.ticket, reason)
            else:
                is_closed = (result.retcode == 10018) or ("market closed" in str(result.message or "").lower())
                if is_closed:
                    self._pending_cancel_cooldown[int(trade.ticket)] = now_ts + 1800.0
                    if now_ts - self._last_market_closed_log.get(trade.symbol, 0.0) > 3600.0:
                        self._last_market_closed_log[trade.symbol] = now_ts
                        logger.info(
                            "[%s] Market closed (Code 10018). Pending order %s cancellation deferred until market reopen.",
                            trade.symbol, trade.ticket,
                        )
                else:
                    logger.error("[%s] Could not cancel invalid pending breakout %s: %s", trade.symbol, trade.ticket, result.message)

        # --- Fix 4: Cancel pending orders stale beyond max_pending_hours ---
        #     A signal generated 2+ hours ago describes a different market context.
        #     Better to miss the trade than to fill a stale thesis in an unknown regime.
        max_pending_hours = float(getattr(
            getattr(self.config, "liquidity_guard", None), "max_pending_hours", 2.0
        ) if getattr(self.config, "liquidity_guard", None) else 2.0)
        now_utc = datetime.now(timezone.utc)
        for trade in self.trade_repo.get_pending_trades():
            if trade.ticket is None:
                continue
            created_at = trade.created_at
            if created_at is None:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_hours = (now_utc - created_at).total_seconds() / 3600.0
            if age_hours < max_pending_hours:
                continue
            # Only cancel if the order is still live at broker
            pending_tickets = {
                int(o.ticket)
                for o in self.connector.get_pending_orders(magic=self.config.bot.magic_number)
                if getattr(o, "ticket", None) is not None
            }
            if int(trade.ticket) not in pending_tickets:
                continue
            now_ts = time.time()
            if now_ts < self._pending_cancel_cooldown.get(int(trade.ticket), 0.0):
                continue
            result = self.order_mgr.cancel_pending_order(int(trade.ticket))
            if result.success:
                reason = f"STALE_PENDING_{age_hours:.1f}H_EXCEEDED_{max_pending_hours:.1f}H_LIMIT"
                self.trade_repo.mark_pending_cancelled(int(trade.ticket), reason)
                self.signal_repo.update_signal_status(trade.signal_id, was_traded=False, reasons=[reason])
                logger.warning(
                    "[%s] Cancelled stale pending breakout %s: %.1fh old (limit %.1fh).",
                    trade.symbol, trade.ticket, age_hours, max_pending_hours,
                )
            else:
                is_closed = (result.retcode == 10018) or ("market closed" in str(result.message or "").lower())
                if is_closed:
                    self._pending_cancel_cooldown[int(trade.ticket)] = now_ts + 1800.0
                    if now_ts - self._last_market_closed_log.get(trade.symbol, 0.0) > 3600.0:
                        self._last_market_closed_log[trade.symbol] = now_ts
                        logger.info(
                            "[%s] Market closed (Code 10018). Stale pending %s cancellation deferred until market reopen.",
                            trade.symbol, trade.ticket,
                        )
                else:
                    logger.error(
                        "[%s] Could not cancel stale pending %s: %s", trade.symbol, trade.ticket, result.message
                    )

        # --- Fix 5: Cancel pending orders during news blackout window ---
        if self.config.filters.news_filter:
            for trade in self.trade_repo.get_pending_trades():
                if trade.ticket is None or int(trade.ticket) not in pending_by_ticket:
                    continue
                news_res = self.context_filter.news_filter.is_news_blackout(
                    self._server_now(trade.symbol), trade.symbol, self.config.filters
                )
                if not news_res.approved:
                    now_ts = time.time()
                    if now_ts < self._pending_cancel_cooldown.get(int(trade.ticket), 0.0):
                        continue
                    result = self.order_mgr.cancel_pending_order(int(trade.ticket))
                    if result.success:
                        reason = "CANCELLED_NEWS_BLACKOUT"
                        self.trade_repo.mark_pending_cancelled(int(trade.ticket), reason)
                        self.signal_repo.update_signal_status(trade.signal_id, was_traded=False, reasons=news_res.reasons)
                        logger.warning(
                            "[%s] Cancelled pending order %s before high-impact news: %s",
                            trade.symbol, trade.ticket, ", ".join(news_res.reasons)
                        )
                    else:
                        is_closed = (result.retcode == 10018) or ("market closed" in str(result.message or "").lower())
                        if is_closed:
                            self._pending_cancel_cooldown[int(trade.ticket)] = now_ts + 1800.0

    def process_symbol(self, symbol: str) -> None:
        """Process OHLCV download, indicator calculation, and trade scoring for a symbol."""
        entry_tf = self.config.timeframes.entry
        setup_tf = self.config.timeframes.setup
        htf = self.config.timeframes.higher

        # 1. Check if symbol selected
        self.connector.select_symbol(symbol, True)

        # Probe only the newest closed candle on each loop. The loop also
        # manages open positions every two seconds, so downloading and cleaning
        # the full indicator history before checking for a new bar needlessly
        # repeats hundreds of rows between M15 closes.
        latest_bar = self.downloader.download_rates(
            symbol, entry_tf, count=1, closed_only=True
        )
        if latest_bar is None or latest_bar.empty:
            return
        current_bar_time = latest_bar['time'].iloc[-1]
        last_time = self.last_bar_time.get(symbol)
        is_startup = last_time is None
        if not is_startup and current_bar_time <= last_time:
            # Not a new bar
            return

        # Stale Bar Guard: Prevent executing signals from expired historical bars upon bot startup/restart.
        now_dt = self._server_now(symbol)
        bar_open_dt = pd.to_datetime(current_bar_time).to_pydatetime()
        if bar_open_dt.tzinfo is None:
            bar_open_dt = bar_open_dt.replace(tzinfo=timezone.utc)

        tf_secs_map = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
        tf_secs = tf_secs_map.get(str(entry_tf).upper(), 3600)
        bar_close_dt = bar_open_dt + timedelta(seconds=tf_secs)
        age_since_close = (now_dt - bar_close_dt).total_seconds()

        # If more than 3 minutes (180s) have passed since the candle closed, do not execute on startup.
        if is_startup and age_since_close > 180:
            self.last_bar_time[symbol] = current_bar_time
            logger.info(
                "[%s] [STALE BAR GUARD] Latest %s bar closed %.0fs ago (closed: %s, now: %s). "
                "Synchronized baseline without trading historical candle; waiting for next live bar close.",
                symbol, entry_tf, age_since_close, bar_close_dt.strftime("%H:%M:%S"), now_dt.strftime("%H:%M:%S")
            )
            return

        # A new closed bar is available; fetch the complete indicator window
        # only for this decision cycle.
        df_entry_raw = self.downloader.download_rates(
            symbol, entry_tf, count=self._entry_history_bars(), closed_only=True
        )
        if df_entry_raw is None or len(df_entry_raw) < 50:
            return
        df_entry, quality = self.cleaner.clean(df_entry_raw)
        if not quality.get("quality_ok", True):
            return
        current_bar_time = df_entry['time'].iloc[-1]
        self.last_bar_time[symbol] = current_bar_time

        logger.debug(f"[{symbol}] New {entry_tf} bar detected at {current_bar_time}")

        # 3. Download Setup and HTF data
        df_setup_raw = self.downloader.download_rates(symbol, setup_tf, count=200)
        htf_history = (
            1500
            if self.config.alchemist.enabled and self.config.alchemist.entry_model == "msnr_storyline_h1"
            else 200
        )
        df_htf_raw = self.downloader.download_rates(symbol, htf, count=htf_history)

        df_setup, _ = self.cleaner.clean(df_setup_raw) if df_setup_raw is not None else (None, {})
        df_htf, _ = self.cleaner.clean(df_htf_raw) if df_htf_raw is not None else (None, {})
        df_daily = df_weekly = df_monthly = None
        df_confirmation = df_gap = None
        if self.config.alchemist.enabled:
            daily_raw = self.downloader.download_rates(symbol, self.config.timeframes.daily, count=420)
            weekly_raw = self.downloader.download_rates(symbol, self.config.timeframes.weekly, count=120)
            monthly_raw = self.downloader.download_rates(symbol, self.config.timeframes.monthly, count=60)
            confirmation_raw = self.downloader.download_rates(
                symbol, self.config.timeframes.confirmation, count=200, closed_only=True
            )
            gap_raw = self.downloader.download_rates(
                symbol, self.config.timeframes.gap, count=240, closed_only=True
            )
            df_daily, _ = self.cleaner.clean(daily_raw) if daily_raw is not None else (None, {})
            df_weekly, _ = self.cleaner.clean(weekly_raw) if weekly_raw is not None else (None, {})
            df_monthly, _ = self.cleaner.clean(monthly_raw) if monthly_raw is not None else (None, {})
            df_confirmation, _ = self.cleaner.clean(confirmation_raw) if confirmation_raw is not None else (None, {})
            df_gap, _ = self.cleaner.clean(gap_raw) if gap_raw is not None else (None, {})

        # Trend confirmation needs computed setup indicators.  Downloading
        # M15/H1 bars alone is not a confirmation unless the scorer can read
        # their EMAs and ADX.
        if df_setup is not None and len(df_setup) > 0:
            df_setup = calculate_all_trend_indicators(
                df_setup,
                ema_fast_period=self.config.trend.ema_fast,
                ema_medium_period=self.config.trend.ema_medium,
                ema_slow_period=self.config.trend.ema_slow,
                adx_period=self.config.trend.adx_period,
            )

        # 4. Calculate Indicators on Entry TF
        df_entry = calculate_all_trend_indicators(
            df_entry,
            ema_fast_period=self.config.trend.ema_fast,
            ema_medium_period=self.config.trend.ema_medium,
            ema_slow_period=self.config.trend.ema_slow,
            adx_period=self.config.trend.adx_period
        )
        df_entry = calculate_all_momentum_indicators(
            df_entry,
            rsi_period=self.config.momentum.rsi_period,
            macd_fast=self.config.momentum.macd_fast,
            macd_slow=self.config.momentum.macd_slow,
            macd_signal=self.config.momentum.macd_signal
        )
        df_entry = calculate_all_volatility_indicators(
            df_entry,
            atr_period=self.config.volatility.atr_period,
            atr_avg_period=self.config.volatility.atr_avg_period
        )
        df_entry = calculate_all_volume_indicators(
            df_entry,
            volume_period=self.config.volume.period,
            volume_col="real_volume" if str(getattr(self.config.volume.volume_mode, "value", self.config.volume.volume_mode)).lower() == "real" else "tick_volume",
            use_obv=self.config.volume.use_obv
        )

        # Indicators on HTF for confirmation
        if df_htf is not None and len(df_htf) > 0:
            df_htf = calculate_all_volatility_indicators(df_htf, atr_period=self.config.volatility.atr_period)

        # 5. Market Structure Analysis
        atr_series = df_entry['atr']
        structure_analysis = get_market_structure(
            df=df_entry,
            atr_series=atr_series,
            swing_lookback=self.config.structure.swing_lookback,
            swing_confirmation=self.config.structure.swing_confirmation,
            bos_min_distance_atr=self.config.structure.bos_min_distance_atr,
            bos_close_confirmation=self.config.structure.bos_close_confirmation,
            use_bos=self.config.structure.use_bos,
            use_choch=self.config.structure.use_choch,
            max_swing_age=self.config.structure.max_swing_age
        )

        if self.config.alchemist.enabled:
            sym_info = self.connector.get_symbol_info(symbol)
            alchemist_setup = detect_alchemist_setup(
                df_entry, df_setup, df_htf, df_daily, df_weekly,
                self.config.alchemist,
                point=float(sym_info.point) if sym_info and sym_info.point else 0.00001,
                consecutive_losses=self.trade_repo.get_consecutive_losses(symbol),
                df_monthly=df_monthly,
                df_confirmation=df_confirmation,
                df_gap=df_gap,
            )
            if alchemist_setup is not None:
                # 5.1 Kronos Oracle Forward Trajectory Evaluation
                kronos_verdict = None
                if self.kronos_filter.is_active:
                    kronos_appr, kronos_conf, kronos_reason, kronos_verdict = self.kronos_filter.evaluate(
                        symbol=symbol,
                        direction=alchemist_setup.direction.value,
                        entry_price=alchemist_setup.entry_price,
                        sl_price=alchemist_setup.sl_price,
                        tp_price=alchemist_setup.tp_price,
                        df=df_entry,
                    )
                    if not kronos_appr:
                        logger.warning(
                            "[%s] Alchemist %s setup BLOCKED by Kronos Oracle: %s",
                            symbol, alchemist_setup.direction.value, kronos_reason
                        )
                        return

                sweep_sig = SweepReversalSignal(
                    direction=alchemist_setup.direction,
                    entry_price=alchemist_setup.entry_price,
                    sl_price=alchemist_setup.sl_price,
                    tp_price=alchemist_setup.tp_price,
                    level_name=f"ALCHEMIST_{alchemist_setup.liquidity_name}_{'_'.join(alchemist_setup.confluences)}",
                    level_price=alchemist_setup.liquidity_price,
                    penetration_atr=0.0,
                    risk_reward=alchemist_setup.rr,
                )
                strategy_ctx = {
                    "entry_model": alchemist_setup.model,
                    "poi_name": alchemist_setup.poi_name,
                    "liquidity_name": alchemist_setup.liquidity_name,
                    "confluences": list(alchemist_setup.confluences),
                }
                if kronos_verdict:
                    strategy_ctx["kronos"] = kronos_verdict

                self._execute_sweep_reversal(
                    symbol, entry_tf, df_entry, sweep_sig,
                    self.connector.get_positions(magic=self.config.bot.magic_number),
                    self.trade_repo.get_pending_trades(),
                    strategy_name=(
                        "ALCHEMIST_CRT" if self.config.alchemist.entry_model == "crt_m15"
                        else "ALCHEMIST_MSNR_H1"
                    ),
                    strategy_context=strategy_ctx,
                )
                return
            logger.debug(
                "[%s] Alchemist (%s): no complete setup on closed %s bar.",
                symbol, self.config.alchemist.entry_model, entry_tf,
            )
            return
        self.market_contexts[symbol] = {
            "df_entry": df_entry,
            "structure": structure_analysis,
        }

        # 6. Score Confirmation
        score_res, signal_obj = self.score_engine.calculate_score(
            symbol=symbol,
            timeframe=entry_tf,
            df_entry=df_entry,
            df_setup=df_setup,
            df_htf=df_htf,
            structure_analysis=structure_analysis,
            config=self.config
        )

        # These layers are a fixed closed-bar snapshot for later OOS analysis.
        # They cannot change the entry decision while shadow_mode is enabled.
        candidate = signal_obj
        if candidate is None and self.config.telemetry.log_rejected_score_candidates:
            candidate = self.score_engine.build_signal(symbol, entry_tf, df_entry, score_res)
        decision_context = None
        if candidate is not None:
            struct_sc = float(score_res.structure.score) if score_res and hasattr(score_res, "structure") and hasattr(score_res.structure, "score") else None
            decision_context = self._build_decision_context(
                df_entry, structure_analysis, candidate.direction, df_setup=df_setup, structure_score=struct_sc
            )
            if decision_context is not None:
                quality = self.market_quality_filter.assess(decision_context, candidate.direction)
                decision_context["market_quality"] = quality.as_dict()
                self.decision_context_repo.save_context(candidate, decision_context)
                if signal_obj is not None and not quality.approved:
                    reasons = ["MARKET_QUALITY_FILTER", *quality.active_reasons]
                    self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=reasons)
                    logger.info("[%s] Breakout blocked: %s", symbol, ", ".join(reasons))
                    signal_obj = None

        # Check for Sweep Reversal opportunity (exploiting institutional false breakouts)
        if getattr(self.config, "sweep_reversal", None) and self.config.sweep_reversal.enabled:
            ctx = decision_context or self._build_decision_context(
                df_entry, structure_analysis, Direction.BUY, df_setup=df_setup
            )
            if ctx is not None:
                sym_info = self.connector.get_symbol_info(symbol)
                sweep_sig = self.sweep_strategy.detect_sweep_reversal(
                    symbol=symbol,
                    timeframe=entry_tf,
                    df=df_entry,
                    decision_context=ctx,
                    digits=sym_info.digits if sym_info else 2,
                )
                if sweep_sig is not None:
                    # 1. Macro Trend Alignment Filter (H4 / D1)
                    if getattr(self.config.sweep_reversal, "require_macro_trend_alignment", True):
                        macro_aligned = True
                        macro_reasons = []
                        if df_setup is not None and not df_setup.empty and len(df_setup) >= 20:
                            last_setup = df_setup.iloc[-1]
                            ema_fast_h4 = float(last_setup.get("ema_fast", 0.0) or 0.0)
                            ema_med_h4 = float(last_setup.get("ema_medium", 0.0) or 0.0)
                            ema_slow_h4 = float(last_setup.get("ema_slow", 0.0) or 0.0)
                            close_h4 = float(last_setup.get("close", 0.0) or 0.0)
                            
                            if sweep_sig.direction == Direction.BUY:
                                if close_h4 < ema_slow_h4 and ema_fast_h4 < ema_med_h4:
                                    macro_aligned = False
                                    macro_reasons.append(f"H4 Bearish (Close {close_h4:.1f} < EMA200 {ema_slow_h4:.1f})")
                            elif sweep_sig.direction == Direction.SELL:
                                if close_h4 > ema_slow_h4 and ema_fast_h4 > ema_med_h4:
                                    macro_aligned = False
                                    macro_reasons.append(f"H4 Bullish (Close {close_h4:.1f} > EMA200 {ema_slow_h4:.1f})")
                        
                        if macro_aligned and df_htf is not None and not df_htf.empty and len(df_htf) >= 20:
                            last_htf = df_htf.iloc[-1]
                            close_d1 = float(last_htf.get("close", 0.0) or 0.0)
                            ema_50_d1 = float(df_htf["close"].ewm(span=50, adjust=False).mean().iloc[-1])
                            if sweep_sig.direction == Direction.BUY and close_d1 < ema_50_d1 * 0.98:
                                macro_aligned = False
                                macro_reasons.append(f"D1 Deep Bearish (Close {close_d1:.1f} < EMA50 {ema_50_d1:.1f})")
                            elif sweep_sig.direction == Direction.SELL and close_d1 > ema_50_d1 * 1.02:
                                macro_aligned = False
                                macro_reasons.append(f"D1 Deep Bullish (Close {close_d1:.1f} > EMA50 {ema_50_d1:.1f})")

                        is_exhaustion_sweep = False
                        if getattr(self.config.sweep_reversal, "allow_exhaustion_reversals", True):
                            level_str = str(sweep_sig.level_name).upper()
                            if any(k in level_str for k in ("LOW", "HIGH", "PDL", "PDH", "ASIA", "SWING", "DEMAND", "SUPPLY")):
                                is_exhaustion_sweep = True
                                logger.info(
                                    "[%s] SFP / Exhaustion sweep approved at key level %s despite macro trend (exploiting institutional absorption)",
                                    symbol, sweep_sig.level_name
                                )

                        if not macro_aligned and not is_exhaustion_sweep:
                            reason_str = ", ".join(macro_reasons)
                            logger.info(
                                "[%s] Sweep reversal BLOCKED by Macro Trend Filter: Sweep %s opposes macro trend (%s)",
                                symbol, sweep_sig.direction.value, reason_str
                            )
                            sig_obj = self.sweep_strategy.build_sweep_signal(symbol, entry_tf, sweep_sig)
                            self.signal_repo.save_signal(
                                sig_obj, was_traded=False, rejection_reasons=[f"SWEEP_MACRO_TREND_BLOCKED: {reason_str}"]
                            )
                            return

                    # 2. ML Filter Validation
                    if getattr(self.config.sweep_reversal, "require_ml_approval", True):
                        tick_info_sw = self.connector.get_symbol_tick(symbol)
                        sym_info_sw = self.connector.get_symbol_info(symbol)
                        point_val_sw = sym_info_sw.point if sym_info_sw else 0.00001
                        spread_pts_sw = (tick_info_sw.ask - tick_info_sw.bid) / point_val_sw if tick_info_sw else 10.0
                        atr_val_sw = float(df_entry['atr'].iloc[-1]) if 'atr' in df_entry.columns else 2.5
                        
                        ml_approved, ml_prob, ml_reason = self.ml_filter.evaluate(
                            symbol=symbol,
                            direction=sweep_sig.direction.value,
                            df=df_entry,
                            structure_score=30.0,
                            trend_score=20.0,
                            spread_points=spread_pts_sw,
                            atr_value=atr_val_sw,
                            point_size=point_val_sw,
                            total_score=85.0,
                        )
                        if not ml_approved:
                            logger.info(
                                "[%s] Sweep reversal BLOCKED by ML Filter: %s (Probability: %s)",
                                symbol, ml_reason, f"{ml_prob*100:.1f}%" if ml_prob is not None else "N/A"
                            )
                            sig_obj = self.sweep_strategy.build_sweep_signal(symbol, entry_tf, sweep_sig)
                            self.signal_repo.save_signal(
                                sig_obj, was_traded=False, rejection_reasons=[f"SWEEP_ML_REJECTED: {ml_reason}"]
                            )
                            return

                    # 3. System 1 AI Decision Model Validation (quantum:latest / NanoJev)
                    if hasattr(self, "system_one_hub") and self.system_one_hub.enabled:
                        session_label = ctx.get("session", {}).get("label", "UNKNOWN") if ctx else "UNKNOWN"
                        ai_state = {
                            "symbol": symbol,
                            "timeframe": entry_tf,
                            "direction": sweep_sig.direction.value,
                            "session": session_label,
                            "adx": float(df_entry["adx"].iloc[-1]) if "adx" in df_entry.columns else 25.0,
                            "rsi": float(df_entry["rsi"].iloc[-1]) if "rsi" in df_entry.columns else 50.0,
                            "impulse_atr": float(sweep_sig.penetration_atr),
                            "sweep_confirmed": True,
                        }
                        ai_decision = self.system_one_hub.evaluate_sync(ai_state)
                        if ai_decision:
                            is_contradictory = ai_decision.thesis_alignment in ("contradictory", "insufficient")
                            is_trap = ai_decision.trap_risk in ("potential_trap", "overextended_exhaustion")
                            low_evidence = ai_decision.evidence_score <= 2
                            if not self.system_one_hub.shadow_mode and (is_contradictory or is_trap or low_evidence):
                                logger.info(
                                    "[%s] Sweep reversal BLOCKED by System-1 AI (%s): thesis=%s, score=%d/5, trap=%s",
                                    symbol, ai_decision.provider, ai_decision.thesis_alignment,
                                    ai_decision.evidence_score, ai_decision.trap_risk
                                )
                                sig_obj = self.sweep_strategy.build_sweep_signal(symbol, entry_tf, sweep_sig)
                                self.signal_repo.save_signal(
                                    sig_obj, was_traded=False, rejection_reasons=[f"AI_MODEL_REJECTED: {ai_decision.trap_risk or ai_decision.thesis_alignment}"]
                                )
                                return
                            else:
                                logger.info(
                                    "[SYSTEM-1 AI] Evaluation for %s %s: thesis=%s, score=%d/5, trap=%s, latency=%.1fms (Shadow: %s)",
                                    symbol, sweep_sig.direction.value, ai_decision.thesis_alignment,
                                    ai_decision.evidence_score, ai_decision.trap_risk, ai_decision.latency_ms,
                                    self.system_one_hub.shadow_mode
                                )

                    open_pos = self.connector.get_positions(magic=self.config.bot.magic_number)
                    pending_trades = self.trade_repo.get_pending_trades()
                    self._execute_sweep_reversal(
                        symbol, entry_tf, df_entry, sweep_sig, open_pos, pending_trades,
                        decision_context=ctx,
                    )
                    return

        # Check for Asian Range Breakdown / Expansion opportunity (macro trend continuation)
        if getattr(self.config, "asian_breakdown", None) and self.config.asian_breakdown.enabled:
            ctx = decision_context or self._build_decision_context(
                df_entry, structure_analysis, Direction.SELL, df_setup=df_setup
            )
            if ctx is not None:
                sym_info = self.connector.get_symbol_info(symbol)
                ab_sig = self.asian_breakdown.detect_asian_breakdown(
                    symbol=symbol,
                    timeframe=entry_tf,
                    df=df_entry,
                    decision_context=ctx,
                    df_setup=df_setup,
                    df_htf=df_htf,
                    digits=sym_info.digits if sym_info else 2,
                )
                if ab_sig is not None:
                    # ML Filter Validation (if explicitly requested by config)
                    if getattr(self.config.asian_breakdown, "require_ml_approval", False):
                        tick_info_ab = self.connector.get_symbol_tick(symbol)
                        sym_info_ab = self.connector.get_symbol_info(symbol)
                        point_val_ab = sym_info_ab.point if sym_info_ab else 0.00001
                        spread_pts_ab = (tick_info_ab.ask - tick_info_ab.bid) / point_val_ab if tick_info_ab else 10.0
                        atr_val_ab = float(df_entry['atr'].iloc[-1]) if 'atr' in df_entry.columns else 2.5
                        
                        ml_approved, ml_prob, ml_reason = self.ml_filter.evaluate(
                            symbol=symbol,
                            direction=ab_sig.direction.value,
                            df=df_entry,
                            structure_score=30.0,
                            trend_score=20.0,
                            spread_points=spread_pts_ab,
                            atr_value=atr_val_ab,
                            point_size=point_val_ab,
                            total_score=85.0,
                        )
                        if not ml_approved:
                            logger.info(
                                "[%s] Asian breakdown BLOCKED by ML Filter: %s (Probability: %s)",
                                symbol, ml_reason, f"{ml_prob*100:.1f}%" if ml_prob is not None else "N/A"
                            )
                            sig_obj = self.asian_breakdown.build_breakdown_signal(symbol, entry_tf, ab_sig)
                            self.signal_repo.save_signal(
                                sig_obj, was_traded=False, rejection_reasons=[f"ASIAN_BREAKDOWN_ML_REJECTED: {ml_reason}"]
                            )
                            return

                    # 3. System 1 AI Decision Model Validation (quantum:latest / NanoJev)
                    if hasattr(self, "system_one_hub") and self.system_one_hub.enabled:
                        session_label = ctx.get("session", {}).get("label", "UNKNOWN") if ctx else "UNKNOWN"
                        ai_state = {
                            "symbol": symbol,
                            "timeframe": entry_tf,
                            "direction": ab_sig.direction.value,
                            "session": session_label,
                            "adx": float(df_entry["adx"].iloc[-1]) if "adx" in df_entry.columns else 25.0,
                            "rsi": float(df_entry["rsi"].iloc[-1]) if "rsi" in df_entry.columns else 50.0,
                            "impulse_atr": float(getattr(ab_sig, "penetration_atr", 1.0)),
                            "sweep_confirmed": False,
                        }
                        ai_decision = self.system_one_hub.evaluate_sync(ai_state)
                        if ai_decision:
                            is_contradictory = ai_decision.thesis_alignment in ("contradictory", "insufficient")
                            is_trap = ai_decision.trap_risk in ("potential_trap", "overextended_exhaustion")
                            low_evidence = ai_decision.evidence_score <= 2
                            if not self.system_one_hub.shadow_mode and (is_contradictory or is_trap or low_evidence):
                                logger.info(
                                    "[%s] Asian breakdown BLOCKED by System-1 AI (%s): thesis=%s, score=%d/5, trap=%s",
                                    symbol, ai_decision.provider, ai_decision.thesis_alignment,
                                    ai_decision.evidence_score, ai_decision.trap_risk
                                )
                                sig_obj = self.asian_breakdown.build_breakdown_signal(symbol, entry_tf, ab_sig)
                                self.signal_repo.save_signal(
                                    sig_obj, was_traded=False, rejection_reasons=[f"AI_MODEL_REJECTED: {ai_decision.trap_risk or ai_decision.thesis_alignment}"]
                                )
                                return
                            else:
                                logger.info(
                                    "[SYSTEM-1 AI] Evaluation for %s %s: thesis=%s, score=%d/5, trap=%s, latency=%.1fms (Shadow: %s)",
                                    symbol, ab_sig.direction.value, ai_decision.thesis_alignment,
                                    ai_decision.evidence_score, ai_decision.trap_risk, ai_decision.latency_ms,
                                    self.system_one_hub.shadow_mode
                                )

                    open_pos = self.connector.get_positions(magic=self.config.bot.magic_number)
                    pending_trades = self.trade_repo.get_pending_trades()
                    self._execute_asian_breakdown(
                        symbol, entry_tf, df_entry, ab_sig, open_pos, pending_trades,
                        decision_context=ctx,
                    )
                    return

        if not signal_obj:
            # Observation only: retain near/failed candidates so the new
            # profile can quantify the cost of each rejected condition. This
            # block never submits or reserves an order.
            if self.config.telemetry.log_rejected_score_candidates:
                reasons = [
                    "SHADOW_REJECTED_SCORE",
                    f"total={score_res.total_score:.1f}/{self.config.score.minimum:.1f}",
                    f"structure={score_res.structure.score:.1f}/{self.config.score.min_structure:.1f}",
                    f"trend={score_res.trend.score:.1f}/{self.config.score.min_trend:.1f}",
                ]
                if self.config.confirmation.require_setup_trend_alignment:
                    reasons.append(
                        "setup_trend=" + str(score_res.trend.details.get("setup_trend_score", "missing"))
                    )
                self.signal_repo.save_signal(candidate, was_traded=False, rejection_reasons=reasons)
            return

        # 7. Context Filter
        sym_cfg = self.config.symbols.get(symbol)
        tick_info = self.connector.get_symbol_tick(symbol)
        spread_pts = (tick_info.ask - tick_info.bid) / (self.connector.get_symbol_info(symbol).point if self.connector.get_symbol_info(symbol) else 0.00001) if tick_info else 10.0
        atr_val = float(df_entry['atr'].iloc[-1])
        point_val = self.connector.get_symbol_info(symbol).point if self.connector.get_symbol_info(symbol) else 0.00001

        account_info = self.connector.get_account_info()
        curr_balance = account_info.balance if account_info else 10000.0
        curr_equity = account_info.equity if account_info else 10000.0
        open_pos = self.connector.get_positions(magic=self.config.bot.magic_number)
        self.exposure_mgr.update_positions(open_pos)
        pending_trades = self.trade_repo.get_pending_trades()
        consec_losses = self.trade_repo.get_consecutive_losses(symbol)

        if len(open_pos) + len(pending_trades) >= self.config.risk.max_open_positions:
            reason = "Open/pending position capacity is fully reserved."
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            logger.info("[%s] %s", symbol, reason)
            return

        # Daily trade limit guard
        trades_today = self.trade_repo.get_today_trades()
        max_daily = self.config.risk.max_daily_trades
        if max_daily > 0 and len(trades_today) >= max_daily:
            reason = f"Daily trade limit reached: {len(trades_today)}/{max_daily} trades today."
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            logger.info("[%s] %s", symbol, reason)
            return

        # Count pending orders and account-wide open positions for this specific symbol
        pending_for_symbol = [t for t in pending_trades if t.symbol == symbol]
        all_symbol_positions = self.connector.get_positions(symbol=symbol) or []
        open_for_symbol = all_symbol_positions if all_symbol_positions is not None else [p for p in open_pos if getattr(p, "symbol", None) == symbol]
        mt5_pending_all = self.connector.get_pending_orders() if hasattr(self.connector, "get_pending_orders") else []
        mt5_pending_symbol = [o for o in mt5_pending_all if getattr(o, "symbol", None) == symbol]
        total_for_symbol = len(open_for_symbol) + max(len(pending_for_symbol), len(mt5_pending_symbol))
        if total_for_symbol >= self.config.risk.max_positions_per_symbol:
            reason = f"Fleet exposure limit reached for {symbol}: {total_for_symbol}/{self.config.risk.max_positions_per_symbol} active positions/orders on account."
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            logger.info("[%s] %s", symbol, reason)
            return

        filter_res = self.context_filter.check_all(
            symbol=symbol,
            current_time_utc=self._server_now(symbol),
            current_spread_points=spread_pts,
            atr_value=atr_val,
            point_size=point_val,
            current_equity=curr_equity,
            current_balance=curr_balance,
            daily_pnl=self.stats_repo.get_today_pnl(),
            consecutive_losses=consec_losses,
            direction=signal_obj.direction,
            open_positions=open_pos,
            # Include recent closed-bar histories for *all* symbols already
            # observed in this process. Passing only the candidate symbol made
            # the correlation filter unable to compare it with open trades.
            price_history_dict={
                name: context["df_entry"]
                for name, context in self.market_contexts.items()
                if isinstance(context.get("df_entry"), pd.DataFrame)
            },
            symbol_config=sym_cfg
        )

        if not filter_res.approved:
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=filter_res.reasons)
            logger.info(f"[{symbol}] Signal rejected by context filters: {', '.join(filter_res.reasons)}")
            return

        # Keep the dedicated exposure manager in the execution path as an
        # additional invariant, even though ContextFilter and ExecutionGuard
        # independently enforce the same limits.
        if not self.exposure_mgr.can_open_new_position(symbol):
            reason = "ExposureManager rejected position-count limit."
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            logger.warning("[%s] %s", symbol, reason)
            return

        # 7.5 ML Meta-Filter (Evaluates P(success) with Shadow / Active mode)
        ml_approved, ml_prob, ml_reason = self.ml_filter.evaluate(
            symbol=symbol,
            direction=signal_obj.direction.value,
            df=df_entry,
            structure_score=score_res.structure.score,
            trend_score=score_res.trend.score,
            spread_points=spread_pts,
            atr_value=atr_val,
            point_size=point_val,
            total_score=score_res.total_score,
        )

        if not ml_approved:
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[ml_reason])
            logger.info(f"[{symbol}] Signal blocked by ML Filter: {ml_reason}")
            return

        # 7.6 Kronos Foundation Model Trajectory Filter
        if self.kronos_filter.is_active:
            kronos_appr, kronos_conf, kronos_reason, kronos_verdict = self.kronos_filter.evaluate(
                symbol=symbol,
                direction=signal_obj.direction.value,
                entry_price=signal_obj.entry_price or float(df_entry['close'].iloc[-1]),
                sl_price=signal_obj.sl_price,
                tp_price=signal_obj.tp_price,
                df=df_entry,
            )
            if not kronos_appr:
                self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[f"KRONOS_BLOCKED: {kronos_reason}"])
                logger.info(f"[{symbol}] Signal blocked by Kronos Oracle: {kronos_reason}")
                return
            if decision_context is not None and kronos_verdict:
                decision_context["kronos"] = kronos_verdict

        # 8. Entry Planning (Pullback Retest or Controlled Breakout)
        entry_mode = getattr(self.config.trade.entry_mode, "value", self.config.trade.entry_mode)
        mode_str = str(entry_mode).lower()
        if mode_str not in ("breakout", "pullback"):
            reason = f"Unsupported routed entry mode '{entry_mode}'; only structural breakout and pullback are enabled."
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            logger.warning("[%s] %s", symbol, reason)
            return

        sym_info = self.connector.get_symbol_info(symbol)
        if tick_info is None or sym_info is None:
            reason = "Cannot plan entry without a live tick and symbol metadata."
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            return

        # Institutional Trend Exhaustion and Support Absorption Guard
        current_check_price = tick_info.bid if signal_obj.direction == Direction.SELL else tick_info.ask
        fresh_trend, exhaust_reason = self._check_trend_exhaustion_and_support(
            direction=signal_obj.direction,
            symbol=symbol,
            current_price=current_check_price,
            df_entry=df_entry,
            df_setup=df_setup,
            df_htf=df_htf,
            atr_val=atr_val,
        )
        if not fresh_trend:
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[exhaust_reason or "Trend impulse exhausted / at major level."])
            logger.info("[%s] Continuation entry blocked by Exhaustion/Support Guard: %s", symbol, exhaust_reason)
            return

        if mode_str == "pullback":
            entry_price, plan_reason, is_pending = self._plan_pullback_entry(
                signal_obj.direction, structure_analysis, tick_info, sym_info, atr_val=atr_val, df_entry=df_entry
            )
        else:
            entry_price, plan_reason, is_pending = self._plan_breakout_entry(
                signal_obj.direction, structure_analysis, tick_info, sym_info, atr_val=atr_val
            )

        if entry_price is None:
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[plan_reason or "Entry plan invalid."])
            logger.info("[%s] Entry plan rejected: %s", symbol, plan_reason)
            return

        spread_price = (tick_info.ask - tick_info.bid) if tick_info else point_val * 10

        sl_price = self.sl_calc.calculate_sl(
            direction=signal_obj.direction,
            entry_price=entry_price,
            structure_analysis=structure_analysis,
            atr_value=atr_val,
            spread_price=spread_price,
            config=self.config.volatility,
            symbol_point=point_val
        )

        tp_price = self.tp_calc.calculate_tp(
            direction=signal_obj.direction,
            entry_price=entry_price,
            sl_price=sl_price,
            structure_analysis=structure_analysis,
            atr_value=atr_val,
            config=self.config.trade
        )

        risk_money = self.pos_sizer.calculate_risk_money(
            balance=curr_balance,
            risk_config=self.config.risk,
            score_classification=score_res.classification,
            consecutive_losses=consec_losses
        )

        # Portfolio risk is a cash invariant, not merely a position count.
        # A reconciled legacy position without an original risk snapshot is
        # intentionally treated as unquantified and blocks new exposure.
        local_open = {int(t.ticket) for t in self.trade_repo.get_open_trades() if t.ticket is not None}
        unknown_positions = [
            pos for pos in open_pos if getattr(pos, "ticket", None) is not None
            and int(pos.ticket) not in local_open
        ]
        if unknown_positions:
            reason = "Existing MT5 position has no quantified local risk; new exposure blocked."
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            logger.warning("[%s] %s", symbol, reason)
            return
        reserved_risk = self.trade_repo.total_open_and_pending_risk()
        portfolio_limit = curr_balance * (float(self.config.risk.max_portfolio_risk_percent) / 100.0)
        if reserved_risk + risk_money > portfolio_limit:
            reason = (
                f"Portfolio risk limit exceeded: {reserved_risk + risk_money:.2f} "
                f"> {portfolio_limit:.2f}."
            )
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=[reason])
            logger.info("[%s] %s", symbol, reason)
            return

        sl_points = abs(entry_price - sl_price) / point_val

        lot_size = self.pos_sizer.calculate_lot(
            symbol=symbol,
            risk_money=risk_money,
            sl_distance_points=sl_points,
            tick_value=sym_info.trade_tick_value if sym_info else 1.0,
            tick_size=sym_info.trade_tick_size if sym_info else 0.00001,
            point=point_val,
            volume_min=sym_info.volume_min if sym_info else 0.01,
            volume_max=sym_info.volume_max if sym_info else 100.0,
            volume_step=sym_info.volume_step if sym_info else 0.01,
            min_trade_volume=float(getattr(self.config.risk, "min_trade_volume", 0.01)),
        )

        rr_ratio = abs(tp_price - entry_price) / max(0.00001, abs(entry_price - sl_price))

        minutes_per_bar = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}.get(entry_tf, 15)
        expiration = self._server_now(symbol) + timedelta(
            minutes=minutes_per_bar * self.config.trade.breakout_expiry_bars
        )
        trade_setup = TradeSetup(
            signal=signal_obj,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            volume=lot_size,
            risk_percent=self.config.risk.risk_percent,
            risk_money=risk_money,
            risk_reward_ratio=round(rr_ratio, 2),
            is_pending=is_pending,
            expiration=expiration,
            invalidation_price=sl_price,
        )

        # 9. Pre-Execution Guard (11 checks)
        guard_ok, guard_reasons = self.guard.validate_pre_execution(
            trade_setup=trade_setup,
            connector=self.connector,
            context_filter=self.context_filter,
            symbol_config=sym_cfg
        )

        if not guard_ok:
            self.signal_repo.save_signal(signal_obj, was_traded=False, rejection_reasons=guard_reasons)
            logger.warning(f"[{symbol}] Trade blocked by ExecutionGuard: {', '.join(guard_reasons)}")
            return

        self._observe_jev_shadow(
            trade_setup,
            strategy="STRUCTURAL_BREAKOUT",
            df_entry=df_entry,
            spread_points=spread_pts,
            point_size=point_val,
            decision_context=decision_context,
        )

        # 10. Execution.  Reserve first: SQLite's unique signal_id is the
        # inter-process idempotency lock.  No second process/restart can now
        # send another order for this signal.
        if self.config.bot.mode in (BotMode.DEMO, BotMode.LIVE):
            if not self.signal_repo.reserve_signal(signal_obj):
                logger.warning("[%s] Duplicate or already-reserved signal %s blocked.", symbol, signal_obj.signal_id)
                return
            if is_pending:
                exec_res = self.order_mgr.create_breakout_order(trade_setup)
                if exec_res.success:
                    # A placed stop is not an executed trade.  Reconciliation
                    # promotes this flag only after MT5 exposes a position.
                    self.signal_repo.update_signal_status(signal_obj.signal_id, was_traded=False, reasons=["PENDING_PLACED"])
                    try:
                        self._persist_pending_trade(trade_setup, exec_res, df_entry, spread_pts)
                    except Exception as exc:
                        # The pending order exists at the broker. Keep its reserved
                        # signal and surface a critical error; startup/loop MT5
                        # reconciliation will import it without sending a retry.
                        logger.critical("[%s] Order filled but local persistence failed: %s", symbol, exc, exc_info=True)
                    logger.info(
                        "[%s] Structural breakout pending at %s; expires %s (ticket %s).",
                        symbol, entry_price, expiration.isoformat(), exec_res.ticket,
                    )
                else:
                    self.signal_repo.update_signal_status(
                        signal_obj.signal_id,
                        was_traded=False,
                        reasons=[f"ORDER_FAILED: {exec_res.message}"],
                    )
                    if exec_res.retcode == 10018 or "market closed" in str(exec_res.message or "").lower():
                        logger.info(f"[{symbol}] Order placement deferred: Market is closed (Code 10018).")
                    else:
                        logger.error(f"Order placement failed: {exec_res.message}")
            else:
                exec_res = self.order_mgr.create_market_order(trade_setup)
                if exec_res.success:
                    self.signal_repo.update_signal_status(signal_obj.signal_id, was_traded=True, reasons=["MARKET_BREAKOUT_EXECUTED"])
                    try:
                        self._persist_open_trade(trade_setup, exec_res, df_entry, spread_pts)
                    except Exception as exc:
                        logger.critical("[%s] Breakout market order filled but local persistence failed: %s", symbol, exc, exc_info=True)
                    logger.info(
                        "[%s] Fresh breakout executed at market %.2f (ticket %s, R:R 1:%.1f).",
                        symbol, exec_res.price or entry_price, exec_res.ticket, rr_ratio,
                    )
                else:
                    self.signal_repo.update_signal_status(
                        signal_obj.signal_id,
                        was_traded=False,
                        reasons=[f"ORDER_FAILED: {exec_res.message}"],
                    )
                    if exec_res.retcode == 10018 or "market closed" in str(exec_res.message or "").lower():
                        logger.info(f"[{symbol}] Order placement deferred: Market is closed (Code 10018).")
                    else:
                        logger.error(f"Breakout market order failed: {exec_res.message}")
        elif self.config.bot.mode == BotMode.PAPER:
            order_type_str = "STOP" if is_pending else "MARKET"
            logger.info(f"📝 PAPER BREAKOUT: {symbol} {signal_obj.direction.value} {order_type_str} @ {entry_price} (SL: {sl_price}, TP: {tp_price}, Lot: {lot_size}, Expiry: {expiration.isoformat() if is_pending else 'N/A'})")
            self.signal_repo.save_signal(
                signal_obj, was_traded=False,
                rejection_reasons=["PAPER_CANDIDATE_NOT_ROUTED"],
            )

    def run(self) -> None:
        """Main bot execution loop."""
        if not self.connect():
            logger.critical("Could not start bot due to MT5 initialization failure.")
            return

        self.running = True
        logger.info(f"🚀 {self.config.bot.name} is RUNNING on symbols: {', '.join(self.config.market.symbols)}")

        last_dashboard_time = 0.0

        try:
            while self.running:
                # 1. Update Drawdown tracker
                account = self.connector.get_account_info()
                if account:
                    now = self._server_now()
                    today = now.date()
                    if self._drawdown_day != today:
                        baseline = self.stats_repo.ensure_daily_start_balance(now.strftime("%Y-%m-%d"), account.balance)
                        self.drawdown_tracker.reset_day(starting_balance=baseline)
                        self._drawdown_day = today
                        logger.info("Daily drawdown baseline reset for %s.", today.isoformat())
                    dd_state = self.drawdown_tracker.update(account.balance, account.equity)
                    self.runtime_state_repo.set("account_peak_equity", self.drawdown_tracker.peak_equity)
                    if dd_state == DrawdownState.STOPPED:
                        logger.critical("KILL SWITCH TRIGGERED: Max drawdown/daily loss exceeded. Halting operations.")
                        break

                # 2. Manage Open Positions
                open_positions = self.connector.get_positions(magic=self.config.bot.magic_number)
                if open_positions:
                    df_entry_map, structure_map, trade_contexts = self._position_management_contexts(open_positions)
                    self.pos_mgr.manage_positions(
                        open_positions,
                        df_entry_map=df_entry_map,
                        structure_map=structure_map,
                        trade_context_by_ticket=trade_contexts,
                    )

                    # Friday Weekend Flat Guard: close open positions before weekend freeze to prevent gaps
                    now_dt = self._server_now()
                    if now_dt.weekday() == 4 and now_dt.hour >= 20:
                        for p in open_positions:
                            p_sym = getattr(p, "symbol", "")
                            if any(k in p_sym.upper() for k in ("XAU", "USD", "EUR", "GBP", "JPY")):
                                logger.info(f"[WEEKEND FLAT GUARD] Closing {p_sym} ticket {p.ticket} before Friday weekend freeze.")
                                self.connector.close_position(p.ticket)

                # 2.5 Pending structural orders have their own lifecycle:
                # broker expiry plus pre-trigger invalidation.
                self.manage_pending_breakouts()

                # Reconciliation is cheap relative to the 2-second loop, but
                # throttle broker-history reads and never infer a close from
                # a missing local record alone.
                now_ts = time.time()
                if now_ts - self._last_reconciliation_time >= 30.0:
                    self.reconcile_mt5_state(open_positions)
                    self._last_reconciliation_time = now_ts

                # 3. Process Symbols for new signals
                for symbol in self.config.market.symbols:
                    try:
                        self.process_symbol(symbol)
                    except Exception as e:
                        logger.error(f"Error processing {symbol}: {e}", exc_info=True)

                # 4. Render Dashboard periodically (every 10s)
                now_ts = time.time()
                if now_ts - last_dashboard_time > 10.0:
                    last_dashboard_time = now_ts
                    if account:
                        self.dashboard.render(
                            status="RUNNING" if self.running else "STOPPED",
                            mode=self.config.bot.mode.value.upper(),
                            balance=account.balance,
                            equity=account.equity,
                            drawdown_pct=self.drawdown_tracker.current_drawdown_pct,
                            daily_pnl=self.stats_repo.get_today_pnl(),
                            trades_today=len(self.trade_repo.get_today_trades()),
                            wins_today=0,
                            losses_today=0,
                            open_positions=len(open_positions),
                            consecutive_losses=self.trade_repo.get_consecutive_losses(self.config.market.symbols[0] if self.config.market.symbols else None),
                            symbols_active=self.config.market.symbols
                        )

                time.sleep(2)

        except KeyboardInterrupt:
            logger.info("Bot execution interrupted by user.")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Clean shutdown procedure."""
        logger.info("Shutting down trading bot...")
        self.running = False
        self.connector.shutdown()
        logger.info("Shutdown complete.")


def main():
    parser = argparse.ArgumentParser(description="QuantTradingBot - Professional Algorithmic Trading Bot")
    parser.add_argument("--config", default="config", help="Configuration directory")
    parser.add_argument("--mode", choices=["paper", "demo", "live", "backtest"], help="Override bot operational mode")
    parser.add_argument("--symbols", help="Override symbols (comma-separated, e.g. EURUSD,GBPUSD)")

    args = parser.parse_args()

    bot = QuantTradingBot(config_dir=args.config, mode_override=args.mode)
    if args.symbols:
        bot.config.market.symbols = [s.strip() for s in args.symbols.split(",")]

    bot.run()


if __name__ == "__main__":
    main()
