"""Mechanical Alchemist Strategy Engine (MSNR + CRT + ICT Sessions).

Faithful implementation of "Alchemist Strategy Simplified FX Pro" by Bam FX.
Core Components:
1. Malaysian Support & Resistance (MSNR) on Line Chart (Open/Close junctions, ignoring wicks):
   - [A] Formations (Resistance): Bullish close + Bearish open.
   - [V] Formations (Support): Bearish close + Bullish open.
   - RBS / SBR transitions (Resistance Becoming Support / Support Becoming Resistance).
   - Quasimodo (QM) reversal structure.
   - Freshness tracking (untouched levels).
2. London Killzone Model (08:00 - 10:00 GMT):
   - Asian Range (00:00 - 08:00 GMT) liquidity sweep (Asia High / Asia Low taken out).
   - Tap into HTF MSNR / Rejection Block.
   - M15 structure shift / close back inside range.
3. New York 9:00 AM NY CRT Model (13:00 - 16:00 UTC):
   - 13:00 UTC Range Candle.
   - 14:00 UTC Directional Expansion Candle.
   - 15:00 UTC Tap into the 13:00 CRT range zone -> M15 entry confirmation.
4. Rule 6: Avoid Monday trading (consolidation and manipulation).
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from core.config_loader import AlchemistConfig
from core.constants import Direction

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
_MSNR_ZONE_CACHE: OrderedDict[tuple, list["MSNRZone"]] = OrderedDict()
_MSNR_ZONE_CACHE_LIMIT = 2048


@dataclass(frozen=True)
class AlchemistSetup:
    direction: Direction
    entry_price: float
    sl_price: float
    tp_price: float
    model: str
    poi_name: str
    poi_price: float
    liquidity_name: str
    liquidity_price: float
    confluences: tuple[str, ...]
    rr: float


@dataclass(frozen=True)
class MSNRZone:
    name: str
    direction: Direction  # BUY = support, SELL = resistance
    price: float
    width: float
    fresh: bool
    timeframe: str
    bar_index: int = -1


def extract_msnr_zones(
    frame: Optional[pd.DataFrame],
    tf_name: str,
    max_lookback: int = 100,
) -> list[MSNRZone]:
    """Detect [A] and [V] MSNR levels from Open/Close transitions (Line Chart).

    Per the guide:
    - Draw line across first candle's CLOSE to join next candle's OPEN. Ignore wicks.
    - [A] Resistance: Bullish candle followed by Bearish candle.
    - [V] Support: Bearish candle followed by Bullish candle.
    - Fresh: Price has not traded through the level since formation.
    """
    if frame is None or len(frame) < 3 or "close" not in frame or "open" not in frame:
        return []

    tail = frame.tail(max_lookback)
    fingerprint_cols = [c for c in ("time", "open", "high", "low", "close") if c in tail]
    fingerprint = hashlib.sha256(
        pd.util.hash_pandas_object(tail[fingerprint_cols], index=True).values.tobytes()
    ).digest()[:12]
    last = frame.iloc[-1]
    cache_key = (
        tf_name, len(frame), max_lookback, fingerprint, str(last.get("time", "")),
        float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"]),
    )
    cached = _MSNR_ZONE_CACHE.get(cache_key)
    if cached is not None:
        _MSNR_ZONE_CACHE.move_to_end(cache_key)
        return cached

    tail = frame.tail(max_lookback)
    opens = tail["open"].to_numpy(dtype=float)
    closes = tail["close"].to_numpy(dtype=float)
    highs = tail["high"].to_numpy(dtype=float)
    lows = tail["low"].to_numpy(dtype=float)
    atrs = tail["atr"].to_numpy(dtype=float) if "atr" in tail else None
    n = len(tail)
    zones: list[MSNRZone] = []

    source_offset = len(frame) - n
    for i in range(1, n - 1):
        p_open, p_close = opens[i - 1], closes[i - 1]
        c_open, c_close = opens[i], closes[i]

        prev_bull = p_close > p_open
        cur_bull = c_close > c_open

        if prev_bull == cur_bull or p_close == p_open or c_close == c_open:
            continue

        if prev_bull and not cur_bull:
            direction = Direction.SELL
            name = f"{tf_name}_A_RES"
        else:
            direction = Direction.BUY
            name = f"{tf_name}_V_SUP"

        level_price = (p_close + c_open) / 2.0
        atr = atrs[i] if atrs is not None and not math.isnan(atrs[i]) else 0.0
        width = max(atr * 0.10, abs(p_close - c_open), 1e-5)

        if i + 1 < n:
            later_touched = ((highs[i + 1:] >= level_price - width)
                             & (lows[i + 1:] <= level_price + width))
            fresh = not bool(later_touched.any())
        else:
            fresh = True

        zones.append(MSNRZone(
            name=name,
            direction=direction,
            price=float(level_price),
            width=float(width),
            fresh=fresh,
            timeframe=tf_name,
            bar_index=source_offset + i,
        ))

    _MSNR_ZONE_CACHE[cache_key] = zones
    if len(_MSNR_ZONE_CACHE) > _MSNR_ZONE_CACHE_LIMIT:
        _MSNR_ZONE_CACHE.popitem(last=False)
    return zones


def detect_quasimodo_level(
    frame: Optional[pd.DataFrame],
    direction: Direction,
    swing_window: int = 3,
) -> Optional[float]:
    """Detect Quasimodo (QM) Left Shoulder level.

    Bullish QM (for BUY):
    - Low 1 (Left Shoulder) -> High 1 -> Lower Low 2 (Sweep) -> Higher High 2 (BOS).
    - Left shoulder level provides strong reversal support.

    Bearish QM (for SELL):
    - High 1 (Left Shoulder) -> Low 1 -> Higher High 2 (Sweep) -> Lower Low 2 (BOS).
    - Left shoulder level provides strong reversal resistance.
    """
    if frame is None or len(frame) < swing_window * 4:
        return None

    highs = frame["high"].values
    lows = frame["low"].values
    n = len(frame)

    if direction == Direction.BUY:
        # Look for L1, H1, L2 (L2 < L1), H2 (H2 > H1)
        for i in range(n - 10, max(5, n - 40), -1):
            l1 = lows[i]
            # Find subsequent high
            h_slice = highs[i:i + 6]
            if len(h_slice) < 2:
                continue
            h1 = h_slice.max()
            h1_idx = i + h_slice.argmax()
            # Find subsequent lower low
            l_slice = lows[h1_idx:min(h1_idx + 8, n)]
            if len(l_slice) < 2:
                continue
            l2 = l_slice.min()
            l2_idx = h1_idx + l_slice.argmin()
            if l2 < l1 and l2_idx > h1_idx:
                # Check for subsequent BOS above H1
                if highs[l2_idx:].max() > h1:
                    return float(l1)
    else:
        # Bearish QM: H1, L1, H2 (H2 > H1), L2 (L2 < L1)
        for i in range(n - 10, max(5, n - 40), -1):
            h1 = highs[i]
            l_slice = lows[i:i + 6]
            if len(l_slice) < 2:
                continue
            l1 = l_slice.min()
            l1_idx = i + l_slice.argmin()
            h_slice = highs[l1_idx:min(l1_idx + 8, n)]
            if len(h_slice) < 2:
                continue
            h2 = h_slice.max()
            h2_idx = l1_idx + h_slice.argmax()
            if h2 > h1 and h2_idx > l1_idx:
                if lows[h2_idx:].min() < l1:
                    return float(h1)

    return None


def _directional_bias(frame: Optional[pd.DataFrame], minimum_bars: int = 6) -> Optional[Direction]:
    """Small, closed-bar proxy for the guide's higher-timeframe storyline."""
    if frame is None or len(frame) < minimum_bars or "close" not in frame:
        return None
    closes = pd.to_numeric(frame["close"], errors="coerce").dropna().tail(minimum_bars)
    if len(closes) < minimum_bars:
        return None
    start, end = float(closes.iloc[0]), float(closes.iloc[-1])
    midpoint = (float(closes.max()) + float(closes.min())) / 2.0
    if end > start and end > midpoint:
        return Direction.BUY
    if end < start and end < midpoint:
        return Direction.SELL
    return None


