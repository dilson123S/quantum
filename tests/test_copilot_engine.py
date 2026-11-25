"""Unit tests for MarketRadar and TradingCopilot engines."""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from core.constants import Direction, SwingType, SwingClassification
from core.models import StructureAnalysis, SwingPoint
from monitoring.copilot_engine import MarketRadar, TradingCopilot


def _sample_df(n_bars: int = 100, base_price: float = 4350.0) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    times = [now - timedelta(hours=n_bars - i) for i in range(n_bars)]
    highs = [base_price + 10.0 + np.sin(i / 5.0) * 20.0 for i in range(n_bars)]
    lows = [base_price - 10.0 + np.sin(i / 5.0) * 20.0 for i in range(n_bars)]
    closes = [(h + l) / 2.0 for h, l in zip(highs, lows)]
    opens = [c - 1.0 for c in closes]
    atrs = [15.0] * n_bars
    return pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "atr": atrs,
    })


def _sample_structure() -> StructureAnalysis:
    swings = [
        SwingPoint(index=10, price=4420.0, swing_type=SwingType.SWING_HIGH, classification=SwingClassification.HH, bar_index=10),
        SwingPoint(index=25, price=4300.0, swing_type=SwingType.SWING_LOW, classification=SwingClassification.LL, bar_index=25),
        SwingPoint(index=40, price=4400.0, swing_type=SwingType.SWING_HIGH, classification=SwingClassification.LH, bar_index=40),
        SwingPoint(index=55, price=4280.0, swing_type=SwingType.SWING_LOW, classification=SwingClassification.LL, bar_index=55),
    ]
    return StructureAnalysis(
        swings=swings,
        last_swing_high=swings[2],
        last_swing_low=swings[3],
        current_trend=Direction.SELL,
    )


def test_market_radar_extracts_levels():
    radar = MarketRadar()
    df = _sample_df()
    structure = _sample_structure()
    current_price = 4330.0

    levels = radar.extract_key_levels("XAUUSD", current_price, df, structure, point=0.01, digits=2)

    assert "supports" in levels
    assert "resistances" in levels
    assert len(levels["supports"]) > 0
    assert len(levels["resistances"]) > 0

    nearest_res = levels["nearest_resistance"]
    nearest_sup = levels["nearest_support"]

    assert nearest_res is not None
    assert nearest_res["price"] > current_price
    assert nearest_sup is not None
    assert nearest_sup["price"] < current_price


def test_trading_copilot_evaluates_sell_trade():
    copilot = TradingCopilot()
    df = _sample_df()
    structure = _sample_structure()
    current_price = 4330.0

    radar_data = copilot.radar.extract_key_levels("XAUUSD", current_price, df, structure, point=0.01, digits=2)

    trade = {
        "ticket": 1897528573,
        "symbol": "XAUUSD",
        "direction": "SELL",
        "volume": 0.01,
        "price_open": 4395.84,
        "price_current": 4330.0,
        "sl": 4414.39,
        "tp": 4141.92,
        "profit": 65.50,
    }

    diag = copilot.evaluate_active_trade(trade, radar_data, digits=2)

    assert diag["ticket"] == 1897528573
    assert diag["direction"] == "SELL"
    assert diag["profit_usd"] == 65.50
    assert diag["r_multiple"] > 0
    assert diag["semaphore"] in {"GREEN", "YELLOW", "RED"}
    assert len(diag["explanation"]) > 0
    assert len(diag["recommendations"]) > 0

    # Check recommendation actions
    actions = [r["action"] for r in diag["recommendations"]]
    assert "BREAK_EVEN" in actions
    assert "HOLD" in actions


def test_copilot_build_complete_snapshot():
    copilot = TradingCopilot()
    df = _sample_df()
    structure = _sample_structure()
    current_price = 4330.0

    trades = [{
        "ticket": 1897528573,
        "symbol": "XAUUSD",
        "direction": "SELL",
        "volume": 0.01,
        "price_open": 4395.84,
        "price_current": 4330.0,
        "sl": 4414.39,
        "tp": 4141.92,
        "profit": 65.50,
    }]

    snapshot = copilot.build_complete_snapshot(
        symbol="XAUUSD",
        current_price=current_price,
        df_entry=df,
        structure=structure,
        active_positions=trades,
        point=0.01,
        digits=2
    )

    assert snapshot["symbol"] == "XAUUSD"
    assert snapshot["current_price"] == current_price
    assert len(snapshot["active_diagnostics"]) == 1
    assert snapshot["active_diagnostics"][0]["ticket"] == 1897528573
