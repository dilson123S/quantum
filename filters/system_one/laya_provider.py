"""Laya Provider (Convai Innovations) — Non-autoregressive System 1 local decision engine.

Ultra-low latency (<50ms), zero API cost, runs natively via ONNX/ModernBERT.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from filters.system_one.base import (
    BaseSystemOneProvider,
    STANDARD_TRADING_QUESTIONS,
    SystemOneDecision,
    ThesisAlignment,
)

logger = logging.getLogger(__name__)


class LayaProvider(BaseSystemOneProvider):
    """Local, open-weights Laya decision router."""

    def __init__(
        self,
        model_name: str = "laya-base",
        device: str = "cuda",
        preload: bool = False,
    ):
        super().__init__(name="Laya", model_name=model_name, device=device)
        self.router = None
        self._load_error = None
        if preload:
            self._ensure_loaded()

    def _ensure_loaded(self) -> bool:
        if self._is_ready:
            return True
        if self._load_error:
            return False
        try:
            from laya import Router
            logger.info("Initializing Laya local System 1 engine on device=%s...", self.device)
            self.router = Router(preload=True)
            self._is_ready = True
            logger.info("Laya System 1 engine successfully loaded.")
            return True
        except ImportError:
            self._load_error = "laya package not installed. Run 'pip install laya'."
            logger.warning("[LAYA] %s", self._load_error)
            return False
        except Exception as exc:
            self._load_error = str(exc)
            logger.error("[LAYA] Error loading Laya router: %s", exc)
            return False

    def evaluate(
        self,
        candidate_state: Dict[str, Any],
        questions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[SystemOneDecision]:
        """Run single candidate evaluation through Laya."""
        if not self._ensure_loaded():
            return None

        active_questions = questions or STANDARD_TRADING_QUESTIONS
        signal_id = candidate_state.get("signal_id", "unknown")
        
        # Serialize candidate state cleanly
        state_text = json.dumps(candidate_state, ensure_ascii=False, separators=(",", ":"))
        
        started = time.monotonic()
        try:
            # Laya non-autoregressive prediction pass
            raw_result = self.router.predict(state_text, active_questions)
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
            
            # Parse responses
            thesis_data = raw_result.get("thesis_alignment", {})
            evidence_data = raw_result.get("evidence_quality", {})
            trap_data = raw_result.get("trap_risk", {})

            choice = thesis_data.get("choice") or thesis_data.get("value") or ThesisAlignment.INSUFFICIENT.value
            choice_conf = float(thesis_data.get("confidence", 0.50))
            
            score_val = int(evidence_data.get("score") or evidence_data.get("value") or 3)
            score_conf = float(evidence_data.get("confidence", 0.50))

            trap_val = trap_data.get("choice") or trap_data.get("value") if trap_data else None
            trap_conf = float(trap_data.get("confidence", 0.50)) if trap_data else None

            decision = SystemOneDecision(
                signal_id=signal_id,
                provider="Laya",
                model_name=self.model_name,
                latency_ms=elapsed_ms,
                thesis_alignment=choice,
                thesis_confidence=choice_conf,
                evidence_score=score_val,
                evidence_confidence=score_conf,
                trap_risk=trap_val,
                trap_confidence=trap_conf,
                raw_response=raw_result,
                execution_effect="none_shadow_only",
            )
            logger.info(
                "[LAYA SYSTEM-1] signal_id=%s thesis=%s (conf: %.2f) score=%d/5 trap=%s latency=%.1fms",
                signal_id, decision.thesis_alignment, decision.thesis_confidence,
                decision.evidence_score, decision.trap_risk, decision.latency_ms
            )
            return decision

        except Exception as exc:
            logger.error("[LAYA] Evaluation failed for signal %s: %s", signal_id, exc)
            return None

    def evaluate_batch(
        self,
        candidates: List[Dict[str, Any]],
        questions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Optional[SystemOneDecision]]:
        """Sequential or native batched evaluation."""
        return [self.evaluate(c, questions) for c in candidates]
