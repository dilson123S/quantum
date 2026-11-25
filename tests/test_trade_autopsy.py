"""Unit tests for the AI Trade Autopsy and Cognitive Adaptation Engine."""
import pytest
from datetime import datetime, timezone
from dataclasses import dataclass

from learning.trade_autopsy import TradeAutopsyEngine, TradeAutopsyReport
from database.database import Database
from database.repository import TradeRepository
from database.models import TradeDB


@dataclass
class MockTrade:
    ticket: int = 123456
    symbol: str = "XAUUSD"
    direction: str = "SELL"
    entry_price: float = 4322.66
    exit_price: float = 4284.90
    sl_price: float = 4340.00
    tp_price: float = 4220.00
    volume: float = 0.02
    profit: float = 75.52
    profit_r: float = 0.535
    total_score: float = 90.0
    duration_seconds: int = 7200
    state: str = "CLOSED"


def test_trade_autopsy_win_analysis():
    engine = TradeAutopsyEngine()
    trade = MockTrade(profit=75.52, profit_r=0.535)
    report = engine.analyze_trade(trade)

    assert isinstance(report, TradeAutopsyReport)
    assert report.ticket == 123456
    assert report.symbol == "XAUUSD"
    assert report.direction == "SELL"
    assert report.profit_usd == 75.52
    assert report.result_type == "WIN"
    assert report.result_badge_class == "badge-green"
    assert report.timing_rating == "ÓPTIMO"
    assert "Score 90.0/100" in report.bot_reflection
    assert "4322.66" in report.bot_reflection
    assert len(report.what_went_well) > 10
    assert len(report.what_to_improve) > 10
    assert len(report.xauusd_lesson) > 10


def test_trade_autopsy_breakeven_analysis():
    engine = TradeAutopsyEngine()
    trade = MockTrade(ticket=222333, profit=-0.35, profit_r=-0.002)
    report = engine.analyze_trade(trade)

    assert report.result_type == "BREAK_EVEN"
    assert report.result_badge_class == "badge-amber"
    assert "Break-Even" in report.bot_reflection
    assert "retesteo" in report.xauusd_lesson.lower()


def test_trade_autopsy_loss_analysis():
    engine = TradeAutopsyEngine()
    trade = MockTrade(ticket=333444, profit=-35.0, profit_r=-1.0)
    report = engine.analyze_trade(trade)

    assert report.result_type == "LOSS"
    assert report.result_badge_class == "badge-red"
    assert "falso quiebre" in report.bot_reflection.lower()


def test_xauusd_dna_insights():
    engine = TradeAutopsyEngine()
    trades = [
        MockTrade(profit=61.14, profit_r=0.32),
        MockTrade(profit=-0.35, profit_r=-0.002),
        MockTrade(profit=75.52, profit_r=0.54),
    ]
    dna = engine.get_xauusd_dna_insights(trades)

    assert dna["symbol"] == "XAUUSD (Oro)"
    assert dna["total_analyzed_trades"] == 3
    assert dna["win_rate_percent"] >= 60.0
    assert "12.4" in str(dna["avg_pullback_depth_usd"])
    assert "Londres" in dna["best_liquidity_window"]


def test_trade_repository_cumulative_stats(tmp_path):
    db_path = tmp_path / "test_journal.db"
    db = Database(str(db_path))
    db.create_tables()
    repo = TradeRepository(db)

    # Empty stats
    stats = repo.get_cumulative_stats()
    assert stats["total_trades"] == 0
    assert stats["net_pnl"] == 0.0

    # Insert test closed trades
    with db.session() as s:
        t1 = TradeDB(
            trade_id="t1", signal_id="s1", symbol="XAUUSD", timeframe="H1",
            direction="SELL", state="CLOSED", entry_price=4300.0, exit_price=4250.0,
            sl_price=4320.0, tp_price=4200.0, volume=0.02, total_score=85.0,
            structure_score=25.0, trend_score=20.0, momentum_score=15.0,
            volume_score=10.0, volatility_score=15.0, risk_percent=1.0,
            risk_money=50.0, profit=100.0, profit_r=2.0, ticket=101
        )
        t2 = TradeDB(
            trade_id="t2", signal_id="s2", symbol="XAUUSD", timeframe="H1",
            direction="BUY", state="CLOSED", entry_price=4300.0, exit_price=4280.0,
            sl_price=4280.0, tp_price=4350.0, volume=0.02, total_score=80.0,
            structure_score=20.0, trend_score=20.0, momentum_score=15.0,
            volume_score=10.0, volatility_score=15.0, risk_percent=1.0,
            risk_money=50.0, profit=-20.0, profit_r=-0.4, ticket=102
        )
        s.add_all([t1, t2])
        s.commit()

    stats = repo.get_cumulative_stats()
    assert stats["total_trades"] == 2
    assert stats["winning_trades"] == 1
    assert stats["losing_trades"] == 1
    assert stats["net_pnl"] == 80.0
    assert stats["profit_factor"] == 5.0
    assert stats["win_rate"] == 50.0
    assert stats["best_trade"] == 100.0
    assert stats["worst_trade"] == -20.0

    closed = repo.get_all_closed_trades(limit=10)
    assert len(closed) == 2
