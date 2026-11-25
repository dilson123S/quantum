"""Test suite for Stage 3.5: Statistical Integrity, Wilson 95% CI, 6-Phase Pipeline, and Contextual Memory."""
import pytest
from types import SimpleNamespace

from learning.validation_engine import AdaptiveValidationEngine
from learning.trade_autopsy import TradeAutopsyEngine, TradeAutopsyReport


def test_wilson_confidence_interval():
    """Verify Wilson 95% score interval calculations for small vs large samples."""
    validator = AdaptiveValidationEngine()

    # Small sample: N=4, 3 wins -> Lower: ~30.1%, Upper: ~95.4%
    ci_small = validator.compute_wilson_ci(successes=3, total=4, confidence=0.95)
    assert 29.5 <= ci_small["lower"] <= 30.5
    assert 95.0 <= ci_small["upper"] <= 95.8
    assert "30.1%" in ci_small["display"] or "30.0%" in ci_small["display"]

    # Zero sample: N=0
    ci_zero = validator.compute_wilson_ci(successes=0, total=0)
    assert ci_zero["lower"] == 0.0
    assert ci_zero["upper"] == 0.0

    # Large sample: N=100, 70 wins -> Much tighter CI (~60.4% - 78.1%)
    ci_large = validator.compute_wilson_ci(successes=70, total=100, confidence=0.95)
    assert 59.5 <= ci_large["lower"] <= 61.0
    assert 77.0 <= ci_large["upper"] <= 79.0


def test_lift_audit_sample_size_warnings():
    """Verify that Lift flags insufficient evidence when sample size is below 20."""
    validator = AdaptiveValidationEngine()

    # Small sample: 4 in pattern vs 4 in control -> Not statistically significant!
    matrix_small = {"tp": 3, "fp": 1, "fn": 1, "tn": 3}
    audit_small = validator.compute_lift_audit(matrix_small)
    assert audit_small["is_significant"] is False
    assert audit_small["warning"] is not None
    assert "Muestra insuficiente" in audit_small["warning"]

    # Robust sample: 25 in pattern vs 25 in control -> Statistically significant
    matrix_large = {"tp": 20, "fp": 5, "fn": 10, "tn": 15}
    audit_large = validator.compute_lift_audit(matrix_large)
    assert audit_large["is_significant"] is True
    assert audit_large["warning"] is None


def test_6_phase_pipeline_with_shadow_live():
    """Verify the 6-phase pipeline: SHADOW -> CANDIDATE -> VALIDATING -> ACTIVE_CANDIDATE -> SHADOW_LIVE -> ACTIVE."""
    validator = AdaptiveValidationEngine()

    baseline = {"win_rate": 62.5, "profit_factor": 1.30, "max_drawdown_pct": 3.0}
    treatment = {"win_rate": 68.0, "profit_factor": 1.55, "max_drawdown_pct": 2.7}

    # Step 1: SHADOW mode while N < 15
    eval_1 = validator.evaluate_lifecycle_transition(observations=12, outcome_score=75.0, lift_ratio=1.2, current_state="SHADOW")
    assert eval_1["next_state"] == "SHADOW"
    assert eval_1["is_active"] is False

    # Step 2: Transition to CANDIDATE at N >= 15
    eval_2 = validator.evaluate_lifecycle_transition(observations=16, outcome_score=75.0, lift_ratio=1.2, current_state="SHADOW")
    assert eval_2["next_state"] == "CANDIDATE"
    assert eval_2["is_active"] is False

    # Step 3: CANDIDATE verified in simulation -> ACTIVE_CANDIDATE (NOT yet active in live MT5!)
    eval_3 = validator.evaluate_lifecycle_transition(
        observations=16, outcome_score=75.0, lift_ratio=1.2,
        current_state="CANDIDATE", baseline_metrics=baseline, treatment_metrics=treatment
    )
    assert eval_3["next_state"] == "ACTIVE_CANDIDATE"
    assert eval_3["is_active"] is False  # CRITICAL: Must not modify live orders!

    # Step 4: ACTIVE_CANDIDATE enters SHADOW_LIVE with N_live < 15
    eval_4 = validator.evaluate_lifecycle_transition(
        observations=20, outcome_score=75.0, lift_ratio=1.2,
        current_state="ACTIVE_CANDIDATE", shadow_live_samples=8
    )
    assert eval_4["next_state"] == "SHADOW_LIVE"
    assert eval_4["is_active"] is False  # CRITICAL: Must not modify live orders!

    # Step 5: SHADOW_LIVE completes N_live >= 15 with confirmed edge -> ACTIVE
    eval_5 = validator.evaluate_lifecycle_transition(
        observations=31, outcome_score=75.0, lift_ratio=1.2,
        current_state="SHADOW_LIVE", shadow_live_samples=16
    )
    assert eval_5["next_state"] == "ACTIVE"
    assert eval_5["is_active"] is True  # Only now has permission to modify live orders!


