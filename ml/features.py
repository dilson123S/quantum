"""Feature extraction for ML model — 15 normalized variables per bar."""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional


# Canonical feature names in order
FEATURE_COLUMNS: List[str] = [
    "ema20_slope",
    "ema50_slope",
    "price_vs_ema200",
    "adx_norm",
    "rsi_norm",
    "macd_hist_slope",
    "atr_ratio_val",
    "volume_ratio_val",
    "obv_trend",
    "spread_norm",
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "structure_score_norm",
    "trend_score_norm",
]


def extract_features(
    df: pd.DataFrame,
    structure_score: float = 0.0,
    trend_score: float = 0.0,
    spread_points: float = 0.0,
    atr_value: float = 1.0,
    point_size: Optional[float] = None,
    direction: Optional[str] = None,
) -> Dict[str, float]:
    """Extract a single feature vector from the latest bar of a DataFrame.

    The DataFrame must already contain indicator columns:
        ema_fast, ema_medium, ema_slow, adx, rsi, macd_histogram,
        atr, atr_ratio, volume_ratio, obv, time.

    All features are bounded / normalized to prevent extreme values from
    dominating tree splits.

    Returns:
        Dict mapping feature name -> float value.
    """
    if df is None or len(df) < 10:
        return {col: 0.0 for col in FEATURE_COLUMNS}

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Direction multiplier: orient indicators so positive values reflect strength in the trade direction
    dir_mult = -1.0 if (direction and str(direction).upper() in ("SELL", "DIRECTION.SELL")) else 1.0

    # --- Trend features ---
    ema20 = float(last.get("ema_fast", 0.0))
    ema20_prev = float(prev.get("ema_fast", 0.0))
    ema50 = float(last.get("ema_medium", 0.0))
    ema50_prev = float(prev.get("ema_medium", 0.0))
    ema200 = float(last.get("ema_slow", 0.0))
    close = float(last.get("close", 0.0))

    # Slopes as percentage change (clamped), oriented by trade direction
    ema20_slope = _safe_pct(ema20, ema20_prev) * dir_mult
    ema50_slope = _safe_pct(ema50, ema50_prev) * dir_mult

    # Distance of price to EMA200 as percentage, oriented by trade direction
    price_vs_ema200 = ((close - ema200) / ema200 * 100.0) if ema200 > 0 else 0.0
    price_vs_ema200 = _clamp(price_vs_ema200 * dir_mult, -5.0, 5.0)

    # ADX normalized to 0-1 (magnitude, direction agnostic)
    adx_val = float(last.get("adx", 0.0))
    adx_norm = _clamp(adx_val / 60.0, 0.0, 1.0)

    # --- Momentum features ---
    # RSI > 50 supports BUY; RSI < 50 supports SELL
    rsi_val = float(last.get("rsi", 50.0))
    rsi_norm = _clamp(((rsi_val - 50.0) / 50.0) * dir_mult, -1.0, 1.0)

    macd_hist = float(last.get("macd_histogram", 0.0))
    macd_hist_prev = float(prev.get("macd_histogram", 0.0))
    macd_hist_slope = _clamp((macd_hist - macd_hist_prev) * dir_mult, -0.01, 0.01) * 100.0

    # --- Volatility features ---
    atr_ratio_val = float(last.get("atr_ratio", 1.0))
    atr_ratio_val = _clamp(atr_ratio_val, 0.0, 4.0)

    # --- Volume features ---
    volume_ratio_val = float(last.get("volume_ratio", 1.0))
    volume_ratio_val = _clamp(volume_ratio_val, 0.0, 5.0)

    obv_curr = float(last.get("obv", 0.0))
    obv_prev = float(prev.get("obv", 0.0))
    obv_trend = 1.0 if obv_curr > obv_prev else (-1.0 if obv_curr < obv_prev else 0.0)

    # --- Spread normalized by ATR ---
    atr_safe = max(atr_value, 1e-8)
    # Use the broker point whenever available. The prior heuristic inverted
    # FX and metal point sizes (e.g. EURUSD was treated as 0.01), causing a
    # train/live feature mismatch. The fallback is deliberately conservative.
    effective_point = point_size if point_size and point_size > 0 else _default_point_size(close)
    spread_norm = _clamp((spread_points * effective_point) / atr_safe, 0.0, 1.0)

    # --- Time features (cyclical encoding) ---
    bar_time = pd.Timestamp(last.get("time", pd.Timestamp.now()))
    hour = bar_time.hour + bar_time.minute / 60.0
    hour_sin = float(np.sin(2.0 * np.pi * hour / 24.0))
    hour_cos = float(np.cos(2.0 * np.pi * hour / 24.0))
    day_of_week = _clamp(float(bar_time.dayofweek) / 6.0, 0.0, 1.0)  # 0=Mon..6=Sun -> 0..1

    # --- Score features (from the bot's 5-confirmation engine) ---
    structure_score_norm = _clamp(structure_score / 30.0, 0.0, 1.0)
    trend_score_norm = _clamp(trend_score / 25.0, 0.0, 1.0)

    return {
        "ema20_slope": round(ema20_slope, 6),
        "ema50_slope": round(ema50_slope, 6),
        "price_vs_ema200": round(price_vs_ema200, 4),
        "adx_norm": round(adx_norm, 4),
        "rsi_norm": round(rsi_norm, 4),
        "macd_hist_slope": round(macd_hist_slope, 6),
        "atr_ratio_val": round(atr_ratio_val, 4),
        "volume_ratio_val": round(volume_ratio_val, 4),
        "obv_trend": obv_trend,
        "spread_norm": round(spread_norm, 6),
        "hour_sin": round(hour_sin, 4),
        "hour_cos": round(hour_cos, 4),
        "day_of_week": round(day_of_week, 4),
        "structure_score_norm": round(structure_score_norm, 4),
        "trend_score_norm": round(trend_score_norm, 4),
    }


def extract_features_dataframe(
    df: pd.DataFrame,
    structure_scores: Optional[List[float]] = None,
    trend_scores: Optional[List[float]] = None,
) -> pd.DataFrame:
    """Extract features for every row in a DataFrame (for dataset building).

    Returns a DataFrame with one row per bar and FEATURE_COLUMNS as columns.
    Rows with insufficient history (first 10) are filled with zeros.
    """
    n = len(df)
    results = []

    for i in range(n):
        if i < 10:
            results.append({col: 0.0 for col in FEATURE_COLUMNS})
            continue

        window = df.iloc[max(0, i - 1): i + 1]
        struct_s = structure_scores[i] if structure_scores and i < len(structure_scores) else 0.0
        trend_s = trend_scores[i] if trend_scores and i < len(trend_scores) else 0.0

        feat = extract_features(
            df.iloc[max(0, i - 1): i + 1],
            structure_score=struct_s,
            trend_score=trend_s,
        )
        results.append(feat)

    return pd.DataFrame(results, columns=FEATURE_COLUMNS)


def _safe_pct(current: float, previous: float) -> float:
    """Percentage change, clamped to [-1, 1]."""
    if previous == 0:
        return 0.0
    return _clamp((current - previous) / abs(previous) * 100.0, -1.0, 1.0)


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


def _default_point_size(close: float) -> float:
    """Fallback for offline data only; live callers pass broker metadata."""
    if close >= 1000.0:  # metals/indices quoted with cents
        return 0.01
    if close >= 10.0:  # JPY-style FX quotes
        return 0.001
    return 0.00001
