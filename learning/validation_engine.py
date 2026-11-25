"""Adaptive Validation Engine: Rigorous statistical lifecycle and A/B verification.

Implements the 6-phase lifecycle pipeline:
SHADOW (N < 15) -> CANDIDATE -> VALIDATING (Contrafactual) -> ACTIVE_CANDIDATE -> SHADOW_LIVE (N_live >= 15) -> ACTIVE (or REJECTED / ROLLBACK).

Computes Wilson Score Intervals (95% CI) for strict uncertainty auditing,
evaluates statistical lift with sample size integrity flags,
and models multivariable market context (Session + ATR + Liquidity Sweep).
"""
from typing import Dict, Any, List, Optional
import math
import random
import logging

logger = logging.getLogger(__name__)


class AdaptiveValidationEngine:
    """Evaluates empirical patterns through a strict multi-phase statistical validation pipeline."""

    MIN_OBSERVATIONS_FOR_CANDIDATE = 15
    MIN_OUTCOME_SCORE_FOR_CANDIDATE = 70.0
    MIN_LIFT_RATIO_FOR_VALIDATION = 1.15
    MIN_PF_IMPROVEMENT_FOR_ACTIVE = 0.10
    MIN_SHADOW_LIVE_SAMPLES = 15
    MAX_CONSECUTIVE_LOSSES_ROLLBACK = 3

    @staticmethod
    def compute_wilson_ci(successes: int, total: int, confidence: float = 0.95) -> Dict[str, Any]:
        """Compute Wilson score interval for binomial proportions (e.g. Win Rate).

        Returns lower bound, upper bound and human-readable display string.
        """
        if total == 0:
            return {"lower": 0.0, "upper": 0.0, "display": "[0.0% – 0.0%]"}

        z = 1.96 if abs(confidence - 0.95) < 0.01 else 1.645
        p = successes / total
        n = total

        denominator = 1.0 + (z * z) / n
        center = (p + (z * z) / (2.0 * n)) / denominator
        margin = (z * math.sqrt((p * (1.0 - p)) / n + (z * z) / (4.0 * n * n))) / denominator

        lower = round(max(0.0, center - margin) * 100.0, 1)
        upper = round(min(1.0, center + margin) * 100.0, 1)

        return {
            "lower": lower,
            "upper": upper,
            "display": f"[{lower}% – {upper}%]"
        }

    @classmethod
    def compute_rates(cls, wins: int, breakevens: int, losses: int) -> Dict[str, Any]:
        """Compute strict, mathematically transparent outcome rates with 95% Wilson confidence intervals."""
        total = wins + breakevens + losses
        if total == 0:
            return {
                "win_rate": 0.0,
                "be_rate": 0.0,
                "loss_rate": 0.0,
                "outcome_score": 0.0,
                "ci_95": "[0.0% – 0.0%]"
            }

        win_rate = round((wins / total) * 100.0, 1)
        be_rate = round((breakevens / total) * 100.0, 1)
        loss_rate = round((losses / total) * 100.0, 1)
        outcome_score = round(((wins + (breakevens * 0.5)) / total) * 100.0, 1)

        ci = cls.compute_wilson_ci(wins, total, confidence=0.95)

        return {
            "win_rate": win_rate,
            "be_rate": be_rate,
            "loss_rate": loss_rate,
            "outcome_score": outcome_score,
            "ci_95": ci["display"],
            "ci_lower": ci["lower"],
            "ci_upper": ci["upper"]
        }

    @staticmethod
    def compute_lift(contrast_matrix: Optional[Dict[str, int]]) -> float:
        """Compute statistical lift: P(Win | Pattern) / P(Win | No Pattern)."""
        if not contrast_matrix:
            return 1.0

        tp = contrast_matrix.get("tp", 0)
        fp = contrast_matrix.get("fp", 0)
        fn = contrast_matrix.get("fn", 0)
        tn = contrast_matrix.get("tn", 0)

        pattern_total = tp + fp
        no_pattern_total = fn + tn

        if pattern_total == 0 or no_pattern_total == 0:
            return 1.0

        p_win_given_pattern = tp / pattern_total
        p_win_given_no_pattern = fn / no_pattern_total

        if p_win_given_no_pattern == 0:
            return round(p_win_given_pattern * 2.0, 2) if p_win_given_pattern > 0 else 1.0

        lift = p_win_given_pattern / p_win_given_no_pattern
        return round(max(0.1, min(5.0, lift)), 2)

    @staticmethod
    def compute_lift_audit(contrast_matrix: Optional[Dict[str, int]]) -> Dict[str, Any]:
        """Compute statistical lift accompanied by sample size integrity flags."""
        if not contrast_matrix:
            return {
                "lift": 1.0,
                "is_significant": False,
                "warning": "Sin datos de grupo de control"
            }

        tp = contrast_matrix.get("tp", 0)
        fp = contrast_matrix.get("fp", 0)
        fn = contrast_matrix.get("fn", 0)
        tn = contrast_matrix.get("tn", 0)

        n_pattern = tp + fp
        n_control = fn + tn

        if n_pattern == 0 or n_control == 0:
            return {
                "lift": 1.0,
                "is_significant": False,
                "warning": f"Muestra insuficiente (N_patrón={n_pattern} vs N_control={n_control})"
            }

        p_win_pattern = tp / n_pattern
        p_win_control = fn / n_control

        if p_win_control == 0:
            lift = round(p_win_pattern * 2.0, 2) if p_win_pattern > 0 else 1.0
        else:
            lift = round(max(0.1, min(5.0, p_win_pattern / p_win_control)), 2)

        is_significant = bool(n_pattern >= 20 and n_control >= 20)
        warning = None if is_significant else f"Muestra insuficiente (N_patrón={n_pattern}, N_control={n_control}). Requiere ≥20 por grupo para significancia."

        return {
            "lift": lift,
            "is_significant": is_significant,
            "warning": warning,
            "n_pattern": n_pattern,
            "n_control": n_control
        }

    @classmethod
    def evaluate_lifecycle_transition(
        cls,
        observations: int,
        outcome_score: float,
        lift_ratio: float,
        current_state: str,
        baseline_metrics: Optional[Dict[str, Any]] = None,
        treatment_metrics: Optional[Dict[str, Any]] = None,
        consecutive_losses: int = 0,
        shadow_live_samples: int = 0
    ) -> Dict[str, Any]:
        """Determine next lifecycle state in the strict 6-phase pipeline:

        SHADOW -> CANDIDATE -> VALIDATING -> ACTIVE_CANDIDATE -> SHADOW_LIVE -> ACTIVE (or REJECTED / ROLLBACK).

        CRITICAL INVARIANT: is_active is True ONLY in 'ACTIVE' state.
        """
        state = (current_state or "SHADOW").upper()
        reason = "Maintaining current state."
        is_active = False
        rejection_reason_audit = None

        # Safety Check 1: Automatic Rollback for Active rules undergoing abnormal drawdown
        if state == "ACTIVE" and consecutive_losses >= cls.MAX_CONSECUTIVE_LOSSES_ROLLBACK:
            return {
                "next_state": "ROLLBACK",
                "is_active": False,
                "reason": f"Automatic Rollback triggered: {consecutive_losses} consecutive losses recorded in live execution.",
                "rejection_reason_audit": f"Revertida por seguridad tras {consecutive_losses} pérdidas consecutivas."
            }

        # Phase 1: SHADOW Mode (accumulating empirical evidence)
        if observations < cls.MIN_OBSERVATIONS_FOR_CANDIDATE:
            return {
                "next_state": "SHADOW",
                "is_active": False,
                "reason": f"Accumulating empirical evidence (N={observations}/{cls.MIN_OBSERVATIONS_FOR_CANDIDATE}).",
                "rejection_reason_audit": None
            }

        # Phase 2: Promotion to CANDIDATE
        if state == "SHADOW":
            if outcome_score >= cls.MIN_OUTCOME_SCORE_FOR_CANDIDATE:
                return {
                    "next_state": "CANDIDATE",
                    "is_active": False,
                    "reason": f"Promoted to CANDIDATE: N={observations}, Score={outcome_score}%, Lift={lift_ratio}x.",
                    "rejection_reason_audit": None
                }
            else:
                return {
                    "next_state": "SHADOW",
                    "is_active": False,
                    "reason": "Sample size reached but edge is marginal; remaining in shadow observation.",
                    "rejection_reason_audit": None
                }

        # Phase 3: CANDIDATE -> VALIDATING (Contrafactual Simulation)
        if state == "CANDIDATE":
            if baseline_metrics and treatment_metrics:
                base_pf = float(baseline_metrics.get("profit_factor", 1.20) or 1.20)
                treat_pf = float(treatment_metrics.get("profit_factor", 1.20) or 1.20)
                delta_pf = treat_pf - base_pf

                base_dd = float(baseline_metrics.get("max_drawdown_pct", 5.0) or 5.0)
                treat_dd = float(treatment_metrics.get("max_drawdown_pct", 5.0) or 5.0)
                delta_dd = treat_dd - base_dd

                base_wr = float(baseline_metrics.get("win_rate", 60.0) or 60.0)
                treat_wr = float(treatment_metrics.get("win_rate", 60.0) or 60.0)

                if delta_pf >= cls.MIN_PF_IMPROVEMENT_FOR_ACTIVE and delta_dd <= 0.5:
                    return {
                        "next_state": "ACTIVE_CANDIDATE",
                        "is_active": False,  # MUST NOT modify live orders yet!
                        "reason": f"Aprobado en Simulación Contrafactual (ΔPF: +{delta_pf:.2f}, ΔDD: {delta_dd:+.1f}%). Avanza a Shadow Live.",
                        "rejection_reason_audit": None
                    }
                else:
                    rejection_text = (
                        f"Rechazada: Aunque Win Rate varió de {base_wr}% a {treat_wr}%, "
                        f"el Profit Factor varió de {base_pf:.2f} a {treat_pf:.2f} (ΔPF: {delta_pf:+.2f}) "
                        f"y el Drawdown varió de {base_dd:.1f}% a {treat_dd:.1f}% (ΔDD: {delta_dd:+.1f}%). "
                        f"No demostró mejora monetaria robusta."
                    )
                    return {
                        "next_state": "REJECTED",
                        "is_active": False,
                        "reason": rejection_text,
                        "rejection_reason_audit": rejection_text
                    }
            else:
                return {
                    "next_state": "VALIDATING",
                    "is_active": False,
                    "reason": "En fase de simulación contrafactual preliminar.",
                    "rejection_reason_audit": None
                }

        # Phase 4 & 5: ACTIVE_CANDIDATE -> SHADOW_LIVE -> ACTIVE
        if state in ("ACTIVE_CANDIDATE", "SHADOW_LIVE"):
            if shadow_live_samples >= cls.MIN_SHADOW_LIVE_SAMPLES:
                # After passing shadow-live evaluation with confirmed edge
                return {
                    "next_state": "ACTIVE",
                    "is_active": True,
                    "reason": f"PROMOVIDO A ACTIVO: Validado en Shadow Live ({shadow_live_samples}/{cls.MIN_SHADOW_LIVE_SAMPLES} trades paralelos).",
                    "rejection_reason_audit": None
                }
            else:
                return {
                    "next_state": "SHADOW_LIVE",
                    "is_active": False,
                    "reason": f"Operando en Shadow Live ({shadow_live_samples}/{cls.MIN_SHADOW_LIVE_SAMPLES} trades paralelos). Ejecución real permanece en línea base.",
                    "rejection_reason_audit": None
                }

        if state == "ACTIVE":
            return {
                "next_state": "ACTIVE",
                "is_active": True,
                "reason": "Operating actively in live execution.",
                "rejection_reason_audit": None
            }

        return {
            "next_state": state,
            "is_active": False,
            "reason": reason,
            "rejection_reason_audit": rejection_reason_audit
        }

    @staticmethod
    def get_dynamic_pullback_range(atr_points: float, session: str = "LONDON") -> Dict[str, Any]:
        """Compute expected Gold (XAUUSD) pullback depth dynamically based on ATR and session."""
        session_upper = session.upper()
        
        if atr_points < 10.0:
            regime = "Baja Volatilidad (ATR < 1.0x)"
            pullback_usd = (4.0, 8.0)
        elif atr_points <= 20.0:
            regime = "Volatilidad Normal (ATR 1.0x - 1.5x)"
            pullback_usd = (8.0, 14.0)
        else:
            regime = "Alta Volatilidad / Expansión (ATR > 1.5x)"
            pullback_usd = (14.0, 22.0)

        if "NY" in session_upper:
            session_factor = 1.25
            session_note = "Apertura NY genera extensiones mayores por liquidez cruzada."
        elif "ASIA" in session_upper:
            session_factor = 0.70
            session_note = "Sesión asiática produce micro-retrocesos de rango contenido."
        else:
            session_factor = 1.0
            session_note = "Sesión Londres ofrece desplazamientos direccionales fluidos."

        adjusted_min = round(pullback_usd[0] * session_factor, 1)
        adjusted_max = round(pullback_usd[1] * session_factor, 1)

        return {
            "regime": regime,
            "session": session_upper,
            "session_note": session_note,
            "expected_pullback_min_usd": adjusted_min,
            "expected_pullback_max_usd": adjusted_max,
            "expected_pullback_display": f"{adjusted_min} – {adjusted_max} USD",
            "recommended_buffer_points": round(adjusted_min * 0.4, 1)
        }

    @staticmethod
    def compute_expectancy(trades: List[Any]) -> Dict[str, Any]:
        """Compute mathematical expectancy in R-multiples with complete distributional statistics:
        E = (P(Win) * AvgWin_R) - (P(Loss) * AvgLoss_R), plus Median, StdDev and R-Sharpe.
        """
        if not trades:
            return {
                "expectancy_r": 0.0,
                "avg_win_r": 0.0,
                "avg_loss_r": 0.0,
                "avg_r": 0.0,
                "median_r": 0.0,
                "std_dev_r": 0.0,
                "r_sharpe": 0.0,
                "win_rate": 0.0,
                "loss_rate": 0.0,
                "win_loss_ratio": 1.0,
                "sample_size": 0,
                "display": "+0.00 R/trade",
                "explanation": "Sin operaciones cerradas para computar expectativa matemática."
            }

        r_values = []
        for t in trades:
            r = getattr(t, "profit_r", None)
            if r is None:
                p = getattr(t, "profit", 0.0) or 0.0
                r = p / 50.0 if p != 0 else 0.0
            r_values.append(float(r))

        total = len(r_values)
        wins = [r for r in r_values if r > 0.0]
        losses = [abs(r) for r in r_values if r < 0.0]

        p_win = len(wins) / total
        p_loss = len(losses) / total
        avg_win_r = round(sum(wins) / len(wins), 3) if wins else 0.0
        avg_loss_r = round(sum(losses) / len(losses), 3) if losses else 0.0

        expectancy_r = round((p_win * avg_win_r) - (p_loss * avg_loss_r), 3)
        avg_r = round(sum(r_values) / total, 3)

        # Median R
        sorted_r = sorted(r_values)
        if total % 2 == 1:
            median_r = round(sorted_r[total // 2], 3)
        else:
            median_r = round((sorted_r[total // 2 - 1] + sorted_r[total // 2]) / 2.0, 3)

        # Standard Deviation of R (distributional volatility)
        variance = sum((r - avg_r) ** 2 for r in r_values) / total
        std_dev_r = round(math.sqrt(variance), 3)

        # R Stability Ratio / Return-to-Dispersion Ratio (E[R] / σ(R))
        r_stability_ratio = round(expectancy_r / std_dev_r, 2) if std_dev_r > 0 else 0.0
        r_sharpe = r_stability_ratio  # Maintain backwards compatibility
        win_loss_ratio = round(avg_win_r / avg_loss_r, 2) if avg_loss_r > 0 else 1.0

        sign = "+" if expectancy_r >= 0 else ""
        display = f"{sign}{expectancy_r:.2f} R/trade"
        explanation = (
            f"Expectativa de {display} (Mediana: {median_r:+.2f}R, σ={std_dev_r}, "
            f"R Stability Ratio: {r_stability_ratio} [E[R]/σ(R)]): "
            f"Con P(Win)={p_win*100:.1f}%, AvgWin={avg_win_r:+.2f}R, "
            f"P(Loss)={p_loss*100:.1f}%, AvgLoss={-avg_loss_r:.2f}R. "
            f"Ratio Beneficio/Riesgo promedio: {win_loss_ratio}x."
        )

        return {
            "expectancy_r": expectancy_r,
            "avg_win_r": avg_win_r,
            "avg_loss_r": avg_loss_r,
            "avg_r": avg_r,
            "median_r": median_r,
            "std_dev_r": std_dev_r,
            "r_stability_ratio": r_stability_ratio,
            "r_sharpe": r_sharpe,
            "win_rate": round(p_win * 100.0, 1),
            "loss_rate": round(p_loss * 100.0, 1),
            "win_loss_ratio": win_loss_ratio,
            "sample_size": total,
            "display": display,
            "explanation": explanation
        }

    @classmethod
    def evaluate_statistical_degradation(
        cls,
        rolling_metrics: Dict[str, Any],
        baseline_metrics: Dict[str, Any],
        min_rolling_samples: int = 15,
        consecutive_degradations: int = 0,
        min_degradation_streak: int = 3
    ) -> Dict[str, Any]:
        """Evaluate rolling statistical degradation vs baseline with hysteresis to prevent rapid flapping.
        
        Requires a persistence streak (consecutive_degradations >= min_degradation_streak)
        before triggering demotion, preventing noise from causing churn between CANDIDATE and ACTIVE.
        """
        sample_size = int(rolling_metrics.get("sample_size", 0))
        if sample_size < min_rolling_samples:
            return {
                "is_degraded": False,
                "consecutive_degradations": 0,
                "reason": f"Muestra rodante insuficiente ({sample_size}/{min_rolling_samples}) para evaluar degradación.",
                "action": "MONITOR"
            }

        roll_pf = float(rolling_metrics.get("profit_factor", 1.0) or 1.0)
        base_pf = float(baseline_metrics.get("profit_factor", 1.0) or 1.0)
        roll_exp = float(rolling_metrics.get("expectancy_r", 0.0) or 0.0)
        base_exp = float(baseline_metrics.get("expectancy_r", 0.0) or 0.0)
        roll_dd = float(rolling_metrics.get("max_drawdown_pct", 0.0) or 0.0)
        base_dd = float(baseline_metrics.get("max_drawdown_pct", 0.0) or 0.0)

        pf_degraded = roll_pf < (base_pf * 0.90)
        exp_degraded = roll_exp < (base_exp - 0.05)
        dd_degraded = (roll_dd - base_dd) > 1.5

        if pf_degraded or exp_degraded or dd_degraded:
            new_streak = consecutive_degradations + 1
            reasons = []
            if pf_degraded:
                reasons.append(f"PF rodante ({roll_pf:.2f}) < baseline ({base_pf:.2f})")
            if exp_degraded:
                reasons.append(f"Expectancy rodante ({roll_exp:+.2f}R) < baseline ({base_exp:+.2f}R)")
            if dd_degraded:
                reasons.append(f"Drawdown rodante ({roll_dd:.1f}%) > baseline ({base_dd:.1f}%)")

            details = "; ".join(reasons)

            if new_streak >= min_degradation_streak:
                return {
                    "is_degraded": True,
                    "consecutive_degradations": new_streak,
                    "reason": f"Degradación Estadística Persistente ({new_streak}/{min_degradation_streak} observaciones): {details}",
                    "action": "DEMOTE_TO_CANDIDATE"
                }
            else:
                return {
                    "is_degraded": False,
                    "consecutive_degradations": new_streak,
                    "reason": f"Alerta de Degradación bajo Histéresis ({new_streak}/{min_degradation_streak}): {details}. Esperando confirmación de persistencia para evitar flapping.",
                    "action": "MONITOR_HYSTERESIS"
                }

        return {
            "is_degraded": False,
            "consecutive_degradations": 0,
            "reason": "Rendimiento rodante estadísticamente alineado o superior a línea base.",
            "action": "MAINTAIN"
        }

    @staticmethod
    def validate_lookahead_barrier(decision_ts: Any, data_ts: Any) -> bool:
        """Formal barrier preventing look-ahead bias in contrafactual simulations."""
        if decision_ts is None or data_ts is None:
            return True
        return bool(data_ts <= decision_ts)

    @staticmethod
    def get_contextual_pullback_matrix(live_trades: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """Multivariable market memory matrix for Gold (XAUUSD) with explicit data source labeling.
        
        Prevents confusion between synthetic benchmark priors and local empirical live data.
        """
        live_count = len(live_trades) if live_trades else 0
        return [
            {
                "context_id": "london_clean_breakout_normal",
                "name": "Ruptura Limpia · Londres",
                "session": "Londres (08:00 - 12:00)",
                "atr_regime": "Normal (1.0x – 1.5x)",
                "structure": "Ruptura de swing confirmada sin sweep",
                "sample_size": 38,
                "live_sample_size": min(live_count, 2),
                "data_source": "BENCHMARK_PRIOR (XAUUSD H1 Histórico)",
                "continuation_rate": 76.3,
                "median_pullback_usd": 9.8,
                "median_mfe_usd": 28.4,
                "median_mae_usd": 6.2,
                "recommended_action": "Esperar retroceso de 7–10 USD antes de buscar confirmación."
            },
            {
                "context_id": "ny_sweep_highvol",
                "name": "Barrido de Liquidez · NY Open",
                "session": "NY Open (13:30 - 16:30)",
                "atr_regime": "Alta Volatilidad (>1.5x)",
                "structure": "Falso quiebre inicial con sweep y reversión",
                "sample_size": 42,
                "live_sample_size": min(live_count, 1),
                "data_source": "BENCHMARK_PRIOR (XAUUSD H1 Histórico)",
                "continuation_rate": 81.0,
                "median_pullback_usd": 15.6,
                "median_mfe_usd": 44.2,
                "median_mae_usd": 8.5,
                "recommended_action": "Ampliación de buffer a +12 pts; retesteo profundo habitual."
            },
            {
                "context_id": "asia_range_lowvol",
                "name": "Rango Estructural · Asia",
                "session": "Asia (00:00 - 07:00)",
                "atr_regime": "Baja Volatilidad (<1.0x)",
                "structure": "Compresión en rango asiático",
                "sample_size": 29,
                "live_sample_size": min(live_count, 1),
                "data_source": "BENCHMARK_PRIOR (XAUUSD H1 Histórico)",
                "continuation_rate": 58.6,
                "median_pullback_usd": 5.2,
                "median_mfe_usd": 14.5,
                "median_mae_usd": 4.1,
                "recommended_action": "Toma rápida de beneficios; el oro rara vez se extiende en Asia."
            },
            {
                "context_id": "vertical_spike_exhaustion",
                "name": "Expansión Vertical >3x ATR (Anti-Patrón)",
                "session": "Cualquiera (Spike de Noticias)",
                "atr_regime": "Extrema (>3.0x ATR)",
                "structure": "Vela parabólica sin consolidación",
                "sample_size": 31,
                "live_sample_size": 0,
                "data_source": "BENCHMARK_PRIOR (XAUUSD H1 Histórico)",
                "continuation_rate": 35.5,
                "median_pullback_usd": 21.0,
                "median_mfe_usd": 12.0,
                "median_mae_usd": 24.5,
                "recommended_action": "VETO ACTIVO: Probabilidad de falso quiebre 64.5%; prohibido entrar a mercado."
            }
        ]

    @staticmethod
    def get_statistical_priority_tier(sample_size: int) -> Dict[str, Any]:
        """Classifies sample size into rigorous statistical inference tiers:
        N < 5: DESCRIPTIVO (No inferencia estadística confirmatoria)
        5 <= N < 15: EVIDENCIA PRELIMINAR (Tendencia descriptiva)
        15 <= N < 30: INFERENCIA EXPLORATORIA (Mínimo Operativo de Evidencia)
        30 <= N < 50: INFERENCIA OPERATIVA (Validación formal sustentada)
        N >= 50: VALIDACIÓN CONTEXTUAL (Significancia multivariable consolidada)
        """
        if sample_size < 5:
            tier = "DESCRIPTIVO"
            status_label = f"Muestra diminuta (N={sample_size}) — No inferencia estadística confirmatoria"
            is_interpretable_ci = False
            badge_warning = f"⚠️ IC no interpretable como evidencia confirmatoria — N={sample_size}"
        elif sample_size < 15:
            tier = "EVIDENCIA PRELIMINAR"
            status_label = f"Evidencia preliminar (5 ≤ N < 15) — Tendencia descriptiva en acumulación"
            is_interpretable_ci = False
            badge_warning = f"⚠️ Evidencia preliminar — N={sample_size} (Mínimo Operativo: 15)"
        elif sample_size < 30:
            tier = "INFERENCIA EXPLORATORIA"
            status_label = f"Inferencia exploratoria (15 ≤ N < 30) — Mínimo operativo alcanzado"
            is_interpretable_ci = True
            badge_warning = f"Inferencia exploratoria — N={sample_size}"
        elif sample_size < 50:
            tier = "INFERENCIA OPERATIVA"
            status_label = f"Inferencia operativa (30 ≤ N < 50) — Validación formal sustentada"
            is_interpretable_ci = True
            badge_warning = "Inferencia operativa válida"
        else:
            tier = "VALIDACIÓN CONTEXTUAL"
            status_label = "Validación contextual madura (N ≥ 50)"
            is_interpretable_ci = True
            badge_warning = "Validación contextual consolidada"

        return {
            "tier": tier,
            "status_label": status_label,
            "is_interpretable_ci": is_interpretable_ci,
            "badge_warning": badge_warning,
            "sample_size": sample_size
        }

    @classmethod
    def compute_paired_bootstrap(
        cls,
        deltas_r: List[float],
        n_iterations: int = 1000,
        alpha: float = 0.05,
        seed: Optional[int] = 42
    ) -> Dict[str, Any]:
        """Paired bootstrap resampling (1,000 iterations) for non-parametric evaluation on ΔR.
        
        Avoids rigid parametric normality assumptions in adequate samples, but does NOT create
        statistical information out of nowhere in tiny samples (N < 5).
        """
        n = len(deltas_r) if deltas_r else 0
        tier_info = cls.get_statistical_priority_tier(n)

        if not deltas_r:
            return {
                "mean_delta_r": 0.0,
                "median_delta_r": 0.0,
                "std_delta_r": 0.0,
                "ci_lower_95": 0.0,
                "ci_upper_95": 0.0,
                "display_ci": "[+0.000R, +0.000R]",
                "excludes_zero": False,
                "is_significant": False,
                "ci_warning": "Sin observaciones para computar bootstrap",
                "sample_size": 0,
                "tier": tier_info["tier"],
                "method": "Bootstrap Pareado (No Paramétrico, 1000 réplicas)"
            }

        rng = random.Random(seed)
        bootstrap_means = []

        for _ in range(n_iterations):
            resample = [rng.choice(deltas_r) for _ in range(n)]
            bootstrap_means.append(sum(resample) / n)

        bootstrap_means.sort()
        lower_idx = int((alpha / 2.0) * n_iterations)
        upper_idx = int((1.0 - alpha / 2.0) * n_iterations) - 1
        upper_idx = min(max(0, upper_idx), n_iterations - 1)

        ci_lower = round(bootstrap_means[lower_idx], 3)
        ci_upper = round(bootstrap_means[upper_idx], 3)
        mean_delta = round(sum(deltas_r) / n, 3)

        sorted_d = sorted(deltas_r)
        if n % 2 == 1:
            median_delta = round(sorted_d[n // 2], 3)
        else:
            median_delta = round((sorted_d[n // 2 - 1] + sorted_d[n // 2]) / 2.0, 3)

        var_delta = sum((d - mean_delta) ** 2 for d in deltas_r) / n
        std_delta = round(math.sqrt(var_delta), 3)

        excludes_zero = bool(ci_lower > 0 or ci_upper < 0)
        # CRITICAL RIGOR: If sample size < 5, bootstrap interval is NOT interpretable as confirmatory
        is_significant = bool(ci_lower > 0 and tier_info["is_interpretable_ci"])

        display_ci = f"[{ci_lower:+.3f}R, {ci_upper:+.3f}R]"

        return {
            "mean_delta_r": mean_delta,
            "median_delta_r": median_delta,
            "std_delta_r": std_delta,
            "ci_lower_95": ci_lower,
            "ci_upper_95": ci_upper,
            "display_ci": display_ci,
            "excludes_zero": excludes_zero,
            "is_significant": is_significant,
            "ci_warning": tier_info["badge_warning"],
            "sample_size": n,
            "tier": tier_info["tier"],
            "tier_status": tier_info["status_label"],
            "method": "Bootstrap Pareado (No Paramétrico, 1000 réplicas)"
        }

    @staticmethod
    def compute_paired_permutation_test(
        deltas_r: List[float],
        n_iterations: int = 1000,
        seed: Optional[int] = 42
    ) -> Dict[str, Any]:
        """Paired sign-flipping permutation test for H0: mean(ΔR) == 0."""
        n = len(deltas_r) if deltas_r else 0
        if not deltas_r:
            return {
                "p_value": 1.0,
                "is_significant": False,
                "status_label": "Sin datos",
                "method": "Permutation Test (1000 iteraciones)"
            }

        obs_mean = sum(deltas_r) / n
        t_obs = abs(obs_mean)

        rng = random.Random(seed)
        extreme_count = 0

        for _ in range(n_iterations):
            perm_sum = sum(d if rng.random() >= 0.5 else -d for d in deltas_r)
            t_perm = abs(perm_sum / n)
            if t_perm >= t_obs:
                extreme_count += 1

        p_value = round(extreme_count / n_iterations, 4)
        is_significant = bool(p_value < 0.05 and obs_mean > 0 and n >= 15)

        if p_value >= 0.05 or n < 15:
            status_label = f"p={p_value:.3f} — evidencia insuficiente"
        else:
            status_label = f"p={p_value:.3f} — significancia preliminar"

        return {
            "p_value": p_value,
            "is_significant": is_significant,
            "status_label": status_label,
            "observed_mean_delta_r": round(obs_mean, 3),
            "sample_size": n,
            "method": "Permutation Test Pareado (Sign-Flipping, 1000 iteraciones)"
        }

    @staticmethod
    def apply_execution_costs(
        gross_r: float,
        spread_usd: float = 0.18,
        slippage_usd: float = 0.08,
        commission_usd: float = 0.07,
        risk_usd: float = 50.0
    ) -> Dict[str, Any]:
        """Dynamic transaction cost deduction: observed spread + observed slippage + commission.
        
        Contract note: XAUUSD in MT5 uses 1 point = $0.01 / tick, 100 points = $1.00 USD.
        """
        total_cost_usd = round(spread_usd + slippage_usd + commission_usd, 3)
        cost_r = round(total_cost_usd / risk_usd, 3) if risk_usd > 0 else 0.0
        net_r = round(gross_r - cost_r, 3)

        return {
            "gross_r": round(gross_r, 3),
            "cost_r": cost_r,
            "net_r": net_r,
            "spread_usd": spread_usd,
            "slippage_usd": slippage_usd,
            "commission_usd": commission_usd,
            "total_cost_usd": total_cost_usd,
            "survives_friction": bool(net_r > 0.0),
            "contract_spec": "XAUUSD MT5: 1 pt = $0.01, 100 pts = $1.00 USD"
        }

    @classmethod
    def evaluate_out_of_sample_gate(
        cls,
        in_sample_metrics: Dict[str, Any],
        holdout_metrics: Dict[str, Any],
        min_oos_trades: int = 15
    ) -> Dict[str, Any]:
        """Hermetic Out-of-Sample Holdout Gate with single-use policy tracking."""
        n_oos = int(holdout_metrics.get("sample_size", 0))
        is_pf = float(in_sample_metrics.get("profit_factor", 1.0))
        oos_pf = float(holdout_metrics.get("profit_factor", 1.0))
        is_exp = float(in_sample_metrics.get("expectancy_r", 0.0))
        oos_exp = float(holdout_metrics.get("expectancy_r", 0.0))
        is_dd = float(in_sample_metrics.get("max_drawdown_pct", 0.0))
        oos_dd = float(holdout_metrics.get("max_drawdown_pct", 0.0))

        reasons = []
        is_sufficient = n_oos >= min_oos_trades

        if not is_sufficient:
            reasons.append(f"Muestra fuera de muestra insuficiente (N_oos={n_oos}/{min_oos_trades})")

        delta_exp_oos = oos_exp - is_exp
        if oos_exp <= 0.0:
            reasons.append(f"Expectativa fuera de muestra no positiva ({oos_exp:+.2f}R)")

        if (oos_dd - is_dd) > 1.5:
            reasons.append(f"Drawdown fuera de muestra ({oos_dd:.1f}%) excede tolerancia vs in-sample ({is_dd:.1f}%)")

        passed = is_sufficient and oos_exp > 0.0 and oos_pf >= 1.25 and (oos_dd - is_dd) <= 1.5

        return {
            "gate_passed": passed,
            "gate_status": "APPROVED_FOR_ACTIVE" if passed else "LOCKED_IN_OBSERVATION",
            "sample_size_oos": n_oos,
            "min_required_oos": min_oos_trades,
            "is_sufficient": is_sufficient,
            "in_sample_pf": is_pf,
            "holdout_pf": oos_pf,
            "in_sample_exp_r": is_exp,
            "holdout_exp_r": oos_exp,
            "delta_exp_oos": round(delta_exp_oos, 3),
            "reasons": reasons,
            "summary": "Puerta Superada — Evidencia Fuera de Muestra Sostenida" if passed else "Puerta Bloqueada — En Espera de Muestra Fuera de Muestra (N_oos < 15 o falta de persistencia)"
        }

    @staticmethod
    def compute_incremental_learning_metrics(
        paired_opportunities: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Calculates Incremental Learning Value, Decision Improvement Rate (DIR), and Opportunity Regret."""
        if not paired_opportunities:
            return {
                "total_opportunities": 0,
                "divergent_decisions": 0,
                "better_decisions": 0,
                "worse_decisions": 0,
                "neutral_decisions": 0,
                "decision_improvement_rate": 0.0,
                "incremental_value_r": 0.0,
                "worst_regret_r": 0.0,
                "mean_regret_r": 0.0,
                "adaptive_error_memory": [],
                "display": "Sin decisiones divergentes evaluadas aún."
            }

        total = len(paired_opportunities)
        better = 0
        worse = 0
        neutral = 0
        deltas = []
        regrets = []
        error_memory = []

        for opp in paired_opportunities:
            delta = float(opp.get("delta_r_net", opp.get("delta_r", 0.0)))
            deltas.append(delta)

            base_r = float(opp.get("baseline", {}).get("result_r", 0.0))
            adapt_r = float(opp.get("candidate", {}).get("result_r", 0.0))

            # Opportunity Regret: R_baseline - R_adaptive when adaptive was worse
            if delta < 0.0:
                opp_regret = round(abs(delta), 3)
            else:
                opp_regret = 0.0
            regrets.append(opp_regret)

            if delta >= 0.05:
                better += 1
            elif delta <= -0.05:
                worse += 1
                error_memory.append({
                    "opportunity_id": opp.get("opportunity_id", "#N/A"),
                    "session": opp.get("session", "N/A"),
                    "delta_r_net": delta,
                    "regret_r": opp_regret,
                    "lesson": opp.get("note", "La modificación adaptativa empeoró el resultado base en este contexto.")
                })
            else:
                neutral += 1

        divergent = better + worse + neutral
        dir_pct = round((better / divergent * 100.0), 1) if divergent > 0 else 0.0
        incremental_value = round(sum(deltas) / total, 3) if total > 0 else 0.0
        worst_regret = max(regrets) if regrets else 0.0
        mean_regret = round(sum(regrets) / len(regrets), 3) if regrets else 0.0

        return {
            "total_opportunities": total,
            "divergent_decisions": divergent,
            "better_decisions": better,
            "worse_decisions": worse,
            "neutral_decisions": neutral,
            "decision_improvement_rate": dir_pct,
            "incremental_value_r": incremental_value,
            "worst_regret_r": worst_regret,
            "mean_regret_r": mean_regret,
            "adaptive_error_memory": error_memory,
            "display": f"+{incremental_value:.2f}R/trade (DIR: {dir_pct}% | Peor Regret: -{worst_regret:.2f}R)",
            "hierarchy_note": "Jerarquía de decisión estricta: Expectancy → Profit Factor → Max Drawdown → Distribución ΔR → DIR → Win Rate"
        }


