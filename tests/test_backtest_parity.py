"""Regression tests for live/backtest execution parity and anti-leakage."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest.engine import (
    BacktestEngine, BacktestResult, BacktestTrade, InstrumentSpec,
    PendingBreakout, PendingOrderEvent
)
from backtest_runner import save_report_csv, save_pending_events_csv
from core.config_loader import AppConfig, BacktestConfig, TradeConfig
from core.constants import Direction, TrailingMode, EntryMode
from core.models import StructureAnalysis
from backtest.robustness import bootstrap_trade_outcomes


def _engine() -> BacktestEngine:
    config = AppConfig(
        trade=TradeConfig(
            break_even_r=10.0,
            partial_close=True,
            partial_r=1.0,
            partial_percent=50.0,
            trailing=TrailingMode.HYBRID,
        ),
        backtest=BacktestConfig(
            commission_per_lot_round_turn=0.0,
            slippage_points=0.0,
            gap_slippage_points=0.0,
        ),
    )
    return BacktestEngine(config)


def _pending(created_at: datetime) -> PendingBreakout:
    return PendingBreakout(
        symbol="EURUSD", direction=Direction.BUY, trigger_price=1.1010,
        sl_price=1.0990, tp_price=1.1050, volume=1.0, risk_money=200.0,
        created_at=created_at, expires_at=created_at + timedelta(minutes=30),
        invalidation_price=1.0990, total_score=80.0, classification="GOOD",
        structure_score=20.0, trend_score=18.0, momentum_score=12.0,
        volume_score=12.0, volatility_score=12.0,
    )


def test_closed_bar_slice_uses_inclusive_cutoff_and_latest_limit():
    engine = _engine()
    frame = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=300, freq="1h", tz="UTC"),
        "close": range(300),
    })

    result = engine._closed_bars_before(
        frame, datetime(2026, 1, 13, 11, tzinfo=timezone.utc), "unit", limit=250,
    )

    assert result is not None
    assert len(result) == 250
    assert result.iloc[0]["close"] == 50
    assert result.iloc[-1]["time"] == pd.Timestamp("2026-01-13 11:00", tz="UTC")


def test_pending_breakout_never_becomes_a_market_entry():
    engine = _engine()
    created = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    bar = pd.Series({"open": 1.1000, "high": 1.1008, "low": 1.0995, "close": 1.1005})
    pending, trade, outcome = engine._process_pending(
        _pending(created), bar, created + timedelta(minutes=15), 0.0,
        InstrumentSpec(point=0.00001, tick_size=0.00001, tick_value=1.0),
    )
    assert pending is not None
    assert trade is None
    assert outcome is None


def test_pending_same_bar_stop_and_target_is_stop_first():
    engine = _engine()
    created = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    bar = pd.Series({"open": 1.1000, "high": 1.1060, "low": 1.0980, "close": 1.1020, "atr": 0.001})
    spec = InstrumentSpec(point=0.00001, tick_size=0.00001, tick_value=1.0)
    _, trade, outcome = engine._process_pending(_pending(created), bar, created + timedelta(minutes=15), 0.0, spec)
    assert outcome == "FILLED"
    assert trade is not None
    assert engine._manage_open_trade(trade, bar, created + timedelta(minutes=15), 0.0, spec, pd.DataFrame([bar]), StructureAnalysis()) is True
    assert trade.exit_reason == "SL_HIT"


def test_pending_order_expires_without_a_fill():
    engine = _engine()
    created = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    bar = pd.Series({"open": 1.1000, "high": 1.1005, "low": 1.0995, "close": 1.1000})
    pending, trade, outcome = engine._process_pending(
        _pending(created), bar, created + timedelta(minutes=45), 0.0,
        InstrumentSpec(point=0.00001, tick_size=0.00001, tick_value=1.0),
    )
    assert pending is None
    assert trade is None
    assert outcome == "EXPIRED"


def test_shadow_context_survives_pending_to_trade():
    """A filled trade must retain the candidate's shadow counterfactual."""
    engine = _engine()
    created = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    pending = _pending(created)
    pending.regime_label = "RANGING"
    pending.smc_sweep_aligned = True
    pending.smc_fvg_aligned = False
    pending.smc_order_block_aligned = True
    pending.shadow_reasons = ["REGIME_RANGING"]
    bar = pd.Series({"open": 1.1000, "high": 1.1060, "low": 1.0980, "close": 1.1020, "atr": 0.001})
    spec = InstrumentSpec(point=0.00001, tick_size=0.00001, tick_value=1.0)

    _, trade, outcome = engine._process_pending(pending, bar, created + timedelta(minutes=15), 0.0, spec)

    assert outcome == "FILLED"
    assert trade is not None
    assert trade.regime_label == "RANGING"
    assert trade.smc_sweep_aligned is True
    assert trade.smc_fvg_aligned is False
    assert trade.smc_order_block_aligned is True
    assert trade.shadow_reasons == ["REGIME_RANGING"]
    trade.shadow_reasons.append("EXTRA")
    assert pending.shadow_reasons == ["REGIME_RANGING"]