def _is_adverse_htf_expansion(frame: Optional[pd.DataFrame], direction: Direction) -> bool:
    """Mathematical Marubozu / Displacement check on the most recent closed HTF candle.

    Vetoes trades if the preceding HTF candle was a massive momentum expansion against the setup:
    1. Body >= 1.5 * ATR_14 (if ATR available, high volatility displacement).
    2. Body Dominance >= 70% of total candle range ((Close - Open) / (High - Low) >= 0.70).
    3. Close Proximity: closes within extreme 15% of range (minimal wick against momentum).
    """
    if frame is None or len(frame) < 2 or "open" not in frame or "close" not in frame:
        return False
    bar = frame.iloc[-1]
    high, low = float(bar["high"]), float(bar["low"])
    open_, close = float(bar["open"]), float(bar["close"])
    candle_range = high - low
    if candle_range <= 0:
        return False
    body = abs(close - open_)
    atr = float(bar.get("atr", 0.0) or 0.0)

    # 1. Volatility check: if ATR available, body must exceed 1.5x ATR
    if atr > 0 and body < 1.5 * atr:
        return False

    # 2. Body dominance >= 70% of range
    if (body / candle_range) < 0.70:
        return False

    # 3. Directional check against setup
    if direction == Direction.SELL:
        # Preceding HTF candle is strong bullish expansion closing in top 15%
        is_bullish = close > open_
        upper_wick_ratio = (high - close) / candle_range
        if is_bullish and upper_wick_ratio <= 0.15:
            return True
    elif direction == Direction.BUY:
        # Preceding HTF candle is strong bearish expansion closing in bottom 15%
        is_bearish = close < open_
        lower_wick_ratio = (close - low) / candle_range
        if is_bearish and lower_wick_ratio <= 0.15:
            return True

    return False


def _m15_structure_shift(frame: pd.DataFrame, direction: Direction, lookback: int = 4) -> bool:
    """Require a closed M15 body to break recent structure in the setup direction."""
    if frame is None or len(frame) < lookback + 1:
        return False
    bar = frame.iloc[-1]
    prior = frame.iloc[-lookback - 1:-1]
    if direction == Direction.BUY:
        return float(bar["close"]) > float(prior["high"].max()) and float(bar["close"]) > float(bar["open"])
    return float(bar["close"]) < float(prior["low"].min()) and float(bar["close"]) < float(bar["open"])


