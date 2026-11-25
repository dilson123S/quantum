"""Trend indicators: EMA, ADX, slope analysis."""
import pandas as pd
import numpy as np
from typing import Tuple


def ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return series.rolling(window=period).mean()


def ema_slope(ema_series: pd.Series, period: int = 5) -> pd.Series:
    """Calculate normalized slope of EMA over the last N periods.
    
    Returns the percentage change of EMA over the period.
    Positive = upward slope, Negative = downward slope.
    """
    return ema_series.pct_change(periods=period) * 100


def ema_alignment(ema_fast: pd.Series, ema_medium: pd.Series, ema_slow: pd.Series) -> pd.Series:
    """Classify EMA alignment at each bar.
    
    Returns:
        Series of strings: 'BULLISH' (fast>med>slow), 'BEARISH' (fast<med<slow), 'MIXED'
    """
    bullish = (ema_fast > ema_medium) & (ema_medium > ema_slow)
    bearish = (ema_fast < ema_medium) & (ema_medium < ema_slow)
    
    result = pd.Series('MIXED', index=ema_fast.index)
    result[bullish] = 'BULLISH'
    result[bearish] = 'BEARISH'
    return result


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Calculate Average Directional Index (ADX) with +DI and -DI.
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ADX period (default 14)
        
    Returns:
        DataFrame with columns: 'adx', 'plus_di', 'minus_di'
    """
    high = df['high']
    low = df['low']
    close = df['close']
    
    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
    
    # Smoothed with Wilder's method (equivalent to EMA with alpha=1/period)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    
    # DX and ADX
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_values = dx.ewm(alpha=1/period, adjust=False).mean()
    
    result = pd.DataFrame({
        'adx': adx_values,
        'plus_di': plus_di,
        'minus_di': minus_di,
    }, index=df.index)
    
    return result


def calculate_all_trend_indicators(df: pd.DataFrame, 
                                    ema_fast_period: int = 20,
                                    ema_medium_period: int = 50,
                                    ema_slow_period: int = 200,
                                    adx_period: int = 14,
                                    slope_period: int = 5) -> pd.DataFrame:
    """Calculate all trend indicators and add them as columns to the DataFrame.
    
    Adds columns: ema_fast, ema_medium, ema_slow, ema_alignment, 
                  ema_fast_slope, ema_medium_slope, adx, plus_di, minus_di
    """
    result = df.copy()
    
    result['ema_fast'] = ema(df['close'], ema_fast_period)
    result['ema_medium'] = ema(df['close'], ema_medium_period)
    result['ema_slow'] = ema(df['close'], ema_slow_period)
    result['ema_alignment'] = ema_alignment(result['ema_fast'], result['ema_medium'], result['ema_slow'])
    result['ema_fast_slope'] = ema_slope(result['ema_fast'], slope_period)
    result['ema_medium_slope'] = ema_slope(result['ema_medium'], slope_period)
    
    adx_df = adx(df, adx_period)
    result['adx'] = adx_df['adx']
    result['plus_di'] = adx_df['plus_di']
    result['minus_di'] = adx_df['minus_di']
    
    return result
