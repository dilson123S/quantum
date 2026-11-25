"""Unit tests for ML module: features, dataset builder, predictor, and filter."""
import os
import pytest
import numpy as np
import pandas as pd

from ml.features import FEATURE_COLUMNS, extract_features, extract_features_dataframe
from filters.ml_filter import MLFilter
from ml.predictor import MLPredictor
from ml.dataset_builder import _breakout_outcome
from ml.trainer import _purged_temporal_splits
from core.constants import Direction


@pytest.fixture
def sample_ohlcv_with_indicators():
    """Create synthetic OHLCV DataFrame with required indicators."""
    n = 60
    np.random.seed(42)
    prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)

    times = pd.date_range("2026-08-01", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({
        "time": times,
        "open": prices + np.random.randn(n) * 0.1,
        "high": prices + np.abs(np.random.randn(n) * 0.4),
        "low": prices - np.abs(np.random.randn(n) * 0.4),
        "close": prices,
        "tick_volume": np.random.randint(100, 1000, n),
        "spread": np.full(n, 10),
        "ema_fast": prices * 1.001,
        "ema_medium": prices * 0.999,
        "ema_slow": prices * 0.995,
        "adx": np.random.uniform(15, 35, n),
        "rsi": np.random.uniform(40, 70, n),
        "macd_histogram": np.random.randn(n) * 0.05,
        "atr": np.random.uniform(0.3, 0.8, n),
        "atr_ratio": np.random.uniform(0.9, 1.3, n),
        "volume_ratio": np.random.uniform(0.8, 1.5, n),
        "obv": np.cumsum(np.random.randn(n) * 100),
    })
    return df


def test_feature_extraction_columns(sample_ohlcv_with_indicators):
    """Test that extract_features returns all 15 canonical features."""
    feat = extract_features(
        sample_ohlcv_with_indicators,
        structure_score=20.0,
        trend_score=18.0,
        spread_points=10.0,
        atr_value=0.5,
    )

    assert isinstance(feat, dict)
    assert len(feat) == len(FEATURE_COLUMNS)
    for col in FEATURE_COLUMNS:
        assert col in feat
        assert isinstance(feat[col], (float, int))
        assert not np.isnan(feat[col])


def test_feature_bounds(sample_ohlcv_with_indicators):
    """Test that extracted features stay within reasonable bounds."""
    feat = extract_features(
        sample_ohlcv_with_indicators,
        structure_score=25.0,
        trend_score=20.0,
        spread_points=15.0,
        atr_value=0.6,
    )

    assert 0.0 <= feat["adx_norm"] <= 1.0
    assert -1.0 <= feat["rsi_norm"] <= 1.0
    assert 0.0 <= feat["atr_ratio_val"] <= 4.0
    assert 0.0 <= feat["volume_ratio_val"] <= 5.0
    assert -1.0 <= feat["hour_sin"] <= 1.0
    assert -1.0 <= feat["hour_cos"] <= 1.0
    assert 0.0 <= feat["day_of_week"] <= 1.0
    assert 0.0 <= feat["structure_score_norm"] <= 1.0
    assert 0.0 <= feat["trend_score_norm"] <= 1.0


def test_feature_extraction_empty_or_small():
    """Test safe fallback on empty or very short DataFrame."""
    empty_df = pd.DataFrame()
    feat_empty = extract_features(empty_df)
    assert len(feat_empty) == len(FEATURE_COLUMNS)
    assert all(v == 0.0 for v in feat_empty.values())

    short_df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    feat_short = extract_features(short_df)
    assert len(feat_short) == len(FEATURE_COLUMNS)
    assert all(v == 0.0 for v in feat_short.values())


def test_ml_predictor_graceful_missing_model():
    """Test that MLPredictor handles missing model files gracefully without crashing."""
    predictor = MLPredictor(model_path="data/non_existent_model_xyz.joblib")
    assert not predictor.is_ready
    prob = predictor.predict_probability(pd.DataFrame())
    assert prob is None


def test_ml_filter_shadow_mode(sample_ohlcv_with_indicators):
    """Test that Shadow Mode NEVER blocks signals even if probability is low or no model exists."""
    ml_filter = MLFilter(
        enabled=True,
        shadow_mode=True,
        model_path="data/non_existent_model_xyz.joblib",
        min_confidence=0.65,
    )

    approved, prob, reason = ml_filter.evaluate(
        symbol="EURUSD",
        direction="BUY",
        df=sample_ohlcv_with_indicators,
        structure_score=20.0,
        trend_score=18.0,
        total_score=80.0,
    )

    # In Shadow mode, approved must always be True
    assert approved is True


def test_ml_filter_disabled():
    """Test that disabled ML filter approves everything silently."""
    ml_filter = MLFilter(enabled=False)
    approved, prob, reason = ml_filter.evaluate(
        symbol="XAUUSD",
        direction="BUY",
        df=pd.DataFrame(),
    )
    assert approved is True
    assert prob is None
    assert reason == ""


def test_breakout_label_is_conservative_when_sl_and_tp_touch_same_bar():
    future = pd.DataFrame([{"high": 1.1030, "low": 1.0980}])
    label, filled = _breakout_outcome(
        future, Direction.BUY, entry=1.1000, sl=1.0990, tp=1.1020
    )
    assert filled is True
    assert label == 0


def test_purged_temporal_splits_do_not_overlap_label_horizon():
    times = pd.Series(pd.date_range("2026-01-01", periods=80, freq="15min", tz="UTC").repeat(2))
    folds = list(_purged_temporal_splits(times, n_splits=3, purge_bars=5))
    assert len(folds) == 3
    for train_idx, validation_idx in folds:
        train_end = times.iloc[train_idx].max()
        validation_start = times.iloc[validation_idx].min()
        assert train_end < validation_start
        assert (validation_start - train_end) >= pd.Timedelta(minutes=15 * 6)
