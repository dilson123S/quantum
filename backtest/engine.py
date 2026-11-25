"""Cost-aware, closed-bar backtest engine aligned with the MT5 execution path.

This engine intentionally favours conservative assumptions over attractive
results. It models the live structural pending-stop and Asian expansion entries,
expiry, invalidation and stop-first intra-bar policy. It must not be used to optimise a strategy on
the same history that is later reported as out-of-sample evidence.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional
from uuid import uuid4

import numpy as np
import pandas as pd

from core.config_loader import AppConfig
from core.constants import Direction, SignalClassification, EntryMode
from data.cleaner import DataCleaner
from filters.context_filter import ContextFilter
from filters.market_quality_filter import MarketQualityFilter
from filters.ml_filter import MLFilter
from indicators.momentum import calculate_all_momentum_indicators
from indicators.structure import get_market_structure
from indicators.trend import calculate_all_trend_indicators
from indicators.volatility import calculate_all_volatility_indicators
from indicators.volume import calculate_all_volume_indicators
from risk.position_size import PositionSizeCalculator
from risk.stop_loss import StopLossCalculator
from risk.take_profit import TakeProfitCalculator
from strategy.scorer import ScoreEngine
from strategy.decision_context import build_decision_context
from strategy.asian_breakdown import AsianBreakdownStrategy
from strategy.alchemist import detect_alchemist_setup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstrumentSpec:
    """Broker contract data required for consistent sizing and P/L."""
    point: float
    tick_size: float
    tick_value: float
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01


@dataclass
class BacktestTrade:
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    volume: float
    entry_time: datetime
    risk_money: float
    initial_volume: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    profit_pips: float = 0.0
    profit_r: float = 0.0
    profit_money: float = 0.0
    exit_reason: str = ""
    total_score: float = 0.0
    classification: str = ""
    entry_strategy: str = "STRUCTURAL_BREAKOUT"
    structure_score: float = 0.0
    trend_score: float = 0.0
    momentum_score: float = 0.0
    volume_score: float = 0.0
    volatility_score: float = 0.0
    be_set: bool = False
    partial_closed: bool = False
    current_sl: float = 0.0
    realized_gross_money: float = 0.0
    # Closed-bar regime/SMC data belongs to the resulting trade, not only the
    # candidate.  This makes shadow-rule efficacy measurable after a run.
    regime_label: str = ""
    smc_sweep_aligned: bool = False
    smc_fvg_aligned: bool = False
    smc_order_block_aligned: bool = False
    shadow_reasons: List[str] = field(default_factory=list)


@dataclass
class PendingBreakout:
    symbol: str
    direction: Direction
    trigger_price: float
    sl_price: float
    tp_price: float
    volume: float
    risk_money: float
    created_at: datetime
    expires_at: datetime
    invalidation_price: float
    total_score: float
    classification: str
    structure_score: float
    trend_score: float
    momentum_score: float
    volume_score: float
    volatility_score: float
    regime_label: str = ""
    smc_sweep_aligned: bool = False
    smc_fvg_aligned: bool = False
    smc_order_block_aligned: bool = False
    shadow_reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PendingOrderEvent:
    """Individual pending order lifecycle event for CSV export.

    Each PendingBreakout emits exactly one CREATED event when planned, and
    exactly one terminal event (FILLED, EXPIRED, or INVALIDATED) when resolved.
    """
    symbol: str
    direction: str
    event: str              # CREATED | FILLED | EXPIRED | INVALIDATED
    bar_time: datetime
    trigger_price: float
    invalidation_price: float
    sl_price: float
    tp_price: float
    created_at: datetime
    expires_at: datetime
    total_score: float
    classification: str
    bars_alive: int         # how many bars the order lived before this event
    fill_price: float = 0.0  # only meaningful for FILLED events


@dataclass(frozen=True)
class CandidateAuditRow:
    """Point-in-time candidate audit; not a simulated trade outcome.

    ``forward_return_atr`` is the direction-adjusted close-to-close move over
    a fixed, declared horizon.  It deliberately does not pretend a pending
    breakout filled, so discarded signals can be compared without optimistic
    intrabar assumptions.
    """
    symbol: str
    timeframe: str
    bar_time: datetime
    direction: str
    total_score: float
    structure_score: float
    trend_score: float
    momentum_score: float
    volume_score: float
    volatility_score: float
    decision: str
    rejection_reasons: List[str]
    regime_label: str
    session_label: str
    smc_sweep_aligned: bool
    smc_fvg_aligned: bool
    smc_order_block_aligned: bool
    forward_horizon_bars: int
    forward_return_atr: Optional[float]


@dataclass
class BacktestResult:
    symbol: str
    period_start: datetime
    period_end: datetime
    timeframe: str
    total_bars: int = 0
    total_trades: int = 0
    pending_created: int = 0
    pending_updated: int = 0
    pending_filled: int = 0
    pending_expired: int = 0
    pending_invalidated: int = 0
    signals_skipped_serialization: int = 0
    signals_skipped_circuit_breaker: int = 0
    signals_skipped_daily_loss: int = 0
    signals_skipped_drawdown: int = 0
    count_expired_target_reached: int = 0
    opportunity_cost_r_missed_target: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate: float = 0.0
    total_profit_money: float = 0.0
    total_profit_r: float = 0.0
    avg_profit_r: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0
    avg_trade_duration_minutes: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    pending_events: List[PendingOrderEvent] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


class BacktestEngine:
    """Single-symbol, event-driven simulator using confirmed bars only.

    Each invocation represents one symbol. Do not aggregate independently-run
    instances into a portfolio result: portfolio-level exposure needs a merged
    event loop and is deliberately reported separately by the runner.
    """

    WARMUP_BARS = 250
    DIAGNOSTIC_HORIZON_BARS = 12

    def __init__(
        self,
        config: AppConfig,
        initial_balance: float = 10_000.0,
        instrument_specs: Optional[Dict[str, InstrumentSpec]] = None,
    ):
        self.config = config
        self.initial_balance = initial_balance
        self.instrument_specs = instrument_specs or {}
        self.score_engine = ScoreEngine(config)
        self.sl_calc = StopLossCalculator()
        self.tp_calc = TakeProfitCalculator()
        self.pos_sizer = PositionSizeCalculator()
        self.context_filter = ContextFilter(config)
        self.market_quality_filter = MarketQualityFilter(config)
        self.asian_breakdown = AsianBreakdownStrategy(config)

        if getattr(config, "ml", None) and getattr(config.ml, "enabled", False):
            effective_shadow_mode = bool(
                getattr(config.ml, "shadow_mode", True)
                or not getattr(config.ml, "allow_active_filter", True)
            )
            self.ml_filter = MLFilter(
                enabled=bool(config.ml.enabled),
                shadow_mode=effective_shadow_mode,
                model_path=getattr(config.ml, "model_path", "data/ml_model.joblib"),
                min_confidence=float(getattr(config.ml, "min_confidence", 0.65)),
            )
            if self.ml_filter.is_active:
                logger.info(
                    "BacktestEngine: ML Filter loaded (%s mode, min_conf=%.2f)",
                    "SHADOW" if effective_shadow_mode else "ACTIVE",
                    self.ml_filter.min_confidence,
                )
        else:
            self.ml_filter = None

    def diagnose_candidates(
        self,
        symbol: str,
        df_full: pd.DataFrame,
        df_htf: Optional[pd.DataFrame] = None,
        evaluation_start: Optional[datetime] = None,
        df_setup: Optional[pd.DataFrame] = None,
        horizon_bars: int = DIAGNOSTIC_HORIZON_BARS,
    ) -> List[CandidateAuditRow]:
        """Audit every closed-bar score decision without routing an order.

        This intentionally keeps account/position capacity out of the audit:
        those controls answer whether a signal could be routed, whereas this
        method answers whether the *signal discriminator* is useful.  Its
        fixed horizon is declared in the exported data before inspection.
        """
        if horizon_bars <= 0:
            raise ValueError("Candidate diagnostic horizon must be positive.")
        data = self._add_indicators(df_full.copy())
        htf_data = self._add_indicators(df_htf.copy()) if df_htf is not None and not df_htf.empty else None
        setup_data = self._add_indicators(df_setup.copy()) if df_setup is not None and not df_setup.empty else None
        spec = self._spec_for(symbol)
        rows: List[CandidateAuditRow] = []

        for index in range(self.WARMUP_BARS, len(data)):
            bar = data.iloc[index]
            bar_time = self._as_datetime(bar["time"])
            if evaluation_start is not None and bar_time < evaluation_start:
                continue
            window = data.iloc[max(0, index - 249):index + 1]
            htf_slice = self._closed_htf_slice(htf_data, bar_time)
            setup_slice = self._closed_setup_slice(setup_data, bar_time)
            structure = self._structure(window)
            bar_time = window['time'].iloc[-1] if 'time' in window.columns else None
            score, signal = self.score_engine.calculate_score(
                symbol=symbol, timeframe=self.config.timeframes.entry,
                df_entry=window, df_setup=setup_slice, df_htf=htf_slice,
                structure_analysis=structure, config=self.config,
                created_at=bar_time,
            )
            if score is None:
                continue
            candidate = signal or self.score_engine.build_signal(
                symbol, self.config.timeframes.entry, window, score,
                created_at=bar_time,
            )
            reasons: List[str] = []
            if score.structure.score < self.config.score.min_structure:
                reasons.append("SCORE_STRUCTURE_BELOW_MIN")
            if score.trend.score < self.config.score.min_trend:
                reasons.append("SCORE_TREND_BELOW_MIN")
            if score.total_score < self.config.score.minimum:
                reasons.append("SCORE_TOTAL_BELOW_MIN")
            if self.config.confirmation.require_setup_trend_alignment and not score.trend.details.get("setup_trend_aligned", False):
                reasons.append("SETUP_TREND_NOT_ALIGNED")

            decision_context = build_decision_context(
                window, structure, candidate.direction,
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
            quality = self.market_quality_filter.assess(decision_context, candidate.direction)
            decision = "SCORE_REJECTED" if reasons else "ELIGIBLE"
            if not reasons and not quality.approved:
                decision, reasons = "MARKET_QUALITY_REJECTED", list(quality.active_reasons)
            if not reasons:
                atr = float(bar.get("atr", 0.0) or 0.0)
                context = self.context_filter.check_all(
                    symbol=symbol, direction=candidate.direction, current_time_utc=bar_time,
                    current_spread_points=max(0.0, float(bar.get("spread", 0.0) or 0.0)),
                    atr_value=atr, point_size=spec.point, current_equity=self.initial_balance,
                    current_balance=self.initial_balance, daily_pnl=0.0, consecutive_losses=0,
                    open_positions=[], price_history_dict={symbol: window},
                    symbol_config=self.config.symbols.get(symbol),
                )
                if not context.approved:
                    decision, reasons = "CONTEXT_REJECTED", list(context.reasons)

            atr = float(bar.get("atr", 0.0) or 0.0)
            future_index = index + horizon_bars
            forward_return = None
            if future_index < len(data) and atr > 0:
                move = float(data.iloc[future_index]["close"]) - float(bar["close"])
                forward_return = round((move if candidate.direction == Direction.BUY else -move) / atr, 6)
            smc = decision_context.get("smc", {})
            rows.append(CandidateAuditRow(
                symbol=symbol, timeframe=self.config.timeframes.entry, bar_time=bar_time,
                direction=candidate.direction.value, total_score=score.total_score,
                structure_score=score.structure.score, trend_score=score.trend.score,
                momentum_score=score.momentum.score, volume_score=score.volume.score,
                volatility_score=score.volatility.score, decision=decision,
                rejection_reasons=reasons, regime_label=str(decision_context["regime"]["label"]),
                session_label=str(decision_context["session"]["label"]),
                smc_sweep_aligned=bool(smc.get("sweep_aligned", False)),
                smc_fvg_aligned=bool(smc.get("fvg_aligned", False)),
                smc_order_block_aligned=bool(smc.get("order_block_aligned", False)),
                forward_horizon_bars=horizon_bars, forward_return_atr=forward_return,
            ))
        return rows

    def run(
        self,
        symbol: str,
        df_full: pd.DataFrame,
        df_htf: Optional[pd.DataFrame] = None,
        evaluation_start: Optional[datetime] = None,
        df_setup: Optional[pd.DataFrame] = None,
        df_daily: Optional[pd.DataFrame] = None,
        df_weekly: Optional[pd.DataFrame] = None,
        df_monthly: Optional[pd.DataFrame] = None,
        df_confirmation: Optional[pd.DataFrame] = None,
        df_gap: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """Simulate one symbol without seeing a future entry or HTF candle."""
        if df_full is None or len(df_full) == 0:
            raise ValueError("Backtest needs non-empty entry timeframe data.")
        data = self._add_indicators(df_full.copy())
        htf_data = self._add_indicators(df_htf.copy()) if df_htf is not None and not df_htf.empty else None
        setup_data = self._add_indicators(df_setup.copy()) if df_setup is not None and not df_setup.empty else None
        daily_data = df_daily.copy() if df_daily is not None and not df_daily.empty else None
        weekly_data = df_weekly.copy() if df_weekly is not None and not df_weekly.empty else None
        monthly_data = df_monthly.copy() if df_monthly is not None and not df_monthly.empty else None
        confirmation_data = df_confirmation.copy() if df_confirmation is not None and not df_confirmation.empty else None
        gap_data = df_gap.copy() if df_gap is not None and not df_gap.empty else None
        result = BacktestResult(
            symbol=symbol,
            period_start=evaluation_start or data["time"].iloc[0],
            period_end=data["time"].iloc[-1],
            timeframe=self.config.timeframes.entry,
            total_bars=len(data),
        )
        if len(data) <= self.WARMUP_BARS:
            return result

        spec = self._spec_for(symbol)
        balance = self.initial_balance
        peak_balance = balance
        max_drawdown = 0.0
        consecutive_losses = 0
        daily_date = None
        daily_start_balance = balance
        daily_trade_count = 0
        alchemist_traded_sessions: set[str] = set()
        pending: Optional[PendingBreakout] = None
        open_trade: Optional[BacktestTrade] = None
        result.equity_curve = [balance]

        for index in range(self.WARMUP_BARS, len(data)):
            bar = data.iloc[index]
            bar_time = self._as_datetime(bar["time"])
            # Retain pre-boundary data exclusively as indicator/structure
            # warm-up. No signal, order, fill or P/L belongs to the holdout
            # before its explicitly declared start date.
            if evaluation_start is not None and bar_time < evaluation_start:
                continue
            if daily_date != bar_time.date():
                daily_date = bar_time.date()
                daily_start_balance = balance
                consecutive_losses = 0
                daily_trade_count = 0
                alchemist_traded_sessions.clear()
            spread_points = max(0.0, float(bar.get("spread", 0.0) or 0.0))
            spread_price = spread_points * spec.point

            # MT5 processes existing pending orders and positions before it
            # considers the new signal from the just-closed candle.
            if pending is not None and open_trade is None:
                current_pending = pending
                pending, opened, outcome = self._process_pending(
                    pending, bar, bar_time, spread_price, spec
                )
                tf_delta = self._timeframe_delta(self.config.timeframes.entry)
                bars_alive = max(1, int(round((bar_time - current_pending.created_at).total_seconds() / tf_delta.total_seconds()))) if tf_delta.total_seconds() > 0 else 1

                if outcome == "EXPIRED":
                    result.pending_expired += 1
                    result.pending_events.append(
                        PendingOrderEvent(
                            symbol=symbol,
                            direction=current_pending.direction.value,
                            event="EXPIRED",
                            bar_time=bar_time,
                            trigger_price=current_pending.trigger_price,
                            invalidation_price=current_pending.invalidation_price,
                            sl_price=current_pending.sl_price,
                            tp_price=current_pending.tp_price,
                            created_at=current_pending.created_at,
                            expires_at=current_pending.expires_at,
                            total_score=current_pending.total_score,
                            classification=current_pending.classification,
                            bars_alive=bars_alive,
                            fill_price=0.0,
                        )
                    )
                elif outcome == "EXPIRED_TARGET_REACHED":
                    result.count_expired_target_reached += 1
                    sl_dist = abs(current_pending.trigger_price - current_pending.sl_price)
                    tp_dist = abs(current_pending.tp_price - current_pending.trigger_price)
                    r_target = (tp_dist / sl_dist) if sl_dist > 0 else 2.0
                    result.opportunity_cost_r_missed_target += r_target
                    result.pending_expired += 1
                    result.pending_events.append(
                        PendingOrderEvent(
                            symbol=symbol,
                            direction=current_pending.direction.value,
                            event="EXPIRED_TARGET_REACHED",
                            bar_time=bar_time,
                            trigger_price=current_pending.trigger_price,
                            invalidation_price=current_pending.invalidation_price,
                            sl_price=current_pending.sl_price,
                            tp_price=current_pending.tp_price,
                            created_at=current_pending.created_at,
                            expires_at=current_pending.expires_at,
                            total_score=current_pending.total_score,
                            classification=current_pending.classification,
                            bars_alive=bars_alive,
                            fill_price=0.0,
                        )
                    )
                elif outcome == "INVALIDATED":
                    result.pending_invalidated += 1
                    result.pending_events.append(
                        PendingOrderEvent(
                            symbol=symbol,
                            direction=current_pending.direction.value,
                            event="INVALIDATED",
                            bar_time=bar_time,
                            trigger_price=current_pending.trigger_price,
                            invalidation_price=current_pending.invalidation_price,
                            sl_price=current_pending.sl_price,
                            tp_price=current_pending.tp_price,
                            created_at=current_pending.created_at,
                            expires_at=current_pending.expires_at,
                            total_score=current_pending.total_score,
                            classification=current_pending.classification,
                            bars_alive=bars_alive,
                            fill_price=0.0,
                        )
                    )
                if opened is not None:
                    result.pending_filled += 1
                    result.pending_events.append(
                        PendingOrderEvent(
                            symbol=symbol,
                            direction=current_pending.direction.value,
                            event="FILLED",
                            bar_time=bar_time,
                            trigger_price=current_pending.trigger_price,
                            invalidation_price=current_pending.invalidation_price,
                            sl_price=current_pending.sl_price,
                            tp_price=current_pending.tp_price,
                            created_at=current_pending.created_at,
                            expires_at=current_pending.expires_at,
                            total_score=current_pending.total_score,
                            classification=current_pending.classification,
                            bars_alive=bars_alive,
                            fill_price=opened.entry_price,
                        )
                    )
                    open_trade = opened
                    daily_trade_count += 1

            if open_trade is not None:
                window = data.iloc[max(0, index - 249):index + 1]
                structure = None if self.config.alchemist.enabled else self._structure(window)
                if self._manage_open_trade(open_trade, bar, bar_time, spread_price, spec, window, structure):
                    self._finalize_trade(open_trade, spec)
                    balance += open_trade.profit_money
                    result.trades.append(open_trade)
                    consecutive_losses = consecutive_losses + 1 if open_trade.profit_money < 0 else 0
                    open_trade = None
                    peak_balance = max(peak_balance, balance)
                    max_drawdown = max(max_drawdown, self._drawdown(peak_balance, balance))

            # Drawdown must include the marked-to-market open position, not
            # only realised balance after a close.
            equity = balance
            if open_trade is not None:
                exit_mark = self._market_exit_price(
                    open_trade.direction, float(bar["close"]), spread_price, spec.point
                )
                equity += open_trade.realized_gross_money + self._gross_profit(
                    open_trade.direction, open_trade.entry_price, exit_mark, open_trade.volume, spec
                ) - open_trade.initial_volume * float(self.config.backtest.commission_per_lot_round_turn)
            peak_balance = max(peak_balance, equity)
            max_drawdown = max(max_drawdown, self._drawdown(peak_balance, equity))
            result.equity_curve.append(round(equity, 2))
            daily_pnl = balance - daily_start_balance
            if open_trade is not None:
                result.signals_skipped_serialization += 1
                continue

            if pending is not None:
                if not getattr(self.config.trade, "update_pending_on_new_signal", False):
                    result.signals_skipped_serialization += 1
                    continue

                # When update_pending_on_new_signal is True, evaluate if the freshly
                # closed bar forms a valid breakout plan in the same direction.
                window = data.iloc[max(0, index - 249):index + 1]
                htf_slice = self._closed_htf_slice(htf_data, bar_time)
                setup_slice = self._closed_setup_slice(setup_data, bar_time)
                structure = self._structure(window)
                daily_pnl = balance - daily_start_balance
                new_pending = self._plan_pending_breakout(
                    symbol=symbol,
                    window=window,
                    htf_slice=htf_slice,
                    setup_slice=setup_slice,
                    structure=structure,
                    bar_time=bar_time,
                    balance=balance,
                    daily_pnl=daily_pnl,
                    consecutive_losses=consecutive_losses,
                    spread_points=spread_points,
                    spec=spec,
                    result=result,
                )
                if new_pending is not None and new_pending.direction == pending.direction:
                    tf_delta = self._timeframe_delta(self.config.timeframes.entry)
                    bars_alive = max(1, int(round((bar_time - pending.created_at).total_seconds() / tf_delta.total_seconds()))) if tf_delta.total_seconds() > 0 else 1
                    result.pending_updated += 1
                    result.pending_events.append(
                        PendingOrderEvent(
                            symbol=symbol,
                            direction=new_pending.direction.value,
                            event="UPDATED",
                            bar_time=bar_time,
                            trigger_price=new_pending.trigger_price,
                            invalidation_price=new_pending.invalidation_price,
                            sl_price=new_pending.sl_price,
                            tp_price=new_pending.tp_price,
                            created_at=pending.created_at,
                            expires_at=new_pending.expires_at,
                            total_score=new_pending.total_score,
                            classification=new_pending.classification,
                            bars_alive=bars_alive,
                            fill_price=0.0,
                        )
                    )
                    pending = new_pending
                else:
                    result.signals_skipped_serialization += 1
                continue

            # The current candle has just closed. Everything below uses
            # this and older data; the pending order may only fill next bar.
            window = data.iloc[max(0, index - 249):index + 1]
            htf_slice = self._closed_htf_slice(htf_data, bar_time)
            setup_slice = self._closed_setup_slice(setup_data, bar_time)
            structure = None if self.config.alchemist.enabled else self._structure(window)
            if self.config.alchemist.enabled:
                daily_slice = self._closed_reference_slice(daily_data, bar_time, "D1")

                weekly_slice = self._closed_reference_slice(weekly_data, bar_time, "W1")
                monthly_slice = self._closed_reference_slice(monthly_data, bar_time, "MN1")
                confirmation_slice = self._closed_reference_slice(
                    confirmation_data, bar_time, self.config.timeframes.confirmation,
                )
                gap_slice = self._closed_reference_slice(gap_data, bar_time, self.config.timeframes.gap)
                alchemist_trade = self._plan_alchemist_trade(
                    symbol=symbol, window=window, setup_slice=setup_slice,
                    htf_slice=htf_slice, daily_slice=daily_slice,
                    weekly_slice=weekly_slice, monthly_slice=monthly_slice, structure=structure,
                    confirmation_slice=confirmation_slice, gap_slice=gap_slice,
                    bar_time=bar_time, balance=balance, equity=equity,
                    daily_start_balance=daily_start_balance, consecutive_losses=consecutive_losses,
                    daily_trade_count=daily_trade_count, spread_points=spread_points, spec=spec,
                    traded_sessions=alchemist_traded_sessions,
                )
                if alchemist_trade is not None:
                    open_trade = alchemist_trade
                    daily_trade_count += 1
                    session_tag = "LONDON" if bar_time.hour < 12 else "NY"
                    alchemist_traded_sessions.add(session_tag)
                # Alchemist replaces the old breakout/Asian entry engine for
                # this profile; a missing CRT is a no-trade decision.
                continue
            asian_trade = self._plan_asian_breakdown(
                symbol=symbol,
                window=window,
                htf_slice=htf_slice,
                setup_slice=setup_slice,
                structure=structure,
                bar_time=bar_time,
                balance=balance,
                equity=equity,
                daily_start_balance=daily_start_balance,
                consecutive_losses=consecutive_losses,
                daily_trade_count=daily_trade_count,
                spread_points=spread_points,
                spec=spec,
            )
            if asian_trade is not None:
                open_trade = asian_trade
                daily_trade_count += 1
                continue
            pending = self._plan_pending_breakout(
                symbol=symbol,
                window=window,
                htf_slice=htf_slice,
                setup_slice=setup_slice,
                structure=structure,
                bar_time=bar_time,
                balance=balance,
                daily_pnl=daily_pnl,
                consecutive_losses=consecutive_losses,
                spread_points=spread_points,
                spec=spec,
                result=result,
            )
            if pending is not None:
                result.pending_created += 1
                result.pending_events.append(
                    PendingOrderEvent(
                        symbol=symbol,
                        direction=pending.direction.value,
                        event="CREATED",
                        bar_time=bar_time,
                        trigger_price=pending.trigger_price,
                        invalidation_price=pending.invalidation_price,
                        sl_price=pending.sl_price,
                        tp_price=pending.tp_price,
                        created_at=pending.created_at,
                        expires_at=pending.expires_at,
                        total_score=pending.total_score,
                        classification=pending.classification,
                        bars_alive=0,
                        fill_price=0.0,
                    )
                )

        if open_trade is not None:
            last = data.iloc[-1]
            exit_price = self._market_exit_price(
                open_trade.direction, float(last["close"]), float(last.get("spread", 0.0) or 0.0) * spec.point, spec.point
            )
            open_trade.exit_price = exit_price
            open_trade.exit_time = self._as_datetime(last["time"])
            open_trade.exit_reason = "END_OF_DATA"
            self._finalize_trade(open_trade, spec)
            balance += open_trade.profit_money
            result.trades.append(open_trade)
            peak_balance = max(peak_balance, balance)
            max_drawdown = max(max_drawdown, self._drawdown(peak_balance, balance))

        self._aggregate(result, balance, max_drawdown)
        return result

    def _plan_pending_breakout(
        self,
        symbol: str,
        window: pd.DataFrame,
        htf_slice: Optional[pd.DataFrame],
        setup_slice: Optional[pd.DataFrame],
        structure,
        bar_time: datetime,
        balance: float,
        daily_pnl: float,
        consecutive_losses: int,
        spread_points: float,
        spec: InstrumentSpec,
        result: Optional[BacktestResult] = None,
    ) -> Optional[PendingBreakout]:
        last = window.iloc[-1]
        atr = float(last.get("atr", 0.0) or 0.0)
        if atr <= 0:
            return None
        score, signal = self.score_engine.calculate_score(
            symbol=symbol,
            timeframe=self.config.timeframes.entry,
            df_entry=window,
            df_setup=setup_slice,
            df_htf=htf_slice,
            structure_analysis=structure,
            config=self.config,
        )
        if score is None or signal is None:
            return None
        decision_context = build_decision_context(
            window, structure, signal.direction,
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
        if decision_context is not None:
            if score and hasattr(score, "structure") and hasattr(score.structure, "score"):
                decision_context["structure_score"] = float(score.structure.score)
            elif structure and hasattr(structure, "score"):
                decision_context["structure_score"] = float(structure.score)

            if setup_slice is not None and not setup_slice.empty and len(setup_slice) >= 20:
                last_setup = setup_slice.iloc[-1]
                ema_fast_h4 = float(last_setup.get("ema_fast", 0.0) or 0.0)
                ema_med_h4 = float(last_setup.get("ema_medium", 0.0) or 0.0)
                ema_slow_h4 = float(last_setup.get("ema_slow", 0.0) or 0.0)
                close_h4 = float(last_setup.get("close", 0.0) or 0.0)
                if close_h4 < ema_slow_h4 and ema_fast_h4 < ema_med_h4:
                    decision_context["macro_direction"] = "SELL"
                elif close_h4 > ema_slow_h4 and ema_fast_h4 > ema_med_h4:
                    decision_context["macro_direction"] = "BUY"

        assessment = self.market_quality_filter.assess(decision_context, signal.direction)
        if not assessment.approved:
            return None
        # Shadow findings do not gate an order.  Preserve them while both the
        # immutable context and assessment coexist so filled trades can be
        # evaluated against the exact counterfactual rule later.
        regime_label = str(decision_context.get("regime", {}).get("label", "UNKNOWN"))
        smc_alignment = decision_context.get("smc", {})
        smc_sweep_aligned = bool(smc_alignment.get("sweep_aligned", False))
        smc_fvg_aligned = bool(smc_alignment.get("fvg_aligned", False))
        smc_order_block_aligned = bool(smc_alignment.get("order_block_aligned", False))
        shadow_reasons = list(assessment.shadow_reasons)
        context = self.context_filter.check_all(
            symbol=symbol,
            direction=signal.direction,
            current_time_utc=bar_time,
            current_spread_points=spread_points,
            atr_value=atr,
            point_size=spec.point,
            current_equity=balance,
            current_balance=balance,
            daily_pnl=daily_pnl,
            consecutive_losses=consecutive_losses,
            open_positions=[],
            price_history_dict={symbol: window},
            symbol_config=self.config.symbols.get(symbol),
        )
        if not context.approved:
            if result is not None:
                for reason in context.reasons:
                    if "Consecutive losses" in reason:
                        result.signals_skipped_circuit_breaker += 1
                    elif "Daily loss" in reason:
                        result.signals_skipped_daily_loss += 1
                    elif "drawdown" in reason.lower():
                        result.signals_skipped_drawdown += 1
            return None

        if self.ml_filter is not None and self.ml_filter.is_active:
            dir_str = signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction)
            ml_approved, ml_prob, ml_reason = self.ml_filter.evaluate(
                symbol=symbol,
                direction=dir_str,
                df=window,
                structure_score=float(getattr(score.structure, "score", 0.0)),
                trend_score=float(getattr(score.trend, "score", 0.0)),
                spread_points=spread_points,
                atr_value=atr,
                point_size=spec.point,
                total_score=float(getattr(score, "total_score", 0.0)),
            )
            if not ml_approved:
                return None
            if ml_prob is not None:
                shadow_reasons.append(f"ML_P:{ml_prob:.1%}")

        direction = signal.direction
        entry_mode = getattr(self.config.trade, "entry_mode", EntryMode.BREAKOUT)
        close = float(last["close"])
        spread_price = spread_points * spec.point

        if entry_mode == EntryMode.PULLBACK:
            # PULLBACK MODE:
            # Enter on retest of the broken swing level (support/resistance flip).
            # In MT5 Forex, the chart displays Bid price.
            # BUY positions open at Ask (Bid + spread), so to wait for a pullback down to the broken swing,
            # the current bar must have closed above the swing level (close > swing.price + spread_price).
            # SELL positions open at Bid (close), so the current bar must have closed below the swing level (close < swing.price).
            swing = structure.last_swing_high if direction == Direction.BUY else structure.last_swing_low
            if swing is None:
                return None
            trigger = float(swing.price)
            if direction == Direction.BUY and close <= trigger + spread_price:
                return None
            if direction == Direction.SELL and close >= trigger:
                return None
            expiry_bars = int(getattr(self.config.trade, "pullback_expiry_bars", 8))
        else:
            # BREAKOUT MODE:
            swing = structure.last_swing_high if direction == Direction.BUY else structure.last_swing_low
            if swing is None:
                return None
            buffer = max(float(self.config.trade.breakout_buffer_points), 1.0) * spec.point
            trigger = float(swing.price) + buffer if direction == Direction.BUY else float(swing.price) - buffer
            # The live router refuses a level that price already crossed rather than converting it to a market order.
            if direction == Direction.BUY and trigger <= close + spread_price:
                return None
            if direction == Direction.SELL and trigger >= close:
                return None
            expiry_bars = int(self.config.trade.breakout_expiry_bars)

        sl = self.sl_calc.calculate_sl(
            direction=direction,
            entry_price=trigger,
            structure_analysis=structure,
            atr_value=atr,
            spread_price=spread_price,
            config=self.config.volatility,
            symbol_point=spec.point,
        )
        tp = self.tp_calc.calculate_tp(
            direction=direction,
            entry_price=trigger,
            sl_price=sl,
            structure_analysis=structure,
            atr_value=atr,
            config=self.config.trade,
        )
        if (direction == Direction.BUY and not (sl < trigger < tp)) or (
            direction == Direction.SELL and not (tp < trigger < sl)
        ):
            return None
        risk_money = self.pos_sizer.calculate_risk_money(
            balance, self.config.risk, score.classification, consecutive_losses
        )
        volume = self.pos_sizer.calculate_lot(
            symbol=symbol,
            risk_money=risk_money,
            sl_distance_points=abs(trigger - sl) / spec.point,
            tick_value=spec.tick_value,
            tick_size=spec.tick_size,
            point=spec.point,
            volume_min=spec.volume_min,
            volume_max=spec.volume_max,
            volume_step=spec.volume_step,
        )
        if volume <= 0:
            if result is not None and risk_money <= 0 and consecutive_losses > 0:
                result.signals_skipped_circuit_breaker += 1
            return None
        expires_at = bar_time + self._timeframe_delta(self.config.timeframes.entry) * expiry_bars
        return PendingBreakout(
            symbol=symbol,
            direction=direction,
            trigger_price=round(trigger, self._digits(spec.point)),
            sl_price=round(sl, self._digits(spec.point)),
            tp_price=round(tp, self._digits(spec.point)),
            volume=volume,
            risk_money=risk_money,
            created_at=bar_time,
            expires_at=expires_at,
            invalidation_price=sl,
            total_score=score.total_score,
            classification=score.classification.value,
            structure_score=score.structure.score,
            trend_score=score.trend.score,
            momentum_score=score.momentum.score,
            volume_score=score.volume.score,
            volatility_score=score.volatility.score,
            regime_label=regime_label,
            smc_sweep_aligned=smc_sweep_aligned,
            smc_fvg_aligned=smc_fvg_aligned,
            smc_order_block_aligned=smc_order_block_aligned,
            shadow_reasons=shadow_reasons,
        )

    def _plan_alchemist_trade(
        self, *, symbol: str, window: pd.DataFrame, setup_slice: Optional[pd.DataFrame],
        htf_slice: Optional[pd.DataFrame], daily_slice: Optional[pd.DataFrame],
        weekly_slice: Optional[pd.DataFrame], monthly_slice: Optional[pd.DataFrame], structure, bar_time: datetime,
        confirmation_slice: Optional[pd.DataFrame], gap_slice: Optional[pd.DataFrame],
        balance: float, equity: float, daily_start_balance: float,
        consecutive_losses: int, daily_trade_count: int, spread_points: float,
        spec: InstrumentSpec,
        traded_sessions: Optional[set[str]] = None,
    ) -> Optional[BacktestTrade]:
        if window is None or len(window) < 20:
            return None
        session_tag = "LONDON" if bar_time.hour < 12 else "NY"
        if (
            self.config.alchemist.entry_model == "crt_m15"
            and traded_sessions is not None and session_tag in traded_sessions
        ):
            return None
        max_daily = int(self.config.risk.max_daily_trades)
        if max_daily > 0 and daily_trade_count >= max_daily:
            return None
        if self.config.risk.max_consecutive_losses > 0 and consecutive_losses >= self.config.risk.max_consecutive_losses:
            return None
        daily_loss_pct = max(0.0, (daily_start_balance - equity) / daily_start_balance * 100.0) if daily_start_balance > 0 else 0.0
        account_dd_pct = max(0.0, (self.initial_balance - equity) / self.initial_balance * 100.0) if self.initial_balance > 0 else 0.0
        if daily_loss_pct >= float(self.config.risk.max_daily_loss) or account_dd_pct >= float(self.config.risk.max_drawdown):
            return None

        setup = detect_alchemist_setup(
            window, setup_slice, htf_slice, daily_slice, weekly_slice,
            self.config.alchemist, point=spec.point,
            consecutive_losses=consecutive_losses,
            df_monthly=monthly_slice,
            df_confirmation=confirmation_slice,
            df_gap=gap_slice,
        )
        if setup is None:
            return None

        symbol_config = self.config.symbols.get(symbol)
        if self.config.filters.news_filter:
            news = self.context_filter.news_filter.is_news_blackout(
                current_time_utc=bar_time, symbol=symbol, config=self.config.filters,
            )
            if not news.approved:
                return None
        if self.config.filters.session_filter:
            session = self.context_filter.session_filter.check(
                current_time_utc=bar_time, symbol=symbol, config=self.config.filters,
                symbol_config=symbol_config,
            )
            if not session.approved:
                return None

        spread_price = spread_points * spec.point
        close = float(window.iloc[-1]["close"])
        current_price = close + spread_price if setup.direction == Direction.BUY else close
        if setup.direction == Direction.BUY and not setup.sl_price < current_price < setup.tp_price:
            return None
        if setup.direction == Direction.SELL and not setup.tp_price < current_price < setup.sl_price:
            return None
        risk_money = self.pos_sizer.calculate_risk_money(
            balance, self.config.risk,
            SignalClassification.from_score(60 + 5 * len(setup.confluences)), consecutive_losses,
        )
        sl_distance_points = abs(current_price - setup.sl_price) / spec.point
        if risk_money <= 0 or sl_distance_points <= 0:
            return None
        volume = self.pos_sizer.calculate_lot(
            symbol=symbol, risk_money=risk_money, sl_distance_points=sl_distance_points,
            tick_value=spec.tick_value, tick_size=spec.tick_size, point=spec.point,
            volume_min=spec.volume_min, volume_max=spec.volume_max, volume_step=spec.volume_step,
            min_trade_volume=float(self.config.risk.min_trade_volume),
        )
        if volume <= 0:
            return None
        slip = float(self.config.backtest.slippage_points) * spec.point
        entry = current_price + slip if setup.direction == Direction.BUY else current_price - slip
        score = 60 + 5 * len(setup.confluences)
        regime = "UNKNOWN"
        if "adx" in window:
            adx = float(window.iloc[-1].get("adx", 0.0) or 0.0)
            regime = "TRENDING" if adx >= self.config.trend.adx_min else "RANGING"
        return BacktestTrade(
            trade_id=str(uuid4())[:8], symbol=symbol, direction=setup.direction.value,
            entry_price=round(entry, self._digits(spec.point)), sl_price=setup.sl_price,
            tp_price=setup.tp_price, current_sl=setup.sl_price, volume=volume,
            initial_volume=volume, risk_money=risk_money, entry_time=bar_time,
            total_score=score, classification=SignalClassification.from_score(score).value,
            entry_strategy=(
                "ALCHEMIST_CRT" if self.config.alchemist.entry_model == "crt_m15"
                else "ALCHEMIST_MSNR_H1"
            ), structure_score=25.0 if "SMC_STRUCTURE" in setup.confluences else 0.0,
            trend_score=20.0 if "SMC_STRUCTURE" in setup.confluences else 0.0,
            momentum_score=12.0, volume_score=0.0, volatility_score=12.0,
            regime_label=regime, smc_sweep_aligned=True,
            smc_fvg_aligned=False, smc_order_block_aligned=False,
            shadow_reasons=[*setup.confluences, f"DOL:{setup.liquidity_name}", f"RR:{setup.rr:.2f}"],
        )

    @staticmethod
    def _closed_reference_slice(data: Optional[pd.DataFrame], entry_time: datetime, timeframe: str) -> Optional[pd.DataFrame]:
        if data is None or data.empty:
            return None
        cache_key = f"_cutoff_{timeframe}"
        if cache_key not in data.attrs:
            times = pd.to_datetime(data["time"], utc=True)
            if timeframe == "MN1":
                cutoff_times = times + pd.DateOffset(months=1)
            else:
                delta = BacktestEngine._timeframe_delta(timeframe)
                cutoff_times = times + delta
            data.attrs[cache_key] = cutoff_times.to_numpy(dtype="datetime64[ns]")
        
        cutoffs = data.attrs[cache_key]
        import numpy as np
        decision_ns = np.datetime64(pd.Timestamp(entry_time).tz_convert("UTC").tz_localize(None), "ns")
        idx = int(np.searchsorted(cutoffs, decision_ns, side="right"))
        if idx <= 0:
            return None
        return data.iloc[max(0, idx - 260):idx]


    def _plan_asian_breakdown(
        self,
        symbol: str,
        window: pd.DataFrame,
        htf_slice: Optional[pd.DataFrame],
        setup_slice: Optional[pd.DataFrame],
        structure,
        bar_time: datetime,
        balance: float,
        equity: float,
        daily_start_balance: float,
        consecutive_losses: int,
        daily_trade_count: int,
        spread_points: float,
        spec: InstrumentSpec,
    ) -> Optional[BacktestTrade]:
        """Simulate the live, standalone Asian expansion market-entry path."""
        asian_config = getattr(self.config, "asian_breakdown", None)
        if asian_config is None or not asian_config.enabled or window.empty:
            return None

        max_daily = int(self.config.risk.max_daily_trades)
        if max_daily > 0 and daily_trade_count >= max_daily:
            return None
        if self.config.risk.max_consecutive_losses > 0 and consecutive_losses >= self.config.risk.max_consecutive_losses:
            return None
        daily_loss_pct = (
            max(0.0, (daily_start_balance - equity) / daily_start_balance * 100.0)
            if daily_start_balance > 0 else 0.0
        )
        account_dd_pct = (
            max(0.0, (self.initial_balance - equity) / self.initial_balance * 100.0)
            if self.initial_balance > 0 else 0.0
        )
        if daily_loss_pct >= float(self.config.risk.max_daily_loss):
            return None
        if account_dd_pct >= float(self.config.risk.max_drawdown):
            return None

        context = build_decision_context(
            window, structure, Direction.SELL,
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
        if setup_slice is not None and not setup_slice.empty and len(setup_slice) >= 20:
            last_setup = setup_slice.iloc[-1]
            close_setup = float(last_setup.get("close", 0.0) or 0.0)
            ema_fast = float(last_setup.get("ema_fast", 0.0) or 0.0)
            ema_medium = float(last_setup.get("ema_medium", 0.0) or 0.0)
            ema_slow = float(last_setup.get("ema_slow", 0.0) or 0.0)
            if close_setup < ema_slow and ema_fast < ema_medium:
                context["macro_direction"] = "SELL"
            elif close_setup > ema_slow and ema_fast > ema_medium:
                context["macro_direction"] = "BUY"

        setup = self.asian_breakdown.detect_asian_breakdown(
            symbol=symbol,
            timeframe=self.config.timeframes.entry,
            df=window,
            decision_context=context,
            df_setup=setup_slice,
            df_htf=htf_slice,
            digits=self._digits(spec.point),
        )
        if setup is None:
            return None

        filters = self.config.filters
        symbol_config = self.config.symbols.get(symbol)
        if filters.news_filter:
            news = self.context_filter.news_filter.is_news_blackout(
                current_time_utc=bar_time, symbol=symbol, config=filters,
            )
            if not news.approved:
                return None
        if filters.session_filter:
            session = self.context_filter.session_filter.check(
                current_time_utc=bar_time,
                symbol=symbol,
                config=filters,
                symbol_config=symbol_config,
            )
            if not session.approved:
                return None

        atr = float(window.iloc[-1].get("atr", 0.0) or 0.0)
        ml_prob = None
        if self.ml_filter is not None and self.ml_filter.is_active:
            dir_str = setup.direction.value if hasattr(setup.direction, "value") else str(setup.direction)
            ml_approved, ml_prob, ml_reason = self.ml_filter.evaluate(
                symbol=symbol,
                direction=dir_str,
                df=window,
                structure_score=30.0,
                trend_score=20.0,
                spread_points=spread_points,
                atr_value=atr,
                point_size=spec.point,
                total_score=85.0,
            )
            if not ml_approved:
                return None

        close = float(window.iloc[-1]["close"])
        spread_price = spread_points * spec.point
        current_price = close + spread_price if setup.direction == Direction.BUY else close
        max_drift = max(1.5, min(3.0, atr * 0.25))
        if abs(current_price - setup.entry_price) > max_drift:
            return None
        if setup.direction == Direction.BUY:
            if not setup.sl_price < current_price < setup.tp_price:
                return None
        elif not setup.tp_price < current_price < setup.sl_price:
            return None

        signal = self.asian_breakdown.build_breakdown_signal(symbol, self.config.timeframes.entry, setup)
        risk_money = self.pos_sizer.calculate_risk_money(
            balance,
            self.config.risk,
            signal.score_result.classification,
            consecutive_losses,
        )
        sl_distance_points = abs(current_price - setup.sl_price) / spec.point
        if risk_money <= 0 or sl_distance_points <= 0:
            return None
        volume = self.pos_sizer.calculate_lot(
            symbol=symbol,
            risk_money=risk_money,
            sl_distance_points=sl_distance_points,
            tick_value=spec.tick_value,
            tick_size=spec.tick_size,
            point=spec.point,
            volume_min=spec.volume_min,
            volume_max=spec.volume_max,
            volume_step=spec.volume_step,
            min_trade_volume=float(self.config.risk.min_trade_volume),
        )
        if volume <= 0:
            return None

        slippage = float(self.config.backtest.slippage_points) * spec.point
        entry_price = current_price + slippage if setup.direction == Direction.BUY else current_price - slippage
        digits = self._digits(spec.point)
        return BacktestTrade(
            trade_id=str(uuid4())[:8],
            symbol=symbol,
            direction=setup.direction.value,
            entry_price=round(entry_price, digits),
            sl_price=round(setup.sl_price, digits),
            tp_price=round(setup.tp_price, digits),
            current_sl=round(setup.sl_price, digits),
            volume=volume,
            initial_volume=volume,
            risk_money=risk_money,
            entry_time=bar_time,
            total_score=signal.score_result.total_score,
            classification=signal.score_result.classification.value,
            entry_strategy="ASIAN_BREAKDOWN",
            structure_score=signal.score_result.structure.score,
            trend_score=signal.score_result.trend.score,
            momentum_score=signal.score_result.momentum.score,
            volume_score=signal.score_result.volume.score,
            volatility_score=signal.score_result.volatility.score,
            regime_label=str(context.get("regime", {}).get("label", "UNKNOWN")),
            smc_sweep_aligned=bool(context.get("smc", {}).get("sweep_aligned", False)),
            smc_fvg_aligned=bool(context.get("smc", {}).get("fvg_aligned", False)),
            smc_order_block_aligned=bool(context.get("smc", {}).get("order_block_aligned", False)),
            shadow_reasons=[
                f"ASIAN_EXPANSION_{setup.level_name}",
                "MARKET_ENTRY",
                *([f"ML_P:{ml_prob:.1%}"] if ml_prob is not None else []),
            ],
        )

    def _process_pending(
        self, pending: PendingBreakout, bar: pd.Series, bar_time: datetime,
        spread_price: float, spec: InstrumentSpec,
    ) -> tuple[Optional[PendingBreakout], Optional[BacktestTrade], Optional[str]]:
        if bar_time >= pending.expires_at:
            return None, None, "EXPIRED"
        high, low, opening = float(bar["high"]), float(bar["low"]), float(bar["open"])
        ask_high, ask_low, ask_open = high + spread_price, low + spread_price, opening + spread_price
        entry_mode = getattr(self.config.trade, "entry_mode", EntryMode.BREAKOUT)

        if entry_mode == EntryMode.PULLBACK:
            if pending.direction == Direction.BUY:
                triggered = ask_low <= pending.trigger_price
                invalidated = low <= pending.invalidation_price
                target_reached = ask_high >= pending.tp_price
                fill = min(pending.trigger_price, ask_open) + self.config.backtest.slippage_points * spec.point
            else:
                triggered = high >= pending.trigger_price
                invalidated = ask_high >= pending.invalidation_price
                target_reached = low <= pending.tp_price
                fill = max(pending.trigger_price, opening) - self.config.backtest.slippage_points * spec.point

            if not triggered and target_reached:
                return None, None, "EXPIRED_TARGET_REACHED"
            if invalidated and not triggered:
                return None, None, "INVALIDATED"
            if not triggered:
                return pending, None, None
        else:
            if pending.direction == Direction.BUY:
                triggered = ask_high >= pending.trigger_price
                invalidated = low <= pending.invalidation_price
                fill = max(pending.trigger_price, ask_open) + self.config.backtest.slippage_points * spec.point
            else:
                triggered = low <= pending.trigger_price
                invalidated = ask_high >= pending.invalidation_price
                fill = min(pending.trigger_price, opening) - self.config.backtest.slippage_points * spec.point

            if invalidated and not triggered:
                return None, None, "INVALIDATED"
            if not triggered:
                return pending, None, None
        trade = BacktestTrade(
            trade_id=str(uuid4())[:8], symbol=pending.symbol,
            direction=pending.direction.value, entry_price=round(fill, self._digits(spec.point)),
            sl_price=pending.sl_price, tp_price=pending.tp_price, current_sl=pending.sl_price,
            volume=pending.volume, initial_volume=pending.volume, risk_money=pending.risk_money,
            entry_time=bar_time, total_score=pending.total_score,
            classification=pending.classification, structure_score=pending.structure_score,
            trend_score=pending.trend_score, momentum_score=pending.momentum_score,
            volume_score=pending.volume_score, volatility_score=pending.volatility_score,
            regime_label=pending.regime_label,
            smc_sweep_aligned=pending.smc_sweep_aligned,
            smc_fvg_aligned=pending.smc_fvg_aligned,
            smc_order_block_aligned=pending.smc_order_block_aligned,
            shadow_reasons=list(pending.shadow_reasons),
        )
        return None, trade, "FILLED"

    def _manage_open_trade(
        self, trade: BacktestTrade, bar: pd.Series, bar_time: datetime,
        spread_price: float, spec: InstrumentSpec, window: pd.DataFrame, structure,
    ) -> bool:
        high, low, close, opening = (float(bar["high"]), float(bar["low"]), float(bar["close"]), float(bar["open"]))
        ask_high, ask_low = high + spread_price, low + spread_price
        is_buy = trade.direction == Direction.BUY.value
        if is_buy:
            sl_hit, tp_hit = low <= trade.current_sl, high >= trade.tp_price
        else:
            sl_hit, tp_hit = ask_high >= trade.current_sl, ask_low <= trade.tp_price
        if sl_hit:  # stop wins an ambiguous same-bar SL/TP touch
            trade.exit_price = self._stop_exit_price(trade.direction, trade.current_sl, opening, spread_price, spec.point)
            trade.exit_time, trade.exit_reason = bar_time, "SL_HIT"
            return True
        if tp_hit:
            trade.exit_price = self._tp_exit_price(trade.direction, trade.tp_price, opening, spread_price)
            trade.exit_time, trade.exit_reason = bar_time, "TP_HIT"
            return True

        executable_close = close if is_buy else close + spread_price
        initial_risk = abs(trade.entry_price - trade.sl_price)
        profit_distance = (executable_close - trade.entry_price) if is_buy else (trade.entry_price - executable_close)
        if not trade.be_set and initial_risk > 0 and profit_distance >= float(self.config.trade.break_even_r) * initial_risk:
            be = trade.entry_price + spec.point * 10 if is_buy else trade.entry_price - spec.point * 10
            if (is_buy and be > trade.current_sl) or (not is_buy and be < trade.current_sl):
                trade.current_sl, trade.be_set = be, True

        if self.config.trade.partial_close and not trade.partial_closed and initial_risk > 0:
            if profit_distance >= float(self.config.trade.partial_r) * initial_risk:
                portion = self._quantize_volume(trade.volume * float(self.config.trade.partial_percent) / 100.0, spec)
                if spec.volume_min <= portion < trade.volume:
                    exit_price = self._market_exit_price(trade.direction, close, spread_price, spec.point)
                    trade.realized_gross_money += self._gross_profit(trade.direction, trade.entry_price, exit_price, portion, spec)
                    trade.volume = round(trade.volume - portion, self._volume_digits(spec.volume_step))
                    trade.partial_closed = True

        stagnation_bars = int(getattr(self.config.trade, "stagnation_exit_bars", 0))
        if stagnation_bars > 0 and initial_risk > 0 and not trade.be_set and not trade.partial_closed:
            tf_delta = self._timeframe_delta(self.config.timeframes.entry)
            bars_open = int(round((bar_time - trade.entry_time).total_seconds() / tf_delta.total_seconds())) if tf_delta.total_seconds() > 0 else 0
            if bars_open >= stagnation_bars:
                min_progress_r = float(getattr(self.config.trade, "stagnation_min_progress_r", 0.4))
                current_r = profit_distance / initial_risk
                if current_r < min_progress_r:
                    exit_price = self._market_exit_price(trade.direction, close, spread_price, spec.point)
                    trade.exit_price = exit_price
                    trade.exit_time = bar_time
                    trade.exit_reason = "STAGNATION_EXIT"
                    return True

        mode = str(getattr(self.config.trade.trailing, "value", self.config.trade.trailing)).upper()
        if mode not in {"OFF", "NONE"}:
            atr = float(window["atr"].iloc[-1]) if "atr" in window else 0.0
            atr_sl = None
            if mode in {"ATR", "HYBRID"} and atr > 0:
                mult = float(getattr(self.config.trade, "trailing_atr_multiplier", 2.0))
                atr_sl = executable_close - atr * mult if is_buy else executable_close + atr * mult
            struct_sl = None
            if mode in {"STRUCTURE", "HYBRID"}:
                swing = (structure.last_swing_low if is_buy else structure.last_swing_high) if structure is not None else None
                if swing is not None:
                    struct_sl = float(swing.price)
            proposed = struct_sl if mode == "STRUCTURE" else atr_sl
            if mode == "HYBRID":
                if atr_sl is None:
                    proposed = struct_sl
                elif struct_sl is not None:
                    proposed = max(atr_sl, struct_sl) if is_buy else min(atr_sl, struct_sl)
            if proposed is not None and ((is_buy and proposed > trade.current_sl) or (not is_buy and proposed < trade.current_sl)):
                trade.current_sl = round(proposed, self._digits(spec.point))
        return False

    def _finalize_trade(self, trade: BacktestTrade, spec: InstrumentSpec) -> None:
        if trade.exit_price is None:
            raise ValueError("Cannot finalize a trade without an exit price.")
        gross = trade.realized_gross_money + self._gross_profit(
            trade.direction, trade.entry_price, trade.exit_price, trade.volume, spec
        )
        commission = trade.initial_volume * float(self.config.backtest.commission_per_lot_round_turn)
        trade.profit_money = round(gross - commission, 2)
        trade.profit_r = round(trade.profit_money / trade.risk_money, 4) if trade.risk_money else 0.0
        delta = (trade.exit_price - trade.entry_price) if trade.direction == Direction.BUY.value else (trade.entry_price - trade.exit_price)
        trade.profit_pips = round(delta / spec.point, 2)

    def _aggregate(self, result: BacktestResult, final_balance: float, max_drawdown: float) -> None:
        result.total_trades = len(result.trades)
        result.winning_trades = sum(trade.profit_r > 0.05 for trade in result.trades)
        result.losing_trades = sum(trade.profit_r < -0.05 for trade in result.trades)
        result.breakeven_trades = result.total_trades - result.winning_trades - result.losing_trades
        result.win_rate = round(result.winning_trades / result.total_trades * 100, 1) if result.total_trades else 0.0
        result.total_profit_money = round(final_balance - self.initial_balance, 2)
        result.total_profit_r = round(sum(trade.profit_r for trade in result.trades), 4)
        result.avg_profit_r = round(result.total_profit_r / result.total_trades, 4) if result.total_trades else 0.0
        result.max_drawdown_pct = round(max_drawdown, 4)
        gross_profit = sum(trade.profit_money for trade in result.trades if trade.profit_money > 0)
        gross_loss = abs(sum(trade.profit_money for trade in result.trades if trade.profit_money < 0))
        result.profit_factor = round(gross_profit / gross_loss, 4) if gross_loss else 0.0
        result.best_trade_r = max((trade.profit_r for trade in result.trades), default=0.0)
        result.worst_trade_r = min((trade.profit_r for trade in result.trades), default=0.0)
        durations = [
            (trade.exit_time - trade.entry_time).total_seconds() / 60
            for trade in result.trades if trade.exit_time is not None
        ]
        result.avg_trade_duration_minutes = round(sum(durations) / len(durations), 2) if durations else 0.0

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # DataDownloader retains ``time`` both as an index and a column.
        # Indicator code addresses it as a column, so remove the named index
        # before sorting to avoid pandas' ambiguous-label failure.
        data = df.reset_index(drop=True).sort_values("time").drop_duplicates("time").copy()
        data = calculate_all_trend_indicators(data, self.config.trend.ema_fast, self.config.trend.ema_medium, self.config.trend.ema_slow, self.config.trend.adx_period)
        data = calculate_all_momentum_indicators(data, self.config.momentum.rsi_period, self.config.momentum.macd_fast, self.config.momentum.macd_slow, self.config.momentum.macd_signal)
        data = calculate_all_volatility_indicators(data, self.config.volatility.atr_period, self.config.volatility.atr_avg_period)
        volume_col = "real_volume" if str(getattr(self.config.volume.volume_mode, "value", self.config.volume.volume_mode)).lower() == "real" else "tick_volume"
        return calculate_all_volume_indicators(data, volume_period=self.config.volume.period, volume_col=volume_col, use_obv=self.config.volume.use_obv)

    def _structure(self, window: pd.DataFrame):
        return get_market_structure(
            df=window, atr_series=window["atr"], swing_lookback=self.config.structure.swing_lookback,
            swing_confirmation=self.config.structure.swing_confirmation,
            bos_min_distance_atr=self.config.structure.bos_min_distance_atr,
            bos_close_confirmation=self.config.structure.bos_close_confirmation,
            use_bos=self.config.structure.use_bos, use_choch=self.config.structure.use_choch,
            max_swing_age=self.config.structure.max_swing_age,
        )

    def _closed_htf_slice(self, data: Optional[pd.DataFrame], entry_time: datetime) -> Optional[pd.DataFrame]:
        if data is None or data.empty:
            return None
        cutoff = entry_time - self._timeframe_delta(self.config.timeframes.higher)
        limit = (
            1500
            if self.config.alchemist.enabled and self.config.alchemist.entry_model == "msnr_storyline_h1"
            else 250
        )
        return self._closed_bars_before(data, cutoff, "higher", limit=limit)

    def _closed_setup_slice(self, data: Optional[pd.DataFrame], entry_time: datetime) -> Optional[pd.DataFrame]:
        if data is None or data.empty:
            return None
        cutoff = entry_time - self._timeframe_delta(self.config.timeframes.setup)
        return self._closed_bars_before(data, cutoff, "setup")

    @staticmethod
    def _closed_bars_before(
        data: pd.DataFrame, cutoff: datetime, label: str, limit: int = 250,
    ) -> Optional[pd.DataFrame]:
        """Take the last closed bars using an indexed cutoff, not a full scan.

        The backtest calls this for every entry bar. Re-evaluating a boolean
        mask over an entire multi-year H1/H4 input on every call dominated the
        runtime while returning only 250 rows.
        """
        cache_key = f"_closed_times_ns_{label}"
        times_ns = data.attrs.get(cache_key)
        if times_ns is None:
            times_ns = pd.to_datetime(data["time"], utc=True).to_numpy(dtype="datetime64[ns]")
            data.attrs[cache_key] = times_ns
        cutoff_ts = pd.Timestamp(cutoff)
        if cutoff_ts.tzinfo is None:
            cutoff_ts = cutoff_ts.tz_localize("UTC")
        else:
            cutoff_ts = cutoff_ts.tz_convert("UTC")
        cutoff_ns = cutoff_ts.tz_localize(None).to_datetime64()
        idx = int(np.searchsorted(times_ns, cutoff_ns, side="right"))
        if idx <= 0:
            return None
        return data.iloc[max(0, idx - limit):idx]

    def _spec_for(self, symbol: str) -> InstrumentSpec:
        if symbol in self.instrument_specs:
            return self.instrument_specs[symbol]
        point = self._point(symbol)
        # Offline fallback only. The runner supplies MT5 contract metadata.
        value = 0.67 if "JPY" in symbol.upper() else 1.0
        return InstrumentSpec(point=point, tick_size=point, tick_value=value)

    @staticmethod
    def _point(symbol: str) -> float:
        upper = symbol.upper()
        if "JPY" in upper:
            return 0.001
        if upper.startswith(("XAU", "XAG")):
            return 0.01
        return 0.00001

    @staticmethod
    def _timeframe_delta(timeframe: str) -> timedelta:
        minutes = {
            "M1": 1, "M2": 2, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H2": 120, "H4": 240, "D1": 1440, "W1": 10080,
        }
        return timedelta(minutes=minutes.get(timeframe, 15))

    @staticmethod
    def _as_datetime(value) -> datetime:
        return value.to_pydatetime() if hasattr(value, "to_pydatetime") else value

    @staticmethod
    def _digits(point: float) -> int:
        return max(0, int(round(-math.log10(point)))) if point < 1 else 0

    @staticmethod
    def _volume_digits(step: float) -> int:
        return max(0, int(round(-math.log10(step)))) if 0 < step < 1 else 0

    @staticmethod
    def _drawdown(peak: float, balance: float) -> float:
        return (peak - balance) / peak * 100 if peak > 0 else 0.0

    @staticmethod
    def _gross_profit(direction: str, entry: float, exit_price: float, volume: float, spec: InstrumentSpec) -> float:
        delta = exit_price - entry if direction == Direction.BUY.value else entry - exit_price
        return delta / spec.tick_size * spec.tick_value * volume

    def _market_exit_price(self, direction: str, bid_price: float, spread: float, point: float) -> float:
        slip = float(self.config.backtest.slippage_points) * point
        return bid_price - slip if direction == Direction.BUY.value else bid_price + spread + slip

    def _stop_exit_price(self, direction: str, stop: float, bar_open: float, spread: float, point: float) -> float:
        gap = float(self.config.backtest.gap_slippage_points) * point
        if direction == Direction.BUY.value:
            return min(stop, bar_open) - gap
        return max(stop, bar_open + spread) + gap

    @staticmethod
    def _tp_exit_price(direction: str, target: float, bar_open: float, spread: float) -> float:
        if direction == Direction.BUY.value:
            return max(target, bar_open)
        return min(target, bar_open + spread)

    def _quantize_volume(self, volume: float, spec: InstrumentSpec) -> float:
        steps = math.floor(volume / spec.volume_step)
        return round(steps * spec.volume_step, self._volume_digits(spec.volume_step))
