"""Volume scoring strategy (Weight: 15 points)."""
import pandas as pd
from typing import Dict, Any

from core.constants import Direction, VolumeMode
from core.models import ComponentScore


class VolumeStrategy:
    """Evaluates volume confirmation (0 to 15 points)."""

    def __init__(self, max_score: float = 15.0):
        self.max_score = max_score

    def calculate_score(
        self,
        df: pd.DataFrame,
        direction: Direction,
        volume_mode: VolumeMode = VolumeMode.TICK,
        min_ratio: float = 1.20,
        strong_ratio: float = 1.50,
        use_obv: bool = True
    ) -> ComponentScore:
        """Calculate volume score.

        Points breakdown (Max: 15):
        - Volume Ratio > min_ratio (1.2): +5
        - Volume Ratio > strong_ratio (1.5): +5
        - OBV trend aligned with direction (BULLISH for BUY, BEARISH for SELL): +5
        - Partial (+2) if Volume Ratio >= 1.0 or OBV is neutral
        """
        score = 0.0
        details: Dict[str, Any] = {
            "volume_ratio": 0.0,
            "volume_strength": "LOW",
            "obv_trend": "NONE",
            "volume_mode": volume_mode.value,
        }

        if df is None or len(df) == 0:
            return ComponentScore(name="volume", score=0.0, max_score=self.max_score, details=details)

        last_row = df.iloc[-1]

        # 1. Volume Ratio (Max 10 pts)
        vol_ratio = last_row.get("volume_ratio")
        if vol_ratio is not None and not pd.isna(vol_ratio):
            details["volume_ratio"] = float(vol_ratio)
            if vol_ratio >= strong_ratio:
                score += 10.0
                details["volume_strength"] = "STRONG"
            elif vol_ratio >= min_ratio:
                score += 5.0
                details["volume_strength"] = "MODERATE"
            elif vol_ratio >= 1.0:
                score += 2.0
                details["volume_strength"] = "AVERAGE"
            elif vol_ratio < 0.75:
                # Dead volume penalty: institutional lack of participation
                score = max(0.0, score - 2.0)
                details["low_volume_penalty"] = True

        # 2. OBV Alignment (Max 5 pts)
        if use_obv:
            obv_tr = last_row.get("obv_trend")
            if obv_tr is not None:
                details["obv_trend"] = str(obv_tr)
                if direction == Direction.BUY and obv_tr == "BULLISH":
                    score += 5.0
                elif direction == Direction.SELL and obv_tr == "BEARISH":
                    score += 5.0
                elif obv_tr == "NEUTRAL":
                    score += 2.0
                elif (direction == Direction.BUY and obv_tr == "BEARISH") or (direction == Direction.SELL and obv_tr == "BULLISH"):
                    # Institutional divergence: OBV flow directly opposes trade direction
                    score = max(0.0, score - 3.0)
                    details["obv_divergence_penalty"] = True

        final_score = min(self.max_score, max(0.0, score))
        return ComponentScore(
            name="volume",
            score=final_score,
            max_score=self.max_score,
            details=details
        )
