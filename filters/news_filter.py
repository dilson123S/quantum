import json
import logging
import os
import time
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from core.models import FilterResult
from core.config_loader import FilterConfig

logger = logging.getLogger(__name__)


class NewsProvider(ABC):
    """Abstract interface for fetching economic calendar events."""
    is_available: bool = False
    
    @abstractmethod
    def get_events(self, symbol: str, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """
        Get news events for a symbol within a timeframe.
        
        Args:
            symbol: Target symbol.
            start_time: Start of the query window.
            end_time: End of the query window.
            
        Returns:
            List of news events.
        """
        pass


class StubNewsProvider(NewsProvider):
    """Placeholder provider. It is explicitly unavailable, never 'no news'."""
    is_available = False
    
    def get_events(self, symbol: str, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        return []


class ForexFactoryNewsProvider(NewsProvider):
    """Live economic calendar provider powered by ForexFactory weekly feed with local disk caching."""
    is_available = True
    FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    def __init__(self, cache_file: str = "data/economic_calendar.json", cache_ttl_seconds: int = 7200):
        self.cache_file = Path(cache_file)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.last_fetch_time: float = 0.0
        self._cached_events: List[Dict[str, Any]] = []
        self._load_cache_from_disk()

    def _load_cache_from_disk(self) -> None:
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._cached_events = json.load(f)
                    self.last_fetch_time = self.cache_file.stat().st_mtime
                    logger.debug(f"Loaded {len(self._cached_events)} economic calendar events from disk cache: {self.cache_file}")
        except Exception as exc:
            logger.warning(f"Unable to load economic calendar cache from disk: {exc}")

    def _save_cache_to_disk(self, data: List[Dict[str, Any]]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning(f"Unable to save economic calendar cache to disk: {exc}")

    def fetch_events(self, force: bool = False) -> List[Dict[str, Any]]:
        """Fetch weekly calendar events from ForexFactory or return cached version."""
        now_ts = time.time()
        if not force and self._cached_events and (now_ts - self.last_fetch_time < self.cache_ttl_seconds):
            return self._cached_events

        try:
            req = urllib.request.Request(
                self.FEED_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuantumBot/2.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list) and data:
                    self._cached_events = data
                    self.last_fetch_time = now_ts
                    self._save_cache_to_disk(data)
                    logger.info(f"Updated economic calendar ({len(data)} events from ForexFactory)")
                    return self._cached_events
        except Exception as exc:
            logger.warning(f"Could not fetch fresh ForexFactory economic calendar: {exc}")
            if self._cached_events:
                logger.info("Using previously cached economic calendar events.")
                return self._cached_events

        return self._cached_events

    def get_events(self, symbol: str, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """Get high-impact news events for symbol within query window [start_time, end_time]."""
        events = self.fetch_events()
        if not events:
            return []

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        else:
            start_time = start_time.astimezone(timezone.utc)
            
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        else:
            end_time = end_time.astimezone(timezone.utc)

        sym = symbol.upper()
        currencies = {"USD"}
        if "EUR" in sym:
            currencies.add("EUR")
        if "GBP" in sym:
            currencies.add("GBP")

        matching = []
        for e in events:
            country = str(e.get("country", "")).upper()
            if country not in currencies:
                continue

            impact = str(e.get("impact", "")).capitalize()
            if impact not in ("High", "Holiday"):
                continue

            raw_date = e.get("date")
            if not raw_date:
                continue

            try:
                dt = datetime.fromisoformat(raw_date)
                dt_utc = dt.astimezone(timezone.utc)
                if start_time <= dt_utc <= end_time:
                    matching.append({
                        "title": e.get("title", "Economic Event"),
                        "country": country,
                        "impact": impact,
                        "forecast": e.get("forecast", ""),
                        "previous": e.get("previous", ""),
                        "date_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        "timestamp_utc": dt_utc.isoformat(),
                        "time_utc": dt_utc,
                    })
            except Exception:
                continue

        return matching

    def get_upcoming_events(self, symbol: str = "XAUUSD", hours_ahead: int = 24) -> List[Dict[str, Any]]:
        """Get upcoming high-impact events for the next N hours."""
        now_utc = datetime.now(timezone.utc)
        end_utc = now_utc + timedelta(hours=hours_ahead)
        return self.get_events(symbol, now_utc, end_utc)


class NewsFilter:
    """Filter to reject trades during high-impact news events."""
    
    def __init__(self, provider: Optional[NewsProvider] = None):
        self.provider = provider if provider is not None else ForexFactoryNewsProvider()

    def is_news_blackout(self, current_time_utc: datetime, symbol: str, config: FilterConfig) -> FilterResult:
        """
        Check if we are currently in a news blackout window.
        
        Args:
            current_time_utc: Current time in UTC.
            symbol: Target symbol.
            config: Filter configuration.
            
        Returns:
            FilterResult: Result containing approval status and reasons.
        """
        if not config.news_filter:
            return FilterResult(approved=True)

        # An enabled risk control must fail closed. A stub, expired API
        # credential, timeout, or malformed response cannot be interpreted as
        # an empty economic calendar.
        if not getattr(self.provider, "is_available", False):
            return FilterResult(
                approved=False,
                reasons=["News filter is enabled but no reliable news provider is available."],
            )

        if current_time_utc.tzinfo is None:
            current_time_utc = current_time_utc.replace(tzinfo=timezone.utc)
        else:
            current_time_utc = current_time_utc.astimezone(timezone.utc)
            
        start_time = current_time_utc - timedelta(minutes=config.news_blackout_after)
        end_time = current_time_utc + timedelta(minutes=config.news_blackout_before)
        
        try:
            events = self.provider.get_events(symbol, start_time, end_time)
        except Exception as exc:
            return FilterResult(
                approved=False,
                reasons=[f"News provider failed while the news filter is enabled: {exc}"],
            )
        if events is None:
            return FilterResult(
                approved=False,
                reasons=["News provider returned no usable response while the filter is enabled."],
            )
        if events:
            event_summaries = [f"{e.get('title', 'Noticia')} ({e.get('impact', 'High')}) a las {e.get('date_utc', '')} UTC" for e in events]
            reasons = [f"Ventana de bloqueo por noticia activa para {symbol}: {', '.join(event_summaries)}"]
            return FilterResult(approved=False, reasons=reasons)
            
        return FilterResult(approved=True)
