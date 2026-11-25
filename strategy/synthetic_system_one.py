"""Quantitative System 1 Evaluator for Deriv Synthetic Indices (Boom, Crash, Volatility).

Evaluates candidate setups through non-autoregressive decision models (Laya, NanoJev, Kev)
using strict, unambiguous mathematical state representations (Hawkes jump clustering,
structural demand/supply floors, multi-timeframe drift exhaustion).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from core.constants import Direction
from filters.system_one.base import (
    SystemOneDecision,
    ThesisAlignment,
    TrapRisk,
)
from filters.system_one.hub import SystemOneHub

logger = logging.getLogger("SyntheticSystemOne")


SYNTHETIC_SYSTEM_ONE_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "thesis_alignment": {
        "type": "choice",
        "instructions": (
            "Using only the supplied closed-bar facts for this Deriv synthetic index (Boom, Crash, or Volatility), "
            "classify whether the setup thesis is coherent. "
            "For Boom (BUY): requires H1 bullish regime, price at structural demand floor with absorption wick, or jump cluster. "
            "For Crash (SELL): requires H1 bearish regime, price at supply ceiling with rejection wick, or jump cluster. "
            "For Volatility (BUY/SELL): requires HTF macro alignment (H1/M15 trend matching direction), pullback to institutional value zone or squeeze release. "
            "If price moves against macro regime or is overextended, choose contradictory."
        ),
        "criteria": {
            "aligned": "Setup satisfies macro regime alignment and structural boundary with confirmed absorption.",
            "mixed": "Near zone but weak rejection wick or marginal distance.",
            "contradictory": "Counter-trend macro regime, overextended drift, or missing structural boundary.",
            "insufficient": "Insufficient historical bars to determine regime.",
        },
    },
    "evidence_quality": {
        "type": "score",
        "instructions": (
            "Score from 1 to 5 how solid the structural evidence is for this synthetic trade candidate."
        ),
        "criteria": [
            "1: Direct conflict with H1 regime or falling/rising through invalidation",
            "2: Weak rejection, counter-trend drift continuing with momentum",
            "3: Standard touch of zone with moderate wick",
            "4: High confluence: macro H1 aligned + prime swing floor/ceiling + clear absorption",
            "5: Perfect confluence: institutional value zone retest / squeeze release with tight structural invalidation",
        ],
    },
    "trap_risk": {
        "type": "choice",
        "instructions": (
            "Assess whether this setup risks being a false wick / drift continuation trap."
        ),
        "criteria": {
            "genuine_expansion": "High probability continuation/expansion zone with confirmed absorption.",
            "potential_trap": "Drift momentum is accelerating against the trade with no absorption.",
            "overextended_exhaustion": "Price has drifted too far without reversal confirmation, decaying probability.",
        },
    },
}


from enum import Enum


class ExecutionTier(str, Enum):
    TIER_A_ALPHA = "TIER_A_ALPHA"          # High conviction: 100% risk, full target R:R (2.2R-2.5R)
    TIER_B_SURGICAL = "TIER_B_SURGICAL"    # Baseline surgical: 50% risk, capped target 1.50R, faster BE 0.80R
    TIER_C_VETO = "TIER_C_VETO"            # Toxic liquidity trap / macro conflict: 0% risk (Blocked)


@dataclass
class SyntheticEvaluationResult:
    """Strict, unambiguous evaluation result for synthetic trade execution."""
    approved: bool
    signal_id: str
    symbol: str
    direction: str
    provider: str
    thesis_alignment: str
    evidence_score: int
    trap_risk: str
    confidence: float
    latency_ms: float
    rejection_reasons: List[str]
    tier: str = ExecutionTier.TIER_A_ALPHA.value
    risk_multiplier: float = 1.0
    target_rr_cap: Optional[float] = None
    be_activation_r: Optional[float] = None

    @property
    def summary(self) -> str:
        status = "APPROVED" if self.approved else "REJECTED"
        tier_info = f" | Tier: {self.tier} ({int(self.risk_multiplier * 100)}% risk)" if self.approved else ""
        return (
            f"[{self.provider} SYSTEM-1 {status}{tier_info}] {self.symbol} {self.direction} | "
            f"Thesis: {self.thesis_alignment} (conf: {self.confidence:.2f}) | "
            f"Score: {self.evidence_score}/5 | Trap: {self.trap_risk} | Latency: {self.latency_ms:.1f}ms"
        )


class SyntheticSystemOneEvaluator:
    """Quantitative state extractor and decision gate for Synthetic Indices."""

    def __init__(
        self,
        hub: SystemOneHub,
        shadow_mode: bool = True,
        min_evidence_score: int = 3,
        min_confidence: float = 0.60,
    ):
        self.hub = hub
        self.shadow_mode = shadow_mode
        self.min_evidence_score = min_evidence_score
        self.min_confidence = min_confidence

    def build_candidate_state(
        self,
        symbol: str,
        direction: Direction,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        target_rr: float,
        df_m5: pd.DataFrame,
        df_m15: Optional[pd.DataFrame],
        df_h1: Optional[pd.DataFrame],
        confluences: Tuple[str, ...],
        point: float = 0.01,
    ) -> Dict[str, Any]:
        """Extract unambiguous, normalized mathematical state tensor for System 1."""
        curr_m5 = df_m5.iloc[-1]
        prev_m5 = df_m5.iloc[-2] if len(df_m5) >= 2 else curr_m5

        c_close = float(curr_m5["close"])
        c_atr = float(curr_m5["atr"]) if "atr" in curr_m5 else float(curr_m5["high"] - curr_m5["low"])
        c_rsi = float(curr_m5["rsi"]) if "rsi" in curr_m5 else 50.0

        # Previous bar wick metrics (measuring buyer/seller absorption)
        p_open = float(prev_m5["open"])
        p_close = float(prev_m5["close"])
        p_high = float(prev_m5["high"])
        p_low = float(prev_m5["low"])
        p_range = max(p_high - p_low, point * 5)
        p_lower_wick_ratio = round((min(p_open, p_close) - p_low) / p_range, 4)
        p_upper_wick_ratio = round((p_high - max(p_open, p_close)) / p_range, 4)

        # Macro H1 state
        h1_trend = "UNKNOWN"
        h1_dist_ema50_atr = 0.0
        if df_h1 is not None and not df_h1.empty and len(df_h1) >= 20:
            h1_bar = df_h1.iloc[-2]
            h1_close = float(h1_bar["close"])
            if "ema_50" not in df_h1.columns:
                h1_ema50 = float(df_h1["close"].ewm(span=50, adjust=False).mean().iloc[-2])
            else:
                h1_ema50 = float(h1_bar["ema_50"])
            h1_trend = "BULLISH" if h1_close > h1_ema50 else "BEARISH"
            h1_dist_ema50_atr = round((h1_close - h1_ema50) / max(c_atr, 0.0001), 2)

        # Hawkes jump clustering evidence & index classification
        has_recent_spike = "JUMP_CLUSTER_CONTINUATION" in confluences
        is_boom = "BOOM" in symbol.upper() or "B1000" in symbol.upper()
        is_crash = "CRASH" in symbol.upper() or "C1000" in symbol.upper()
        is_vol = not is_boom and not is_crash

        # Structural floor / ceiling calculation
        if has_recent_spike:
            # For Jump Clustering, structural level is the origin/pullback base of the recent spike bar
            spike_col = "is_boom_spike" if is_boom else "is_crash_spike"
            if spike_col in df_m5.columns:
                spike_bars = df_m5[df_m5[spike_col]].tail(3)
                if not spike_bars.empty:
                    last_spike = spike_bars.iloc[-1]
                    structural_level = float(last_spike["low"]) if is_boom else float(last_spike["high"])
                else:
                    structural_level = c_close
            else:
                structural_level = c_close
            dist_to_zone_atr = round(abs(c_close - structural_level) / max(c_atr, 0.0001), 2)
        elif is_vol:
            # For continuous Volatility indices: look at immediate swing pivot / value zone
            lookback_bars = df_m5.iloc[:-3].tail(25) if len(df_m5) >= 28 else df_m5
            if direction == Direction.BUY:
                structural_level = float(lookback_bars["low"].min())
                dist_to_zone_atr = round((c_close - structural_level) / max(c_atr, 0.0001), 2)
            else:
                structural_level = float(lookback_bars["high"].max())
                dist_to_zone_atr = round((structural_level - c_close) / max(c_atr, 0.0001), 2)
        else:
            lookback_bars = df_m5.iloc[:-3].tail(35) if len(df_m5) >= 38 else df_m5
            if is_boom:
                structural_level = float(lookback_bars["low"].min())
                dist_to_zone_atr = round((c_close - structural_level) / max(c_atr, 0.0001), 2)
            else:
                structural_level = float(lookback_bars["high"].max())
                dist_to_zone_atr = round((structural_level - c_close) / max(c_atr, 0.0001), 2)

        risk_points = round(abs(entry_price - sl_price), 2)

        # Continuous Brownian Volatility Dynamics
        c_chop = float(curr_m5["chop"]) if "chop" in curr_m5 else 50.0
        c_ker = float(curr_m5["ker"]) if "ker" in curr_m5 else 0.35
        is_chop_regime = bool((c_chop >= 60.0) or (c_ker < 0.25))
        is_trend_pullback = any("TREND_PULLBACK" in c or "VALUE_ZONE_RETEST" in c for c in confluences)
        is_momentum_expansion = any("MOMENTUM_TREND_EXPANSION" in c or "DONCHIAN" in c or "SQUEEZE" in c for c in confluences)
        is_trap_reversal = any("SWING_FAILURE_PATTERN" in c or "LIQUIDITY_TRAP" in c or "TRAP_EXPLOITED" in c for c in confluences)

        # Quantitative confluence scoring (0 to 100 points)
        if is_vol:
            macro_score = 25.0 if (direction == Direction.BUY and h1_trend == "BULLISH") or (direction == Direction.SELL and h1_trend == "BEARISH") else 0.0

            if is_trap_reversal:
                # SFP Trap Reversals exploit trapped retail order flow: peak structural conviction
                macro_score = 25.0 if macro_score > 0 else 18.0
                struct_score = 30.0
                active_wick = p_lower_wick_ratio if direction == Direction.BUY else p_upper_wick_ratio
                wick_score = min(20.0, max(12.0, (active_wick / 0.15) * 20.0))
                cluster_score = 25.0
                rsi_score = 10.0
            elif is_momentum_expansion:
                if is_chop_regime:
                    # BREAKOUT IN CHOP IS A TOXIC FAKEOUT: Zero points
                    struct_score = 0.0
                    macro_score = 0.0
                    wick_score = 0.0
                    cluster_score = 0.0
                    rsi_score = 0.0
                else:
                    struct_score = 25.0
                    active_wick = p_lower_wick_ratio if direction == Direction.BUY else p_upper_wick_ratio
                    wick_score = min(20.0, max(10.0, (active_wick / 0.10) * 20.0))
                    cluster_score = 20.0
                    rsi_score = 10.0 if (50.0 <= c_rsi <= 82.0 if direction == Direction.BUY else 18.0 <= c_rsi <= 50.0) else max(0.0, 10.0 - abs(c_rsi - 50.0) * 0.2)
            else:
                struct_score = 30.0 if dist_to_zone_atr <= 1.2 else max(0.0, 30.0 - (dist_to_zone_atr - 1.2) * 15.0)
                active_wick = p_lower_wick_ratio if direction == Direction.BUY else p_upper_wick_ratio
                wick_score = min(20.0, (active_wick / 0.15) * 20.0) if active_wick > 0 else 0.0
                cluster_score = 25.0 if any(c in confluences for c in ("INSTITUTIONAL_VALUE_ZONE_RETEST", "KELTNER_BOLLINGER_SQUEEZE_RELEASE")) else 5.0
                rsi_score = 10.0 if (35.0 <= c_rsi <= 65.0) else max(0.0, 10.0 - abs(c_rsi - 50.0) * 0.3)

                # High-entropy consolidation penalty: pullbacks in chop have negative expectation
                if is_chop_regime and is_trend_pullback:
                    struct_score = max(0.0, struct_score - 20.0)
                    macro_score = max(0.0, macro_score - 15.0)
        else:
            macro_score = 25.0 if (is_boom and h1_trend == "BULLISH") or (not is_boom and h1_trend == "BEARISH") else 0.0
            struct_score = 30.0 if dist_to_zone_atr <= 0.8 else max(0.0, 30.0 - (dist_to_zone_atr - 0.8) * 15.0)
            active_wick = p_lower_wick_ratio if is_boom else p_upper_wick_ratio
            wick_score = min(20.0, (active_wick / 0.25) * 20.0) if active_wick > 0 else 0.0
            cluster_score = 25.0 if has_recent_spike else 5.0
            if is_boom:
                rsi_score = 10.0 if c_rsi <= 50.0 else max(0.0, (70.0 - c_rsi) / 2.0)
            else:
                rsi_score = 10.0 if c_rsi >= 50.0 else max(0.0, (c_rsi - 30.0) / 2.0)

        total_score = round(macro_score + struct_score + wick_score + cluster_score + rsi_score, 1)

        state = {
            "signal_id": f"syn_{symbol[:4]}_{int(time.time())}",
            "symbol": symbol,
            "direction": direction.value,
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "tp_price": round(tp_price, 2),
            "target_rr": round(target_rr, 2),
            "risk_points": risk_points,
            "risk_to_atr_ratio": round(risk_points / max(c_atr, 0.001), 2),
            "score": {
                "total": total_score,
                "macro": macro_score,
                "structure": struct_score,
                "absorption": wick_score,
                "cluster": cluster_score,
                "momentum": rsi_score,
            },
            "market_evidence": {
                "macro_regime": {
                    "h1_trend": h1_trend,
                    "h1_distance_ema50_atr": h1_dist_ema50_atr,
                    "chop_index": round(c_chop, 1),
                    "efficiency_ratio": round(c_ker, 2),
                    "is_chop_regime": is_chop_regime,
                },
                "structure_demand": {
                    "distance_to_zone_atr": dist_to_zone_atr,
                    "rejection_wick_ratio": p_lower_wick_ratio if (is_boom or (is_vol and direction == Direction.BUY)) else p_upper_wick_ratio,
                    "drift_rsi_m5": round(c_rsi, 1),
                },
                "jump_clustering": {
                    "has_recent_spike": has_recent_spike,
                    "confluences": list(confluences),
                },
                "liquidity": {
                    "sweep": {
                        "detected": bool(has_recent_spike or dist_to_zone_atr <= 0.6),
                        "direction": direction.value,
                    }
                },
                "momentum": {
                    "m5_rsi": round(c_rsi, 1),
                },
            },
        }
        return state

    def evaluate_setup(
        self,
        symbol: str,
        direction: Direction,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        target_rr: float,
        df_m5: pd.DataFrame,
        df_m15: Optional[pd.DataFrame],
        df_h1: Optional[pd.DataFrame],
        confluences: Tuple[str, ...],
        point: float = 0.01,
    ) -> SyntheticEvaluationResult:
        """Evaluate trade candidate through System 1 without ambiguity."""
        state = self.build_candidate_state(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            target_rr=target_rr,
            df_m5=df_m5,
            df_m15=df_m15,
            df_h1=df_h1,
            confluences=confluences,
            point=point,
        )
        signal_id = state["signal_id"]

        # Run evaluation via System 1 Hub
        decision: Optional[SystemOneDecision] = self.hub.evaluate_sync(state)

        rejection_reasons: List[str] = []
        if decision is None:
            # Fallback if model fails or is offline: apply deterministic mathematical safeguards
            evidence = state["market_evidence"]
            macro = evidence["macro_regime"]
            h1_ok = (direction == Direction.BUY and macro["h1_trend"] == "BULLISH") or \
                    (direction == Direction.SELL and macro["h1_trend"] == "BEARISH")
            
            if not h1_ok:
                rejection_reasons.append(f"FALLBACK_REJECT: H1 {macro['h1_trend']} conflicts with {direction.value}")

            approved = len(rejection_reasons) == 0
            res = SyntheticEvaluationResult(
                approved=approved or self.shadow_mode,
                signal_id=signal_id,
                symbol=symbol,
                direction=direction.value,
                provider="DeterministicFallback",
                thesis_alignment="aligned" if approved else "contradictory",
                evidence_score=4 if approved else 1,
                trap_risk="genuine_expansion" if approved else "potential_trap",
                confidence=0.85 if approved else 0.40,
                latency_ms=0.5,
                rejection_reasons=rejection_reasons,
            )
            logger.info(res.summary)
            return res

        # Neo Híbrido: Multi-Tier Execution Arbitration
        is_macro_contradiction = decision.thesis_alignment == ThesisAlignment.CONTRADICTORY.value
        is_toxic_score = decision.evidence_score <= 1
        is_high_trap = decision.trap_risk == TrapRisk.POTENTIAL_TRAP.value and decision.evidence_score <= 2

        evidence = state["market_evidence"]
        macro = evidence.get("macro_regime", {})
        c_chop = float(macro.get("chop_index", 50.0))
        c_ker = float(macro.get("efficiency_ratio", 0.35))
        is_extreme_chop = bool((c_chop >= 68.0) or (c_ker < 0.16))
        is_moderate_chop = bool((c_chop >= 60.0) or (c_ker < 0.25))
        is_trend_pullback = any("TREND_PULLBACK" in c or "VALUE_ZONE_RETEST" in c for c in confluences)
        is_momentum_expansion = any("MOMENTUM_TREND_EXPANSION" in c or "DONCHIAN" in c or "SQUEEZE" in c for c in confluences)
        is_trap_reversal = any("SWING_FAILURE_PATTERN" in c or "LIQUIDITY_TRAP" in c or "TRAP_EXPLOITED" in c for c in confluences)
        is_vol = "VOLATILITY" in symbol.upper() or "V75" in symbol.upper() or "V100" in symbol.upper()

        is_chop_veto = is_vol and is_extreme_chop and is_trend_pullback
        is_breakout_chop_veto = is_vol and is_momentum_expansion and (c_chop >= 56.0 or c_ker < 0.28)

        # Elastic Gating Calibration:
        # In clean, low-entropy trends (CHOP < 55.0 and KER >= 0.30), relax threshold:
        # SFP Trap Reversals qualify directly for Tier A Alpha with evidence_score >= 3.
        is_clean_trend = bool((c_chop < 55.0) and (c_ker >= 0.30))
        is_sfp_tier_a = is_vol and is_trap_reversal and decision.evidence_score >= 3 and decision.thesis_confidence >= 0.45
        qualifies_tier_a = (
            is_sfp_tier_a
            or (decision.evidence_score >= 4 and decision.thesis_confidence >= 0.58 and decision.trap_risk != TrapRisk.POTENTIAL_TRAP.value)
            or (is_clean_trend and decision.evidence_score >= 3 and decision.thesis_confidence >= 0.50 and decision.trap_risk != TrapRisk.POTENTIAL_TRAP.value)
        )

        tier: ExecutionTier
        risk_multiplier: float
        target_rr_cap: Optional[float]
        be_activation_r: Optional[float]
        is_approved: bool

        if is_macro_contradiction or is_toxic_score or is_high_trap or is_chop_veto or is_breakout_chop_veto:
            tier = ExecutionTier.TIER_C_VETO
            risk_multiplier = 0.0
            target_rr_cap = None
            be_activation_r = None
            is_approved = False
            if is_breakout_chop_veto:
                rejection_reasons.append(f"SYSTEM1_REJECT: Breakout attempt in consolidating market (CHOP={c_chop:.1f} >= 56.0). High fakeout risk.")
            elif is_chop_veto:
                rejection_reasons.append("SYSTEM1_REJECT: Extreme high-entropy chop detected (CHOP >= 68.0 / KER < 0.16). Trend pullback vetoed.")
            elif is_macro_contradiction:
                rejection_reasons.append("SYSTEM1_REJECT: Thesis contradicted by macro/structural evidence.")
            elif is_toxic_score:
                rejection_reasons.append(f"SYSTEM1_REJECT: Toxic evidence score {decision.evidence_score} <= 1.")
            elif is_high_trap:
                rejection_reasons.append("SYSTEM1_REJECT: High risk of drift continuation / liquidity trap.")
        elif qualifies_tier_a:
            tier = ExecutionTier.TIER_A_ALPHA
            risk_multiplier = 1.0
            target_rr_cap = None
            be_activation_r = 1.0
            is_approved = True
        else:
            tier = ExecutionTier.TIER_B_SURGICAL
            risk_multiplier = 0.50
            target_rr_cap = 1.50
            be_activation_r = 0.80
            is_approved = True

        final_approved = is_approved or self.shadow_mode

        result = SyntheticEvaluationResult(
            approved=final_approved,
            signal_id=signal_id,
            symbol=symbol,
            direction=direction.value,
            provider=decision.provider,
            thesis_alignment=decision.thesis_alignment,
            evidence_score=decision.evidence_score,
            trap_risk=decision.trap_risk or "unknown",
            confidence=decision.thesis_confidence,
            latency_ms=decision.latency_ms,
            rejection_reasons=rejection_reasons,
            tier=tier.value,
            risk_multiplier=risk_multiplier,
            target_rr_cap=target_rr_cap,
            be_activation_r=be_activation_r,
        )
        logger.info(result.summary)
        if not is_approved:
            logger.warning("   [FILTRO SYSTEM-1 MOTIVO]: %s", " | ".join(rejection_reasons))

        return result
