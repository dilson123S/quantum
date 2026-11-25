"""Console ASCII dashboard with robust character encoding support."""
from typing import Optional, List, Dict
from datetime import datetime, timezone
import os
import sys
import logging

logger = logging.getLogger(__name__)


class Dashboard:
    """ASCII console dashboard showing bot status."""

    def __init__(self, bot_name: str = "QuantTradingBot"):
        self.bot_name = bot_name

    def render(
        self,
        status: str = "RUNNING",
        mode: str = "DEMO",
        balance: float = 0.0,
        equity: float = 0.0,
        drawdown_pct: float = 0.0,
        daily_pnl: float = 0.0,
        trades_today: int = 0,
        wins_today: int = 0,
        losses_today: int = 0,
        open_positions: int = 0,
        consecutive_losses: int = 0,
        symbols_active: Optional[List[str]] = None,
        last_signal: Optional[str] = None,
    ) -> str:
        """Render the dashboard as a formatted ASCII string and print it."""
        try:
            if symbols_active is None:
                symbols_active = []

            win_rate = (wins_today / trades_today * 100) if trades_today > 0 else 0.0
            now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

            width = 54
            border = '+' + '-' * width + '+'
            sep = '+' + '-' * width + '+'

            def line(text: str) -> str:
                return '| ' + text.ljust(width - 2) + ' |'

            def center_line(text: str) -> str:
                return '| ' + text.center(width - 2) + ' |'

            lines = [
                "",
                border,
                center_line(self.bot_name.upper()),
                center_line(now),
                sep,
                line(f"Status:              {status}"),
                line(f"Mode:                {mode}"),
                line(f"Balance:             ${balance:,.2f}"),
                line(f"Equity:              ${equity:,.2f}"),
                line(f"Drawdown:            {drawdown_pct:.1f}%"),
                line(f"Daily P&L:           ${daily_pnl:,.2f}"),
                sep,
                line(f"Trades Today:        {trades_today}"),
                line(f"Winners:             {wins_today}"),
                line(f"Losers:              {losses_today}"),
                line(f"Win Rate:            {win_rate:.1f}%"),
                line(f"Open Positions:      {open_positions}"),
                line(f"Consecutive Losses:  {consecutive_losses}"),
                sep,
                line(f"Symbols:             {', '.join(symbols_active) if symbols_active else 'None'}"),
                line(f"Last Signal:         {last_signal or 'None'}"),
                border,
                ""
            ]

            dashboard_str = '\n'.join(lines)
            print(dashboard_str)
            return dashboard_str

        except Exception as e:
            logger.error(f"Error rendering dashboard: {e}")
            return ""

    def render_score_card(
        self,
        symbol: str,
        direction: str,
        structure: float,
        trend: float,
        momentum: float,
        volume: float,
        volatility: float,
        total: float
    ) -> str:
        """Render a score breakdown card."""
        def bar(score: float, max_score: float) -> str:
            filled = int((score / max_score) * 15) if max_score > 0 else 0
            return '#' * filled + '-' * max(0, 15 - filled)

        card = f"""
+------------------------------------------+
| {symbol:7s} {direction:6s}      Score: {total:4.1f}/100    |
+------------------------------------------+
| Structure  [{bar(structure, 30)}] {structure:4.1f}/30  |
| Trend      [{bar(trend, 25)}] {trend:4.1f}/25  |
| Momentum   [{bar(momentum, 15)}] {momentum:4.1f}/15  |
| Volume     [{bar(volume, 15)}] {volume:4.1f}/15  |
| Volatility [{bar(volatility, 15)}] {volatility:4.1f}/15  |
+------------------------------------------+"""
        print(card)
        return card
