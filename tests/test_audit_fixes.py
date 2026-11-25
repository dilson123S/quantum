"""Tests specifically verifying the deep code audit fixes."""
import pytest
import pandas as pd
import numpy as np
from types import SimpleNamespace
from sqlalchemy import text

from core.constants import Direction, SwingType, SwingClassification, StructureBreakType, TrailingMode
from core.models import SwingPoint, StructureBreak, StructureAnalysis
from core.config_loader import AppConfig, FilterConfig, SymbolConfig, VolatilityConfig
from execution.position_manager import PositionManager
from filters.correlation_filter import CorrelationFilter
from filters.spread_filter import SpreadFilter
from strategy.structure_strategy import StructureStrategy
from risk.stop_loss import StopLossCalculator
from database.database import Database


def test_position_manager_single_argument():
    """Verify manage_positions can be called safely with 1 argument."""
    pos_mgr = PositionManager(connector=None, order_manager=None, config=None)
    mock_pos = SimpleNamespace(
        ticket=12345,
        symbol="EURUSD",
        type=0,
        volume=0.1,
        price_open=1.1000,
        price_current=1.1050,
        sl=1.0950,
        tp=1.1150
    )
    # Should not raise TypeError: missing positional arguments
    pos_mgr.manage_positions([mock_pos])


def test_position_manager_trailing_stop_with_swing_point_object():
    """Verify trailing stop works with SwingPoint object without throwing TypeError."""
    class MockConnector:
        def get_symbol_info(self, symbol):
            return SimpleNamespace(digits=5, point=0.00001)

    class MockOrderManager:
        def __init__(self):
            self.modified = []
        def modify_sltp(self, ticket, sl, tp):
            self.modified.append((ticket, sl, tp))
            return SimpleNamespace(success=True)

    config = SimpleNamespace(
        trade=SimpleNamespace(
            trailing_mode="STRUCTURE",
            trailing_atr_multiplier=2.0
        )
    )

    connector = MockConnector()
    order_mgr = MockOrderManager()
    pos_mgr = PositionManager(connector=connector, order_manager=order_mgr, config=config)

    mock_pos = SimpleNamespace(
        ticket=999,
        symbol="EURUSD",
        type=0, # BUY
        price_open=1.1000,
        price_current=1.1080,
        sl=1.0950,
        tp=1.1200
    )

    swing_low = SwingPoint(
        index=0,
        price=1.1020,
        swing_type=SwingType.SWING_LOW,
        classification=SwingClassification.HL,
        bar_index=10
    )
    analysis = StructureAnalysis(
        swings=[swing_low],
        bos_list=[],
        choch_list=[],
        last_swing_high=None,
        last_swing_low=swing_low,
        structure_direction=Direction.BUY
    )

    df_entry = pd.DataFrame({"atr": [0.0015], "close": [1.1080]})

    result = pos_mgr.check_trailing_stop(mock_pos, df_entry, analysis)
    assert result is True
    assert len(order_mgr.modified) == 1
    assert order_mgr.modified[0][1] == 1.1020


def test_position_manager_honours_trailing_enum_value():
    """TrailingMode.HYBRID must not stringify into an unrecognised mode."""
    class MockConnector:
        def get_symbol_info(self, symbol):
            return SimpleNamespace(digits=5, point=0.00001)

    class MockOrderManager:
        def modify_sltp(self, ticket, sl, tp):
            return SimpleNamespace(success=True)

    config = SimpleNamespace(trade=SimpleNamespace(trailing=TrailingMode.ATR, trailing_atr_multiplier=2.0))
    position = SimpleNamespace(ticket=1, symbol="EURUSD", type=0, price_open=1.1, price_current=1.11, sl=1.09, tp=1.12)
    manager = PositionManager(MockConnector(), MockOrderManager(), config)
    assert manager.check_trailing_stop(position, pd.DataFrame({"atr": [0.001]})) is True


