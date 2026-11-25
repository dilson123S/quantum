from core.models import FilterResult
from core.config_loader import RiskConfig

class DrawdownFilter:
    """Filter to enforce drawdown and loss limits."""
    
    def check(self, 
              current_equity: float, 
              current_balance: float, 
              daily_pnl: float, 
              consecutive_losses: int, 
              config: RiskConfig) -> FilterResult:
        """
        Check if account satisfies risk constraints.
        
        Args:
            current_equity: Current account equity.
            current_balance: Current account balance.
            daily_pnl: Realized and floating profit/loss for the day.
            consecutive_losses: Number of consecutive losses.
            config: Risk configuration.
            
        Returns:
            FilterResult: Result containing approval status and reasons.
        """
        reasons = []
        approved = True
        
        if current_balance > 0:
            # 1. Daily loss percent
            daily_loss_pct = (daily_pnl / current_balance) * 100.0
            if daily_loss_pct < -config.max_daily_loss:
                approved = False
                reasons.append(f"Daily loss {-daily_loss_pct:.2f}% exceeds max allowed {config.max_daily_loss}%.")
                
            # 2. Account drawdown
            drawdown_pct = ((current_balance - current_equity) / current_balance) * 100.0
            if drawdown_pct > config.max_drawdown:
                approved = False
                reasons.append(f"Account drawdown {drawdown_pct:.2f}% exceeds max allowed {config.max_drawdown}%.")
                
        # 3. Consecutive losses (disabled if <= 0)
        if config.max_consecutive_losses > 0 and consecutive_losses >= config.max_consecutive_losses:
            approved = False
            reasons.append(f"Consecutive losses ({consecutive_losses}) reached max allowed ({config.max_consecutive_losses}).")
            
        return FilterResult(approved=approved, reasons=reasons)
