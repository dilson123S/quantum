"""Unit tests for Risk Engine modules."""
import pytest
from core.constants import Direction, SignalClassification, DrawdownState, RiskMode
from core.models import StructureAnalysis, SwingPoint, SwingType, SwingClassification
from core.config_loader import RiskConfig, VolatilityConfig, TradeConfig
from risk.position_size import PositionSizeCalculator
from risk.stop_loss import StopLossCalculator
from risk.take_profit import TakeProfitCalculator
from risk.drawdown import DrawdownTracker
from risk.exposure import ExposureManager


def test_position_size_calculator():
    calc = PositionSizeCalculator()

    # 1. Test lot calculation: $1,000 risk, 100 points (10 pips), $10 per point for 1 lot
    lot = calc.calculate_lot(
        symbol="EURUSD",
        risk_money=1000.0,
        sl_distance_points=100.0,
        tick_value=1.0,
        tick_size=0.00001,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01
    )
    assert lot > 0
    assert lot <= 100.0
    assert round(lot, 2) == lot

    # 2. Test risk money calculation with consecutive loss reduction
    risk_cfg = RiskConfig(
        mode=RiskMode.PERCENT,
        risk_percent=1.0,
        risk_money=20.0,
        max_daily_loss=2.0,
        max_drawdown=10.0,
        max_consecutive_losses=3,
        max_open_positions=3,
        max_positions_per_symbol=1,
        max_correlated_positions=2,
        max_risk_per_trade=1.0,
        max_correlation=0.80
    )

    # 0 losses -> 100% of 1% on $10,000 = $100
    risk_m = calc.calculate_risk_money(
        balance=10000.0,
        risk_config=risk_cfg,
        score_classification=SignalClassification.EXCELLENT,
        consecutive_losses=0
    )
    assert risk_m == 100.0

    # 2 losses -> 75% of 1% on $10,000 = $75
    risk_m2 = calc.calculate_risk_money(
        balance=10000.0,
        risk_config=risk_cfg,
        score_classification=SignalClassification.EXCELLENT,
        consecutive_losses=2
    )
    assert risk_m2 == 75.0


