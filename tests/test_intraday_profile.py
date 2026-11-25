"""Safety and anti-leakage regression tests for the intraday experiment."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from core.config_loader import AppConfig, ConfirmationConfig, ScoreConfig, TimeframeConfig, load_config
from core.constants import Direction, SignalClassification, SwingClassification, SwingType, StructureBreakType
from core.models import ComponentScore, ScoreResult, StructureAnalysis, StructureBreak, SwingPoint
from indicators.momentum import calculate_all_momentum_indicators
from indicators.trend import calculate_all_trend_indicators
from indicators.volatility import calculate_all_volatility_indicators
from indicators.volume import calculate_all_volume_indicators
from strategy.scorer import ScoreEngine
from strategy.decision_context import build_decision_context


def _bullish_setup_data():
    prices = np.linspace(100, 150, 100) + np.random.default_rng(7).normal(0, 0.02, 100)
    frame = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=100, freq="5min", tz="UTC"),
        "open": prices - 0.1, "high": prices + 0.5, "low": prices - 0.5,
        "close": prices, "tick_volume": [1500] * 100,
        "real_volume": [1500] * 100, "spread": [2] * 100,
    })
    frame = calculate_all_trend_indicators(frame)
    frame = calculate_all_momentum_indicators(frame)
    frame = calculate_all_volatility_indicators(frame)
    frame = calculate_all_volume_indicators(frame)
    swings = [
        SwingPoint(index=0, price=100, swing_type=SwingType.SWING_LOW, classification=SwingClassification.HL, bar_index=10),
        SwingPoint(index=1, price=120, swing_type=SwingType.SWING_HIGH, classification=SwingClassification.HH, bar_index=30),
        SwingPoint(index=2, price=115, swing_type=SwingType.SWING_LOW, classification=SwingClassification.HL, bar_index=50),
        SwingPoint(index=3, price=140, swing_type=SwingType.SWING_HIGH, classification=SwingClassification.HH, bar_index=70),
    ]
    return frame, StructureAnalysis(
        swings=swings,
        bos_list=[StructureBreak(break_type=StructureBreakType.BOS, direction=Direction.BUY, price=120, bar_index=65, swing_broken=swings[1])],
        last_swing_high=swings[3], last_swing_low=swings[2], structure_direction=Direction.BUY,
    )


def test_intraday_profile_is_paper_safe_and_has_small_risk():
    config = load_config("config/intraday")

    assert config.bot.mode.value == "paper"
    assert config.bot.allow_live_trading is False
    assert config.bot.journal_path == "data/intraday_bot.db"
    assert config.timeframes.entry == "M5"
    assert config.timeframes.setup == "M15"
    assert config.confirmation.require_setup_trend_alignment is True
    assert config.risk.risk_percent == 0.25
    assert config.risk.max_risk_per_trade == 0.25
    assert config.telemetry.target_daily_trades == 5
    assert len(config.market.symbols) == 7
    assert config.decision_context.shadow_mode is True
    assert config.decision_context.history_bars >= 650


def test_decision_context_uses_confirmed_history_without_becoming_a_trade_rule():
    entry, structure = _bullish_setup_data()
    # Include a completed prior UTC day and a current London bar, which proves
    # session/day levels are built from the supplied (closed) frame only.
    entry["time"] = pd.date_range("2026-01-01 22:00", periods=len(entry), freq="5min", tz="UTC")
    context = build_decision_context(
        entry,
        structure,
        Direction.BUY,
        min_atr_ratio=0.8,
        max_atr_ratio=2.5,
        adx_min=20,
    )

    assert context["version"] == "shadow-context-v2"
    assert context["candidate_direction"] == "BUY"
    assert context["regime"]["label"] in {"TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "TRANSITIONAL"}
    assert context["liquidity"]["previous_day"]["high"] is not None
    assert context["fibonacci"]["available"] is True


def test_setup_slice_excludes_m15_candle_still_forming_at_m5_entry():
    engine = BacktestEngine(AppConfig(timeframes=TimeframeConfig(higher="H1", setup="M15", entry="M5")))
    setup = pd.DataFrame({
        "time": pd.to_datetime(["2026-01-01T09:45:00Z", "2026-01-01T10:00:00Z"]),
        "close": [1.0, 2.0],
    })

    # An M5 bar opened at 10:10 has just closed at 10:15.  The M15 candle
    # opened at 10:00 only completes at 10:15, so it stays excluded.
    sliced = engine._closed_setup_slice(
        setup, datetime(2026, 1, 1, 10, 10, tzinfo=timezone.utc)
    )

    assert len(sliced) == 1
    assert sliced["close"].iloc[-1] == 1.0


def test_setup_confirmation_rejects_missing_setup_data():
    entry, structure = _bullish_setup_data()
    config = AppConfig(
        score=ScoreConfig(minimum=0, min_structure=0, min_trend=0),
        confirmation=ConfirmationConfig(
            require_setup_trend_alignment=True,
            setup_min_trend_score=1,
        ),
    )
    engine = ScoreEngine(config)

    result, signal = engine.calculate_score(
        symbol="EURUSD",
        timeframe="M5",
        df_entry=entry,
        df_setup=None,
        df_htf=entry,
        structure_analysis=structure,
    )

    assert result is not None
    assert signal is None
    assert result.trend.details["setup_trend_aligned"] is False


def test_setup_confirmation_accepts_aligned_setup_data():
    entry, structure = _bullish_setup_data()
    config = AppConfig(
        score=ScoreConfig(minimum=0, min_structure=0, min_trend=0),
        confirmation=ConfirmationConfig(
            require_setup_trend_alignment=True,
            setup_min_trend_score=1,
        ),
    )
    engine = ScoreEngine(config)

    result, signal = engine.calculate_score(
        symbol="EURUSD",
        timeframe="M5",
        df_entry=entry,
        df_setup=entry,
        df_htf=entry,
        structure_analysis=structure,
    )

    assert result is not None
    assert signal is not None
    assert result.trend.details["setup_trend_aligned"] is True


def test_aligned_secondary_direction_is_selected_when_raw_leader_fails_setup_filter():
    config = AppConfig(
        score=ScoreConfig(minimum=75, min_structure=18, min_trend=15),
        confirmation=ConfirmationConfig(
            require_setup_trend_alignment=True,
            setup_min_trend_score=15,
            select_highest_passing_direction=True,
        ),
    )
    engine = ScoreEngine(config)

    def make_result(direction, total):
        return ScoreResult(
            direction=direction,
            total_score=total,
            classification=SignalClassification.from_score(total),
            structure=ComponentScore(name="structure", score=22, max_score=30),
            trend=ComponentScore(name="trend", score=18, max_score=25),
            momentum=ComponentScore(name="momentum", score=12, max_score=15),
            volume=ComponentScore(name="volume", score=10, max_score=15),
            volatility=ComponentScore(name="volatility", score=12, max_score=15),
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    candidates = {
        Direction.SELL: make_result(Direction.SELL, 80),
        Direction.BUY: make_result(Direction.BUY, 77),
    }
    engine.evaluate_direction = lambda direction, *_args, **_kwargs: candidates[direction]
    engine.trend_strat.calculate_score = lambda *, direction, **_kwargs: ComponentScore(
        name="trend", score=25 if direction == Direction.BUY else 5, max_score=25,
    )

    result, signal = engine.calculate_score(
        symbol="USDJPY",
        timeframe="M15",
        df_entry=pd.DataFrame({"close": [157.5]}),
        df_setup=pd.DataFrame({"close": [157.5]}),
        df_htf=None,
        structure_analysis=StructureAnalysis(),
    )

    assert result is not None
    assert result.direction == Direction.BUY
    assert result.total_score == 77
    assert result.trend.details["setup_trend_aligned"] is True
    assert signal is not None
    assert signal.direction == Direction.BUY
