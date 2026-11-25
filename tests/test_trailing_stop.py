import pytest
import pandas as pd
from types import SimpleNamespace

from core.constants import TrailingMode, Direction, SwingType, SwingClassification
from core.models import SwingPoint, StructureAnalysis
from execution.position_manager import PositionManager


class MockConnector:
    def __init__(self, digits=2, point=0.01, stops_level=10):
        self._digits = digits
        self._point = point
        self._stops_level = stops_level

    def get_symbol_info(self, symbol):
        return SimpleNamespace(digits=self._digits, point=self._point, stops_level=self._stops_level)


class MockOrderManager:
    def __init__(self):
        self.modified = []
        self.closed = []

    def modify_sltp(self, ticket, sl, tp):
        self.modified.append((ticket, sl, tp))
        return SimpleNamespace(success=True)

    def close_order(self, ticket, volume=None):
        self.closed.append((ticket, volume))
        return SimpleNamespace(success=True)


def test_trailing_stop_activation_threshold_blocks_early_moves():
    """Trade with profit < activation_r * risk must NOT trail SL."""
    connector = MockConnector()
    order_mgr = MockOrderManager()
    config = SimpleNamespace(
        trade=SimpleNamespace(
            trailing=TrailingMode.HYBRID,
            trailing_activation_r=1.0,
            trailing_struct_buffer_atr=0.3,
            min_trailing_distance_atr=0.8,
            trailing_atr_multiplier=2.0,
        )
    )
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)

    # Buy opened at 4286.0 with initial SL at 4278.0 (risk = 8.0)
    # Price currently at 4290.0 (profit = 4.0 = 0.5R < 1.0R)
    mock_pos = SimpleNamespace(
        ticket=1001,
        symbol="XAUUSD",
        type=0,  # BUY
        price_open=4286.0,
        price_current=4290.0,
        sl=4278.0,
        tp=4350.0,
    )
    df_entry = pd.DataFrame({"atr": [4.0], "close": [4290.0]})

    swing_low = SwingPoint(
        index=0,
        price=4288.0,
        swing_type=SwingType.SWING_LOW,
        classification=SwingClassification.HL,
        bar_index=10,
    )
    analysis = StructureAnalysis(
        swings=[swing_low],
        bos_list=[],
        choch_list=[],
        last_swing_high=None,
        last_swing_low=swing_low,
        structure_direction=Direction.BUY,
    )

    result = pos_mgr.check_trailing_stop(mock_pos, df_entry, analysis)
    # Should NOT trigger trailing stop because profit (0.5R) < activation threshold (1.0R)
    assert result is False
    assert len(order_mgr.modified) == 0


def test_trailing_stop_activates_when_threshold_reached():
    """Trade with profit >= activation_r * risk must trail SL."""
    connector = MockConnector()
    order_mgr = MockOrderManager()
    config = SimpleNamespace(
        trade=SimpleNamespace(
            trailing=TrailingMode.HYBRID,
            trailing_activation_r=1.0,
            trailing_struct_buffer_atr=0.3,
            min_trailing_distance_atr=0.8,
            trailing_atr_multiplier=2.0,
        )
    )
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)

    # Buy opened at 4286.0 with initial SL at 4278.0 (risk = 8.0)
    # Price now at 4296.0 (profit = 10.0 = 1.25R >= 1.0R)
    mock_pos = SimpleNamespace(
        ticket=1002,
        symbol="XAUUSD",
        type=0,  # BUY
        price_open=4286.0,
        price_current=4296.0,
        sl=4278.0,
        tp=4350.0,
    )
    atr = 4.0
    df_entry = pd.DataFrame({"atr": [atr], "close": [4296.0]})

    # Swing low formed at 4290.0
    # Structural buffer: 0.3 * 4.0 = 1.2 -> struct_sl = 4290.0 - 1.2 = 4288.8
    # Min breathing distance: 0.8 * 4.0 = 3.2 -> max_allowed_sl = 4296.0 - 3.2 = 4292.8
    # struct_sl (4288.8) is < 4292.8, so SL = 4288.8
    swing_low = SwingPoint(
        index=0,
        price=4290.0,
        swing_type=SwingType.SWING_LOW,
        classification=SwingClassification.HL,
        bar_index=10,
    )
    analysis = StructureAnalysis(
        swings=[swing_low],
        bos_list=[],
        choch_list=[],
        last_swing_high=None,
        last_swing_low=swing_low,
        structure_direction=Direction.BUY,
    )

    result = pos_mgr.check_trailing_stop(mock_pos, df_entry, analysis)
    assert result is True
    assert len(order_mgr.modified) == 1
    assert order_mgr.modified[0][0] == 1002
    assert order_mgr.modified[0][1] == 4288.8


