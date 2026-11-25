"""Kev Provider (Jared Palmer) — Reasoning-focused System 1 decision engine.

Based on Qwen architectures (0.6B to 9B / Kev-4B), offering deep narrative understanding,
news/macro reasoning, and full wire-compatibility with Jev's typed question format.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from filters.system_one.base import (
    BaseSystemOneProvider,
    STANDARD_TRADING_QUESTIONS,
    SystemOneDecision,
    ThesisAlignment,
)

logger = logging.getLogger(__name__)


class KevProvider(BaseSystemOneProvider):
    """Kev System 1 Provider (Local or OpenRouter / wire-compatible endpoint)."""

    def __init__(
        self,
        model_name: str = "kev-4b",
        endpoint_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = 4.0,
    ):
        super().__init__(name="Kev", model_name=model_name, device="auto")
        self.endpoint_url = endpoint_url or os.getenv("KEV_ENDPOINT_URL", "http://localhost:11434/v1/chat/completions")
        self.api_key = api_key or os.getenv("KEV_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))
        self.timeout_seconds = timeout_seconds
        self._is_ready = True

    def evaluate(
        self,
        candidate_state: Dict[str, Any],
        questions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[SystemOneDecision]:
        """Run candidate evaluation through Kev."""
        active_questions = questions or STANDARD_TRADING_QUESTIONS
        signal_id = candidate_state.get("signal_id", "unknown")
        
        # Check if local wire-compatible systemone endpoint or standard LLM endpoint
        is_wire_compatible = "/systemone" in self.endpoint_url.lower()

        started = time.monotonic()
        try:
            if is_wire_compatible:
                decision = self._call_wire_endpoint(signal_id, candidate_state, active_questions, started)
            else:
                decision = self._call_chat_completion(signal_id, candidate_state, active_questions, started)
            
            if decision:
                logger.info(
                    "[KEV SYSTEM-1] signal_id=%s thesis=%s (conf: %.2f) score=%d/5 trap=%s latency=%.1fms",
                    signal_id, decision.thesis_alignment, decision.thesis_confidence,
                    decision.evidence_score, decision.trap_risk, decision.latency_ms
                )
            return decision

        except Exception as exc:
            logger.warning("[KEV] Evaluation failed for signal %s: %s", signal_id, exc)
            return None

    def _call_wire_endpoint(
        self,
        signal_id: str,
        state: Dict[str, Any],
        questions: Dict[str, Any],
        started_ts: float,
    ) -> Optional[SystemOneDecision]:
        """Direct call to wire-compatible /systemone server (Jev / Kev local server)."""
        body = json.dumps({
            "state": json.dumps(state, ensure_ascii=False),
            "model": self.model_name,
            "questions": questions,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = Request(self.endpoint_url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=self.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        elapsed_ms = round((time.monotonic() - started_ts) * 1000.0, 2)
        answers = data.get("answers", {})
        thesis_data = answers.get("thesis_alignment", {})
        evidence_data = answers.get("evidence_quality", {})
        trap_data = answers.get("trap_risk", {})

        return SystemOneDecision(
            signal_id=signal_id,
            provider="Kev",
            model_name=self.model_name,
            latency_ms=elapsed_ms,
            thesis_alignment=thesis_data.get("choice", ThesisAlignment.INSUFFICIENT.value),
            thesis_confidence=float(thesis_data.get("confidence", 0.5)),
            evidence_score=int(evidence_data.get("score", 3)),
            evidence_confidence=float(evidence_data.get("confidence", 0.5)),
            trap_risk=trap_data.get("choice") if trap_data else None,
            trap_confidence=float(trap_data.get("confidence", 0.5)) if trap_data else None,
            raw_response=data,
        )

    def _call_chat_completion(
        self,
        signal_id: str,
        state: Dict[str, Any],
        questions: Dict[str, Any],
        started_ts: float,
    ) -> Optional[SystemOneDecision]:
        """Structured JSON schema completion for Qwen/Kev models on OpenRouter/Ollama."""
        prompt = (
            f"You are Kev, a System 1 Decision Model. Analyze this trading candidate:\n"
            f"{json.dumps(state, indent=2)}\n\n"
            f"Respond STRICTLY in JSON format with keys: thesis_alignment (aligned/mixed/contradictory/insufficient), "
            f"thesis_confidence (0.0-1.0), evidence_score (1-5), evidence_confidence (0.0-1.0), "
            f"trap_risk (genuine_expansion/potential_trap/overextended_exhaustion)."
        )

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = Request(self.endpoint_url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=self.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        elapsed_ms = round((time.monotonic() - started_ts) * 1000.0, 2)
        content_text = data["choices"][0]["message"]["content"]
        parsed = json.loads(content_text)

        return SystemOneDecision(
            signal_id=signal_id,
            provider="Kev",
            model_name=self.model_name,
            latency_ms=elapsed_ms,
            thesis_alignment=parsed.get("thesis_alignment", "insufficient"),
            thesis_confidence=float(parsed.get("thesis_confidence", 0.5)),
            evidence_score=int(parsed.get("evidence_score", 3)),
            evidence_confidence=float(parsed.get("evidence_confidence", 0.5)),
            trap_risk=parsed.get("trap_risk"),
            trap_confidence=float(parsed.get("trap_confidence", 0.5)),
            raw_response=parsed,
        )

    def evaluate_batch(
        self,
        candidates: List[Dict[str, Any]],
        questions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Optional[SystemOneDecision]]:
        return [self.evaluate(c, questions) for c in candidates]
