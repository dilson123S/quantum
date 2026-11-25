"""Unit and Integration Tests for Kronos Foundation Model in BOT-A and BOT-G."""
import unittest
import pandas as pd
import numpy as np

from core.config_loader import load_config, AppConfig, KronosConfig
from filters.kronos_filter import KronosFilter
from ml.kronos_oracle import KronosOracle, KronosVerdict


class TestKronosIntegration(unittest.TestCase):
    """Verifies Kronos Foundation Model configuration, oracle, and filter integration."""

    def setUp(self):
        # Create deterministic synthetic 100-bar M15 OHLCV test dataset
        dates = pd.date_range("2026-10-01", periods=100, freq="15min")
        close_series = np.linspace(2650.0, 2670.0, 100)
        self.df_test = pd.DataFrame({
            "open": close_series - 0.5,
            "high": close_series + 1.2,
            "low": close_series - 1.0,
            "close": close_series,
            "volume": 250.0,
            "amount": close_series * 250.0,
            "timestamps": dates,
        })

    def test_kronos_config_schema(self):
        """Verifies KronosConfig schema defaults and AppConfig integration."""
        cfg = KronosConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.model_name, "NeoQuasar/Kronos-small")
        self.assertEqual(cfg.tokenizer_name, "NeoQuasar/Kronos-Tokenizer-base")
        self.assertEqual(cfg.forecast_horizon, 12)
        self.assertEqual(cfg.min_confidence, 0.60)
        self.assertTrue(cfg.shadow_mode)

        app_cfg = AppConfig()
        self.assertIsNotNone(app_cfg.kronos)
        self.assertFalse(app_cfg.kronos.enabled)

    def test_bot_a_and_bot_g_configs(self):
        """Verifies that BOT-A and BOT-G configs have Kronos enabled and configured."""
        cfg_a = load_config("config/bot_a_gold")
        self.assertTrue(cfg_a.kronos.enabled)
        self.assertEqual(cfg_a.kronos.model_name, "NeoQuasar/Kronos-small")
        self.assertTrue(cfg_a.kronos.shadow_mode)

        cfg_g = load_config("config/bot_g_alchemist")
        self.assertTrue(cfg_g.kronos.enabled)
        self.assertEqual(cfg_g.kronos.model_name, "NeoQuasar/Kronos-small")
        self.assertTrue(cfg_g.kronos.shadow_mode)

    def test_kronos_filter_shadow_mode_always_approves(self):
        """In shadow mode, signals must ALWAYS be approved while logging metrics."""
        k_filter = KronosFilter(
            enabled=True,
            shadow_mode=True,
            min_confidence=0.99,  # Impossibly high threshold
        )
        self.assertTrue(k_filter.is_active)

        approved, conf, reason, v_dict = k_filter.evaluate(
            symbol="XAUUSD",
            direction="BUY",
            entry_price=2670.0,
            sl_price=2660.0,
            tp_price=2685.0,
            df=self.df_test,
        )

        # In shadow mode, it must never block the trade
        self.assertTrue(approved)
        self.assertIsNotNone(conf)
        self.assertIn("Shadow:", reason)
        self.assertIsNotNone(v_dict)
        self.assertIn("drift_pct", v_dict)
        self.assertIn("projected_mfe_points", v_dict)

    def test_kronos_filter_active_mode_enforces_veto(self):
        """In active mode, confidence below threshold must veto the signal."""
        k_filter = KronosFilter(
            enabled=True,
            shadow_mode=False,
            min_confidence=0.99,  # Impossibly high threshold
        )
        self.assertTrue(k_filter.is_active)

        approved, conf, reason, v_dict = k_filter.evaluate(
            symbol="XAUUSD",
            direction="SELL",  # Opposing an upward trend in test data
            entry_price=2670.0,
            sl_price=2675.0,
            tp_price=2650.0,
            df=self.df_test,
        )

        self.assertFalse(approved)
        self.assertIn("Kronos Veto", reason)

    def test_disabled_filter_behavior(self):
        """Disabled filter must seamlessly pass through without overhead."""
        k_filter = KronosFilter(enabled=False)
        self.assertFalse(k_filter.is_active)

        approved, conf, reason, v_dict = k_filter.evaluate(
            symbol="XAUUSD",
            direction="BUY",
            entry_price=2670.0,
            sl_price=2660.0,
            tp_price=2685.0,
            df=self.df_test,
        )
        self.assertTrue(approved)
        self.assertIsNone(conf)
        self.assertIn("disabled", reason.lower())
        self.assertIsNone(v_dict)


if __name__ == "__main__":
    unittest.main()
