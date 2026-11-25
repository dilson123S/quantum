"""Closed-bar market context recorded alongside every observed signal.

This layer describes the market only.  It never approves, rejects, or resizes
a trade, so each proposed rule can be evaluated out of sample first.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from core.constants import Direction, SwingType
from core.models import StructureAnalysis
from strategy.smc import latest_fair_value_gap, latest_order_block


def _number(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _session_label(timestamp: pd.Timestamp) -> str:
    hour = timestamp.hour
    # MT5 Broker timestamps are Eastern European / Cyprus (UTC+3)
    # Asia (Tokyo): 00:00 - 10:00 Broker (21:00 - 07:00 UTC)
    # London:       10:00 - 15:00 Broker (07:00 - 12:00 UTC)
    # New York:     15:00 - 20:00 Broker (12:00 - 17:00 UTC)
    # NY Close:     20:00 - 23:00 Broker (17:00 - 20:00 UTC)
    if hour < 10:
        return "ASIA"
    if hour < 15:
        return "LONDON"
    if hour < 20:
        return "NEW_YORK"
    if hour < 23:
        return "NEW_YORK_CLOSE"
    return "OFF_SESSION"


def classify_regime(
    df: pd.DataFrame, min_atr_ratio: float, max_atr_ratio: float, adx_min: float,
    min_bb_width_ratio: float = 0.90, max_range_bb_width_ratio: float = 1.05,
) -> Dict[str, Any]:
    """Return an explainable volatility/trend regime from the final closed bar."""
    last = df.iloc[-1]
    atr_ratio, adx = _number(last.get("atr_ratio")), _number(last.get("adx"))
    bb_width_ratio = _number(last.get("bb_width_ratio"))
    if atr_ratio is not None and atr_ratio >= max_atr_ratio:
        label = "HIGH_VOLATILITY"
    elif atr_ratio is not None and atr_ratio < min_atr_ratio:
        label = "LOW_VOLATILITY"
    elif adx is not None and adx >= adx_min and (bb_width_ratio is None or bb_width_ratio >= min_bb_width_ratio):
        label = "TRENDING"
    elif adx is not None and adx < adx_min and (bb_width_ratio is None or bb_width_ratio <= max_range_bb_width_ratio):
        label = "RANGING"
    else:
        label = "TRANSITIONAL"
    return {"label": label, "atr_ratio": atr_ratio, "adx": adx, "bb_width_ratio": bb_width_ratio}


def _previous_day_levels(df: pd.DataFrame, current_time: pd.Timestamp) -> Dict[str, Optional[float]]:
    times = pd.to_datetime(df["time"], utc=True)
    completed = df.loc[times.dt.normalize() < current_time.normalize()]
    if completed.empty:
        return {"high": None, "low": None, "date": None}
    last_date = pd.to_datetime(completed["time"], utc=True).dt.normalize().max()
    previous_day = completed.loc[pd.to_datetime(completed["time"], utc=True).dt.normalize() == last_date]
    return {"high": _number(previous_day["high"].max()), "low": _number(previous_day["low"].min()), "date": last_date.date().isoformat()}


def _asia_levels(df: pd.DataFrame, current_time: pd.Timestamp) -> Dict[str, Any]:
    times = pd.to_datetime(df["time"], utc=True)
    # Broker UTC+3: Asia session is 00:00 to 09:59 Broker time (21:00 to 07:00 UTC)
    asia = df.loc[(times.dt.normalize() == current_time.normalize()) & (times.dt.hour < 10)]
    complete = current_time.hour >= 10
    if asia.empty:
        return {"high": None, "low": None, "complete": complete}
    return {"high": _number(asia["high"].max()), "low": _number(asia["low"].min()), "complete": complete}


def _equal_levels(structure: StructureAnalysis, atr: Optional[float], tolerance_atr: float) -> Dict[str, Any]:
    tolerance = (atr or 0.0) * tolerance_atr
    result: Dict[str, Any] = {"high": None, "low": None, "tolerance": tolerance}
    for key, swing_type in (("high", SwingType.SWING_HIGH), ("low", SwingType.SWING_LOW)):
        swings = [s for s in structure.swings if s.swing_type == swing_type]
        if len(swings) >= 2 and tolerance > 0 and abs(swings[-2].price - swings[-1].price) <= tolerance:
            result[key] = _number((swings[-2].price + swings[-1].price) / 2.0)
    return result


def _fibonacci_context(structure: StructureAnalysis, close: Optional[float], atr: Optional[float], zone_atr: float) -> Dict[str, Any]:
    """Describe the latest confirmed opposite-swing retracement, if available."""
    swings = sorted(structure.swings, key=lambda swing: swing.bar_index)
    pair = next(((a, b) for a, b in zip(reversed(swings[:-1]), reversed(swings[1:])) if a.swing_type != b.swing_type and a.price != b.price), None)
    if pair is None:
        return {"available": False}
    start, end = pair
    high, low = max(start.price, end.price), min(start.price, end.price)
    span = high - low
    if span <= 0:
        return {"available": False}
    direction = "BUY" if end.swing_type == SwingType.SWING_HIGH else "SELL"
    levels = {"0.500": high - span * .5 if direction == "BUY" else low + span * .5, "0.618": high - span * .618 if direction == "BUY" else low + span * .618}
    distances = {name: (abs((close or price) - price) / atr if atr and atr > 0 else None) for name, price in levels.items()}
    nearest = min(distances, key=lambda name: distances[name] if distances[name] is not None else float("inf"))
    return {"available": True, "direction": direction, "anchor_start": _number(start.price), "anchor_end": _number(end.price), "levels": {name: _number(price) for name, price in levels.items()}, "nearest_level": nearest, "nearest_distance_atr": _number(distances[nearest]), "in_zone": bool(distances[nearest] is not None and distances[nearest] <= zone_atr)}


def build_decision_context(
    df: pd.DataFrame, structure: StructureAnalysis, direction: Direction, *, min_atr_ratio: float,
    max_atr_ratio: float, adx_min: float, equal_level_tolerance_atr: float = .15, fib_zone_atr: float = .15,
    smc_max_age_bars: int = 40, smc_min_gap_atr: float = .10, sweep_min_penetration_atr: float = .05,
    min_bb_width_ratio: float = .90, max_range_bb_width_ratio: float = 1.05,
) -> Dict[str, Any]:
    """Build serialisable context using only bars MT5 has already closed."""
    if df is None or df.empty:
        raise ValueError("Decision context requires at least one closed bar")
    last = df.iloc[-1]
    current_time, close, atr = _timestamp(last["time"]), _number(last.get("close")), _number(last.get("atr"))
    previous_day, asia = _previous_day_levels(df, current_time), _asia_levels(df, current_time)
    equal = _equal_levels(structure, atr, equal_level_tolerance_atr)
    levels: List[Dict[str, Any]] = []
    for name, price, side in (("PREVIOUS_DAY_HIGH", previous_day["high"], "HIGH"), ("PREVIOUS_DAY_LOW", previous_day["low"], "LOW"), ("ASIA_HIGH", asia["high"] if asia["complete"] else None, "HIGH"), ("ASIA_LOW", asia["low"] if asia["complete"] else None, "LOW"), ("LAST_SWING_HIGH", _number(structure.last_swing_high.price) if structure.last_swing_high else None, "HIGH"), ("LAST_SWING_LOW", _number(structure.last_swing_low.price) if structure.last_swing_low else None, "LOW"), ("EQUAL_HIGH", equal["high"], "HIGH"), ("EQUAL_LOW", equal["low"], "LOW")):
        if price is not None:
            distance = abs(close - price) / atr if close is not None and atr and atr > 0 else None
            levels.append({"name": name, "price": price, "side": side, "distance_atr": _number(distance)})
    nearest = min(levels, key=lambda level: level["distance_atr"] if level["distance_atr"] is not None else float("inf")) if levels else None
    sweep = {"detected": False, "direction": None, "level": None, "penetration_atr": None}
    major_sweep_level_names = {
        "PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW",
        "ASIA_HIGH", "ASIA_LOW",
        "EQUAL_HIGH", "EQUAL_LOW",
    }
    candidate_sweeps = []
    for level in levels:
        if level["name"] not in major_sweep_level_names:
            continue
        candle_range = (float(last["high"]) - float(last["low"])) if atr else 0.0
        open_price = float(last.get("open", close)) if close is not None else 0.0

        if level["side"] == "HIGH" and atr:
            penetration = (float(last["high"]) - level["price"]) / atr
            upper_wick = float(last["high"]) - max(open_price, float(close)) if close is not None else 0.0
            wick_ratio = (upper_wick / candle_range) if candle_range > 0 else 0.0

            if penetration >= sweep_min_penetration_atr and close is not None and close < level["price"] and wick_ratio >= 0.20:
                candidate_sweeps.append({
                    "detected": True, "direction": "SELL", "level": level["name"],
                    "penetration_atr": _number(penetration), "distance_atr": level["distance_atr"]
                })
        elif level["side"] == "LOW" and atr:
            penetration = (level["price"] - float(last["low"])) / atr
            lower_wick = min(open_price, float(close)) - float(last["low"]) if close is not None else 0.0
            wick_ratio = (lower_wick / candle_range) if candle_range > 0 else 0.0

            if penetration >= sweep_min_penetration_atr and close is not None and close > level["price"] and wick_ratio >= 0.20:
                candidate_sweeps.append({
                    "detected": True, "direction": "BUY", "level": level["name"],
                    "penetration_atr": _number(penetration), "distance_atr": level["distance_atr"]
                })

    if candidate_sweeps:
        best_sweep = min(candidate_sweeps, key=lambda s: s["distance_atr"] if s["distance_atr"] is not None else float("inf"))
        sweep = {
            "detected": True,
            "direction": best_sweep["direction"],
            "level": best_sweep["level"],
            "penetration_atr": best_sweep["penetration_atr"]
        }
    fvg = latest_fair_value_gap(df, smc_max_age_bars, smc_min_gap_atr)
    order_block = latest_order_block(df, structure, smc_max_age_bars)
    smc_alignment = {
        "sweep_aligned": bool(sweep["detected"] and sweep["direction"] == direction.value),
        "fvg_aligned": bool(fvg.get("available") and fvg.get("direction") == direction.value),
        "order_block_aligned": bool(order_block.get("available") and order_block.get("direction") == direction.value),
    }
    return {
        "version": "shadow-context-v2", "bar_time": current_time.isoformat(), "candidate_direction": direction.value,
        "regime": classify_regime(df, min_atr_ratio, max_atr_ratio, adx_min, min_bb_width_ratio, max_range_bb_width_ratio),
        "session": {"label": _session_label(current_time)},
        "liquidity": {"previous_day": previous_day, "asia": asia, "equal_levels": equal, "nearest_level": nearest, "sweep": sweep},
        "close": close,
        "atr": atr,
        "fibonacci": _fibonacci_context(structure, close, atr, fib_zone_atr),
        "smc": {"fair_value_gap": fvg, "order_block": order_block, **smc_alignment},
    }
