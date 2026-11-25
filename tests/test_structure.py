"""Unit tests for market structure indicator."""
import pytest
import pandas as pd
import numpy as np

from indicators.structure import (
    find_swing_highs, find_swing_lows, classify_swings,
    detect_bos, detect_choch, get_market_structure
)
from indicators.volatility import atr
from core.constants import SwingClassification, Direction


@pytest.fixture
def structure_ohlcv_df():
    """Create OHLCV DataFrame with explicit peaks and valleys for structure testing."""
    # Pattern: Low (10), High (30), Higher Low (18), Higher High (45), etc.
    closes = [10.0, 15.0, 20.0, 30.0, 25.0, 18.0, 24.0, 35.0, 45.0, 40.0, 32.0, 38.0, 50.0]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    opens = closes.copy()

    df = pd.DataFrame({
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'tick_volume': [1000] * len(closes),
    })
    return df


def test_find_swings(structure_ohlcv_df):
    df = structure_ohlcv_df
    sh = find_swing_highs(df, lookback=2, confirmation=1)
    sl = find_swing_lows(df, lookback=2, confirmation=1)

    assert isinstance(sh, list)
    assert isinstance(sl, list)
    if sh:
        assert sh[0].price > 0
    if sl:
        assert sl[0].price > 0


def test_classify_swings(structure_ohlcv_df):
    df = structure_ohlcv_df
    sh = find_swing_highs(df, lookback=2, confirmation=1)
    sl = find_swing_lows(df, lookback=2, confirmation=1)

    c_sh, c_sl = classify_swings(sh, sl)
    assert len(c_sh) == len(sh)
    assert len(c_sl) == len(sl)

    # Classifications should be HH/LH and HL/LL
    for s in c_sh:
        assert s.classification in (SwingClassification.HH, SwingClassification.LH, SwingClassification.UNCLASSIFIED)
    for s in c_sl:
        assert s.classification in (SwingClassification.HL, SwingClassification.LL, SwingClassification.UNCLASSIFIED)


def test_detect_bos_and_choch(structure_ohlcv_df):
    df = structure_ohlcv_df
    atr_s = atr(df, period=5)

    sh = find_swing_highs(df, lookback=2, confirmation=1)
    sl = find_swing_lows(df, lookback=2, confirmation=1)
    c_sh, c_sl = classify_swings(sh, sl)

    bos = detect_bos(c_sh, c_sl, df, atr_s, min_distance_atr=0.01)
    assert isinstance(bos, list)

    choch = detect_choch(c_sh, c_sl, df, atr_s, min_distance_atr=0.01)
    assert isinstance(choch, list)


def test_get_market_structure(structure_ohlcv_df):
    df = structure_ohlcv_df
    atr_s = atr(df, period=5)

    analysis = get_market_structure(df, atr_s, swing_lookback=2, swing_confirmation=1)
    assert analysis is not None
    assert isinstance(analysis.swings, list)
    assert isinstance(analysis.bos_list, list)
    assert isinstance(analysis.choch_list, list)
