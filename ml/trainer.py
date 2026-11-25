"""XGBoost trainer with purged, multi-symbol temporal validation."""
import json
import logging
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd

from ml.features import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

try:
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False

try:
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
    import joblib
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


DEFAULT_PARAMS: Dict[str, Any] = {
    "n_estimators": 200,
    "max_depth": 4,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "learning_rate": 0.05,
    "gamma": 1.0,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "scale_pos_weight": 1.0,
    "eval_metric": "logloss",
    "random_state": 42,
    "use_label_encoder": False,
    "verbosity": 0,
}


def train_model(
    dataset_path: str = "data/ml_dataset.csv",
    model_path: str = "data/ml_model.joblib",
    report_path: str = "data/ml_report.json",
    n_splits: int = 5,
    min_auc_threshold: float = 0.52,
    min_fold_auc_threshold: float = 0.45,
    purge_bars: int = 20,
    calibrate: bool = True,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Train only if strictly earlier samples predict later, unshared periods.

    Training samples in the final ``purge_bars`` timestamps before a validation
    fold are discarded. Their labels could otherwise inspect future candles
    inside the validation interval, a subtle leakage common in financial ML.
    """
    if not HAS_XGBOOST:
        return False, {"error": "xgboost not installed"}
    if not HAS_SKLEARN:
        return False, {"error": "scikit-learn/joblib not installed"}
    if not os.path.exists(dataset_path):
        return False, {"error": f"Dataset not found: {dataset_path}"}
    if n_splits < 2 or purge_bars < 0:
        return False, {"error": "n_splits must be >= 2 and purge_bars must be >= 0"}

    df = pd.read_csv(dataset_path)
    required = {"time", "label"}
    if not required.issubset(df.columns):
        return False, {"error": f"Dataset must include {sorted(required)}"}
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time", "label"]).sort_values(
        ["time", "symbol"] if "symbol" in df.columns else ["time"]
    ).reset_index(drop=True)
    if len(df) < 200:
        return False, {"error": f"Dataset too small: {len(df)} rows (need at least 200)"}

    features = [column for column in FEATURE_COLUMNS if column in df.columns]
    if len(features) < 5:
        return False, {"error": f"Only {len(features)} canonical features found"}
    X = df[features].fillna(0.0).to_numpy()
    y = df["label"].astype(int).to_numpy()
    positive, negative = int(y.sum()), int(len(y) - y.sum())
    if positive == 0 or negative == 0:
        return False, {"error": "Dataset contains a single label class"}

    folds = list(_purged_temporal_splits(df["time"], n_splits, purge_bars))
    if len(folds) != n_splits:
        return False, {
            "error": (
                "Not enough distinct timestamps for purged validation: "
                f"got {df['time'].nunique()}, require more than purge_bars + n_splits + 1"
            )
        }

    model_params = {**DEFAULT_PARAMS, **(params or {})}
    model_params["scale_pos_weight"] = negative / positive
    fold_metrics: List[Dict[str, Any]] = []
    invalid_folds: List[Dict[str, Any]] = []
    for fold_number, (train_idx, val_idx) in enumerate(folds, start=1):
        y_train, y_val = y[train_idx], y[val_idx]
        if len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
            invalid_folds.append({
                "fold": fold_number,
                "reason": "single label class in training or validation window",
                "train_size": len(train_idx),
                "val_size": len(val_idx),
            })
            continue
        model = XGBClassifier(**model_params)
        model.fit(X[train_idx], y_train, verbose=False)
        predicted = model.predict(X[val_idx])
        probabilities = model.predict_proba(X[val_idx])[:, 1]
        auc = float(roc_auc_score(y_val, probabilities))
        fold_metrics.append({
            "fold": fold_number,
            "train_size": int(len(train_idx)),
            "val_size": int(len(val_idx)),
            "train_end": df["time"].iloc[train_idx].max().isoformat(),
            "validation_start": df["time"].iloc[val_idx].min().isoformat(),
            "validation_end": df["time"].iloc[val_idx].max().isoformat(),
            "accuracy": round(float(accuracy_score(y_val, predicted)), 4),
            "precision": round(float(precision_score(y_val, predicted, zero_division=0)), 4),
            "recall": round(float(recall_score(y_val, predicted, zero_division=0)), 4),
            "f1": round(float(f1_score(y_val, predicted, zero_division=0)), 4),
            "auc_roc": round(auc, 4),
        })

    report: Dict[str, Any] = {
        "dataset_rows": int(len(df)),
        "positive_samples": positive,
        "negative_samples": negative,
        "features_used": features,
        "n_features": len(features),
        "data_start": df["time"].min().isoformat(),
        "data_end": df["time"].max().isoformat(),
        "n_folds": n_splits,
        "purge_bars": purge_bars,
        "validation_protocol": "expanding purged temporal folds grouped by timestamp across symbols",
        "fold_metrics": fold_metrics,
        "invalid_folds": invalid_folds,
        "min_auc_threshold": min_auc_threshold,
        "min_fold_auc_threshold": min_fold_auc_threshold,
        "xgb_params": {key: value for key, value in model_params.items() if key != "use_label_encoder"},
    }
    if invalid_folds or len(fold_metrics) != n_splits:
        report["status"] = "REJECTED"
        report["error"] = "Temporal folds lack both classes; collect more representative history."
        report["model_accepted"] = False
        _save_report(report, report_path)
        return False, report

    for key in ("auc_roc", "accuracy", "precision", "f1"):
        report[f"avg_{key}"] = round(float(np.mean([metric[key] for metric in fold_metrics])), 4)
    min_fold_auc = min(metric["auc_roc"] for metric in fold_metrics)
    report["min_fold_auc_roc"] = min_fold_auc
    accepted = report["avg_auc_roc"] >= min_auc_threshold and min_fold_auc >= min_fold_auc_threshold
    report["model_accepted"] = accepted
    if not accepted:
        report["status"] = "REJECTED"
        report["error"] = "Average or worst-fold AUC did not meet the acceptance threshold."
        _save_report(report, report_path)
        return False, report

    raw_model = XGBClassifier(**model_params)
    raw_model.fit(X, y, verbose=False)
    report["feature_importance"] = [
        {"feature": feature, "importance": round(float(importance), 4)}
        for feature, importance in sorted(
            zip(features, raw_model.feature_importances_.tolist()),
            key=lambda item: item[1], reverse=True,
        )
    ]

    if calibrate:
        try:
            from sklearn.calibration import CalibratedClassifierCV
            calibrated_model = CalibratedClassifierCV(
                estimator=XGBClassifier(**model_params),
                cv=3,
                method="sigmoid",
            )
            calibrated_model.fit(X, y)
            final_model = calibrated_model
            report["calibrated"] = True
            logger.info("[ML Trainer] Final model calibrated via Platt sigmoid scaling.")
        except Exception as exc:
            logger.warning("[ML Trainer] Calibration failed (%s); using uncalibrated model.", exc)
            final_model = raw_model
            report["calibrated"] = False
    else:
        final_model = raw_model
        report["calibrated"] = False

    report["status"] = "ACCEPTED_SHADOW_ONLY"
    _ensure_parent(model_path)
    joblib.dump({"model": final_model, "features": features}, model_path)
    _save_report(report, report_path)
    return True, report


def _purged_temporal_splits(
    timestamps: pd.Series, n_splits: int, purge_bars: int
) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
    """Yield expanding, timestamp-grouped folds with a label-horizon purge."""
    ordered_times = pd.Index(timestamps.drop_duplicates().sort_values())
    test_size = len(ordered_times) // (n_splits + 1)
    if test_size <= 0:
        return
    values = timestamps.to_numpy()
    for fold in range(1, n_splits + 1):
        val_start_idx = fold * test_size
        val_end_idx = (fold + 1) * test_size if fold < n_splits else len(ordered_times)
        train_end_idx = val_start_idx - purge_bars
        if train_end_idx <= 0 or val_start_idx >= val_end_idx:
            return
        train_end, val_start, val_end = (
            ordered_times[train_end_idx - 1],
            ordered_times[val_start_idx],
            ordered_times[val_end_idx - 1],
        )
        train_idx = np.flatnonzero(values <= train_end)
        val_idx = np.flatnonzero((values >= val_start) & (values <= val_end))
        yield train_idx, val_idx


def _ensure_parent(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _save_report(report: Dict[str, Any], report_path: str) -> None:
    _ensure_parent(report_path)
    with open(report_path, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, default=str)
    logger.info("[ML Trainer] Report saved to %s", report_path)
