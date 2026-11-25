"""Base interfaces and typed question schemas for open-source System 1 decision models."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ThesisAlignment(str, Enum):
    ALIGNED = "aligned"
    MIXED = "mixed"
    CONTRADICTORY = "contradictory"
    INSUFFICIENT = "insufficient"


class TrapRisk(str, Enum):
    GENUINE_EXPANSION = "genuine_expansion"
    POTENTIAL_TRAP = "potential_trap"
    OVEREXTENDED_EXHAUSTION = "overextended_exhaustion"


@dataclass
class SystemOneDecision:
    """Standardized result returned by any System 1 provider (Laya, Kev, NanoJev, etc.)."""
    signal_id: str
    provider: str
    model_name: str
    latency_ms: float
    thesis_alignment: str
    thesis_confidence: float
    evidence_score: int
    evidence_confidence: float
    trap_risk: Optional[str] = None
    trap_confidence: Optional[float] = None
    sl_recommendation: Optional[str] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)
    execution_effect: str = "none_shadow_only"

    def is_favorable(self, min_score: int = 3, min_confidence: float = 0.50) -> bool:
        """Returns True if the decision supports trade execution."""
        return (
            self.thesis_alignment == ThesisAlignment.ALIGNED.value
            and self.evidence_score >= min_score
            and self.thesis_confidence >= min_confidence
            and self.trap_risk != TrapRisk.POTENTIAL_TRAP.value
        )


STANDARD_TRADING_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "thesis_alignment": {
        "type": "choice",
        "instructions": (
            "Using only the supplied closed-bar market facts, classify whether the stated "
            "trade direction and setup evidence tell a coherent strategy thesis. Do not "
            "predict price or profitability. If key facts are absent, choose insufficient."
        ),
        "criteria": {
            "aligned": "The stated direction is strongly supported by the supplied setup, structure, and market evidence.",
            "mixed": "The evidence is mixed, with both meaningful support and conflicting indicators.",
            "contradictory": "The supplied evidence materially conflicts with the stated direction or setup.",
            "insufficient": "The supplied facts are not enough to assess the thesis.",
        },
    },
    "evidence_quality": {
        "type": "score",
        "instructions": (
            "Score how complete, unextended, and internally consistent the supplied evidence is "
            "for the named setup on a scale of 1 to 5."
        ),
        "criteria": [
            "1: Missing or contradictory evidence, overextended entry",
            "2: Weak evidence with major gaps or high friction",
            "3: Mixed evidence with notable uncertainty",
            "4: Good evidence with limited gaps and clear structure",
            "5: Strong, complete, highly confluent evidence at prime level",
        ],
    },
    "trap_risk": {
        "type": "choice",
        "instructions": (
            "Evaluate whether this candidate entry has characteristics of an institutional "
            "liquidity trap / fakeout, an overextended exhaustion move, or a genuine expansion."
        ),
        "criteria": {
            "genuine_expansion": "Clear institutional displacement with momentum and structural backing.",
            "potential_trap": "Entry into opposing liquidity pool, low volume breakout, or obvious retail inducement.",
            "overextended_exhaustion": "Price has moved too far from the anchor or mean, high risk of sharp pullback.",
        },
    },
}


class BaseSystemOneProvider(ABC):
    """Abstract provider for non-autoregressive System 1 decision models."""

    def __init__(self, name: str, model_name: str, device: str = "cpu"):
        self.name = name
        self.model_name = model_name
        self.device = device
        self._is_ready = False

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    @abstractmethod
    def evaluate(
        self,
        candidate_state: Dict[str, Any],
        questions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[SystemOneDecision]:
        """Evaluate a trade candidate snapshot and return a standardized decision."""
        pass

    @abstractmethod
    def evaluate_batch(
        self,
        candidates: List[Dict[str, Any]],
        questions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Optional[SystemOneDecision]]:
        """Evaluate multiple candidate snapshots in a single forward pass."""
        pass