def _m15_entry_patterns(frame: Optional[pd.DataFrame], direction: Direction, atr: float) -> tuple[str, ...]:
    """Closed M15 confirmations corresponding to OB, SBR/RBS, QM and MSS."""
    if frame is None or len(frame) < 8 or atr <= 0:
        return ()
    bar, previous = frame.iloc[-1], frame.iloc[-2]
    favorable = float(bar["close"]) > float(bar["open"]) if direction == Direction.BUY else float(bar["close"]) < float(bar["open"])
    if not favorable:
        return ()
    patterns: list[str] = []
    if _m15_structure_shift(frame, direction, lookback=4):
        patterns.append("M15_STRUCTURE_SHIFT")

    if direction == Direction.BUY:
        engulf = (float(previous["close"]) < float(previous["open"])
                  and float(bar["open"]) <= float(previous["close"])
                  and float(bar["close"]) >= float(previous["open"]))
    else:
        engulf = (float(previous["close"]) > float(previous["open"])
                  and float(bar["open"]) >= float(previous["close"])
                  and float(bar["close"]) <= float(previous["open"]))
    if engulf:
        patterns.append("M15_ENGULFING")

    # Calculate MSNR before the current bar so a fresh level can be tapped by it.
    for zone in extract_msnr_zones(frame.iloc[:-1], "M15", max_lookback=80):
        touched = float(bar["low"]) <= zone.price + zone.width and float(bar["high"]) >= zone.price - zone.width
        near_close = abs(float(bar["close"]) - zone.price) <= max(atr * 0.25, zone.width)
        if zone.direction == direction and zone.fresh and touched and near_close:
            patterns.append("M15_MSNR_FLIP")
            break

    # Retest of the last opposing candle before a directional displacement.
    for idx in range(len(frame) - 4, max(1, len(frame) - 8), -1):
        ob = frame.iloc[idx]
        opposing = float(ob["close"]) < float(ob["open"]) if direction == Direction.BUY else float(ob["close"]) > float(ob["open"])
        displacement = frame.iloc[idx + 1:-1]
        if not opposing or displacement.empty:
            continue
        broke = float(displacement["close"].max()) > float(ob["high"]) if direction == Direction.BUY else float(displacement["close"].min()) < float(ob["low"])
        retested = float(bar["low"]) <= float(ob["high"]) and float(bar["high"]) >= float(ob["low"])
        if broke and retested:
            patterns.append("M15_ORDER_BLOCK")
            break

    qm = detect_quasimodo_level(frame.tail(60), direction)
    if qm is not None and float(bar["low"]) <= qm + atr * 0.20 and float(bar["high"]) >= qm - atr * 0.20:
        patterns.append("M15_QM")
    return tuple(dict.fromkeys(patterns))


def _fvg_zones(frame: Optional[pd.DataFrame], timeframe: str, lookback: int = 40) -> list[tuple[Direction, float, float]]:
    """Return closed-candle three-bar fair-value gaps as (side, lower, upper)."""
    if frame is None or len(frame) < 3:
        return []
    tail = frame.tail(lookback)
    zones: list[tuple[Direction, float, float]] = []
    for i in range(2, len(tail)):
        first, middle, third = tail.iloc[i - 2], tail.iloc[i - 1], tail.iloc[i]
        # The middle candle must show displacement; this avoids labeling every
        # tiny non-overlap as an ICT imbalance.
        body = abs(float(middle["close"]) - float(middle["open"]))
        atr = float(middle.get("atr", 0.0) or 0.0)
        if atr > 0 and body < atr * 0.25:
            continue
        if float(third["low"]) > float(first["high"]):
            zones.append((Direction.BUY, float(first["high"]), float(third["low"])))
        elif float(third["high"]) < float(first["low"]):
            zones.append((Direction.SELL, float(third["high"]), float(first["low"])))
    return zones


def _directional_m30_confirmation(frame: Optional[pd.DataFrame], direction: Direction) -> bool:
    """Require a closed M30 displacement through recent structure or a sweep/reclaim."""
    if frame is None or len(frame) < 5:
        return False
    bar = frame.iloc[-1]
    prior = frame.iloc[-4:-1]
    high, low = float(bar["high"]), float(bar["low"])
    close, open_ = float(bar["close"]), float(bar["open"])
    if direction == Direction.BUY:
        return (close > open_ and (close > float(prior["high"].max())
                                   or (low < float(prior["low"].min()) and close > float(prior["low"].min()))))
    return (close < open_ and (close < float(prior["low"].min())
                               or (high > float(prior["high"].max()) and close < float(prior["high"].max()))))


def _m30_level_reaction(frame: Optional[pd.DataFrame], direction: Direction, price: float, width: float) -> bool:
    """Closed M30 CRT-style rejection at an H4 decision level."""
    if frame is None or frame.empty:
        return False
    bar = frame.iloc[-1]
    high, low = float(bar["high"]), float(bar["low"])
    open_, close = float(bar["open"]), float(bar["close"])
    if low > price + width or high < price - width:
        return False
    midpoint = (high + low) / 2.0
    if direction == Direction.BUY:
        return close > open_ and close > price and close >= midpoint
    return close < open_ and close < price and close <= midpoint


def _build_htf_setup(
    *, direction: Direction, bar: pd.Series, df_entry: pd.DataFrame, config: AlchemistConfig,
    point: float, model: str, poi_name: str, poi_price: float, liquidity_name: str,
    liquidity_price: float, confluences: tuple[str, ...], stop_reference: float,
) -> Optional[AlchemistSetup]:
    """Build a mechanically risk-bounded setup from a confirmed HTF model."""
    atr = float(bar.get("atr", 0.0) or 0.0)
    if atr <= 0:
        atr = abs(float(bar["high"]) - float(bar["low"]))
    if atr <= 0:
        return None
    entry = float(bar["close"])
    buffer = atr * float(config.stop_buffer_atr)
    stop = stop_reference - buffer if direction == Direction.BUY else stop_reference + buffer
    risk = abs(entry - stop)
    if risk <= point or risk > atr * float(config.max_stop_atr):
        return None
    target = entry + risk * float(config.target_rr) if direction == Direction.BUY else entry - risk * float(config.target_rr)
    rr = abs(target - entry) / risk
    if rr < float(config.minimum_rr):
        return None
    return AlchemistSetup(
        direction=direction, entry_price=entry, sl_price=stop, tp_price=target,
        model=model, poi_name=poi_name, poi_price=float(poi_price),
        liquidity_name=liquidity_name, liquidity_price=float(liquidity_price),
        confluences=confluences, rr=round(rr, 2),
    )


