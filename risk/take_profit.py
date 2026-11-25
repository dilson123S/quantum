from core.constants import Direction
from core.models import StructureAnalysis
from core.config_loader import TradeConfig


class TakeProfitCalculator:
    """Calculates take profit levels."""

    def __init__(self):
        pass

    def calculate_tp(
        self,
        direction: Direction,
        entry_price: float,
        sl_price: float,
        structure_analysis: StructureAnalysis,
        atr_value: float,
        config: TradeConfig
    ) -> float:
        """
        Calculate the take profit price based on multiple models and select the most 
        conservative one that meets the minimum Risk:Reward requirements.
        
        Args:
            direction: Trade direction (BUY or SELL).
            entry_price: The entry price of the trade.
            sl_price: The stop loss price.
            structure_analysis: The analyzed market structure.
            atr_value: Current ATR value.
            config: Trade configuration (risk_reward ratio, etc.).
            
        Returns:
            The calculated take profit price.
        """
        risk_reward = getattr(config, "risk_reward", 2.0)
        min_rr = getattr(config, "min_risk_reward", min(1.0, risk_reward))
        
        models_tp = []
        
        if direction == Direction.BUY:
            sl_distance = entry_price - sl_price
            if sl_distance <= 0:
                sl_distance = atr_value  # Fallback

            # 1. R-multiple model
            tp_r_multiple = entry_price + sl_distance * risk_reward
            models_tp.append(tp_r_multiple)
            
            # 2. ATR model
            tp_atr = entry_price + atr_value * (risk_reward * 1.5)
            models_tp.append(tp_atr)
            
            # 3. Structure model (next resistance / last swing high)
            if structure_analysis.last_swing_high and structure_analysis.last_swing_high.price > entry_price:
                tp_structure = structure_analysis.last_swing_high.price
                # Verify minimum RR
                if (tp_structure - entry_price) >= (sl_distance * min_rr):
                    models_tp.append(tp_structure)
                    
            # Most conservative (lowest) TP for BUY among those satisfying min R:R
            # Filter models that satisfy min RR
            valid_tps = [tp for tp in models_tp if (tp - entry_price) >= (sl_distance * min_rr)]
            if not valid_tps:
                # If none valid, fallback to R-multiple
                return tp_r_multiple
                
            return min(valid_tps)
            
        else: # SELL
            sl_distance = sl_price - entry_price
            if sl_distance <= 0:
                sl_distance = atr_value  # Fallback

            # 1. R-multiple model
            tp_r_multiple = entry_price - sl_distance * risk_reward
            models_tp.append(tp_r_multiple)
            
            # 2. ATR model
            tp_atr = entry_price - atr_value * (risk_reward * 1.5)
            models_tp.append(tp_atr)
            
            # 3. Structure model (next support / last swing low)
            if structure_analysis.last_swing_low and structure_analysis.last_swing_low.price < entry_price:
                tp_structure = structure_analysis.last_swing_low.price
                # Verify minimum RR
                if (entry_price - tp_structure) >= (sl_distance * min_rr):
                    models_tp.append(tp_structure)
                    
            # Most conservative (highest) TP for SELL among those satisfying min R:R
            # Filter models that satisfy min RR
            valid_tps = [tp for tp in models_tp if (entry_price - tp) >= (sl_distance * min_rr)]
            if not valid_tps:
                # If none valid, fallback to R-multiple
                return tp_r_multiple
                
            return max(valid_tps)
