"""Unit tests for strategy scoring and ScoreEngine."""
import pytest
import pandas as pd
import numpy as np

from core.constants import Direction, SignalClassification
from core.models import StructureAnalysis, SwingPoint, SwingType, SwingClassification, StructureBreak, StructureBreakType
from core.config_loader import load_config
from strategy.structure_strategy import StructureStrategy
from strategy.trend_strategy import TrendStrategy
from strategy.momentum_strategy import MomentumStrategy
from strategy.volume_strategy import VolumeStrategy
from strategy.volatility_strategy import VolatilityStrategy
from strategy.scorer import ScoreEngine
from indicators.trend import calculate_all_trend_indicators
from indicators.momentum import calculate_all_momentum_indicators
from indicators.volatility import calculate_all_volatility_indicators
from indicators.volume import calculate_all_volume_indicators


@pytest.fixture
def bullish_setup_data():
    """Create data simulating a strong bullish setup."""
    n = 100
    prices = np.linspace(100, 150, n) + np.random.randn(n) * 0.2
    df = pd.DataFrame({
        'open': prices - 0.1,
        'high': prices + 0.5,
        'low': prices - 0.5,
        'close': prices,
        'tick_volume': [1500] * n,
        'real_volume': [1500] * n,
        'spread': [2] * n
    })
    df = calculate_all_trend_indicators(df)
    df = calculate_all_momentum_indicators(df)
    df = calculate_all_volatility_indicators(df)
    df = calculate_all_volume_indicators(df)

    # Mock structure
    swings = [
        SwingPoint(index=0, price=100.0, swing_type=SwingType.SWING_LOW, classification=SwingClassification.HL, bar_index=10),
        SwingPoint(index=1, price=120.0, swing_type=SwingType.SWING_HIGH, classification=SwingClassification.HH, bar_index=30),
        SwingPoint(index=2, price=115.0, swing_type=SwingType.SWING_LOW, classification=SwingClassification.HL, bar_index=50),
        SwingPoint(index=3, price=140.0, swing_type=SwingType.SWING_HIGH, classification=SwingClassification.HH, bar_index=70),
    ]
    bos = [
        StructureBreak(break_type=StructureBreakType.BOS, direction=Direction.BUY, price=120.0, bar_index=65, swing_broken=swings[1])
    ]
    analysis = StructureAnalysis(
        swings=swings,
        bos_list=bos,
        choch_list=[],
        last_swing_high=swings[3],
        last_swing_low=swings[2],
        structure_direction=Direction.BUY
    )

    return df, analysis


def test_individual_strategies(bullish_setup_data):
    df, analysis = bullish_setup_data

    struct_strat = StructureStrategy(max_score=30.0)
    score_struct = struct_strat.calculate_score(analysis, Direction.BUY)
    assert score_struct.score >= 15.0  # Should have strong structure score

    trend_strat = TrendStrategy(max_score=25.0)
    score_trend = trend_strat.calculate_score(df, Direction.BUY)
    assert score_trend.score > 0.0

    mom_strat = MomentumStrategy(max_score=15.0)
    score_mom = mom_strat.calculate_score(df, Direction.BUY)
    assert score_mom.score > 0.0

    vol_strat = VolumeStrategy(max_score=15.0)
    score_vol = vol_strat.calculate_score(df, Direction.BUY)
    assert score_vol.score > 0.0

    volat_strat = VolatilityStrategy(max_score=15.0)
    score_volat = volat_strat.calculate_score(df)
    assert score_volat.score > 0.0


def test_score_engine(bullish_setup_data):
    df, analysis = bullish_setup_data
    config = load_config("config")

    engine = ScoreEngine(config)
    score_result, signal = engine.calculate_score(
        symbol="EURUSD",
        timeframe="M15",
        df_entry=df,
        df_setup=df,
        df_htf=df,
        structure_analysis=analysis,
        config=config
    )

    assert score_result is not None
    assert score_result.total_score >= 0
    assert score_result.direction in (Direction.BUY, Direction.SELL)
