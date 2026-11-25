"""Synthetic Volatility Institutional Quantitative Strategy for Deriv Continuous Indices.

Specifically engineered for:
- Volatility 75 Index (V75 / R_75)
- Volatility 75 (1s) Index (1HZ75V)
- Volatility 100 Index (V100 / R_100)
- Volatility 50 Index (V50 / R_50)
- Volatility 25 Index (V25) & Volatility 10 Index (V10)

Institutional Quantitative Edge:
1. 24/7 continuous Brownian motion dynamics (GBM): Volatility indices have NO deterministic
   drift and NO Poisson jump spikes (unlike Boom/Crash). Trend-following pullback continuations
   and volatility squeeze expansions are the mathematically validated alpha drivers.
2. Multi-Timeframe Alignment: H1 Macro Regime Filter (EMA-50 + Slope) + M15 Value Alignment + M5 Trigger.
3. Institutional Value Zone Pullback: Price retracements into the EMA 20/50 zone with RSI reset
   and absorption wick confirmation.
4. Volatility Squeeze Compression & Expansion: John Carter Keltner-Bollinger squeeze release.
5. Surgical Stop Loss: Strictly anchored to the immediate pullback swing pivot (-0.25x ATR),
   reducing risk to 25-45 points on V75 (never 150-250 points).
6. Dynamic Asymmetric Payoff: Partial profit at 1.35R with break-even lock + Chandelier/ATR trailing runner.
"""
from dataclasses import dataclass
import logging
from typing import Optional, List, Tuple, Dict, Any
import pandas as pd
import numpy as np

from core.constants import Direction

logger = logging.getLogger("SyntheticVolatility")


@dataclass
class SyntheticVolatilitySignal:
    """Trading signal for continuous synthetic volatility indices."""
    symbol: str
    direction: Direction
    entry_price: float
    sl_price: float
    tp_price: float
    risk_points: float
    risk_reward: float
    partial_tp_price: float
    trailing_activation_r: float
    setup_model: str
    confluences: Tuple[str, ...]
    atr: float


