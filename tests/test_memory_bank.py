"""Unit tests for the NeuralMemoryBank closed-loop learning engine."""
import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

from database.database import Database
from database.models import PatternMemoryDB, TradeDB
from learning.memory_bank import NeuralMemoryBank
from learning.trade_autopsy import TradeAutopsyEngine


@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_mb.db"
    db = Database(str(db_path))
    db.create_tables()
    return db


def test_memory_bank_default_patterns_initialized(test_db):
    mb = NeuralMemoryBank(test_db)
    patterns = mb.get_memory_bank_summary()

    assert len(patterns) == 4
    pattern_ids = [p["pattern_id"] for p in patterns]
    assert "xauusd_breakout_pullback" in pattern_ids
    assert "xauusd_trend_trailing_cushion" in pattern_ids
    assert "xauusd_ny_session_liquidity" in pattern_ids
    assert "xauusd_spike_exhaustion" in pattern_ids

    for p in patterns:
        assert p["observations"] == 0
        assert p["is_active"] is False
        assert p["confidence"] == "EN_OBSERVACION"


def test_anti_overfitting_safeguard(test_db):
    """Ensure patterns with small sample size (N < 15) remain inactive (Shadow Mode)."""
    mb = NeuralMemoryBank(test_db)

    mock_trade_win = SimpleNamespace(
        symbol="XAUUSD",
        profit=50.0,
        profit_r=0.5,
        direction="SELL",
        entry_price=4300.0,
        exit_price=4270.0
    )

    # Record 4 wins (simulating initial small sample)
    for _ in range(4):
        mb.record_trade_observation(mock_trade_win)

    patterns = {p["pattern_id"]: p for p in mb.get_memory_bank_summary()}
    pullback = patterns["xauusd_breakout_pullback"]

    assert pullback["observations"] == 4
    assert pullback["success_rate"] == 100.0
    # Crucial safeguard: MUST NOT activate with only 4 observations!
    assert pullback["is_active"] is False
    assert pullback["confidence"] == "EN_OBSERVACION"

    # Query active modifications -> should be empty because safeguard is holding
    active_mods = mb.get_active_modifications("XAUUSD", category="PULLBACK")
    assert active_mods == {}


def test_pattern_activation_at_statistical_significance(test_db):
    """Ensure pattern activates once sample size >= 15 with success rate >= 70%."""
    mb = NeuralMemoryBank(test_db)

    mock_trade_win = SimpleNamespace(
        symbol="XAUUSD", profit=40.0, profit_r=0.4, direction="BUY", entry_price=4300.0, exit_price=4330.0
    )
    mock_trade_loss = SimpleNamespace(
        symbol="XAUUSD", profit=-20.0, profit_r=-0.2, direction="BUY", entry_price=4300.0, exit_price=4280.0
    )

    # Record 12 wins and 3 losses (15 total, 80% win rate)
    for _ in range(12):
        mb.record_trade_observation(mock_trade_win)
    for _ in range(3):
        mb.record_trade_observation(mock_trade_loss)

    patterns = {p["pattern_id"]: p for p in mb.get_memory_bank_summary()}
    pullback = patterns["xauusd_breakout_pullback"]

    assert pullback["observations"] == 15
    assert pullback["success_rate"] == 80.0
    assert pullback["confidence"] == "ALTA"
    # In Stage 3.5, reaching N=15 promotes to CANDIDATE; stays inactive until validated in Shadow Live
    assert pullback["lifecycle_state"] == "CANDIDATE"
    assert pullback["is_active"] is False

    # Once fully validated through Shadow Live and promoted to ACTIVE:
    with test_db.session() as s:
        from database.models import PatternMemoryDB
        p_db = s.query(PatternMemoryDB).filter_by(pattern_id="xauusd_breakout_pullback").first()
        p_db.lifecycle_state = "ACTIVE"
        p_db.is_active = True
        s.commit()

    # Query active modifications -> now delivers the parameter adjustment to the bot!
    active_mods = mb.get_active_modifications("XAUUSD", category="PULLBACK")
    assert active_mods.get("buffer_points_offset") == 5.0


def test_4_step_autopsy_generation():
    """Verify that every autopsy generates the 4 structured steps."""
    engine = TradeAutopsyEngine()

    mock_win = SimpleNamespace(
        ticket=1001, symbol="XAUUSD", direction="SELL", entry_price=4320.0, exit_price=4280.0,
        sl_price=4340.0, tp_price=4200.0, volume=0.02, profit=80.0, profit_r=0.8,
        total_score=88.0, duration_seconds=1800
    )
    report = engine.analyze_trade(mock_win)

    assert report.step1_thought != ""
    assert report.step2_happened != ""
    assert report.step3_learned != ""
    assert report.step4_action != ""

    assert "Detecté estructura" in report.step1_thought
    assert "El precio recorrió" in report.step2_happened
    assert "En rupturas" in report.step3_learned
    assert "Memory Bank" in report.step4_action