def test_trailing_stop_min_distance_clamps_close_sl():
    """SL candidate that is too close to live price must be clamped by min_trailing_distance_atr."""
    connector = MockConnector()
    order_mgr = MockOrderManager()
    config = SimpleNamespace(
        trade=SimpleNamespace(
            trailing=TrailingMode.STRUCTURE,
            trailing_activation_r=1.0,
            trailing_struct_buffer_atr=0.1,
            min_trailing_distance_atr=1.0,  # Require 1.0 ATR distance
            trailing_atr_multiplier=2.0,
        )
    )
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)

    # Buy opened at 4280.0, SL at 4270.0 (risk = 10.0)
    # Price current at 4300.0 (profit = 20.0 = 2.0R)
    mock_pos = SimpleNamespace(
        ticket=1003,
        symbol="XAUUSD",
        type=0,
        price_open=4280.0,
        price_current=4300.0,
        sl=4270.0,
        tp=4350.0,
    )
    atr = 5.0
    df_entry = pd.DataFrame({"atr": [atr], "close": [4300.0]})

    # Swing low at 4298.0 (only 2.0 away from current price 4300.0)
    # With buffer 0.1 * 5.0 = 0.5 -> struct_sl = 4297.5
    # But min_distance is 1.0 * 5.0 = 5.0 -> max_allowed_sl = 4300.0 - 5.0 = 4295.0
    # Trailing must clamp to 4295.0 to give market room to breathe!
    swing_low = SwingPoint(
        index=0,
        price=4298.0,
        swing_type=SwingType.SWING_LOW,
        classification=SwingClassification.HL,
        bar_index=10,
    )
    analysis = StructureAnalysis(
        swings=[swing_low],
        bos_list=[],
        choch_list=[],
        last_swing_high=None,
        last_swing_low=swing_low,
        structure_direction=Direction.BUY,
    )

    result = pos_mgr.check_trailing_stop(mock_pos, df_entry, analysis)
    assert result is True
    assert len(order_mgr.modified) == 1
    assert order_mgr.modified[0][1] == 4295.0


def test_trailing_stop_sell_direction_with_buffer_and_min_distance():
    """SELL position trails downward safely with buffer above swing high and min distance."""
    connector = MockConnector()
    order_mgr = MockOrderManager()
    config = SimpleNamespace(
        trade=SimpleNamespace(
            trailing=TrailingMode.HYBRID,
            trailing_activation_r=1.0,
            trailing_struct_buffer_atr=0.3,
            min_trailing_distance_atr=0.8,
            trailing_atr_multiplier=3.0,
        )
    )
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)

    # SELL opened at 2000.0, SL at 2010.0 (risk = 10.0)
    # Current price at 1985.0 (profit = 15.0 = 1.5R >= 1.0R)
    mock_pos = SimpleNamespace(
        ticket=1004,
        symbol="XAUUSD",
        type=1,  # SELL
        price_open=2000.0,
        price_current=1985.0,
        sl=2010.0,
        tp=1950.0,
    )
    atr = 4.0
    df_entry = pd.DataFrame({"atr": [atr], "close": [1985.0]})

    # Swing high formed at 1992.0
    # Structural buffer: 0.3 * 4.0 = 1.2 -> struct_sl = 1992.0 + 1.2 = 1993.2
    # Min breathing distance: 0.8 * 4.0 = 3.2 -> min_allowed_sl = 1985.0 + 3.2 = 1988.2
    # struct_sl (1993.2) is >= 1988.2, so SL = 1993.2 (tightened from initial 2010.0)
    swing_high = SwingPoint(
        index=0,
        price=1992.0,
        swing_type=SwingType.SWING_HIGH,
        classification=SwingClassification.LH,
        bar_index=10,
    )
    analysis = StructureAnalysis(
        swings=[swing_high],
        bos_list=[],
        choch_list=[],
        last_swing_high=swing_high,
        last_swing_low=None,
        structure_direction=Direction.SELL,
    )

    result = pos_mgr.check_trailing_stop(mock_pos, df_entry, analysis)
    assert result is True
    assert len(order_mgr.modified) == 1
    assert order_mgr.modified[0][1] == 1993.2


