"""Liquidity Sweep / False Breakout Reversal Strategy.

Exploits institutional manipulation by detecting sweeps of key liquidity levels
(Previous Day High/Low, Asian High/Low, Swing High/Low) where the price pierces
the level but closes back inside the range, creating a high-probability reversal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

from core.config_loader import AppConfig
from core.constants import Direction, SignalClassification
from core.models import ComponentScore, ScoreResult, Signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepReversalSignal:
    direction: Direction
    entry_price: float
    sl_price: float
    tp_price: float
    level_name: str
    level_price: float
    penetration_atr: float
    risk_reward: float


class SweepReversalStrategy:
    """Evaluates closed-bar context to generate sweep reversal trade opportunities."""

    def __init__(self, config: AppConfig):
        self.config = config

    def detect_sweep_reversal(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        decision_context: Dict[str, Any],
        digits: int = 2,
    ) -> Optional[SweepReversalSignal]:
        """Check if the latest closed bar completed an institutional liquidity sweep."""
        if not getattr(self.config, "sweep_reversal", None) or not self.config.sweep_reversal.enabled:
            return None

        if df is None or df.empty or len(df) < 5:
            return None

        liquidity = decision_context.get("liquidity", {}) if decision_context else {}
        sweep = liquidity.get("sweep", {}) if liquidity else {}

        if not sweep.get("detected"):
            return None

        sweep_dir_str = sweep.get("direction")
        if not sweep_dir_str or sweep_dir_str not in ("BUY", "SELL"):
            return None

        direction = Direction(sweep_dir_str)
        level_name = str(sweep.get("level", "UNKNOWN_LEVEL"))
        penetration_atr = float(sweep.get("penetration_atr") or 0.0)

        min_pen = float(self.config.sweep_reversal.min_penetration_atr)
        if penetration_atr < min_pen:
            logger.debug(
                "[%s] Sweep penetration %.2f ATR below threshold %.2f ATR",
                symbol, penetration_atr, min_pen,
            )
            return None

        last_bar = df.iloc[-1]
        close_price = float(last_bar["close"])
        high_price = float(last_bar["high"])
        low_price = float(last_bar["low"])
        atr = float(last_bar.get("atr") or 0.0)
        if atr <= 0:
            return None

        sl_buffer = atr * float(self.config.sweep_reversal.sl_buffer_atr)

        # ADX Overheat Guard: Counter-trend sweeps against an explosive momentum trend (ADX > 35)
        # have a high failure rate due to strong continuation impulses.
        adx_val = float(last_bar.get("adx") or 0.0)
        max_reversal_adx = float(getattr(self.config.sweep_reversal, "max_reversal_adx", 35.0))
        if adx_val > 0.0 and adx_val > max_reversal_adx:
            logger.info(
                "[%s] Sweep reversal BLOCKED by ADX Overheat Guard: ADX %.1f > %.1f (trend too strong to fade)",
                symbol, adx_val, max_reversal_adx,
            )
            return None

        # 1. Entry is at the current closed bar's price
        entry = round(close_price, digits)

        # 2. Stop loss is placed tightly beyond the wick extreme
        if direction == Direction.SELL:
            # Price swept a high and closed below it. SL is above the candle high.
            sl = round(high_price + sl_buffer, digits)
            risk = sl - entry
            if risk <= 0:
                return None

            # 3. Take Profit target
            # Default: Look for opposite liquidity level
            tp_candidate = None
            if self.config.sweep_reversal.target_opposite_level:
                pdl = liquidity.get("previous_day", {}).get("low")
                asia_low = liquidity.get("asia", {}).get("low")
                if pdl and float(pdl) < entry:
                    tp_candidate = float(pdl)
                elif asia_low and float(asia_low) < entry:
                    tp_candidate = float(asia_low)

            min_rr = float(self.config.sweep_reversal.min_rr)
            min_target = entry - (risk * min_rr)
            if tp_candidate is not None and tp_candidate <= entry - (risk * 1.5):
                tp = round(tp_candidate, digits)
            else:
                tp = round(min_target, digits)

            rr = abs(entry - tp) / risk

        else:  # Direction.BUY
            # Price swept a low and closed above it. SL is below the candle low.
            sl = round(low_price - sl_buffer, digits)
            risk = entry - sl
            if risk <= 0:
                return None

            tp_candidate = None
            if self.config.sweep_reversal.target_opposite_level:
                pdh = liquidity.get("previous_day", {}).get("high")
                asia_high = liquidity.get("asia", {}).get("high")
                if pdh and float(pdh) > entry:
                    tp_candidate = float(pdh)
                elif asia_high and float(asia_high) > entry:
                    tp_candidate = float(asia_high)

            min_rr = float(self.config.sweep_reversal.min_rr)
            min_target = entry + (risk * min_rr)
            if tp_candidate is not None and tp_candidate >= entry + (risk * 1.5):
                tp = round(tp_candidate, digits)
            else:
                tp = round(min_target, digits)

            rr = abs(tp - entry) / risk

        if rr < float(self.config.sweep_reversal.min_rr):
            logger.debug(
                "[%s] Sweep reversal R:R (%.2f) below minimum %.2f",
                symbol, rr, self.config.sweep_reversal.min_rr,
            )
            return None

        # Determine level price
        level_price = 0.0
        nearest = liquidity.get("nearest_level", {})
        if nearest and nearest.get("name") == level_name:
            level_price = float(nearest.get("price", 0.0))

        return SweepReversalSignal(
            direction=direction,
            entry_price=entry,
            sl_price=sl,
            tp_price=tp,
            level_name=level_name,
            level_price=level_price,
            penetration_atr=penetration_atr,
            risk_reward=round(rr, 2),
        )

    def build_sweep_signal(
        self,
        symbol: str,
        timeframe: str,
        sweep_sig: SweepReversalSignal,
    ) -> Signal:
        """Create a full Signal object compatible with Quantum execution pipeline."""
        now = datetime.now(timezone.utc)
        struct_score = ComponentScore(name="structure", score=30.0, max_score=30.0, details={"reason": f"SWEEP_{sweep_sig.level_name}", "penetration_atr": sweep_sig.penetration_atr})
        trend_score = ComponentScore(name="trend", score=15.0, max_score=25.0, details={"aligned": True, "type": "SWEEP_REVERSAL"})
        momentum_score = ComponentScore(name="momentum", score=12.0, max_score=15.0, details={"rejection_wick": True})
        volume_score = ComponentScore(name="volume", score=12.0, max_score=15.0, details={"absorption": True})
        volatility_score = ComponentScore(name="volatility", score=11.0, max_score=15.0, details={"rr": sweep_sig.risk_reward})
        
        total = struct_score.score + trend_score.score + momentum_score.score + volume_score.score + volatility_score.score
        score_res = ScoreResult(
            direction=sweep_sig.direction,
            total_score=total,
            classification=SignalClassification.GOOD,
            structure=struct_score,
            trend=trend_score,
            momentum=momentum_score,
            volume=volume_score,
            volatility=volatility_score,
            timestamp=now,
        )
        return Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=sweep_sig.direction,
            score_result=score_res,
            created_at=now,
            entry_price=sweep_sig.entry_price,
        )
