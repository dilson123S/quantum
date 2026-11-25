"""Timeframe resampling for OHLCV data."""
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Mapping from our timeframe strings to pandas resample rules
TF_TO_RESAMPLE = {
    'M1': '1min', 'M2': '2min', 'M3': '3min', 'M5': '5min',
    'M10': '10min', 'M15': '15min', 'M20': '20min', 'M30': '30min',
    'H1': '1h', 'H2': '2h', 'H4': '4h', 'H6': '6h', 'H8': '8h', 'H12': '12h',
    'D1': '1D', 'W1': '1W', 'MN1': '1ME',
}


class DataResampler:
    """Converts OHLCV data between timeframes."""
    
    def resample(self, df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
        """Resample OHLCV data to a higher timeframe.
        
        Args:
            df: DataFrame with OHLCV data (must have DatetimeIndex or 'time' column)
            target_tf: Target timeframe string (e.g. 'H4', 'D1')
            
        Returns:
            Resampled DataFrame
        """
        rule = TF_TO_RESAMPLE.get(target_tf)
        if rule is None:
            raise ValueError(f"Unsupported target timeframe: {target_tf}")
        
        # Ensure we have a DatetimeIndex
        work_df = df.copy()
        if not isinstance(work_df.index, pd.DatetimeIndex):
            if 'time' in work_df.columns:
                work_df = work_df.set_index('time')
            else:
                raise ValueError("DataFrame must have DatetimeIndex or 'time' column")
        
        # Resample with proper OHLCV aggregation
        agg_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
        }
        
        if 'tick_volume' in work_df.columns:
            agg_dict['tick_volume'] = 'sum'
        if 'real_volume' in work_df.columns:
            agg_dict['real_volume'] = 'sum'
        if 'spread' in work_df.columns:
            agg_dict['spread'] = 'max'  # worst spread in the period
        
        resampled = work_df.resample(rule).agg(agg_dict).dropna()
        resampled['time'] = resampled.index
        
        logger.debug(f"Resampled {len(df)} bars to {len(resampled)} bars ({target_tf})")
        return resampled
