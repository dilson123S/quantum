"""Drawdown and loss circuit breaker tracking."""
from core.constants import DrawdownState


class DrawdownTracker:
    """Tracks account drawdown, daily PnL, and determines trading state."""

    def __init__(
        self,
        max_daily_loss_pct: float = 2.0,
        max_account_drawdown_pct: float = 10.0,
        max_consecutive_losses: int = 3
    ):
        self.starting_daily_balance = 0.0
        self.current_balance = 0.0
        self.current_equity = 0.0
        self.peak_equity = 0.0

        self.consecutive_losses = 0
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_account_drawdown_pct = max_account_drawdown_pct
        self.max_consecutive_losses = max_consecutive_losses

        self.state = DrawdownState.NORMAL

    def reset_day(self, starting_balance: float):
        """Update starting balance for a new day."""
        self.starting_daily_balance = starting_balance
        self.current_balance = starting_balance
        self.current_equity = starting_balance
        self.consecutive_losses = 0
        if starting_balance > self.peak_equity:
            self.peak_equity = starting_balance
        self.evaluate_state()

    def update(self, current_balance: float, current_equity: float) -> DrawdownState:
        """Update balance/equity and return current DrawdownState."""
        self.current_balance = current_balance
        self.current_equity = current_equity

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        self.evaluate_state()
        return self.state

    def record_trade_result(self, pnl: float):
        """Record trade profit/loss to update consecutive losses count."""
        if pnl < 0:
            self.consecutive_losses += 1
        elif pnl > 0:
            self.consecutive_losses = 0
        self.evaluate_state()

    @property
    def daily_pnl(self) -> float:
        return self.current_equity - self.starting_daily_balance

    @property
    def daily_loss_pct(self) -> float:
        if self.starting_daily_balance <= 0:
            return 0.0
        loss = self.starting_daily_balance - self.current_equity
        return max(0.0, (loss / self.starting_daily_balance) * 100.0)

    @property
    def current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        dd = self.peak_equity - self.current_equity
        return max(0.0, (dd / self.peak_equity) * 100.0)

    def evaluate_state(self) -> DrawdownState:
        """Evaluate state against configured thresholds."""
        if (
            self.current_drawdown_pct >= self.max_account_drawdown_pct
            or self.daily_loss_pct >= self.max_daily_loss_pct
            or (self.max_consecutive_losses > 0 and self.consecutive_losses >= self.max_consecutive_losses)
        ):
            self.state = DrawdownState.STOPPED
        elif (self.max_consecutive_losses > 0 and self.consecutive_losses >= 2) or self.daily_loss_pct >= (self.max_daily_loss_pct * 0.5):
            self.state = DrawdownState.REDUCED_RISK
        else:
            self.state = DrawdownState.NORMAL
        return self.state
