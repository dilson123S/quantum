"""Focused safety and closed-bar checks for the experimental MR.R model."""
import pandas as pd

from core.config_loader import AlchemistConfig
from core.constants import Direction
from strategy.alchemist import MSNRZone
import strategy.msnr_storyline as storyline
from strategy.msnr_storyline import (
    _StoryEvent,
    _breaks_structure,
    _closed_frame,
    _recent_h4_rejections,
    _select_entry_story,
    detect_msnr_storyline_setup,
)


def _frame(start: str, count: int, frequency: str) -> pd.DataFrame:
    times = pd.date_range(start, periods=count, freq=frequency, tz="UTC")
    return pd.DataFrame({
        "time": times,
        "open": [100.0] * count,
        "high": [101.0] * count,
        "low": [99.0] * count,
        "close": [100.0] * count,
    })


def test_closed_frame_excludes_a_still_open_daily_candle():
    daily = _frame("2026-09-20 00:00", 4, "1D")

    closed = _closed_frame(daily, "D1", pd.Timestamp("2026-09-23 12:00", tz="UTC"))

    assert closed is not None
    assert len(closed) == 3
    assert closed.iloc[-1]["time"] == pd.Timestamp("2026-09-22 00:00", tz="UTC")


def test_h1_model_fails_closed_when_given_bot_es_m15_stream():
    m15 = _frame("2026-09-23 00:00", 24, "15min")
    h4 = _frame("2026-09-22 00:00", 12, "4h")
    daily = _frame("2026-09-10 00:00", 14, "1D")
    weekly = _frame("2026-08-01 00:00", 8, "7D")
    monthly = _frame("2026-01-01 00:00", 5, "30D")
    config = AlchemistConfig(enabled=True, entry_model="msnr_storyline_h1")

    setup = detect_msnr_storyline_setup(
        m15, h4, daily, weekly, monthly, config, point=0.001,
    )

    assert setup is None


def test_story_structure_break_requires_closed_directional_close():
    bars = pd.DataFrame([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
        {"open": 100.4, "high": 101.2, "low": 100.0, "close": 101.0},
        {"open": 101.1, "high": 101.5, "low": 100.5, "close": 101.3},
        {"open": 101.4, "high": 102.0, "low": 101.0, "close": 101.8},
    ])

    assert _breaks_structure(bars, 3, Direction.BUY, lookback=2)
    assert not _breaks_structure(bars, 3, Direction.SELL, lookback=2)


def _story(direction: Direction) -> _StoryEvent:
    stamp = pd.Timestamp("2026-01-01", tz="UTC")
    return _StoryEvent(direction=direction, parent_time=stamp, confirmed_time=stamp, level=100.0)


def test_daily_story_has_priority_over_higher_conflicting_stories():
    daily, weekly, monthly = _story(Direction.BUY), _story(Direction.SELL), _story(Direction.SELL)

    chosen, label = _select_entry_story(daily, weekly, monthly, allow_higher_fallback=True)

    assert chosen is daily
    assert label == "D1_STORYLINE"


def test_weekly_then_monthly_are_only_fallbacks_when_enabled():
    weekly, monthly = _story(Direction.BUY), _story(Direction.SELL)

    chosen, label = _select_entry_story(None, weekly, monthly, allow_higher_fallback=True)
    disabled, disabled_label = _select_entry_story(None, weekly, monthly, allow_higher_fallback=False)

    assert chosen is weekly
    assert label == "W1_STORYLINE_FALLBACK"
    assert disabled is None
    assert disabled_label == ""


def test_monthly_story_is_last_fallback():
    monthly = _story(Direction.SELL)

    chosen, label = _select_entry_story(None, None, monthly, allow_higher_fallback=True)

    assert chosen is monthly
    assert label == "MN1_STORYLINE_FALLBACK"


def test_recent_h4_rejection_index_maps_back_to_full_history(monkeypatch):
    h4 = _frame("2026-01-01 00:00", 200, "4h")
    h4.loc[199, ["open", "high", "low", "close"]] = [100.1, 100.8, 100.0, 100.6]
    zone = MSNRZone(
        name="H4_V_SUP", direction=Direction.BUY, price=100.3, width=0.2,
        fresh=True, timeframe="H4", bar_index=170,
    )
    monkeypatch.setattr(storyline, "extract_msnr_zones", lambda *_args, **_kwargs: [zone])

    rejections = _recent_h4_rejections(
        h4, Direction.BUY, pd.Timestamp(h4.iloc[-4]["time"]) + pd.Timedelta(hours=4),
    )

    assert len(rejections) == 1
    assert rejections[0].index == len(h4) - 1
    assert h4.iloc[rejections[0].index]["close"] == 100.6
