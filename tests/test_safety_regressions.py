from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from core.config_loader import load_config
from core.constants import Direction, SwingClassification, SwingType
from core.models import StructureAnalysis, SwingPoint
from data.cleaner import DataCleaner
from strategy.structure_strategy import StructureStrategy


def test_rejected_profile_is_not_execution_authorized():
    config = load_config("config/intraday")
    assert config.bot.execution_authorized is False


def test_cleaner_rejects_invalid_ohlc_and_ambiguous_index():
    frame = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=2, freq="5min", tz="UTC"),
        "open": [1.0, 1.0], "high": [0.9, 1.1],
        "low": [0.8, 0.9], "close": [1.0, 1.0], "tick_volume": [1, 1],
    }).set_index("time", drop=False)
    _, report = DataCleaner().clean(frame)
    assert report["invalid_ohlc_rows"] == 1
    assert report["quality_ok"] is False


def test_structure_swing_age_is_enforced():
    old_low = SwingPoint(index=0, price=1.0, swing_type=SwingType.SWING_LOW,
                         classification=SwingClassification.HL, bar_index=1)
    analysis = StructureAnalysis(
        swings=[old_low], last_swing_low=old_low, last_bar_index=100,
    )
    result = StructureStrategy().calculate_score(analysis, Direction.BUY, max_swing_age=10)
    assert result.details["recent_swing"] is False


def test_anti_chasing_drift_guard_blocks_late_entry():
    from main import QuantTradingBot
    from unittest.mock import MagicMock
    from strategy.sweep_strategy import SweepReversalSignal

    bot = QuantTradingBot(config_dir="config/bot_a_gold", mode_override="demo")
    bot.connector = MagicMock()
    # Mock tick price drifted 13 dollars below trigger (e.g. trigger 4338.73, bid 4325.0)
    mock_tick = SimpleNamespace(bid=4325.0, ask=4325.5)
    bot.connector.get_symbol_tick.return_value = mock_tick
    bot.connector.get_symbol_info.return_value = SimpleNamespace(point=0.01)
    
    sweep_sig = SweepReversalSignal(
        direction=Direction.SELL,
        entry_price=4338.73,
        sl_price=4362.01,
        tp_price=4261.32,
        level_name="ASIA_HIGH",
        level_price=4360.0,
        penetration_atr=0.1,
        risk_reward=3.3,
    )
    df_entry = pd.DataFrame({"atr": [2.5]})
    
    bot.order_mgr = MagicMock()
    bot._execute_sweep_reversal("XAUUSD", "H1", df_entry, sweep_sig, [], [])
    assert not bot.order_mgr.create_market_order.called
