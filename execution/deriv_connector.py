"""Deriv MT5 Connector for Synthetic Indices.

Specialized connector for Deriv (formerly Binary.com) MetaTrader 5 accounts,
handling 24/7 continuous synthetic markets, Boom/Crash spike dynamics,
and precise lot/contract specifications.
"""
from dataclasses import dataclass
import logging
import math
import time
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd

from execution.mt5_connector import MT5Connector, MT5_AVAILABLE
from core.constants import Direction

if MT5_AVAILABLE:
    import MetaTrader5 as mt5

logger = logging.getLogger("DerivConnector")


@dataclass
class DerivSymbolSpec:
    """Contract specifications for a Deriv synthetic index."""
    symbol: str
    display_name: str
    category: str  # "volatility", "boom_crash", "step", "jump"
    min_volume: float
    max_volume: float
    volume_step: float
    point: float
    digits: int
    contract_size: float
    tick_size: float
    tick_value: float
    spike_direction: Optional[str] = None  # "UP" for Boom, "DOWN" for Crash, None for Volatility
    stops_level: int = 0
    min_stop_distance: float = 0.0


# Standard contract specifications for Deriv Synthetic Indices (fallback / offline reference)
DERIV_SYNTHETIC_SPECS: Dict[str, DerivSymbolSpec] = {
    # Volatility Continuous Indices
    "Volatility 75 Index": DerivSymbolSpec(
        symbol="Volatility 75 Index", display_name="Volatility 75", category="volatility",
        min_volume=0.001, max_volume=20.0, volume_step=0.001, point=0.01, digits=2,
        contract_size=1.0, tick_size=0.01, tick_value=0.01,
    ),
    "Volatility 75 (1s) Index": DerivSymbolSpec(
        symbol="Volatility 75 (1s) Index", display_name="Volatility 75 (1s)", category="volatility",
        min_volume=0.005, max_volume=20.0, volume_step=0.001, point=0.01, digits=2,
        contract_size=1.0, tick_size=0.01, tick_value=0.01,
    ),
    "Volatility 100 Index": DerivSymbolSpec(
        symbol="Volatility 100 Index", display_name="Volatility 100", category="volatility",
        min_volume=0.20, max_volume=50.0, volume_step=0.01, point=0.01, digits=2,
        contract_size=1.0, tick_size=0.01, tick_value=0.01,
    ),
    "Volatility 50 Index": DerivSymbolSpec(
        symbol="Volatility 50 Index", display_name="Volatility 50", category="volatility",
        min_volume=3.0, max_volume=300.0, volume_step=0.1, point=0.0001, digits=4,
        contract_size=1.0, tick_size=0.0001, tick_value=0.0001,
    ),
    "Volatility 25 Index": DerivSymbolSpec(
        symbol="Volatility 25 Index", display_name="Volatility 25", category="volatility",
        min_volume=0.50, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001,
    ),
    "Volatility 10 Index": DerivSymbolSpec(
        symbol="Volatility 10 Index", display_name="Volatility 10", category="volatility",
        min_volume=0.30, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001,
    ),
    # Boom Indices (Spikes UP)
    "Boom 1000 Index": DerivSymbolSpec(
        symbol="Boom 1000 Index", display_name="Boom 1000", category="boom_crash",
        min_volume=0.20, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001, spike_direction="UP",
    ),
    "Boom 500 Index": DerivSymbolSpec(
        symbol="Boom 500 Index", display_name="Boom 500", category="boom_crash",
        min_volume=0.20, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001, spike_direction="UP",
    ),
    "Boom 300 Index": DerivSymbolSpec(
        symbol="Boom 300 Index", display_name="Boom 300", category="boom_crash",
        min_volume=0.20, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001, spike_direction="UP",
    ),
    # Crash Indices (Spikes DOWN)
    "Crash 1000 Index": DerivSymbolSpec(
        symbol="Crash 1000 Index", display_name="Crash 1000", category="boom_crash",
        min_volume=0.20, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001, spike_direction="DOWN",
    ),
    "Crash 500 Index": DerivSymbolSpec(
        symbol="Crash 500 Index", display_name="Crash 500", category="boom_crash",
        min_volume=0.20, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001, spike_direction="DOWN",
    ),
    "Crash 300 Index": DerivSymbolSpec(
        symbol="Crash 300 Index", display_name="Crash 300", category="boom_crash",
        min_volume=0.20, max_volume=100.0, volume_step=0.01, point=0.001, digits=3,
        contract_size=1.0, tick_size=0.001, tick_value=0.001, spike_direction="DOWN",
    ),
    # Step Index
    "Step Index": DerivSymbolSpec(
        symbol="Step Index", display_name="Step Index", category="step",
        min_volume=0.10, max_volume=50.0, volume_step=0.10, point=0.10, digits=1,
        contract_size=10.0, tick_size=0.10, tick_value=1.0,
    ),
}

