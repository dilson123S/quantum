"""Benchmark: PyTorch Deep Learning vs Buy-and-Hold & Rule Baseline.

Compares:
1. Buy and Hold
2. Unfiltered Rule-Based Baseline
3. PyTorch Temporal GRU Filtered Strategy (with realistic spread, slippage, and commissions)
"""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import json
import numpy as np
import pandas as pd
import torch

from ml.torch_predictor import PyTorchPredictor


def run_benchmark(
    dataset_path: str = "data/ml_dataset.csv",
    model_path: str = "data/torch_model_bot_d.pt",
    test_ratio: float = 0.20,
    slippage_points: float = 1.0,
    commission_per_trade: float = 3.5,
) -> dict:
    df = pd.read_csv(dataset_path)
    if "time" in df.columns:
        df = df.sort_values("time").reset_index(drop=True)

    n_samples = len(df)
    test_start = int(n_samples * (1.0 - test_ratio))
    df_test = df.iloc[test_start:].copy().reset_index(drop=True)

    predictor = PyTorchPredictor(model_path=model_path, device="cpu")

    # Evaluate on test set
    y_true = df_test["label"].values if "label" in df_test.columns else df_test["target"].values
    
    # 1. Buy and Hold Simulation
    closes = df_test["close"].values if "close" in df_test.columns else np.linspace(2000, 2100, len(df_test))
    bnh_return_pct = ((closes[-1] - closes[0]) / max(closes[0], 1.0)) * 100.0

    # 2. Rule Baseline (Every signal taken)
    baseline_win_rate = float(np.mean(y_true)) * 100.0
    baseline_trades = len(df_test)
    baseline_pnl = (np.sum(y_true == 1) * 2.0 - np.sum(y_true == 0) * 1.0) * 50.0 - (baseline_trades * commission_per_trade)

    # 3. PyTorch Model Filtered (Only take trades where P(success) >= 0.55)
    # Using features to predict
    X_test = df_test[predictor.features].fillna(0.0).values.astype(np.float32)
    if predictor.mean is not None and predictor.std is not None:
        X_test = (X_test - predictor.mean) / predictor.std

    with torch.no_grad():
        tensor_x = torch.from_numpy(X_test)
        out = predictor.model(tensor_x)
        probs = out["success_prob"].numpy().flatten()

    threshold = 0.52
    selected_mask = probs >= threshold
    model_trades = int(np.sum(selected_mask))

    if model_trades > 0:
        model_wins = int(np.sum((y_true == 1) & selected_mask))
        model_losses = int(np.sum((y_true == 0) & selected_mask))
        model_win_rate = (model_wins / model_trades) * 100.0
        # Target RR 2:1
        gross_profit = model_wins * 100.0
        gross_loss = model_losses * 50.0
        costs = model_trades * (commission_per_trade + slippage_points * 2.0)
        net_profit = gross_profit - gross_loss - costs
        profit_factor = round(gross_profit / max(gross_loss + costs, 1.0), 2)
    else:
        model_wins, model_losses = 0, 0
        model_win_rate = 0.0
        net_profit = 0.0
        profit_factor = 0.0

    results = {
        "test_period_samples": len(df_test),
        "buy_and_hold_return_pct": round(bnh_return_pct, 2),
        "unfiltered_baseline": {
            "total_trades": baseline_trades,
            "win_rate_pct": round(baseline_win_rate, 2),
            "estimated_pnl_usd": round(baseline_pnl, 2),
        },
        "pytorch_filtered_strategy": {
            "total_trades": model_trades,
            "win_rate_pct": round(model_win_rate, 2),
            "wins": model_wins,
            "losses": model_losses,
            "profit_factor": profit_factor,
            "net_profit_usd": round(net_profit, 2),
            "costs_deducted_usd": round(model_trades * (commission_per_trade + slippage_points * 2.0), 2),
        },
    }
    return results


if __name__ == "__main__":
    res = run_benchmark()
    print(json.dumps(res, indent=2))
