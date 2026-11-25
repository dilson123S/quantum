"""Rule-based MSNR storyline model based on MR.R's public Alchemist lessons.

This is deliberately separate from the PDF-derived CRT/M15 models in
``strategy.alchemist``.  The source material is discretionary, so this module
uses explicit OHLC rules that can be replayed without chart hindsight:

* a closed D1 rejection of an MSNR A/V level followed by a closed H4 structure
  break starts the active daily storyline; a later confirmed opposite event
  replaces it;
* an H4 rejection in that direction followed by an H1 close beyond the recent
  H1 candle bodies confirms an entry;
* the stop sits beyond the H4 rejection candle and the target is the nearest
  opposing MSNR zone or prior D1/W1/MN1/Asian liquidity level.

Those translations are engineering choices, not rules claimed verbatim by the
instructor.  The strategy is unvalidated and is opt-in through AlchemistConfig.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
from typing import Optional

import numpy as np
import pandas as pd
from collections import defaultdict

from core.config_loader import AlchemistConfig
from core.constants import Direction
from strategy.alchemist import AlchemistSetup, MSNRZone, extract_msnr_zones


_TF_DELTA = {
    "H1": pd.Timedelta(hours=1),
    "H4": pd.Timedelta(hours=4),
    "D1": pd.Timedelta(days=1),
    "W1": pd.Timedelta(days=7),
}


@dataclass(frozen=True)
class _StoryEvent:
    direction: Direction
    parent_time: pd.Timestamp
    confirmed_time: pd.Timestamp
    level: float


@dataclass(frozen=True)
class _Rejection:
    index: int
    zone: MSNRZone
    close_time: pd.Timestamp


_STORY_CACHE: OrderedDict[tuple, Optional[_StoryEvent]] = OrderedDict()
_STORY_CACHE_LIMIT = 256
_REJECTION_CACHE: OrderedDict[tuple, tuple[_Rejection, ...]] = OrderedDict()
_REJECTION_CACHE_LIMIT = 512
_PARENT_REJECTIONS_CACHE: OrderedDict[tuple, tuple[tuple[int, MSNRZone], ...]] = OrderedDict()
_PARENT_REJECTIONS_CACHE_LIMIT = 512


def _utc_times(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["time"], utc=True, errors="coerce")


def _duration(frame: pd.DataFrame, timeframe: str, index: int) -> pd.Timedelta:
    if timeframe == "MN1":
        start = pd.Timestamp(_utc_times(frame).iloc[index])
        return pd.Timestamp(start + pd.DateOffset(months=1)) - start
    return _TF_DELTA[timeframe]


def _closed_frame(
    frame: Optional[pd.DataFrame], timeframe: str, decision_time: pd.Timestamp,
) -> Optional[pd.DataFrame]:
    if frame is None or frame.empty or "time" not in frame:
        return None
    decision = pd.Timestamp(decision_time)
    decision = decision.tz_localize("UTC") if decision.tzinfo is None else decision.tz_convert("UTC")
    if timeframe == "MN1":
        times = _utc_times(frame)
        closes = times.map(lambda t: t + pd.DateOffset(months=1) if pd.notna(t) else pd.NaT)
        idx = int(np.searchsorted(closes.to_numpy(dtype="datetime64[ns]"), decision.tz_localize(None).to_datetime64(), side="right"))
    else:
        times_ns = pd.to_datetime(frame["time"], utc=True).to_numpy(dtype="datetime64[ns]")
        cutoff_ns = (decision - _TF_DELTA[timeframe]).tz_localize(None).to_datetime64()
        idx = int(np.searchsorted(times_ns, cutoff_ns, side="right"))
    if idx <= 0:
        return None
    return frame.iloc[:idx].reset_index(drop=True)


def _close_time(frame: pd.DataFrame, timeframe: str, index: int) -> pd.Timestamp:
    stamp = pd.Timestamp(_utc_times(frame).iloc[index])
    return stamp + _duration(frame, timeframe, index)


def _is_rejection(bar: pd.Series, zone: MSNRZone) -> bool:
    high, low = float(bar["high"]), float(bar["low"])
    open_price, close = float(bar["open"]), float(bar["close"])
    touches = low <= zone.price + zone.width and high >= zone.price - zone.width
    if not touches:
        return False
    if zone.direction == Direction.BUY:
        return close > zone.price and close > open_price
    return close < zone.price and close < open_price


def _breaks_structure(frame: pd.DataFrame, index: int, direction: Direction, lookback: int) -> bool:
    if index < lookback:
        return False
    bar = frame.iloc[index]
    prior = frame.iloc[index - lookback:index]
    close, open_price = float(bar["close"]), float(bar["open"])
    if direction == Direction.BUY:
        return close > float(prior["high"].max()) and close > open_price
    return close < float(prior["low"].min()) and close < open_price


def _story_events(
    parent: Optional[pd.DataFrame], child: Optional[pd.DataFrame],
    parent_tf: str, child_tf: str, *, parent_limit: int, child_window: int = 8,
) -> list[_StoryEvent]:
    if parent is None or child is None or len(parent) < 6 or len(child) < 8:
        return []
    parent = parent.tail(parent_limit).reset_index(drop=True)
    child = child.tail(max(parent_limit * 8, 300)).reset_index(drop=True)
    parent_times = _utc_times(parent)
    child_start_ns = _utc_times(child).to_numpy(dtype="datetime64[ns]").astype(np.int64)
    child_duration = _TF_DELTA.get(child_tf)
    if child_duration is None:
        # The used child frames are fixed-duration H4/D1/W1; keep a general
        # fallback for future models that use calendar-month bars.
        child_times = _utc_times(child)
        child_close_ns = np.asarray([
            (child_times.iloc[index] + _duration(child, child_tf, index)).value
            for index in range(len(child))
        ], dtype=np.int64)
    else:
        child_close_ns = child_start_ns + child_duration.value
    candidates = _parent_rejection_candidates(parent, parent_tf, parent_limit)

    # Parent-frame rejections are cached while the child timeframe advances.
    # This avoids re-scanning hundreds of D1 candles at each H4 close.
    for index, zone in candidates:
        rejection_close = parent_times.iloc[index] + _duration(parent, parent_tf, index)
        child_start = int(np.searchsorted(child_close_ns, rejection_close.value, side="left"))
        child_end = min(child_start + child_window, len(child))
        for child_index in range(child_start, child_end):
            if not _breaks_structure(child, child_index, zone.direction, lookback=3):
                continue
            return [_StoryEvent(
                direction=zone.direction,
                parent_time=pd.Timestamp(parent_times.iloc[index]),
                confirmed_time=pd.Timestamp(child_close_ns[child_index], unit="ns", tz="UTC"),
                level=zone.price,
            )]
    return []


def _parent_rejection_candidates(
    parent: pd.DataFrame, timeframe: str, parent_limit: int,
) -> tuple[tuple[int, MSNRZone], ...]:
    key = (timeframe, parent_limit, _frame_signature(parent))
    if key in _PARENT_REJECTIONS_CACHE:
        _PARENT_REJECTIONS_CACHE.move_to_end(key)
        return _PARENT_REJECTIONS_CACHE[key]

    zones = extract_msnr_zones(parent, timeframe, max_lookback=parent_limit)
    opens = pd.to_numeric(parent["open"], errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(parent["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(parent["low"], errors="coerce").to_numpy(dtype=float)
    closes = pd.to_numeric(parent["close"], errors="coerce").to_numpy(dtype=float)
    matches: dict[int, list[MSNRZone]] = defaultdict(list)
    for zone in zones:
        start = max(3, zone.bar_index + 2)
        end = min(len(parent), zone.bar_index + 102)
        if start >= end:
            continue
        section = slice(start, end)
        touches = (lows[section] <= zone.price + zone.width) & (highs[section] >= zone.price - zone.width)
        if zone.direction == Direction.BUY:
            rejected = touches & (closes[section] > zone.price) & (closes[section] > opens[section])
        else:
            rejected = touches & (closes[section] < zone.price) & (closes[section] < opens[section])
        for offset in np.flatnonzero(rejected):
            matches[start + int(offset)].append(zone)

    candidates = [
        (index, min(matching, key=lambda zone: abs(zone.price - closes[index])))
        for index, matching in sorted(matches.items(), reverse=True)
    ]

    result = tuple(candidates)
    _PARENT_REJECTIONS_CACHE[key] = result
    if len(_PARENT_REJECTIONS_CACHE) > _PARENT_REJECTIONS_CACHE_LIMIT:
        _PARENT_REJECTIONS_CACHE.popitem(last=False)
    return result


def _latest_story(
    parent: Optional[pd.DataFrame], child: Optional[pd.DataFrame],
    parent_tf: str, child_tf: str, *, parent_limit: int,
) -> Optional[_StoryEvent]:
    key = (parent_tf, child_tf, parent_limit, _frame_signature(parent), _frame_signature(child))
    if key in _STORY_CACHE:
        _STORY_CACHE.move_to_end(key)
        return _STORY_CACHE[key]
    events = _story_events(parent, child, parent_tf, child_tf, parent_limit=parent_limit)
    result = max(events, key=lambda event: event.confirmed_time) if events else None
    _STORY_CACHE[key] = result
    if len(_STORY_CACHE) > _STORY_CACHE_LIMIT:
        _STORY_CACHE.popitem(last=False)
    return result


def _select_entry_story(
    daily: Optional[_StoryEvent], weekly: Optional[_StoryEvent], monthly: Optional[_StoryEvent],
    *, allow_higher_fallback: bool,
) -> tuple[Optional[_StoryEvent], str]:
    """Prefer the daily storyline; optionally fall back to weekly, then monthly."""
    if daily is not None:
        return daily, "D1_STORYLINE"
    if allow_higher_fallback and weekly is not None:
        return weekly, "W1_STORYLINE_FALLBACK"
    if allow_higher_fallback and monthly is not None:
        return monthly, "MN1_STORYLINE_FALLBACK"
    return None, ""


def _frame_signature(frame: Optional[pd.DataFrame]) -> tuple:
    if frame is None or frame.empty:
        return (0,)
    first_time = pd.Timestamp(frame["time"].iloc[0])
    last_time = pd.Timestamp(frame["time"].iloc[-1])
    first_time = first_time.tz_localize("UTC") if first_time.tzinfo is None else first_time.tz_convert("UTC")
    last_time = last_time.tz_localize("UTC") if last_time.tzinfo is None else last_time.tz_convert("UTC")
    last = frame.iloc[-1]
    return (
        len(frame), first_time.value, last_time.value,
        *(round(float(last[column]), 8) for column in ("open", "high", "low", "close")),
    )


def _recent_h4_rejections(
    frame: Optional[pd.DataFrame], direction: Direction, after: pd.Timestamp,
) -> tuple[_Rejection, ...]:
    if frame is None or len(frame) < 8:
        return ()
    key = (_frame_signature(frame), direction.value, str(after))
    if key in _REJECTION_CACHE:
        _REJECTION_CACHE.move_to_end(key)
        return _REJECTION_CACHE[key]
    frame_offset = max(0, len(frame) - 180)
    frame = frame.tail(180).reset_index(drop=True)
    indexed_zones = extract_msnr_zones(frame, "H4", max_lookback=180)
    result: list[_Rejection] = []
    # A rejection older than these bars cannot produce the H1 confirmation
    # accepted below (which expires after eight hours); skip stale candidates.
    for index in range(len(frame) - 1, max(2, len(frame) - 4), -1):
        close_time = _close_time(frame, "H4", index)
        if close_time < after:
            break
        zones = [
            zone for zone in indexed_zones
            if zone.direction == direction
            and zone.bar_index <= index - 2
            and zone.bar_index >= index - 101
            and _is_rejection(frame.iloc[index], zone)
        ]
        if zones:
            zone = min(zones, key=lambda item: abs(item.price - float(frame.iloc[index]["close"])))
            # Keep indices relative to the caller's full point-in-time frame.
            # The caller uses this index to read the rejection candle and ATR
            # from the untruncated H4 history.
            result.append(_Rejection(index=frame_offset + index, zone=zone, close_time=close_time))
    cached = tuple(result)
    _REJECTION_CACHE[key] = cached
    if len(_REJECTION_CACHE) > _REJECTION_CACHE_LIMIT:
        _REJECTION_CACHE.popitem(last=False)
    return cached


def _atr(frame: pd.DataFrame, index: int, period: int = 14) -> float:
    start = max(0, index - period + 1)
    window = frame.iloc[start:index + 1]
    previous_close = pd.to_numeric(window["close"], errors="coerce").shift(1)
    ranges = pd.concat([
        pd.to_numeric(window["high"], errors="coerce") - pd.to_numeric(window["low"], errors="coerce"),
        (pd.to_numeric(window["high"], errors="coerce") - previous_close).abs(),
        (pd.to_numeric(window["low"], errors="coerce") - previous_close).abs(),
    ], axis=1).max(axis=1)
    value = float(ranges.mean()) if not ranges.empty else 0.0
    return value if value > 0 else float(window.iloc[-1]["high"] - window.iloc[-1]["low"])


def _targets(
    entry: float, direction: Direction,
    frames: list[tuple[Optional[pd.DataFrame], str]], h1: pd.DataFrame,
) -> list[tuple[float, str]]:
    candidates: list[tuple[float, str]] = []
    opposing = Direction.SELL if direction == Direction.BUY else Direction.BUY
    for frame, tf in frames:
        if frame is None or len(frame) < 3:
            continue
        for zone in extract_msnr_zones(frame, tf, max_lookback=min(120, len(frame))):
            if zone.direction != opposing:
                continue
            if direction == Direction.BUY and zone.price > entry:
                candidates.append((zone.price, f"{tf}_MSNR_RESISTANCE"))
            elif direction == Direction.SELL and zone.price < entry:
                candidates.append((zone.price, f"{tf}_MSNR_SUPPORT"))

    # ``frames`` order is H4, D1, W1, MN1; take the last completed candle
    # for prior-session liquidity. The caller has already removed open bars.
    for label, frame in (("PD", frames[1][0]), ("PW", frames[2][0]), ("PM", frames[3][0])):
        if frame is None or frame.empty:
            continue
        row = frame.iloc[-1]
        price = float(row["high"] if direction == Direction.BUY else row["low"])
        if (direction == Direction.BUY and price > entry) or (direction == Direction.SELL and price < entry):
            candidates.append((price, f"{label}_HIGH" if direction == Direction.BUY else f"{label}_LOW"))

    current_day = pd.Timestamp(_utc_times(h1).iloc[-1]).date()
    asia = h1.loc[
        (_utc_times(h1).dt.date == current_day) & (_utc_times(h1).dt.hour < 7)
    ]
    if not asia.empty:
        price = float(asia["high"].max() if direction == Direction.BUY else asia["low"].min())
        if (direction == Direction.BUY and price > entry) or (direction == Direction.SELL and price < entry):
            candidates.append((price, "ASIA_HIGH" if direction == Direction.BUY else "ASIA_LOW"))
    return candidates


def detect_msnr_storyline_setup(
    df_h1: Optional[pd.DataFrame], df_h4: Optional[pd.DataFrame],
    df_daily: Optional[pd.DataFrame], df_weekly: Optional[pd.DataFrame],
    df_monthly: Optional[pd.DataFrame], config: AlchemistConfig, *, point: float,
) -> Optional[AlchemistSetup]:
    """Return one H1 confirmation setup, or ``None`` when any rule is absent."""
    if any(frame is None for frame in (df_h1, df_h4, df_daily, df_weekly, df_monthly)) or point <= 0:
        return None
    assert df_h1 is not None and df_h4 is not None
    assert df_daily is not None and df_weekly is not None and df_monthly is not None
    if len(df_h1) < 20 or len(df_h4) < 8 or len(df_daily) < 10:
        return None

    h1_times = _utc_times(df_h1.tail(3))
    if h1_times.isna().any() or len(h1_times) < 3:
        return None
    median_gap = h1_times.diff().dropna().median()
    # Fail closed if someone points the H1 model at Bot E's current M15 stream.
    if median_gap < pd.Timedelta(minutes=50) or median_gap > pd.Timedelta(minutes=70):
        return None
    decision_time = h1_times.iloc[-1] + pd.Timedelta(hours=1)

    h1 = _closed_frame(df_h1, "H1", decision_time)
    h4 = _closed_frame(df_h4, "H4", decision_time)
    daily = _closed_frame(df_daily, "D1", decision_time)
    weekly = _closed_frame(df_weekly, "W1", decision_time)
    monthly = _closed_frame(df_monthly, "MN1", decision_time)
    if any(frame is None for frame in (h1, h4, daily, weekly, monthly)):
        return None
    assert h1 is not None and h4 is not None and daily is not None
    assert weekly is not None and monthly is not None

    daily_story = _latest_story(daily, h4, "D1", "H4", parent_limit=180)
    weekly_story = _latest_story(weekly, daily, "W1", "D1", parent_limit=120)
    monthly_story = _latest_story(monthly, weekly, "MN1", "W1", parent_limit=60)
    active_story, active_story_label = _select_entry_story(
        daily_story, weekly_story, monthly_story,
        allow_higher_fallback=bool(config.allow_higher_storyline_fallback),
    )
    if active_story is None:
        return None

    latest_h1 = h1.iloc[-1]
    h1_close_time = _close_time(h1, "H1", len(h1) - 1)
    body_lookback = max(1, int(config.h1_break_lookback))
    if len(h1) < body_lookback + 1:
        return None
    body_highs = h1.iloc[-body_lookback - 1:-1][["open", "close"]].max(axis=1)
    body_lows = h1.iloc[-body_lookback - 1:-1][["open", "close"]].min(axis=1)
    entry = float(latest_h1["close"])
    if active_story.direction == Direction.BUY:
        h1_break = float(latest_h1["close"]) > float(body_highs.max()) and entry > float(latest_h1["open"])
    else:
        h1_break = float(latest_h1["close"]) < float(body_lows.min()) and entry < float(latest_h1["open"])
    rejection = next((
        item for item in _recent_h4_rejections(h4, active_story.direction, active_story.confirmed_time)
        if h1_break
        and h1_close_time >= item.close_time
        and h1_times.iloc[-1] >= item.close_time
        and h1_times.iloc[-1] <= item.close_time + pd.Timedelta(hours=8)
    ), None)
    if rejection is None:
        return None
    h4_row = h4.iloc[rejection.index]

    h4_atr = _atr(h4, rejection.index)
    buffer = max(point * 2, h4_atr * float(config.stop_buffer_atr))
    if active_story.direction == Direction.BUY:
        stop = float(h4_row["low"]) - buffer
        risk = entry - stop
        if risk <= 0 or risk > h4_atr * float(config.max_stop_atr):
            return None
    else:
        stop = float(h4_row["high"]) + buffer
        risk = stop - entry
        if risk <= 0 or risk > h4_atr * float(config.max_stop_atr):
            return None

    candidates = _targets(
        entry, active_story.direction,
        [(h4, "H4"), (daily, "D1"), (weekly, "W1"), (monthly, "MN1")], h1,
    )
    candidates = sorted(candidates, key=lambda item: abs(item[0] - entry))
    if not candidates:
        return None
    target, liquidity_name = candidates[0]
    reward = target - entry if active_story.direction == Direction.BUY else entry - target
    rr = reward / risk if risk > 0 else 0.0
    if reward <= 0 or rr < float(config.minimum_rr):
        return None

    confluences = [active_story_label, "H4_MSNR_REJECTION", "H1_BODY_BREAK"]
    if weekly_story is not None and active_story_label != "W1_STORYLINE_FALLBACK":
        confluences.append("W1_STORYLINE_AGREES" if weekly_story.direction == active_story.direction else "W1_COUNTER_STORY")
    if monthly_story is not None and active_story_label != "MN1_STORYLINE_FALLBACK":
        confluences.append("MN1_STORYLINE_AGREES" if monthly_story.direction == active_story.direction else "MN1_COUNTER_STORY")
    confluences.append("PRIOR_LIQUIDITY_TARGET" if liquidity_name.startswith(("PD", "PW", "PM", "ASIA")) else "MSNR_TARGET")
    digits = max(0, int(round(-math.log10(point)))) if point < 1 else 2
    return AlchemistSetup(
        direction=active_story.direction,
        entry_price=round(entry, digits),
        sl_price=round(stop, digits),
        tp_price=round(target, digits),
        model="MSNR_STORYLINE_H4_H1",
        poi_name=rejection.zone.name,
        poi_price=round(rejection.zone.price, digits),
        liquidity_name=liquidity_name,
        liquidity_price=round(target, digits),
        confluences=tuple(confluences),
        rr=round(rr, 2),
    )
