from core.constants import Direction
from core.models import StructureAnalysis
from core.config_loader import VolatilityConfig


class StopLossCalculator:
    """Calculates stop loss levels."""

    def __init__(self):
        pass

    def calculate_sl(
        self,
        direction: Direction,
        entry_price: float,
        structure_analysis: StructureAnalysis,
        atr_value: float,
        spread_price: float,
        config: VolatilityConfig,
        symbol_point: float
    ) -> float:
        """
        Calculate the stop loss price based on market structure and ATR.
        
        Args:
            direction: Trade direction (BUY or SELL).
            entry_price: The entry price of the trade.
            structure_analysis: The analyzed market structure (swing highs/lows).
            atr_value: Current ATR value.
            spread_price: Current spread in price terms.
            config: Volatility configuration (Multiplier, max distance, etc.).
            symbol_point: The point value for the symbol.
            
        Returns:
            The calculated stop loss price.
        """
        sl_multiplier = getattr(config, "sl_multiplier", 1.5)
        min_distance = getattr(config, "min_stop_distance_points", 0) * symbol_point
        max_distance = getattr(config, "max_atr_distance_multiplier", 3.0) * atr_value
        anti_hunt_buffer = getattr(config, "sl_buffer_atr", 0.15) * atr_value
        
        if direction == Direction.BUY:
            # Structure-based stop loss with anti-hunt buffer
            if structure_analysis and structure_analysis.last_swing_low:
                sl_structure = structure_analysis.last_swing_low.price - spread_price * 2 - anti_hunt_buffer
                # If swing low is invalidly above entry, ignore it
                if sl_structure >= entry_price:
                    sl_structure = float('inf')
            else:
                sl_structure = float('inf')
                
            # ATR-based stop loss with anti-hunt buffer
            sl_atr = entry_price - (atr_value * sl_multiplier) - anti_hunt_buffer
            
            # Final is the protective placement (min gives wider protection below entry)
            sl_final = min(sl_structure, sl_atr) if sl_structure != float('inf') else sl_atr
            
            # Distance validations
            distance = entry_price - sl_final
            if distance < min_distance or sl_final >= entry_price:
                sl_final = entry_price - max(min_distance, atr_value * 0.5)
            elif distance > max_distance:
                sl_final = entry_price - max_distance
                
        else: # SELL
            # Structure-based stop loss with anti-hunt buffer
            if structure_analysis and structure_analysis.last_swing_high:
                sl_structure = structure_analysis.last_swing_high.price + spread_price * 2 + anti_hunt_buffer
                # If swing high is invalidly below entry, ignore it
                if sl_structure <= entry_price:
                    sl_structure = 0.0
            else:
                sl_structure = 0.0
                
            # ATR-based stop loss with anti-hunt buffer
            sl_atr = entry_price + (atr_value * sl_multiplier) + anti_hunt_buffer
            
            # Final is the protective placement
            sl_final = max(sl_structure, sl_atr) if sl_structure > 0.0 else sl_atr
            
            # Distance validations
            distance = sl_final - entry_price
            if distance < min_distance or sl_final <= entry_price:
                sl_final = entry_price + max(min_distance, atr_value * 0.5)
            elif distance > max_distance:
                sl_final = entry_price + max_distance

        return sl_final
