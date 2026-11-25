"""Market structure scoring strategy (Weight: 30 points)."""
from typing import Dict, Any, Optional

from core.constants import Direction, SwingClassification
from core.models import ComponentScore, StructureAnalysis


class StructureStrategy:
    """Evaluates market structure confirmation (0 to 30 points)."""

    def __init__(self, max_score: float = 30.0):
        self.max_score = max_score

    def calculate_score(
        self,
        analysis: StructureAnalysis,
        direction: Direction,
        max_swing_age: int = 100
    ) -> ComponentScore:
        """Calculate market structure score for the target direction.

        Points breakdown (Max: 30):
        - Valid BOS aligned with direction: +15
        - Structure alignment (HH/HL for BUY, LL/LH for SELL): +10
        - Recent swing point (< max_swing_age bars): +5
        - Penalty: Counter-trend BOS or counter-CHoCH: -5
        """
        score = 0.0
        details: Dict[str, Any] = {
            "bos_aligned": False,
            "structure_aligned": False,
            "recent_swing": False,
            "counter_break_penalty": False,
            "raw_points": 0.0,
        }

        raw_points = 0.0
        penalty = 0.0

        # 1. Check BOS alignment (+15 pts or penalty, decayed by age)
        if analysis.bos_list:
            recent_bos = max(analysis.bos_list, key=lambda item: item.bar_index)
            current_bar = analysis.last_bar_index
            bos_age = (current_bar - recent_bos.bar_index) if (current_bar >= 0 and recent_bos.bar_index is not None) else 0

            if recent_bos.direction == direction:
                if bos_age <= 5:
                    bos_pts = 15.0
                elif bos_age <= 15:
                    bos_pts = 10.0
                elif bos_age <= 30:
                    bos_pts = 5.0
                else:
                    bos_pts = 0.0

                raw_points += bos_pts
                details["bos_aligned"] = bool(bos_pts > 0)
                details["bos_age_bars"] = bos_age
                details["last_bos_price"] = recent_bos.price
            else:
                if bos_age <= 20:
                    penalty += 5.0
                    details["counter_break_penalty"] = True

        # 2. Check structure sequence alignment (+10 pts)
        if direction == Direction.BUY:
            has_hh = any(s.classification == SwingClassification.HH for s in analysis.swings[-4:]) if analysis.swings else False
            has_hl = any(s.classification == SwingClassification.HL for s in analysis.swings[-4:]) if analysis.swings else False
            if has_hh or has_hl:
                aligned_pts = 10.0 if (has_hh and has_hl) else 6.0
                raw_points += aligned_pts
                details["structure_aligned"] = True
                details["has_hh"] = has_hh
                details["has_hl"] = has_hl
        else:
            has_ll = any(s.classification == SwingClassification.LL for s in analysis.swings[-4:]) if analysis.swings else False
            has_lh = any(s.classification == SwingClassification.LH for s in analysis.swings[-4:]) if analysis.swings else False
            if has_ll or has_lh:
                aligned_pts = 10.0 if (has_ll and has_lh) else 6.0
                raw_points += aligned_pts
                details["structure_aligned"] = True
                details["has_ll"] = has_ll
                details["has_lh"] = has_lh

        # 3. Check swing freshness (+5 pts)
        last_swing = analysis.last_swing_low if direction == Direction.BUY else analysis.last_swing_high
        current_bar = analysis.last_bar_index
        is_fresh = last_swing is not None and (
            current_bar < 0 or current_bar - last_swing.bar_index <= max_swing_age
        )
        if is_fresh:
            raw_points += 5.0
            details["recent_swing"] = True
            details["last_swing_price"] = last_swing.price

        # 4. CHoCH check: if recent CHoCH is opposite direction, apply penalty
        if analysis.choch_list:
            last_choch = max(analysis.choch_list, key=lambda item: item.bar_index)
            if last_choch.direction != direction:
                penalty += 5.0
                details["counter_choch_penalty"] = True

        final_score = min(self.max_score, max(0.0, raw_points - penalty))
        details["raw_points"] = raw_points - penalty

        return ComponentScore(
            name="structure",
            score=final_score,
            max_score=self.max_score,
            details=details
        )
