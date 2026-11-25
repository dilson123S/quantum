from typing import List, Dict, Any
import pandas as pd
from core.models import FilterResult
from core.constants import Direction
from core.config_loader import RiskConfig

class CorrelationFilter:
    """Filter to prevent overexposure to highly correlated symbols."""
    
    def check(self, 
              new_symbol: str, 
              new_direction: Direction, 
              open_positions: List[Any], 
              price_history_dict: Dict[str, pd.DataFrame], 
              config: RiskConfig) -> FilterResult:
        """
        Check if taking this trade would violate correlation limits.
        
        Args:
            new_symbol: Symbol to be traded.
            new_direction: Direction of the new trade (LONG/SHORT).
            open_positions: List of currently open positions (expected to have `symbol` and `direction`).
            price_history_dict: Dictionary mapping symbol to its historical dataframe.
            config: Risk configuration.
            
        Returns:
            FilterResult: Result containing approval status and reasons.
        """
        if not open_positions:
            return FilterResult(approved=True)
            
        correlated_count = 0
        
        for pos in open_positions:
            pos_symbol = getattr(pos, 'symbol', None) or (pos['symbol'] if isinstance(pos, dict) else None)
            
            # Extract direction: supports Direction enum, string, or MT5 position type (0=BUY, 1=SELL)
            pos_direction = None
            if hasattr(pos, 'direction') and pos.direction is not None:
                pos_direction = pos.direction
            elif isinstance(pos, dict) and 'direction' in pos and pos['direction'] is not None:
                pos_direction = pos['direction']
            elif hasattr(pos, 'type') and pos.type is not None:
                pos_direction = Direction.BUY if pos.type == 0 else Direction.SELL
            elif isinstance(pos, dict) and 'type' in pos and pos['type'] is not None:
                pos_direction = Direction.BUY if pos['type'] == 0 else Direction.SELL

            # Convert string to Direction enum if necessary
            if isinstance(pos_direction, str):
                pos_direction = Direction.BUY if pos_direction.upper() == "BUY" else Direction.SELL

            if not pos_symbol or pos_direction != new_direction:
                continue

            configured_groups = getattr(config, "correlation_groups", {}) or {}
            same_declared_group = any(
                new_symbol in members and pos_symbol in members
                for members in configured_groups.values()
                if isinstance(members, list)
            )
            if same_declared_group:
                correlated_count += 1
                continue
                
            if new_symbol in price_history_dict and pos_symbol in price_history_dict:
                df_new = price_history_dict[new_symbol]
                df_pos = price_history_dict[pos_symbol]
                
                if 'close' in df_new.columns and 'close' in df_pos.columns:
                    if len(df_new) >= 20 and len(df_pos) >= 20:
                        ret_new = df_new['close'].pct_change().tail(20)
                        ret_pos = df_pos['close'].pct_change().tail(20)
                        
                        corr = ret_new.corr(ret_pos)
                        if corr > config.max_correlation:
                            correlated_count += 1
                            
        # The new position itself is not yet open, so we check if correlated_count is >= max
        # which means adding one more highly correlated position would exceed the limit.
        if correlated_count >= config.max_correlated_positions:
            return FilterResult(
                approved=False, 
                reasons=[f"Max highly correlated positions ({config.max_correlated_positions}) reached for {new_symbol} in {new_direction.value}."]
            )
            
        return FilterResult(approved=True)
