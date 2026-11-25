"""Unit tests for indicator modules."""
import pytest
import numpy as np
import pandas as pd

from indicators.trend import (
    ema, sma, ema_slope, ema_alignment, adx, calculate_all_trend_indicators
)
from indicators.momentum import (
    rsi, macd, histogram_slope, calculate_all_momentum_indicators
)
from indicators.volatility import (
    true_range, atr, atr_ratio, is_expansion, volatility_state, calculate_all_volatility_indicators
)
from indicators.volume import (
    volume_ratio, obv, obv_trend, calculate_all_volume_indicators
)


@pytest.fixture
def sample_ohlcv_df():
    """Create a realistic synthetic OHLCV DataFrame."""
    np.random.seed(42)
    n = 150
    base_price = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    high = base_price + np.random.uniform(0.1, 1.0, n)
    low = base_price - np.random.uniform(0.1, 1.0, n)
    close = (high + low) / 2.0
    open_p = close + np.random.uniform(-0.3, 0.3, n)
    volume = np.random.randint(500, 2000, n)

    df = pd.DataFrame({
        'open': open_p,
        'high': high,
        'low': low,
        'close': close,
        'tick_volume': volume,
        'real_volume': volume,
        'spread': np.full(n, 2),
    })
    return df


def test_trend_indicators(sample_ohlcv_df):
    df = sample_ohlcv_df
    ema20 = ema(df['close'], 20)
    ema50 = ema(df['close'], 50)
    ema200 = ema(df['close'], 200)

    assert len(ema20) == len(df)
    assert not ema20.isna().all()

    # EMA Slope
    slope = ema_slope(ema50, period=5)
    assert len(slope) == len(df)

    # EMA Alignment
    align = ema_alignment(ema20, ema50, ema200)
    assert len(align) == len(df)
    assert set(align.unique()).issubset({'BULLISH', 'BEARISH', 'MIXED'})

    # ADX
    adx_df = adx(df, 14)
    assert 'adx' in adx_df.columns
    assert 'plus_di' in adx_df.columns
    assert 'minus_di' in adx_df.columns

    # Combined
    combined = calculate_all_trend_indicators(df)
    assert 'ema_fast' in combined.columns
    assert 'ema_medium' in combined.columns
    assert 'ema_slow' in combined.columns
    assert 'adx' in combined.columns


def test_momentum_indicators(sample_ohlcv_df):
    df = sample_ohlcv_df

    # RSI
    rsi_vals = rsi(df['close'], 14)
    assert len(rsi_vals) == len(df)
    valid_rsi = rsi_vals.dropna()
    assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()

    # MACD
    macd_df = macd(df['close'], 12, 26, 9)
    assert 'macd' in macd_df.columns
    assert 'signal' in macd_df.columns
    assert 'histogram' in macd_df.columns

    # Histogram slope
    h_slope = histogram_slope(macd_df['histogram'])
    assert len(h_slope) == len(df)

    # Combined
    combined = calculate_all_momentum_indicators(df)
    assert 'rsi' in combined.columns
    assert 'macd' in combined.columns
    assert 'macd_histogram' in combined.columns


def test_volatility_indicators(sample_ohlcv_df):
    df = sample_ohlcv_df

    # True range & ATR
    tr = true_range(df)
    assert (tr >= 0).all()

    atr_vals = atr(df, 14)
    assert len(atr_vals) == len(df)
    assert (atr_vals.dropna() > 0).all()

    # ATR Ratio
    ratio = atr_ratio(atr_vals, 50)
    assert len(ratio) == len(df)

    # Expansion test
    assert is_expansion(1.25, 1.10, 1.50) is True
    assert is_expansion(0.70, 1.10, 1.50) is False

    # Volatility State
    assert volatility_state(0.5) == 'DEAD'
    assert volatility_state(1.25) == 'EXPANSION'
    assert volatility_state(3.0) == 'EXTREME'

    # Combined
    combined = calculate_all_volatility_indicators(df)
    assert 'atr' in combined.columns
    assert 'atr_ratio' in combined.columns


def test_volume_indicators(sample_ohlcv_df):
    df = sample_ohlcv_df

    # Volume ratio
    vr = volume_ratio(df['tick_volume'], 20)
    assert len(vr) == len(df)

    # OBV & Trend
    obv_vals = obv(df)
    assert len(obv_vals) == len(df)

    obv_tr = obv_trend(obv_vals, 20)
    assert set(obv_tr.unique()).issubset({'BULLISH', 'BEARISH', 'NEUTRAL'})

    # Combined
    combined = calculate_all_volume_indicators(df)
    assert 'volume_ratio' in combined.columns
    assert 'obv' in combined.columns
    assert 'obv_trend' in combined.columns
