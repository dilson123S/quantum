"""Momentum indicators: RSI, MACD."""
import pandas as pd
import numpy as np


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index using Wilder's smoothing method."""
    delta = series.diff()
    
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    
    # Wilder's smoothing (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_values = 100 - (100 / (1 + rs))
    
    return rsi_values


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Calculate MACD, Signal line, and Histogram.
    
    Returns:
        DataFrame with columns: 'macd', 'signal', 'histogram'
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    
    return pd.DataFrame({
        'macd': macd_line,
        'signal': signal_line,
        'histogram': histogram,
    }, index=series.index)


def histogram_slope(histogram: pd.Series, period: int = 3) -> pd.Series:
    """Calculate slope direction of MACD histogram.
    
    Positive = histogram is growing (bullish momentum increasing)
    Negative = histogram is shrinking (bearish momentum increasing)
    """
    return histogram.diff(periods=period)


def calculate_all_momentum_indicators(df: pd.DataFrame,
                                       rsi_period: int = 14,
                                       macd_fast: int = 12,
                                       macd_slow: int = 26,
                                       macd_signal: int = 9) -> pd.DataFrame:
    """Calculate all momentum indicators and add them as columns.
    
    Adds columns: rsi, macd, macd_signal, macd_histogram, histogram_slope
    """
    result = df.copy()
    
    result['rsi'] = rsi(df['close'], rsi_period)
    
    macd_df = macd(df['close'], macd_fast, macd_slow, macd_signal)
    result['macd'] = macd_df['macd']
    result['macd_signal'] = macd_df['signal']
    result['macd_histogram'] = macd_df['histogram']
    result['histogram_slope'] = histogram_slope(macd_df['histogram'])
    
    return result