def test_stagnation_exit_triggers_on_stale_trade():
    """Trade open for >= stagnation_exit_bars with profit < min_progress_r must be closed at market."""
    import time
    connector = MockConnector()
    order_mgr = MockOrderManager()
    config = SimpleNamespace(
        trade=SimpleNamespace(
            stagnation_exit_bars=5,
            stagnation_min_progress_r=0.4,
            break_even_r=0.8,
            partial_close=True,
            partial_r=1.0,
            partial_percent=50,
            trailing=TrailingMode.OFF,
        ),
        timeframes=SimpleNamespace(entry="H1"),
    )
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)

    # Buy opened 6 hours ago (6 * 3600 seconds)
    # Entry: 2000.0, SL: 1990.0 (risk = 10.0)
    # Current: 2002.0 (profit = 2.0 = 0.2R < 0.4R)
    six_hours_ago = time.time() - (6 * 3600)
    mock_pos = SimpleNamespace(
        ticket=1005,
        symbol="XAUUSD",
        type=0,  # BUY
        price_open=2000.0,
        price_current=2002.0,
        sl=1990.0,
        tp=2020.0,
        time=six_hours_ago,
    )

    result = pos_mgr.check_stagnation_exit(mock_pos)
    assert result is True
    assert len(order_mgr.closed) == 1
    assert order_mgr.closed[0][0] == 1005


def test_stagnation_exit_ignores_developing_trade():
    """Trade with profit >= min_progress_r must NOT trigger stagnation exit."""
    import time
    connector = MockConnector()
    order_mgr = MockOrderManager()
    config = SimpleNamespace(
        trade=SimpleNamespace(
            stagnation_exit_bars=5,
            stagnation_min_progress_r=0.4,
            break_even_r=0.8,
            partial_close=True,
            partial_r=1.0,
            partial_percent=50,
            trailing=TrailingMode.OFF,
        ),
        timeframes=SimpleNamespace(entry="H1"),
    )
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)

    six_hours_ago = time.time() - (6 * 3600)
    # Entry: 2000.0, SL: 1990.0 (risk = 10.0), Current: 2005.0 (profit = 5.0 = 0.5R >= 0.4R)
    mock_pos = SimpleNamespace(
        ticket=1006,
        symbol="XAUUSD",
        type=0,  # BUY
        price_open=2000.0,
        price_current=2005.0,
        sl=1990.0,
        tp=2020.0,
        time=six_hours_ago,
    )

    result = pos_mgr.check_stagnation_exit(mock_pos)
    assert result is False
    assert len(order_mgr.closed) == 0


def test_stagnation_exit_ignores_breakeven_trade():
    """Trade that has already secured Break-Even must NOT be closed by stagnation exit."""
    import time
    connector = MockConnector()
    order_mgr = MockOrderManager()
    config = SimpleNamespace(
        trade=SimpleNamespace(
            stagnation_exit_bars=5,
            stagnation_min_progress_r=0.4,
            break_even_r=0.8,
            partial_close=True,
            partial_r=1.0,
            partial_percent=50,
            trailing=TrailingMode.OFF,
        ),
        timeframes=SimpleNamespace(entry="H1"),
    )
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)
    # Pre-mark BE as set
    state = pos_mgr._get_trade_state(1007)
    state["be_set"] = True

    six_hours_ago = time.time() - (6 * 3600)
    mock_pos = SimpleNamespace(
        ticket=1007,
        symbol="XAUUSD",
        type=0,  # BUY
        price_open=2000.0,
        price_current=2001.0,
        sl=2000.1,  # BE
        tp=2020.0,
        time=six_hours_ago,
    )

    result = pos_mgr.check_stagnation_exit(mock_pos)
    assert result is False
    assert len(order_mgr.closed) == 0