def test_detailed_rejection_audit_reason():
    """Verify that a candidate deteriorating PF or DD gets a detailed quantitative rejection reason."""
    validator = AdaptiveValidationEngine()

    baseline = {"win_rate": 62.0, "profit_factor": 1.45, "max_drawdown_pct": 3.0}
    # Treatment has higher win rate, BUT worse profit factor and worse drawdown!
    bad_treatment = {"win_rate": 67.0, "profit_factor": 1.18, "max_drawdown_pct": 4.6}

    eval_rejected = validator.evaluate_lifecycle_transition(
        observations=20, outcome_score=70.0, lift_ratio=1.2,
        current_state="CANDIDATE", baseline_metrics=baseline, treatment_metrics=bad_treatment
    )
    assert eval_rejected["next_state"] == "REJECTED"
    assert eval_rejected["is_active"] is False
    assert eval_rejected["rejection_reason_audit"] is not None
    assert "Rechazada:" in eval_rejected["rejection_reason_audit"]
    assert "1.45" in eval_rejected["rejection_reason_audit"]
    assert "1.18" in eval_rejected["rejection_reason_audit"]


def test_contextual_market_memory_matrix():
    """Verify that contextual market memory returns rich multivariable records."""
    validator = AdaptiveValidationEngine()
    matrix = validator.get_contextual_pullback_matrix()

    assert len(matrix) >= 4
    context_ids = [c["context_id"] for c in matrix]
    assert "london_clean_breakout_normal" in context_ids
    assert "ny_sweep_highvol" in context_ids
    assert "vertical_spike_exhaustion" in context_ids

    # Check that each record contains multivariable metadata
    for c in matrix:
        assert "session" in c
        assert "atr_regime" in c
        assert "sample_size" in c
        assert "continuation_rate" in c
        assert "median_pullback_usd" in c
        assert "median_mfe_usd" in c
        assert "median_mae_usd" in c
        assert "recommended_action" in c


def test_decision_audit_trail_in_autopsy():
    """Verify that every generated trade autopsy contains decision audit trail metadata."""
    engine = TradeAutopsyEngine()

    mock_trade = SimpleNamespace(
        ticket=1901182930, symbol="XAUUSD", direction="SELL",
        entry_price=4330.0, exit_price=4295.0, sl_price=4350.0, tp_price=4240.0,
        volume=0.02, profit=70.0, profit_r=0.7,
        open_time=SimpleNamespace(strftime=lambda fmt: "2026-09-02 14:00"),
        close_time=SimpleNamespace(strftime=lambda fmt: "2026-09-02 15:30")
    )

    report = engine.analyze_trade(mock_trade)

    assert hasattr(report, "active_rules")
    assert hasattr(report, "consulted_memory")
    assert hasattr(report, "applied_mod")
    assert hasattr(report, "confidence_ic")
    assert hasattr(report, "rule_consecutive_losses")
    assert hasattr(report, "decision_verdict")

    assert "v1.0 Base" in report.active_rules[0]
    assert "xauusd_breakout_pullback" in report.consulted_memory
    assert "[30.1% – 95.4%]" in report.confidence_ic
