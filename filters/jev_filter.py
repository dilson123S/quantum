"""Asynchronous, shadow-only integration with TypeSafe Jev.

This adapter records a typed opinion about an already-approved candidate. Its
answer is telemetry only: it never approves, rejects, sizes, or submits trades.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from queue import Empty, Full, Queue
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
_MAX_STATE_BYTES = 12_000
_MAX_RESPONSE_BYTES = 64 * 1024

_QUESTIONS = {
    "thesis_alignment": {
        "type": "choice",
        "instructions": (
            "Using only the supplied closed-bar facts, classify whether the stated "
            "trade direction and evidence tell a coherent strategy thesis. Do not "
            "predict price or profitability. If key facts are absent, choose insufficient."
        ),
        "criteria": {
            "aligned": "The stated direction is supported by the supplied setup and market evidence.",
            "mixed": "The evidence is mixed, with meaningful support and conflict.",
            "contradictory": "The supplied evidence materially conflicts with the stated direction or setup.",
            "insufficient": "The supplied facts are not enough to assess the thesis.",
        },
    },
    "evidence_quality": {
        "type": "score",
        "instructions": (
            "Score how complete and internally consistent the supplied evidence is "
            "for the named setup, not the chance that the trade wins."
        ),
        "criteria": [
            "Missing or contradictory evidence",
            "Weak evidence with major gaps",
            "Mixed evidence with notable uncertainty",
            "Good evidence with limited gaps",
            "Strong, complete, internally consistent evidence",
        ],
    },
}


class JevShadowFilter:
    """Queue sparse candidate snapshots to Jev without affecting execution."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        shadow_mode: bool = True,
        model: str = "jev-latest",
        timeout_seconds: float = 3.0,
        api_key: Optional[str] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.shadow_mode = bool(shadow_mode)
        self.model = model or "jev-latest"
        self.timeout_seconds = max(0.5, min(float(timeout_seconds), 15.0))
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY", "")
        self._queue: Queue[Dict[str, Any]] = Queue(maxsize=64)
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._missing_key_warned = False

    @property
    def is_configured(self) -> bool:
        return self.enabled and self.shadow_mode and bool(self.api_key)

    def observe(
        self,
        *,
        dedupe_key: str,
        signal_id: str,
        state: Dict[str, Any],
    ) -> bool:
        """Queue one assessment. Return value says only whether it was queued."""
        if not self.enabled:
            return False
        if not self.shadow_mode:
            logger.error("Jev is shadow-only in this integration; non-shadow mode ignored.")
            return False
        if not self.api_key:
            if not self._missing_key_warned:
                logger.warning("Jev is enabled but TYPESAFE_API_KEY is not configured; no request sent.")
                self._missing_key_warned = True
            return False

        try:
            state_text = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(state_text.encode("utf-8")) > _MAX_STATE_BYTES:
                logger.warning("Jev shadow snapshot too large; skipped signal_id=%s", signal_id)
                return False
        except (TypeError, ValueError):
            logger.warning("Jev shadow snapshot is not valid JSON; skipped signal_id=%s", signal_id)
            return False

        with self._lock:
            if dedupe_key in self._seen:
                return False
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run_worker,
                    name="jev-shadow-worker",
                    daemon=True,
                )
                self._worker.start()
            job = {"dedupe_key": dedupe_key, "signal_id": signal_id, "state": state_text}
            try:
                self._queue.put_nowait(job)
            except Full:
                logger.warning("Jev shadow queue full; skipped signal_id=%s", signal_id)
                return False
            self._seen.add(dedupe_key)
            self._seen_order.append(dedupe_key)
            while len(self._seen_order) > 4096:
                self._seen.discard(self._seen_order.popleft())
        logger.info("[JEV SHADOW] queued signal_id=%s", signal_id)
        return True

    def _run_worker(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=1.0)
            except Empty:
                continue
            try:
                self._request_jev(job)
            except Exception as exc:  # keep telemetry failures out of the trade loop
                logger.warning(
                    "[JEV SHADOW] request failed signal_id=%s error_type=%s",
                    job.get("signal_id"), type(exc).__name__,
                )
            finally:
                self._queue.task_done()

    def _request_jev(self, job: Dict[str, Any]) -> None:
        body = json.dumps(
            {"state": job["state"], "model": self.model, "questions": _QUESTIONS},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            _ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            logger.warning(
                "[JEV SHADOW] HTTP failure signal_id=%s status=%s",
                job.get("signal_id"), exc.code,
            )
            return
        except URLError as exc:
            # Never log the exception reason: transport errors can include request details.
            logger.warning(
                "[JEV SHADOW] transport failure signal_id=%s reason_type=%s",
                job.get("signal_id"), type(exc.reason).__name__,
            )
            return
        elapsed_ms = round((time.monotonic() - started) * 1000.0)
        if len(response_body) > _MAX_RESPONSE_BYTES:
            logger.warning("[JEV SHADOW] oversized response signal_id=%s", job.get("signal_id"))
            return
        try:
            response_data = json.loads(response_body.decode("utf-8"))
            answers = response_data.get("answers", {})
            alignment = answers.get("thesis_alignment", {})
            quality = answers.get("evidence_quality", {})
            result = {
                "signal_id": job.get("signal_id"),
                "model": response_data.get("model"),
                "latency_ms": elapsed_ms,
                "thesis_alignment": {
                    "choice": alignment.get("choice"),
                    "confidence": alignment.get("confidence"),
                    "probabilities": alignment.get("probabilities"),
                },
                "evidence_quality": {
                    "score": quality.get("score"),
                    "confidence": quality.get("confidence"),
                    "probabilities": quality.get("probabilities"),
                },
                "usage": response_data.get("usage", {}),
                "execution_effect": "none_shadow_only",
            }
            logger.info("[JEV SHADOW] result=%s", json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
            logger.warning("[JEV SHADOW] invalid response format signal_id=%s", job.get("signal_id"))
