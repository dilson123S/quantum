"""Composite context filter orchestrating all filters."""
from typing import List, Dict, Any, Optional
import pandas as pd
from datetime import datetime

from core.models import FilterResult
from core.constants import Direction
from core.config_loader import AppConfig, SymbolConfig

from .spread_filter import SpreadFilter
from .session_filter import SessionFilter
from .news_filter import NewsFilter, StubNewsProvider
from .drawdown_filter import DrawdownFilter
from .correlation_filter import CorrelationFilter


class ContextFilter:
    """Composite filter running all enabled sub-filters."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config
        self.spread_filter = SpreadFilter()
        self.session_filter = SessionFilter()
        self.news_filter = NewsFilter()
        self.drawdown_filter = DrawdownFilter()
        self.correlation_filter = CorrelationFilter()

    def check_trade_context(
        self,
        symbol: str,
        direction: Direction,
        current_time_utc: datetime,
        current_spread_points: float,
        atr_value: float,
        point_size: float,
        current_equity: float,
        current_balance: float,
        daily_pnl: float,
        consecutive_losses: int,
        open_positions: List[Any],
        price_history_dict: Dict[str, pd.DataFrame],
        config: Optional[AppConfig] = None,
        symbol_config: Optional[SymbolConfig] = None
    ) -> FilterResult:
        """Run all applicable filters to determine if a trade should be allowed."""
        cfg = config or self.config
        if cfg is None:
            raise ValueError("AppConfig must be provided to ContextFilter")

        reasons = []
        approved = True
        sym_cfg = symbol_config or cfg.symbols.get(symbol)

        # 1. Session Filter
        if cfg.filters.session_filter:
            res = self.session_filter.check(current_time_utc, symbol, cfg.filters, sym_cfg)
            if not res.approved:
                approved = False
                reasons.extend(res.reasons)

        # 2. Spread Filter
        if cfg.filters.max_spread:
            res = self.spread_filter.check(symbol, current_spread_points, atr_value, point_size, cfg.filters, sym_cfg)
            if not res.approved:
                approved = False
                reasons.extend(res.reasons)

        # 3. News Filter
        if cfg.filters.news_filter:
            res = self.news_filter.is_news_blackout(current_time_utc, symbol, cfg.filters)
            if not res.approved:
                approved = False
                reasons.extend(res.reasons)

        # 4. Drawdown Filter
        res = self.drawdown_filter.check(current_equity, current_balance, daily_pnl, consecutive_losses, cfg.risk)
        if not res.approved:
            approved = False
            reasons.extend(res.reasons)

        # 5. Hard position-count limits.  These are deliberately evaluated
        # here, before the correlation check, and are repeated in the final
        # execution guard to protect against a race between symbols.
        open_symbols = [getattr(pos, "symbol", None) for pos in open_positions]
        open_symbols = [item for item in open_symbols if item]
        if len(open_symbols) >= cfg.risk.max_open_positions:
            approved = False
            reasons.append(
                f"Maximum open bot positions ({cfg.risk.max_open_positions}) reached."
            )
        if open_symbols.count(symbol) >= cfg.risk.max_positions_per_symbol:
            approved = False
            reasons.append(
                f"Maximum positions for {symbol} ({cfg.risk.max_positions_per_symbol}) reached."
            )

        # 6. Correlation Filter
        if cfg.filters.correlation_filter and open_positions:
            res = self.correlation_filter.check(symbol, direction, open_positions, price_history_dict, cfg.risk)
            if not res.approved:
                approved = False
                reasons.extend(res.reasons)

        return FilterResult(approved=approved, reasons=reasons)

    def check_all(self, *args, **kwargs) -> FilterResult:
        """Alias for check_trade_context."""
        return self.check_trade_context(*args, **kwargs)
