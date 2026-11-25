"""System 1 Hub — Universal orchestrator for open-source decision models (Laya, Kev, NanoJev).

Provides non-blocking queue-based observation, automatic fallback, and execution integration.
"""
from __future__ import annotations

import logging
import os
import threading
from queue import Empty, Full, Queue
from typing import Any, Dict, List, Optional

from filters.system_one.base import (
    BaseSystemOneProvider,
    SystemOneDecision,
)
from filters.system_one.laya_provider import LayaProvider
from filters.system_one.kev_provider import KevProvider
from filters.system_one.nanojev_provider import NanoJevProvider

logger = logging.getLogger(__name__)


class SystemOneHub:
    """Manages asynchronous System 1 inference without impacting trade loop latency."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        provider_name: str = "laya",
        shadow_mode: bool = True,
        device: str = "cuda",
        model_name: Optional[str] = None,
        timeout_seconds: float = 3.0,
    ):
        self.enabled = bool(enabled)
        self.shadow_mode = bool(shadow_mode)
        self.provider_name = (provider_name or "laya").lower()
        self.device = device
        self.timeout_seconds = timeout_seconds

        # Instantiate provider
        self.provider: Optional[BaseSystemOneProvider] = None
        if self.enabled:
            self._init_provider(model_name)

        self._queue: Queue[Dict[str, Any]] = Queue(maxsize=128)
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self.last_decision: Optional[SystemOneDecision] = None

    def _init_provider(self, model_name: Optional[str]) -> None:
        try:
            if self.provider_name == "laya":
                self.provider = LayaProvider(
                    model_name=model_name or "laya-base",
                    device=self.device,
                    preload=False,
                )
            elif self.provider_name == "kev":
                self.provider = KevProvider(
                    model_name=model_name or "quantum",
                    endpoint_url=os.getenv("KEV_ENDPOINT_URL", "http://localhost:11434/v1/chat/completions"),
                    timeout_seconds=self.timeout_seconds,
                )
            elif self.provider_name == "nanojev":
                self.provider = NanoJevProvider(
                    model_name=model_name or "nanojev-0.6b",
                    device=self.device,
                )
            else:
                logger.warning("Unknown System 1 provider '%s'; defaulting to NanoJev.", self.provider_name)
                self.provider = NanoJevProvider()
        except Exception as exc:
            logger.error("Failed to initialize System 1 provider '%s': %s", self.provider_name, exc)
            self.provider = NanoJevProvider()

    def observe(
        self,
        *,
        dedupe_key: str,
        signal_id: str,
        state: Dict[str, Any],
    ) -> bool:
        """Queue candidate for asynchronous evaluation."""
        if not self.enabled or self.provider is None:
            return False

        with self._lock:
            if dedupe_key in self._seen:
                return False
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run_worker,
                    name=f"system1-{self.provider_name}-worker",
                    daemon=True,
                )
                self._worker.start()

            try:
                self._queue.put_nowait({"signal_id": signal_id, "state": state})
                self._seen.add(dedupe_key)
                if len(self._seen) > 2048:
                    self._seen.pop()
                logger.info("[SYSTEM-1 HUB] Queued candidate for %s evaluation (signal_id=%s).", self.provider.name, signal_id)
                return True
            except Full:
                logger.warning("[SYSTEM-1 HUB] Queue full; dropped signal_id=%s", signal_id)
                return False

    def evaluate_sync(self, state: Dict[str, Any]) -> Optional[SystemOneDecision]:
        """Synchronous evaluation for active gating / filters."""
        if not self.enabled or self.provider is None:
            return None
        return self.provider.evaluate(state)

    def _run_worker(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=1.0)
            except Empty:
                continue
            try:
                decision = self.provider.evaluate(job["state"])
                if decision:
                    self.last_decision = decision
            except Exception as exc:
                logger.warning("[SYSTEM-1 HUB] Asynchronous worker error: %s", exc)
            finally:
                self._queue.task_done()
