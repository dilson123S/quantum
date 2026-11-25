"""Data downloader for MetaTrader 5 OHLCV and tick data."""
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import time
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

# We import MT5 conditionally so the module can be tested without MT5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from core.constants import TIMEFRAME_MAP
from data.mt5_time import normalize_mt5_bar_times, to_mt5_query_time


class DataDownloader:
    """Downloads OHLCV data from MetaTrader 5."""
    
    OHLCV_COLUMNS = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']
    
    def __init__(self):
        if not MT5_AVAILABLE:
            logger.warning("MetaTrader5 not available. DataDownloader will not work.")
    
    def download_rates(
        self,
        symbol: str,
        timeframe: str,
        count: int = 500,
        closed_only: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Download the most recent N bars using copy_rates_from_pos.
        
        Args:
            symbol: Trading symbol (e.g. 'EURUSD')
            timeframe: Timeframe string (e.g. 'M15', 'H1', 'H4')
            count: Number of bars to download
            
        Returns:
            DataFrame with columns: time, open, high, low, close, tick_volume, spread, real_volume
            Time is timezone-aware UTC datetime. By default the active MT5
            candle is excluded so indicators never use mutable OHLC values.
        """
        tf_value = TIMEFRAME_MAP.get(timeframe)
        if tf_value is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None
        
        rates = None
        for attempt in range(3):
            rates = mt5.copy_rates_from_pos(symbol, tf_value, 1 if closed_only else 0, count)
            if rates is not None and len(rates) > 0:
                break
            err = mt5.last_error()
            if attempt < 2 and isinstance(err, (tuple, list)) and len(err) > 0 and err[0] == -10001:
                time.sleep(0.15 * (attempt + 1))
                continue
            break

        if rates is None or len(rates) == 0:
            logger.error(f"Failed to download rates for {symbol} {timeframe}: {mt5.last_error()}")
            return None
        
        df = pd.DataFrame(rates, columns=self.OHLCV_COLUMNS)
        account = mt5.account_info()
        server = getattr(account, "server", "") if account else ""
        df['time'] = normalize_mt5_bar_times(df['time'], server)
        df = df.set_index('time', drop=False)  # Keep time as column too
        
        logger.debug(f"Downloaded {len(df)} bars for {symbol} {timeframe}")
        return df
    
    def download_rates_range(self, symbol: str, timeframe: str, 
                              start: datetime, end: datetime) -> Optional[pd.DataFrame]:
        """Download bars in a date range using copy_rates_range."""
        tf_value = TIMEFRAME_MAP.get(timeframe)
        if tf_value is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None
        
        # Ensure timezone-aware UTC
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        
        account = mt5.account_info()
        server = getattr(account, "server", "") if account else ""
        query_start = to_mt5_query_time(start, server)
        query_end = to_mt5_query_time(end, server)
        rates = None
        for attempt in range(3):
            rates = mt5.copy_rates_range(symbol, tf_value, query_start, query_end)
            if rates is not None and len(rates) > 0:
                break
            err = mt5.last_error()
            if attempt < 2 and isinstance(err, (tuple, list)) and len(err) > 0 and err[0] == -10001:
                time.sleep(0.15 * (attempt + 1))
                continue
            break

        if rates is None or len(rates) == 0:
            logger.error(f"Failed to download rates range for {symbol} {timeframe}: {mt5.last_error()}")
            return None
        
        df = pd.DataFrame(rates, columns=self.OHLCV_COLUMNS)
        df['time'] = normalize_mt5_bar_times(df['time'], server)
        df = df[(df['time'] >= start) & (df['time'] <= end)]
        df = df.set_index('time', drop=False)
        
        logger.debug(f"Downloaded {len(df)} bars for {symbol} {timeframe} from {start} to {end} UTC")
        return df
    
    def download_ticks(self, symbol: str, start: datetime, count: int = 1000) -> Optional[pd.DataFrame]:
        """Download tick data using copy_ticks_from."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        
        ticks = mt5.copy_ticks_from(symbol, start, count, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            logger.error(f"Failed to download ticks for {symbol}: {mt5.last_error()}")
            return None
        
        df = pd.DataFrame(ticks)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        if 'time_msc' in df.columns:
            df['time_msc'] = pd.to_datetime(df['time_msc'], unit='ms', utc=True)
        
        logger.debug(f"Downloaded {len(df)} ticks for {symbol}")
        return df
    
    def download_multi_timeframe(self, symbol: str, timeframes: Dict[str, int]) -> Dict[str, Optional[pd.DataFrame]]:
        """Download data for multiple timeframes at once.
        
        Args:
            symbol: Trading symbol
            timeframes: Dict of {timeframe_name: bar_count}
                e.g. {'H4': 200, 'H1': 500, 'M15': 500}
        
        Returns:
            Dict of {timeframe_name: DataFrame}
        """
        result = {}
        for tf_name, count in timeframes.items():
            result[tf_name] = self.download_rates(symbol, tf_name, count)
        return result
