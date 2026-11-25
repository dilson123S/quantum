"""Unit tests for Stage 4.2: Robust Statistical Validation, Bootstrap, OOS Gate, Execution Costs, DIR, and Regret."""
import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

from learning.validation_engine import AdaptiveValidationEngine
from learning.learning_lab import LearningPerformanceLab


def test_expectancy_distributional_stats_and_stability_ratio():
    """Verify that Expectancy calculates R Stability Ratio (E[R]/σ(R)) alongside mean and median."""
    validator = AdaptiveValidationEngine()

    trades = [
        SimpleNamespace(profit_r=1.0),
        SimpleNamespace(profit_r=1.5),
        SimpleNamespace(profit_r=2.0),
        SimpleNamespace(profit_r=-0.5),
        SimpleNamespace(profit_r=-1.0),
    ]

    exp = validator.compute_expectancy(trades)
    assert exp["expectancy_r"] > 0
    assert exp["median_r"] == 1.0
    assert exp["std_dev_r"] > 0
    assert exp["r_stability_ratio"] > 0
    assert exp["r_sharpe"] == exp["r_stability_ratio"]  # backwards compatibility
    assert "R Stability Ratio" in exp["explanation"]
    assert "E[R]/σ(R)" in exp["explanation"]


def test_statistical_degradation_hysteresis():
    """Verify that statistical degradation incorporates hysteresis to prevent rapid flapping."""
    validator = AdaptiveValidationEngine()

    baseline = {"profit_factor": 1.45, "expectancy_r": 0.25, "max_drawdown_pct": 3.0}
    degraded_sample = {
        "sample_size": 20,
        "profit_factor": 1.10,
        "expectancy_r": 0.10,
        "max_drawdown_pct": 4.8
    }

    eval_1 = validator.evaluate_statistical_degradation(
        degraded_sample, baseline, consecutive_degradations=0, min_degradation_streak=3
    )
    assert eval_1["is_degraded"] is False
    assert eval_1["action"] == "MONITOR_HYSTERESIS"

    eval_3 = validator.evaluate_statistical_degradation(
        degraded_sample, baseline, consecutive_degradations=2, min_degradation_streak=3
    )
    assert eval_3["is_degraded"] is True
    assert eval_3["action"] == "DEMOTE_TO_CANDIDATE"


def test_lookahead_barrier_validation():
    """Verify look-ahead barrier prevents using future data at decision timestamp."""
    validator = AdaptiveValidationEngine()

    t_decision = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    t_past = datetime(2026, 9, 3, 11, 59, tzinfo=timezone.utc)
    t_future = datetime(2026, 9, 3, 12, 1, tzinfo=timezone.utc)

    assert validator.validate_lookahead_barrier(t_decision, t_past) is True
    assert validator.validate_lookahead_barrier(t_decision, t_future) is False


def test_statistical_priority_tier_and_low_n_guard():
    """Verify statistical priority tiers and low-N guard for bootstrap inference."""
    validator = AdaptiveValidationEngine()

    # N < 5 -> Descriptive, not confirmative
    tier_2 = validator.get_statistical_priority_tier(2)
    assert tier_2["tier"] == "DESCRIPTIVO"
    assert tier_2["is_interpretable_ci"] is False
    assert "⚠️ IC no interpretable" in tier_2["badge_warning"]

    # Bootstrap on N=2: even if strictly positive, it must NOT be marked significant
    boot_2 = validator.compute_paired_bootstrap([0.36, 0.46], n_iterations=1000, seed=42)
    assert boot_2["sample_size"] == 2
    assert boot_2["is_significant"] is False  # Guard active!
    assert "⚠️ IC no interpretable" in boot_2["ci_warning"]

    # 15 <= N < 30 -> Exploratory
    tier_20 = validator.get_statistical_priority_tier(20)
    assert tier_20["tier"] == "INFERENCIA EXPLORATORIA"
    assert tier_20["is_interpretable_ci"] is True

    # N >= 50 -> Contextual
    tier_60 = validator.get_statistical_priority_tier(60)
    assert tier_60["tier"] == "VALIDACIÓN CONTEXTUAL"