def test_position_size_refuses_zero_or_unaffordable_risk():
    calc = PositionSizeCalculator()
    common = dict(
        symbol="EURUSD",
        sl_distance_points=100.0,
        tick_value=1.0,
        tick_size=0.00001,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    assert calc.calculate_lot(risk_money=0.0, **common) == 0.0
    # The broker minimum would risk $1.00 here, above the $0.50 budget.
    assert calc.calculate_lot(risk_money=0.50, **common) == 0.0


def test_position_size_elevates_to_min_trade_volume():
    calc = PositionSizeCalculator()
    common = dict(
        symbol="XAUUSD",
        sl_distance_points=12727.0,
        tick_value=1.0,
        tick_size=0.01,
        point=0.01,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    # Raw calculation yields 0.0146 -> quantized to 0.01
    lot_standard = calc.calculate_lot(risk_money=186.0, **common)
    assert lot_standard == 0.01

    # With min_trade_volume=0.02, it elevates to 0.02 so that 50% partial (0.01) is possible
    lot_elevated = calc.calculate_lot(risk_money=186.0, min_trade_volume=0.02, **common)
    assert lot_elevated == 0.02


def test_stop_loss_calculator():
    calc = StopLossCalculator()
    vol_cfg = VolatilityConfig(
        atr_period=14,
        atr_avg_period=50,
        min_atr_ratio=0.8,
        max_atr_ratio=2.5,
        sl_multiplier=1.5
    )

    swings = [
        SwingPoint(index=0, price=1.0800, swing_type=SwingType.SWING_LOW, classification=SwingClassification.HL, bar_index=10)
    ]
    analysis = StructureAnalysis(swings=swings, last_swing_low=swings[0], last_swing_high=None)

    sl_buy = calc.calculate_sl(
        direction=Direction.BUY,
        entry_price=1.0850,
        structure_analysis=analysis,
        atr_value=0.0020,
        spread_price=0.0001,
        config=vol_cfg,
        symbol_point=0.00001
    )
    assert sl_buy < 1.0850
    assert sl_buy > 0.0


def test_take_profit_calculator():
    calc = TakeProfitCalculator()
    trade_cfg = TradeConfig(
        risk_reward=2.0,
        break_even_r=1.0,
        partial_close=True,
        partial_r=1.0,
        partial_percent=50,
        trailing="hybrid",
        entry_mode="breakout"
    )

    tp_buy = calc.calculate_tp(
        direction=Direction.BUY,
        entry_price=1.0850,
        sl_price=1.0800,  # 50 pips risk
        structure_analysis=StructureAnalysis(),
        atr_value=0.0020,
        config=trade_cfg
    )
    assert tp_buy > 1.0850


def test_drawdown_tracker():
    tracker = DrawdownTracker(max_daily_loss_pct=2.0, max_account_drawdown_pct=10.0, max_consecutive_losses=3)
    tracker.reset_day(starting_balance=10000.0)

    state = tracker.update(current_balance=10000.0, current_equity=10200.0)
    assert state == DrawdownState.NORMAL

    # Loss exceeds daily limit
    state_loss = tracker.update(current_balance=9750.0, current_equity=9750.0)  # -2.5% loss
    assert state_loss == DrawdownState.STOPPED


def test_exposure_manager():
    mgr = ExposureManager(max_open_positions=3, max_positions_per_symbol=1)
    assert mgr.can_open_position(symbol="EURUSD", current_open_symbols=["GBPUSD"]) is True
    assert mgr.can_open_position(symbol="GBPUSD", current_open_symbols=["GBPUSD"]) is False
    assert mgr.can_open_position(symbol="USDJPY", current_open_symbols=["EURUSD", "GBPUSD", "AUDUSD"]) is False


def test_get_consecutive_losses_isolated_by_symbol(tmp_path):
    from database.database import Database
    from database.repository import TradeRepository
    from database.models import TradeDB
    from datetime import datetime, timezone, timedelta
    from core.constants import TradeState

    db_file = tmp_path / "test_trades.db"
    db = Database(str(db_file))
    db.create_tables()
    repo = TradeRepository(db)

    # Insert historical trades with different exit times
    now = datetime.now(timezone.utc)
    with db.session() as s:
        # XAUUSD: win, then loss (consecutive = 1)
        s.add(TradeDB(signal_id="s1", symbol="XAUUSD", timeframe="H1", direction="BUY",
                      entry_price=2000, sl_price=1990, tp_price=2020, volume=0.01,
                      total_score=80, structure_score=20, trend_score=20, momentum_score=10,
                      volume_score=10, volatility_score=10, risk_percent=1, risk_money=50,
                      state=TradeState.CLOSED.value, profit=50.0,
                      exited_at=now - timedelta(hours=3)))
        s.add(TradeDB(signal_id="s2", symbol="XAUUSD", timeframe="H1", direction="SELL",
                      entry_price=2000, sl_price=2010, tp_price=1980, volume=0.01,
                      total_score=80, structure_score=20, trend_score=20, momentum_score=10,
                      volume_score=10, volatility_score=10, risk_percent=1, risk_money=50,
                      state=TradeState.CLOSED.value, profit=-30.0,
                      exited_at=now - timedelta(hours=2)))
        # BTCUSD: win, then loss (consecutive = 1)
        s.add(TradeDB(signal_id="s3", symbol="BTCUSD", timeframe="H1", direction="BUY",
                      entry_price=60000, sl_price=59000, tp_price=62000, volume=0.01,
                      total_score=80, structure_score=20, trend_score=20, momentum_score=10,
                      volume_score=10, volatility_score=10, risk_percent=1, risk_money=50,
                      state=TradeState.CLOSED.value, profit=100.0,
                      exited_at=now - timedelta(hours=2, minutes=30)))
        s.add(TradeDB(signal_id="s4", symbol="BTCUSD", timeframe="H1", direction="SELL",
                      entry_price=60000, sl_price=61000, tp_price=58000, volume=0.01,
                      total_score=80, structure_score=20, trend_score=20, momentum_score=10,
                      volume_score=10, volatility_score=10, risk_percent=1, risk_money=50,
                      state=TradeState.CLOSED.value, profit=-40.0,
                      exited_at=now - timedelta(hours=1)))
        # Purged crypto (SOLUSD) with recent loss: should be ignored globally and not poison symbols
        s.add(TradeDB(signal_id="s5", symbol="SOLUSD", timeframe="H1", direction="SELL",
                      entry_price=100, sl_price=105, tp_price=90, volume=1.0,
                      total_score=80, structure_score=20, trend_score=20, momentum_score=10,
                      volume_score=10, volatility_score=10, risk_percent=1, risk_money=50,
                      state=TradeState.CLOSED.value, profit=-80.0,
                      exited_at=now))

    # Symbol-isolated checks
    assert repo.get_consecutive_losses("XAUUSD") == 1
    assert repo.get_consecutive_losses("BTCUSD") == 1

    # Global check excludes SOLUSD (only counts active BTCUSD & XAUUSD losses = 2)
    assert repo.get_consecutive_losses() == 2

