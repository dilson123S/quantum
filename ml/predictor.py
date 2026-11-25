"""Real-time ML predictor — loads trained model and returns P(success)."""
import logging
import os
from typing import Dict, Optional

import pandas as pd

from ml.features import FEATURE_COLUMNS, extract_features

logger = logging.getLogger(__name__)

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False


class MLPredictor:
    """Loads a trained XGBoost model and predicts P(success) for a signal."""

    def __init__(self, model_path: str = "data/ml_model.joblib"):
        self.model = None
        self.features: list = FEATURE_COLUMNS
        self.model_path = model_path
        self._loaded = False

        self._try_load()

    def _try_load(self) -> bool:
        """Attempt to load the model from disk."""
        if self.model_path.endswith(".pt"):
            try:
                from ml.torch_predictor import PyTorchPredictor
                self._torch_predictor = PyTorchPredictor(model_path=self.model_path)
                if self._torch_predictor.is_ready:
                    self._loaded = True
                    self.features = self._torch_predictor.features
                    logger.info(f"[ML Predictor] PyTorch model loaded successfully from {self.model_path}")
                    return True
            except Exception as e:
                logger.warning(f"[ML Predictor] Failed to load PyTorch model: {e}")
                return False

        if not HAS_JOBLIB:
            logger.debug("[ML Predictor] joblib not installed, ML prediction disabled.")
            return False

        if not os.path.exists(self.model_path):
            logger.info(f"[ML Predictor] No model found at {self.model_path}. ML prediction disabled until training.")
            return False

        try:
            data = joblib.load(self.model_path)
            self.model = data["model"]
            self.features = data.get("features", FEATURE_COLUMNS)
            self._loaded = True
            logger.info(f"[ML Predictor] Model loaded successfully from {self.model_path} ({len(self.features)} features)")
            return True
        except Exception as e:
            logger.warning(f"[ML Predictor] Failed to load model: {e}")
            return False

    @property
    def is_ready(self) -> bool:
        """Whether a model is loaded and ready for predictions."""
        return self._loaded and (self.model is not None or getattr(self, "_torch_predictor", None) is not None)

    def predict_probability(
        self,
        df: pd.DataFrame,
        structure_score: float = 0.0,
        trend_score: float = 0.0,
        spread_points: float = 0.0,
        atr_value: float = 1.0,
        point_size: Optional[float] = None,
        direction: Optional[str] = None,
    ) -> Optional[float]:
        """Predict P(success) for the current signal.

        Args:
            df: DataFrame with indicator columns (last 2+ rows needed).
            structure_score: Structure component score from the 5-confirmation engine.
            trend_score: Trend component score from the 5-confirmation engine.
            spread_points: Current spread in points.
            atr_value: Current ATR value.
            point_size: Point value of the traded symbol.
            direction: Trade direction ('BUY' or 'SELL') to orient features.

        Returns:
            Float probability in [0, 1], or None if model is not available.
        """
        if not self.is_ready:
            return None

        if hasattr(self, "_torch_predictor") and self._torch_predictor is not None:
            return self._torch_predictor.predict_probability(
                df=df,
                structure_score=structure_score,
                trend_score=trend_score,
                spread_points=spread_points,
                atr_value=atr_value,
                point_size=point_size,
                direction=direction,
            )

        try:
            feat = extract_features(
                df,
                structure_score=structure_score,
                trend_score=trend_score,
                spread_points=spread_points,
                atr_value=atr_value,
                point_size=point_size,
                direction=direction,
            )

            # Build feature array in the correct order
            feature_values = [feat.get(col, 0.0) for col in self.features]
            X = [feature_values]

            proba = self.model.predict_proba(X)[0][1]
            return float(proba)

        except Exception as e:
            logger.warning(f"[ML Predictor] Prediction error: {e}")
            return None

    def reload(self) -> bool:
        """Reload model from disk (e.g. after retraining)."""
        self._loaded = False
        self.model = None
        return self._try_load()
