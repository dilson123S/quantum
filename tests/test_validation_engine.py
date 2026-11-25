"""Comprehensive test suite for AdaptiveValidationEngine and Stage 3 Lifecycle Pipeline."""
import pytest
from types import SimpleNamespace

from learning.validation_engine import AdaptiveValidationEngine
from database.database import Database
from learning.memory_bank import NeuralMemoryBank


def test_strict_mathematical_rates():
    """Verify that 3 wins, 1 break-even, 0 losses produces strictly separated rates."""
    validator = AdaptiveValidationEngine()
    rates = validator.compute_rates(wins=3, breakevens=1, losses=0)

    # 3/4 is strictly 75.0%
    assert rates["win_rate"] == 75.0
    # 1/4 is strictly 25.0%
    assert rates["be_rate"] == 25.0
    # 0/4 is strictly 0.0%
    assert rates["loss_rate"] == 0.0
    # Outcome score (weighted effectiveness) is strictly (3 + 0.5)/4 = 87.5%
    assert rates["outcome_score"] == 87.5


def test_statistical_lift_calculation():
    """Verify lift ratio comparing Pattern Present vs Control Group."""
    validator = AdaptiveValidationEngine()

    # Pattern Present: 8 wins, 2 losses (80% win rate)
    # Pattern Absent:  5 wins, 5 losses (50% win rate)
    # Lift = 0.80 / 0.50 = 1.60x
    matrix = {"tp": 8, "fp": 2, "fn": 5, "tn": 5}
    lift = validator.compute_lift(matrix)

    assert lift == 1.6


def test_lifecycle_pipeline_shadow_to_candidate():
    """Ensure rule remains in SHADOW when N < 15, and transitions to CANDIDATE at N >= 15 with edge."""
    validator = AdaptiveValidationEngine()

    # N = 10 -> Must stay in SHADOW regardless of score
    eval_shadow = validator.evaluate_lifecycle_transition(
        observations=10, outcome_score=85.0, lift_ratio=1.40, current_state="SHADOW"
    )
    assert eval_shadow["next_state"] == "SHADOW"
    assert eval_shadow["is_active"] is False

    # N = 16 with Score = 75.0% and Lift = 1.25x -> Promoted to CANDIDATE
    eval_candidate = validator.evaluate_lifecycle_transition(
        observations=16, outcome_score=75.0, lift_ratio=1.25, current_state="SHADOW"
    )
    assert eval_candidate["next_state"] == "CANDIDATE"
    assert eval_candidate["is_active"] is False


def test_lifecycle_pipeline_candidate_to_active_or_rejected():
    """Verify A/B validation promotes when PF improves, and rejects when PF degrades."""
    validator = AdaptiveValidationEngine()

    baseline = {"profit_factor": 1.30, "max_drawdown_pct": 3.0}
    
    # Treatment 1: Improves PF from 1.30 to 1.55 with lower DD -> ACTIVE
    treatment_win = {"profit_factor": 1.55, "max_drawdown_pct": 2.8}
    eval_active = validator.evaluate_lifecycle_transition(
        observations=20, outcome_score=75.0, lift_ratio=1.30,
        current_state="CANDIDATE", baseline_metrics=baseline, treatment_metrics=treatment_win
    )
    assert eval_active["next_state"] == "ACTIVE_CANDIDATE"
    assert eval_active["is_active"] is False

    # Completing SHADOW_LIVE phase -> promoted to ACTIVE
    eval_live = validator.evaluate_lifecycle_transition(
        observations=35, outcome_score=75.0, lift_ratio=1.30,
        current_state="SHADOW_LIVE", shadow_live_samples=15
    )
    assert eval_live["next_state"] == "ACTIVE"
    assert eval_live["is_active"] is True

    # Treatment 2: Degrades PF from 1.30 to 1.10 -> REJECTED
    treatment_loss = {"profit_factor": 1.10, "max_drawdown_pct": 4.5}
    eval_rejected = validator.evaluate_lifecycle_transition(
        observations=20, outcome_score=75.0, lift_ratio=1.30,
        current_state="CANDIDATE", baseline_metrics=baseline, treatment_metrics=treatment_loss
    )
    assert eval_rejected["next_state"] == "REJECTED"
    assert eval_rejected["is_active"] is False


def test_lifecycle_automatic_rollback_on_drawdown():
    """Verify that an ACTIVE rule experiencing 3 consecutive losses triggers automatic rollback."""
    validator = AdaptiveValidationEngine()

    eval_rollback = validator.evaluate_lifecycle_transition(
        observations=25, outcome_score=68.0, lift_ratio=1.20,
        current_state="ACTIVE", consecutive_losses=3
    )
    assert eval_rollback["next_state"] == "ROLLBACK"
    assert eval_rollback["is_active"] is False
    assert "Rollback triggered" in eval_rollback["reason"]


def test_dynamic_pullback_range_by_atr_and_session():
    """Verify that Gold pullback depth adapts dynamically to ATR and session."""
    validator = AdaptiveValidationEngine()

    low_atr_asia = validator.get_dynamic_pullback_range(atr_points=8.0, session="ASIA")
    assert "Baja Volatilidad" in low_atr_asia["regime"]
    assert low_atr_asia["expected_pullback_min_usd"] < 5.0

    high_atr_ny = validator.get_dynamic_pullback_range(atr_points=25.0, session="NY")
    assert "Alta Volatilidad" in high_atr_ny["regime"]
    assert high_atr_ny["expected_pullback_max_usd"] > 20.0


def test_memory_bank_veto_rule(tmp_path):
    """Verify negative rule veto mechanism in NeuralMemoryBank."""
    db = Database(str(tmp_path / "test_veto.db"))
    db.create_tables()
    mb = NeuralMemoryBank(db)

    # By default, negative rule is in SHADOW mode (not active), so veto is None
    veto = mb.check_veto("XAUUSD", setup_context={"is_vertical_spike": True})
    assert veto is None

    # Manually activate the negative pattern to test veto trigger
    with db.session() as s:
        from database.models import PatternMemoryDB
        p = s.query(PatternMemoryDB).filter_by(pattern_id="xauusd_spike_exhaustion").first()
        p.is_active = True
        s.commit()

    veto_active = mb.check_veto("XAUUSD", setup_context={"is_vertical_spike": True})
    assert veto_active is not None
    assert "VETO ACTIVO" in veto_active
