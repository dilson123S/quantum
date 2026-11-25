import time
import logging
from typing import Optional, Any, List
from datetime import datetime, timedelta, timezone
import pandas as pd
from data.mt5_time import normalize_mt5_bar_times, to_mt5_query_time

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False


logger = logging.getLogger(__name__)


class MT5Connector:
    """
    Wrapper for MetaTrader5 API with fallback/mock capabilities if MT5 is unavailable.
    """

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode or not MT5_AVAILABLE
        self.connected = False

    @staticmethod
    def is_terminal_running() -> bool:
        """Check if terminal64.exe process exists in Windows."""
        try:
            import psutil
            for p in psutil.process_iter(['name']):
                if p.info.get('name') and 'terminal64' in p.info['name'].lower():
                    return True
        except Exception:
            pass
        try:
            import subprocess
            output = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/NH"],
                text=True, creationflags=0x08000000
            )
            return "terminal64.exe" in output.lower()
        except Exception:
            return False

    def initialize(self, path: Optional[str] = None, login: Optional[int] = None, password: Optional[str] = None, server: Optional[str] = None, timeout: int = 60000, require_running_gui: bool = True) -> bool:
        """Initializes the MT5 terminal. Guarantees we attach to user's visible GUI instead of spawning headless background process."""
        if self.mock_mode:
            logger.info("MOCK: MT5 initialized.")
            self.connected = True
            return True

        if require_running_gui and not self.is_terminal_running():
            logger.warning("[GUI REQUERIDA] MetaTrader 5 no está abierto en pantalla. Por favor abre la aplicación MT5 desde tu escritorio.")
            self.connected = False
            return False

        init_args = {}
        if path: init_args["path"] = path
        if login: init_args["login"] = login
        if password: init_args["password"] = password
        if server: init_args["server"] = server
        init_args["timeout"] = timeout

        if not mt5.initialize(**init_args):
            logger.error(f"MT5 initialization failed, error code: {mt5.last_error()}")
            self.connected = False
            return False

        logger.info("MT5 initialized successfully (attached to GUI session).")
        self.connected = True
        return True

    def login(self, login: int, password: str, server: str) -> bool:
        if self.mock_mode:
            logger.info(f"MOCK: Logged in to {server} as {login}.")
            return True

        if not mt5.login(login, password, server):
            logger.error(f"MT5 login failed, error code: {mt5.last_error()}")
            return False
        
        logger.info(f"MT5 login successful for {login}.")
        return True

    def shutdown(self) -> None:
        if self.mock_mode:
            logger.info("MOCK: MT5 shutdown.")
            self.connected = False
            return
            
        mt5.shutdown()
        self.connected = False
        logger.info("MT5 shutdown successfully.")

    def is_connected(self) -> bool:
        if self.mock_mode:
            return self.connected
        
        # terminal_info doesn't necessarily mean connected to broker, but usually sufficient
        info = mt5.terminal_info()
        return info is not None and info.connected

    def reconnect(self, max_retries: int = 3, delay: int = 5) -> bool:
        for attempt in range(max_retries):
            if self.is_connected():
                return True
            logger.warning(f"Reconnecting to MT5, attempt {attempt + 1}/{max_retries}...")
            self.shutdown()
            if self.initialize():
                return True
            time.sleep(delay)
        return False

    def get_account_info(self) -> Optional[Any]:
        if self.mock_mode:
            class MockAccountInfo:
                margin_free = 10000.0
                balance = 10000.0
                equity = 10000.0
                trade_allowed = True
                # MT5 values: demo=0, contest=1, real=2.
                trade_mode = 0
            return MockAccountInfo()

        account_info = None
        for attempt in range(3):
            account_info = mt5.account_info()
            if account_info is not None:
                return account_info
            err = mt5.last_error()
            if attempt < 2 and isinstance(err, (tuple, list)) and len(err) > 0 and err[0] == -10001:
                time.sleep(0.15 * (attempt + 1))
                continue
            break

        if account_info is None:
            logger.error(f"Failed to get account info, error code: {mt5.last_error()}")
        return account_info

    def validate_execution_account(self, mode: Any, allow_live_trading: bool) -> tuple[bool, str]:
        """Validate the account type immediately before opening an order.

        This method intentionally lives next to the MT5 adapter so both the
        orchestrator and OrderManager can apply the same policy.  `live`
        requires the explicit configuration switch *and* a real account;
        `demo` refuses to route an order to a real account.
        """
        mode_value = getattr(mode, "value", mode)
        mode_value = str(mode_value).lower()

        if mode_value not in {"demo", "live"}:
            return True, ""
        if mode_value == "live" and not allow_live_trading:
            return False, "Live trading is disabled by bot.allow_live_trading."

        account = self.get_account_info()
        if account is None:
            return False, "Unable to read MT5 account information."
        if getattr(account, "trade_allowed", True) is False:
            return False, "Trading is disabled for the connected MT5 account."

        account_mode = getattr(account, "trade_mode", None)
        demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0) if mt5 else 0
        real_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2) if mt5 else 2
        if account_mode is None:
            return False, "MT5 account type is unavailable; refusing to route an order."
        if mode_value == "demo" and account_mode != demo_mode:
            return False, "Demo mode requires an MT5 demo account."
        if mode_value == "live" and account_mode != real_mode:
            return False, "Live mode requires an MT5 real account."

        return True, ""

    def get_symbol_info(self, symbol: str) -> Optional[Any]:
        if self.mock_mode:
            class MockSymbolInfo:
                digits = 5
                point = 0.00001
                filling_mode = 1 # FOK
                trade_mode = 4 # FULL
            return MockSymbolInfo()
            
        symbol_info = None
        for attempt in range(3):
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is not None:
                return symbol_info
            err = mt5.last_error()
            if attempt < 2 and isinstance(err, (tuple, list)) and len(err) > 0 and err[0] == -10001:
                time.sleep(0.15 * (attempt + 1))
                continue
            break

        if symbol_info is None:
            logger.error(f"Failed to get symbol info for {symbol}, error code: {mt5.last_error()}")
        return symbol_info

    def get_symbol_tick(self, symbol: str) -> Optional[Any]:
        if self.mock_mode:
            class MockTick:
                ask = 1.10010
                bid = 1.10000
                time = int(time.time())
            return MockTick()

        tick = None
        for attempt in range(3):
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None:
                return tick
            err = mt5.last_error()
            if attempt < 2 and isinstance(err, (tuple, list)) and len(err) > 0 and err[0] == -10001:
                time.sleep(0.15 * (attempt + 1))
                continue
            break

        if tick is None:
            logger.error(f"Failed to get symbol tick for {symbol}, error code: {mt5.last_error()}")
        return tick

    def select_symbol(self, symbol: str, enable: bool = True) -> bool:
        if self.mock_mode:
            return True
        return mt5.symbol_select(symbol, enable)

    def get_rates(self, symbol: str, timeframe: str, count: int = 500) -> Optional[pd.DataFrame]:
        if self.mock_mode:
            return pd.DataFrame()
            
        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1
        }
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)
        
        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
        if rates is None or len(rates) == 0:
            return None
        
        df = pd.DataFrame(rates)
        account = mt5.account_info()
        server = getattr(account, "server", "") if account else ""
        df['time'] = normalize_mt5_bar_times(df['time'], server)
        return df

    def get_rates_range(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
        if self.mock_mode:
            return pd.DataFrame()

        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1
        }
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)

        account = mt5.account_info()
        server = getattr(account, "server", "") if account else ""
        rates = mt5.copy_rates_range(
            symbol, mt5_tf,
            to_mt5_query_time(start, server),
            to_mt5_query_time(end, server),
        )
        if rates is None or len(rates) == 0:
            return None
            
        df = pd.DataFrame(rates)
        df['time'] = normalize_mt5_bar_times(df['time'], server)
        return df

    def get_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> List[Any]:
        if self.mock_mode:
            return []
            
        kwargs = {}
        if symbol: kwargs["symbol"] = symbol
        positions = mt5.positions_get(**kwargs)
        if positions is None:
            return []
        if magic is not None:
            positions = [p for p in positions if p.magic == magic]
        return list(positions)

    def find_position_ticket(self, symbol: str, magic: int, comment: str) -> Optional[int]:
        """Return the open MT5 position matching an idempotency comment."""
        position = self.find_position_by_comment(symbol, magic, comment)
        if position is None:
            return None
        # On hedging accounts multiple positions can exist.  The most recent
        # matching ticket is the safest association for the just-filled order.
        return int(position.ticket)

    def find_position_by_comment(self, symbol: str, magic: int, comment: str) -> Optional[Any]:
        """Return the newest open position matching this bot's comment."""
        matches = [
            position for position in self.get_positions(symbol=symbol, magic=magic)
            if getattr(position, "comment", "") == comment
        ]
        return max(matches, key=lambda item: getattr(item, "time_msc", 0)) if matches else None

    def get_pending_orders(self, magic: Optional[int] = None) -> List[Any]:
        """Return this bot's pending MT5 orders (not positions)."""
        if self.mock_mode:
            return []
        orders = mt5.orders_get()
        if orders is None:
            return []
        if magic is not None:
            orders = [order for order in orders if getattr(order, "magic", None) == magic]
        return list(orders)

    def find_pending_order_by_comment(self, symbol: str, magic: int, comment: str) -> Optional[Any]:
        matches = [
            order for order in self.get_pending_orders(magic=magic)
            if getattr(order, "symbol", None) == symbol and getattr(order, "comment", "") == comment
        ]
        return max(matches, key=lambda item: getattr(item, "time_setup_msc", 0)) if matches else None

    def cancel_pending_order(self, ticket: int) -> Optional[Any]:
        """Cancel a still-pending order by ticket; never closes a position."""
        if self.mock_mode:
            class MockResult:
                retcode = 10009
                order = ticket
                comment = "Mock pending order cancelled"
            return MockResult()
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(ticket),
        }
        return self.execute_with_retry(request)

    def get_historical_order_status(self, ticket: int) -> Optional[str]:
        """Return the terminal state for a pending order that left the book."""
        if self.mock_mode or mt5 is None:
            return None
        try:
            orders = mt5.history_orders_get(ticket=int(ticket))
            if not orders:
                return None
            latest = max(orders, key=lambda order: getattr(order, "time_done_msc", 0))
            state = getattr(latest, "state", None)
            mapping = {
                getattr(mt5, "ORDER_STATE_FILLED", 4): "FILLED",
                getattr(mt5, "ORDER_STATE_CANCELED", 3): "CANCELLED",
                getattr(mt5, "ORDER_STATE_EXPIRED", 5): "EXPIRED",
                getattr(mt5, "ORDER_STATE_REJECTED", 2): "REJECTED",
            }
            return mapping.get(state, f"STATE_{state}")
        except Exception as exc:
            logger.warning("Unable to read MT5 history for pending order %s: %s", ticket, exc)
            return None

    def get_closed_position_result(
        self,
        ticket: int,
        lookback_days: int = 30,
    ) -> Optional[dict]:
        """Read the authoritative close result for an MT5 position.

        Returns None while the position is still open or MT5 history has not
        yet made the close deal available.  Profit includes commission, swap
        and fee, so daily stats match the broker's realised result.
        """
        if self.mock_mode or mt5 is None:
            return None

        try:
            deals = mt5.history_deals_get(position=int(ticket))
            if deals is None:
                start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
                deals = mt5.history_deals_get(start, datetime.now(timezone.utc) + timedelta(days=1))
                if deals is not None:
                    deals = [
                        deal for deal in deals
                        if int(getattr(deal, "position_id", 0)) == int(ticket)
                    ]
            if not deals:
                return None

            entry_in = getattr(mt5, "DEAL_ENTRY_IN", 0)
            exit_deals = [deal for deal in deals if getattr(deal, "entry", entry_in) != entry_in]
            if not exit_deals:
                return None

            last_exit = max(exit_deals, key=lambda deal: getattr(deal, "time_msc", 0))
            net_profit = sum(
                float(getattr(deal, "profit", 0.0))
                + float(getattr(deal, "commission", 0.0))
                + float(getattr(deal, "swap", 0.0))
                + float(getattr(deal, "fee", 0.0))
                for deal in deals
            )
            exit_time = datetime.fromtimestamp(
                int(getattr(last_exit, "time", 0)), tz=timezone.utc
            )
            return {
                "exit_price": float(getattr(last_exit, "price", 0.0)),
                "profit": round(net_profit, 2),
                "exited_at": exit_time,
            }
        except Exception as exc:
            logger.warning("Unable to reconcile MT5 position %s: %s", ticket, exc)
            return None

    def get_position_by_ticket(self, ticket: int) -> Optional[Any]:
        if self.mock_mode:
            return None
            
        positions = mt5.positions_get(ticket=ticket)
        if positions is None or len(positions) == 0:
            return None
        return positions[0]

    def get_filling_mode(self, symbol: str) -> int:
        if self.mock_mode:
            return 0 # ORDER_FILLING_FOK
            
        symbol_info = self.get_symbol_info(symbol)
        if symbol_info is None:
            return mt5.ORDER_FILLING_FOK

        mode = getattr(symbol_info, "filling_mode", 0)
        # In MT5 symbol info: bit 0 (1) = FOK supported, bit 1 (2) = IOC supported
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def calc_margin(self, order_type: int, symbol: str, volume: float, price: float) -> Optional[float]:
        if self.mock_mode:
            return volume * price * 0.01 # fake margin

        margin = mt5.order_calc_margin(order_type, symbol, volume, price)
        if margin is None:
            logger.error(f"Failed to calculate margin: {mt5.last_error()}")
        return margin

    def calc_profit(self, order_type: int, symbol: str, volume: float, price_open: float, price_close: float) -> Optional[float]:
        if self.mock_mode:
            diff = (price_close - price_open) if order_type == 0 else (price_open - price_close)
            return diff * volume * 100000

        profit = mt5.order_calc_profit(order_type, symbol, volume, price_open, price_close)
        if profit is None:
            logger.error(f"Failed to calculate profit: {mt5.last_error()}")
        return profit

    def send_order(self, request: dict) -> Any:
        if self.mock_mode:
            class MockResult:
                retcode = 10009
                order = 12345
                price = request.get('price', 0.0)
                volume = request.get('volume', 0.0)
                comment = "Mock order sent"
            return MockResult()
            
        return mt5.order_send(request)

    def check_order(self, request: dict) -> Any:
        if self.mock_mode:
            class MockCheckResult:
                retcode = 0
                margin = 100.0
                margin_free = 9900.0
                profit = 0.0
                comment = "Mock check passed"
            return MockCheckResult()

        return mt5.order_check(request)

    def modify_position(self, ticket: int, sl: Optional[float] = None, tp: Optional[float] = None) -> Any:
        if self.mock_mode:
            class MockResult:
                retcode = 10009
                order = ticket
                comment = "Mock modify sent"
            return MockResult()

        position = self.get_position_by_ticket(ticket)
        if position is None:
            logger.error(f"Position {ticket} not found for modification.")
            return None

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": position.symbol,
        }
        
        if sl is not None: request["sl"] = sl
        else: request["sl"] = position.sl
        
        if tp is not None: request["tp"] = tp
        else: request["tp"] = position.tp

        return self.execute_with_retry(request)

    def close_position(self, ticket: int, volume: Optional[float] = None, symbol: Optional[str] = None) -> Any:
        if self.mock_mode:
            class MockResult:
                retcode = 10009
                order = ticket
                comment = "Mock close sent"
            return MockResult()
            
        position = self.get_position_by_ticket(ticket)
        if position is None:
            logger.error(f"Position {ticket} not found for closing.")
            return None

        sym = symbol or position.symbol
        vol = volume or position.volume
        order_type = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        
        tick = self.get_symbol_tick(sym)
        if tick is None:
            return None
            
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": sym,
            "volume": float(vol),
            "type": order_type,
            "price": float(price),
            "deviation": 20,
            "magic": position.magic,
            "comment": "Close Position",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self.get_filling_mode(sym),
        }
        
        return self.execute_with_retry(request)

    def execute_with_retry(self, request: dict, max_retries: int = 3, delay: float = 0.5) -> Optional[Any]:
        if self.mock_mode:
            return self.send_order(request)

        retryable_errors = [10004, 10012, 10025, 10026] # REQUOTE, PRICE_OFF, NO_MONEY, BROKER_BUSY

        for attempt in range(max_retries):
            # Refresh price on retry if it's a market order
            if attempt > 0 and request.get("action") == mt5.TRADE_ACTION_DEAL:
                tick = self.get_symbol_tick(request["symbol"])
                if tick:
                    request["price"] = tick.ask if request["type"] == mt5.ORDER_TYPE_BUY else tick.bid
                    
            result = mt5.order_send(request)
            if result is None:
                logger.error(f"order_send failed unconditionally, code: {mt5.last_error()}")
                return None
                
            # TRADE_RETCODE_PLACED is a successful acknowledgement for a
            # pending order. Retrying it would submit duplicate stop orders.
            successful_retcodes = {
                getattr(mt5, "TRADE_RETCODE_DONE", 10009),
                getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
                10009,
                10008,
            }
            if result.retcode in successful_retcodes:
                return result
                
            if result.retcode in retryable_errors:
                logger.warning(f"Retryable error {result.retcode}, attempt {attempt + 1}/{max_retries}...")
                time.sleep(delay)
                continue
                
            if result.retcode == 10018 or "market closed" in (result.comment or "").lower():
                logger.debug(f"Order action rejected because market is closed: {result.comment}")
                return result

            logger.error(f"Order failed with unrecoverable code {result.retcode}: {result.comment}")
            return result
            
        return result