# Alias map to resolve short names or broker-specific variations
DERIV_SYMBOL_ALIASES: Dict[str, str] = {
    # Volatility aliases
    "v75": "Volatility 75 Index",
    "v75_1s": "Volatility 75 (1s) Index",
    "1hz75v": "Volatility 75 (1s) Index",
    "r_75": "Volatility 75 Index",
    "v100": "Volatility 100 Index",
    "r_100": "Volatility 100 Index",
    "v50": "Volatility 50 Index",
    "r_50": "Volatility 50 Index",
    "v25": "Volatility 25 Index",
    "r_25": "Volatility 25 Index",
    "v10": "Volatility 10 Index",
    "r_10": "Volatility 10 Index",
    # Boom / Crash aliases
    "b1000": "Boom 1000 Index",
    "boom1000": "Boom 1000 Index",
    "b500": "Boom 500 Index",
    "boom500": "Boom 500 Index",
    "b300": "Boom 300 Index",
    "boom300": "Boom 300 Index",
    "c1000": "Crash 1000 Index",
    "crash1000": "Crash 1000 Index",
    "c500": "Crash 500 Index",
    "crash500": "Crash 500 Index",
    "c300": "Crash 300 Index",
    "crash300": "Crash 300 Index",
    # Step index
    "step": "Step Index",
}


