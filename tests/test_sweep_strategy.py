"""Tests for SweepReversalStrategy (Liquidity Sweep / SFP exploitation)."""
from datetime import datetime, timezone
import pandas as pd

from core.config_loader import AppConfig, SweepReversalConfig
from core.constants import Direction
from strategy.sweep_strategy import SweepReversalStrategy


def _sweep_frame() -> pd.DataFrame:
    """Create DataFrame representing a gold candle that swept 4434.10 and closed at 4432.00."""
    return pd.DataFrame({
        "time": pd.date_range("2026-09-10 05:00", periods=5, freq="1h", tz="UTC"),
        "open": [4410.0, 4420.0, 4425.0, 4430.0, 4431.0],
        "high": [4415.0, 4424.0, 4428.0, 4432.0, 4434.6],  # Swept high of 4434.10
        "low": [4405.0, 4418.0, 4422.0, 4428.0, 4429.0],
        "close": [4412.0, 4422.0, 4426.0, 4430.5, 4432.0],  # Closed back below level
        "atr": [10.0] * 5,
        "tick_volume": [1000] * 5,
    })


def test_detect_bearish_sweep_reversal():
    config = AppConfig(sweep_reversal=SweepReversalConfig(
        enabled=True,
        min_penetration_atr=0.05,
        min_rr=2.0,
        sl_buffer_atr=0.10,
        target_opposite_level=True,
    ))
    strategy = SweepReversalStrategy(config)

    decision_context = {
        "liquidity": {
            "sweep": {
                "detected": True,
                "direction": "SELL",
                "level": "PREVIOUS_DAY_HIGH",
                "penetration_atr": 0.15,
            },
            "previous_day": {"high": 4434.1, "low": 4390.0},
            "nearest_level": {"name": "PREVIOUS_DAY_HIGH", "price": 4434.1},
        }
    }

    df = _sweep_frame()
    signal = strategy.detect_sweep_reversal(
        symbol="XAUUSD",
        timeframe="H1",
        df=df,
        decision_context=decision_context,
        digits=2,
    )

    assert signal is not None
    assert signal.direction == Direction.SELL
    assert signal.entry_price == 4432.0  # Closed price
    # SL is high (4434.6) + atr * 0.10 (1.0) = 4435.6
    assert signal.sl_price == 4435.6
    # TP is target level 4390.0
    assert signal.tp_price == 4390.0
    assert signal.risk_reward >= 2.0


def test_detect_bullish_sweep_reversal():
    config = AppConfig(sweep_reversal=SweepReversalConfig(
        enabled=True,
        min_penetration_atr=0.05,
        min_rr=2.0,
        sl_buffer_atr=0.10,
        target_opposite_level=True,
    ))
    strategy = SweepReversalStrategy(config)

    # Pierced low of 4400.0, reached 4398.0, closed at 4403.0
    df = pd.DataFrame({
        "time": pd.date_range("2026-09-10 05:00", periods=5, freq="1h", tz="UTC"),
        "open": [4420.0, 4415.0, 4410.0, 4405.0, 4402.0],
        "high": [4425.0, 4418.0, 4412.0, 4408.0, 4406.0],
        "low": [4415.0, 4410.0, 4404.0, 4401.0, 4398.0],  # Swept low of 4400.0
        "close": [4418.0, 4412.0, 4406.0, 4403.0, 4403.0],  # Closed back above 4400.0
        "atr": [10.0] * 5,
        "tick_volume": [1000] * 5,
    })

    decision_context = {
        "liquidity": {
            "sweep": {
                "detected": True,
                "direction": "BUY",
                "level": "PREVIOUS_DAY_LOW",
                "penetration_atr": 0.20,
            },
            "previous_day": {"high": 4440.0, "low": 4400.0},
            "nearest_level": {"name": "PREVIOUS_DAY_LOW", "price": 4400.0},
        }
    }

    signal = strategy.detect_sweep_reversal(
        symbol="XAUUSD",
        timeframe="H1",
        df=df,
        decision_context=decision_context,
        digits=2,
    )

    assert signal is not None
    assert signal.direction == Direction.BUY
    assert signal.entry_price == 4403.0
    # SL is low (4398.0) - atr * 0.10 (1.0) = 4397.0
    assert signal.sl_price == 4397.0
    assert signal.tp_price == 4440.0
    assert signal.risk_reward >= 2.0


def test_sweep_strategy_disabled_returns_none():
    config = AppConfig(sweep_reversal=SweepReversalConfig(enabled=False))
    strategy = SweepReversalStrategy(config)
    decision_context = {"liquidity": {"sweep": {"detected": True, "direction": "SELL"}}}
    assert strategy.detect_sweep_reversal("XAUUSD", "H1", _sweep_frame(), decision_context) is None