def test_paired_bootstrap_resampling():
    """Verify non-parametric paired bootstrap computation on delta R."""
    validator = AdaptiveValidationEngine()

    # Scenario A: Clear positive shift with N=15 (exceeds low-N guard)
    deltas_positive = [0.20, 0.35, 0.15, -0.05, 0.40, 0.25, 0.30, 0.10, 0.22, 0.18, -0.02, 0.28, 0.31, 0.19, 0.24]
    boot_pos = validator.compute_paired_bootstrap(deltas_positive, n_iterations=1000, seed=42)

    assert boot_pos["sample_size"] == 15
    assert boot_pos["mean_delta_r"] > 0.15
    assert boot_pos["ci_lower_95"] > 0.0  # Confirmed: excludes zero!
    assert boot_pos["excludes_zero"] is True
    assert boot_pos["is_significant"] is True
    assert "[" in boot_pos["display_ci"] and "]" in boot_pos["display_ci"]

    # Scenario B: Noise around zero
    deltas_noise = [-0.10, 0.12, -0.08, 0.05, -0.04, 0.02, -0.06, 0.07, -0.01, 0.03, -0.02, 0.04]
    boot_noise = validator.compute_paired_bootstrap(deltas_noise, n_iterations=1000, seed=42)
    assert boot_noise["ci_lower_95"] < 0.0 and boot_noise["ci_upper_95"] > 0.0
    assert boot_noise["is_significant"] is False


def test_paired_permutation_test():
    """Verify sign-flipping permutation test under H0: mean(delta R) == 0."""
    validator = AdaptiveValidationEngine()

    # Strong positive signal with N=15
    deltas_strong = [0.30, 0.45, 0.25, 0.35, 0.40, 0.28, 0.32, 0.50, 0.22, 0.38, 0.29, 0.33, 0.41, 0.27, 0.36]
    perm_strong = validator.compute_paired_permutation_test(deltas_strong, n_iterations=1000, seed=42)
    assert perm_strong["p_value"] < 0.01
    assert perm_strong["is_significant"] is True
    assert "significancia preliminar" in perm_strong["status_label"]

    # Small sample N=2 -> MUST be flagged as insufficient evidence
    deltas_small = [0.36, 0.46]
    perm_small = validator.compute_paired_permutation_test(deltas_small, n_iterations=1000, seed=42)
    assert perm_small["is_significant"] is False
    assert "evidencia insuficiente" in perm_small["status_label"]


def test_execution_friction_deduction():
    """Verify that execution costs (spread, slippage, commission) are properly deducted from gross R."""
    validator = AdaptiveValidationEngine()

    cost_eval = validator.apply_execution_costs(
        gross_r=0.04, spread_usd=0.80, slippage_usd=0.50, commission_usd=0.20, risk_usd=50.0
    )
    assert cost_eval["gross_r"] == 0.04
    assert cost_eval["cost_r"] == 0.030
    assert cost_eval["net_r"] == 0.010
    assert cost_eval["survives_friction"] is True
    assert "XAUUSD MT5" in cost_eval["contract_spec"]

    cost_fail = validator.apply_execution_costs(
        gross_r=0.02, spread_usd=0.80, slippage_usd=0.50, commission_usd=0.20, risk_usd=50.0
    )
    assert cost_fail["net_r"] == -0.010
    assert cost_fail["survives_friction"] is False


def test_out_of_sample_gate_enforcement():
    """Verify that Out-of-Sample Holdout Gate blocks promotion when holdout is insufficient or unproven."""
    validator = AdaptiveValidationEngine()

    in_sample = {"profit_factor": 1.45, "expectancy_r": 0.22, "max_drawdown_pct": 3.2}

    # Case A: N_oos = 4 (< 15 required) -> MUST BE LOCKED
    holdout_small = {"sample_size": 4, "profit_factor": 1.40, "expectancy_r": 0.20, "max_drawdown_pct": 3.4}
    gate_small = validator.evaluate_out_of_sample_gate(in_sample, holdout_small, min_oos_trades=15)
    assert gate_small["gate_passed"] is False
    assert gate_small["gate_status"] == "LOCKED_IN_OBSERVATION"

    # Case B: N_oos = 20 with sustained out-of-sample edge -> APPROVED
    holdout_good = {"sample_size": 20, "profit_factor": 1.42, "expectancy_r": 0.21, "max_drawdown_pct": 3.5}
    gate_good = validator.evaluate_out_of_sample_gate(in_sample, holdout_good, min_oos_trades=15)
    assert gate_good["gate_passed"] is True
    assert gate_good["gate_status"] == "APPROVED_FOR_ACTIVE"


