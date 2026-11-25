"""11 Pre-execution safety and validation checks."""
import logging
from typing import Tuple, List, Optional, Any

from core.models import TradeSetup
from core.constants import BotMode, Direction
from core.config_loader import AppConfig, SymbolConfig
from execution.mt5_connector import MT5Connector
from filters.context_filter import ContextFilter

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = logging.getLogger(__name__)


class ExecutionGuard:
    """Validates trade setups against hard safety rules before execution."""

    def __init__(self, config: Optional[AppConfig] = None, repository: Optional[Any] = None):
        self.config = config
        self.repository = repository

    def validate_pre_execution(
        self,
        trade_setup: TradeSetup,
        connector: MT5Connector,
        context_filter: Optional[ContextFilter] = None,
        symbol_config: Optional[SymbolConfig] = None
    ) -> Tuple[bool, List[str]]:
        """Run all safety checks before a signal may reserve an order slot."""
        reasons = []

        symbol = trade_setup.signal.symbol
        direction = trade_setup.signal.direction

        # 1. MT5 Connected?
        if not connector.is_connected():
            reasons.append("MT5 is not connected.")
            return False, reasons

        # 2. The configured route must match the actual connected account.
        # This is deliberately checked here *and* in OrderManager so a future
        # caller cannot bypass the orchestrator's guard.
        if self.config:
            mode = self.config.bot.mode
            mode_value = getattr(mode, "value", mode)
            mode_value = str(mode_value).lower()
            if mode_value in {BotMode.DEMO.value, BotMode.LIVE.value}:
                account_ok, account_reason = connector.validate_execution_account(
                    mode=mode_value,
                    allow_live_trading=self.config.bot.allow_live_trading,
                )
                if not account_ok:
                    reasons.append(account_reason)
                    logger.debug("[%s] Pre-execution account route failed: %s", symbol, account_reason)
                    return False, reasons

        # 3. Symbol available & selected in Market Watch?
        symbol_info = connector.get_symbol_info(symbol)
        if symbol_info is None:
            reasons.append(f"Symbol '{symbol}' not found or not available.")
            return False, reasons

        if not getattr(symbol_info, "visible", True):
            if not connector.select_symbol(symbol, True):
                reasons.append(f"Symbol '{symbol}' could not be selected in Market Watch.")

        # 4. Market open / trade mode allowed (full trading)?
        if symbol_info.trade_mode != (mt5.SYMBOL_TRADE_MODE_FULL if mt5 else 4):
            reasons.append(f"Trade mode for '{symbol}' is not FULL.")

        # 5. Tick quotes & spread
        tick = connector.get_symbol_tick(symbol)
        if not tick or tick.bid <= 0 or tick.ask <= 0:
            reasons.append(f"Invalid live tick price for '{symbol}'.")
        else:
            spread = tick.ask - tick.bid
            point = symbol_info.point if symbol_info.point > 0 else 0.00001
            spread_points = spread / point
            max_spread = symbol_config.max_spread_points if symbol_config and symbol_config.max_spread_points else 50.0
            if spread_points > max_spread:
                reasons.append(f"Spread {spread_points:.1f} pts exceeds maximum allowed ({max_spread:.1f} pts).")

        # 6. Margin sufficient
        acc_info = connector.get_account_info()
        order_type = (mt5.ORDER_TYPE_BUY if mt5 else 0) if direction == Direction.BUY else (mt5.ORDER_TYPE_SELL if mt5 else 1)
        price = tick.ask if direction == Direction.BUY else tick.bid if tick else trade_setup.entry_price

        margin_req = connector.calc_margin(order_type, symbol, trade_setup.volume, price)
        if margin_req and acc_info:
            if margin_req > acc_info.margin_free:
                reasons.append(f"Insufficient margin. Required: ${margin_req:,.2f}, Free: ${acc_info.margin_free:,.2f}")

        # 7. Volume valid.  A zero volume is an intentional risk stop and
        # must never be converted into the broker's minimum volume.
        vol = trade_setup.volume
        if vol <= 0:
            reasons.append("Risk budget does not permit a tradable volume.")
        elif vol < symbol_info.volume_min or vol > symbol_info.volume_max:
            reasons.append(f"Volume {vol} out of bounds [{symbol_info.volume_min}, {symbol_info.volume_max}].")

        # 8 & 9. SL and TP valid, including the broker's stops-level.  A
        # pending breakout is validated against its trigger price, not merely
        # against the current quote.
        stoplevel = (symbol_info.trade_stops_level * symbol_info.point) if symbol_info else 0.0001
        sl = trade_setup.sl_price
        tp = trade_setup.tp_price
        entry = trade_setup.entry_price

        if trade_setup.is_pending and not trade_setup.expiration:
            reasons.append("Pending breakout order has no expiration.")

        if trade_setup.is_pending and direction == Direction.BUY:
            if tick and entry <= tick.ask:
                reasons.append("Buy breakout trigger must be above current ask.")
            if sl >= entry:
                reasons.append("Buy breakout Stop Loss must be below its trigger.")
            if tp <= entry:
                reasons.append("Buy breakout Take Profit must be above its trigger.")
            if tick and stoplevel > 0 and (entry - tick.ask) < stoplevel:
                reasons.append("Buy breakout trigger is inside the broker minimum stops-level.")
        elif trade_setup.is_pending and direction == Direction.SELL:
            if tick and entry >= tick.bid:
                reasons.append("Sell breakout trigger must be below current bid.")
            if sl <= entry:
                reasons.append("Sell breakout Stop Loss must be above its trigger.")
            if tp >= entry:
                reasons.append("Sell breakout Take Profit must be below its trigger.")
            if tick and stoplevel > 0 and (tick.bid - entry) < stoplevel:
                reasons.append("Sell breakout trigger is inside the broker minimum stops-level.")
        elif direction == Direction.BUY:
            if tick and sl >= tick.bid:
                reasons.append("Buy Stop Loss must be strictly below current bid.")
            if tick and tp > 0 and tp <= tick.bid:
                reasons.append("Buy Take Profit must be strictly above current bid.")
            if tick and stoplevel > 0 and (tick.bid - sl) < stoplevel:
                reasons.append("Buy Stop Loss is inside the broker minimum stops-level.")
            if tick and tp > 0 and stoplevel > 0 and (tp - tick.bid) < stoplevel:
                reasons.append("Buy Take Profit is inside the broker minimum stops-level.")
        else:
            if tick and sl <= tick.ask:
                reasons.append("Sell Stop Loss must be strictly above current ask.")
            if tick and tp > 0 and tp >= tick.ask:
                reasons.append("Sell Take Profit must be strictly below current ask.")
            if tick and stoplevel > 0 and (sl - tick.ask) < stoplevel:
                reasons.append("Sell Stop Loss is inside the broker minimum stops-level.")
            if tick and tp > 0 and stoplevel > 0 and (tick.ask - tp) < stoplevel:
                reasons.append("Sell Take Profit is inside the broker minimum stops-level.")

        # 10. Score sufficient
        score = trade_setup.signal.score_result
        min_total = self.config.score.minimum if self.config else 75.0
        min_struct = self.config.score.min_structure if self.config else 18.0
        min_trend = self.config.score.min_trend if self.config else 15.0

        if score.total_score < min_total:
            reasons.append(f"Total score {score.total_score} below threshold {min_total}.")
        if score.structure.score < min_struct:
            reasons.append(f"Structure score {score.structure.score} below threshold {min_struct}.")
        if score.trend.score < min_trend:
            reasons.append(f"Trend score {score.trend.score} below threshold {min_trend}.")

        # 11. Position-count limits are rechecked immediately before the
        # atomic signal reservation to close the race between symbol loops.
        if self.config:
            open_positions = connector.get_positions(magic=self.config.bot.magic_number)
            if len(open_positions) >= self.config.risk.max_open_positions:
                reasons.append(
                    f"Maximum open bot positions ({self.config.risk.max_open_positions}) reached."
                )
            symbol_count = sum(
                1 for position in open_positions
                if getattr(position, "symbol", None) == symbol
            )
            if symbol_count >= self.config.risk.max_positions_per_symbol:
                reasons.append(
                    f"Maximum positions for {symbol} ({self.config.risk.max_positions_per_symbol}) reached."
                )

        # 12. Deduplication check.  The repository is SignalRepository, whose
        # unique signal_id constraint is the durable source of truth.
        if self.repository and hasattr(self.repository, "is_signal_processed"):
            if self.repository.is_signal_processed(trade_setup.signal.signal_id):
                reasons.append(f"Signal '{trade_setup.signal.signal_id}' was already processed.")

        if reasons:
            logger.debug(f"[{symbol}] Pre-execution check failed: {', '.join(reasons)}")
            return False, reasons

        return True, []