def test_shadow_context_is_exported_in_the_backtest_csv(tmp_path):
    trade = BacktestTrade(
        trade_id="shadow-csv", symbol="EURUSD", direction="BUY", entry_price=1.1,
        sl_price=1.099, tp_price=1.102, volume=0.1, initial_volume=0.1,
        risk_money=10.0, entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        regime_label="RANGING", smc_sweep_aligned=True,
        smc_fvg_aligned=False, smc_order_block_aligned=True,
        shadow_reasons=["REGIME_RANGING", "SMC_NO_BUY_FVG_OR_ORDER_BLOCK"],
    )
    result = BacktestResult(
        symbol="EURUSD", period_start=trade.entry_time, period_end=trade.entry_time,
        timeframe="M15", trades=[trade],
    )

    report_path = save_report_csv([result], str(tmp_path))
    exported = pd.read_csv(report_path)

    assert exported.loc[0, "regime_label"] == "RANGING"
    assert bool(exported.loc[0, "smc_sweep_aligned"]) is True
    assert bool(exported.loc[0, "smc_fvg_aligned"]) is False
    assert bool(exported.loc[0, "smc_order_block_aligned"]) is True
    assert exported.loc[0, "shadow_reasons"] == "REGIME_RANGING; SMC_NO_BUY_FVG_OR_ORDER_BLOCK"


