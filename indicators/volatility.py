"""Volatility indicators: ATR, ATR ratio, expansion/contraction."""
import pandas as pd
import numpy as np


def true_range(df: pd.DataFrame) -> pd.Series:
    """Calculate True Range."""
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range using Wilder's smoothing."""
    tr = true_range(df)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def atr_ratio(atr_series: pd.Series, avg_period: int = 50) -> pd.Series:
    """Calculate ATR ratio: current ATR / average ATR.
    
    Values:
    - < 1.0: Below average volatility (contraction)
    - 1.0: Average volatility
    - > 1.0: Above average volatility (expansion)
    """
    atr_avg = atr_series.rolling(window=avg_period).mean()
    return atr_series / atr_avg.replace(0, np.nan)


def bollinger_width_ratio(df: pd.DataFrame, period: int = 20, stddev: float = 2.0) -> tuple[pd.Series, pd.Series]:
    """Return normalized Bollinger width and its rolling relative width."""
    middle = df['close'].rolling(window=period).mean()
    deviation = df['close'].rolling(window=period).std(ddof=0)
    width = (2.0 * stddev * deviation) / middle.replace(0, np.nan)
    baseline = width.rolling(window=max(period * 2, 40)).mean()
    return width, width / baseline.replace(0, np.nan)


def is_expansion(ratio_value: float, min_ratio: float = 1.10, max_ratio: float = 1.50) -> bool:
    """Check if ATR ratio indicates a favorable expansion.
    
    The sweet spot: enough volatility for movement, not too much for chaos.
    """
    return min_ratio <= ratio_value <= max_ratio


def volatility_state(ratio_value: float, min_atr: float = 0.80, max_atr: float = 2.50) -> str:
    """Classify current volatility state.
    
    Returns:
        'DEAD' if ratio < min_atr (market too quiet)
        'LOW' if min_atr <= ratio < 1.0
        'NORMAL' if 1.0 <= ratio < 1.10
        'EXPANSION' if 1.10 <= ratio <= 1.50
        'HIGH' if 1.50 < ratio <= max_atr
        'EXTREME' if ratio > max_atr
    """
    if ratio_value < min_atr:
        return 'DEAD'
    elif ratio_value < 1.0:
        return 'LOW'
    elif ratio_value < 1.10:
        return 'NORMAL'
    elif ratio_value <= 1.50:
        return 'EXPANSION'
    elif ratio_value <= max_atr:
        return 'HIGH'
    else:
        return 'EXTREME'


def calculate_all_volatility_indicators(df: pd.DataFrame,
                                         atr_period: int = 14,
                                         atr_avg_period: int = 50) -> pd.DataFrame:
    """Calculate all volatility indicators and add them as columns.
    
    Adds columns: atr, atr_ratio, atr_avg
    """
    result = df.copy()
    
    result['atr'] = atr(df, atr_period)
    result['atr_avg'] = result['atr'].rolling(window=atr_avg_period).mean()
    result['atr_ratio'] = atr_ratio(result['atr'], atr_avg_period)
    result['bb_width'], result['bb_width_ratio'] = bollinger_width_ratio(result)
    
    return result
