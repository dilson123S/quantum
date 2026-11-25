from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Optional
from core.models import FilterResult
from core.config_loader import FilterConfig, SymbolConfig

class SessionFilter:
    """Filter to reject trades outside allowed trading hours."""
    
    def check(self, 
              current_time_utc: datetime, 
              symbol: str, 
              config: FilterConfig, 
              symbol_config: Optional[SymbolConfig] = None) -> FilterResult:
        """
        Check if the current time is within the allowed trading session.
        
        Args:
            current_time_utc: Current time in UTC.
            symbol: Symbol being traded.
            config: General filter configuration.
            symbol_config: Symbol-specific configuration (optional).
            
        Returns:
            FilterResult: Result containing approval status and reasons.
        """
        if not config.session_filter:
            return FilterResult(approved=True)

        start_str = config.trading_start
        end_str = config.trading_end
        
        if symbol_config:
            if symbol_config.session_start:
                start_str = symbol_config.session_start
            if symbol_config.session_end:
                end_str = symbol_config.session_end
        
        try:
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
        except ValueError:
            return FilterResult(approved=False, reasons=["Invalid time format in config. Expected HH:MM."])
            
        try:
            tz = ZoneInfo(config.timezone or "UTC")
        except Exception:
            return FilterResult(approved=False, reasons=[f"Invalid configured timezone '{config.timezone}'."])
        if current_time_utc.tzinfo is None:
            current_time_utc = current_time_utc.replace(tzinfo=ZoneInfo("UTC"))
        current_time = current_time_utc.astimezone(tz).time()
        
        if start_time <= end_time:
            # Session is within a single day (e.g., 07:00 to 17:00)
            if not (start_time <= current_time <= end_time):
                return FilterResult(approved=False, reasons=[f"Outside trading session {start_str}-{end_str} {config.timezone}"])
        else:
            # Session crosses midnight (e.g., 22:00 to 06:00)
            if not (current_time >= start_time or current_time <= end_time):
                return FilterResult(approved=False, reasons=[f"Outside trading session {start_str}-{end_str} {config.timezone}"])

        # Check excluded dead-volume hours (e.g. 09:00 - 12:59 UTC)
        excluded = getattr(symbol_config, "excluded_hours", None) or getattr(config, "excluded_hours", None)
        if excluded:
            if isinstance(excluded, str):
                excluded_set = {int(h.strip()) for h in excluded.split(",") if h.strip().isdigit()}
            else:
                excluded_set = {int(h) for h in excluded}
            if current_time.hour in excluded_set:
                return FilterResult(
                    approved=False,
                    reasons=[f"Hour {current_time.hour:02d}:00 is in excluded dead-volume hours {sorted(list(excluded_set))}"]
                )

        return FilterResult(approved=True)
