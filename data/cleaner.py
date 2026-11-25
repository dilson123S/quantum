"""Data cleaning and validation for OHLCV data."""
import pandas as pd
import numpy as np
from typing import Tuple
import logging

logger = logging.getLogger(__name__)


class DataCleaner:
    """Validates and cleans OHLCV data."""
    
    def __init__(self, max_frozen_bars: int = 10):
        self.max_frozen_bars = max_frozen_bars
    
    def clean(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        """Clean and validate OHLCV data.
        
        Returns:
            Tuple of (cleaned_df, quality_report)
        """
        report = {
            'original_rows': len(df),
            'nan_rows_removed': 0,
            'invalid_price_rows': 0,
            'invalid_ohlc_rows': 0,
            'duplicate_time_rows': 0,
            'out_of_order_rows': 0,
            'negative_volume_rows': 0,
            'frozen_detected': False,
            'gaps_detected': 0,
            'quality_ok': True,
        }
        
        # MT5 downloader keeps ``time`` both as index and column.  Work on a
        # neutral index so every downstream sort is unambiguous.
        df = df.copy().reset_index(drop=True)
        if 'time' in df.columns:
            original_time = df['time']
            report['out_of_order_rows'] = int((original_time.diff().dropna() < pd.Timedelta(0)).sum())
            report['duplicate_time_rows'] = int(df.duplicated(subset=['time']).sum())
            df = df.sort_values('time').drop_duplicates(subset=['time'], keep='last').reset_index(drop=True)
        
        # Remove NaN rows
        nan_mask = df[['open', 'high', 'low', 'close']].isna().any(axis=1)
        report['nan_rows_removed'] = int(nan_mask.sum())
        df = df[~nan_mask]
        
        # Validate prices > 0
        invalid_price = (df[['open', 'high', 'low', 'close']] <= 0).any(axis=1)
        report['invalid_price_rows'] = int(invalid_price.sum())
        df = df[~invalid_price]
        
        # Validate OHLC consistency: high >= low, high >= open/close, low <= open/close  
        ohlc_invalid = (
            (df['high'] < df['low']) |
            (df['high'] < df[['open', 'close']].max(axis=1)) |
            (df['low'] > df[['open', 'close']].min(axis=1))
        )
        report['invalid_ohlc_rows'] = int(ohlc_invalid.sum())
        df = df[~ohlc_invalid]
        
        # Check negative volume
        if 'tick_volume' in df.columns:
            neg_vol = df['tick_volume'] < 0
            report['negative_volume_rows'] = int(neg_vol.sum())
            df.loc[neg_vol, 'tick_volume'] = 0
        
        # Detect frozen data (N consecutive identical closes)
        report['frozen_detected'] = self._detect_frozen(df)
        
        # Detect gaps
        report['gaps_detected'] = self._detect_gaps(df)
        
        report['final_rows'] = len(df)
        report['quality_ok'] = (
            report['nan_rows_removed'] == 0 
            and report['invalid_price_rows'] == 0
            and report['invalid_ohlc_rows'] == 0
            and report['duplicate_time_rows'] == 0
            and report['out_of_order_rows'] == 0
            and not report['frozen_detected']
        )
        
        if not report['quality_ok']:
            logger.warning(f"Data quality issues: {report}")
        
        return df, report
    
    def _detect_frozen(self, df: pd.DataFrame) -> bool:
        """Detect frozen data: N consecutive bars with identical close prices."""
        if len(df) < self.max_frozen_bars:
            return False
        
        close = df['close'].values
        count = 1
        for i in range(1, len(close)):
            if close[i] == close[i-1]:
                count += 1
                if count >= self.max_frozen_bars:
                    logger.warning(f"Frozen data detected: {count} consecutive identical closes")
                    return True
            else:
                count = 1
        return False
    
    def _detect_gaps(self, df: pd.DataFrame) -> int:
        """Detect time gaps in the data (excluding weekends)."""
        if len(df) < 2 or 'time' not in df.columns:
            return 0
        
        time_diff = df['time'].diff()
        if time_diff.empty:
            return 0
        
        median_diff = time_diff.median()
        if pd.isna(median_diff):
            return 0
        
        # A gap is > 3x the median time difference (excludes weekends naturally for daily+)
        gaps = (time_diff > median_diff * 3).sum()
        
        if gaps > 0:
            logger.debug(f"Detected {gaps} time gaps in data")
        
        return int(gaps)
    
    def validate_minimum_bars(self, df: pd.DataFrame, minimum: int = 200) -> bool:
        """Check if we have enough bars for indicator calculation."""
        if df is None or len(df) < minimum:
            logger.error(f"Insufficient data: {len(df) if df is not None else 0} bars, need {minimum}")
            return False
        return True
