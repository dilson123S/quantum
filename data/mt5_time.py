"""Normalize MT5 bar times for brokers that expose server-wall timestamps.

MT5's Python docs specify UTC timestamps. The connected IC Markets demo
terminal empirically returns epoch values that track its UTC+2/UTC+3 chart
clock, so apply this correction only to IC Markets server names.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

_NEW_YORK = ZoneInfo("America/New_York")


def uses_icmarkets_server_clock(server: str | None) -> bool:
    return "icmarkets" in str(server or "").casefold()


def _icmarkets_offset_hours(utc_reference: datetime) -> int:
    """IC server offset: UTC+2 in US standard time, UTC+3 during US DST."""
    ny_reference = utc_reference.astimezone(_NEW_YORK)
    return 3 if ny_reference.dst() else 2


def to_mt5_query_time(value: datetime, server: str | None) -> datetime:
    """Map a UTC range boundary to the IC server-wall epoch expected by MT5."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    if not uses_icmarkets_server_clock(server):
        return value
    offset = _icmarkets_offset_hours(value)
    return value + timedelta(hours=offset)


def normalize_mt5_bar_times(values: Iterable, server: str | None) -> pd.Series:
    """Convert MT5 epoch bar times into UTC, correcting IC server-wall time."""
    numeric = pd.to_numeric(pd.Series(values).reset_index(drop=True), errors="coerce")
    utc = pd.to_datetime(numeric, unit="s", utc=True)
    if not uses_icmarkets_server_clock(server):
        return utc

    # The epoch encodes the server's wall clock. Guess UTC-2 first to identify
    # which US DST regime applies, then subtract the matching IC offset.
    offsets = []
    for stamp in utc:
        candidate = (stamp - pd.Timedelta(hours=2)).to_pydatetime()
        offsets.append(_icmarkets_offset_hours(candidate))
    return utc - pd.to_timedelta(offsets, unit="h")
