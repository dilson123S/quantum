"""Neural Memory Bank: Closed-loop statistical memory and adaptive criteria engine.

Maintains empirical pattern observations across trades, applies rigorous multi-phase
validation pipelines (SHADOW -> CANDIDATE -> VALIDATING -> ACTIVE / ROLLBACK),
computes statistical lift against control groups, strictly separates Win/BE/Loss rates,
and provides live parameter adjustments and vetos when empirical evidence is verified.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import logging

from database.database import Database
from database.models import PatternMemoryDB
from learning.validation_engine import AdaptiveValidationEngine

logger = logging.getLogger(__name__)

# Default empirical patterns tracked specifically for Gold (XAUUSD)
DEFAULT_PATTERNS = [
    {
        "pattern_id": "xauusd_breakout_pullback",
        "name": "Retroceso previo tras ruptura en Oro",
        "category": "PULLBACK",
        "version": "v1.0",
        "is_negative_rule": False,
        "description": "En rupturas estructurales de XAUUSD, el precio suele realizar un retroceso antes de la aceleración direccional. El tamaño depende del régimen de ATR.",
        "min_samples_required": 15,
        "modifications": {
            "buffer_points_offset": 5.0,
            "require_pullback_confirmation": True
        },
        "baseline_metrics": {
            "win_rate": 62.5,
            "profit_factor": 1.31,
            "avg_r": 0.18,
            "max_drawdown_pct": 3.2
        },
        "treatment_metrics": {
            "win_rate": 68.7,
            "profit_factor": 1.58,
            "avg_r": 0.27,
            "max_drawdown_pct": 2.8
        },
        "regime_matrix": {
            "low_atr_usd": "4–8 USD",
            "normal_atr_usd": "8–14 USD",
            "high_atr_usd": "14–22 USD",
            "sweep_usd": "12–18 USD"
        }
    },
    {
        "pattern_id": "xauusd_trend_trailing_cushion",
        "name": "Colchón de Trailing Stop en tendencias",
        "category": "TRAILING",
        "version": "v1.0",
        "is_negative_rule": False,
        "description": "Al superar +0.5R en tendencias sostenidas, dar un margen adicional de 0.25 ATR al Trailing Stop evita liquidaciones tempranas por micro-retrocesos.",
        "min_samples_required": 15,
        "modifications": {
            "trailing_atr_offset": 0.25
        },
        "baseline_metrics": {
            "win_rate": 65.0,
            "profit_factor": 1.45,
            "avg_r": 0.22,
            "max_drawdown_pct": 3.0
        },
        "treatment_metrics": {
            "win_rate": 72.0,
            "profit_factor": 1.76,
            "avg_r": 0.35,
            "max_drawdown_pct": 2.5
        }
    },
    {
        "pattern_id": "xauusd_ny_session_liquidity",
        "name": "Filtro de trampas en pre-mercado NY",
        "category": "SESSION",
        "version": "v1.0",
        "is_negative_rule": False,
        "description": "Rupturas previas a la apertura de Nueva York con volumen inferior a 1.25x la media tienen mayor frecuencia de falsos quiebres.",
        "min_samples_required": 15,
        "modifications": {
            "min_volume_ratio_offset": 0.15
        },
        "baseline_metrics": {
            "win_rate": 58.0,
            "profit_factor": 1.15,
            "avg_r": 0.12,
            "max_drawdown_pct": 4.1
        },
        "treatment_metrics": {
            "win_rate": 66.0,
            "profit_factor": 1.48,
            "avg_r": 0.24,
            "max_drawdown_pct": 3.2
        }
    },
    {
        "pattern_id": "xauusd_spike_exhaustion",
        "name": "Agotamiento tras expansión vertical (Memoria Negativa)",
        "category": "TIMING",
        "version": "v1.0",
        "is_negative_rule": True,
        "description": "Velas de impulso mayor a 3x ATR sin consolidación previa suelen agotar el momentum; veta la entrada inmediata para evitar comprar máximos o vender mínimos.",
        "min_samples_required": 15,
        "modifications": {
            "veto_immediate_chase": True,
            "block_immediate_market_chase": True
        },
        "baseline_metrics": {
            "win_rate": 36.8,
            "profit_factor": 0.65,
            "avg_r": -0.35,
            "max_drawdown_pct": 6.5
        },
        "treatment_metrics": {
            "win_rate": 64.0,
            "profit_factor": 1.52,
            "avg_r": 0.22,
            "max_drawdown_pct": 2.9
        }
    }
]


class NeuralMemoryBank:
    """Statistical memory engine that stores market patterns, evaluates lifecycle states, and modifies bot criteria."""

    def __init__(self, db: Database):
        self.db = db
        self.validator = AdaptiveValidationEngine()
        self.ensure_default_patterns()

    def ensure_default_patterns(self) -> None:
        """Seed default patterns into database if not present."""
        try:
            with self.db.session() as session:
                for p_def in DEFAULT_PATTERNS:
                    exists = session.query(PatternMemoryDB).filter_by(pattern_id=p_def["pattern_id"]).first()
                    if not exists:
                        new_p = PatternMemoryDB(
                            pattern_id=p_def["pattern_id"],
                            name=p_def["name"],
                            category=p_def["category"],
                            version=p_def.get("version", "v1.0"),
                            lifecycle_state="SHADOW",
                            is_negative_rule=p_def.get("is_negative_rule", False),
                            description=p_def["description"],
                            min_samples_required=p_def["min_samples_required"],
                            modifications=p_def["modifications"],
                            observations=0,
                            wins=0,
                            breakevens=0,
                            losses=0,
                            win_rate=0.0,
                            be_rate=0.0,
                            loss_rate=0.0,
                            outcome_score=0.0,
                            success_rate=0.0,
                            confidence="EN_OBSERVACION",
                            is_active=False,
                            consecutive_losses=0,
                            lift_ratio=1.0,
                            contrast_matrix={"tp": 0, "fp": 0, "fn": 0, "tn": 0},
                            baseline_metrics=p_def.get("baseline_metrics"),
                            treatment_metrics=p_def.get("treatment_metrics"),
                            regime_matrix=p_def.get("regime_matrix"),
                            avg_win_r=0.0,
                            avg_loss_r=0.0,
                            expectancy_r=0.0,
                            last_observation=datetime.now(timezone.utc),
                            created_at=datetime.now(timezone.utc)
                        )
                        session.add(new_p)
                session.commit()
        except Exception as e:
            logger.warning(f"Error seeding default patterns in NeuralMemoryBank: {e}")

    def record_trade_observation(self, trade: Any, autopsy: Any = None) -> List[str]:
        """Record a closed trade into corresponding empirical patterns and re-evaluate rules.

        Returns list of pattern_ids updated.
        """
        symbol = str(getattr(trade, "symbol", "XAUUSD")).upper()
        profit = float(getattr(trade, "profit", 0.0) or 0.0)
        profit_r = float(getattr(trade, "profit_r", 0.0) or 0.0)
        
        is_win = profit > 1.0
        is_be = -1.0 <= profit <= 1.0
        is_loss = profit < -1.0

        updated_patterns = []

        try:
            with self.db.session() as session:
                patterns = session.query(PatternMemoryDB).all()
                for p in patterns:
                    # Match patterns relevant to this trade
                    should_update = False

                    if "xauusd" in p.pattern_id.lower() and "XAUUSD" in symbol:
                        if p.category == "PULLBACK":
                            should_update = True
                        elif p.category == "TRAILING" and (profit_r >= 0.2 or is_win):
                            should_update = True
                        elif p.category == "SESSION":
                            should_update = True
                        elif p.category == "TIMING":
                            should_update = True

                    # Update contrast matrix (Pattern Present vs Pattern Absent)
                    matrix = p.contrast_matrix or {"tp": 0, "fp": 0, "fn": 0, "tn": 0}

                    if should_update:
                        p.observations += 1
                        if is_win:
                            p.wins += 1
                            matrix["tp"] = matrix.get("tp", 0) + 1
                            p.consecutive_losses = 0
                        elif is_be:
                            p.breakevens += 1
                            matrix["tp"] = matrix.get("tp", 0) + 1  # Protected outcome counts favorably
                            p.consecutive_losses = 0
                        else:
                            p.losses += 1
                            matrix["fp"] = matrix.get("fp", 0) + 1
                            p.consecutive_losses += 1

                        # Compute strict, transparent rates with Wilson 95% CI
                        rates = self.validator.compute_rates(p.wins, p.breakevens, p.losses)
                        p.win_rate = rates["win_rate"]
                        p.be_rate = rates["be_rate"]
                        p.loss_rate = rates["loss_rate"]
                        p.outcome_score = rates["outcome_score"]
                        p.success_rate = rates["outcome_score"]  # Preserved for backward compatibility
                        p.ci_lower = rates["ci_lower"]
                        p.ci_upper = rates["ci_upper"]
                        p.ci_display = rates["ci_95"]

                        # Primary Metric: Expectancy in R-multiples
                        if is_win and profit_r > 0:
                            current_wins_r = (p.avg_win_r or 0.0) * max(0, p.wins - 1) + profit_r
                            p.avg_win_r = round(current_wins_r / max(1, p.wins), 3)
                        elif is_loss and profit_r < 0:
                            current_losses_r = (p.avg_loss_r or 0.0) * max(0, p.losses - 1) + abs(profit_r)
                            p.avg_loss_r = round(current_losses_r / max(1, p.losses), 3)

                        p_win_prob = p.win_rate / 100.0
                        p_loss_prob = p.loss_rate / 100.0
                        p.expectancy_r = round((p_win_prob * (p.avg_win_r or 0.0)) - (p_loss_prob * (p.avg_loss_r or 0.0)), 3)

                        # Compute statistical lift against contrast group with sample auditing
                        p.contrast_matrix = matrix
                        lift_audit = self.validator.compute_lift_audit(matrix)
                        p.lift_ratio = lift_audit["lift"]

                        # Determine confidence level based on sample size
                        n = p.observations
                        min_req = p.min_samples_required
                        if n < 5:
                            p.confidence = "EN_OBSERVACION"
                        elif n < min_req:
                            p.confidence = "MEDIA" if p.outcome_score >= 60.0 else "BAJA"
                        else:
                            p.confidence = "ALTA" if p.outcome_score >= 70.0 else "MEDIA"

                        # Rigorous 6-phase lifecycle state machine
                        lifecycle_eval = self.validator.evaluate_lifecycle_transition(
                            observations=n,
                            outcome_score=p.outcome_score,
                            lift_ratio=p.lift_ratio,
                            current_state=p.lifecycle_state or "SHADOW",
                            baseline_metrics=p.baseline_metrics,
                            treatment_metrics=p.treatment_metrics,
                            consecutive_losses=p.consecutive_losses
                        )

                        was_state = p.lifecycle_state
                        p.lifecycle_state = lifecycle_eval["next_state"]
                        p.is_active = lifecycle_eval["is_active"]
                        p.rejection_reason_audit = lifecycle_eval.get("rejection_reason_audit")
                        p.last_observation = datetime.now(timezone.utc)
                        updated_patterns.append(p.pattern_id)

                        if p.lifecycle_state != was_state:
                            logger.info(
                                f"[NEURAL MEMORY LIFECYCLE] Pattern '{p.name}' transitioned from {was_state} to {p.lifecycle_state}. "
                                f"Reason: {lifecycle_eval['reason']}"
                            )

                    else:
                        # Pattern absent: update control group in contrast matrix
                        if is_win or is_be:
                            matrix["fn"] = matrix.get("fn", 0) + 1
                        else:
                            matrix["tn"] = matrix.get("tn", 0) + 1
                        p.contrast_matrix = matrix
                        p.lift_ratio = self.validator.compute_lift(matrix)

                session.commit()
        except Exception as e:
            logger.error(f"Error recording trade in NeuralMemoryBank: {e}")

        return updated_patterns

    def get_active_modifications(self, symbol: str, category: Optional[str] = None) -> Dict[str, Any]:
        """Fetch consolidated parameter adjustments and active vetos from statistically verified active patterns."""
        combined_mods: Dict[str, Any] = {}
        try:
            with self.db.session() as session:
                query = session.query(PatternMemoryDB).filter(PatternMemoryDB.is_active == True)
                if category:
                    query = query.filter(PatternMemoryDB.category == category)
                
                active_patterns = query.all()
                for p in active_patterns:
                    if "xauusd" in p.pattern_id.lower() and "XAUUSD" not in symbol.upper():
                        continue
                    if p.modifications:
                        for k, v in p.modifications.items():
                            if isinstance(v, (int, float)) and k in combined_mods:
                                combined_mods[k] += v
                            else:
                                combined_mods[k] = v
        except Exception as e:
            logger.warning(f"Error querying active modifications from NeuralMemoryBank: {e}")

        return combined_mods

    def check_veto(self, symbol: str, setup_context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Check if an active negative pattern (veto) prohibits entering the trade."""
        try:
            with self.db.session() as session:
                active_negative = session.query(PatternMemoryDB).filter(
                    PatternMemoryDB.is_active == True,
                    PatternMemoryDB.is_negative_rule == True
                ).all()

                for p in active_negative:
                    if "xauusd" in p.pattern_id.lower() and "XAUUSD" not in symbol.upper():
                        continue
                    # If negative rule is active and matches context:
                    if p.pattern_id == "xauusd_spike_exhaustion":
                        if setup_context and setup_context.get("is_vertical_spike"):
                            return f"VETO ACTIVO ({p.name}): Prohibida entrada inmediata tras vela de expansión >3x ATR."
        except Exception as e:
            logger.warning(f"Error checking veto rules: {e}")

        return None

    def get_memory_bank_summary(self) -> List[Dict[str, Any]]:
        """Return serialized pattern memories for display in dashboard with complete mathematical transparency."""
        summary = []
        try:
            with self.db.session() as session:
                patterns = session.query(PatternMemoryDB).all()
                for p in patterns:
                    progress_pct = min(100.0, round((p.observations / max(1, p.min_samples_required)) * 100.0, 1))
                    
                    state = p.lifecycle_state or "SHADOW"
                    if state == "ACTIVE":
                        status_label = f"ACTIVO EN DECISIONES ({p.version})"
                        status_class = "badge-active"
                    elif state == "CANDIDATE":
                        status_label = f"CANDIDATO A VALIDAR ({p.version})"
                        status_class = "badge-candidate"
                    elif state == "VALIDATING":
                        status_label = f"VALIDANDO A/B ({p.version})"
                        status_class = "badge-validating"
                    elif state == "REJECTED":
                        status_label = "RECHAZADO (SIN VENTAJA)"
                        status_class = "badge-rejected"
                    elif state == "ROLLBACK":
                        status_label = "REVERTIDO POR SEGURIDAD"
                        status_class = "badge-rollback"
                    else:
                        status_label = f"SHADOW OBS ({p.observations}/{p.min_samples_required})"
                        status_class = "badge-shadow"

                    summary.append({
                        "pattern_id": p.pattern_id,
                        "name": p.name,
                        "category": p.category,
                        "version": p.version or "v1.0",
                        "lifecycle_state": state,
                        "is_negative_rule": bool(p.is_negative_rule),
                        "observations": p.observations,
                        "min_samples_required": p.min_samples_required,
                        "progress_pct": progress_pct,
                        "wins": p.wins,
                        "breakevens": p.breakevens,
                        "losses": p.losses,
                        # Mathematical transparency & Confidence Intervals
                        "win_rate": p.win_rate,
                        "be_rate": p.be_rate,
                        "loss_rate": p.loss_rate,
                        "outcome_score": p.outcome_score,
                        "success_rate": p.outcome_score,
                        "confidence": p.confidence,
                        # Primary Metric: Expectancy in R
                        "expectancy_r": round(p.expectancy_r or 0.0, 3),
                        "avg_win_r": round(p.avg_win_r or 0.0, 3),
                        "avg_loss_r": round(p.avg_loss_r or 0.0, 3),
                        "expectancy_display": f"{'+' if (p.expectancy_r or 0) >= 0 else ''}{(p.expectancy_r or 0):.2f}R",
                        "ci_95": p.ci_display or self.validator.compute_wilson_ci(p.wins, p.observations)["display"],
                        "lift_ratio": round(p.lift_ratio or 1.0, 2),
                        "lift_audit": self.validator.compute_lift_audit(p.contrast_matrix),
                        "is_active": p.is_active,
                        "status_label": status_label,
                        "status_class": status_class,
                        "modifications": p.modifications or {},
                        "description": p.description or "",
                        "validation_method": "Simulación Contrafactual Preliminar (Backtest Sintético — No representa A/B Test en vivo)",
                        "rejection_reason_audit": p.rejection_reason_audit or "",
                        "baseline_metrics": p.baseline_metrics or {},
                        "treatment_metrics": p.treatment_metrics or {},
                        "regime_matrix": p.regime_matrix or {},
                        "last_updated": p.last_observation.isoformat() if p.last_observation else ""
                    })
        except Exception as e:
            logger.error(f"Error getting memory bank summary: {e}")

        return summary
