from typing import Optional
from core.models import FilterResult
from core.config_loader import FilterConfig, SymbolConfig

class SpreadFilter:
    """Filter to reject trades if spread is too high (absolute or relative to ATR)."""
    
    def check(self, 
              symbol: str, 
              current_spread_points: float, 
              atr_value: float, 
              point_size: float, 
              config: FilterConfig, 
              symbol_config: Optional[SymbolConfig] = None) -> FilterResult:
        """
        Check if the current spread is acceptable.
        
        Args:
            symbol: Symbol being traded.
            current_spread_points: Current spread in points.
            atr_value: Current ATR value.
            point_size: Point size for the symbol.
            config: General filter configuration.
            symbol_config: Symbol-specific configuration (optional).
            
        Returns:
            FilterResult: Result containing approval status and reasons.
        """
        reasons = []
        approved = True
        
        # 1. Fixed max spread points
        max_spread = 50.0
        if symbol_config and symbol_config.max_spread_points is not None:
            max_spread = float(symbol_config.max_spread_points)
            
        if current_spread_points > max_spread:
            approved = False
            reasons.append(f"Spread {current_spread_points:.1f} points exceeds max allowed {max_spread:.1f} points.")
            
        # 2. ATR relative spread
        if atr_value > 0 and point_size > 0:
            spread_price = current_spread_points * point_size
            spread_atr_percent = (spread_price / atr_value) * 100.0
            
            if spread_atr_percent > config.max_spread_atr_percent:
                approved = False
                reasons.append(f"Spread/ATR ratio {spread_atr_percent:.2f}% exceeds max {config.max_spread_atr_percent}%.")
                
        return FilterResult(approved=approved, reasons=reasons)