def evaluate_h4_m30_crt_setup(
    *, bar: pd.Series, df_entry: pd.DataFrame, df_h4: Optional[pd.DataFrame],
    df_m30: Optional[pd.DataFrame], htf_zones: list[MSNRZone], config: AlchemistConfig,
    point: float,
) -> Optional[AlchemistSetup]:
    """H4 MSNR rejection + M30 CRT/structure confirmation + M15 MSS entry."""
    if df_h4 is None or len(df_h4) < 3 or df_m30 is None or len(df_m30) < 5:
        return None
    h4 = df_h4.iloc[-1]
    atr = float(bar.get("atr", 0.0) or 0.0)
    if atr <= 0:
        atr = abs(float(bar["high"]) - float(bar["low"]))
    if atr <= 0:
        return None
    for zone in htf_zones:
        if zone.timeframe != "H4":
            continue
        direction = zone.direction
        h4_o, h4_c = float(h4["open"]), float(h4["close"])
        h4_h, h4_l = float(h4["high"]), float(h4["low"])
        touched = h4_l <= zone.price + zone.width and h4_h >= zone.price - zone.width
        if not touched:
            continue
        lookback = min(len(df_entry), 20)
        if direction == Direction.BUY:
            rejected = h4_c > h4_o and h4_c > zone.price
            entry_rejection = (float(bar["close"]) > float(bar["open"])
                               and float(bar["close"]) > zone.price
                               and float(bar["low"]) <= zone.price + max(zone.width, atr * 0.10))
            swing_low = float(df_entry.tail(lookback)["low"].min())
            stop_ref = min(swing_low, zone.price - zone.width)
        else:
            rejected = h4_c < h4_o and h4_c < zone.price
            entry_rejection = (float(bar["close"]) < float(bar["open"])
                               and float(bar["close"]) < zone.price
                               and float(bar["high"]) >= zone.price - max(zone.width, atr * 0.10))
            swing_high = float(df_entry.tail(lookback)["high"].max())
            stop_ref = max(swing_high, zone.price + zone.width)
        m30_confirmed = (_directional_m30_confirmation(df_m30, direction)
                         or _m30_level_reaction(df_m30, direction, zone.price, max(zone.width, atr * 0.15)))
        if not rejected or not m30_confirmed:
            continue
        if not entry_rejection:
            continue
        # Reject stale or remote entries after the H4 touch.
        if abs(float(bar["close"]) - zone.price) > atr * float(config.max_poi_distance_atr):
            continue
        return _build_htf_setup(
            direction=direction, bar=bar, df_entry=df_entry, config=config, point=point,
            model="H4_M30_CRT", poi_name=zone.name, poi_price=zone.price,
            liquidity_name="H4_MSNR_REJECTION", liquidity_price=zone.price,
            confluences=("H4_MSNR", "H4_REJECTION", "M30_CRT_CONFIRMATION", "M15_MSNR_REJECTION"),
            stop_reference=stop_ref,
        )
    return None


def evaluate_h4_h2_gap_setup(
    *, bar: pd.Series, df_entry: pd.DataFrame, df_h4: Optional[pd.DataFrame],
    df_h2: Optional[pd.DataFrame], htf_zones: list[MSNRZone], config: AlchemistConfig,
    point: float,
) -> Optional[AlchemistSetup]:
    """H4 imbalance at MSNR, refined by an overlapping H2 FVG and M15 retest/MSS."""
    if df_h4 is None or len(df_h4) < 5 or df_h2 is None or len(df_h2) < 5:
        return None
    h4_gaps, h2_gaps = _fvg_zones(df_h4, "H4"), _fvg_zones(df_h2, "H2")
    # Freshness is measured before the current decision bar so the live
    # retest itself does not consume the POI before it can be traded.
    h2_history = df_h2.iloc[:-1] if len(df_h2) > 5 else df_h2
    h2_msnr = extract_msnr_zones(h2_history, "H2", max_lookback=80)
    if not h4_gaps or not h2_gaps:
        return None
    atr = float(bar.get("atr", 0.0) or 0.0)
    if atr <= 0:
        atr = abs(float(bar["high"]) - float(bar["low"]))
    if atr <= 0:
        return None
    current_low, current_high = float(bar["low"]), float(bar["high"])
    for direction, h4_low, h4_high in reversed(h4_gaps[-12:]):
        zone = next((z for z in htf_zones if z.timeframe == "H4" and z.direction == direction
                     and h4_low - z.width <= z.price <= h4_high + z.width), None)
        if zone is None:
            continue
        for h2_direction, h2_low, h2_high in reversed(h2_gaps[-16:]):
            if h2_direction != direction:
                continue
            overlap_low, overlap_high = max(h4_low, h2_low), min(h4_high, h2_high)
            if overlap_low > overlap_high:
                continue
            # The guide refines the H4 gap on H2 and looks for the SBR/RBS
            # decision level there; require that structure to agree with the
            # gap direction instead of treating any overlapping FVG as enough.
            flip = next((z for z in h2_msnr if z.direction == direction and z.fresh
                         and overlap_low - z.width <= z.price <= overlap_high + z.width), None)
            if flip is None:
                continue
            tapped = current_low <= overlap_high and current_high >= overlap_low
            current_open, current_close = float(bar["open"]), float(bar["close"])
            midpoint = (current_low + current_high) / 2.0
            reaction = (current_close > current_open and current_close >= midpoint) if direction == Direction.BUY else (current_close < current_open and current_close <= midpoint)
            if not tapped or not reaction:
                continue
            lookback = min(len(df_entry), 20)
            if direction == Direction.BUY:
                recent_ext = float(df_entry.tail(lookback)["low"].min())
                stop_ref = min(recent_ext, overlap_low)
            else:
                recent_ext = float(df_entry.tail(lookback)["high"].max())
                stop_ref = max(recent_ext, overlap_high)
            return _build_htf_setup(
                direction=direction, bar=bar, df_entry=df_entry, config=config, point=point,
                model="H4_H2_GAP", poi_name=zone.name, poi_price=zone.price,
                liquidity_name="H2_FVG_RETEST", liquidity_price=(overlap_low + overlap_high) / 2.0,
                confluences=("H4_MSNR", "H4_FVG", "H2_FVG_REFINEMENT", "H2_SBR_RBS", "M15_FVG_RETEST", "M15_FVG_REACTION"),
                stop_reference=stop_ref,
            )
    return None


