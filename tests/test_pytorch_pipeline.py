"""Unit and Integration Tests for PyTorch Deep Learning Components in Quantum Trading Bot."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import numpy as np
import pandas as pd
import torch

from filters.system_one.base import ThesisAlignment, TrapRisk
from filters.system_one.nanojev_provider import NanoJevProvider
from filters.system_one.pytorch_nanojev import PyTorchNanoJevEngine
from ml.features import FEATURE_COLUMNS
from ml.torch_models import NanoJevPyTorchNet, TemporalGRUPredictor
from ml.torch_predictor import PyTorchPredictor


class TestPyTorchArchitecture(unittest.TestCase):
    """Test PyTorch neural network forward pass logic."""

    def test_gru_predictor_forward_2d(self):
        model = TemporalGRUPredictor(num_features=15, hidden_dim=32, num_layers=1)
        x = torch.randn(8, 15)  # (batch_size, features)
        out = model(x)
        self.assertIn("direction_logits", out)
        self.assertIn("success_prob", out)
        self.assertIn("volatility", out)
        self.assertEqual(out["direction_logits"].shape, (8, 3))
        self.assertEqual(out["success_prob"].shape, (8, 1))
        self.assertTrue((out["success_prob"] >= 0.0).all() and (out["success_prob"] <= 1.0).all())

    def test_gru_predictor_forward_3d(self):
        model = TemporalGRUPredictor(num_features=15, hidden_dim=32, num_layers=2)
        x = torch.randn(4, 20, 15)  # (batch_size, seq_len, features)
        out = model(x)
        self.assertEqual(out["direction_logits"].shape, (4, 3))
        self.assertEqual(out["success_prob"].shape, (4, 1))

    def test_nanojev_pytorch_net(self):
        net = NanoJevPyTorchNet(input_dim=16, hidden_dim=32)
        x = torch.randn(5, 16)
        out = net(x)
        self.assertEqual(out["thesis_logits"].shape, (5, 3))
        self.assertEqual(out["evidence_score"].shape, (5,))
        self.assertEqual(out["trap_logits"].shape, (5, 3))
        self.assertEqual(out["confidence"].shape, (5, 1))
        # Evidence scores must be 1 to 5
        self.assertTrue((out["evidence_score"] >= 1).all() and (out["evidence_score"] <= 5).all())


class TestPyTorchNanoJevIntegration(unittest.TestCase):
    """Test NanoJev PyTorch neural decision validation."""

    def setUp(self):
        self.provider = NanoJevProvider()
        self.engine = PyTorchNanoJevEngine(device="cpu")

    def test_evaluate_aligned_candidate(self):
        candidate = {
            "signal_id": "test_boom_signal",
            "symbol": "Boom 1000 Index",
            "direction": "BUY",
            "entry_price": 16000.0,
            "sl_price": 15980.0,
            "tp_price": 16060.0,
            "target_rr": 3.0,
            "risk_to_atr_ratio": 1.2,
            "score": {"total": 78.0, "macro": 25.0, "structure": 25.0, "absorption": 15.0, "cluster": 5.0, "momentum": 8.0},
            "market_evidence": {
                "macro_regime": {"h1_trend": "BULLISH", "h1_distance_ema50_atr": 1.5},
                "structure_demand": {"distance_to_zone_atr": 0.4, "rejection_wick_ratio": 0.22},
                "jump_clustering": {"has_recent_spike": True},
                "momentum": {"m5_rsi": 42.0},
            },
        }

        decision = self.provider.evaluate(candidate)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.thesis_alignment, ThesisAlignment.ALIGNED.value)
        self.assertGreaterEqual(decision.evidence_score, 4)
        self.assertEqual(decision.trap_risk, TrapRisk.GENUINE_EXPANSION.value)
        self.assertGreater(decision.thesis_confidence, 0.70)
        self.assertLess(decision.latency_ms, 50.0)  # Ultra low latency

    def test_evaluate_trap_candidate(self):
        candidate = {
            "signal_id": "test_trap_signal",
            "symbol": "Boom 1000 Index",
            "direction": "BUY",
            "entry_price": 16000.0,
            "sl_price": 15980.0,
            "tp_price": 16060.0,
            "target_rr": 3.0,
            "risk_to_atr_ratio": 2.5,
            "score": {"total": 35.0, "macro": 0.0, "structure": 10.0, "absorption": 2.0, "cluster": 0.0, "momentum": 2.0},
            "market_evidence": {
                "macro_regime": {"h1_trend": "BEARISH", "h1_distance_ema50_atr": -3.5},
                "structure_demand": {"distance_to_zone_atr": 2.2, "rejection_wick_ratio": 0.02},
                "jump_clustering": {"has_recent_spike": False},
                "momentum": {"m5_rsi": 75.0},
            },
        }

        decision = self.provider.evaluate(candidate)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.thesis_alignment, ThesisAlignment.CONTRADICTORY.value)
        self.assertLessEqual(decision.evidence_score, 2)
        self.assertEqual(decision.trap_risk, TrapRisk.POTENTIAL_TRAP.value)


class TestPyTorchPredictorAndMLFilter(unittest.TestCase):
    """Test PyTorchPredictor and MLFilter compatibility with .pt checkpoint."""

    def setUp(self):
        self.model_path = "data/torch_model_bot_d.pt"
        self.predictor = PyTorchPredictor(model_path=self.model_path)

        # Create dummy indicator dataframe
        dates = pd.date_range("2026-09-01", periods=15, freq="5min")
        self.df = pd.DataFrame({
            "open": np.linspace(2000, 2010, 15),
            "high": np.linspace(2002, 2012, 15),
            "low": np.linspace(1998, 2008, 15),
            "close": np.linspace(2001, 2011, 15),
            "ema_fast": np.linspace(2000, 2010, 15),
            "ema_medium": np.linspace(1995, 2005, 15),
            "ema_slow": np.linspace(1990, 2000, 15),
            "adx": np.full(15, 28.0),
            "rsi": np.full(15, 56.0),
            "macd_histogram": np.full(15, 0.05),
            "atr": np.full(15, 2.5),
            "atr_ratio": np.full(15, 1.1),
            "volume_ratio": np.full(15, 1.2),
            "obv": np.linspace(100, 200, 15),
        }, index=dates)

    def test_predictor_is_ready(self):
        self.assertTrue(self.predictor.is_ready)

    def test_predict_probability(self):
        prob = self.predictor.predict_probability(
            df=self.df,
            structure_score=20.0,
            trend_score=25.0,
            spread_points=1.5,
            atr_value=2.5,
            direction="BUY",
        )
        self.assertIsNotNone(prob)
        self.assertGreaterEqual(prob, 0.0)
        self.assertLessEqual(prob, 1.0)

    def test_predict_detailed(self):
        res = self.predictor.predict_detailed(
            df=self.df,
            structure_score=20.0,
            trend_score=25.0,
            spread_points=1.5,
            atr_value=2.5,
            direction="BUY",
        )
        self.assertIsNotNone(res)
        self.assertIn(res.direction, ["BUY", "SELL", "HOLD"])
        self.assertIn("BUY", res.direction_probs)
        self.assertGreater(res.volatility, 0.0)

    def test_ml_filter_integration(self):
        from filters.ml_filter import MLFilter
        ml_filter = MLFilter(
            enabled=True,
            shadow_mode=True,
            model_path=self.model_path,
            min_confidence=0.60,
        )
        self.assertTrue(ml_filter.is_active)
        approved, prob, reason = ml_filter.evaluate(
            symbol="XAUUSD",
            direction="BUY",
            df=self.df,
            structure_score=20.0,
            trend_score=25.0,
            spread_points=1.5,
            atr_value=2.5,
        )
        self.assertTrue(approved)  # Shadow mode always approves
        self.assertIsNotNone(prob)
        self.assertIn("Shadow", reason)


if __name__ == "__main__":
    unittest.main()