def test_correlation_filter_mt5_position_type():
    """Verify CorrelationFilter correctly detects MT5 namedtuple position types (0=BUY, 1=SELL)."""
    filter_obj = CorrelationFilter()
    
    # MT5 TradePosition object has `.type = 0` (BUY) and NO `.direction`
    mt5_pos = SimpleNamespace(symbol="EURUSD", type=0)
    
    # Same price movements -> 1.0 correlation
    df_eur = pd.DataFrame({"close": np.linspace(1.0, 2.0, 30)})
    df_gbp = pd.DataFrame({"close": np.linspace(1.0, 2.0, 30)})
    
    risk_cfg = SimpleNamespace(
        max_correlation=0.80,
        max_correlated_positions=1
    )

    res = filter_obj.check(
        new_symbol="GBPUSD",
        new_direction=Direction.BUY,
        open_positions=[mt5_pos],
        price_history_dict={"EURUSD": df_eur, "GBPUSD": df_gbp},
        config=risk_cfg
    )

    assert res.approved is False
    assert "Max highly correlated positions" in res.reasons[0]


def test_spread_filter_no_symbol_override():
    """Verify SpreadFilter does not treat config.max_spread=True as 1.0 point limit."""
    filter_obj = SpreadFilter()
    cfg = FilterConfig(max_spread=True, max_spread_atr_percent=50.0)

    # 15 points spread on EURUSD without symbol config should PASS with 50.0 default
    res = filter_obj.check(
        symbol="EURUSD",
        current_spread_points=15.0,
        atr_value=0.0015,
        point_size=0.00001,
        config=cfg,
        symbol_config=None
    )
    assert res.approved is True


def test_structure_strategy_penalty_applied():
    """Verify counter-trend BOS deducts points from the score."""
    strat = StructureStrategy(max_score=30.0)

    swing_low = SwingPoint(
        index=0,
        price=1.1000,
        swing_type=SwingType.SWING_LOW,
        classification=SwingClassification.HL,
        bar_index=5
    )
    # Opposing BOS (Direction SELL when we evaluate BUY)
    opposing_bos = StructureBreak(
        break_type=StructureBreakType.BOS,
        direction=Direction.SELL,
        price=1.0980,
        bar_index=15,
        swing_broken=swing_low
    )

    analysis = StructureAnalysis(
        swings=[swing_low],
        bos_list=[opposing_bos],
        choch_list=[],
        last_swing_high=None,
        last_swing_low=swing_low,
        structure_direction=Direction.SELL
    )

    # Base structure alignment + recent swing = 11 pts, with -5 penalty = 6 pts
    score_res = strat.calculate_score(analysis, Direction.BUY)
    assert score_res.details["counter_break_penalty"] is True
    assert score_res.score == 6.0


def test_stop_loss_geometric_validity():
    """Verify StopLossCalculator enforces valid SL relative to entry price."""
    sl_calc = StopLossCalculator()
    vol_cfg = VolatilityConfig(sl_multiplier=1.5)

    # BUY: Swing low corrupted to be ABOVE entry price
    corrupt_swing = SwingPoint(
        index=0,
        price=1.1200, # Higher than entry price!
        swing_type=SwingType.SWING_LOW,
        classification=SwingClassification.HL,
        bar_index=5
    )
    analysis = StructureAnalysis(
        swings=[corrupt_swing],
        bos_list=[],
        choch_list=[],
        last_swing_high=None,
        last_swing_low=corrupt_swing,
        structure_direction=Direction.BUY
    )

    sl_buy = sl_calc.calculate_sl(
        direction=Direction.BUY,
        entry_price=1.1000,
        structure_analysis=analysis,
        atr_value=0.0010,
        spread_price=0.0001,
        config=vol_cfg,
        symbol_point=0.00001
    )

    # SL must be strictly below entry price
    assert sl_buy < 1.1000


def test_sqlite_wal_mode_enabled(tmp_path):
    """Verify SQLite initializes in WAL mode."""
    db_file = tmp_path / "test_wal.db"
    db = Database(db_path=str(db_file))
    with db.session() as session:
        result = session.execute(text("PRAGMA journal_mode;")).scalar()
        assert str(result).upper() == "WAL"