def get_asia_session_range(
    df: pd.DataFrame,
    current_time_utc: pd.Timestamp,
) -> Optional[Tuple[float, float, pd.Timestamp, pd.Timestamp]]:
    """Calculate the Asian session range (00:00 to 08:00 GMT/London).

    Returns (asia_high, asia_low, start_time, end_time) or None.
    """
    if df is None or df.empty or "time" not in df:
        return None

    times_utc = pd.to_datetime(df["time"], utc=True)
    times_london = times_utc.dt.tz_convert(LONDON)
    local_london = current_time_utc.to_pydatetime().astimezone(LONDON)

    # Filter bars from the same day with local London hour < 8 (00:00 to 07:45 London time)
    same_day_asia = (times_london.dt.date == local_london.date()) & (times_london.dt.hour < 8)
    asia_bars = df.loc[same_day_asia]

    if len(asia_bars) < 8:
        return None

    asia_high = float(asia_bars["high"].max())
    asia_low = float(asia_bars["low"].min())
    return (asia_high, asia_low, times_utc.loc[same_day_asia].iloc[0], times_utc.loc[same_day_asia].iloc[-1])


def evaluate_london_killzone_setup(
    bar: pd.Series,
    df_entry: pd.DataFrame,
    current_time_utc: pd.Timestamp,
    config: AlchemistConfig,
    htf_zones: list[MSNRZone],
    point: float,
) -> Optional[AlchemistSetup]:
    """Model 1: London Killzone (08:00 - 10:00 GMT/BST) Asian Liquidity Sweep.

    1. London Open sweeps Asian High or Asian Low.
    2. Optional tap into H4/D1 MSNR level.
    3. M15 rejection candle confirms reversal.
    4. Target opposite Asian extreme or target R:R.
    """
    local_london = current_time_utc.to_pydatetime().astimezone(LONDON)
    hour = local_london.hour
    if not (config.london_open_hour <= hour < config.london_close_hour):
        return None

    asia = get_asia_session_range(df_entry, current_time_utc)
    if asia is None:
        return None

    asia_high, asia_low, _, _ = asia
    asia_range_pips = (asia_high - asia_low) / (point * 10 if point > 0 else 1)
    if asia_high <= asia_low or asia_range_pips < 5:
        return None

    # Look back at recent bars within London Killzone
    times_utc = pd.to_datetime(df_entry["time"], utc=True)
    times_london = times_utc.dt.tz_convert(LONDON)
    kz_mask = (times_london.dt.date == local_london.date()) & (times_london.dt.hour >= config.london_open_hour)
    kz_bars = df_entry.loc[kz_mask]


    if len(kz_bars) < 1:
        return None

    c_open = float(bar["open"])
    c_close = float(bar["close"])
    c_high = float(bar["high"])
    c_low = float(bar["low"])
    atr = float(bar.get("atr", 0.0) or 0.0)
    if atr <= 0:
        atr = abs(c_high - c_low)

    buffer = atr * float(config.stop_buffer_atr)
    confluences: list[str] = ["LONDON_KILLZONE"]

    # Scenario A: Bullish Reversal after Sweeping Asian Low
    kz_min_low = float(kz_bars["low"].min())
    sweep_low_depth = asia_low - kz_min_low
    if 0.15 * atr <= sweep_low_depth <= 1.50 * atr:
        # Asian low was cleanly swept!
        # Must close back ABOVE Asia Low with rejection confirmation
        rejection = (c_close > asia_low) and (c_close > c_open or (c_close - c_low) >= 0.4 * (c_high - c_low))
        if rejection:
            direction = Direction.BUY
            confluences.append("ASIA_LOW_SWEEP")
            confluences.append("M15_BULLISH_REJECTION")

            # Check if there is an H4/D1 Support level near the sweep extreme
            poi_name = "ASIA_LOW"
            poi_price = asia_low
            for zone in htf_zones:
                if zone.direction == Direction.BUY:
                    if abs(zone.price - kz_min_low) <= atr * float(config.max_poi_distance_atr):
                        confluences.append(f"MSNR_{zone.name}")
                        poi_name = zone.name
                        poi_price = zone.price
                        break

            # Stop loss: below the lowest low of the sweep
            sl_price = kz_min_low - buffer
            risk = abs(c_close - sl_price)
            if risk <= point or risk > atr * float(config.max_stop_atr):
                return None

            # Take profit: target Asia High, capped at 2.5R to ensure reachable intraday targets
            desired_tp = c_close + risk * float(config.target_rr)
            if asia_high > c_close:
                tp_price = min(asia_high, c_close + risk * 2.5)
            else:
                tp_price = desired_tp
            rr = abs(tp_price - c_close) / risk

            if rr >= float(config.minimum_rr):
                return AlchemistSetup(
                    direction=direction,
                    entry_price=c_close,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    model="LONDON_ASIA_SWEEP",
                    poi_name=poi_name,
                    poi_price=poi_price,
                    liquidity_name="ASIA_LOW",
                    liquidity_price=asia_low,
                    confluences=tuple(confluences),
                    rr=round(rr, 2),
                )

    # Scenario B: Bearish Reversal after Sweeping Asian High
    kz_max_high = float(kz_bars["high"].max())
    sweep_high_depth = kz_max_high - asia_high
    if 0.15 * atr <= sweep_high_depth <= 1.50 * atr:
        # Asian high was cleanly swept!
        # Must close back BELOW Asia High with rejection confirmation
        rejection = (c_close < asia_high) and (c_close < c_open or (c_high - c_close) >= 0.4 * (c_high - c_low))
        if rejection:
            direction = Direction.SELL
            confluences.append("ASIA_HIGH_SWEEP")
            confluences.append("M15_BEARISH_REJECTION")

            # Check if there is an H4/D1 Resistance level near the sweep extreme
            poi_name = "ASIA_HIGH"
            poi_price = asia_high
            for zone in htf_zones:
                if zone.direction == Direction.SELL:
                    if abs(zone.price - kz_max_high) <= atr * float(config.max_poi_distance_atr):
                        confluences.append(f"MSNR_{zone.name}")
                        poi_name = zone.name
                        poi_price = zone.price
                        break

            # Stop loss: above the highest high of the sweep
            sl_price = kz_max_high + buffer
            risk = abs(sl_price - c_close)
            if risk <= point or risk > atr * float(config.max_stop_atr):
                return None

            # Take profit: target Asia Low, capped at 2.5R to ensure reachable intraday targets
            desired_tp = c_close - risk * float(config.target_rr)
            if asia_low < c_close:
                tp_price = max(asia_low, c_close - risk * 2.5)
            else:
                tp_price = desired_tp
            rr = abs(c_close - tp_price) / risk

            if rr >= float(config.minimum_rr):
                return AlchemistSetup(
                    direction=direction,
                    entry_price=c_close,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    model="LONDON_ASIA_SWEEP",
                    poi_name=poi_name,
                    poi_price=poi_price,
                    liquidity_name="ASIA_HIGH",
                    liquidity_price=asia_high,
                    confluences=tuple(confluences),
                    rr=round(rr, 2),
                )

    return None


