"""Unit tests for Open-Source System 1 Decision Architecture (Laya, Kev, NanoJev)."""
import time
import unittest
from filters.system_one.base import (
    SystemOneDecision,
    ThesisAlignment,
    STANDARD_TRADING_QUESTIONS,
)
from filters.system_one.nanojev_provider import NanoJevProvider
from filters.system_one.laya_provider import LayaProvider
from filters.system_one.hub import SystemOneHub


class TestSystemOneArchitecture(unittest.TestCase):
    def setUp(self):
        self.sample_candidate = {
            "signal_id": "test_sig_001",
            "symbol": "XAUUSD",
            "direction": "SELL",
            "timeframe": "M15",
            "score": {"total": 85.0, "structure": 25.0, "trend": 25.0, "momentum": 20.0},
            "market_evidence": {
                "regime": {"label": "TRENDING"},
                "liquidity": {"sweep": {"detected": True, "direction": "SELL"}},
            },
        }

    def test_nanojev_single_evaluation(self):
        provider = NanoJevProvider()
        decision = provider.evaluate(self.sample_candidate)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.provider, "NanoJev")
        self.assertEqual(decision.signal_id, "test_sig_001")
        self.assertIn(decision.thesis_alignment, [ThesisAlignment.ALIGNED.value, ThesisAlignment.MIXED.value])
        self.assertTrue(decision.is_favorable(min_score=3))

    def test_nanojev_multi_asset_batch(self):
        provider = NanoJevProvider()
        candidates = [
            {"signal_id": "sig_gold", "symbol": "XAUUSD", "score": {"total": 88.0}, "market_evidence": {"liquidity": {"sweep": {"detected": True}}}},
            {"signal_id": "sig_crash", "symbol": "Crash 1000 Index", "score": {"total": 45.0}, "market_evidence": {}},
            {"signal_id": "sig_boom", "symbol": "Boom 1000 Index", "score": {"total": 75.0}, "market_evidence": {}},
        ]
        results = provider.evaluate_batch(candidates)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].signal_id, "sig_gold")
        self.assertEqual(results[0].thesis_alignment, ThesisAlignment.ALIGNED.value)
        self.assertEqual(results[1].signal_id, "sig_crash")
        self.assertEqual(results[1].thesis_alignment, ThesisAlignment.CONTRADICTORY.value)

    def test_hub_queue_and_worker(self):
        hub = SystemOneHub(enabled=True, provider_name="nanojev", shadow_mode=True)
        queued = hub.observe(
            dedupe_key="XAUUSD|M15|SELL|strategy|bar1",
            signal_id="test_sig_001",
            state=self.sample_candidate,
        )
        self.assertTrue(queued)
        # Give worker a moment to process from the queue
        time.sleep(0.5)
        self.assertIsNotNone(hub.last_decision)
        self.assertEqual(hub.last_decision.signal_id, "test_sig_001")


if __name__ == "__main__":
    unittest.main()
