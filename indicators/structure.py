"""Market structure analysis: Swings, BOS, CHoCH.

This is the most sophisticated indicator module. It implements:
- Swing High/Low detection with lookback and confirmation
- Swing classification: HH, HL, LH, LL
- Break of Structure (BOS) detection with noise filtering
- Change of Character (CHoCH / MSS) detection
"""
import pandas as pd
import numpy as np
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

from core.constants import SwingType, SwingClassification, StructureBreakType, Direction
from core.models import SwingPoint, StructureBreak, StructureAnalysis


def find_swing_highs(df: pd.DataFrame, lookback: int = 3, confirmation: int = 2) -> List[SwingPoint]:
    """Identify Swing Highs.
    
    A Swing High occurs when a bar's high is higher than the highs of 
    'lookback' bars on each side, confirmed by 'confirmation' bars after.
    
    Args:
        df: DataFrame with 'high' column
        lookback: Number of bars to look back and forward
        confirmation: Number of confirmed lower bars after the swing
    """
    highs = df['high'].values
    swings = []
    
    # We need lookback bars before and confirmation bars after
    for i in range(lookback, len(highs) - confirmation):
        is_swing = True
        
        # Check bars before: all must have lower highs
        for j in range(1, lookback + 1):
            if highs[i - j] >= highs[i]:
                is_swing = False
                break
        
        if not is_swing:
            continue
        
        # Check confirmation bars after: must have lower highs
        confirmed = 0
        for j in range(1, min(confirmation + lookback, len(highs) - i)):
            if highs[i + j] < highs[i]:
                confirmed += 1
            else:
                break
        
        if confirmed >= confirmation:
            swings.append(SwingPoint(
                index=len(swings),
                price=float(highs[i]),
                swing_type=SwingType.SWING_HIGH,
                classification=SwingClassification.UNCLASSIFIED,
                bar_index=i,
            ))
    
    return swings


def find_swing_lows(df: pd.DataFrame, lookback: int = 3, confirmation: int = 2) -> List[SwingPoint]:
    """Identify Swing Lows.
    
    A Swing Low occurs when a bar's low is lower than the lows of
    'lookback' bars on each side, confirmed by 'confirmation' bars after.
    """
    lows = df['low'].values
    swings = []
    
    for i in range(lookback, len(lows) - confirmation):
        is_swing = True
        
        for j in range(1, lookback + 1):
            if lows[i - j] <= lows[i]:
                is_swing = False
                break
        
        if not is_swing:
            continue
        
        confirmed = 0
        for j in range(1, min(confirmation + lookback, len(lows) - i)):
            if lows[i + j] > lows[i]:
                confirmed += 1
            else:
                break
        
        if confirmed >= confirmation:
            swings.append(SwingPoint(
                index=len(swings),
                price=float(lows[i]),
                swing_type=SwingType.SWING_LOW,
                classification=SwingClassification.UNCLASSIFIED,
                bar_index=i,
            ))
    
    return swings


def classify_swings(swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]) -> Tuple[List[SwingPoint], List[SwingPoint]]:
    """Classify swings as HH, HL, LH, LL by comparing with previous swing of same type.
    
    - Higher High (HH): current high > previous high
    - Lower High (LH): current high < previous high
    - Higher Low (HL): current low > previous low
    - Lower Low (LL): current low < previous low
    """
    # Classify highs
    classified_highs = []
    for i, sh in enumerate(swing_highs):
        if i == 0:
            classified_highs.append(sh.model_copy(update={'classification': SwingClassification.UNCLASSIFIED}))
        else:
            prev = swing_highs[i - 1]
            if sh.price > prev.price:
                classified_highs.append(sh.model_copy(update={'classification': SwingClassification.HH}))
            else:
                classified_highs.append(sh.model_copy(update={'classification': SwingClassification.LH}))
    
    # Classify lows
    classified_lows = []
    for i, sl in enumerate(swing_lows):
        if i == 0:
            classified_lows.append(sl.model_copy(update={'classification': SwingClassification.UNCLASSIFIED}))
        else:
            prev = swing_lows[i - 1]
            if sl.price > prev.price:
                classified_lows.append(sl.model_copy(update={'classification': SwingClassification.HL}))
            else:
                classified_lows.append(sl.model_copy(update={'classification': SwingClassification.LL}))
    
    return classified_highs, classified_lows