def test_incremental_learning_and_opportunity_regret():
    """Verify Incremental Learning Value, DIR, and Opportunity Regret calculation."""
    validator = AdaptiveValidationEngine()

    mock_paired = [
        {"opportunity_id": "1", "delta_r": 0.35, "baseline": {"result_r": 0.40}, "candidate": {"result_r": 0.75}},
        {"opportunity_id": "2", "delta_r": 0.20, "baseline": {"result_r": -0.10}, "candidate": {"result_r": 0.10}},
        {"opportunity_id": "3", "delta_r": -0.30, "baseline": {"result_r": 0.50}, "candidate": {"result_r": 0.20}},
        {"opportunity_id": "4", "delta_r": 0.02, "baseline": {"result_r": 0.30}, "candidate": {"result_r": 0.32}},
    ]

    metrics = validator.compute_incremental_learning_metrics(mock_paired)
    assert metrics["total_opportunities"] == 4
    assert metrics["better_decisions"] == 2
    assert metrics["worse_decisions"] == 1
    assert metrics["neutral_decisions"] == 1
    assert metrics["decision_improvement_rate"] == 50.0

    # Opportunity 3 had delta_r = -0.30 -> regret = 0.30R
    assert metrics["worst_regret_r"] == 0.30
    assert len(metrics["adaptive_error_memory"]) == 1
    assert metrics["adaptive_error_memory"][0]["opportunity_id"] == "3"


def test_rejected_learnings_and_sample_roadmap():
    """Verify negative knowledge tracking and sample maturity roadmap."""
    lab = LearningPerformanceLab()

    rejected = lab.get_rejected_learnings()
    assert len(rejected) >= 3
    for r in rejected:
        assert r["verdict"] == "RECHAZADA"
        assert "bot_takeaway" in r

    roadmap = lab.get_sample_maturity_roadmap(current_trades=4)
    assert len(roadmap) == 4
    assert roadmap[0]["tier"].startswith("Tier 1")
    assert roadmap[0]["is_active"] is True
    assert roadmap[1]["is_active"] is False


def test_paired_shadow_preview_with_guards():
    """Verify paired shadow live comparison with non-parametric bootstrap and low-N guards."""
    lab = LearningPerformanceLab()

    preview = lab.get_paired_shadow_preview(total_trades=4)
    assert "Bootstrap" in preview["method"]
    assert preview["total_paired_opportunities"] == 2
    assert preview["is_sufficient"] is False
    assert "MUESTRA INSUFICIENTE" in preview["status"]

    # Low-N guard verified
    assert "⚠️ IC no interpretable como evidencia confirmatoria — N=2" in preview["ci_warning"]
    assert "evidencia insuficiente" in preview["permutation_label"]


def test_single_use_holdout_metadata_and_bot_voice():
    """Verify single-use holdout metadata and epistemic voice of the bot."""
    lab = LearningPerformanceLab()

    oos_status = lab.get_out_of_sample_gate_status(total_trades=4)
    meta = oos_status["rule_freeze_metadata"]
    assert meta["rule_version"] == "v1.3"
    assert meta["holdout_id"] == "H-001"
    assert meta["holdout_status"] == "LOCKED / UNCONSUMED"
    assert "prohibido reutilizarse" in meta["single_use_policy"].lower() or "anti-data snooping" in meta["single_use_policy"].lower()

    # Executive conclusion voice with N=4 (N_paired = 2)
    conclusion = lab.generate_executive_conclusion(
        head_to_head={"baseline": {"expectancy_r": 0.18}, "adaptive": {"expectancy_r": 0.26}, "deltas": {}},
        evidence={"trades_evaluated": 4}
    )
    quote = conclusion["quote"]
    assert "Tengo 2 observaciones emparejadas" in quote
    assert "p=0.503" in quote
    assert "Mantengo la regla bloqueada" in quote
    assert "Holdout H-001" in quote


def test_full_lab_payload_stage42_integration():
    """Verify get_full_lab_payload assembles all Stage 4.2 components."""
    lab = LearningPerformanceLab()

    mock_trades = [
        SimpleNamespace(profit=61.14, profit_r=0.33),
        SimpleNamespace(profit=-0.35, profit_r=-0.01),
        SimpleNamespace(profit=3.86, profit_r=0.03),
        SimpleNamespace(profit=75.52, profit_r=0.54),
    ]

    payload = lab.get_full_lab_payload(mock_trades)

    # Check new Stage 4.2 sections
    assert "experimentation_budget" in payload
    assert "out_of_sample_gate" in payload
    assert "incremental_learning" in payload
    assert "paired_shadow_comparison" in payload

    # Check experimentation budget
    exp = payload["experimentation_budget"]
    assert exp["hypotheses_generated"] == 27
    assert exp["hypotheses_tested"] == 14
    assert exp["hypotheses_accepted"] == 2

    # Check Out-of-Sample Holdout Gate
    oos = payload["out_of_sample_gate"]
    assert oos["gate_status"] == "LOCKED_IN_OBSERVATION"
    assert "Priors Históricos" in oos["partitions"]["discovery_set"]
    assert "H-001" in oos["partitions"]["holdout_set"]

    # Check R Stability Ratio
    assert payload["head_to_head"]["baseline"]["r_stability_ratio"] == 0.42
    assert payload["head_to_head"]["adaptive"]["r_stability_ratio"] == 0.58
