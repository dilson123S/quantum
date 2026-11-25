"""Order creation, placement, and modification."""
import logging
from typing import Optional, Any

from core.models import TradeSetup, ExecutionResult
from core.constants import BotMode, Direction
from execution.mt5_connector import MT5Connector

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = logging.getLogger(__name__)


class OrderManager:
    """Manages order creation, transmission, and modifications."""

    def __init__(self, connector: MT5Connector, config: Any):
        self.connector = connector
        self.config = config

    def _validate_order_route(self) -> Optional[ExecutionResult]:
        """Reject every broker mutation unless its route is authorised.

        Creation, cancellation, partial close and SL/TP modification are all
        mutations.  Treating only entry creation as an execution route made a
        supposedly harmless PAPER process able to alter a real MT5 account.
        """
        if not self.connector.is_connected():
            return ExecutionResult(success=False, message="MT5 is not connected.")

        mode = getattr(self.config, "mode", BotMode.PAPER)
        mode_value = getattr(mode, "value", mode)
        mode_value = str(mode_value).lower()
        if mode_value not in {BotMode.DEMO.value, BotMode.LIVE.value}:
            return ExecutionResult(
                success=False,
                message=f"Order routing is disabled in '{mode_value}' mode.",
            )

        authorized = getattr(self.config, "execution_authorized", None)
        if authorized is False:
            return ExecutionResult(
                success=False,
                message="Execution is blocked by the profile validation status.",
            )

        account_ok, account_reason = self.connector.validate_execution_account(
            mode=mode_value,
            allow_live_trading=bool(getattr(self.config, "allow_live_trading", False)),
        )
        if not account_ok:
            return ExecutionResult(success=False, message=account_reason)
        return None

    def _comment_for(self, trade_setup: TradeSetup) -> str:
        tag = getattr(self.config, "comment_tag", None)
        if not tag:
            name = str(getattr(self.config, "name", "QTB"))
            if "DemoA" in name or name.endswith("A"):
                tag = "BOT-A"
            elif "DemoB" in name or name.endswith("B"):
                tag = "BOT-B"
            elif "DemoC" in name or name.endswith("C"):
                tag = "BOT-C"
            elif "DemoD" in name or name.endswith("D"):
                tag = "BOT-D"
            else:
                tag = "QTB"
        sig_id = trade_setup.signal.signal_id[:16]
        return f"{tag}:{sig_id}"

    def create_market_order(self, trade_setup: TradeSetup) -> ExecutionResult:
        """Create and transmit a market order to MT5 based on a TradeSetup."""
        route_error = self._validate_order_route()
        if route_error:
            return route_error

        symbol = trade_setup.signal.symbol if hasattr(trade_setup, "signal") else getattr(trade_setup, "symbol", "EURUSD")
        direction = trade_setup.signal.direction if hasattr(trade_setup, "signal") else getattr(trade_setup, "direction", Direction.BUY)
        sl_price = getattr(trade_setup, "sl_price", getattr(trade_setup, "sl", 0.0))
        tp_price = getattr(trade_setup, "tp_price", getattr(trade_setup, "tp", 0.0))
        volume = float(trade_setup.volume)
        if volume <= 0:
            return ExecutionResult(success=False, message="Order volume must be greater than zero.")

        symbol_info = self.connector.get_symbol_info(symbol)
        if symbol_info is None:
            return ExecutionResult(success=False, message=f"Failed to get symbol info for {symbol}")

        tick = self.connector.get_symbol_tick(symbol)
        if tick is None:
            return ExecutionResult(success=False, message=f"Failed to get live tick for {symbol}")

        # Determine order type and price
        if direction in (Direction.BUY, "BUY"):
            order_type = mt5.ORDER_TYPE_BUY if mt5 else 0
            price = tick.ask
        elif direction in (Direction.SELL, "SELL"):
            order_type = mt5.ORDER_TYPE_SELL if mt5 else 1
            price = tick.bid
        else:
            return ExecutionResult(success=False, message=f"Invalid order direction: {direction}")

        digits = symbol_info.digits
        price = round(price, digits)
        sl = round(sl_price, digits) if sl_price > 0 else 0.0
        tp = round(tp_price, digits) if tp_price > 0 else 0.0

        comment = self._comment_for(trade_setup)

        magic_number = getattr(self.config, "magic_number", 26081901)

        request = {
            "action": mt5.TRADE_ACTION_DEAL if mt5 else 1,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC if mt5 else 0,
            "type_filling": self.connector.get_filling_mode(symbol),
        }

        # Let the terminal validate broker-specific margin, stop and filling
        # constraints before an irreversible order_send call.
        check = self.connector.check_order(request)
        check_retcode = getattr(check, "retcode", None) if check is not None else None
        if check_retcode not in (0, 10009):
            message = getattr(check, "comment", "MT5 order pre-check failed.") if check else "MT5 order pre-check failed."
            return ExecutionResult(success=False, retcode=check_retcode, message=message)

        result = self.connector.execute_with_retry(request)
        if result is None:
            return ExecutionResult(success=False, message="Order execution failed after retries.")

        success = result.retcode == (mt5.TRADE_RETCODE_DONE if mt5 else 10009)
        ticket = getattr(result, "order", None)
        if success:
            ticket = self.connector.find_position_ticket(symbol, magic_number, comment) or ticket

        return ExecutionResult(
            success=success,
            ticket=ticket,
            price=getattr(result, "price", price),
            volume=getattr(result, "volume", volume),
            retcode=getattr(result, "retcode", None),
            message=getattr(result, "comment", "Executed")
        )

    def create_breakout_order(self, trade_setup: TradeSetup) -> ExecutionResult:
        """Place an expiring BUY_STOP/SELL_STOP at a structural breakout.

        A breakout setup is not allowed to degrade into a market order.  MT5
        owns the expiration, so a process restart cannot leave it live beyond
        the configured bar window.
        """
        route_error = self._validate_order_route()
        if route_error:
            return route_error
        if not trade_setup.is_pending or trade_setup.expiration is None:
            return ExecutionResult(success=False, message="Breakout order requires an expiration timestamp.")

        symbol = trade_setup.signal.symbol
        direction = trade_setup.signal.direction
        volume = float(trade_setup.volume)
        if volume <= 0:
            return ExecutionResult(success=False, message="Order volume must be greater than zero.")

        symbol_info = self.connector.get_symbol_info(symbol)
        tick = self.connector.get_symbol_tick(symbol)
        if symbol_info is None or tick is None:
            return ExecutionResult(success=False, message=f"Failed to obtain live symbol data for {symbol}.")

        digits = symbol_info.digits
        entry = round(float(trade_setup.entry_price), digits)
        sl = round(float(trade_setup.sl_price), digits)
        tp = round(float(trade_setup.tp_price), digits)
        point = float(symbol_info.point)
        stops_level = float(getattr(symbol_info, "trade_stops_level", 0)) * point

        if direction in (Direction.BUY, "BUY"):
            if entry <= tick.ask:
                return ExecutionResult(success=False, message="Buy breakout trigger is no longer above the live ask.")
            if sl >= entry or tp <= entry:
                return ExecutionResult(success=False, message="Invalid BUY breakout SL/TP geometry.")
            if stops_level and (entry - tick.ask) < stops_level:
                return ExecutionResult(success=False, message="Buy breakout trigger is inside the broker stops-level.")
            order_type = mt5.ORDER_TYPE_BUY_STOP if mt5 else 4
        elif direction in (Direction.SELL, "SELL"):
            if entry >= tick.bid:
                return ExecutionResult(success=False, message="Sell breakout trigger is no longer below the live bid.")
            if sl <= entry or tp >= entry:
                return ExecutionResult(success=False, message="Invalid SELL breakout SL/TP geometry.")
            if stops_level and (tick.bid - entry) < stops_level:
                return ExecutionResult(success=False, message="Sell breakout trigger is inside the broker stops-level.")
            order_type = mt5.ORDER_TYPE_SELL_STOP if mt5 else 5
        else:
            return ExecutionResult(success=False, message=f"Invalid order direction: {direction}")

        comment = self._comment_for(trade_setup)
        request = {
            "action": mt5.TRADE_ACTION_PENDING if mt5 else 5,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": entry,
            "sl": sl,
            "tp": tp,
            "magic": getattr(self.config, "magic_number", 26081901),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_SPECIFIED if mt5 else 2,
            "expiration": int(trade_setup.expiration.timestamp()) if hasattr(trade_setup.expiration, "timestamp") else int(trade_setup.expiration or 0),
            "type_filling": self.connector.get_filling_mode(symbol),
        }
        check = self.connector.check_order(request)
        check_retcode = getattr(check, "retcode", None) if check is not None else None
        if check_retcode not in (0, 10008, 10009):
            return ExecutionResult(
                success=False,
                retcode=check_retcode,
                message=getattr(check, "comment", "MT5 pending-order pre-check failed.") if check else "MT5 pending-order pre-check failed.",
            )

        result = self.connector.execute_with_retry(request)
        if result is None:
            return ExecutionResult(success=False, message="Breakout order placement failed after retries.")
        retcode = getattr(result, "retcode", None)
        success = retcode in {
            mt5.TRADE_RETCODE_PLACED if mt5 else 10008,
            mt5.TRADE_RETCODE_DONE if mt5 else 10009,
        }
        return ExecutionResult(
            success=success,
            ticket=getattr(result, "order", None),
            price=getattr(result, "price", entry),
            volume=getattr(result, "volume", volume),
            retcode=retcode,
            message=getattr(result, "comment", "Breakout order placed"),
        )

    def cancel_pending_order(self, ticket: int) -> ExecutionResult:
        route_error = self._validate_order_route()
        if route_error:
            return route_error
        result = self.connector.cancel_pending_order(ticket)
        if result is None:
            return ExecutionResult(success=False, message="Pending order cancellation failed.")
        success = getattr(result, "retcode", None) == (mt5.TRADE_RETCODE_DONE if mt5 else 10009)
        return ExecutionResult(
            success=success,
            ticket=getattr(result, "order", ticket),
            retcode=getattr(result, "retcode", None),
            message=getattr(result, "comment", "Pending order cancelled"),
        )

    def close_order(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        """Close an existing position (full or partial)."""
        route_error = self._validate_order_route()
        if route_error:
            return route_error
        result = self.connector.close_position(ticket, volume=volume)
        if result is None:
            return ExecutionResult(success=False, message="Close position failed.")

        success = result.retcode == (mt5.TRADE_RETCODE_DONE if mt5 else 10009)
        return ExecutionResult(
            success=success,
            ticket=getattr(result, "order", ticket),
            price=getattr(result, "price", None),
            volume=getattr(result, "volume", None),
            retcode=getattr(result, "retcode", None),
            message=getattr(result, "comment", "Closed")
        )

    def modify_sltp(self, ticket: int, sl: float, tp: float) -> ExecutionResult:
        """Modify SL and TP for an open position."""
        route_error = self._validate_order_route()
        if route_error:
            return route_error
        result = self.connector.modify_position(ticket, sl=sl, tp=tp)
        if result is None:
            return ExecutionResult(success=False, message="Modify position failed.")

        success = result.retcode == (mt5.TRADE_RETCODE_DONE if mt5 else 10009)
        return ExecutionResult(
            success=success,
            ticket=getattr(result, "order", ticket),
            retcode=getattr(result, "retcode", None),
            message=getattr(result, "comment", "Modified")
        )
