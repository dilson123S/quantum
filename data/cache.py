"""In-memory data cache with TTL."""
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class DataCache:
    """Caches DataFrames in memory with time-to-live (TTL) per entry."""
    
    def __init__(self, default_ttl_seconds: int = 60):
        self._cache: dict[str, Tuple[pd.DataFrame, datetime]] = {}
        self.default_ttl = timedelta(seconds=default_ttl_seconds)
    
    def _make_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"
    
    def get(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Get cached data if not expired."""
        key = self._make_key(symbol, timeframe)
        if key not in self._cache:
            return None
        
        df, cached_at = self._cache[key]
        now = datetime.now(timezone.utc)
        
        if now - cached_at > self.default_ttl:
            logger.debug(f"Cache expired for {key}")
            del self._cache[key]
            return None
        
        logger.debug(f"Cache hit for {key}")
        return df.copy()
    
    def set(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        """Store data in cache."""
        key = self._make_key(symbol, timeframe)
        self._cache[key] = (df.copy(), datetime.now(timezone.utc))
        logger.debug(f"Cached {len(df)} rows for {key}")
    
    def invalidate(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> None:
        """Invalidate cache entries.
        
        If both symbol and timeframe given, invalidate specific entry.
        If only symbol, invalidate all timeframes for that symbol.
        If neither, clear all cache.
        """
        if symbol is None and timeframe is None:
            self._cache.clear()
            logger.debug("Cache cleared entirely")
            return
        
        if symbol and timeframe:
            key = self._make_key(symbol, timeframe)
            self._cache.pop(key, None)
            return
        
        # Partial match
        prefix = f"{symbol}_" if symbol else ""
        suffix = f"_{timeframe}" if timeframe else ""
        keys_to_remove = [
            k for k in self._cache 
            if (prefix and k.startswith(prefix)) or (suffix and k.endswith(suffix))
        ]
        for k in keys_to_remove:
            del self._cache[k]
    
    def invalidate_all(self) -> None:
        """Clear all cached data. Call at the start of each analysis cycle."""
        self._cache.clear()
