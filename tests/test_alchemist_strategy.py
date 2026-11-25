"""Unit tests for the Alchemist Strategy (MSNR + CRT + Rule 6).

Verifies:
1. Malaysian Support & Resistance (MSNR) zone detection on line chart open/close.
2. Rule 6: Skip Mondays.
3. Model 1: London Killzone Asian Liquidity Sweep depth and rejection logic.
4. Model 2: New York CRT directional confirmation.
"""
import pandas as pd
import pytest
from datetime import datetime, timezone

from core.config_loader import AlchemistConfig
from core.constants import Direction
from strategy.alchemist import (
    extract_msnr_zones,
    detect_alchemist_setup,
    MSNRZone,
    _fvg_zones,
    _is_adverse_htf_expansion,
)


def test_extract_msnr_zones():
    """Verify [A] resistance and [V] support detection from line chart open/close."""
    # Pattern:
    # Bar 0: Bullish (Open 100, Close 110)
    # Bar 1: Bearish (Open 110, Close 95) -> [A] Resistance at 110
    # Bar 2: Bearish (Open 95, Close 80)
    # Bar 3: Bullish (Open 80, Close 105) -> [V] Support at 80
    data = [
        {"time": "2026-09-01 01:00:00", "open": 100.0, "high": 115.0, "low": 98.0, "close": 110.0},
        {"time": "2026-09-01 02:00:00", "open": 110.0, "high": 112.0, "low": 94.0, "close": 95.0},
        {"time": "2026-09-01 03:00:00", "open": 95.0, "high": 96.0, "low": 78.0, "close": 80.0},
        {"time": "2026-09-01 04:00:00", "open": 80.0, "high": 108.0, "low": 79.0, "close": 105.0},
        {"time": "2026-09-01 05:00:00", "open": 105.0, "high": 112.0, "low": 104.0, "close": 110.0},
    ]
    df = pd.DataFrame(data)
    zones = extract_msnr_zones(df, "H1")

    assert len(zones) >= 2
    types = [z.direction for z in zones]
    assert Direction.SELL in types  # Resistance [A]
    assert Direction.BUY in types   # Support [V]


def test_fvg_zones_detect_closed_three_candle_imbalance():
    frame = pd.DataFrame([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 2.0},
        {"open": 100.0, "high": 104.0, "low": 100.0, "close": 103.0, "atr": 2.0},
        {"open": 103.0, "high": 106.0, "low": 103.0, "close": 105.0, "atr": 2.0},
    ])
    assert _fvg_zones(frame, "H4") == [(Direction.BUY, 101.0, 103.0)]


def test_alchemist_skips_mondays():
    """Verify Rule 6: No trades executed on Mondays."""
    config = AlchemistConfig(enabled=True, avoid_mondays=True)
    # 2026-09-07 was a Monday
    monday_time = pd.Timestamp("2026-09-07 08:30:00", tz="UTC")
    bars = []
    for i in range(25):
        t = monday_time - pd.Timedelta(minutes=15 * (24 - i))
        bars.append({"time": t, "open": 2500.0, "high": 2505.0, "low": 2495.0, "close": 2501.0, "atr": 5.0})
    df_m15 = pd.DataFrame(bars)

    setup = detect_alchemist_setup(
        df_entry=df_m15,
        df_setup=df_m15,
        df_higher=df_m15,
        df_daily=df_m15,
        df_weekly=df_m15,
        config=config,
        point=0.01,
    )
    assert setup is None, "Should skip all trades on Mondays per Rule 6"


def test_london_asian_sweep_rejection():
    """Verify London Killzone detects sweep and rejection back inside Asian range."""
    config = AlchemistConfig(enabled=True, avoid_mondays=False, london_sweep_enabled=True, new_york_crt_enabled=False)
    # Wednesday 2026-09-09
    # Build Asian session (00:00 to 08:00) with High=2510.0, Low=2490.0
    bars = []
    base_date = "2026-09-09"
    for h in range(0, 8):
        for m in (0, 15, 30, 45):
            t = pd.Timestamp(f"{base_date} {h:02d}:{m:02d}:00", tz="UTC")
            bars.append({"time": t, "open": 2500.0, "high": 2510.0, "low": 2490.0, "close": 2500.0, "atr": 10.0})

    # 08:00 bar sweeps Asian Low (2490.0) down to 2486.0 (depth 4.0 = 0.4 ATR) and closes at 2492.0 (inside range!)
    t_sweep = pd.Timestamp(f"{base_date} 08:00:00", tz="UTC")
    bars.append({"time": t_sweep, "open": 2495.0, "high": 2496.0, "low": 2486.0, "close": 2492.0, "atr": 10.0})

    df_m15 = pd.DataFrame(bars)
    setup = detect_alchemist_setup(
        df_entry=df_m15,
        df_setup=df_m15,
        df_higher=df_m15,
        df_daily=df_m15,
        df_weekly=df_m15,
        config=config,
        point=0.01,
    )
    assert setup is not None
    assert setup.direction == Direction.BUY
    assert setup.model == "LONDON_ASIA_SWEEP"
    assert setup.entry_price == 2492.0
    assert setup.sl_price < 2486.0
    assert setup.tp_price > 2492.0


def test_avoid_asian_session():
    """Verify trades during Asian dead zone (e.g. 03:30 London) are blocked."""
    config = AlchemistConfig(enabled=True, avoid_mondays=False, avoid_asian_session=True)
    # Wednesday at 03:30 London time
    t_asia = pd.Timestamp("2026-09-09 03:30:00", tz="Europe/London")
    bars = [{"time": t_asia, "open": 2500.0, "high": 2505.0, "low": 2495.0, "close": 2498.0, "atr": 5.0}]
    df = pd.DataFrame(bars)
    setup = detect_alchemist_setup(
        df_entry=df,
        df_setup=df,
        df_higher=df,
        df_daily=df,
        df_weekly=df,
        config=config,
        point=0.01,
    )
    assert setup is None, "Should block all trades in Asian session when avoid_asian_session=True"


def test_adverse_htf_expansion_veto():
    """Verify strong bullish Marubozu expansion candle vetoes SELL setups mathematically."""
    # Bullish expansion: Open 4155, High 4183.6, Low 4147.2, Close 4183.4 (75% body, 0.5% upper wick)
    df_h4 = pd.DataFrame([
        {"open": 4140.0, "high": 4150.0, "low": 4135.0, "close": 4145.0, "atr": 12.0},
        {"open": 4155.94, "high": 4183.60, "low": 4147.22, "close": 4183.41, "atr": 12.5},
    ])
    assert _is_adverse_htf_expansion(df_h4, Direction.SELL) is True, "Must veto SELL against bullish Marubozu"
    assert _is_adverse_htf_expansion(df_h4, Direction.BUY) is False, "Must NOT veto BUY with bullish Marubozu"
