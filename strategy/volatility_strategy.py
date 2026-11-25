"""Volatility scoring strategy (Weight: 15 points)."""
import pandas as pd
from typing import Dict, Any, Optional

from core.models import ComponentScore


class VolatilityStrategy:
    """Evaluates volatility confirmation (0 to 15 points)."""

    def __init__(self, max_score: float = 15.0):
        self.max_score = max_score

    def calculate_score(
        self,
        df_entry: pd.DataFrame,
        df_htf: Optional[pd.DataFrame] = None,
        min_atr_ratio: float = 0.80,
        max_atr_ratio: float = 2.50
    ) -> ComponentScore:
        """Calculate volatility score.

        Points breakdown (Max: 15):
        - Favorable ATR ratio expansion (1.10 - 1.50): +5
        - Healthy volatility state (not DEAD, ratio >= 0.95): +5
        - HTF volatility confirmation (HTF ATR healthy and expanding): +5
        - Dead market (ratio < min_atr_ratio): 0 points
        """
        score = 0.0
        details: Dict[str, Any] = {
            "atr_ratio": 0.0,
            "volatility_state": "UNKNOWN",
            "htf_confirmed": False,
        }

        if df_entry is None or len(df_entry) == 0:
            return ComponentScore(name="volatility", score=0.0, max_score=self.max_score, details=details)

        last_row = df_entry.iloc[-1]
        atr_ratio_val = last_row.get("atr_ratio")

        if atr_ratio_val is not None and not pd.isna(atr_ratio_val):
            details["atr_ratio"] = float(atr_ratio_val)

            # Check if market is completely dead
            if atr_ratio_val < min_atr_ratio:
                details["volatility_state"] = "DEAD"
                return ComponentScore(name="volatility", score=0.0, max_score=self.max_score, details=details)

            # 1. Sweet spot expansion (1.10 - 1.50) (+5 pts)
            if 1.10 <= atr_ratio_val <= 1.50:
                score += 5.0
                details["volatility_state"] = "EXPANSION_IDEAL"
            elif 1.00 <= atr_ratio_val <= 1.80:
                score += 3.0
                details["volatility_state"] = "MODERATE_EXPANSION"
            elif atr_ratio_val > max_atr_ratio:
                details["volatility_state"] = "EXTREME_VOLATILITY"
                # Extreme volatility is a hard quality failure.  An H1
                # confirmation cannot make an unsafe entry condition healthy.
                return ComponentScore(name="volatility", score=0.0, max_score=self.max_score, details=details)
            else:
                details["volatility_state"] = "LOW_NORMAL"

            # 2. General baseline volatility health (+5 pts)
            if atr_ratio_val >= 0.95 and atr_ratio_val <= max_atr_ratio:
                score += 5.0

        # 3. HTF Volatility Check (+5 pts)
        if df_htf is not None and len(df_htf) > 0:
            htf_last = df_htf.iloc[-1]
            htf_atr_ratio = htf_last.get("atr_ratio")
            if htf_atr_ratio is not None and not pd.isna(htf_atr_ratio):
                if 0.90 <= htf_atr_ratio <= 2.20:
                    score += 5.0
                    details["htf_confirmed"] = True
            else:
                # If no ratio on HTF, award default if basic ATR exists
                if "atr" in htf_last and not pd.isna(htf_last["atr"]):
                    score += 5.0
                    details["htf_confirmed"] = True
        else:
            # Fallback if HTF not provided
            score += 3.0

        final_score = min(self.max_score, max(0.0, score))
        return ComponentScore(
            name="volatility",
            score=final_score,
            max_score=self.max_score,
            details=details
        )
