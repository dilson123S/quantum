import unittest
from datetime import datetime, timezone
import pandas as pd
from unittest.mock import MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath("."))

from core.config_loader import load_config
from core.models import Direction
from strategy.asian_breakdown import AsianBreakdownStrategy
from execution.position_manager import PositionManager


class TestDeepMarketValidation(unittest.TestCase):
    def setUp(self):
        self.config = load_config("config/bot_a_gold")
        self.strat = AsianBreakdownStrategy(self.config)

    def test_rejected_flawed_trade_today(self):
        """Verify that the flawed setup from today at 19:00 UTC is strictly rejected."""
        # Simulated H1 candles (length >= 5) matching today at 19:00 UTC (14:00 local)
        df_entry = pd.DataFrame([
            {
                "time": "2026-09-21 19:00:00+00:00",
                "open": 4346.08,
                "high": 4346.11,
                "low": 4342.21,
                "close": 4343.33,
                "tick_volume": 1545,
                "atr": 15.98,
                "adx": 14.01,  # Low ADX (< 20)
                "volume_ratio": 0.76,  # Low Volume (< 1.10)
            }
        ] * 10)

        # H4 setup
        df_setup = pd.DataFrame([{"close": 4343.31, "ema_fast": 4357.2, "ema_medium": 4349.6, "ema_slow": 4350.7}] * 25)

        # D1 HTF (Daily is Bullish: Close > EMA20 and EMA50)
        df_htf = pd.DataFrame([{"close": 4378.11}] * 25)

        # Decision context with Asian level
        ctx = {
            "liquidity": {
                "asia": {"complete": True, "high": 4383.37, "low": 4346.37},
                "previous_day": {"low": 4334.25, "high": 4399.65}
            }
        }

        # Run detection
        sig = self.strat.detect_asian_breakdown(
            symbol="XAUUSD",
            timeframe="H1",
            df=df_entry,
            decision_context=ctx,
            df_setup=df_setup,
            df_htf=df_htf,
        )

        # MUST BE REJECTED!
        self.assertIsNone(sig, "Flawed setup from today MUST be rejected by deep filters!")
        print("[PASS] Today's flawed setup was strictly REJECTED by AsianBreakdownStrategy.")

    def test_valid_institutional_breakdown(self):
        """Verify that a genuine institutional breakdown during London open with high ADX & volume is APPROVED."""
        df_entry = pd.DataFrame([
            {
                "time": "2026-09-21 08:00:00+00:00",  # Inside window 07:00 - 16:00 UTC
                "open": 4348.00,
                "high": 4348.50,
                "low": 4340.00,
                "close": 4342.00,
                "tick_volume": 3500,
                "atr": 10.0,
                "adx": 26.5,  # Strong trend (> 20)
                "volume_ratio": 1.45,  # High volume (> 1.10)
            }
        ] * 10)

        # H4 setup is bearish (close < ema_slow and ema_fast < ema_med)
        df_setup = pd.DataFrame([{"close": 4340.0, "ema_fast": 4342.0, "ema_medium": 4348.0, "ema_slow": 4355.0}] * 25)

        # D1 HTF is neutral or bearish (close < ema_20)
        df_htf = pd.DataFrame([{"close": 4330.0}] * 25)

        ctx = {
            "liquidity": {
                "asia": {"complete": True, "high": 4360.0, "low": 4345.0},
                "previous_day": {"low": 4320.0, "high": 4380.0}
            }
        }

        sig = self.strat.detect_asian_breakdown(
            symbol="XAUUSD",
            timeframe="H1",
            df=df_entry,
            decision_context=ctx,
            df_setup=df_setup,
            df_htf=df_htf,
        )

        self.assertIsNotNone(sig, "Valid institutional breakdown should be approved!")
        self.assertEqual(sig.direction, Direction.SELL)
        # Verify robust SL distance: must be >= 0.75 ATR ($7.50 USD)
        sl_dist = sig.sl_price - sig.entry_price
        self.assertGreaterEqual(sl_dist, 7.5, f"SL distance ({sl_dist}) must be at least 0.75 * ATR")
        print(f"[PASS] Valid institutional breakdown approved with robust SL distance: ${sl_dist:.2f} (Entry: {sig.entry_price}, SL: {sig.sl_price})")

    def test_atomic_break_even_post_harvest(self):
        """Verify that executing partial close atomically moves remaining SL to Break-Even."""
        connector = MagicMock()
        order_manager = MagicMock()
        repo = None
        
        pos_mgr = PositionManager(connector, order_manager, self.config, repo)
        
        # Position mock: entry 4345.80, initial SL 4352.00 (risk = 6.20).
        # At 0.5R profit distance = 3.10 -> price at 4342.00 gives 3.80 profit distance (> 0.5R).
        pos = MagicMock()
        pos.ticket = 12345
        pos.symbol = "XAUUSD"
        pos.type = 1  # SELL
        pos.volume = 0.20
        pos.price_open = 4345.80
        pos.price_current = 4342.00
        pos.sl = 4352.00
        pos.tp = 4334.00
        
        sym_info = MagicMock()
        sym_info.point = 0.01
        sym_info.digits = 2
        sym_info.volume_min = 0.01
        sym_info.volume_step = 0.01
        connector.get_symbol_info.return_value = sym_info
        
        order_manager.close_order.return_value = MagicMock(success=True)
        order_manager.modify_sltp.return_value = MagicMock(success=True)
        
        closed = pos_mgr.check_partial_close(pos)
        self.assertTrue(closed)
        
        # Verify order_manager.modify_sltp was called to set Break-Even
        order_manager.modify_sltp.assert_called_once()
        call_kwargs = order_manager.modify_sltp.call_args[1]
        # For SELL, BE SL should be entry - spread_buffer (4345.80 - 0.10 = 4345.70)
        self.assertAlmostEqual(call_kwargs["sl"], 4345.70, places=2)
        print(f"[PASS] Atomic Break-Even confirmed! SL moved post-harvest to: {call_kwargs['sl']}")


if __name__ == "__main__":
    unittest.main()
