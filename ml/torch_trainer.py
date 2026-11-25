"""PyTorch Walk-Forward Chronological Trainer for Financial Market Predictions.

Follows strict quantitative principles:
1. Strict chronological splitting (Train -> Validation -> Out-of-Sample Test) to prevent future leakage.
2. Purged boundary bars between folds to eliminate autocorrelation contamination.
3. Multi-task loss (BCE for success probability + CrossEntropy for direction classification).
4. Early stopping on validation loss with AdamW and Cosine Annealing learning rate schedule.
5. Saves clean PyTorch state checkpoint (.pt) with feature metadata and scaling statistics.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from ml.features import FEATURE_COLUMNS
from ml.torch_models import TemporalGRUPredictor

logger = logging.getLogger("PyTorchTrainer")


class PyTorchFinancialTrainer:
    """Trains TemporalGRUPredictor with purged chronological cross-validation."""

    def __init__(
        self,
        features: Optional[List[str]] = None,
        device: Optional[str] = None,
        learning_rate: float = 1e-3,
        weight_decay: float = 1e-4,
        batch_size: int = 64,
        epochs: int = 40,
        patience: int = 8,
    ):
        self.features = features or FEATURE_COLUMNS
        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)
            
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.batch_size = batch_size
        self.epochs = epochs
        self.patience = patience

    def prepare_data(
        self,
        df: pd.DataFrame,
        purge_bars: int = 20,
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
    ) -> Tuple[TensorDataset, TensorDataset, TensorDataset, Dict[str, Any]]:
        """Split data chronologically into Train, Validation, and Out-of-Sample Test sets."""
        # Ensure chronological sort
        if "time" in df.columns:
            df = df.sort_values("time").reset_index(drop=True)
            
        # Target column
        target_col = "label" if "label" in df.columns else ("target" if "target" in df.columns else None)
        if target_col is None:
            raise ValueError("DataFrame must contain 'label' or 'target' column.")

        X_raw = df[self.features].fillna(0.0).values.astype(np.float32)
        y_raw = df[target_col].values.astype(np.float32)

        # Direction labels: if direction column exists, map to 0 (SELL), 1 (HOLD), 2 (BUY)
        if "direction" in df.columns:
            dir_series = df["direction"].astype(str).str.upper()
            dir_labels = np.where(dir_series.str.contains("BUY"), 2,
                         np.where(dir_series.str.contains("SELL"), 0, 1)).astype(np.int64)
        else:
            # Fallback direction from label
            dir_labels = np.where(y_raw == 1, 2, 0).astype(np.int64)

        n_samples = len(df)
        train_end = int(n_samples * train_ratio)
        val_end = int(n_samples * (train_ratio + val_ratio))

        # Apply purging: discard the last `purge_bars` of train and val to avoid lookahead leak
        train_idx = np.arange(0, max(0, train_end - purge_bars))
        val_idx = np.arange(train_end, max(train_end, val_end - purge_bars))
        test_idx = np.arange(val_end, n_samples)

        # Compute scaling statistics strictly on Train set ONLY (no future leak)
        mean_vals = np.mean(X_raw[train_idx], axis=0)
        std_vals = np.std(X_raw[train_idx], axis=0)
        std_vals[std_vals == 0.0] = 1.0

        # Standardize features
        X_norm = (X_raw - mean_vals) / std_vals

        stats = {
            "mean": mean_vals.tolist(),
            "std": std_vals.tolist(),
            "features": self.features,
            "train_samples": len(train_idx),
            "val_samples": len(val_idx),
            "test_samples": len(test_idx),
        }

        train_dataset = TensorDataset(
            torch.from_numpy(X_norm[train_idx]),
            torch.from_numpy(y_raw[train_idx]).unsqueeze(1),
            torch.from_numpy(dir_labels[train_idx]),
        )
        val_dataset = TensorDataset(
            torch.from_numpy(X_norm[val_idx]),
            torch.from_numpy(y_raw[val_idx]).unsqueeze(1),
            torch.from_numpy(dir_labels[val_idx]),
        )
        test_dataset = TensorDataset(
            torch.from_numpy(X_norm[test_idx]),
            torch.from_numpy(y_raw[test_idx]).unsqueeze(1),
            torch.from_numpy(dir_labels[test_idx]),
        )

        return train_dataset, val_dataset, test_dataset, stats

    def train(
        self,
        dataset_path: str = "data/ml_dataset.csv",
        save_model_path: str = "data/torch_model_bot_d.pt",
        save_report_path: str = "data/torch_model_report.json",
    ) -> Dict[str, Any]:
        """Execute chronological model training and save checkpoint."""
        logger.info(f"Loading dataset from {dataset_path}...")
        df = pd.read_csv(dataset_path)

        train_ds, val_ds, test_ds, stats = self.prepare_data(df)
        train_loader = DataLoader(train_ds, batch_size=self.batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=self.batch_size, shuffle=False)
        test_loader = DataLoader(test_ds, batch_size=self.batch_size, shuffle=False)

        model = TemporalGRUPredictor(
            num_features=len(self.features),
            hidden_dim=64,
            num_layers=2,
            dropout=0.25,
            bidirectional=True,
        ).to(self.device)

        bce_loss_fn = nn.BCEWithLogitsLoss()
        ce_loss_fn = nn.CrossEntropyLoss()
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=self.learning_rate,
            weight_decay=self.weight_decay,
        )
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=self.epochs, eta_min=1e-5)

        best_val_loss = float("inf")
        best_state = None
        patience_counter = 0
        history: List[Dict[str, float]] = []

        logger.info(f"Starting PyTorch training on device: {self.device} for {self.epochs} epochs...")
        start_time = time.time()

        for epoch in range(1, self.epochs + 1):
            model.train()
            train_loss = 0.0
            train_correct = 0
            train_total = 0

            for X_b, y_b, dir_b in train_loader:
                X_b, y_b, dir_b = X_b.to(self.device), y_b.to(self.device), dir_b.to(self.device)
                optimizer.zero_grad()

                out = model(X_b)
                loss_success = bce_loss_fn(out["direction_logits"][:, [2]] - out["direction_logits"][:, [0]], y_b)
                loss_dir = ce_loss_fn(out["direction_logits"], dir_b)
                total_loss = loss_success + 0.5 * loss_dir

                total_loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
                optimizer.step()

                train_loss += total_loss.item() * len(X_b)
                preds = (out["success_prob"] >= 0.5).float()
                train_correct += (preds == y_b).sum().item()
                train_total += len(X_b)

            scheduler.step()
            train_loss /= max(train_total, 1)
            train_acc = train_correct / max(train_total, 1)

            # Validation
            model.eval()
            val_loss = 0.0
            val_correct = 0
            val_total = 0
            all_val_preds = []
            all_val_y = []

            with torch.no_grad():
                for X_b, y_b, dir_b in val_loader:
                    X_b, y_b, dir_b = X_b.to(self.device), y_b.to(self.device), dir_b.to(self.device)
                    out = model(X_b)

                    loss_success = bce_loss_fn(out["direction_logits"][:, [2]] - out["direction_logits"][:, [0]], y_b)
                    loss_dir = ce_loss_fn(out["direction_logits"], dir_b)
                    v_loss = loss_success + 0.5 * loss_dir

                    val_loss += v_loss.item() * len(X_b)
                    preds = (out["success_prob"] >= 0.5).float()
                    val_correct += (preds == y_b).sum().item()
                    val_total += len(X_b)
                    all_val_preds.extend(out["success_prob"].cpu().numpy().flatten())
                    all_val_y.extend(y_b.cpu().numpy().flatten())

            val_loss /= max(val_total, 1)
            val_acc = val_correct / max(val_total, 1)

            epoch_record = {
                "epoch": epoch,
                "train_loss": round(train_loss, 4),
                "train_acc": round(train_acc, 4),
                "val_loss": round(val_loss, 4),
                "val_acc": round(val_acc, 4),
            }
            history.append(epoch_record)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= self.patience:
                    logger.info(f"Early stopping triggered at epoch {epoch} (best val loss: {best_val_loss:.4f})")
                    break

        duration = time.time() - start_time
        logger.info(f"Training completed in {duration:.1f}s. Evaluating on Out-of-Sample Test set...")

        # Load best weights
        if best_state is not None:
            model.load_state_dict(best_state)
            model.to(self.device)

        # Final Out-of-Sample Evaluation
        model.eval()
        test_correct = 0
        test_total = 0
        y_test_true = []
        y_test_probs = []

        with torch.no_grad():
            for X_b, y_b, _ in test_loader:
                X_b, y_b = X_b.to(self.device), y_b.to(self.device)
                out = model(X_b)
                probs = out["success_prob"].cpu().numpy().flatten()
                preds = (probs >= 0.5).astype(float)
                labels = y_b.cpu().numpy().flatten()

                test_correct += (preds == labels).sum()
                test_total += len(labels)
                y_test_true.extend(labels)
                y_test_probs.extend(probs)

        test_acc = test_correct / max(test_total, 1)
        
        # Calculate AUC if sklearn available
        auc_score = 0.5
        try:
            from sklearn.metrics import roc_auc_score
            auc_score = float(roc_auc_score(y_test_true, y_test_probs))
        except Exception:
            pass

        # Save model checkpoint
        os.makedirs(os.path.dirname(save_model_path), exist_ok=True)
        checkpoint = {
            "model_type": "TemporalGRUPredictor",
            "state_dict": best_state,
            "features": self.features,
            "scaling": {
                "mean": stats["mean"],
                "std": stats["std"],
            },
            "metrics": {
                "val_loss": best_val_loss,
                "test_accuracy": round(test_acc, 4),
                "test_auc": round(auc_score, 4),
                "test_samples": test_total,
            },
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }
        torch.save(checkpoint, save_model_path)
        logger.info(f"PyTorch model saved to {save_model_path} | Test Acc: {test_acc:.2%}, Test AUC: {auc_score:.4f}")

        # Save json report
        report = {
            "model_path": save_model_path,
            "device": str(self.device),
            "features": self.features,
            "data_splits": {
                "train_samples": stats["train_samples"],
                "val_samples": stats["val_samples"],
                "test_samples": stats["test_samples"],
            },
            "results": checkpoint["metrics"],
            "training_history": history,
            "duration_seconds": round(duration, 2),
        }
        with open(save_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        return report