def test_partial_close_is_realized_in_final_profit():
    engine = _engine()
    spec = InstrumentSpec(point=0.00001, tick_size=0.00001, tick_value=1.0)
    trade = BacktestTrade(
        trade_id="partial", symbol="EURUSD", direction="BUY", entry_price=1.1000,
        sl_price=1.0990, tp_price=1.1050, current_sl=1.0990,
        volume=1.0, initial_volume=1.0, risk_money=100.0,
        entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    bar = pd.Series({"open": 1.1000, "high": 1.1018, "low": 1.1001, "close": 1.1015, "atr": 0.001})
    engine._manage_open_trade(
        trade, bar, trade.entry_time + timedelta(minutes=15), 0.0, spec,
        pd.DataFrame([bar]), StructureAnalysis(),
    )
    assert trade.partial_closed is True
    assert trade.volume == 0.5
    assert trade.realized_gross_money > 0
    trade.exit_price = 1.1010
    trade.exit_time = trade.entry_time + timedelta(minutes=30)
    engine._finalize_trade(trade, spec)
    assert trade.profit_money > 0


def test_higher_timeframe_slice_excludes_the_still_forming_bar():
    engine = _engine()
    times = pd.to_datetime(["2026-01-01T08:00:00Z", "2026-01-01T12:00:00Z"])
    htf = pd.DataFrame({"time": times, "close": [1.0, 2.0]})
    entry_time = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    sliced = engine._closed_htf_slice(htf, entry_time)
    assert len(sliced) == 1
    assert sliced["close"].iloc[-1] == 1.0


def test_holdout_boundary_uses_prior_bars_only_as_warmup(monkeypatch):
    """No order is permitted before the declared untouched test interval."""
    engine = _engine()
    dates = pd.date_range("2026-01-01", periods=270, freq="15min", tz="UTC")
    prices = pd.Series(range(270), dtype=float) * 0.0001 + 1.1
    data = pd.DataFrame({
        "time": dates, "open": prices, "high": prices + 0.0002,
        "low": prices - 0.0002, "close": prices, "spread": 1,
        "tick_volume": 100, "real_volume": 0,
    })
    monkeypatch.setattr(engine, "_add_indicators", lambda frame: frame.assign(atr=0.001, atr_ratio=1.0, ema_fast=1.0, ema_medium=1.0, ema_slow=1.0, rsi=50.0, adx=25.0, macd_histogram=0.0, volume_ratio=1.0, obv=0.0))
    monkeypatch.setattr(engine, "_structure", lambda window: StructureAnalysis())
    monkeypatch.setattr(engine, "_plan_pending_breakout", lambda **kwargs: None)
    boundary = dates[260].to_pydatetime()
    result = engine.run("EURUSD", data, evaluation_start=boundary)
    assert result.period_start == boundary
    assert result.total_trades == 0


def test_backtest_accepts_downloader_time_as_index_and_column():
    engine = _engine()
    times = pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC")
    source = pd.DataFrame({
        "time": times, "open": [1.0, 1.1, 1.2], "high": [1.1, 1.2, 1.3],
        "low": [0.9, 1.0, 1.1], "close": [1.0, 1.1, 1.2],
        "tick_volume": [10, 10, 10], "spread": [1, 1, 1], "real_volume": [0, 0, 0],
    }).set_index("time", drop=False)
    result = engine._add_indicators(source)
    assert len(result) == 3
    assert "time" in result.columns


def test_bootstrap_is_reproducible_and_rejects_tiny_samples():
    first = bootstrap_trade_outcomes([1.0, -1.0, 0.5], paths=100, seed=7)
    second = bootstrap_trade_outcomes([1.0, -1.0, 0.5], paths=100, seed=7)
    assert first == second
    assert first.sufficient_sample is False
    assert first.trade_count == 3


def test_pending_events_lifecycle_and_serialization_tracking(monkeypatch, tmp_path):
    engine = _engine()
    dates = pd.date_range("2026-01-01", periods=260, freq="15min", tz="UTC")
    prices = pd.Series(range(260), dtype=float) * 0.0001 + 1.1
    data = pd.DataFrame({
        "time": dates, "open": prices, "high": prices + 0.0002,
        "low": prices - 0.0002, "close": prices, "spread": 1,
        "tick_volume": 100, "real_volume": 0,
    })
    monkeypatch.setattr(engine, "_add_indicators", lambda frame: frame.assign(
        atr=0.001, atr_ratio=1.0, ema_fast=1.0, ema_medium=1.0, ema_slow=1.0,
        rsi=50.0, adx=25.0, macd_histogram=0.0, volume_ratio=1.0, obv=0.0
    ))
    monkeypatch.setattr(engine, "_structure", lambda window: StructureAnalysis())

    # Create a pending order on the first eligible bar (index 250) with high trigger so it stays pending
    planned = False
    def mock_plan(**kwargs):
        nonlocal planned
        if not planned:
            planned = True
            created_at = kwargs["bar_time"]
            return PendingBreakout(
                symbol="EURUSD", direction=Direction.BUY, trigger_price=1.5000,
                sl_price=1.0500, tp_price=1.6000, volume=1.0, risk_money=200.0,
                created_at=created_at, expires_at=created_at + timedelta(hours=5),
                invalidation_price=1.0500, total_score=80.0, classification="GOOD",
                structure_score=20.0, trend_score=18.0, momentum_score=12.0,
                volume_score=12.0, volatility_score=12.0,
            )
        return None

    monkeypatch.setattr(engine, "_plan_pending_breakout", mock_plan)

    result = engine.run("EURUSD", data)
    assert result.pending_created == 1
    assert len(result.pending_events) >= 1
    created_events = [e for e in result.pending_events if e.event == "CREATED"]
    assert len(created_events) == 1
    assert created_events[0].symbol == "EURUSD"
    assert created_events[0].direction == "BUY"

    # Verify that while pending order was active, subsequent bars incremented signals_skipped_serialization
    assert result.signals_skipped_serialization == 9  # bars 251..259

    # Test CSV export
    csv_path = save_pending_events_csv([result], output_dir=str(tmp_path))
    assert csv_path != ""
    df = pd.read_csv(csv_path)
    assert len(df) == len(result.pending_events)
    assert "event" in df.columns
    assert "trigger_price" in df.columns
    assert "bars_alive" in df.columns


def test_dynamic_pending_order_update(monkeypatch):
    config = AppConfig(
        trade=TradeConfig(
            break_even_r=10.0,
            partial_close=True,
            partial_r=1.0,
            partial_percent=50.0,
            trailing=TrailingMode.HYBRID,
            update_pending_on_new_signal=True,
        ),
        backtest=BacktestConfig(
            commission_per_lot_round_turn=0.0,
            slippage_points=0.0,
            gap_slippage_points=0.0,
        ),
    )
    engine = BacktestEngine(config)
    dates = pd.date_range("2026-01-01", periods=260, freq="15min", tz="UTC")
    prices = pd.Series(range(260), dtype=float) * 0.0001 + 1.1
    data = pd.DataFrame({
        "time": dates, "open": prices, "high": prices + 0.0002,
        "low": prices - 0.0002, "close": prices, "spread": 1,
        "tick_volume": 100, "real_volume": 0,
    })
    monkeypatch.setattr(engine, "_add_indicators", lambda frame: frame.assign(
        atr=0.001, atr_ratio=1.0, ema_fast=1.0, ema_medium=1.0, ema_slow=1.0,
        rsi=50.0, adx=25.0, macd_histogram=0.0, volume_ratio=1.0, obv=0.0
    ))
    monkeypatch.setattr(engine, "_structure", lambda window: StructureAnalysis())

    call_count = 0
    def mock_plan(**kwargs):
        nonlocal call_count
        call_count += 1
        created_at = kwargs["bar_time"]
        # Trigger price moves closer/updates on subsequent bars
        trigger = 1.5000 - (call_count * 0.0010)
        return PendingBreakout(
            symbol="EURUSD", direction=Direction.BUY, trigger_price=trigger,
            sl_price=1.0500, tp_price=1.6000, volume=1.0, risk_money=200.0,
            created_at=created_at, expires_at=created_at + timedelta(hours=5),
            invalidation_price=1.0500, total_score=80.0, classification="GOOD",
            structure_score=20.0, trend_score=18.0, momentum_score=12.0,
            volume_score=12.0, volatility_score=12.0,
        )

    monkeypatch.setattr(engine, "_plan_pending_breakout", mock_plan)

    result = engine.run("EURUSD", data)
    assert result.pending_created == 1
    assert result.pending_updated == 9  # bars 251..259 updated the pending order
    updated_events = [e for e in result.pending_events if e.event == "UPDATED"]
    assert len(updated_events) == 9
    assert result.signals_skipped_serialization == 0


def test_pullback_pending_lifecycle_and_target_reached(monkeypatch):
    """Test that EntryMode.PULLBACK properly tracks limit order fills and EXPIRED_TARGET_REACHED."""
    cfg = AppConfig(
        trade=TradeConfig(
            entry_mode=EntryMode.PULLBACK,
            pullback_expiry_bars=4,
        ),
        backtest=BacktestConfig(
            commission_per_lot_round_turn=0.0,
            slippage_points=0.0,
            gap_slippage_points=0.0,
        ),
    )

    spec = InstrumentSpec(point=0.0001, tick_size=0.0001, tick_value=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01)
    engine = BacktestEngine(cfg, initial_balance=10000.0, instrument_specs={"EURUSD": spec})
    dates = pd.date_range("2026-01-01", periods=260, freq="15min", tz="UTC")
    prices = pd.Series(range(260), dtype=float) * 0.0001 + 1.1
    data = pd.DataFrame({
        "time": dates, "open": prices, "high": prices + 0.0002,
        "low": prices - 0.0002, "close": prices, "spread": 1,
        "tick_volume": 100, "real_volume": 0,
    })
    monkeypatch.setattr(engine, "_add_indicators", lambda frame: frame.assign(
        atr=0.001, atr_ratio=1.0, ema_fast=1.0, ema_medium=1.0, ema_slow=1.0,
        rsi=50.0, adx=25.0, macd_histogram=0.0, volume_ratio=1.0, obv=0.0
    ))
    monkeypatch.setattr(engine, "_structure", lambda window: StructureAnalysis())

    # Create a pullback limit order at bar 250
    # Current close is 1.1050, trigger (broken swing) is 1.1000, SL is 1.0950, TP is 1.1150
    planned = False
    def mock_plan(**kwargs):
        nonlocal planned
        if not planned:
            planned = True
            created_at = kwargs["bar_time"]
            return PendingBreakout(
                symbol="EURUSD", direction=Direction.BUY, trigger_price=1.1000,
                sl_price=1.0950, tp_price=1.1150, volume=1.0, risk_money=50.0,
                created_at=created_at, expires_at=created_at + timedelta(hours=2),
                invalidation_price=1.0950, total_score=80.0, classification="GOOD",
                structure_score=20.0, trend_score=18.0, momentum_score=12.0,
                volume_score=12.0, volatility_score=12.0,
            )
        return None

    monkeypatch.setattr(engine, "_plan_pending_breakout", mock_plan)

    # Bar 251: price shoots up to 1.1160 (hits TP) without dropping to 1.1000
    data.loc[251, "open"] = 1.1060
    data.loc[251, "high"] = 1.1160
    data.loc[251, "low"] = 1.1040
    data.loc[251, "close"] = 1.1155

    result = engine.run("EURUSD", data)
    assert result.pending_created == 1
    assert result.count_expired_target_reached == 1
    assert result.opportunity_cost_r_missed_target >= 2.0
    events = [e for e in result.pending_events if e.event == "EXPIRED_TARGET_REACHED"]
    assert len(events) == 1

