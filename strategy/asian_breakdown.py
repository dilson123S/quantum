"""Asian Range Breakdown / Expansion Strategy (Smart Money Concepts).

Exploits institutional expansion of the Asian session range in the direction
of the higher-timeframe macro trend (H4 / D1).

- Bearish Macro + Asia Low Broken -> SELL targeting Previous Day Low / NY expansion.
- Bullish Macro + Asia High Broken -> BUY targeting Previous Day High / NY expansion.
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
class AsianBreakdownSignal:
    direction: Direction
    entry_price: float
    sl_price: float
    tp_price: float
    level_name: str
    level_price: float
    target_name: str
    target_price: float
    risk_reward: float


class AsianBreakdownStrategy:
    """Evaluates closed bars to detect institutional Asian session range expansions."""

    def __init__(self, config: AppConfig):
        self.config = config

    def detect_asian_breakdown(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        decision_context: Dict[str, Any],
        df_setup: Optional[pd.DataFrame] = None,
        df_htf: Optional[pd.DataFrame] = None,
        digits: int = 2,
    ) -> Optional[AsianBreakdownSignal]:
        """Detect if the latest closed bar broke the Asian range in the macro trend direction."""
        cfg_ab = getattr(self.config, "asian_breakdown", None)
        if not cfg_ab or not cfg_ab.enabled:
            return None

        if df is None or df.empty or len(df) < 5:
            return None

        liquidity = decision_context.get("liquidity", {}) if decision_context else {}
        asia = liquidity.get("asia", {})
        if not asia or not asia.get("complete", False):
            return None

        asia_high = asia.get("high")
        asia_low = asia.get("low")
        if asia_high is None or asia_low is None:
            return None

        asia_high = float(asia_high)
        asia_low = float(asia_low)

        last_bar = df.iloc[-1]
        bar_time = last_bar.get("time")
        if bar_time is not None:
            bar_dt = pd.to_datetime(bar_time, utc=True)
            hour_utc = bar_dt.hour
            # Institutional Window: Asian expansion is only traded during London & early NY (07:00 to 16:00 UTC)
            if hour_utc < 7 or hour_utc >= 16:
                logger.debug(
                    "[%s] Asian breakdown rejected: outside institutional window 07:00-16:00 UTC (bar hour: %d UTC)",
                    symbol, hour_utc,
                )
                return None

        liquidity = decision_context.get("liquidity", {}) if decision_context else {}
        asia = liquidity.get("asia", {})
        if not asia or not asia.get("complete", False):
            return None

        asia_high = asia.get("high")
        asia_low = asia.get("low")
        if asia_high is None or asia_low is None:
            return None

        asia_high = float(asia_high)
        asia_low = float(asia_low)

        close = float(last_bar["close"])
        high_price = float(last_bar["high"])
        low_price = float(last_bar["low"])
        atr = float(last_bar.get("atr") or 0.0)
        if atr <= 0:
            return None

        # 1. Impulse & Trend Validation: ADX must show real expansion momentum (reject lateral chop)
        adx_val = float(last_bar.get("adx") or 0.0)
        if adx_val > 0 and adx_val < 20.0:
            logger.debug("[%s] Asian breakdown rejected: ADX %.1f < 20.0 (no expansion momentum)", symbol, adx_val)
            return None

        # 2. Institutional Volume Confirmation: Breakout requires real capital participation
        vol_ratio = float(last_bar.get("volume_ratio") or 0.0)
        if vol_ratio == 0.0 and "tick_volume" in df.columns and len(df) >= 20:
            avg_vol = float(df["tick_volume"].iloc[-21:-1].mean() or 1.0)
            vol_ratio = float(last_bar["tick_volume"]) / avg_vol if avg_vol > 0 else 1.0

        if vol_ratio > 0 and vol_ratio < 1.10:
            logger.debug("[%s] Asian breakdown rejected: Volume ratio %.2f < 1.10 (low volume fakeout risk)", symbol, vol_ratio)
            return None

        # 3. Freshness / One-Touch Validation:
        # Check if the level was ALREADY broken and traded through earlier today
        if "time" in df.columns and bar_time is not None:
            times = pd.to_datetime(df["time"], utc=True)
            today_bars = df.loc[times.dt.normalize() == bar_dt.normalize()]
            if not today_bars.empty and len(today_bars) > 1:
                prior_bars = today_bars.iloc[:-1]
                if (prior_bars["low"] < asia_low - (0.5 * atr)).any():
                    logger.debug("[%s] Asian breakdown rejected: ASIA_LOW already penetrated earlier today", symbol)
                    return None
                if (prior_bars["high"] > asia_high + (0.5 * atr)).any():
                    logger.debug("[%s] Asian breakout rejected: ASIA_HIGH already penetrated earlier today", symbol)
                    return None

        # 4. Determine Macro Trend Direction from H4 and D1
        macro_direction: Optional[Direction] = None
        if df_setup is not None and not df_setup.empty and len(df_setup) >= 20:
            last_setup = df_setup.iloc[-1]
            close_h4 = float(last_setup.get("close", 0.0) or 0.0)
            ema_fast_h4 = float(last_setup.get("ema_fast", 0.0) or 0.0)
            ema_med_h4 = float(last_setup.get("ema_medium", 0.0) or 0.0)
            ema_slow_h4 = float(last_setup.get("ema_slow", 0.0) or 0.0)

            # Strong Bearish H4: Close below slow EMA and fast below medium
            if close_h4 < ema_slow_h4 and ema_fast_h4 < ema_med_h4:
                macro_direction = Direction.SELL
            # Strong Bullish H4: Close above slow EMA and fast above medium
            elif close_h4 > ema_slow_h4 and ema_fast_h4 > ema_med_h4:
                macro_direction = Direction.BUY

        if macro_direction is None:
            return None

        # 5. D1 Trend Confluence: Do NOT sell into a strong daily bull trend, nor buy into a strong daily bear trend
        if df_htf is not None and not df_htf.empty and len(df_htf) >= 20:
            last_d1 = df_htf.iloc[-1]
            close_d1 = float(last_d1.get("close", 0.0) or 0.0)
            ema_20_d1 = float(df_htf["close"].ewm(span=20, adjust=False).mean().iloc[-1])
            ema_50_d1 = float(df_htf["close"].ewm(span=50, adjust=False).mean().iloc[-1])

            if macro_direction == Direction.SELL and close_d1 > ema_20_d1 and close_d1 > ema_50_d1:
                logger.debug(
                    "[%s] Asian breakdown rejected: D1 is in strong Bullish trend (Close %.2f > EMA20 %.2f and EMA50 %.2f)",
                    symbol, close_d1, ema_20_d1, ema_50_d1,
                )
                return None
            elif macro_direction == Direction.BUY and close_d1 < ema_20_d1 and close_d1 < ema_50_d1:
                logger.debug(
                    "[%s] Asian breakout rejected: D1 is in strong Bearish trend (Close %.2f < EMA20 %.2f and EMA50 %.2f)",
                    symbol, close_d1, ema_20_d1, ema_50_d1,
                )
                return None

        min_rr = float(cfg_ab.min_rr)

        # 6. Evaluate Setup Based on Macro Direction
        if macro_direction == Direction.SELL:
            # Bearish Asian Breakdown: price broke and closed below ASIA_LOW
            # Must not be overextended (within 1.5 ATR from ASIA_LOW)
            if close < asia_low and (asia_low - close) <= (1.5 * atr):
                entry = round(close, digits)
                # Robust SL: placed behind candle high with minimum 0.75 ATR distance
                min_sl_dist = 0.75 * atr
                sl_structure = high_price + (0.2 * atr)
                sl = round(max(sl_structure, entry + min_sl_dist), digits)
                risk = sl - entry
                if risk <= 0:
                    return None

                # Target Previous Day Low (PDL) or expansion target
                pdl = liquidity.get("previous_day", {}).get("low")
                tp_candidate = float(pdl) if pdl and float(pdl) < entry - (risk * 1.5) else None

                if tp_candidate is not None:
                    tp = round(tp_candidate, digits)
                    target_name = "PREVIOUS_DAY_LOW"
                else:
                    tp = round(entry - (risk * min_rr), digits)
                    target_name = "EXPANSION_TARGET"

                rr = abs(entry - tp) / risk
                if rr >= min_rr:
                    logger.info(
                        "[%s] ASIAN RANGE BREAKDOWN DETECTED: SELL @ %.2f (SL: %.2f, TP: %.2f, R:R 1:%.2f, Level: ASIA_LOW)",
                        symbol, entry, sl, tp, rr
                    )
                    return AsianBreakdownSignal(
                        direction=Direction.SELL,
                        entry_price=entry,
                        sl_price=sl,
                        tp_price=tp,
                        level_name="ASIA_LOW",
                        level_price=asia_low,
                        target_name=target_name,
                        target_price=tp,
                        risk_reward=round(rr, 2),
                    )

        elif macro_direction == Direction.BUY:
            # Bullish Asian Breakout: price broke and closed above ASIA_HIGH
            if close > asia_high and (close - asia_high) <= (1.5 * atr):
                entry = round(close, digits)
                # Robust SL: placed behind candle low with minimum 0.75 ATR distance
                min_sl_dist = 0.75 * atr
                sl_structure = low_price - (0.2 * atr)
                sl = round(min(sl_structure, entry - min_sl_dist), digits)
                risk = entry - sl
                if risk <= 0:
                    return None

                # Target Previous Day High (PDH) or expansion target
                pdh = liquidity.get("previous_day", {}).get("high")
                tp_candidate = float(pdh) if pdh and float(pdh) > entry + (risk * 1.5) else None

                if tp_candidate is not None:
                    tp = round(tp_candidate, digits)
                    target_name = "PREVIOUS_DAY_HIGH"
                else:
                    tp = round(entry + (risk * min_rr), digits)
                    target_name = "EXPANSION_TARGET"

                rr = abs(tp - entry) / risk
                if rr >= min_rr:
                    logger.info(
                        "[%s] ASIAN RANGE BREAKOUT DETECTED: BUY @ %.2f (SL: %.2f, TP: %.2f, R:R 1:%.2f, Level: ASIA_HIGH)",
                        symbol, entry, sl, tp, rr
                    )
                    return AsianBreakdownSignal(
                        direction=Direction.BUY,
                        entry_price=entry,
                        sl_price=sl,
                        tp_price=tp,
                        level_name="ASIA_HIGH",
                        level_price=asia_high,
                        target_name=target_name,
                        target_price=tp,
                        risk_reward=round(rr, 2),
                    )

        return None

    def build_breakdown_signal(
        self,
        symbol: str,
        timeframe: str,
        breakdown_sig: AsianBreakdownSignal,
    ) -> Signal:
        """Create a full Signal object compatible with Quantum execution pipeline."""
        now = datetime.now(timezone.utc)
        struct_score = ComponentScore(
            name="structure",
            score=30.0,
            max_score=30.0,
            details={"reason": f"ASIAN_EXPANSION_{breakdown_sig.level_name}", "target": breakdown_sig.target_name},
        )
        trend_score = ComponentScore(
            name="trend",
            score=20.0,
            max_score=25.0,
            details={"aligned_macro": True, "type": "ASIAN_RANGE_BREAKDOWN"},
        )
        momentum_score = ComponentScore(
            name="momentum",
            score=12.0,
            max_score=15.0,
            details={"breakout_momentum": True},
        )
        volume_score = ComponentScore(
            name="volume",
            score=12.0,
            max_score=15.0,
            details={"expansion_volume": True},
        )
        volatility_score = ComponentScore(
            name="volatility",
            score=11.0,
            max_score=15.0,
            details={"rr": breakdown_sig.risk_reward},
        )

        total = struct_score.score + trend_score.score + momentum_score.score + volume_score.score + volatility_score.score  # 85.0
        score_res = ScoreResult(
            direction=breakdown_sig.direction,
            total_score=total,
            classification=SignalClassification.EXCELLENT,
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
            direction=breakdown_sig.direction,
            score_result=score_res,
            created_at=now,
            entry_price=breakdown_sig.entry_price,
        )
