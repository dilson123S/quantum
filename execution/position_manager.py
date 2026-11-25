import logging
import time
from typing import Optional, Any, List, Dict
import pandas as pd

from .mt5_connector import MT5Connector
from .order_manager import OrderManager

try:
    from core.models import TradeSetup, StructureAnalysis
    from core.config_loader import AppConfig
except ImportError:
    TradeSetup = Any
    StructureAnalysis = Any
    AppConfig = Any

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = logging.getLogger(__name__)

class PositionManager:
    """
    Manages active positions: break even, trailing stops, partial closures.
    """

    def __init__(self, connector: MT5Connector, order_manager: OrderManager, config: Any, trade_repository: Any = None):
        self.connector = connector
        self.order_manager = order_manager
        self.config = config
        self.trade_repository = trade_repository
        self.active_trades_state: Dict[int, Dict[str, Any]] = {}

    def _get_trade_state(self, ticket: int) -> Dict[str, Any]:
        if ticket not in self.active_trades_state:
            persisted = self.trade_repository.get_management_state(ticket) if self.trade_repository else {}
            self.active_trades_state[ticket] = {
                "partial_closed": bool(persisted.get("partial_closed", False)),
                "be_set": bool(persisted.get("be_set", False)),
                "initial_sl": persisted.get("initial_sl"),
            }
        return self.active_trades_state[ticket]

    def check_break_even(self, position: Any, trade_setup: Optional[Any] = None) -> bool:
        """
        Moves SL to entry + spread buffer if current profit >= break_even_r * initial_risk_distance.
        """
        if not self.config or not hasattr(self.config, 'trade'):
            return False

        be_r = getattr(self.config.trade, 'break_even_r', 1.0)
        if be_r is None or be_r <= 0:
            return False
            
        ticket = position.ticket
        state = self._get_trade_state(ticket)
        if state["be_set"]:
            return False

        # Determine initial SL from trade_setup or position.sl
        initial_sl = None
        if trade_setup is not None:
            initial_sl = getattr(trade_setup, 'sl_price', getattr(trade_setup, 'sl', None))
        if initial_sl is None and state.get("initial_sl"):
            initial_sl = state["initial_sl"]
        if initial_sl is None and position.sl > 0:
            initial_sl = position.sl
            state["initial_sl"] = initial_sl
        elif initial_sl is not None and not state.get("initial_sl"):
            state["initial_sl"] = initial_sl

        if initial_sl is None:
            return False

        entry_price = position.price_open
        current_price = position.price_current
        
        is_buy = position.type == (mt5.ORDER_TYPE_BUY if mt5 else 0)
        
        risk_distance = abs(entry_price - initial_sl)
        if risk_distance == 0:
            return False

        profit_distance = (current_price - entry_price) if is_buy else (entry_price - current_price)
        
        if profit_distance >= (be_r * risk_distance):
            symbol_info = self.connector.get_symbol_info(position.symbol)
            if symbol_info is None:
                return False
                
            spread_buffer = symbol_info.point * 10 # 10 points buffer
            
            new_sl = entry_price + spread_buffer if is_buy else entry_price - spread_buffer
            new_sl = round(new_sl, symbol_info.digits)

            # Ensure new_sl is strictly better than current SL
            if (is_buy and new_sl > position.sl) or (not is_buy and (position.sl == 0.0 or new_sl < position.sl)):
                res = self.order_manager.modify_sltp(ticket, sl=new_sl, tp=position.tp)
                if res.success:
                    state["be_set"] = True
                    if self.trade_repository:
                        self.trade_repository.update_management_state(ticket, be_set=True)
                    logger.info(f"🔒 Break-even secured for ticket {ticket} ({position.symbol}) at {new_sl}")
                    return True
                
        return False

    def check_partial_close(self, position: Any, trade_setup: Optional[Any] = None) -> bool:
        """
        Closes partial_percent of volume if profit >= partial_r * risk.
        """
        if not self.config or not hasattr(self.config, 'trade'):
            return False

        if not getattr(self.config.trade, 'partial_close', False):
            return False
            
        ticket = position.ticket
        state = self._get_trade_state(ticket)
        if state["partial_closed"]:
            return False

        partial_r = getattr(self.config.trade, 'partial_r', 1.0)
        raw_pct = getattr(self.config.trade, 'partial_percent', 50.0)
        partial_percent = (raw_pct / 100.0) if raw_pct > 1.0 else raw_pct
        
        initial_sl = None
        if trade_setup is not None:
            initial_sl = getattr(trade_setup, 'sl_price', getattr(trade_setup, 'sl', None))
        if initial_sl is None and state.get("initial_sl"):
            initial_sl = state["initial_sl"]
        if initial_sl is None and position.sl > 0:
            initial_sl = position.sl
            state["initial_sl"] = initial_sl
        elif initial_sl is not None and not state.get("initial_sl"):
            state["initial_sl"] = initial_sl

        if initial_sl is None:
            return False

        entry_price = position.price_open
        current_price = position.price_current
        is_buy = position.type == (mt5.ORDER_TYPE_BUY if mt5 else 0)
        
        risk_distance = abs(entry_price - initial_sl)
        if risk_distance == 0:
            return False

        profit_distance = (current_price - entry_price) if is_buy else (entry_price - current_price)
        
        if profit_distance >= (partial_r * risk_distance):
            symbol_info = self.connector.get_symbol_info(position.symbol)
            min_vol = symbol_info.volume_min if symbol_info else 0.01
            vol_step = symbol_info.volume_step if symbol_info else 0.01
            
            raw_close_vol = position.volume * partial_percent
            steps = round(raw_close_vol / vol_step)
            close_vol = round(steps * vol_step, 2)

            if close_vol >= min_vol and close_vol < position.volume:
                res = self.order_manager.close_order(ticket, volume=close_vol)
                if res.success:
                    state["partial_closed"] = True
                    if self.trade_repository:
                        remaining = max(0.0, float(position.volume) - float(close_vol))
                        self.trade_repository.update_management_state(
                            ticket, partial_closed=True, remaining_volume=remaining,
                        )
                    logger.info(f"💰 Partial close executed for ticket {ticket} ({position.symbol}), secured volume {close_vol}")

                    # Atomic Harvest: immediately move SL of remaining volume to entry + buffer so trade CANNOT lose
                    spread_buffer = (symbol_info.point * 10) if symbol_info else 0.10
                    be_sl = round(entry_price + spread_buffer if is_buy else entry_price - spread_buffer, symbol_info.digits if symbol_info else 2)
                    be_res = self.order_manager.modify_sltp(ticket, sl=be_sl, tp=position.tp)
                    if be_res.success:
                        state["be_set"] = True
                        if self.trade_repository:
                            self.trade_repository.update_management_state(ticket, be_set=True)
                        logger.info(f"🔒 Atomic Break-Even secured post-harvest for ticket {ticket} ({position.symbol}) at {be_sl}")

                    return True
                    
        return False

    def check_stagnation_exit(
        self,
        position: Any,
        df_entry: Optional[pd.DataFrame] = None,
        trade_setup: Optional[Any] = None,
    ) -> bool:
        """
        Closes position at market if it has remained open for >= stagnation_exit_bars
        without achieving stagnation_min_progress_r (e.g. failing to reach escape velocity).
        Only applies to trades that haven't already secured Break-Even or partial profit.
        """
        if not self.config or not hasattr(self.config, 'trade'):
            return False

        stagnation_bars = getattr(self.config.trade, 'stagnation_exit_bars', 0)
        if not stagnation_bars or stagnation_bars <= 0:
            return False

        ticket = getattr(position, 'ticket', None)
        if ticket is None:
            return False

        state = self._get_trade_state(ticket)
        if state.get("be_set") or state.get("partial_closed"):
            return False

        # Calculate bars open
        bars_open = 0
        open_time = getattr(position, 'time', None)
        entry_tf = getattr(getattr(self.config, 'timeframes', None), 'entry', 'H1')
        tf_seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}.get(entry_tf, 3600)

        if open_time:
            elapsed = max(0.0, time.time() - float(open_time))
            bars_open = int(elapsed // tf_seconds)
        elif df_entry is not None and len(df_entry) > 0 and trade_setup is not None:
            created_at = getattr(trade_setup, 'created_at', getattr(trade_setup, 'entered_at', None))
            if created_at:
                now_dt = df_entry['time'].iloc[-1] if 'time' in df_entry.columns else None
                if hasattr(now_dt, "to_pydatetime"):
                    now_dt = now_dt.to_pydatetime()
                if hasattr(created_at, "to_pydatetime"):
                    created_at = created_at.to_pydatetime()
                if now_dt and created_at:
                    elapsed = max(0.0, (now_dt - created_at).total_seconds())
                    bars_open = int(elapsed // tf_seconds)

        if bars_open < stagnation_bars:
            return False

        initial_sl = None
        if trade_setup is not None:
            initial_sl = getattr(trade_setup, 'sl_price', getattr(trade_setup, 'sl', None))
        if initial_sl is None and state.get("initial_sl"):
            initial_sl = state["initial_sl"]
        if initial_sl is None and position.sl > 0:
            initial_sl = position.sl
            state["initial_sl"] = initial_sl
        elif initial_sl is not None and not state.get("initial_sl"):
            state["initial_sl"] = initial_sl

        if initial_sl is None:
            return False

        entry_price = position.price_open
        current_price = position.price_current
        is_buy = position.type == (mt5.ORDER_TYPE_BUY if mt5 else 0)

        risk_distance = abs(entry_price - initial_sl)
        if risk_distance == 0:
            return False

        profit_distance = (current_price - entry_price) if is_buy else (entry_price - current_price)
        current_r = profit_distance / risk_distance
        min_progress_r = getattr(self.config.trade, 'stagnation_min_progress_r', 0.4)

        if current_r < min_progress_r:
            res = self.order_manager.close_order(ticket)
            if res.success:
                state["stagnation_exited"] = True
                if self.trade_repository:
                    self.trade_repository.update_management_state(ticket, stagnation_exited=True)
                logger.info(
                    f"⏱️ Stagnation exit triggered for ticket {ticket} ({position.symbol}) "
                    f"after {bars_open} bars: progress was {current_r:+.2f}R (threshold {min_progress_r:.2f}R)."
                )
                return True

        return False

    def check_trailing_stop(
        self,
        position: Any,
        df_entry: pd.DataFrame,
        structure_analysis: Optional[Any] = None,
        trade_setup: Optional[Any] = None,
    ) -> bool:
        """
        Updates SL based on ATR, STRUCTURE, or HYBRID mode.
        Guards against premature trailing with activation threshold (R),
        structural buffer behind swings, and minimum breathing distance.
        """
        if not self.config or not hasattr(self.config, 'trade'):
            return False
            
        trailing_setting = getattr(self.config.trade, 'trailing', getattr(self.config.trade, 'trailing_mode', None))
        if not trailing_setting:
            return False

        # TrailingMode is a ``str, Enum``; stringifying the enum itself yields
        # "TrailingMode.HYBRID" rather than "HYBRID" on supported Python
        # versions, silently disabling configured trailing stops.
        mode = str(getattr(trailing_setting, "value", trailing_setting)).upper()
        if mode in ("OFF", "NONE"):
            return False

        current_price = position.price_current
        entry_price = position.price_open
        is_buy = position.type == (mt5.ORDER_TYPE_BUY if mt5 else 0)
        symbol_info = self.connector.get_symbol_info(position.symbol)
        if not symbol_info:
            return False
            
        digits = symbol_info.digits
        point = symbol_info.point or 0.00001
        ticket = getattr(position, "ticket", None)
        state = self._get_trade_state(ticket) if ticket else {}

        # 1. Activation threshold: require trade to develop at least trailing_activation_r profit
        activation_r = getattr(self.config.trade, 'trailing_activation_r', 0.0)
        if activation_r and activation_r > 0:
            initial_sl = None
            if trade_setup is not None:
                initial_sl = getattr(trade_setup, 'sl_price', getattr(trade_setup, 'sl', None))
            if initial_sl is None and state.get("initial_sl"):
                initial_sl = state["initial_sl"]
            if initial_sl is None and position.sl > 0:
                initial_sl = position.sl
                if ticket:
                    state["initial_sl"] = initial_sl

            profit_distance = (current_price - entry_price) if is_buy else (entry_price - current_price)
            risk_distance = abs(entry_price - initial_sl) if initial_sl else 0.0

            # Fallback if initial risk distance is not explicitly known: estimate from ATR
            if risk_distance <= 0 and "atr" in df_entry.columns and len(df_entry["atr"]) > 0:
                atr_fallback = float(df_entry["atr"].iloc[-1])
                sl_mult = getattr(getattr(self.config, 'volatility', None), 'sl_multiplier', 1.5)
                risk_distance = atr_fallback * sl_mult

            if risk_distance > 0 and profit_distance < (activation_r * risk_distance):
                return False

        atr_val = None
        if "atr" in df_entry.columns and len(df_entry["atr"]) > 0:
            atr_val = float(df_entry["atr"].iloc[-1])

        new_sl = None

        if mode in ["ATR", "HYBRID"]:
            if atr_val is not None:
                mult = getattr(self.config.trade, 'trailing_atr_multiplier', 2.0)
                atr_dist = atr_val * mult
                atr_sl = current_price - atr_dist if is_buy else current_price + atr_dist
                new_sl = atr_sl

        if mode in ["STRUCTURE", "HYBRID"] and structure_analysis:
            struct_sl = None
            if is_buy and structure_analysis.last_swing_low:
                struct_sl = structure_analysis.last_swing_low.price if hasattr(structure_analysis.last_swing_low, 'price') else float(structure_analysis.last_swing_low)
            elif not is_buy and structure_analysis.last_swing_high:
                struct_sl = structure_analysis.last_swing_high.price if hasattr(structure_analysis.last_swing_high, 'price') else float(structure_analysis.last_swing_high)
                
            if struct_sl is not None:
                # 2. Structural buffer: place SL safely behind the swing level (prevent wick sweep stop-outs)
                struct_buffer_mult = getattr(self.config.trade, 'trailing_struct_buffer_atr', 0.0)
                if struct_buffer_mult > 0 and atr_val is not None:
                    struct_buffer = atr_val * struct_buffer_mult
                    struct_sl = (struct_sl - struct_buffer) if is_buy else (struct_sl + struct_buffer)

                if mode == "STRUCTURE":
                    new_sl = struct_sl
                elif mode == "HYBRID" and new_sl is not None:
                    new_sl = max(struct_sl, new_sl) if is_buy else min(struct_sl, new_sl)
                elif mode == "HYBRID":
                    new_sl = struct_sl

        if new_sl is None:
            return False

        # 3. Minimum safety breathing distance from current price
        min_dist_mult = getattr(self.config.trade, 'min_trailing_distance_atr', 0.0)
        stops_level_dist = float(getattr(symbol_info, 'stops_level', 0) or 0) * point
        min_distance = max(stops_level_dist, (atr_val * min_dist_mult) if (min_dist_mult > 0 and atr_val) else 0.0)

        if min_distance > 0:
            if is_buy:
                new_sl = min(new_sl, current_price - min_distance)
            else:
                new_sl = max(new_sl, current_price + min_distance)

        new_sl = round(new_sl, digits)
        
        # Only move SL forward (never widen risk)
        if is_buy and new_sl > position.sl:
            res = self.order_manager.modify_sltp(position.ticket, sl=new_sl, tp=position.tp)
            if res.success:
                logger.info(f"Trailing SL moved up to {new_sl} for ticket {position.ticket}")
                return True
        elif not is_buy and (position.sl == 0.0 or new_sl < position.sl):
            res = self.order_manager.modify_sltp(position.ticket, sl=new_sl, tp=position.tp)
            if res.success:
                logger.info(f"Trailing SL moved down to {new_sl} for ticket {position.ticket}")
                return True
                
        return False

    def manage_positions(
        self,
        open_positions: List[Any],
        df_entry_map: Optional[Dict[str, pd.DataFrame]] = None,
        structure_map: Optional[Dict[str, Any]] = None,
        trade_context_by_ticket: Optional[Dict[int, Any]] = None,
    ) -> None:
        """
        Iterates over open positions and runs BE -> Partial -> Trailing checks.
        """
        df_entry_map = df_entry_map or {}
        structure_map = structure_map or {}
        trade_context_by_ticket = trade_context_by_ticket or {}

        for pos in open_positions:
            sym = pos.symbol
            trade_setup = trade_context_by_ticket.get(
                getattr(pos, "ticket", None), getattr(pos, 'trade_setup', None)
            )
            df_entry = df_entry_map.get(sym)

            # Check Stagnation Exit
            if self.check_stagnation_exit(pos, df_entry, trade_setup):
                continue

            # Check Break Even
            self.check_break_even(pos, trade_setup)
            
            # Check Partial Close
            self.check_partial_close(pos, trade_setup)
            
            # Check Trailing Stop
            if sym in df_entry_map:
                struct = structure_map.get(sym)
                self.check_trailing_stop(pos, df_entry_map[sym], struct, trade_setup)
