"""ML Meta-Filter — Shadow Mode and Active filtering for trade signals."""
import logging
from typing import Optional

import pandas as pd

from ml.predictor import MLPredictor

logger = logging.getLogger(__name__)


class MLFilter:
    """Filters trade signals using ML probability estimation.

    Modes:
        Shadow (shadow_mode=True):  Logs predictions but NEVER blocks signals.
        Active (shadow_mode=False): Blocks signals with P(success) < min_confidence.
        Disabled (enabled=False):   Completely bypassed, no logging.
    """

    def __init__(
        self,
        enabled: bool = True,
        shadow_mode: bool = True,
        model_path: str = "data/ml_model.joblib",
        min_confidence: float = 0.65,
    ):
        self.enabled = enabled
        self.shadow_mode = shadow_mode
        self.min_confidence = min_confidence
        self.predictor: Optional[MLPredictor] = None

        if self.enabled:
            self.predictor = MLPredictor(model_path=model_path)

    @property
    def is_active(self) -> bool:
        """Whether the filter can actually make predictions."""
        return (
            self.enabled
            and self.predictor is not None
            and self.predictor.is_ready
        )

    def evaluate(
        self,
        symbol: str,
        direction: str,
        df: pd.DataFrame,
        structure_score: float = 0.0,
        trend_score: float = 0.0,
        spread_points: float = 0.0,
        atr_value: float = 1.0,
        point_size: Optional[float] = None,
        total_score: float = 0.0,
    ) -> tuple:
        """Evaluate a signal through the ML filter.

        Returns:
            (approved: bool, probability: float or None, reason: str)

            In shadow mode, approved is ALWAYS True regardless of probability.
        """
        # Completely disabled — approve everything silently
        if not self.enabled:
            return True, None, ""

        # No model loaded — approve everything, no interference
        if not self.is_active:
            return True, None, "ML model not loaded"

        # Run prediction
        probability = self.predictor.predict_probability(
            df=df,
            structure_score=structure_score,
            trend_score=trend_score,
            spread_points=spread_points,
            atr_value=atr_value,
            point_size=point_size,
            direction=direction,
        )

        if probability is None:
            return True, None, "ML prediction unavailable"

        # Determine approval
        approved = probability >= self.min_confidence

        if self.shadow_mode:
            # SHADOW MODE: Log but never block
            status = "PASS" if approved else "WOULD_BLOCK"
            logger.info(
                f"[ML SHADOW] {symbol} {direction} | Score: {total_score:.0f} | "
                f"P(success): {probability:.2%} | Threshold: {self.min_confidence:.0%} | "
                f"Verdict: {status}"
            )
            return True, probability, f"Shadow: P={probability:.2%} ({status})"

        else:
            # ACTIVE MODE: Actually filter
            if approved:
                logger.info(
                    f"[ML ACTIVE] ✅ {symbol} {direction} APPROVED | "
                    f"P(success): {probability:.2%} >= {self.min_confidence:.0%}"
                )
                return True, probability, f"ML approved: P={probability:.2%}"
            else:
                logger.info(
                    f"[ML ACTIVE] ❌ {symbol} {direction} BLOCKED | "
                    f"P(success): {probability:.2%} < {self.min_confidence:.0%}"
                )
                return False, probability, f"ML blocked: P(success)={probability:.2%} < {self.min_confidence:.0%}"

    def reload_model(self) -> bool:
        """Reload the ML model from disk (e.g. after retraining)."""
        if self.predictor:
            return self.predictor.reload()
        return False