def detect_bos(swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
               df: pd.DataFrame, atr_series: pd.Series,
               min_distance_atr: float = 0.10, 
               close_confirmation: bool = True,
               max_swing_age: int = 100) -> List[StructureBreak]:
    """Detect Break of Structure (BOS).
    
    Bullish BOS: Price closes above the last Swing High (especially HH)
    Bearish BOS: Price closes below the last Swing Low (especially LL)
    
    Noise filtering:
    1. Close confirmation: requires candle CLOSE beyond the level, not just wick
    2. Minimum distance: break must exceed min_distance_atr * ATR
    3. Age limit: swing must be within max_swing_age bars
    4. Subsequent bars must confirm the break holds
    """
    closes = df['close'].values
    highs_vals = df['high'].values
    lows_vals = df['low'].values
    atr_vals = atr_series.values
    current_bar = len(df) - 1
    
    breaks = []
    
    # Check for bullish BOS (break above swing highs)
    for sh in reversed(swing_highs):
        # Age check
        age = current_bar - sh.bar_index
        if age > max_swing_age:
            continue
        if age < 1:
            continue
        
        # Find bars after the swing high where price breaks above
        for bar_idx in range(sh.bar_index + 1, len(df)):
            atr_at_bar = atr_vals[bar_idx] if bar_idx < len(atr_vals) else atr_vals[-1]
            min_distance = min_distance_atr * atr_at_bar
            
            if close_confirmation:
                broke = closes[bar_idx] > sh.price + min_distance
            else:
                broke = highs_vals[bar_idx] > sh.price + min_distance
            
            if broke:
                breaks.append(StructureBreak(
                    break_type=StructureBreakType.BOS,
                    direction=Direction.BUY,
                    price=sh.price,
                    bar_index=bar_idx,
                    swing_broken=sh,
                ))
                break  # Only record first break of each swing
    
    # Check for bearish BOS (break below swing lows)
    for sl in reversed(swing_lows):
        age = current_bar - sl.bar_index
        if age > max_swing_age:
            continue
        if age < 1:
            continue
        
        for bar_idx in range(sl.bar_index + 1, len(df)):
            atr_at_bar = atr_vals[bar_idx] if bar_idx < len(atr_vals) else atr_vals[-1]
            min_distance = min_distance_atr * atr_at_bar
            
            if close_confirmation:
                broke = closes[bar_idx] < sl.price - min_distance
            else:
                broke = lows_vals[bar_idx] < sl.price - min_distance
            
            if broke:
                breaks.append(StructureBreak(
                    break_type=StructureBreakType.BOS,
                    direction=Direction.SELL,
                    price=sl.price,
                    bar_index=bar_idx,
                    swing_broken=sl,
                ))
                break
    
    return breaks


