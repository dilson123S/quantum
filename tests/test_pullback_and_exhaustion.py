import pytest
import pandas as pd
from unittest.mock import MagicMock
from datetime import datetime, timezone

from core.constants import Direction
from main import QuantTradingBot
from core.config_loader import AppConfig, BotConfig, RiskConfig, TradeConfig


def test_plan_pullback_entry_logic():
    bot = QuantTradingBot.__new__(QuantTradingBot)
    
    # Mock structure with last_swing_low at 4124.0
    structure = MagicMock()
    structure.last_swing_low = MagicMock(price=4124.0)
    structure.last_swing_high = MagicMock(price=4150.0)

    symbol_info = MagicMock(point=0.01, digits=2)
    
    # Case 1: Price is down in the hole at 4115.4 (way below 4124.0 breakdown level)
    tick_hole = MagicMock(bid=4115.4, ask=4115.6)
    entry, reason, is_pending = bot._plan_pullback_entry(
        direction=Direction.SELL,
        structure=structure,
        tick=tick_hole,
        symbol_info=symbol_info,
        atr_val=4.0,
    )
    assert entry is None
    assert "extended in the hole" in reason
    assert is_pending is False

    # Case 2: Price has pulled back to retest zone (4123.5)
    tick_retest = MagicMock(bid=4123.5, ask=4123.7)
    df_retest = pd.DataFrame([
        {"open": 4122.0, "high": 4124.5, "low": 4121.5, "close": 4123.0}, # Bearish rejection at level
    ])
    entry, reason, is_pending = bot._plan_pullback_entry(
        direction=Direction.SELL,
        structure=structure,
        tick=tick_retest,
        symbol_info=symbol_info,
        atr_val=4.0,
        df_entry=df_retest,
    )
    assert entry == 4123.5
    assert reason is None
    assert is_pending is False


def test_trend_exhaustion_and_support_guard():
    bot = QuantTradingBot.__new__(QuantTradingBot)
    
    # Build H1 setup dataframe with drop from 4190 down to 4111.63
    bars = []
    # Drop over 30 bars from 4190 to 4112 with no major pullback
    p = 4190.0
    for i in range(30):
        p -= 2.6
        bars.append({"open": p + 1.0, "high": p + 1.5, "low": p - 0.5, "close": p, "atr": 12.0})
    # Lowest bar reaches 4111.63
    bars[-1] = {"open": 4115.0, "high": 4117.0, "low": 4111.63, "close": 4114.0, "atr": 12.0}
    df_setup = pd.DataFrame(bars)

    # Daily ATR is ~28.0. Drop from 4190 to 4115 is 75 pts (2.67x Daily ATR!)
    df_htf = pd.DataFrame([{"atr": 28.0}] * 20)

    # Case 1: At 4115.4, it is both exhausted (>2.5x ATR_D1) and within 0.35 ATR of major support (4111.63)
    ok, reason = bot._check_trend_exhaustion_and_support(
        direction=Direction.SELL,
        symbol="XAUUSD",
        current_price=4115.40,
        df_entry=pd.DataFrame(),
        df_setup=df_setup,
        df_htf=df_htf,
        atr_val=12.0,
    )
    assert ok is False
    assert "exhausted" in reason or "support" in reason

    # Case 2: Healthy trend with drop of only 20 pts (0.7x Daily ATR)
    bars_fresh = []
    p = 4190.0
    for i in range(10):
        p -= 2.0
        bars_fresh.append({"open": p + 1.0, "high": p + 1.5, "low": p - 0.5, "close": p, "atr": 12.0})
    df_setup_fresh = pd.DataFrame(bars_fresh)
    ok_fresh, reason_fresh = bot._check_trend_exhaustion_and_support(
        direction=Direction.SELL,
        symbol="XAUUSD",
        current_price=4170.0,
        df_entry=pd.DataFrame(),
        df_setup=df_setup_fresh,
        df_htf=df_htf,
        atr_val=12.0,
    )
    assert ok_fresh is True
    assert reason_fresh is None
