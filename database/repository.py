"""Repository for database CRUD operations."""
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import logging

from sqlalchemy import desc, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.database import Database
from database.models import TradeDB, DailyStatsDB, SignalLogDB, DecisionContextDB, RuntimeStateDB
from core.models import TradeRecord, ScoreResult, Signal
from core.constants import TradeState

logger = logging.getLogger(__name__)


class TradeRepository:
    """Handles trade persistence operations."""
    
    def __init__(self, database: Database):
        self.db = database
    
    def save_trade(self, trade: TradeRecord) -> None:
        """Persist an opened trade idempotently by signal and broker ticket."""
        try:
            with self.db.session() as session:
                db_trade = session.query(TradeDB).filter_by(signal_id=trade.signal_id).first()
                if db_trade is None and trade.ticket is not None:
                    db_trade = session.query(TradeDB).filter_by(ticket=trade.ticket).first()

                fields = {
                    "signal_id": trade.signal_id,
                    "symbol": trade.symbol,
                    "timeframe": trade.timeframe,
                    "direction": trade.direction.value,
                    "state": trade.state.value,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "sl_price": trade.sl_price,
                    "tp_price": trade.tp_price,
                    "volume": trade.volume,
                    "created_at": trade.created_at,
                    "entered_at": trade.entered_at,
                    "exited_at": trade.exited_at,
                    "duration_seconds": trade.duration_seconds,
                    "total_score": trade.all_scores.total_score,
                    "structure_score": trade.all_scores.structure.score,
                    "trend_score": trade.all_scores.trend.score,
                    "momentum_score": trade.all_scores.momentum.score,
                    "volume_score": trade.all_scores.volume.score,
                    "volatility_score": trade.all_scores.volatility.score,
                    "spread_at_entry": trade.spread_at_entry,
                    "atr_value": trade.atr_value,
                    "rsi_value": trade.rsi_value,
                    "adx_value": trade.adx_value,
                    "macd_value": trade.macd_value,
                    "market_regime": trade.market_regime.value if trade.market_regime else None,
                    "risk_percent": trade.risk_percent,
                    "risk_money": trade.risk_money,
                    "profit": trade.profit,
                    "profit_r": trade.profit_r,
                    "features_at_entry": trade.features_at_entry,
                    "ticket": trade.ticket,
                    "magic_number": trade.magic_number,
                    "comment": trade.comment,
                }
                if db_trade is None:
                    db_trade = TradeDB(trade_id=str(trade.trade_id), **fields)
                    session.add(db_trade)
                else:
                    for key, value in fields.items():
                        setattr(db_trade, key, value)
                logger.info(f"Trade saved: {trade.trade_id} {trade.symbol} {trade.direction.value}")
        except Exception as e:
            logger.error(f"Error saving trade: {e}")
            raise
    
    def update_trade(self, trade_id: str, **kwargs) -> None:
        """Update specific fields of an existing trade."""
        try:
            with self.db.session() as session:
                db_trade = session.query(TradeDB).filter_by(trade_id=trade_id).first()
                if db_trade:
                    for key, value in kwargs.items():
                        if hasattr(db_trade, key):
                            setattr(db_trade, key, value)
                    logger.debug(f"Trade updated: {trade_id}")
                else:
                    logger.warning(f"Trade not found for update: {trade_id}")
        except Exception as e:
            logger.error(f"Error updating trade {trade_id}: {e}")
            raise
    
    def get_trade_by_id(self, trade_id: str) -> Optional[TradeDB]:
        """Retrieve a trade by its ID."""
        try:
            with self.db.session() as session:
                return session.query(TradeDB).filter_by(trade_id=trade_id).first()
        except Exception as e:
            logger.error(f"Error retrieving trade {trade_id}: {e}")
            return None
    
    def get_trades_by_date_range(self, start: datetime, end: datetime) -> List[TradeDB]:
        """Get trades within a date range."""
        try:
            with self.db.session() as session:
                return session.query(TradeDB).filter(
                    TradeDB.created_at >= start,
                    TradeDB.created_at <= end
                ).order_by(TradeDB.created_at).all()
        except Exception as e:
            logger.error(f"Error retrieving trades by date range: {e}")
            return []
    
    def get_open_trades(self) -> List[TradeDB]:
        """Get all currently open trades."""
        open_states = [TradeState.OPEN.value, TradeState.BREAK_EVEN.value, 
                       TradeState.PARTIAL.value, TradeState.TRAILING.value]
        try:
            with self.db.session() as session:
                return session.query(TradeDB).filter(
                    TradeDB.state.in_(open_states)
                ).all()
        except Exception as e:
            logger.error(f"Error retrieving open trades: {e}")
            return []

    def get_pending_trades(self) -> List[TradeDB]:
        """Return local pending entries that must be reconciled with MT5."""
        try:
            with self.db.session() as session:
                return session.query(TradeDB).filter(
                    TradeDB.state == TradeState.ORDER_PENDING.value
                ).all()
        except Exception as exc:
            logger.error("Error retrieving pending trades: %s", exc)
            return []

    def get_all_closed_trades(self, limit: int = 100, symbol: Optional[str] = None) -> List[TradeDB]:
        """Retrieve all closed trades ordered by execution time descending, optionally filtered by symbol."""
        try:
            with self.db.session() as session:
                query = session.query(TradeDB).filter(
                    TradeDB.state == TradeState.CLOSED.value
                )
                if symbol:
                    query = query.filter(TradeDB.symbol == symbol)
                return query.order_by(TradeDB.created_at.desc()).limit(limit).all()
        except Exception as e:
            logger.error(f"Error retrieving closed trades: {e}")
            return []

    def get_cumulative_stats(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Compute all-time cumulative statistics across closed trades, optionally filtered by symbol."""
        try:
            with self.db.session() as session:
                query = session.query(TradeDB).filter(
                    TradeDB.state == TradeState.CLOSED.value
                )
                if symbol:
                    query = query.filter(TradeDB.symbol == symbol)
                closed_trades = query.all()

                total_count = len(closed_trades)
                if total_count == 0:
                    return {
                        "total_trades": 0,
                        "winning_trades": 0,
                        "losing_trades": 0,
                        "net_pnl": 0.0,
                        "gross_profit": 0.0,
                        "gross_loss": 0.0,
                        "profit_factor": 0.0,
                        "win_rate": 0.0,
                        "best_trade": 0.0,
                        "worst_trade": 0.0,
                        "avg_r": 0.0,
                    }

                profits = [float(t.profit) for t in closed_trades if t.profit is not None]
                r_values = [float(t.profit_r) for t in closed_trades if t.profit_r is not None]

                winning = [p for p in profits if p > 0]
                losing = [p for p in profits if p < 0]

                gross_profit = sum(winning)
                gross_loss = abs(sum(losing))
                net_pnl = gross_profit - gross_loss

                profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
                win_rate = (len(winning) / total_count * 100.0) if total_count > 0 else 0.0
                best_trade = max(profits) if profits else 0.0
                worst_trade = min(profits) if profits else 0.0
                avg_r = (sum(r_values) / len(r_values)) if r_values else 0.0

                return {
                    "total_trades": total_count,
                    "winning_trades": len(winning),
                    "losing_trades": len(losing),
                    "net_pnl": round(net_pnl, 2),
                    "gross_profit": round(gross_profit, 2),
                    "gross_loss": round(gross_loss, 2),
                    "profit_factor": round(min(profit_factor, 999.0), 2),
                    "win_rate": round(win_rate, 1),
                    "best_trade": round(best_trade, 2),
                    "worst_trade": round(worst_trade, 2),
                    "avg_r": round(avg_r, 3),
                }
        except Exception as e:
            logger.error(f"Error computing cumulative stats: {e}")
            return {
                "total_trades": 0,
                "net_pnl": 0.0,
                "profit_factor": 0.0,
                "win_rate": 0.0,
            }

    def mark_pending_filled(self, order_ticket: int, position: object) -> bool:
        """Promote a pending journal row when its MT5 position appears."""
        try:
            with self.db.session() as session:
                trade = session.query(TradeDB).filter_by(ticket=int(order_ticket)).first()
                if trade is None or trade.state != TradeState.ORDER_PENDING.value:
                    return False
                trade.state = TradeState.OPEN.value
                trade.ticket = int(getattr(position, "ticket"))
                trade.entry_price = float(getattr(position, "price_open", trade.entry_price))
                trade.volume = float(getattr(position, "volume", trade.volume))
                opened_at = int(getattr(position, "time", 0) or 0)
                trade.entered_at = datetime.fromtimestamp(opened_at, tz=timezone.utc) if opened_at else datetime.now(timezone.utc)
                return True
        except Exception as exc:
            logger.error("Failed to mark pending order %s filled: %s", order_ticket, exc)
            return False

    def mark_pending_cancelled(self, ticket: int, reason: str) -> bool:
        """Close the local lifecycle of a broker-cancelled/expired order."""
        try:
            with self.db.session() as session:
                trade = session.query(TradeDB).filter_by(ticket=int(ticket)).first()
                if trade is None or trade.state != TradeState.ORDER_PENDING.value:
                    return False
                trade.state = TradeState.CANCELLED.value
                suffix = f"PENDING_{reason}"
                trade.comment = f"{trade.comment or ''} | {suffix}".strip(" |")
                return True
        except Exception as exc:
            logger.error("Failed to mark pending order %s cancelled: %s", ticket, exc)
            return False

    def reconcile_open_positions(self, positions: List[object], magic_number: int) -> List[str]:
        """Import bot positions that exist in MT5 but not in local storage.

        MT5 is the authoritative source after a crash.  Reconciled records
        intentionally carry zeroed strategy metrics when their original local
        context is unavailable, rather than fabricating historical signals.
        """
        created_signal_ids: List[str] = []
        try:
            with self.db.session() as session:
                for position in positions:
                    ticket = getattr(position, "ticket", None)
                    if ticket is None:
                        continue

                    comment = str(getattr(position, "comment", ""))
                    signal_id = comment.removeprefix("QTB:") if comment.startswith("QTB:") else ""
                    if not signal_id:
                        signal_id = f"RECONCILED-{magic_number}-{ticket}"

                    existing = session.query(TradeDB).filter_by(ticket=int(ticket)).first()
                    if existing is None:
                        existing = session.query(TradeDB).filter_by(signal_id=signal_id).first()
                    if existing is not None:
                        # A fill can be acknowledged before MT5 exposes its
                        # position ticket.  Attach it on the next sync rather
                        # than creating a second journal row.
                        if existing.ticket is None:
                            existing.ticket = int(ticket)
                            existing.magic_number = magic_number
                        continue

                    position_type = getattr(position, "type", 0)
                    direction = "BUY" if position_type == 0 else "SELL"
                    opened_at = datetime.fromtimestamp(
                        int(getattr(position, "time", 0) or 0), tz=timezone.utc
                    )
                    if opened_at.year < 2000:
                        opened_at = datetime.now(timezone.utc)

                    session.add(TradeDB(
                        signal_id=signal_id,
                        symbol=getattr(position, "symbol", "UNKNOWN"),
                        timeframe="UNKNOWN",
                        direction=direction,
                        state=TradeState.OPEN.value,
                        entry_price=float(getattr(position, "price_open", 0.0)),
                        sl_price=float(getattr(position, "sl", 0.0)),
                        tp_price=float(getattr(position, "tp", 0.0)),
                        volume=float(getattr(position, "volume", 0.0)),
                        created_at=opened_at,
                        entered_at=opened_at,
                        total_score=0.0,
                        structure_score=0.0,
                        trend_score=0.0,
                        momentum_score=0.0,
                        volume_score=0.0,
                        volatility_score=0.0,
                        risk_percent=0.0,
                        risk_money=0.0,
                        ticket=int(ticket),
                        magic_number=magic_number,
                        comment="Reconciled from MT5; original local context unavailable.",
                    ))
                    created_signal_ids.append(signal_id)
        except IntegrityError as exc:
            logger.warning("A reconciled MT5 position already exists locally: %s", exc)
        except Exception as exc:
            logger.error("Failed to reconcile open MT5 positions: %s", exc)
        return created_signal_ids

    def reconcile_pending_orders(self, orders: List[object], magic_number: int) -> List[str]:
        """Import acknowledged bot pending orders missing from the local journal.

        This is the crash window between a successful ``order_send`` and the
        following SQLite write. MT5 is authoritative, so a pending order must
        be recovered and managed instead of being ignored until expiration.
        """
        imported_signal_ids: List[str] = []
        try:
            with self.db.session() as session:
                for order in orders:
                    ticket = getattr(order, "ticket", None)
                    comment = str(getattr(order, "comment", ""))
                    if ticket is None or not comment.startswith("QTB:"):
                        continue
                    signal_id = comment.removeprefix("QTB:")
                    existing = session.query(TradeDB).filter_by(ticket=int(ticket)).first()
                    if existing is None:
                        existing = session.query(TradeDB).filter_by(signal_id=signal_id).first()
                    if existing is not None:
                        # A prior record may exist without its broker ticket.
                        if existing.ticket is None:
                            existing.ticket = int(ticket)
                            existing.magic_number = magic_number
                        continue

                    order_type = int(getattr(order, "type", 0) or 0)
                    # MT5 buy types are 0, 2, 4, 6; sell types are 1, 3, 5, 7.
                    direction = "BUY" if order_type in {0, 2, 4, 6} else "SELL"
                    setup_time = int(getattr(order, "time_setup", 0) or 0)
                    created_at = (
                        datetime.fromtimestamp(setup_time, tz=timezone.utc)
                        if setup_time else datetime.now(timezone.utc)
                    )
                    session.add(TradeDB(
                        signal_id=signal_id,
                        symbol=getattr(order, "symbol", "UNKNOWN"),
                        timeframe="UNKNOWN",
                        direction=direction,
                        state=TradeState.ORDER_PENDING.value,
                        entry_price=float(getattr(order, "price_open", getattr(order, "price", 0.0)) or 0.0),
                        sl_price=float(getattr(order, "sl", 0.0) or 0.0),
                        tp_price=float(getattr(order, "tp", 0.0) or 0.0),
                        volume=float(getattr(order, "volume_current", getattr(order, "volume_initial", 0.0)) or 0.0),
                        created_at=created_at,
                        total_score=0.0,
                        structure_score=0.0,
                        trend_score=0.0,
                        momentum_score=0.0,
                        volume_score=0.0,
                        volatility_score=0.0,
                        risk_percent=0.0,
                        risk_money=0.0,
                        ticket=int(ticket),
                        magic_number=magic_number,
                        comment=comment,
                    ))
                    imported_signal_ids.append(signal_id)
        except IntegrityError as exc:
            logger.warning("A reconciled MT5 pending order already exists locally: %s", exc)
        except Exception as exc:
            logger.error("Failed to reconcile MT5 pending orders: %s", exc)
        return imported_signal_ids

    def mark_trade_closed_by_ticket(
        self,
        ticket: int,
        exit_price: float,
        profit: float,
        exited_at: datetime,
    ) -> bool:
        """Persist a broker-confirmed close and calculate its realised R."""
        try:
            with self.db.session() as session:
                trade = session.query(TradeDB).filter_by(ticket=int(ticket)).first()
                if trade is None or trade.state == TradeState.CLOSED.value:
                    return False
                trade.state = TradeState.CLOSED.value
                trade.exit_price = exit_price
                trade.profit = profit
                trade.profit_r = round(profit / trade.risk_money, 4) if trade.risk_money else None
                trade.exited_at = exited_at
                if trade.entered_at:
                    entered_at = trade.entered_at
                    if entered_at.tzinfo is None:
                        entered_at = entered_at.replace(tzinfo=timezone.utc)
                    if exited_at.tzinfo is None:
                        exited_at = exited_at.replace(tzinfo=timezone.utc)
                    trade.duration_seconds = max(0, int((exited_at - entered_at).total_seconds()))
                return True
        except Exception as exc:
            logger.error("Failed to mark trade %s closed: %s", ticket, exc)
            return False
    
    def get_today_trades(self, symbol: Optional[str] = None) -> List[TradeDB]:
        """Get all trades from today's broker session, optionally filtered by symbol.

        IC Markets server runs on UTC+3.  A trading 'day' starts at
        00:00 broker time (21:00 UTC the previous calendar day).  Using
        raw UTC midnight would silently split the intraday session across
        two calendar days and under-count trades in the daily limit guard.
        """
        from datetime import timezone, timedelta
        broker_tz = timezone(timedelta(hours=3))  # IC Markets server UTC+3
        now_broker = datetime.now(broker_tz)
        broker_day_start = now_broker.replace(hour=0, minute=0, second=0, microsecond=0)
        # Convert broker day-start back to UTC for the DB comparison
        today_start = broker_day_start.astimezone(timezone.utc)
        try:
            with self.db.session() as session:
                query = session.query(TradeDB).filter(
                    TradeDB.created_at >= today_start
                )
                if symbol:
                    query = query.filter(TradeDB.symbol == symbol)
                return query.all()
        except Exception as e:
            logger.error(f"Error retrieving today's trades: {e}")
            return []
    
    def get_consecutive_losses(self, symbol: Optional[str] = None) -> int:
        """Count current consecutive losses (most recent closed trades).
        
        If symbol is provided, counts consecutive losses for that specific asset.
        If symbol is None, counts consecutive losses across active assets (XAUUSD, BTCUSD).
        """
        try:
            with self.db.session() as session:
                query = session.query(TradeDB).filter(
                    TradeDB.state == TradeState.CLOSED.value,
                    TradeDB.profit.isnot(None)
                )
                if symbol:
                    query = query.filter(TradeDB.symbol == symbol.upper())
                else:
                    query = query.filter(TradeDB.symbol.in_(["XAUUSD", "BTCUSD"]))

                recent_trades = query.order_by(desc(TradeDB.exited_at)).limit(20).all()
                
                count = 0
                for trade in recent_trades:
                    if trade.profit < 0:
                        count += 1
                    else:
                        break
                return count
        except Exception as e:
            logger.error(f"Error getting consecutive losses: {e}")
            return 0


    def total_open_and_pending_risk(self) -> float:
        """Return planned cash risk still reserved by this bot journal."""
        states = [
            TradeState.ORDER_PENDING.value, TradeState.OPEN.value,
            TradeState.BREAK_EVEN.value, TradeState.PARTIAL.value,
            TradeState.TRAILING.value,
        ]
        try:
            with self.db.session() as session:
                rows = session.query(TradeDB.risk_money).filter(TradeDB.state.in_(states)).all()
                return round(sum(max(0.0, float(row[0] or 0.0)) for row in rows), 2)
        except Exception as exc:
            logger.error("Unable to calculate reserved risk: %s", exc)
            return float("inf")

    def get_management_state(self, ticket: int) -> dict:
        """Read idempotent BE/partial state stored with the original trade."""
        try:
            with self.db.session() as session:
                trade = session.query(TradeDB).filter_by(ticket=int(ticket)).first()
                features = (trade.features_at_entry or {}) if trade else {}
                state = features.get("management_state", {})
                return dict(state) if isinstance(state, dict) else {}
        except Exception as exc:
            logger.error("Unable to read management state for %s: %s", ticket, exc)
            return {}

    def update_management_state(self, ticket: int, **updates) -> bool:
        """Persist management side effects before a restart can repeat them."""
        try:
            with self.db.session() as session:
                trade = session.query(TradeDB).filter_by(ticket=int(ticket)).first()
                if trade is None:
                    return False
                features = dict(trade.features_at_entry or {})
                state = dict(features.get("management_state") or {})
                state.update(updates)
                features["management_state"] = state
                trade.features_at_entry = features
                if "remaining_volume" in updates:
                    trade.volume = float(updates["remaining_volume"])
                return True
        except Exception as exc:
            logger.error("Unable to persist management state for %s: %s", ticket, exc)
            return False


class SignalRepository:
    """Handles signal log persistence."""
    
    def __init__(self, database: Database):
        self.db = database
    
    def _new_signal_row(
        self,
        signal: Signal,
        was_traded: bool = False,
        rejection_reasons: Optional[List[str]] = None,
    ) -> SignalLogDB:
        return SignalLogDB(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            direction=signal.direction.value,
            total_score=signal.score_result.total_score,
            structure_score=signal.score_result.structure.score,
            trend_score=signal.score_result.trend.score,
            momentum_score=signal.score_result.momentum.score,
            volume_score=signal.score_result.volume.score,
            volatility_score=signal.score_result.volatility.score,
            classification=signal.score_result.classification.value,
            was_traded=was_traded,
            rejection_reasons=', '.join(rejection_reasons) if rejection_reasons else None,
            created_at=signal.created_at,
        )

    def reserve_signal(self, signal: Signal) -> bool:
        """Atomically reserve a signal before order routing.

        The database's unique signal_id is the inter-process lock.  A second
        process/restart receives False and cannot submit another order.
        """
        try:
            with self.db.session() as session:
                session.add(self._new_signal_row(signal, rejection_reasons=["PENDING_EXECUTION"]))
                session.flush()
            return True
        except IntegrityError:
            return False
        except Exception as exc:
            logger.error("Unable to reserve signal %s: %s", signal.signal_id, exc)
            return False

    def update_signal_status(
        self,
        signal_id: str,
        was_traded: bool,
        reasons: Optional[List[str]] = None,
    ) -> bool:
        try:
            with self.db.session() as session:
                db_signal = session.query(SignalLogDB).filter_by(signal_id=signal_id).first()
                if db_signal is None:
                    return False
                db_signal.was_traded = was_traded
                db_signal.rejection_reasons = ', '.join(reasons) if reasons else None
            return True
        except Exception as exc:
            logger.error("Unable to update signal %s: %s", signal_id, exc)
            return False

    def save_signal(self, signal: Signal, was_traded: bool = False,
                    rejection_reasons: Optional[List[str]] = None) -> None:
        """Log a non-routed signal without overwriting an existing decision."""
        try:
            with self.db.session() as session:
                db_signal = session.query(SignalLogDB).filter_by(signal_id=signal.signal_id).first()
                if db_signal is None:
                    session.add(self._new_signal_row(signal, was_traded, rejection_reasons))
        except Exception as e:
            logger.error(f"Error saving signal {signal.signal_id}: {e}")
            raise
    
    def is_signal_processed(self, signal_id: str) -> bool:
        """Check if a signal has already been processed (avoid duplicates)."""
        try:
            with self.db.session() as session:
                exists = session.query(SignalLogDB).filter_by(
                    signal_id=signal_id
                ).first()
                return exists is not None
        except Exception as e:
            logger.error(f"Error checking if signal is processed: {e}")
            return False


class DecisionContextRepository:
    """Persist observation-only context idempotently per deterministic signal."""

    def __init__(self, database: Database):
        self.db = database

    def save_context(self, signal: Signal, context: dict) -> None:
        try:
            with self.db.session() as session:
                if session.query(DecisionContextDB).filter_by(signal_id=signal.signal_id).first() is not None:
                    return
                session.add(DecisionContextDB(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    timeframe=signal.timeframe,
                    direction=signal.direction.value,
                    regime=context.get("regime", {}).get("label"),
                    session=context.get("session", {}).get("label"),
                    context=context,
                    created_at=signal.created_at,
                ))
        except Exception as exc:
            logger.error("Unable to persist decision context for %s: %s", signal.signal_id, exc)
            raise


class DailyStatsRepository:
    """Handles daily statistics."""
    
    def __init__(self, database: Database):
        self.db = database
    
    def update_daily_stats(self, date_str: str, **kwargs) -> None:
        """Update or create daily stats."""
        try:
            with self.db.session() as session:
                stats = session.query(DailyStatsDB).filter_by(date=date_str).first()
                if stats is None:
                    stats = DailyStatsDB(date=date_str)
                    session.add(stats)
                for key, value in kwargs.items():
                    if hasattr(stats, key):
                        setattr(stats, key, value)
        except Exception as e:
            logger.error(f"Error updating daily stats: {e}")
            raise
    
    def get_daily_stats(self, date_str: str) -> Optional[DailyStatsDB]:
        """Get stats for a specific date."""
        try:
            with self.db.session() as session:
                return session.query(DailyStatsDB).filter_by(date=date_str).first()
        except Exception as e:
            logger.error(f"Error getting daily stats: {e}")
            return None
    
    def get_today_pnl(self, symbol: Optional[str] = None) -> float:
        """Get today's total P&L, optionally filtered by symbol."""
        try:
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            if symbol:
                day_start = datetime.strptime(today, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                day_end = day_start + timedelta(days=1)
                with self.db.session() as session:
                    trades = session.query(TradeDB).filter(
                        TradeDB.state == TradeState.CLOSED.value,
                        TradeDB.symbol == symbol,
                        TradeDB.exited_at >= day_start,
                        TradeDB.exited_at < day_end,
                        TradeDB.profit.isnot(None),
                    ).all()
                    return round(sum(float(t.profit) for t in trades), 2)
            stats = self.get_daily_stats(today)
            return stats.net_profit if stats else 0.0
        except Exception as e:
            logger.error(f"Error getting today PNL: {e}")
            return 0.0

    def ensure_daily_start_balance(self, date_str: str, current_balance: float) -> float:
        """Create the daily baseline once; never erase losses on restart."""
        try:
            with self.db.session() as session:
                stats = session.query(DailyStatsDB).filter_by(date=date_str).first()
                if stats is None:
                    stats = DailyStatsDB(date=date_str, start_balance=float(current_balance))
                    session.add(stats)
                    return float(current_balance)
                if stats.start_balance is None:
                    stats.start_balance = float(current_balance)
                return float(stats.start_balance)
        except Exception as exc:
            logger.error("Unable to ensure daily start balance: %s", exc)
            return float(current_balance)

    def rebuild_for_date(self, date_value: datetime) -> None:
        """Recalculate daily realised metrics from reconciled closed trades."""
        day = date_value.astimezone(timezone.utc).strftime('%Y-%m-%d')
        day_start = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        try:
            with self.db.session() as session:
                trades = session.query(TradeDB).filter(
                    TradeDB.state == TradeState.CLOSED.value,
                    TradeDB.exited_at >= day_start,
                    TradeDB.exited_at < day_end,
                    TradeDB.profit.isnot(None),
                ).all()
                profits = [float(trade.profit) for trade in trades]
                gross_profit = sum(value for value in profits if value > 0)
                gross_loss = sum(value for value in profits if value < 0)
                stats = session.query(DailyStatsDB).filter_by(date=day).first()
                if stats is None:
                    stats = DailyStatsDB(date=day)
                    session.add(stats)
                stats.total_trades = len(profits)
                stats.winning_trades = sum(value > 0 for value in profits)
                stats.losing_trades = sum(value < 0 for value in profits)
                stats.gross_profit = round(gross_profit, 2)
                stats.gross_loss = round(gross_loss, 2)
                stats.net_profit = round(sum(profits), 2)
                stats.win_rate = round((stats.winning_trades / len(profits) * 100.0), 2) if profits else 0.0
                stats.profit_factor = round(gross_profit / abs(gross_loss), 4) if gross_loss else (float('inf') if gross_profit else 0.0)
        except Exception as exc:
            logger.error("Unable to rebuild daily statistics for %s: %s", day, exc)


class RuntimeStateRepository:
    """Durable runtime safety state (for example account equity high-water)."""

    def __init__(self, database: Database):
        self.db = database

    def get(self, key: str, default=None):
        try:
            with self.db.session() as session:
                row = session.query(RuntimeStateDB).filter_by(key=key).first()
                return row.value if row is not None else default
        except Exception as exc:
            logger.error("Unable to read runtime state %s: %s", key, exc)
            return default

    def set(self, key: str, value) -> None:
        try:
            with self.db.session() as session:
                row = session.query(RuntimeStateDB).filter_by(key=key).first()
                if row is None:
                    session.add(RuntimeStateDB(key=key, value=value))
                else:
                    row.value = value
        except Exception as exc:
            logger.error("Unable to persist runtime state %s: %s", key, exc)