def detect_choch(swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
                 df: pd.DataFrame, atr_series: pd.Series,
                 min_distance_atr: float = 0.10,
                 close_confirmation: bool = True,
                 max_swing_age: int = 100) -> List[StructureBreak]:
    """Detect Change of Character (CHoCH / MSS).
    
    CHoCH signals a potential trend reversal:
    - In an uptrend (HH, HL): break below the last HL → potential bearish reversal
    - In a downtrend (LL, LH): break above the last LH → potential bullish reversal
    """
    closes = df['close'].values
    atr_vals = atr_series.values
    
    breaks = []
    current_bar = len(df) - 1
    
    # Find HLs in an uptrend context - breaking below them is CHoCH bearish
    for sl in reversed(swing_lows):
        if current_bar - sl.bar_index > max_swing_age:
            continue
        if sl.classification != SwingClassification.HL:
            continue
        
        for bar_idx in range(sl.bar_index + 1, len(df)):
            atr_at_bar = atr_vals[bar_idx] if bar_idx < len(atr_vals) else atr_vals[-1]
            min_dist = min_distance_atr * atr_at_bar
            
            if close_confirmation:
                broke = closes[bar_idx] < sl.price - min_dist
            else:
                broke = df['low'].values[bar_idx] < sl.price - min_dist
            
            if broke:
                breaks.append(StructureBreak(
                    break_type=StructureBreakType.CHOCH,
                    direction=Direction.SELL,
                    price=sl.price,
                    bar_index=bar_idx,
                    swing_broken=sl,
                ))
                break
    
    # Find LHs in a downtrend context - breaking above them is CHoCH bullish
    for sh in reversed(swing_highs):
        if current_bar - sh.bar_index > max_swing_age:
            continue
        if sh.classification != SwingClassification.LH:
            continue
        
        for bar_idx in range(sh.bar_index + 1, len(df)):
            atr_at_bar = atr_vals[bar_idx] if bar_idx < len(atr_vals) else atr_vals[-1]
            min_dist = min_distance_atr * atr_at_bar
            
            if close_confirmation:
                broke = closes[bar_idx] > sh.price + min_dist
            else:
                broke = df['high'].values[bar_idx] > sh.price + min_dist
            
            if broke:
                breaks.append(StructureBreak(
                    break_type=StructureBreakType.CHOCH,
                    direction=Direction.BUY,
                    price=sh.price,
                    bar_index=bar_idx,
                    swing_broken=sh,
                ))
                break
    
    return breaks


def get_market_structure(df: pd.DataFrame, atr_series: pd.Series,
                         swing_lookback: int = 3,
                         swing_confirmation: int = 2,
                         bos_min_distance_atr: float = 0.10,
                         bos_close_confirmation: bool = True,
                         use_bos: bool = True,
                         use_choch: bool = True,
                         max_swing_age: int = 100) -> StructureAnalysis:
    """Complete market structure analysis.
    
    Returns StructureAnalysis with all swings, BOS, CHoCH, 
    and the current structure direction.
    """
    # 1. Find raw swings
    raw_highs = find_swing_highs(df, swing_lookback, swing_confirmation)
    raw_lows = find_swing_lows(df, swing_lookback, swing_confirmation)
    
    # 2. Classify swings
    classified_highs, classified_lows = classify_swings(raw_highs, raw_lows)
    
    # 3. Merge and sort all swings by bar_index
    all_swings = sorted(classified_highs + classified_lows, key=lambda s: s.bar_index)
    
    # 4. Detect BOS
    bos_list = []
    if use_bos:
        bos_list = detect_bos(
            classified_highs, classified_lows, df, atr_series,
            min_distance_atr=bos_min_distance_atr,
            close_confirmation=bos_close_confirmation,
            max_swing_age=max_swing_age
        )
    
    # 5. Detect CHoCH
    choch_list = []
    if use_choch:
        choch_list = detect_choch(
            classified_highs, classified_lows, df, atr_series,
            min_distance_atr=bos_min_distance_atr,
            close_confirmation=bos_close_confirmation
            , max_swing_age=max_swing_age
        )
    
    # 6. Determine structure direction from most recent BOS/CHoCH
    all_breaks = sorted(bos_list + choch_list, key=lambda b: b.bar_index)
    structure_direction = None
    if all_breaks:
        last_break = all_breaks[-1]
        structure_direction = last_break.direction
    
    # 7. Get last swing high and low
    last_sh = classified_highs[-1] if classified_highs else None
    last_sl = classified_lows[-1] if classified_lows else None
    
    return StructureAnalysis(
        swings=all_swings,
        bos_list=sorted(bos_list, key=lambda item: item.bar_index),
        choch_list=sorted(choch_list, key=lambda item: item.bar_index),
        last_swing_high=last_sh,
        last_swing_low=last_sl,
        structure_direction=structure_direction,
        last_bar_index=len(df) - 1,
    )
