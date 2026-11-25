"""Trend scoring strategy (Weight: 25 points)."""
import pandas as pd
from typing import Dict, Any

from core.constants import Direction
from core.models import ComponentScore


class TrendStrategy:
    """Evaluates trend confirmation (0 to 25 points)."""

    def __init__(self, max_score: float = 25.0):
        self.max_score = max_score

    def calculate_score(
        self,
        df: pd.DataFrame,
        direction: Direction,
        adx_min: float = 20.0,
        adx_strong: float = 25.0
    ) -> ComponentScore:
        """Calculate trend score.

        Points breakdown (Max: 25):
        - EMA Alignment (20>50>200 for BUY / 20<50<200 for SELL): +10 (Partial +5 if 2 of 3)
        - Price vs EMA200 (Price > EMA200 for BUY / Price < EMA200 for SELL): +5
        - ADX strength: +5 if ADX >= adx_strong (25), +3 if ADX >= adx_min (20), with DI alignment
        - EMA50 Slope: +5 if slope aligned with direction (+3 if mildly positive/negative)
        """
        score = 0.0
        details: Dict[str, Any] = {
            "ema_alignment": "NONE",
            "price_vs_ema200": False,
            "adx_value": 0.0,
            "adx_strength": "WEAK",
            "ema50_slope": 0.0,
        }

        if df is None or len(df) == 0:
            return ComponentScore(name="trend", score=0.0, max_score=self.max_score, details=details)

        last_row = df.iloc[-1]

        # 1. EMA Alignment
        ema_fast = last_row.get("ema_fast")
        ema_med = last_row.get("ema_medium")
        ema_slow = last_row.get("ema_slow")
        close_price = last_row.get("close")

        if ema_fast is not None and ema_med is not None and ema_slow is not None:
            if direction == Direction.BUY:
                if ema_fast > ema_med > ema_slow:
                    score += 10.0
                    details["ema_alignment"] = "FULL_BULLISH"
                elif ema_fast > ema_med or ema_med > ema_slow:
                    score += 5.0
                    details["ema_alignment"] = "PARTIAL_BULLISH"
            else:
                if ema_fast < ema_med < ema_slow:
                    score += 10.0
                    details["ema_alignment"] = "FULL_BEARISH"
                elif ema_fast < ema_med or ema_med < ema_slow:
                    score += 5.0
                    details["ema_alignment"] = "PARTIAL_BEARISH"

        # 2. Price vs EMA200
        if ema_slow is not None and close_price is not None:
            if direction == Direction.BUY and close_price > ema_slow:
                score += 5.0
                details["price_vs_ema200"] = True
            elif direction == Direction.SELL and close_price < ema_slow:
                score += 5.0
                details["price_vs_ema200"] = True

        # 3. ADX & Directional Movement
        adx_val = last_row.get("adx")
        plus_di = last_row.get("plus_di")
        minus_di = last_row.get("minus_di")

        if adx_val is not None and not pd.isna(adx_val):
            details["adx_value"] = float(adx_val)
            di_aligned = False
            if direction == Direction.BUY and (plus_di is not None and minus_di is not None):
                di_aligned = plus_di > minus_di
            elif direction == Direction.SELL and (plus_di is not None and minus_di is not None):
                di_aligned = minus_di > plus_di
            else:
                di_aligned = True

            if adx_val >= adx_strong and di_aligned:
                score += 5.0
                details["adx_strength"] = "STRONG"
            elif adx_val >= adx_min and di_aligned:
                score += 3.0
                details["adx_strength"] = "MODERATE"

        # 4. EMA50 Slope
        ema50_slope = last_row.get("ema_medium_slope")
        if ema50_slope is not None and not pd.isna(ema50_slope):
            details["ema50_slope"] = float(ema50_slope)
            if direction == Direction.BUY:
                if ema50_slope > 0.05:
                    score += 5.0
                elif ema50_slope > 0.0:
                    score += 3.0
            else:
                if ema50_slope < -0.05:
                    score += 5.0
                elif ema50_slope < 0.0:
                    score += 3.0

        # Flat chop attenuation: if ADX < 15, moving averages are lagging relics of past moves
        if adx_val is not None and not pd.isna(adx_val) and float(adx_val) < 15.0:
            score = score * 0.5
            details["chop_attenuation"] = True

        final_score = min(self.max_score, max(0.0, score))
        return ComponentScore(
            name="trend",
            score=final_score,
            max_score=self.max_score,
            details=details
        )
