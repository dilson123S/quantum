"""Momentum scoring strategy (Weight: 15 points)."""
import pandas as pd
from typing import Dict, Any

from core.constants import Direction
from core.models import ComponentScore


class MomentumStrategy:
    """Evaluates momentum confirmation (0 to 15 points)."""

    def __init__(self, max_score: float = 15.0):
        self.max_score = max_score

    def calculate_score(
        self,
        df: pd.DataFrame,
        direction: Direction,
        rsi_bull_min: float = 50.0,
        rsi_bull_strong: float = 55.0,
        rsi_bear_max: float = 50.0,
        rsi_bear_strong: float = 45.0
    ) -> ComponentScore:
        """Calculate momentum score.

        Points breakdown (Max: 15):
        - RSI confirmation: +5 for strong momentum (>55 BUY, <45 SELL), +3 for moderate (>50 BUY, <50 SELL)
        - MACD line vs Signal line: +5 for aligned crossover/position
        - MACD Histogram slope/expansion: +5 for growing momentum in trade direction (+2 if neutral)
        """
        score = 0.0
        details: Dict[str, Any] = {
            "rsi_value": 0.0,
            "rsi_status": "NEUTRAL",
            "macd_aligned": False,
            "histogram_status": "FLAT",
        }

        if df is None or len(df) == 0:
            return ComponentScore(name="momentum", score=0.0, max_score=self.max_score, details=details)

        last_row = df.iloc[-1]

        # 1. RSI Scoring (+5 pts max)
        rsi_val = last_row.get("rsi")
        if rsi_val is not None and not pd.isna(rsi_val):
            details["rsi_value"] = float(rsi_val)
            if direction == Direction.BUY:
                if rsi_val > 78.0:
                    # Overextended exhaustion: buying near the very top carries high reversal danger
                    score += 1.0
                    details["rsi_status"] = "OVERBOUGHT_EXHAUSTION"
                elif rsi_val >= rsi_bull_strong:
                    score += 5.0
                    details["rsi_status"] = "STRONG_BULLISH"
                elif rsi_val >= rsi_bull_min:
                    score += 3.0
                    details["rsi_status"] = "MODERATE_BULLISH"
            else:
                if rsi_val < 22.0:
                    # Oversold exhaustion: selling near the very bottom carries high short-squeeze danger
                    score += 1.0
                    details["rsi_status"] = "OVERSOLD_EXHAUSTION"
                elif rsi_val <= rsi_bear_strong:
                    score += 5.0
                    details["rsi_status"] = "STRONG_BEARISH"
                elif rsi_val <= rsi_bear_max:
                    score += 3.0
                    details["rsi_status"] = "MODERATE_BEARISH"

        # 2. MACD vs Signal line (+5 pts)
        macd_line = last_row.get("macd")
        macd_signal = last_row.get("macd_signal")
        if macd_line is not None and macd_signal is not None and not pd.isna(macd_line) and not pd.isna(macd_signal):
            if direction == Direction.BUY and macd_line > macd_signal:
                score += 5.0
                details["macd_aligned"] = True
            elif direction == Direction.SELL and macd_line < macd_signal:
                score += 5.0
                details["macd_aligned"] = True

        # 3. Histogram / Slope (+5 pts)
        hist = last_row.get("macd_histogram")
        hist_slope = last_row.get("histogram_slope")

        if hist is not None and not pd.isna(hist):
            if direction == Direction.BUY:
                if hist > 0 and (hist_slope is not None and hist_slope > 0):
                    score += 5.0
                    details["histogram_status"] = "GROWING_POSITIVE"
                elif hist > 0:
                    score += 3.0
                    details["histogram_status"] = "POSITIVE"
                elif hist_slope is not None and hist_slope > 0:
                    score += 2.0
                    details["histogram_status"] = "RISING"
            else:
                if hist < 0 and (hist_slope is not None and hist_slope < 0):
                    score += 5.0
                    details["histogram_status"] = "EXPANDING_NEGATIVE"
                elif hist < 0:
                    score += 3.0
                    details["histogram_status"] = "NEGATIVE"
                elif hist_slope is not None and hist_slope < 0:
                    score += 2.0
                    details["histogram_status"] = "FALLING"

        final_score = min(self.max_score, max(0.0, score))
        return ComponentScore(
            name="momentum",
            score=final_score,
            max_score=self.max_score,
            details=details
        )