def evaluate_new_york_crt_setup(
    bar: pd.Series,
    df_setup_h1: Optional[pd.DataFrame],
    current_time_utc: pd.Timestamp,
    config: AlchemistConfig,
    htf_zones: list[MSNRZone],
    point: float,
) -> Optional[AlchemistSetup]:
    """Model 2: 1H CRT + 15M Entry (New York 9:00 AM NY CRT).

    Sequence (in UTC):
    - 13:00 UTC (9:00 AM NY): Range Candle (Mark High & Low).
    - 14:00 UTC (10:00 AM NY): Direction Candle.
      * Close > 13:00 High (or upper half) -> Bullish.
      * Close < 13:00 Low (or lower half) -> Bearish.
    - 15:00 - 16:30 UTC: Tap candle into 13:00 range zone -> M15 entry confirmation.
    """
    if df_setup_h1 is None or len(df_setup_h1) < 4:
        return None

    # Interpret all session clocks in New York local time so DST does not
    # shift the CRT model by an hour in winter.
    current_local = current_time_utc.to_pydatetime().astimezone(NEW_YORK)
    crt_hour = int(config.new_york_crt_hour)
    if current_local.hour != crt_hour + 2:
        return None

    times_utc = pd.to_datetime(df_setup_h1["time"], utc=True)
    times_local = times_utc.dt.tz_convert(NEW_YORK)
    current_date = current_local.date()

    # Find the CRT range candle and the following direction candle locally (look back max 12 bars).
    c13 = None
    c14 = None
    start_idx = max(0, len(df_setup_h1) - 12)
    for i in range(len(df_setup_h1) - 1, start_idx - 1, -1):
        t = times_local.iloc[i]
        if t.date() == current_date:
            if t.hour == crt_hour and c13 is None:
                c13 = df_setup_h1.iloc[i]
            elif t.hour == crt_hour + 1 and c14 is None:
                c14 = df_setup_h1.iloc[i]
        elif t.date() < current_date:
            break

    if c13 is None or c14 is None:
        return None

    c13_high = float(c13["high"])
    c13_low = float(c13["low"])
    c14_close = float(c14["close"])

    # Determine Direction from 14:00 candle: MUST close outside 13:00 range
    direction: Optional[Direction] = None
    if c14_close > c13_high:
        direction = Direction.BUY
    elif c14_close < c13_low:
        direction = Direction.SELL

    if direction is None:
        return None

    c_open = float(bar["open"])
    c_close = float(bar["close"])
    c_high = float(bar["high"])
    c_low = float(bar["low"])
    atr = float(bar.get("atr", 0.0) or 0.0)
    if atr <= 0:
        atr = abs(c_high - c_low)

    buffer = atr * float(config.stop_buffer_atr)
    confluences: list[str] = ["NY_H1_CRT", "CRT_DIRECTION_CONFIRMED"]

    # Verify that the M15 candle taps inside the 13:00 range
    tapped_range = (c_low <= c13_high and c_high >= c13_low)
    if not tapped_range:
        return None

    confluences.append("CRT_RANGE_TAP")

    # M15 Entry Reversal Confirmation
    if direction == Direction.BUY:
        # Bullish rejection: candle touches range and closes bullish or in top 50%
        if c_close > c_open or c_close > (c_high + c_low) / 2.0:
            confluences.append("M15_BULLISH_CLOSE")
            poi_name = "CRT_13_RANGE_LOW"
            poi_price = c13_low

            # Confluence with H4/D1 support or QM
            for zone in htf_zones:
                if zone.direction == Direction.BUY and abs(zone.price - c_low) <= atr * float(config.max_poi_distance_atr):
                    confluences.append(f"MSNR_{zone.name}")
                    poi_name = zone.name
                    poi_price = zone.price
                    break

            sl_price = min(c13_low, c_low) - buffer
            risk = abs(c_close - sl_price)
            if risk <= point or risk > atr * float(config.max_stop_atr):
                return None

            tp_price = c_close + risk * float(config.target_rr)
            rr = abs(tp_price - c_close) / risk

            if rr >= float(config.minimum_rr):
                return AlchemistSetup(
                    direction=direction,
                    entry_price=c_close,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    model="NY_H1_CRT",
                    poi_name=poi_name,
                    poi_price=poi_price,
                    liquidity_name="CRT_RANGE_LOW",
                    liquidity_price=c13_low,
                    confluences=tuple(confluences),
                    rr=round(rr, 2),
                )

    elif direction == Direction.SELL:
        # Bearish rejection: candle touches range and closes bearish or in bottom 50%
        if c_close < c_open or c_close < (c_high + c_low) / 2.0:
            confluences.append("M15_BEARISH_CLOSE")
            poi_name = "CRT_13_RANGE_HIGH"
            poi_price = c13_high

            # Confluence with H4/D1 resistance or QM
            for zone in htf_zones:
                if zone.direction == Direction.SELL and abs(zone.price - c_high) <= atr * float(config.max_poi_distance_atr):
                    confluences.append(f"MSNR_{zone.name}")
                    poi_name = zone.name
                    poi_price = zone.price
                    break

            sl_price = max(c13_high, c_high) + buffer
            risk = abs(sl_price - c_close)
            if risk <= point or risk > atr * float(config.max_stop_atr):
                return None

            tp_price = c_close - risk * float(config.target_rr)
            rr = abs(c_close - tp_price) / risk

            if rr >= float(config.minimum_rr):
                return AlchemistSetup(
                    direction=direction,
                    entry_price=c_close,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    model="NY_H1_CRT",
                    poi_name=poi_name,
                    poi_price=poi_price,
                    liquidity_name="CRT_RANGE_HIGH",
                    liquidity_price=c13_high,
                    confluences=tuple(confluences),
                    rr=round(rr, 2),
                )

    return None