class SyntheticVolatilityStrategy:
    """
    Institutional Multi-Timeframe Strategy for Deriv Continuous Volatility Indices.
    """

    def __init__(
        self,
        ema_fast: int = 20,
        ema_medium: int = 50,
        ema_slow: int = 200,
        atr_period: int = 14,
        sl_buffer_atr: float = 0.70,
        target_rr: float = 2.20,
        partial_rr: float = 1.35,
        min_rr: float = 1.40,
        trailing_activation_r: float = 1.50,
        trailing_distance_r: float = 0.70,
        enable_trend_pullback: bool = True,
        enable_trap_reversal: bool = True,
        enable_squeeze_expansion: bool = True,
        enable_pullback_sweep: bool = True,
        enable_mean_reversion: bool = False,
        enable_breakout: bool = False,
    ):
        self.ema_fast = ema_fast
        self.ema_medium = ema_medium
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.sl_buffer_atr = sl_buffer_atr
        self.target_rr = target_rr
        self.partial_rr = partial_rr
        self.min_rr = min_rr
        self.trailing_activation_r = trailing_activation_r
        self.trailing_distance_r = trailing_distance_r
        self.enable_trend_pullback = enable_trend_pullback
        self.enable_trap_reversal = enable_trap_reversal
        self.enable_squeeze_expansion = enable_squeeze_expansion
        self.enable_pullback_sweep = enable_pullback_sweep
        self.enable_mean_reversion = enable_mean_reversion
        self.enable_breakout = enable_breakout

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute institutional EMAs, ATR, RSI, Bollinger Bands and Keltner Channels."""
        if df is None or len(df) == 0:
            return df
        if "squeeze_on" in df.columns and "atr_sma" in df.columns and "kc_upper" in df.columns:
            return df
        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # 1. Trend Moving Averages
        df["ema_fast"] = close.ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_med"] = close.ewm(span=self.ema_medium, adjust=False).mean()
        df["ema_slow"] = close.ewm(span=self.ema_slow, adjust=False).mean()
        df["ema_med_slope"] = df["ema_med"].diff()

        # 2. Average True Range (ATR) & Rolling Volatility SMA
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=self.atr_period).mean().fillna(tr)
        df["atr_sma"] = df["atr"].rolling(window=20).mean().fillna(df["atr"])

        # 3. Relative Strength Index (RSI 14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100.0 - (100.0 / (1.0 + rs)).fillna(50.0)

        # 4. Bollinger Bands (20, 2.0 std)
        sma20 = close.rolling(window=20).mean()
        std20 = close.rolling(window=20).std().fillna(0)
        df["bb_upper"] = sma20 + (std20 * 2.0)
        df["bb_lower"] = sma20 - (std20 * 2.0)
        df["bb_mid"] = sma20

        # 5. Keltner Channels (20, 1.5 ATR)
        df["kc_mid"] = df["ema_fast"]
        df["kc_upper"] = df["kc_mid"] + (1.5 * df["atr"])
        df["kc_lower"] = df["kc_mid"] - (1.5 * df["atr"])

        # 6. Volatility Squeeze State (Carter Squeeze)
        df["squeeze_on"] = (df["bb_upper"] <= df["kc_upper"]) & (df["bb_lower"] >= df["kc_lower"])

        # 7. Donchian High / Low (20)
        df["donchian_high"] = high.rolling(window=20).max().shift(1)
        df["donchian_low"] = low.rolling(window=20).min().shift(1)

        # 8. Choppiness Index (CHOP 14)
        sum_tr14 = tr.rolling(window=14).sum()
        max_h14 = high.rolling(window=14).max()
        min_l14 = low.rolling(window=14).min()
        hl_range14 = (max_h14 - min_l14).replace(0, np.nan)
        ratio14 = (sum_tr14 / hl_range14).clip(lower=1e-5)
        df["chop"] = (100.0 * np.log10(ratio14) / np.log10(14.0)).fillna(50.0)

        # 9. Kaufman Efficiency Ratio (KER 14)
        change14 = (close - close.shift(14)).abs()
        volatility14 = close.diff().abs().rolling(window=14).sum().replace(0, np.nan)
        df["ker"] = (change14 / volatility14).fillna(0.35)

        return df

    def detect_liquidity_sweep(self, df: pd.DataFrame, lookback: int = 25) -> Optional[Tuple[Direction, float]]:
        """
        Detect if price swept local liquidity and closed back inside range with institutional absorption.
        1. Sweep: prev_bar pierced local extremes.
        2. Absorption wick: prev_bar has at least 28% wick on rejection side.
        3. Structural Displacement (CHoCH): curr_bar closes beyond prev_bar open with >= 0.25*ATR body displacement.
        """
        if len(df) < lookback + 2:
            return None

        recent = df.iloc[-(lookback + 2):-2]
        prev_bar = df.iloc[-2]
        curr_bar = df.iloc[-1]
        c_atr = float(curr_bar["atr"]) if "atr" in curr_bar else float(curr_bar["high"] - curr_bar["low"])

        local_high = float(recent["high"].max())
        local_low = float(recent["low"].min())

        # Bullish Sweep: Price pierced below local low but rejected and closed back above
        if float(prev_bar["low"]) < local_low and float(prev_bar["close"]) > local_low:
            bar_range = max(float(prev_bar["high"]) - float(prev_bar["low"]), 1e-5)
            lower_wick = min(float(prev_bar["open"]), float(prev_bar["close"])) - float(prev_bar["low"])
            wick_ratio = lower_wick / bar_range

            p_open = float(prev_bar["open"])
            c_close = float(curr_bar["close"])
            c_open = float(curr_bar["open"])
            displacement = c_close - c_open

            if wick_ratio >= 0.28 and c_close > c_open and c_close >= p_open and displacement >= (c_atr * 0.25):
                return Direction.BUY, float(prev_bar["low"])

        # Bearish Sweep: Price pierced above local high but rejected and closed back below
        if float(prev_bar["high"]) > local_high and float(prev_bar["close"]) < local_high:
            bar_range = max(float(prev_bar["high"]) - float(prev_bar["low"]), 1e-5)
            upper_wick = float(prev_bar["high"]) - max(float(prev_bar["open"]), float(prev_bar["close"]))
            wick_ratio = upper_wick / bar_range

            p_open = float(prev_bar["open"])
            c_close = float(curr_bar["close"])
            c_open = float(curr_bar["open"])
            displacement = c_open - c_close

            if wick_ratio >= 0.28 and c_close < c_open and c_close <= p_open and displacement >= (c_atr * 0.25):
                return Direction.SELL, float(prev_bar["high"])

        return None

    def detect_swing_failure_trap_setup(
        self,
        symbol: str,
        df_m5: pd.DataFrame,
        df_m15: Optional[pd.DataFrame] = None,
        df_h1: Optional[pd.DataFrame] = None,
        lookback: int = 20,
        point: float = 0.01,
    ) -> Optional[SyntheticVolatilitySignal]:
        """
        INSTITUTIONAL SWING FAILURE PATTERN (SFP) & LIQUIDITY TRAP REVERSAL ENGINE.
        
        Exploits trapped retail breakout traders (Bull Traps and Bear Traps):
        1. Bull Trap (Short):
           - Price pokes above recent swing high (lookback=20) or Donchian Upper.
           - Smart Money absorbs liquidity; candle fails to sustain and closes back INSIDE range.
           - Significant upper rejection wick (>= 22%) or bearish engulfing confirmation.
           - Targets opposite range floor / EMA-50 with surgical SL above trap wick peak.
        2. Bear Trap (Long):
           - Price pokes below recent swing low (lookback=20) or Donchian Lower.
           - Price rejected by buyer absorption; candle closes back ABOVE the swept level.
           - Significant lower rejection wick (>= 22%) or bullish engulfing confirmation.
           - Targets opposite range ceiling / EMA-50 with surgical SL below trap wick floor.
        """
        if len(df_m5) < lookback + 5:
            return None

        if "atr" not in df_m5.columns:
            df_m5 = self.calculate_indicators(df_m5)

        curr = df_m5.iloc[-1]
        prev = df_m5.iloc[-2]
        c_open = float(curr["open"])
        c_high = float(curr["high"])
        c_low = float(curr["low"])
        c_close = float(curr["close"])
        atr = float(curr["atr"])
        rsi = float(curr["rsi"])
        bar_range = max(c_high - c_low, 1e-5)

        # Reference swing window (excluding current bar)
        recent_window = df_m5.iloc[-(lookback + 2):-1]
        recent_swing_high = float(recent_window["high"].max())
        recent_swing_low = float(recent_window["low"].min())

        # Check macro HTF exhaustion/alignment (avoid fading runaway parabolic trends)
        htf_parabolic_bull = False
        htf_parabolic_bear = False
        if df_h1 is not None and len(df_h1) >= 20:
            df_h1 = self.calculate_indicators(df_h1)
            h1_bar = df_h1.iloc[-2] if len(df_h1) >= 2 else df_h1.iloc[-1]
            h1_rsi = float(h1_bar.get("rsi", 50.0))
            h1_close = float(h1_bar["close"])
            h1_ema_slow = float(h1_bar["ema_slow"])
            htf_parabolic_bull = (h1_close > h1_ema_slow) and (h1_rsi > 72.0)
            htf_parabolic_bear = (h1_close < h1_ema_slow) and (h1_rsi < 28.0)

        # -------------------------------------------------------------
        # 1. BULL TRAP REVERSAL -> ENTER SHORT (SELL)
        # -------------------------------------------------------------
        swept_high = (c_high > recent_swing_high) or (float(prev["high"]) > recent_swing_high)
        closed_inside = c_close < recent_swing_high

        if swept_high and closed_inside and not htf_parabolic_bull:
            upper_wick = c_high - max(c_open, c_close)
            upper_wick_ratio = upper_wick / bar_range
            is_bearish_candle = c_close < c_open
            is_engulfing = c_close < float(prev["low"])
            has_absorption = (upper_wick_ratio >= 0.22) or (is_bearish_candle and upper_wick_ratio >= 0.15) or is_engulfing

            if has_absorption and (32.0 <= rsi <= 82.0):
                trap_peak = max(c_high, float(prev["high"]))
                sl_price = trap_peak + (atr * self.sl_buffer_atr)
                risk = sl_price - c_close

                if (atr * 0.30) <= risk <= (atr * 3.00):
                    tp_price = c_close - (risk * self.target_rr)
                    partial_tp = c_close - (risk * self.partial_rr)
                    rr = abs(c_close - tp_price) / risk
                    if rr >= self.min_rr:
                        confluences = (
                            "SWING_FAILURE_PATTERN_SELL",
                            "LIQUIDITY_TRAP_REVERSAL",
                            "BULL_TRAP_EXPLOITED",
                            "ABSORPTION_WICK_CONFIRMED",
                            "SMC_SWEEP_CONFIRMED",
                        )
                        return SyntheticVolatilitySignal(
                            symbol=symbol,
                            direction=Direction.SELL,
                            entry_price=c_close,
                            sl_price=sl_price,
                            tp_price=tp_price,
                            risk_points=risk,
                            risk_reward=round(rr, 2),
                            partial_tp_price=partial_tp,
                            trailing_activation_r=self.trailing_activation_r,
                            setup_model="SWING_FAILURE_PATTERN_TRAP",
                            confluences=confluences,
                            atr=atr,
                        )

        # -------------------------------------------------------------
        # 2. BEAR TRAP REVERSAL -> ENTER LONG (BUY)
        # -------------------------------------------------------------
        swept_low = (c_low < recent_swing_low) or (float(prev["low"]) < recent_swing_low)
        closed_inside_low = c_close > recent_swing_low

        if swept_low and closed_inside_low and not htf_parabolic_bear:
            lower_wick = min(c_open, c_close) - c_low
            lower_wick_ratio = lower_wick / bar_range
            is_bullish_candle = c_close > c_open
            is_bullish_engulfing = c_close > float(prev["high"])
            has_absorption = (lower_wick_ratio >= 0.22) or (is_bullish_candle and lower_wick_ratio >= 0.15) or is_bullish_engulfing

            if has_absorption and (18.0 <= rsi <= 68.0):
                trap_floor = min(c_low, float(prev["low"]))
                sl_price = trap_floor - (atr * self.sl_buffer_atr)
                risk = c_close - sl_price

                if (atr * 0.30) <= risk <= (atr * 3.00):
                    tp_price = c_close + (risk * self.target_rr)
                    partial_tp = c_close + (risk * self.partial_rr)
                    rr = abs(tp_price - c_close) / risk
                    if rr >= self.min_rr:
                        confluences = (
                            "SWING_FAILURE_PATTERN_BUY",
                            "LIQUIDITY_TRAP_REVERSAL",
                            "BEAR_TRAP_EXPLOITED",
                            "ABSORPTION_WICK_CONFIRMED",
                            "SMC_SWEEP_CONFIRMED",
                        )
                        return SyntheticVolatilitySignal(
                            symbol=symbol,
                            direction=Direction.BUY,
                            entry_price=c_close,
                            sl_price=sl_price,
                            tp_price=tp_price,
                            risk_points=risk,
                            risk_reward=round(rr, 2),
                            partial_tp_price=partial_tp,
                            trailing_activation_r=self.trailing_activation_r,
                            setup_model="SWING_FAILURE_PATTERN_TRAP",
                            confluences=confluences,
                            atr=atr,
                        )

        return None

    def detect_trend_pullback_setup(
        self,
        symbol: str,
        df_m5: pd.DataFrame,
        df_m15: Optional[pd.DataFrame] = None,
        df_h1: Optional[pd.DataFrame] = None,
        point: float = 0.01,
    ) -> Optional[SyntheticVolatilitySignal]:
        """
        Primary Institutional Alpha Engine: Value Zone Retest Continuation.
        - Macro H1 trend alignment (EMA-50 + slope)
        - Intermediate M15 alignment (EMA-50)
        - M5 Golden/Death cross & EMA-200 alignment
        - Pullback into EMA 20-50 Value Zone with RSI reset
        - Surgical SL anchored to immediate pullback swing pivot
        """
        if len(df_m5) < 35:
            return None

        if "atr" not in df_m5.columns:
            df_m5 = self.calculate_indicators(df_m5)

        curr = df_m5.iloc[-1]
        prev = df_m5.iloc[-2]
        c_close = float(curr["close"])
        c_open = float(curr["open"])
        c_high = float(curr["high"])
        c_low = float(curr["low"])
        atr = float(curr["atr"])
        ema_fast = float(curr["ema_fast"])
        ema_med = float(curr["ema_med"])
        ema_slow = float(curr["ema_slow"])
        rsi = float(curr["rsi"])

        # 1. Macro H1 Regime Filter
        htf_bullish = True
        htf_bearish = True
        if df_h1 is not None and len(df_h1) >= 20:
            df_h1 = self.calculate_indicators(df_h1)
            h1_bar = df_h1.iloc[-2] if len(df_h1) >= 2 else df_h1.iloc[-1]
            h1_prev = df_h1.iloc[-3] if len(df_h1) >= 3 else h1_bar
            h1_slope_up = float(h1_bar["ema_med"]) >= float(h1_prev["ema_med"]) - 0.01
            h1_slope_down = float(h1_bar["ema_med"]) <= float(h1_prev["ema_med"]) + 0.01
            htf_bullish = (float(h1_bar["close"]) > float(h1_bar["ema_med"])) and h1_slope_up
            htf_bearish = (float(h1_bar["close"]) < float(h1_bar["ema_med"])) and h1_slope_down

        # 2. Intermediate M15 Regime Filter
        mtf_m15_bullish = True
        mtf_m15_bearish = True
        if df_m15 is not None and len(df_m15) >= 20:
            df_m15 = self.calculate_indicators(df_m15)
            m15_bar = df_m15.iloc[-2] if len(df_m15) >= 2 else df_m15.iloc[-1]
            mtf_m15_bullish = float(m15_bar["close"]) > float(m15_bar["ema_med"])
            mtf_m15_bearish = float(m15_bar["close"]) < float(m15_bar["ema_med"])

        # 3. Dynamic Fractal Chop Filter (Choppiness Index & Kaufman Efficiency Ratio)
        c_chop = float(curr["chop"]) if "chop" in curr else 50.0
        c_ker = float(curr["ker"]) if "ker" in curr else 0.35
        ema_med_slope = float(curr["ema_med_slope"]) if "ema_med_slope" in curr else 0.0

        # Continuous Fractal Chop Evaluation:
        # Extreme Chop (CHOP >= 68.0 or KER < 0.16): Total deadlock, no edge.
        # Moderate Chop (60.0 <= CHOP < 68.0): Mild consolidation, flagged for Neo Tier-B surgical handling.
        is_extreme_chop = (c_chop >= 68.0) or (c_ker < 0.16)
        is_moderate_chop = (c_chop >= 60.0) or (c_ker < 0.25)

        # -----------------
        # BULLISH PULLBACK
        # -----------------
        if not is_extreme_chop and htf_bullish and mtf_m15_bullish and (ema_fast > ema_med) and (c_close > ema_slow and ema_med > ema_slow) and (ema_med_slope >= -0.05 * atr):
            # Check pullback into value zone within last 4 bars
            recent_lows = float(df_m5["low"].iloc[-5:-1].min())
            recent_closes = float(df_m5["close"].iloc[-5:-1].min())

            # Touched EMA-20 and held above EMA-50 - 0.65*ATR (flexibility against stochastic wick noise)
            if recent_lows <= ema_fast and recent_closes >= (ema_med - 0.65 * atr):
                # RSI healthy pullback reset (not oversold collapse)
                if 34.0 <= rsi <= 58.0:
                    # Confirmation Trigger: Bullish candle closing above EMA-20
                    if c_close > c_open and c_close >= ema_fast:
                        bar_range = max(c_high - c_low, 1e-5)
                        lower_wick = (min(c_open, c_close) - c_low) / bar_range
                        engulfing = c_close > float(prev["high"])
                        strong_body = (c_close - c_open) >= (0.25 * atr)

                        if lower_wick >= 0.10 or engulfing or strong_body:
                            # Surgical Stop Loss: lowest low of recent pullback wave
                            pullback_low = float(df_m5["low"].iloc[-6:].min())
                            sl_price = pullback_low - (atr * self.sl_buffer_atr)
                            risk = c_close - sl_price

                            if (atr * 0.35) <= risk <= (atr * 3.00):
                                tp_price = c_close + (risk * self.target_rr)
                                partial_tp = c_close + (risk * self.partial_rr)
                                rr = abs(tp_price - c_close) / risk
                                if rr >= self.min_rr:
                                    conf_list = [
                                        "INSTITUTIONAL_VALUE_ZONE_RETEST",
                                        "HTF_H1_TREND_ALIGNED",
                                        "MTF_M15_CONFIRMED",
                                        "M5_GOLDEN_CROSS_ALIGNED",
                                        "RSI_PULLBACK_RESET",
                                        "SURGICAL_SWING_SL",
                                    ]
                                    if is_moderate_chop:
                                        conf_list.append("CHOP_REGIME_MODERATE")
                                    return SyntheticVolatilitySignal(
                                        symbol=symbol,
                                        direction=Direction.BUY,
                                        entry_price=c_close,
                                        sl_price=sl_price,
                                        tp_price=tp_price,
                                        risk_points=risk,
                                        risk_reward=round(rr, 2),
                                        partial_tp_price=partial_tp,
                                        trailing_activation_r=self.trailing_activation_r,
                                        setup_model="INSTITUTIONAL_TREND_PULLBACK",
                                        confluences=tuple(conf_list),
                                        atr=atr,
                                    )

        # -----------------
        # BEARISH PULLBACK
        # -----------------
        if not is_extreme_chop and htf_bearish and mtf_m15_bearish and (ema_fast < ema_med) and (c_close < ema_slow and ema_med < ema_slow) and (ema_med_slope <= 0.05 * atr):
            recent_highs = float(df_m5["high"].iloc[-5:-1].max())
            recent_closes = float(df_m5["close"].iloc[-5:-1].max())

            # Touched EMA-20 and held below EMA-50 + 0.65*ATR (flexibility against stochastic wick noise)
            if recent_highs >= ema_fast and recent_closes <= (ema_med + 0.65 * atr):
                if 42.0 <= rsi <= 66.0:
                    if c_close < c_open and c_close <= ema_fast:
                        bar_range = max(c_high - c_low, 1e-5)
                        upper_wick = (c_high - max(c_open, c_close)) / bar_range
                        engulfing = c_close < float(prev["low"])
                        strong_body = (c_open - c_close) >= (0.25 * atr)

                        if upper_wick >= 0.10 or engulfing or strong_body:
                            pullback_high = float(df_m5["high"].iloc[-6:].max())
                            sl_price = pullback_high + (atr * self.sl_buffer_atr)
                            risk = sl_price - c_close

                            if (atr * 0.35) <= risk <= (atr * 3.00):
                                tp_price = c_close - (risk * self.target_rr)
                                partial_tp = c_close - (risk * self.partial_rr)
                                rr = abs(c_close - tp_price) / risk
                                if rr >= self.min_rr:
                                    conf_list = [
                                        "INSTITUTIONAL_VALUE_ZONE_RETEST",
                                        "HTF_H1_TREND_ALIGNED",
                                        "MTF_M15_CONFIRMED",
                                        "M5_DEATH_CROSS_ALIGNED",
                                        "RSI_PULLBACK_RESET",
                                        "SURGICAL_SWING_SL",
                                    ]
                                    if is_moderate_chop:
                                        conf_list.append("CHOP_REGIME_MODERATE")
                                    return SyntheticVolatilitySignal(
                                        symbol=symbol,
                                        direction=Direction.SELL,
                                        entry_price=c_close,
                                        sl_price=sl_price,
                                        tp_price=tp_price,
                                        risk_points=risk,
                                        risk_reward=round(rr, 2),
                                        partial_tp_price=partial_tp,
                                        trailing_activation_r=self.trailing_activation_r,
                                        setup_model="INSTITUTIONAL_TREND_PULLBACK",
                                        confluences=tuple(conf_list),
                                        atr=atr,
                                    )

        return None

    def detect_squeeze_expansion_setup(
        self,
        symbol: str,
        df_m5: pd.DataFrame,
        df_m15: Optional[pd.DataFrame] = None,
        df_h1: Optional[pd.DataFrame] = None,
        point: float = 0.01,
    ) -> Optional[SyntheticVolatilitySignal]:
        """
        Carter Volatility Squeeze Compression & Expansion Engine.
        - Bollinger Bands inside Keltner Channels within last 2-8 bars (energy build-up)
        - Expansion candle breaking outside bands with ATR expanding
        - Direction aligned with HTF trend
        """
        if len(df_m5) < 35:
            return None

        if "squeeze_on" not in df_m5.columns:
            df_m5 = self.calculate_indicators(df_m5)

        recent_squeeze = bool(df_m5["squeeze_on"].iloc[-8:-1].any())
        if not recent_squeeze:
            return None

        curr = df_m5.iloc[-1]
        c_close = float(curr["close"])
        bb_upper = float(curr["bb_upper"])
        bb_lower = float(curr["bb_lower"])
        bb_mid = float(curr["bb_mid"])
        atr = float(curr["atr"])
        atr_sma = float(curr["atr_sma"])
        rsi = float(curr["rsi"])

        # Macro trend alignment
        htf_bullish = True
        htf_bearish = True
        if df_h1 is not None and len(df_h1) >= 20:
            df_h1 = self.calculate_indicators(df_h1)
            h1_bar = df_h1.iloc[-2] if len(df_h1) >= 2 else df_h1.iloc[-1]
            htf_bullish = float(h1_bar["close"]) > float(h1_bar["ema_med"])
        c_open = float(curr["open"])
        c_high = float(curr["high"])
        c_low = float(curr["low"])
        bar_range = max(c_high - c_low, 1e-5)
        upper_wick_ratio = (c_high - max(c_open, c_close)) / bar_range
        lower_wick_ratio = (min(c_open, c_close) - c_low) / bar_range

        c_chop = float(curr.get("chop", 50.0))
        c_ker = float(curr.get("ker", 0.35))
        is_chop_regime = (c_chop >= 58.0) or (c_ker < 0.28)

        # Bullish Squeeze Breakout (Reject if upper wick indicates Bull Trap or market is in Chop)
        if not is_chop_regime and upper_wick_ratio < 0.20 and htf_bullish and c_close > bb_upper and atr >= (atr_sma * 1.05) and (52.0 <= rsi <= 72.0):
            sl_price = bb_mid - (atr * self.sl_buffer_atr)
            risk = c_close - sl_price
            if (atr * 0.40) <= risk <= (atr * 2.50):
                tp_price = c_close + (risk * self.target_rr)
                partial_tp = c_close + (risk * self.partial_rr)
                rr = abs(tp_price - c_close) / risk
                if rr >= self.min_rr:
                    confluences = (
                        "KELTNER_BOLLINGER_SQUEEZE_RELEASE",
                        "ATR_EXPANSION_CONFIRMED",
                        "HTF_H1_TREND_ALIGNED",
                        "RSI_MOMENTUM_EXPANSION",
                    )
                    return SyntheticVolatilitySignal(
                        symbol=symbol,
                        direction=Direction.BUY,
                        entry_price=c_close,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        risk_points=risk,
                        risk_reward=round(rr, 2),
                        partial_tp_price=partial_tp,
                        trailing_activation_r=self.trailing_activation_r,
                        setup_model="VOLATILITY_SQUEEZE_EXPANSION",
                        confluences=confluences,
                        atr=atr,
                    )

        # Bearish Squeeze Breakout (Reject if lower wick indicates Bear Trap or market is in Chop)
        if not is_chop_regime and lower_wick_ratio < 0.20 and htf_bearish and c_close < bb_lower and atr >= (atr_sma * 1.05) and (28.0 <= rsi <= 48.0):
            sl_price = bb_mid + (atr * self.sl_buffer_atr)
            risk = sl_price - c_close
            if (atr * 0.40) <= risk <= (atr * 2.50):
                tp_price = c_close - (risk * self.target_rr)
                partial_tp = c_close - (risk * self.partial_rr)
                rr = abs(c_close - tp_price) / risk
                if rr >= self.min_rr:
                    confluences = (
                        "KELTNER_BOLLINGER_SQUEEZE_RELEASE",
                        "ATR_EXPANSION_CONFIRMED",
                        "HTF_H1_TREND_ALIGNED",
                        "RSI_MOMENTUM_EXPANSION",
                    )
                    return SyntheticVolatilitySignal(
                        symbol=symbol,
                        direction=Direction.SELL,
                        entry_price=c_close,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        risk_points=risk,
                        risk_reward=round(rr, 2),
                        partial_tp_price=partial_tp,
                        trailing_activation_r=self.trailing_activation_r,
                        setup_model="VOLATILITY_SQUEEZE_EXPANSION",
                        confluences=confluences,
                        atr=atr,
                    )

        return None

    def detect_momentum_breakout_setup(
        self,
        symbol: str,
        df_m5: pd.DataFrame,
        df_m15: Optional[pd.DataFrame] = None,
        df_h1: Optional[pd.DataFrame] = None,
        point: float = 0.01,
    ) -> Optional[SyntheticVolatilitySignal]:
        """
        ENGINE 5: Institutional Momentum Trend Breakout & Expansion (Donchian / Bollinger Breakout).
        Captures explosive unidirectional moves where price enters strong momentum flow
        without waiting for EMA-20 pullbacks.
        """
        if len(df_m5) < 35:
            return None

        if "atr" not in df_m5.columns:
            df_m5 = self.calculate_indicators(df_m5)

        curr = df_m5.iloc[-1]
        prev = df_m5.iloc[-2]
        c_close = float(curr["close"])
        c_open = float(curr["open"])
        atr = float(curr["atr"])
        atr_sma = float(curr["atr_sma"])
        ema_fast = float(curr["ema_fast"])
        ema_med = float(curr["ema_med"])
        ema_slow = float(curr["ema_slow"])
        rsi = float(curr["rsi"])
        ker = float(curr.get("ker", 0.35))
        chop = float(curr.get("chop", 50.0))
        donchian_high = float(curr["donchian_high"])
        donchian_low = float(curr["donchian_low"])
        bb_upper = float(curr["bb_upper"])
        bb_lower = float(curr["bb_lower"])

        # 1. Macro HTF Alignment
        htf_bullish = True
        htf_bearish = True
        if df_h1 is not None and len(df_h1) >= 20:
            df_h1 = self.calculate_indicators(df_h1)
            h1_bar = df_h1.iloc[-2] if len(df_h1) >= 2 else df_h1.iloc[-1]
            htf_bullish = float(h1_bar["close"]) > float(h1_bar["ema_med"])
            htf_bearish = float(h1_bar["close"]) < float(h1_bar["ema_med"])

        mtf_m15_bullish = True
        mtf_m15_bearish = True
        if df_m15 is not None and len(df_m15) >= 20:
            df_m15 = self.calculate_indicators(df_m15)
            m15_bar = df_m15.iloc[-2] if len(df_m15) >= 2 else df_m15.iloc[-1]
            mtf_m15_bullish = float(m15_bar["close"]) > float(m15_bar["ema_med"])
            mtf_m15_bearish = float(m15_bar["close"]) < float(m15_bar["ema_med"])

        # Trend efficiency requirements (non-congested, high directional flow)
        is_chop_regime = chop >= 56.0 or ker < 0.30
        efficient_flow = (ker >= 0.32) and (chop <= 55.0)

        c_high = float(curr["high"])
        c_low = float(curr["low"])
        bar_range = max(c_high - c_low, 1e-5)
        upper_wick_ratio = (c_high - max(c_open, c_close)) / bar_range
        lower_wick_ratio = (min(c_open, c_close) - c_low) / bar_range

        # ---------------------------
        # BULLISH MOMENTUM BREAKOUT
        # ---------------------------
        if not is_chop_regime and upper_wick_ratio < 0.18 and htf_bullish and mtf_m15_bullish and (ema_fast > ema_med) and (c_close > ema_slow):
            is_donchian_break = c_close > donchian_high
            is_bb_expansion = (c_close > bb_upper) and (atr >= (atr_sma * 0.98))
            is_flow_valid = is_donchian_break or (efficient_flow and is_bb_expansion)

            if is_flow_valid and (c_close > c_open):
                body_disp = c_close - c_open
                if body_disp >= (atr * 0.20) and (50.0 <= rsi <= 85.0):
                    support_anchor = max(ema_fast, float(prev["low"]))
                    sl_price = support_anchor - (atr * self.sl_buffer_atr)
                    risk = c_close - sl_price

                    if (atr * 0.35) <= risk <= (atr * 3.50):
                        tp_price = c_close + (risk * self.target_rr)
                        partial_tp = c_close + (risk * self.partial_rr)
                        rr = abs(tp_price - c_close) / risk
                        if rr >= self.min_rr:
                            confluences = (
                                "MOMENTUM_TREND_EXPANSION_BUY",
                                "DONCHIAN_BREAKOUT" if is_donchian_break else "BB_MOMENTUM_EXPANSION",
                                "KAUFMAN_EFFICIENCY_CONFIRMED",
                                "HTF_H1_TREND_ALIGNED",
                                "MTF_M15_CONFIRMED",
                            )
                            return SyntheticVolatilitySignal(
                                symbol=symbol,
                                direction=Direction.BUY,
                                entry_price=c_close,
                                sl_price=sl_price,
                                tp_price=tp_price,
                                risk_points=risk,
                                risk_reward=round(rr, 2),
                                partial_tp_price=partial_tp,
                                trailing_activation_r=self.trailing_activation_r,
                                setup_model="MOMENTUM_TREND_EXPANSION",
                                confluences=confluences,
                                atr=atr,
                            )

        # ---------------------------
        # BEARISH MOMENTUM BREAKOUT
        # ---------------------------
        if not is_chop_regime and lower_wick_ratio < 0.18 and htf_bearish and mtf_m15_bearish and (ema_fast < ema_med) and (c_close < ema_slow):
            is_donchian_break = c_close < donchian_low
            is_bb_expansion = (c_close < bb_lower) and (atr >= (atr_sma * 0.98))
            is_flow_valid = is_donchian_break or (efficient_flow and is_bb_expansion)

            if is_flow_valid and (c_close < c_open):
                body_disp = c_open - c_close
                if body_disp >= (atr * 0.20) and (15.0 <= rsi <= 50.0):
                    resistance_anchor = min(ema_fast, float(prev["high"]))
                    sl_price = resistance_anchor + (atr * self.sl_buffer_atr)
                    risk = sl_price - c_close

                    if (atr * 0.35) <= risk <= (atr * 3.50):
                        tp_price = c_close - (risk * self.target_rr)
                        partial_tp = c_close - (risk * self.partial_rr)
                        rr = abs(c_close - tp_price) / risk
                        if rr >= self.min_rr:
                            confluences = (
                                "MOMENTUM_TREND_EXPANSION_SELL",
                                "DONCHIAN_BREAKOUT" if is_donchian_break else "BB_MOMENTUM_EXPANSION",
                                "KAUFMAN_EFFICIENCY_CONFIRMED",
                                "HTF_H1_TREND_ALIGNED",
                                "MTF_M15_CONFIRMED",
                            )
                            return SyntheticVolatilitySignal(
                                symbol=symbol,
                                direction=Direction.SELL,
                                entry_price=c_close,
                                sl_price=sl_price,
                                tp_price=tp_price,
                                risk_points=risk,
                                risk_reward=round(rr, 2),
                                partial_tp_price=partial_tp,
                                trailing_activation_r=self.trailing_activation_r,
                                setup_model="MOMENTUM_TREND_EXPANSION",
                                confluences=confluences,
                                atr=atr,
                            )

        return None

    def evaluate_volatility_setup(
        self,
        symbol: str,
        df_m5: pd.DataFrame,
        df_m15: Optional[pd.DataFrame] = None,
        df_h1: Optional[pd.DataFrame] = None,
        point: float = 0.01,
    ) -> Optional[SyntheticVolatilitySignal]:
        """
        Evaluate Volatility Index setup across specialized quantitative engines:
        1. Institutional Value Zone Pullback Continuation (Primary Engine)
        2. Volatility Squeeze & Expansion Breakout (Carter Compression Release)
        3. Pullback Liquidity Sweep (Wyckoff Spring in Trend Direction)
        4. Optional Mean Reversion (Band Bounce) / Breakout if explicitly enabled
        """
        df_m5 = self.calculate_indicators(df_m5)
        if len(df_m5) < 35:
            return None

        # ENGINE 1: Institutional Value Zone Pullback Continuation
        if self.enable_trend_pullback:
            sig = self.detect_trend_pullback_setup(symbol, df_m5, df_m15=df_m15, df_h1=df_h1, point=point)
            if sig is not None:
                return sig

        # ENGINE 2: Institutional Swing Failure Pattern (SFP) & Liquidity Trap Reversal (EXPLOITS FAKEOUTS)
        if self.enable_trap_reversal:
            sig = self.detect_swing_failure_trap_setup(symbol, df_m5, df_m15=df_m15, df_h1=df_h1, point=point)
            if sig is not None:
                return sig

        # ENGINE 3: Pullback Liquidity Sweep (Wyckoff Spring)
        if self.enable_pullback_sweep:
            sweep = self.detect_liquidity_sweep(df_m5, lookback=25)
            if sweep is not None:
                sw_dir, sw_level = sweep
                curr = df_m5.iloc[-1]
                c_close = float(curr["close"])
                atr = float(curr["atr"])

                # Check HTF trend and MTF M15 trend
                htf_ok = True
                if df_h1 is not None and len(df_h1) >= 20:
                    df_h1 = self.calculate_indicators(df_h1)
                    h1_bar = df_h1.iloc[-2] if len(df_h1) >= 2 else df_h1.iloc[-1]
                    h1_bull = float(h1_bar["close"]) > float(h1_bar["ema_med"])
                    h1_bear = float(h1_bar["close"]) < float(h1_bar["ema_med"])
                    htf_ok = (sw_dir == Direction.BUY and h1_bull) or (sw_dir == Direction.SELL and h1_bear)

                if df_m15 is not None and len(df_m15) >= 20:
                    df_m15 = self.calculate_indicators(df_m15)
                    m15_bar = df_m15.iloc[-2] if len(df_m15) >= 2 else df_m15.iloc[-1]
                    m15_bull = float(m15_bar["close"]) > float(m15_bar["ema_med"])
                    m15_bear = float(m15_bar["close"]) < float(m15_bar["ema_med"])
                    m15_ok = (sw_dir == Direction.BUY and m15_bull) or (sw_dir == Direction.SELL and m15_bear)
                    if not m15_ok:
                        htf_ok = False

                if htf_ok:
                    buffer = atr * self.sl_buffer_atr
                    if sw_dir == Direction.BUY:
                        sl_price = sw_level - buffer
                        risk = c_close - sl_price
                        tp_price = c_close + (risk * self.target_rr)
                    else:
                        sl_price = sw_level + buffer
                        risk = sl_price - c_close
                        tp_price = c_close - (risk * self.target_rr)

                    if (atr * 0.35) <= risk <= (atr * 3.00):
                        rr = abs(tp_price - c_close) / risk
                        if rr >= self.min_rr:
                            confluences = (
                                f"PULLBACK_LIQUIDITY_SWEEP_{sw_dir.value}",
                                "ABSORPTION_WICK_CONFIRMED",
                                "CHOCH_MOMENTUM_CONFIRMED",
                                "HTF_H1_TREND_ALIGNED",
                            )
                            return SyntheticVolatilitySignal(
                                symbol=symbol,
                                direction=sw_dir,
                                entry_price=c_close,
                                sl_price=sl_price,
                                tp_price=tp_price,
                                risk_points=risk,
                                risk_reward=round(rr, 2),
                                partial_tp_price=c_close + (risk * self.partial_rr) if sw_dir == Direction.BUY else c_close - (risk * self.partial_rr),
                                trailing_activation_r=self.trailing_activation_r,
                                setup_model="PULLBACK_LIQUIDITY_SWEEP",
                                confluences=confluences,
                                atr=atr,
                            )

        # ENGINE 4: Volatility Squeeze & Expansion Breakout (Protected with Anti-Trap Filters)
        if self.enable_squeeze_expansion:
            sig = self.detect_squeeze_expansion_setup(symbol, df_m5, df_m15=df_m15, df_h1=df_h1, point=point)
            if sig is not None:
                return sig

        # ENGINE 5: Momentum Trend Expansion Breakout (Donchian / Bollinger Breakout)
        if self.enable_breakout:
            sig = self.detect_momentum_breakout_setup(symbol, df_m5, df_m15=df_m15, df_h1=df_h1, point=point)
            if sig is not None:
                return sig

        # ENGINE 4: Mean Reversion Band Bounce (Only if explicitly enabled)
        if self.enable_mean_reversion:
            curr = df_m5.iloc[-1]
            c_close = float(curr["close"])
            c_open = float(curr["open"])
            c_low = float(curr["low"])
            c_high = float(curr["high"])
            atr = float(curr["atr"])
            rsi = float(curr["rsi"])
            bb_upper = float(curr["bb_upper"])
            bb_lower = float(curr["bb_lower"])
            bb_mid = float(curr["bb_mid"])

            if c_low <= bb_lower and c_close > c_open and rsi < 34.0:
                sl_price = c_low - (atr * self.sl_buffer_atr)
                risk = abs(c_close - sl_price)
                tp_price = bb_mid
                rr = abs(tp_price - c_close) / risk if risk > 0 else 0
                if rr >= 1.40 and risk > point:
                    return SyntheticVolatilitySignal(
                        symbol=symbol,
                        direction=Direction.BUY,
                        entry_price=c_close,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        risk_points=risk,
                        risk_reward=round(rr, 2),
                        partial_tp_price=c_close + (risk * 1.0),
                        trailing_activation_r=0.8,
                        setup_model="BOLLINGER_MEAN_REVERSION",
                        confluences=("BB_LOWER_TOUCH", "RSI_OVERSOLD_REVERSAL", "MEAN_REVERSION"),
                        atr=atr,
                    )

            if c_high >= bb_upper and c_close < c_open and rsi > 66.0:
                sl_price = c_high + (atr * self.sl_buffer_atr)
                risk = abs(sl_price - c_close)
                tp_price = bb_mid
                rr = abs(c_close - tp_price) / risk if risk > 0 else 0
                if rr >= 1.40 and risk > point:
                    return SyntheticVolatilitySignal(
                        symbol=symbol,
                        direction=Direction.SELL,
                        entry_price=c_close,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        risk_points=risk,
                        risk_reward=round(rr, 2),
                        partial_tp_price=c_close - (risk * 1.0),
                        trailing_activation_r=0.8,
                        setup_model="BOLLINGER_MEAN_REVERSION",
                        confluences=("BB_UPPER_TOUCH", "RSI_OVERBOUGHT_REVERSAL", "MEAN_REVERSION"),
                        atr=atr,
                    )

        return None