class DerivConnector(MT5Connector):
    """
    Enhanced MT5 Connector with native support for Deriv Synthetic Indices.
    """

    def __init__(self, mock_mode: bool = False, server: str = "Deriv-Demo"):
        super().__init__(mock_mode=mock_mode)
        self.preferred_server = server
        self._specs_cache: Dict[str, DerivSymbolSpec] = {}

    def connect_deriv(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
    ) -> bool:
        """Connect specifically to a Deriv MT5 account (Demo or Real)."""
        target_server = server or self.preferred_server
        logger.info(f"Connecting to Deriv MT5 (Server: {target_server})...")

        if self.mock_mode:
            logger.info("DerivConnector operating in MOCK / Simulation mode.")
            self.connected = True
            return True

        # First, attempt initialization with provided credentials
        if login and password:
            ok = self.initialize(path=path, login=login, password=password, server=target_server)
            if ok:
                logger.info(f"Successfully logged into Deriv account #{login} on {target_server}.")
                return True
            logger.warning(f"Direct login to {target_server} failed. Checking existing terminal session...")

        # If already connected to an MT5 terminal, check if it's connected
        if not self.connected:
            ok = self.initialize(path=path)
            if not ok:
                logger.error("Could not initialize local MT5 terminal.")
                return False

        account = self.get_account_info()
        if account:
            logger.info(f"Active MT5 session: #{account.login} on '{account.server}'.")
            return True

        return False

    def resolve_symbol(self, requested_symbol: str) -> str:
        """Resolve symbol string to the exact broker symbol name."""
        clean = requested_symbol.strip()
        lower_clean = clean.lower()

        # Check alias
        if lower_clean in DERIV_SYMBOL_ALIASES:
            clean = DERIV_SYMBOL_ALIASES[lower_clean]

        if not MT5_AVAILABLE or self.mock_mode:
            return clean

        # If connected to MT5, check if symbol exists directly
        info = mt5.symbol_info(clean)
        if info:
            return clean

        # Search matching symbol in terminal
        symbols = mt5.symbols_get()
        if symbols:
            # Exact case-insensitive match
            for s in symbols:
                if s.name.lower() == lower_clean or s.name.lower() == clean.lower():
                    return s.name
            # Partial substring match (e.g. "Volatility 75" matching "Volatility 75 Index")
            for s in symbols:
                if lower_clean in s.name.lower() or s.name.lower() in lower_clean:
                    return s.name

        return clean

    def get_symbol_specs(self, symbol: str) -> DerivSymbolSpec:
        """
        Get detailed contract specs, dynamically querying MT5 or falling back
        to calibrated Deriv specification tables.
        """
        resolved = self.resolve_symbol(symbol)
        if resolved in self._specs_cache:
            return self._specs_cache[resolved]

        # Base fallback spec
        fallback = DERIV_SYNTHETIC_SPECS.get(
            resolved,
            DERIV_SYNTHETIC_SPECS.get(
                symbol,
                DerivSymbolSpec(
                    symbol=resolved,
                    display_name=resolved,
                    category="volatility",
                    min_volume=0.01,
                    max_volume=50.0,
                    volume_step=0.01,
                    point=0.01,
                    digits=2,
                    contract_size=1.0,
                    tick_size=0.01,
                    tick_value=0.01,
                )
            )
        )

        if MT5_AVAILABLE and not self.mock_mode:
            info = mt5.symbol_info(resolved)
            if info:
                spec = DerivSymbolSpec(
                    symbol=resolved,
                    display_name=info.description or fallback.display_name,
                    category=fallback.category,
                    min_volume=float(info.volume_min) if info.volume_min > 0 else fallback.min_volume,
                    max_volume=float(info.volume_max) if info.volume_max > 0 else fallback.max_volume,
                    volume_step=float(info.volume_step) if info.volume_step > 0 else fallback.volume_step,
                    point=float(info.point) if info.point > 0 else fallback.point,
                    digits=int(info.digits) if info.digits > 0 else fallback.digits,
                    contract_size=float(info.trade_contract_size) if info.trade_contract_size > 0 else fallback.contract_size,
                    tick_size=float(info.trade_tick_size) if info.trade_tick_size > 0 else fallback.tick_size,
                    tick_value=float(info.trade_tick_value) if info.trade_tick_value > 0 else fallback.tick_value,
                    spike_direction=fallback.spike_direction,
                    stops_level=int(getattr(info, "trade_stops_level", 0)),
                    min_stop_distance=float(getattr(info, "trade_stops_level", 0)) * (float(info.point) if info.point > 0 else fallback.point),
                )
                self._specs_cache[resolved] = spec
                return spec

        self._specs_cache[resolved] = fallback
        return fallback

    def get_spike_direction(self, symbol: str) -> Optional[Direction]:
        """Return Direction.BUY for Boom, Direction.SELL for Crash, or None."""
        spec = self.get_symbol_specs(symbol)
        if spec.spike_direction == "UP":
            return Direction.BUY
        elif spec.spike_direction == "DOWN":
            return Direction.SELL
        return None

    def calculate_lot_size(
        self,
        symbol: str,
        risk_money: float,
        sl_distance_price: float,
    ) -> float:
        """
        Calculate the exact lot size for a Deriv synthetic index based on risk money.
        Guarantees that lot size complies with Deriv's volume_min, volume_max, and volume_step.
        """
        spec = self.get_symbol_specs(symbol)
        min_stop_required = max(spec.min_stop_distance * 1.25, spec.point * 10.0)
        effective_sl_distance = max(sl_distance_price, min_stop_required)
        if effective_sl_distance <= 0:
            return spec.min_volume

        # Risk formula: Risk Money = Lot * PriceDistance * ContractSize / Point * TickValue (or Lot * Distance * ContractSize)
        # On Deriv, 1 lot of V75 with a 10.0 point SL distance = 10 * 1.0 * Lot
        value_per_point_per_lot = spec.contract_size if spec.contract_size > 0 else 1.0
        
        raw_lot = risk_money / (effective_sl_distance * value_per_point_per_lot)

        # Round down to volume step
        step = spec.volume_step
        if step > 0:
            steps_count = math.floor(raw_lot / step)
            rounded_lot = round(steps_count * step, 6)
        else:
            rounded_lot = raw_lot

        # Clamp between min and max volume
        clamped_lot = max(spec.min_volume, min(rounded_lot, spec.max_volume))
        
        # Round decimals according to step digits
        step_str = f"{step:.8f}".rstrip("0")
        decimals = len(step_str.split(".")[1]) if "." in step_str else 0
        return round(clamped_lot, decimals)

    def send_order(
        self,
        symbol: Optional[str] = None,
        order_type: Optional[str] = None,
        volume: Optional[float] = None,
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        magic: int = 0,
        comment: str = "",
        request: Optional[dict] = None,
    ) -> Any:
        """
        Send an order to MT5. Supports both dict request and keyword arguments.
        """
        if request is not None:
            return super().send_order(request)

        if self.mock_mode or not MT5_AVAILABLE:
            class MockResult:
                retcode = 10009
                order = int(time.time())
                price = price or 0.0
                volume = volume or 0.0
                comment = "Mock order sent"
            return MockResult()

        resolved_symbol = self.resolve_symbol(symbol) if symbol else ""
        self.select_symbol(resolved_symbol, True)
        tick = self.get_symbol_tick(resolved_symbol)
        
        is_buy = str(order_type).upper() in ("BUY", "0")
        action_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        curr_price = float(tick.ask if is_buy else tick.bid) if tick else float(price or 0.0)

        spec = self.get_symbol_specs(resolved_symbol)
        min_stop = max(spec.min_stop_distance, spec.point * 10)
        
        sl_val = float(sl) if sl else 0.0
        tp_val = float(tp) if tp else 0.0

        bid_price = float(tick.bid) if tick else curr_price
        ask_price = float(tick.ask) if tick else curr_price

        # In MT5: BUY stops are evaluated against BID; SELL stops are evaluated against ASK
        # Strictly ensure stops are on the correct side of the market price
        if is_buy:
            if sl_val > 0 and sl_val >= bid_price:
                logger.error(f"[INVALID STOPS] BUY order on {resolved_symbol} has SL ({sl_val}) >= Bid ({bid_price}). Aborting order.")
                return None
            if tp_val > 0 and tp_val <= bid_price:
                logger.error(f"[INVALID STOPS] BUY order on {resolved_symbol} has TP ({tp_val}) <= Bid ({bid_price}). Aborting order.")
                return None
            if sl_val > 0 and (bid_price - sl_val) < min_stop:
                sl_val = round(bid_price - (min_stop * 1.25), spec.digits)
            if tp_val > 0 and (tp_val - bid_price) < min_stop:
                tp_val = round(bid_price + (min_stop * 1.25), spec.digits)
        else:
            if sl_val > 0 and sl_val <= ask_price:
                logger.error(f"[INVALID STOPS] SELL order on {resolved_symbol} has SL ({sl_val}) <= Ask ({ask_price}). Aborting order.")
                return None
            if tp_val > 0 and tp_val >= ask_price:
                logger.error(f"[INVALID STOPS] SELL order on {resolved_symbol} has TP ({tp_val}) >= Ask ({ask_price}). Aborting order.")
                return None
            if sl_val > 0 and (sl_val - ask_price) < min_stop:
                sl_val = round(ask_price + (min_stop * 1.25), spec.digits)
            if tp_val > 0 and (ask_price - tp_val) < min_stop:
                tp_val = round(ask_price - (min_stop * 1.25), spec.digits)

        filling_mode = self.get_filling_mode(resolved_symbol)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": resolved_symbol,
            "volume": float(volume),
            "type": action_type,
            "price": curr_price,
            "sl": sl_val,
            "tp": tp_val,
            "deviation": 20,
            "magic": int(magic),
            "comment": str(comment),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        res = self.execute_with_retry(req)
        if res and res.retcode != 10009 and res.retcode != 10008:
            logger.warning(f"Order status on {resolved_symbol}: retcode={res.retcode}, comment={getattr(res, 'comment', '')}")
        return res

    def modify_order(self, ticket: int, sl: Optional[float] = None, tp: Optional[float] = None) -> Any:
        """Alias for modify_position to update SL/TP."""
        return self.modify_position(ticket, sl=sl, tp=tp)