def detect_alchemist_setup(
    df_entry: pd.DataFrame,
    df_setup: Optional[pd.DataFrame],
    df_higher: Optional[pd.DataFrame],
    df_daily: Optional[pd.DataFrame],
    df_weekly: Optional[pd.DataFrame],
    config: AlchemistConfig,
    *,
    point: float,
    consecutive_losses: int = 0,
    df_monthly: Optional[pd.DataFrame] = None,
    df_confirmation: Optional[pd.DataFrame] = None,
    df_gap: Optional[pd.DataFrame] = None,
) -> Optional[AlchemistSetup]:
    """Detect closed M15 entry adhering to the Alchemist Strategy (MSNR + CRT).

    Strict mechanical execution:
    - Rule 6: Skip Mondays.
    - Model 1: London Killzone (08:00 - 10:00 GMT) Asia Range Liquidity Sweep.
    - Model 2: New York 9:00 AM NY CRT (13:00 Range, 14:00 Direction, 15:00 Tap).
    """
    if not config.enabled or df_entry is None or len(df_entry) < 20 or point <= 0:
        return None

    if config.entry_model == "msnr_storyline_h1":
        # Keep the MR.R H4/H1 storyline hypothesis isolated from the PDF's
        # London/CRT M15 models. The selected profile must supply H1 bars.
        from strategy.msnr_storyline import detect_msnr_storyline_setup

        return detect_msnr_storyline_setup(
            df_h1=df_entry,
            df_h4=df_higher,
            df_daily=df_daily,
            df_weekly=df_weekly,
            df_monthly=df_monthly,
            config=config,
            point=point,
        )

    bar = df_entry.iloc[-1]
    current_time = pd.Timestamp(bar["time"])
    current_time_utc = current_time.tz_localize("UTC") if current_time.tzinfo is None else current_time.tz_convert("UTC")

    # Rule 6 from guide: Avoid Monday trades (manipulation and consolidation)
    local_london = current_time_utc.to_pydatetime().astimezone(LONDON)
    local_new_york = current_time_utc.to_pydatetime().astimezone(NEW_YORK)
    if config.avoid_mondays and local_london.weekday() == 0:
        return None
    if getattr(config, "avoid_asian_session", True) and (local_london.hour < 7 or local_london.hour >= 18):
        return None

    # Fast trading window filter uses the same local clocks as each model.
    is_london = config.london_sweep_enabled and config.london_open_hour <= local_london.hour < config.london_close_hour
    is_ny = config.new_york_crt_enabled and local_new_york.hour == int(config.new_york_crt_hour) + 2

    # The H4 models have no fixed clock window in the guide. Pre-screen them
    # cheaply so we do not rebuild MSNR/FVG context on every M15 candle.
    shift_buy = _m15_structure_shift(df_entry, Direction.BUY, lookback=4)
    shift_sell = _m15_structure_shift(df_entry, Direction.SELL, lookback=4)
    m30_structural = (
        (shift_buy and _directional_m30_confirmation(df_confirmation, Direction.BUY))
        or (shift_sell and _directional_m30_confirmation(df_confirmation, Direction.SELL))
    )
    m15_buying = float(bar["close"]) > float(bar["open"])
    m15_selling = float(bar["close"]) < float(bar["open"])
    m30_bar = df_confirmation.iloc[-1] if df_confirmation is not None and not df_confirmation.empty else None
    m30_same_direction_reaction = bool(
        m30_bar is not None and (
            (m15_buying and float(m30_bar["close"]) > float(m30_bar["open"]))
            or (m15_selling and float(m30_bar["close"]) < float(m30_bar["open"]))
        )
    )
    h4_model_candidate = (
        (config.h4_m30_enabled and (m30_structural or m30_same_direction_reaction))
        or (config.h4_h2_gap_enabled and (shift_buy or shift_sell))
    )
    if not is_london and not is_ny and not h4_model_candidate:
        return None

    atr = float(bar.get("atr", 0.0) or 0.0)
    if atr <= 0:
        atr = abs(float(bar["high"]) - float(bar["low"]))
        if atr <= 0:
            return None

    # Extract HTF MSNR Zones (H4 and D1)
    htf_zones: list[MSNRZone] = []
    # A level retested by the latest closed HTF candle is still fresh at the
    # decision point if no earlier bar revisited it. Measure freshness from
    # history ending before that retest candle to prevent look-ahead/rejection.
    h4_history = df_higher.iloc[:-1] if df_higher is not None and len(df_higher) > 5 else df_higher
    d1_history = df_daily.iloc[:-1] if df_daily is not None and len(df_daily) > 5 else df_daily
    if h4_history is not None and len(h4_history) >= 5:
        htf_zones.extend(extract_msnr_zones(h4_history, "H4", max_lookback=100))
    if d1_history is not None and len(d1_history) >= 5:
        htf_zones.extend(extract_msnr_zones(d1_history, "D1", max_lookback=60))

    if config.require_fresh_msnr:
        htf_zones = [z for z in htf_zones if z.fresh]

    setup: Optional[AlchemistSetup] = None

    # Try Model 1: London Killzone Asian Sweep
    if config.london_sweep_enabled:
        setup = evaluate_london_killzone_setup(
            bar=bar,
            df_entry=df_entry,
            current_time_utc=current_time_utc,
            config=config,
            htf_zones=htf_zones,
            point=point,
        )

    # Try Model 2: New York 9:00 AM NY CRT (if Model 1 didn't trigger)
    if setup is None and config.new_york_crt_enabled:
        setup = evaluate_new_york_crt_setup(
            bar=bar,
            df_setup_h1=df_setup,
            current_time_utc=current_time_utc,
            config=config,
            htf_zones=htf_zones,
            point=point,
        )

    # Model 3: H4 MSNR + M30 CRT/structure confirmation, with M15 shift entry.
    if setup is None and config.h4_m30_enabled:
        setup = evaluate_h4_m30_crt_setup(
            bar=bar, df_entry=df_entry, df_h4=df_higher, df_m30=df_confirmation,
            htf_zones=htf_zones, config=config, point=point,
        )

    # Model 4: H4 SNR/FVG refined by H2 imbalance, entered on M15 retest/MSS.
    if setup is None and config.h4_h2_gap_enabled:
        setup = evaluate_h4_h2_gap_setup(
            bar=bar, df_entry=df_entry, df_h4=df_higher, df_h2=df_gap,
            htf_zones=htf_zones, config=config, point=point,
        )

    if setup is None:
        return None

    # The guide's top-down storyline begins with monthly/weekly context, then
    # uses daily and H4 as confirmation/roadblock frames. Keep this optional so
    # the experiment can compare the unfiltered and aligned variants.
    # Rule 4: Trade with the Daily & H4 bias and check adverse momentum displacement
    if config.require_htf_alignment:
        daily_bias = _directional_bias(df_daily)
        if daily_bias is not None and daily_bias != setup.direction:
            return None
        h4_bias = _directional_bias(df_higher)
        if h4_bias is not None and h4_bias != setup.direction:
            return None
        if _is_adverse_htf_expansion(df_higher, setup.direction):
            return None
        aligned = list(setup.confluences)
        if daily_bias == setup.direction:
            aligned.append("D1_ALIGNMENT")
        if h4_bias == setup.direction:
            aligned.append("H4_ALIGNMENT")
        setup = replace(setup, confluences=tuple(aligned))

    if config.require_htf_poi and not any(item.startswith("MSNR_") for item in setup.confluences):
        return None
    m15_patterns = _m15_entry_patterns(df_entry, setup.direction, atr)
    if config.require_m15_structure_shift and "M15_STRUCTURE_SHIFT" not in m15_patterns:
        return None
    if config.require_m15_entry_pattern and setup.model == "NY_H1_CRT":
        if not any(item in m15_patterns for item in ("M15_ORDER_BLOCK", "M15_MSNR_FLIP", "M15_QM")):
            return None
    if m15_patterns:
        setup = replace(setup, confluences=tuple(dict.fromkeys((*setup.confluences, *m15_patterns))))

    # Verify confluences threshold
    min_conf = config.min_confluences_after_loss if consecutive_losses > 0 else config.min_confluences
    if len(setup.confluences) < min_conf:
        return None

    # Round to instrument digits
    digits = max(0, int(round(-math.log10(point)))) if point < 1 else 2
    return AlchemistSetup(
        direction=setup.direction,
        entry_price=round(setup.entry_price, digits),
        sl_price=round(setup.sl_price, digits),
        tp_price=round(setup.tp_price, digits),
        model=setup.model,
        poi_name=setup.poi_name,
        poi_price=round(setup.poi_price, digits),
        liquidity_name=setup.liquidity_name,
        liquidity_price=round(setup.liquidity_price, digits),
        confluences=setup.confluences,
        rr=round(setup.rr, 2),
    )
