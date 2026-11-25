import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from unittest.mock import MagicMock

from core.models import Direction
from core.config_loader import load_config
from strategy.decision_context import _session_label
from filters.market_quality_filter import MarketQualityFilter
from ml.features import extract_features


def test_session_label_broker_offset():
    """Verify session hours are shifted for broker UTC+3 offset."""
    # Hour 3 UTC+3 is Asia (00:00 - 10:00 broker)
    t_asia = datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc)
    assert _session_label(t_asia) == "ASIA"

    # Hour 11 UTC+3 is London (10:00 - 15:00 broker)
    t_london = datetime(2026, 9, 21, 11, 0, tzinfo=timezone.utc)
    assert _session_label(t_london) == "LONDON"

    # Hour 16 UTC+3 is NY (15:00 - 20:00 broker)
    t_ny = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    assert _session_label(t_ny) == "NEW_YORK"


def test_market_quality_filter_macro_expansion():
    """Verify macro expansion (e.g. SELL breaking ASIA_LOW / PREV_DAY_LOW) is NOT blocked."""
    cfg = load_config("config/bot_a_gold")
    # Enable shadow mode = false so active rules filter
    cfg.regime_filter.shadow_mode = False
    cfg.smc_filter.shadow_mode = False

    mqf = MarketQualityFilter(cfg)

    # Context with ASIA_LOW sweep during bearish trend expansion
    context_sell_expansion = {
        "regime": {"label": "RANGING"},
        "smc": {
            "sweeps": [
                {
                    "level_name": "ASIA_LOW",
                    "direction": "SELL",
                    "penetration_atr": 0.5,
                    "rejection_confirmed": False,
                }
            ],
            "broken_levels": [],
            "equal_levels": [],
            "fvg_zones": [],
        },
        "structure_score": 25.0,
        "macro_direction": "SELL",
        "bb_expansion": False,
        "spread_ratio": 1.0,
        "atr": 2.5,
        "close": 2700.0,
    }

    res = mqf.assess(context_sell_expansion, Direction.SELL)
    # SMC_CONTRA_LIQUIDITY_SWEEP should NOT be in active reasons
    assert "SMC_CONTRA_LIQUIDITY_SWEEP" not in res.active_reasons
    assert "REGIME_RANGING" not in res.active_reasons
    assert res.approved is True


def test_ml_features_direction_awareness():
    """Verify ML features are properly inverted for SELL trades so downward momentum is positive."""
    # Create sample DataFrame with falling prices (bearish momentum)
    closes = [2720.0 - i * 2.0 for i in range(50)]
    times = [pd.Timestamp("2026-09-21 12:00:00") + pd.Timedelta(minutes=5 * i) for i in range(50)]
    df = pd.DataFrame({
        "time": times,
        "open": [c + 1.0 for c in closes],
        "high": [c + 2.0 for c in closes],
        "low": [c - 2.0 for c in closes],
        "close": closes,
        "volume": [1000.0 for _ in closes],
        "ema_fast": [c + 1.0 for c in closes],
        "ema_medium": [c + 3.0 for c in closes],
        "ema_slow": [c + 5.0 for c in closes],
        "adx": [30.0 for _ in closes],
        "rsi": [35.0 for _ in closes],
        "macd": [-2.0 for _ in closes],
        "macd_histogram": [-0.5 for _ in closes],
        "atr": [2.5 for _ in closes],
        "atr_ratio": [1.0 for _ in closes],
        "volume_ratio": [1.2 for _ in closes],
        "obv": [10000.0 - i * 100.0 for i in range(50)],
    })

    feats_buy = extract_features(df, structure_score=20.0, trend_score=20.0, spread_points=10.0, atr_value=2.5, point_size=0.01, direction="BUY")
    feats_sell = extract_features(df, structure_score=20.0, trend_score=20.0, spread_points=10.0, atr_value=2.5, point_size=0.01, direction="SELL")

    # In falling prices, ema20_slope is negative for BUY, but should be inverted (positive) for SELL
    assert feats_sell["ema20_slope"] > 0
    assert feats_buy["ema20_slope"] < 0
    # price_vs_ema200 should also be inverted
    assert feats_sell["price_vs_ema200"] > 0
    assert feats_buy["price_vs_ema200"] < 0


def test_plan_breakout_entry_logic():
    """Verify breakout entry plans fresh breakout at market vs distant as pending stop."""
    from main import QuantTradingBot

    bot = QuantTradingBot.__new__(QuantTradingBot)
    bot.config = MagicMock()
    bot.config.trade.breakout_buffer_points = 2.0
    bot.memory_bank = None

    symbol_info = MagicMock()
    symbol_info.point = 0.01
    symbol_info.digits = 2
    symbol_info.trade_stops_level = 1.0

    structure = MagicMock()
    swing_low = MagicMock()
    swing_low.price = 2700.00
    structure.last_swing_low = swing_low

    # Case 1: Price has not crossed swing low yet -> pending stop
    tick_unbroken = MagicMock()
    tick_unbroken.bid = 2701.00
    entry_price, reason, is_pending = bot._plan_breakout_entry(
        Direction.SELL, structure, tick_unbroken, symbol_info, atr_val=2.5
    )
    assert is_pending is True
    assert entry_price == 2699.98  # 2700.00 - 2 pts buffer (0.02)

    # Case 2: Price just crossed swing low (2699.50) within 0.35 ATR tolerance (2.5 * 0.35 = 0.875)
    # Drift = 2699.98 - 2699.50 = 0.48 <= 1.5 max_chase_distance -> Market entry!
    tick_fresh = MagicMock()
    tick_fresh.bid = 2699.50
    entry_price, reason, is_pending = bot._plan_breakout_entry(
        Direction.SELL, structure, tick_fresh, symbol_info, atr_val=2.5
    )
    assert is_pending is False
    assert entry_price == 2699.50

    # Case 3: Price is overextended (> max_chase_distance) -> rejected
    tick_overextended = MagicMock()
    tick_overextended.bid = 2690.00
    entry_price, reason, is_pending = bot._plan_breakout_entry(
        Direction.SELL, structure, tick_overextended, symbol_info, atr_val=2.5
    )
    assert entry_price is None
    assert "overextended" in reason
