"""Regression tests for irreversible execution safeguards."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.constants import Direction, SignalClassification
from core.models import ComponentScore, ScoreResult, Signal, TradeRecord, TradeSetup
from core.constants import MarketRegime, TradeState
from core.config_loader import AppConfig, BotConfig
from database.database import Database
from database.repository import DailyStatsRepository, SignalRepository, TradeRepository
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from execution.execution_guard import ExecutionGuard


def _signal() -> Signal:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    score = ScoreResult(
        direction=Direction.BUY,
        total_score=80.0,
        classification=SignalClassification.GOOD,
        structure=ComponentScore(name="structure", score=20.0, max_score=30.0),
        trend=ComponentScore(name="trend", score=18.0, max_score=25.0),
        momentum=ComponentScore(name="momentum", score=12.0, max_score=15.0),
        volume=ComponentScore(name="volume", score=12.0, max_score=15.0),
        volatility=ComponentScore(name="volatility", score=12.0, max_score=15.0),
        timestamp=now,
    )
    return Signal(
        symbol="EURUSD",
        timeframe="M15",
        direction=Direction.BUY,
        score_result=score,
        created_at=now,
        entry_price=1.1,
    )


def test_mock_connector_refuses_live_routing_to_demo_account():
    connector = MT5Connector(mock_mode=True)
    assert connector.validate_execution_account("demo", False)[0] is True
    allowed, reason = connector.validate_execution_account("live", True)
    assert allowed is False
    assert "real account" in reason.lower()


def test_order_manager_blocks_live_order_before_building_request():
    class Connector:
        def is_connected(self):
            return True

        def validate_execution_account(self, mode, allow_live_trading):
            return False, "Live trading is disabled by bot.allow_live_trading."

    signal = _signal()
    setup = TradeSetup(
        signal=signal,
        entry_price=1.1,
        sl_price=1.09,
        tp_price=1.12,
        volume=0.1,
        risk_percent=1.0,
        risk_money=100.0,
        risk_reward_ratio=2.0,
    )
    manager = OrderManager(Connector(), BotConfig(mode="live", allow_live_trading=False))
    result = manager.create_market_order(setup)
    assert result.success is False
    assert "disabled" in result.message.lower()


def test_execution_guard_stops_immediately_when_account_route_is_invalid():
    """A route mismatch must not continue into symbol, quote or margin checks."""
    class Connector:
        def is_connected(self):
            return True

        def validate_execution_account(self, mode, allow_live_trading):
            return False, "Connected account is not an approved demo account."

        def get_symbol_info(self, symbol):
            raise AssertionError("Guard continued after account-route rejection")

    signal = _signal()
    setup = TradeSetup(
        signal=signal, entry_price=1.1, sl_price=1.09, tp_price=1.12,
        volume=0.1, risk_percent=1.0, risk_money=100.0, risk_reward_ratio=2.0,
    )
    allowed, reasons = ExecutionGuard(AppConfig(bot=BotConfig(mode="demo"))).validate_pre_execution(
        setup, Connector()
    )

    assert allowed is False
    assert reasons == ["Connected account is not an approved demo account."]


def test_paper_mode_blocks_every_mt5_mutation():
    class Connector:
        def __init__(self):
            self.calls = []

        def is_connected(self):
            return True

        def cancel_pending_order(self, ticket):
            self.calls.append(("cancel", ticket))

        def close_position(self, ticket, volume=None):
            self.calls.append(("close", ticket, volume))

        def modify_position(self, ticket, sl, tp):
            self.calls.append(("modify", ticket, sl, tp))

    connector = Connector()
    manager = OrderManager(connector, BotConfig(mode="paper"))
    assert not manager.cancel_pending_order(1).success
    assert not manager.close_order(1).success
    assert not manager.modify_sltp(1, 1.0, 2.0).success
    assert connector.calls == []


def test_signal_reservation_is_durable_and_idempotent(tmp_path):
    db = Database(str(tmp_path / "journal.db"))
    db.create_tables()
    repo = SignalRepository(db)
    signal = _signal()

    assert repo.reserve_signal(signal) is True
    assert repo.reserve_signal(signal) is False
    assert repo.update_signal_status(signal.signal_id, was_traded=True) is True
    assert repo.is_signal_processed(signal.signal_id) is True


def test_mt5_reconciliation_persists_close_and_daily_stats(tmp_path):
    db = Database(str(tmp_path / "journal.db"))
    db.create_tables()
    trades = TradeRepository(db)
    stats = DailyStatsRepository(db)
    opened_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    position = SimpleNamespace(
        ticket=991,
        comment="QTB:reconcile-991",
        type=0,
        symbol="EURUSD",
        time=int(opened_at.timestamp()),
        price_open=1.1000,
        sl=1.0950,
        tp=1.1100,
        volume=0.10,
    )

    assert trades.reconcile_open_positions([position], magic_number=42) == ["reconcile-991"]
    closed_at = datetime.now(timezone.utc)
    assert trades.mark_trade_closed_by_ticket(991, 1.1010, -12.5, closed_at) is True
    stats.rebuild_for_date(closed_at)
    today = stats.get_daily_stats(closed_at.strftime("%Y-%m-%d"))
    assert today is not None
    assert today.total_trades == 1
    assert today.net_profit == -12.5


def test_pending_breakout_is_a_stop_order_with_expiry_not_market_order():
    class Connector:
        def __init__(self):
            self.request = None

        def is_connected(self):
            return True

        def validate_execution_account(self, mode, allow_live_trading):
            return True, ""

        def get_symbol_info(self, symbol):
            return SimpleNamespace(digits=5, point=0.00001, trade_stops_level=5)

        def get_symbol_tick(self, symbol):
            return SimpleNamespace(bid=1.10000, ask=1.10010)

        def get_filling_mode(self, symbol):
            return 0

        def check_order(self, request):
            self.request = request
            return SimpleNamespace(retcode=0, comment="ok")

        def execute_with_retry(self, request):
            self.request = request
            return SimpleNamespace(retcode=10008, order=707, price=request["price"], volume=request["volume"], comment="placed")

    connector = Connector()
    setup = TradeSetup(
        signal=_signal(), entry_price=1.10020, sl_price=1.09900, tp_price=1.10260,
        volume=0.10, risk_percent=1.0, risk_money=100.0, risk_reward_ratio=2.0,
        is_pending=True, expiration=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    result = OrderManager(connector, BotConfig(mode="demo")).create_breakout_order(setup)
    assert result.success is True
    assert result.ticket == 707
    assert connector.request["price"] == 1.10020
    assert "expiration" in connector.request
    # MT5 pending action is 5 in the adapter fallback and differs from a deal.
    assert connector.request["action"] != 1


def test_pending_journal_promotes_to_open_position_after_restart(tmp_path):
    db = Database(str(tmp_path / "journal.db"))
    db.create_tables()
    trades = TradeRepository(db)
    signal = _signal()
    trade = TradeRecord(
        state=TradeState.ORDER_PENDING,
        signal_id=signal.signal_id,
        symbol="EURUSD",
        timeframe="M15",
        direction=Direction.BUY,
        entry_price=1.10020,
        sl_price=1.09900,
        tp_price=1.10260,
        volume=0.10,
        spread_at_entry=10.0,
        created_at=datetime.now(timezone.utc),
        risk_percent=1.0,
        risk_money=100.0,
        market_regime=MarketRegime.TRENDING,
        all_scores=signal.score_result,
        ticket=700,
        magic_number=42,
        comment=f"QTB:{signal.signal_id}",
    )
    trades.save_trade(trade)
    filled = SimpleNamespace(ticket=900, price_open=1.10025, volume=0.10, time=int(datetime.now(timezone.utc).timestamp()))
    assert trades.mark_pending_filled(700, filled) is True
    open_trade = trades.get_open_trades()[0]
    assert open_trade.ticket == 900
    assert open_trade.state == TradeState.OPEN.value


def test_reconciliation_imports_pending_order_after_journal_write_failure(tmp_path):
    db = Database(str(tmp_path / "journal.db"))
    db.create_tables()
    trades = TradeRepository(db)
    order = SimpleNamespace(
        ticket=701,
        comment="QTB:recovered-pending-701",
        type=4,
        symbol="EURUSD",
        time_setup=int(datetime.now(timezone.utc).timestamp()),
        price_open=1.10020,
        sl=1.09900,
        tp=1.10260,
        volume_current=0.10,
    )
    assert trades.reconcile_pending_orders([order], magic_number=42) == ["recovered-pending-701"]
    pending = trades.get_pending_trades()
    assert len(pending) == 1
    assert pending[0].ticket == 701
    assert pending[0].direction == "BUY"
