"""Synthetic Indices Risk Manager for Deriv MT5.

Handles:
- Dynamic lot sizing tailored to Deriv contract sizes & minimum volumes.
- Asymmetric risk-reward tracking.
- Post-spike profit locking and break-even logic.
- Account drawdown circuit breakers.
"""
from dataclasses import dataclass
import logging
import math
from typing import Optional, Dict, Any

from execution.deriv_connector import DerivConnector, DerivSymbolSpec

logger = logging.getLogger("SyntheticRiskManager")


@dataclass
class SyntheticRiskConfig:
    risk_mode: str = "percent"  # "percent" or "money"
    risk_percent: float = 0.75  # 0.75% per trade
    risk_money: float = 25.0    # Fallback $25 per trade
    max_daily_loss_pct: float = 5.0
    max_account_drawdown_pct: float = 12.0
    max_open_positions: int = 8
    max_positions_per_symbol: int = 4
    break_even_at_r: float = 1.0
    partial_take_at_r: float = 1.5
    partial_close_percent: float = 50.0
    spike_profit_lock_percent: float = 75.0  # Lock 75% of spike gain immediately


class SyntheticRiskManager:
    """
    Dedicated risk engine for Deriv Synthetic Indices.
    """

    def __init__(self, connector: DerivConnector, config: Optional[SyntheticRiskConfig] = None):
        self.connector = connector
        self.config = config or SyntheticRiskConfig()
        self.daily_starting_balance: float = 0.0
        self.peak_equity: float = 0.0

    def reset_day(self, starting_balance: float):
        """Set daily baseline."""
        self.daily_starting_balance = starting_balance
        if starting_balance > self.peak_equity:
            self.peak_equity = starting_balance

    def calculate_trade_risk(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        account_balance: float,
    ) -> Tuple[float, float]:
        """
        Calculate exact risk amount and compliant lot size for Deriv broker.
        Returns: (lot_size, actual_risk_money)
        """
        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 0:
            spec = self.connector.get_symbol_specs(symbol)
            return spec.min_volume, 0.0

        # Determine target risk money
        if self.config.risk_mode == "percent" and account_balance > 0:
            target_risk_money = account_balance * (self.config.risk_percent / 100.0)
        else:
            target_risk_money = self.config.risk_money

        spec = self.connector.get_symbol_specs(symbol)
        lot = self.connector.calculate_lot_size(symbol, target_risk_money, sl_distance)

        # Actual risk with the rounded/clamped lot
        contract_size = spec.contract_size if spec.contract_size > 0 else 1.0
        actual_risk = lot * sl_distance * contract_size

        return lot, round(actual_risk, 2)

    def can_open_trade(
        self,
        symbol: str,
        current_balance: float,
        current_equity: float,
        open_positions_count: int,
        symbol_positions_count: int,
    ) -> Tuple[bool, Optional[str]]:
        """Validate if a new trade complies with portfolio risk policies."""
        if open_positions_count >= self.config.max_open_positions:
            return False, f"Max open positions reached ({open_positions_count}/{self.config.max_open_positions})"

        if symbol_positions_count >= self.config.max_positions_per_symbol:
            return False, f"Max positions for {symbol} reached ({symbol_positions_count})"

        # Daily loss check
        if self.daily_starting_balance > 0:
            daily_loss = self.daily_starting_balance - current_equity
            daily_loss_pct = (daily_loss / self.daily_starting_balance) * 100.0
            if daily_loss_pct >= self.config.max_daily_loss_pct:
                return False, f"Daily loss limit breached: {daily_loss_pct:.2f}% >= {self.config.max_daily_loss_pct}%"

        # Account max drawdown check
        if self.peak_equity > 0:
            dd = self.peak_equity - current_equity
            dd_pct = (dd / self.peak_equity) * 100.0
            if dd_pct >= self.config.max_account_drawdown_pct:
                return False, f"Max drawdown limit breached: {dd_pct:.2f}% >= {self.config.max_account_drawdown_pct}%"

        return True, None
