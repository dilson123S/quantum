"""Jump-Diffusion Strategy for Deriv Boom and Crash Synthetic Indices."""
from dataclasses import dataclass
import logging
from typing import Optional, List, Tuple, Dict, Any
import numpy as np
import pandas as pd

from core.constants import Direction

logger = logging.getLogger("SpikeSniper")


@dataclass
class SpikeSignal:
    """Trading signal for Boom or Crash spike hunting."""
    symbol: str
    direction: Direction
    entry_price: float
    sl_price: float
    tp_price: float
    risk_points: float
    target_rr: float
    max_holding_bars: int
    setup_name: str
    confluences: Tuple[str, ...]
    order_block_price: float
    atr: float


class SpikeSniperStrategy:
    """
    Jump-Diffusion Strategy for Boom & Crash Indices.
    """

    def __init__(
        self,
        max_holding_bars: int = 16,
        min_rr: float = 1.3,
        target_rr: float = 1.8,
        sl_buffer_atr: float = 1.5,
        crash_rsi_overbought: float = 52.0,
        boom_rsi_oversold: float = 48.0,
        touch_atr: float = 1.00,
        close_near_atr: float = 1.35,
        min_wick_ratio: float = 0.15,
    ):
        self.max_holding_bars = max_holding_bars
        self.min_rr = min_rr
        self.target_rr = target_rr
        self.sl_buffer_atr = sl_buffer_atr
        self.crash_rsi_overbought = crash_rsi_overbought
        self.boom_rsi_oversold = boom_rsi_oversold
        self.touch_atr = touch_atr
        self.close_near_atr = close_near_atr
        self.min_wick_ratio = min_wick_ratio

    @staticmethod
    def is_crash_symbol(symbol: str) -> bool:
        s = symbol.upper()
        return "CRASH" in s or "C1000" in s or "C500" in s or "C300" in s

    @staticmethod
    def is_boom_symbol(symbol: str) -> bool:
        s = symbol.upper()
        return "BOOM" in s or "B1000" in s or "B500" in s or "B300" in s

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute EMAs, ATR, RSI and identify historical spikes."""
        if "atr" in df.columns and "is_crash_spike" in df.columns and "rsi" in df.columns:
            return df

        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        open_ = df["open"]

        # ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=14).mean().fillna(tr)

        # Baseline body size to detect spikes
        body = (close - open_).abs()
        med_body = body.rolling(window=20).median().fillna(1.0)
        
        # Scale-adaptive spike threshold
        spike_thresh = np.maximum(med_body * 3.0, df["atr"] * 1.5)
        df["is_crash_spike"] = (open_ - close) >= spike_thresh
        df["is_boom_spike"] = (close - open_) >= spike_thresh

        # EMAs
        df["ema_20"] = close.ewm(span=20, adjust=False).mean()
        df["ema_50"] = close.ewm(span=50, adjust=False).mean()

        # 14-period RSI for tick drift exhaustion
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.rolling(window=14, min_periods=14).mean()
        avg_loss = loss.rolling(window=14, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

        return df

    def detect_spike_setup(
        self,
        symbol: str,
        df: pd.DataFrame,
        df_higher: Optional[pd.DataFrame] = None,
        point: float = 0.001,
        df_h1: Optional[pd.DataFrame] = None
    ) -> Optional[SpikeSignal]:
        """
        Scan market data for high-probability Boom / Crash spike entry.
        """
        is_crash = self.is_crash_symbol(symbol)
        is_boom = self.is_boom_symbol(symbol)

        if not is_crash and not is_boom:
            return None

        df = self.calculate_indicators(df)
        if len(df) < 30:
            return None

        curr = df.iloc[-1]
        c_close = float(curr["close"])
        c_high = float(curr["high"])
        c_low = float(curr["low"])
        c_atr = float(curr["atr"])
        c_rsi = float(curr["rsi"])
        ema_50 = float(curr["ema_50"])

        confluences = []
        
        # ==========================================
        # H1 REGIME FILTER (Strict Anti-Falling-Knife Filter)
        # ==========================================
        if df_h1 is not None and not df_h1.empty:
            if "ema_50" not in df_h1.columns:
                df_h1 = df_h1.copy()
                df_h1["ema_50"] = df_h1["close"].ewm(span=50, adjust=False).mean()
            h1_bar = df_h1.iloc[-2] if len(df_h1) >= 2 else df_h1.iloc[-1]
            h1_open = float(h1_bar["open"])
            h1_close = float(h1_bar["close"])
            h1_ema50 = float(h1_bar["ema_50"])

            if is_boom:
                # Do NOT buy if H1 is below EMA-50 or crashing down (falling knife)
                if h1_close < h1_ema50:
                    return None
                if h1_close < h1_open and (h1_open - h1_close) > (c_atr * 1.2):
                    return None  # Severe macro bearish expansion
                confluences.append("H1_REGIME_BULLISH")
            elif is_crash:
                # Do NOT sell if H1 is above EMA-50 or pumping up (rocket spike)
                if h1_close > h1_ema50:
                    return None
                if h1_close > h1_open and (h1_close - h1_open) > (c_atr * 1.2):
                    return None  # Severe macro bullish expansion
                confluences.append("H1_REGIME_BEARISH")

        # ==========================================
        # M15 REGIME CONFIRMATION (Trend Alignment)
        # ==========================================
        if df_higher is not None and not df_higher.empty and len(df_higher) >= 20:
            if "ema_50" not in df_higher.columns:
                df_m15 = df_higher.copy()
                df_m15["ema_50"] = df_m15["close"].ewm(span=50, adjust=False).mean()
            else:
                df_m15 = df_higher
            m15_bar = df_m15.iloc[-2] if len(df_m15) >= 2 else df_m15.iloc[-1]
            m15_close = float(m15_bar["close"])
            m15_ema50 = float(m15_bar["ema_50"])

            # Strict M15 Trend Filter: Do NOT bypass on clusters
            if is_boom and m15_close < m15_ema50:
                return None  # Bearish drift in M15
            elif is_crash and m15_close > m15_ema50:
                return None  # Bullish drift in M15
            confluences.append("M15_REGIME_ALIGNED")

        # Previous completed bar inspection for absorption / rejection wicks
        prev_bar = df.iloc[-2]
        p_open = float(prev_bar["open"])
        p_close = float(prev_bar["close"])
        p_high = float(prev_bar["high"])
        p_low = float(prev_bar["low"])
        p_range = max(p_high - p_low, point * 5.0)
        p_lower_wick_ratio = (min(p_open, p_close) - p_low) / p_range
        p_upper_wick_ratio = (p_high - max(p_open, p_close)) / p_range

        # Current forming bar metrics
        c_open = float(curr["open"])
        c_range = max(c_high - c_low, point * 5.0)
        c_lower_wick_ratio = (min(c_open, c_close) - c_low) / c_range
        c_upper_wick_ratio = (c_high - max(c_open, c_close)) / c_range

        # ==========================================
        # CRASH 1000 / 500 / 300 SETUP (ONLY SELL)
        # ==========================================
        if is_crash:
            direction = Direction.SELL
            confluences.append("CRASH_RULE_ONLY_SELL")

            # Upward tick drift exhaustion is MANDATORY for cold supply tests
            if c_rsi < self.crash_rsi_overbought:
                return None
            # Trend alignment: cannot sell if price is above M5 EMA-50
            if c_close > ema_50:
                return None
            confluences.append("M5_RSI_EXHAUSTION")

            swing_bars = df.iloc[:-3].tail(35)
            supply_zone = float(df.iloc[:-1].tail(25)["high"].max())
            if len(swing_bars) >= 5:
                supply_zone = float(swing_bars["high"].max())

            touch_high = c_high >= supply_zone - (c_atr * self.touch_atr)
            close_near = abs(c_close - supply_zone) <= (c_atr * self.close_near_atr)
            not_broken = c_close <= supply_zone + (c_atr * 1.50)
            near_supply = (touch_high or close_near) and not_broken

            if not near_supply:
                return None

            # Mandatory Absorption Wick Verification: Do not sell without rejection
            has_rejection = (
                p_upper_wick_ratio >= self.min_wick_ratio or
                (c_close < c_open and c_upper_wick_ratio >= 0.18) or
                c_upper_wick_ratio >= 0.30
            )
            if not has_rejection:
                return None

            confluences.append("SUPPLY_ZONE_RETEST")
            confluences.append("ABSORPTION_WICK_CONFIRMED")

            min_sl_dist = 20.0 if "1000" in symbol else (15.0 if "500" in symbol else (8.0 if "300" in symbol else max(c_atr * 1.5, point * 50.0)))
            sl_buffer = max(c_atr * self.sl_buffer_atr, min_sl_dist)
            sl_price = supply_zone + sl_buffer
            risk_points = max(abs(sl_price - c_close), min_sl_dist)
            sl_price = c_close + risk_points

            if risk_points <= point:
                return None

            tp_price = c_close - (risk_points * self.target_rr)
            rr = abs(c_close - tp_price) / risk_points

            if rr < self.min_rr:
                return None

            return SpikeSignal(
                symbol=symbol,
                direction=direction,
                entry_price=c_close,
                sl_price=sl_price,
                tp_price=tp_price,
                risk_points=risk_points,
                target_rr=round(rr, 2),
                max_holding_bars=self.max_holding_bars,
                setup_name="CRASH_SPIKE_SNIPER_SUPPLY",
                confluences=tuple(confluences),
                order_block_price=supply_zone,
                atr=c_atr,
            )

        # ==========================================
        # BOOM 1000 / 500 / 300 SETUP (ONLY BUY)
        # ==========================================
        elif is_boom:
            direction = Direction.BUY
            confluences.append("BOOM_RULE_ONLY_BUY")

            # Downward tick drift exhaustion is MANDATORY for cold demand tests
            if c_rsi > self.boom_rsi_oversold:
                return None
            # Trend alignment: cannot buy in cold market if price is below M5 EMA-50 (falling knife)
            if c_close < ema_50:
                return None
            confluences.append("M5_RSI_EXHAUSTION")

            swing_bars = df.iloc[:-3].tail(35)
            demand_zone = float(df.iloc[:-1].tail(25)["low"].min())
            if len(swing_bars) >= 5:
                demand_zone = float(swing_bars["low"].min())

            touch_low = c_low <= demand_zone + (c_atr * self.touch_atr)
            close_near = abs(c_close - demand_zone) <= (c_atr * self.close_near_atr)
            not_broken = c_close >= demand_zone - (c_atr * 1.50)
            near_demand = (touch_low or close_near) and not_broken

            if not near_demand:
                return None

            # Mandatory Absorption Wick Verification: Do not buy without rejection
            has_rejection = (
                p_lower_wick_ratio >= self.min_wick_ratio or
                (c_close > c_open and c_lower_wick_ratio >= 0.18) or
                c_lower_wick_ratio >= 0.30
            )
            if not has_rejection:
                return None

            confluences.append("DEMAND_ZONE_RETEST")
            confluences.append("ABSORPTION_WICK_CONFIRMED")

            min_sl_dist = 20.0 if "1000" in symbol else (15.0 if "500" in symbol else (8.0 if "300" in symbol else max(c_atr * 1.5, point * 50.0)))
            sl_buffer = max(c_atr * self.sl_buffer_atr, min_sl_dist)
            sl_price = demand_zone - sl_buffer
            risk_points = max(abs(c_close - sl_price), min_sl_dist)
            sl_price = c_close - risk_points

            if risk_points <= point:
                return None

            tp_price = c_close + (risk_points * self.target_rr)
            rr = abs(tp_price - c_close) / risk_points

            if rr < self.min_rr:
                return None

            return SpikeSignal(
                symbol=symbol,
                direction=direction,
                entry_price=c_close,
                sl_price=sl_price,
                tp_price=tp_price,
                risk_points=risk_points,
                target_rr=round(rr, 2),
                max_holding_bars=self.max_holding_bars,
                setup_name="BOOM_SPIKE_SNIPER_DEMAND",
                confluences=tuple(confluences),
                order_block_price=demand_zone,
                atr=c_atr,
            )

        return None

    def is_structure_holding(
        self,
        symbol: str,
        direction: Direction,
        entry_price: float,
        df: pd.DataFrame,
        point: float = 0.001,
        lookback_bars: int = 35,
    ) -> Tuple[bool, str, int, float]:
        """
        Evaluate if market structure is respecting the key ceiling (Crash) or floor (Boom).

        Returns:
            (is_holding: bool, reason: str, touches: int, key_level: float)
        """
        if df is None or len(df) < 15:
            return False, "INSUFFICIENT_DATA", 0, 0.0

        is_crash = self.is_crash_symbol(symbol)
        is_boom = self.is_boom_symbol(symbol)
        if not is_crash and not is_boom:
            return False, "NOT_BOOM_CRASH", 0, 0.0

        df_ind = self.calculate_indicators(df)
        atr = float(df_ind.iloc[-1]["atr"])
        window = df.tail(lookback_bars)
        last_close = float(df.iloc[-1]["close"])

        if is_crash:
            # Ceiling is the highest high in the recent window
            ceiling = float(window["high"].max())
            touches = int((window["high"] >= ceiling - (atr * 1.0)).sum())

            # Structure is broken if price closed decisively above ceiling
            if last_close > (ceiling + atr * 0.40):
                return False, f"CEILING_BROKEN: close {last_close:.2f} > ceiling {ceiling:.2f}", touches, ceiling

            # Excessive drift past entry threshold (expanded to 1.5 ATR to avoid prematurely cutting trades)
            if entry_price > 0 and last_close > (entry_price + atr * 1.50):
                return False, f"DRIFT_EXCEEDED: close {last_close:.2f} drifted above entry {entry_price:.2f} by >1.5 ATR", touches, ceiling

            return True, f"CEILING_HOLDING: ceiling at {ceiling:.2f} respected ({touches} touches)", touches, ceiling

        elif is_boom:
            # Floor is the lowest low in the recent window
            floor = float(window["low"].iloc[:-1].min()) if len(window) > 1 else float(window["low"].min())
            touches = int((window["low"] <= floor + (atr * 1.0)).sum())

            # Structure is broken if price closed decisively below floor
            if last_close < (floor - atr * 0.40):
                return False, f"FLOOR_BROKEN: close {last_close:.2f} < floor {floor:.2f}", touches, floor

            # Excessive drift past entry threshold (expanded to 1.5 ATR to avoid prematurely cutting trades)
            if entry_price > 0 and last_close < (entry_price - atr * 1.50):
                return False, f"DRIFT_EXCEEDED: close {last_close:.2f} drifted below entry {entry_price:.2f} by >1.5 ATR", touches, floor

            return True, f"FLOOR_HOLDING: floor at {floor:.2f} respected ({touches} touches)", touches, floor

        return False, "UNKNOWN", 0, 0.0

