"""Volume indicators: Volume Ratio, OBV."""
import pandas as pd
import numpy as np


def volume_ratio(volume_series: pd.Series, period: int = 20) -> pd.Series:
    """Calculate Volume Ratio: current volume / SMA of volume.
    
    > 1.0: Above average volume
    > 1.2: Significant volume
    > 1.5: Strong volume
    """
    vol_avg = volume_series.rolling(window=period).mean()
    return volume_series / vol_avg.replace(0, np.nan)


def obv(df: pd.DataFrame) -> pd.Series:
    """Calculate On-Balance Volume.
    
    Accumulates volume on up-closes, subtracts on down-closes.
    """
    close = df['close']
    # Use tick_volume by default, fall back to real_volume
    volume = df.get('tick_volume', df.get('real_volume', pd.Series(0, index=df.index)))
    
    direction = pd.Series(0, index=df.index, dtype=float)
    direction[close > close.shift(1)] = 1.0
    direction[close < close.shift(1)] = -1.0
    
    return (direction * volume).cumsum()


def obv_trend(obv_series: pd.Series, period: int = 20) -> pd.Series:
    """Classify OBV trend based on its SMA.
    
    Returns:
        Series of strings: 'BULLISH', 'BEARISH', 'NEUTRAL'
    """
    obv_sma = obv_series.rolling(window=period).mean()
    
    result = pd.Series('NEUTRAL', index=obv_series.index)
    # Bullish: OBV above its SMA and rising
    rising = obv_series > obv_sma
    falling = obv_series < obv_sma
    
    result[rising] = 'BULLISH'
    result[falling] = 'BEARISH'
    return result


def volume_sma(volume_series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average of volume."""
    return volume_series.rolling(window=period).mean()


def calculate_all_volume_indicators(df: pd.DataFrame,
                                     volume_period: int = 20,
                                     volume_col: str = 'tick_volume',
                                     use_obv: bool = True) -> pd.DataFrame:
    """Calculate all volume indicators and add them as columns.
    
    Args:
        df: OHLCV DataFrame
        volume_period: Period for volume averages
        volume_col: Column name for volume data ('tick_volume' or 'real_volume')
        use_obv: Whether to calculate OBV
    
    Adds columns: volume_ratio, volume_sma, obv, obv_trend
    """
    result = df.copy()
    
    vol = df[volume_col] if volume_col in df.columns else df.get('tick_volume', pd.Series(0, index=df.index))
    
    result['volume_ratio'] = volume_ratio(vol, volume_period)
    result['volume_sma'] = volume_sma(vol, volume_period)
    
    if use_obv:
        result['obv'] = obv(df)
        result['obv_trend'] = obv_trend(result['obv'], volume_period)
    
    return result
