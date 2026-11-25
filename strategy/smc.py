"""Closed-bar Smart Money Concepts context: sweeps, FVGs and order blocks.

These detectors describe confluence. They make no prediction and must be
validated OOS before a profile makes them an active entry filter.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd

from core.constants import Direction
from core.models import StructureAnalysis


def _num(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None and pd.notna(value) else None
    except (TypeError, ValueError):
        return None


def latest_fair_value_gap(df: pd.DataFrame, max_age_bars: int, min_gap_atr: float) -> Dict[str, Any]:
    """Find the newest unfilled three-candle imbalance using closed bars."""
    if df is None or len(df) < 3:
        return {"available": False}
    final = len(df) - 1
    start = max(2, final - max_age_bars + 1)
    for index in range(final, start - 1, -1):
        left, current = df.iloc[index - 2], df.iloc[index]
        atr = _num(current.get("atr")) or 0.0
        min_gap = atr * min_gap_atr
        direction = None
        lower = upper = None
        if float(current["low"]) - float(left["high"]) >= min_gap:
            direction, lower, upper = Direction.BUY, float(left["high"]), float(current["low"])
            # A later close below the lower boundary fully invalidates it.
            active = not (df.iloc[index + 1:]["close"] < lower).any()
        elif float(left["low"]) - float(current["high"]) >= min_gap:
            direction, lower, upper = Direction.SELL, float(current["high"]), float(left["low"])
            active = not (df.iloc[index + 1:]["close"] > upper).any()
        else:
            continue
        if not active:
            continue
        close = float(df.iloc[-1]["close"])
        return {
            "available": True, "direction": direction.value, "lower": lower, "upper": upper,
            "bar_index": index, "age_bars": final - index,
            "in_zone": lower <= close <= upper,
        }
    return {"available": False}


def latest_order_block(df: pd.DataFrame, structure: StructureAnalysis, max_age_bars: int) -> Dict[str, Any]:
    """Last opposite candle before the newest confirmed BOS, if still valid."""
    if df is None or df.empty or not structure.bos_list:
        return {"available": False}
    bos = max(structure.bos_list, key=lambda item: item.bar_index)
    final = len(df) - 1
    if final - bos.bar_index > max_age_bars:
        return {"available": False}
    lower_bound = max(int(bos.swing_broken.bar_index), bos.bar_index - max_age_bars)
    for index in range(min(bos.bar_index - 1, final - 1), lower_bound - 1, -1):
        candle = df.iloc[index]
        bearish = float(candle["close"]) < float(candle["open"])
        bullish = float(candle["close"]) > float(candle["open"])
        if (bos.direction == Direction.BUY and bearish) or (bos.direction == Direction.SELL and bullish):
            lower, upper = float(candle["low"]), float(candle["high"])
            later = df.iloc[index + 1:]
            invalidated = (later["close"] < lower).any() if bos.direction == Direction.BUY else (later["close"] > upper).any()
            if invalidated:
                return {"available": False, "invalidated": True}
            close = float(df.iloc[-1]["close"])
            return {
                "available": True, "direction": bos.direction.value, "lower": lower, "upper": upper,
                "bar_index": index, "age_bars": final - index, "in_zone": lower <= close <= upper,
            }
    return {"available": False}
