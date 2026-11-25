"""Unit tests for Deriv Synthetic Indices Trading System.

Tests cover:
1. DerivConnector contract specs and lot sizing logic.
2. SpikeSniperStrategy (Boom & Crash spike detection, directional rules, hazard rates).
3. SyntheticVolatilityStrategy (Continuous Brownian motion SMC & Mean Reversion).
4. SyntheticRiskManager (Asymmetric R:R, Circuit breakers, Position and Drawdown policies).
"""
import pytest
import pandas as pd
import numpy as np

from core.constants import Direction
from execution.deriv_connector import DerivConnector, DerivSymbolSpec
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal
from strategy.synthetic_volatility import SyntheticVolatilityStrategy, SyntheticVolatilitySignal
from risk.synthetic_risk import SyntheticRiskManager, SyntheticRiskConfig


class TestDerivConnector:
    """Tests for DerivConnector specifications and lot calculations."""

    def test_default_specs_fallback(self):
        connector = DerivConnector()
        # Verify fallback table has Boom, Crash, and Volatility specs
        boom_spec = connector.get_symbol_specs("Boom 1000 Index")
        assert boom_spec is not None
        assert boom_spec.min_volume == 0.20
        assert boom_spec.volume_step == 0.01

        crash_spec = connector.get_symbol_specs("Crash 1000 Index")
        assert crash_spec is not None
        assert crash_spec.min_volume == 0.20

        v75_spec = connector.get_symbol_specs("Volatility 75 Index")
        assert v75_spec is not None
        assert v75_spec.min_volume == 0.001
        assert v75_spec.volume_step == 0.001

    def test_spike_direction_detection(self):
        connector = DerivConnector()
        assert connector.get_spike_direction("Crash 1000 Index") == Direction.SELL
        assert connector.get_spike_direction("Crash 500 Index") == Direction.SELL
        assert connector.get_spike_direction("Crash 300 Index") == Direction.SELL

        assert connector.get_spike_direction("Boom 1000 Index") == Direction.BUY
        assert connector.get_spike_direction("Boom 500 Index") == Direction.BUY
        assert connector.get_spike_direction("Boom 300 Index") == Direction.BUY

        assert connector.get_spike_direction("Volatility 75 Index") is None
        assert connector.get_spike_direction("Step Index") is None

    def test_lot_size_calculation_boom(self):
        connector = DerivConnector()
        # Boom 1000: min_volume 0.20, step 0.01, contract_size 1.0
        # If risk_points = 50.0 and risk_money = 25.0 -> raw lot = 0.50
        lot = connector.calculate_lot_size("Boom 1000 Index", risk_money=25.0, sl_distance_price=50.0)
        assert lot == 0.50

        # If risk_points is huge (500), raw lot would be 0.05, which is < min_volume 0.20
        # Should be clamped to 0.20
        lot_clamped = connector.calculate_lot_size("Boom 1000 Index", risk_money=25.0, sl_distance_price=500.0)
        assert lot_clamped == 0.20

    def test_lot_size_calculation_v75(self):
        connector = DerivConnector()
        # V75: min_volume 0.001, step 0.001
        # If risk_points = 1000.0, risk_money = 10.0 -> raw lot = 0.010
        lot = connector.calculate_lot_size("Volatility 75 Index", risk_money=10.0, sl_distance_price=1000.0)
        assert lot == 0.010


class TestSpikeSniperStrategy:
    """Tests for Boom and Crash Spike Sniper Strategy."""

    def _generate_synthetic_crash_data(self, n_bars: int = 100) -> pd.DataFrame:
        """Create mock Crash price series: gradual rise with sudden downward spikes."""
        np.random.seed(42)
        base = 5000.0
        closes, opens, highs, lows = [], [], [], []

        curr = base
        for i in range(n_bars):
            o = curr
            if i in (20, 50, 80):
                c = o - 45.0  # Big drop spike
                h = o + 0.5
                l = c - 2.0
            else:
                c = o + 1.2   # Slow upward drift
                h = c + 0.8
                l = o - 0.2
            opens.append(o)
            closes.append(c)
            highs.append(h)
            lows.append(l)
            curr = c

        return pd.DataFrame({
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100] * n_bars,
        })

    def test_spike_detection_and_hazard(self):
        strategy = SpikeSniperStrategy()
        df = self._generate_synthetic_crash_data(100)
        df = strategy.calculate_indicators(df)

        assert "is_crash_spike" in df.columns

        # Spikes were injected at bar 20, 50, 80
        assert df["is_crash_spike"].iloc[20] == True
        assert df["is_crash_spike"].iloc[50] == True
        assert df["is_crash_spike"].iloc[80] == True

    def test_crash_only_allows_sell(self):
        strategy = SpikeSniperStrategy()
        df = self._generate_synthetic_crash_data(100)
        for idx in range(30, len(df)):
            sig = strategy.detect_spike_setup(symbol="Crash 1000 Index", df=df.iloc[:idx], point=0.001)
            if sig is not None:
                assert sig.direction == Direction.SELL
                assert sig.target_rr >= 2.0
                assert sig.tp_price < sig.entry_price
                assert sig.sl_price > sig.entry_price

    def test_boom_only_allows_buy(self):
        strategy = SpikeSniperStrategy()
        # Mock Boom data with upward spikes
        np.random.seed(42)
        base = 8000.0
        opens, highs, lows, closes = [], [], [], []
        curr = base
        for i in range(100):
            o = curr
            if i in (20, 50, 80):
                c = o + 45.0  # Spike UP
                h = c + 2.0
                l = o - 0.5
            else:
                c = o - 1.2   # Drift DOWN
                h = o + 0.2
                l = c - 0.8
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            curr = c

        df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": [100]*100})
        for idx in range(30, len(df)):
            sig = strategy.detect_spike_setup(symbol="Boom 1000 Index", df=df.iloc[:idx], point=0.001)
            if sig is not None:
                assert sig.direction == Direction.BUY
                assert sig.target_rr >= 2.0
                assert sig.tp_price > sig.entry_price
                assert sig.sl_price < sig.entry_price

    def test_boom_blocks_falling_knife_without_absorption(self):
        """Verify that a candle closing on its low (zero wick) during downward drift is blocked."""
        strategy = SpikeSniperStrategy(touch_atr=0.80, close_near_atr=1.10, boom_rsi_oversold=40.0)
        # Create 40 drift candles closing at dead lows
        closes = [10000.0 - (i * 2.0) for i in range(40)]
        opens = [c + 1.8 for c in closes]
        highs = [o + 0.1 for o in opens]
        lows = [c - 0.05 for c in closes] # Virtually no lower wick (< 3%)
        df_knife = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": [100]*40})

        sig = strategy.detect_spike_setup(symbol="Boom 1000 Index", df=df_knife, point=0.001)
        # MUST BE BLOCKED - catching a falling knife with zero absorption is strictly forbidden
        assert sig is None


class TestSyntheticVolatilityStrategy:
    """Tests for Continuous Volatility SMC & Mean Reversion Strategy."""

    def _generate_sine_walk_data(self, n_bars: int = 120) -> pd.DataFrame:
        """Create oscillating data that tests Bollinger Mean Reversion."""
        x = np.linspace(0, 6 * np.pi, n_bars)
        y = 500000.0 + 2000.0 * np.sin(x)
        noise = np.random.normal(0, 50.0, n_bars)
        closes = y + noise
        opens = np.roll(closes, 1)
        opens[0] = closes[0]
        highs = np.maximum(opens, closes) + 30.0
        lows = np.minimum(opens, closes) - 30.0

        return pd.DataFrame({
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [50] * n_bars,
        })

    def test_indicator_calculation_and_caching(self):
        strategy = SyntheticVolatilityStrategy()
        df = self._generate_sine_walk_data(60)
        df_ind = strategy.calculate_indicators(df)

        assert "bb_upper" in df_ind.columns
        assert "bb_lower" in df_ind.columns
        assert "atr" in df_ind.columns
        assert "rsi" in df_ind.columns

        # Test cache check does not recompute
        df_ind["custom_flag"] = 123
        df_cached = strategy.calculate_indicators(df_ind)
        assert "custom_flag" in df_cached.columns

    def test_mean_reversion_signal_generation(self):
        strategy = SyntheticVolatilityStrategy(enable_mean_reversion=True)
        df = self._generate_sine_walk_data(100)
        df_ind = strategy.calculate_indicators(df)

        # Force an oversold candle touching lower band
        df_ind.loc[df_ind.index[-1], "close"] = df_ind.loc[df_ind.index[-1], "bb_lower"] - 10.0
        df_ind.loc[df_ind.index[-1], "open"] = df_ind.loc[df_ind.index[-1], "close"] - 20.0  # close > open (bullish)
        df_ind.loc[df_ind.index[-1], "low"] = df_ind.loc[df_ind.index[-1], "open"] - 5.0
        df_ind.loc[df_ind.index[-1], "rsi"] = 25.0

        sig = strategy.evaluate_volatility_setup(symbol="Volatility 75 Index", df_m5=df_ind, point=0.001)
        assert sig is not None
        assert sig.direction == Direction.BUY
        assert sig.setup_model == "BOLLINGER_MEAN_REVERSION"
        assert sig.risk_reward >= 1.40

    def test_liquidity_sweep_requires_absorption_wick_and_choch(self):
        """Verify that a candle piercing low without absorption wick is rejected."""
        strategy = SyntheticVolatilityStrategy()
        # Create 35 bars
        closes = [500000.0 - (i * 10.0) for i in range(35)]
        opens = [c + 8.0 for c in closes]
        highs = [o + 2.0 for o in opens]
        lows = [c - 1.0 for c in closes]  # Low wick is tiny (1.0 / 11.0 ~ 9% < 25%)

        df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": [100] * 35})
        sweep = strategy.detect_liquidity_sweep(df, lookback=20)
        assert sweep is None  # Blocked due to lack of absorption wick!

        # Now simulate a true institutional absorption wick on prev_bar and strong bullish close on curr_bar
        # prev_bar (index -2): pierced local low (~499679), but rejected with large lower wick (50/60 = 83%) and closed back above
        df.loc[df.index[-2], "high"] = 499690.0
        df.loc[df.index[-2], "open"] = 499680.0
        df.loc[df.index[-2], "close"] = 499685.0  # Closes back above local_low (~499679)
        df.loc[df.index[-2], "low"] = 499630.0  # Pierced to 499630

        # curr_bar (index -1): bullish bar breaking above midpoint of prev_bar
        prev_mid = (499690.0 + 499630.0) / 2.0  # 499660
        df.loc[df.index[-1], "open"] = 499685.0
        df.loc[df.index[-1], "close"] = 499700.0  # Closes above midpoint and above open
        df.loc[df.index[-1], "high"] = 499705.0
        df.loc[df.index[-1], "low"] = 499682.0

        sweep = strategy.detect_liquidity_sweep(df, lookback=20)
        assert sweep is not None
        direction, sw_level = sweep
        assert direction == Direction.BUY
        assert sw_level == 499630.0  # Exactly the sweep low!

    def test_trend_pullback_continuation_detection(self):
        """Verify institutional trend pullback continuation detects Value Zone bounce with surgical SL."""
        strategy = SyntheticVolatilityStrategy()
        np.random.seed(42)
        t = np.linspace(0, 10, 60)
        trend = 500000.0 + t * 20.0
        cycle = 30.0 * np.sin(t * 3)
        closes = trend + cycle
        opens = np.roll(closes, 1)
        opens[0] = closes[0]
        highs = np.maximum(opens, closes) + 10.0
        lows = np.minimum(opens, closes) - 10.0
        df_m5 = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": [100]*60})
        df_m5 = strategy.calculate_indicators(df_m5)

        # Set indicators to test the setup trigger logic cleanly
        df_m5.loc[df_m5.index[-1], "rsi"] = 48.0
        df_m5.loc[df_m5.index[-1], "close"] = df_m5.loc[df_m5.index[-1], "ema_fast"] + 5.0
        df_m5.loc[df_m5.index[-1], "open"] = df_m5.loc[df_m5.index[-1], "close"] - 10.0
        df_m5.loc[df_m5.index[-1], "high"] = df_m5.loc[df_m5.index[-1], "close"] + 2.0
        df_m5.loc[df_m5.index[-1], "low"] = df_m5.loc[df_m5.index[-1], "open"] - 5.0
        df_m5.loc[df_m5.index[-2], "low"] = df_m5.loc[df_m5.index[-2], "ema_fast"] - 5.0

        df_h1 = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": [100]*60})
        df_m15 = df_h1.copy()

        sig = strategy.detect_trend_pullback_setup(
            symbol="Volatility 75 Index",
            df_m5=df_m5,
            df_m15=df_m15,
            df_h1=df_h1,
            point=0.01,
        )
        assert sig is not None
        assert sig.direction == Direction.BUY
        assert sig.setup_model == "INSTITUTIONAL_TREND_PULLBACK"
        assert sig.sl_price < sig.entry_price
        assert sig.tp_price > sig.entry_price
        assert sig.risk_points < 50.0  # Surgical SL (22.3 pts)

    def test_squeeze_expansion_detection(self):
        """Verify Carter Squeeze release detection."""
        strategy = SyntheticVolatilityStrategy()
        # Create compressed bars (low volatility) then breakout
        closes = [500000.0 + (i % 2) * 2.0 for i in range(40)]
        # Breakout bar
        closes.append(500050.0)
        opens = [500000.0] * 40 + [500010.0]
        highs = [c + 3.0 for c in closes]
        highs[-1] = 500055.0
        lows = [c - 3.0 for c in closes]
        lows[-1] = 500008.0

        df_m5 = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": [100] * 41})
        df_m5 = strategy.calculate_indicators(df_m5)

        df_h1 = pd.DataFrame({
            "open": [500000.0 + i * 50 for i in range(40)],
            "high": [500000.0 + i * 50 + 40 for i in range(40)],
            "low": [500000.0 + i * 50 - 10 for i in range(40)],
            "close": [500000.0 + i * 50 + 35 for i in range(40)],
            "volume": [500] * 40,
        })

        sig = strategy.detect_squeeze_expansion_setup(
            symbol="Volatility 75 Index",
            df_m5=df_m5,
            df_h1=df_h1,
            point=0.01,
        )
        if sig is not None:
            assert sig.direction == Direction.BUY
            assert sig.setup_model == "VOLATILITY_SQUEEZE_EXPANSION"


class TestSyntheticRiskManager:
    """Tests for Synthetic Risk Manager and Asymmetric R:R mechanics."""

    def test_max_open_trades_limit(self):
        connector = DerivConnector()
        cfg = SyntheticRiskConfig(max_open_positions=2, max_positions_per_symbol=1)
        rm = SyntheticRiskManager(connector=connector, config=cfg)
        rm.reset_day(starting_balance=1000.0)

        # Allowed when 0 open trades
        can_open, reason = rm.can_open_trade("Boom 1000 Index", 1000.0, 1000.0, open_positions_count=0, symbol_positions_count=0)
        assert can_open is True

        # Rejected when max symbol trades reached
        can_open, reason = rm.can_open_trade("Boom 1000 Index", 1000.0, 1000.0, open_positions_count=1, symbol_positions_count=1)
        assert can_open is False
        assert "Max positions" in reason

        # Allowed for different symbol if under portfolio max
        can_open, reason = rm.can_open_trade("Crash 1000 Index", 1000.0, 1000.0, open_positions_count=1, symbol_positions_count=0)
        assert can_open is True

        # Rejected when total open trades reaches max
        can_open, reason = rm.can_open_trade("Crash 1000 Index", 1000.0, 1000.0, open_positions_count=2, symbol_positions_count=0)
        assert can_open is False
        assert "Max open positions reached" in reason

    def test_daily_drawdown_circuit_breaker(self):
        connector = DerivConnector()
        cfg = SyntheticRiskConfig(max_daily_loss_pct=3.0)
        rm = SyntheticRiskManager(connector=connector, config=cfg)
        rm.reset_day(starting_balance=1000.0)

        # Equity drops to 960 (4% daily drawdown, exceeds 3%)
        can_open, reason = rm.can_open_trade("Boom 1000 Index", 960.0, 960.0, open_positions_count=0, symbol_positions_count=0)
        assert can_open is False
        assert "Daily loss limit breached" in reason

    def test_calculate_trade_risk(self):
        connector = DerivConnector()
        cfg = SyntheticRiskConfig(risk_mode="percent", risk_percent=1.0)
        rm = SyntheticRiskManager(connector=connector, config=cfg)

        # Account balance $5000, 1% risk = $50 target
        # Boom 1000 Index: entry 8000, SL 7950 (50 points distance)
        lot, actual_risk = rm.calculate_trade_risk("Boom 1000 Index", entry_price=8000.0, sl_price=7950.0, account_balance=5000.0)
        assert lot == 1.0  # 50 points * 1 lot = $50
        assert actual_risk == 50.0


class TestSyntheticSystemOne:
    """Tests for Open-Source System 1 Decision Architecture on Synthetic Indices."""

    def test_synthetic_system_one_evaluation_contract(self):
        from filters.system_one.hub import SystemOneHub
        from strategy.synthetic_system_one import SyntheticSystemOneEvaluator

        hub = SystemOneHub(enabled=True, provider_name="nanojev", shadow_mode=True)
        evaluator = SyntheticSystemOneEvaluator(hub=hub, shadow_mode=False)

        dates = pd.date_range("2026-09-28 08:00", periods=50, freq="5min")
        df_m5 = pd.DataFrame({
            "open": [16440.0] * 48 + [16432.0, 16431.0],
            "high": [16450.0] * 48 + [16435.0, 16434.0],
            "low": [16430.0] * 48 + [16428.0, 16429.0],
            "close": [16445.0] * 48 + [16431.0, 16432.0],
            "atr": [4.5] * 50,
            "rsi": [45.0] * 50,
        }, index=dates)

        df_h1 = pd.DataFrame({
            "close": [16420.0, 16440.0],
            "ema_50": [16380.0, 16390.0],
        })

        res = evaluator.evaluate_setup(
            symbol="Boom 1000 Index",
            direction=Direction.BUY,
            entry_price=16432.0,
            sl_price=16424.0,
            tp_price=16456.0,
            target_rr=3.0,
            df_m5=df_m5,
            df_m15=None,
            df_h1=df_h1,
            confluences=("H1_REGIME_BULLISH", "JUMP_CLUSTER_CONTINUATION", "DEMAND_ZONE_ABSORPTION"),
        )
        assert res.approved is True
        assert res.thesis_alignment == "aligned"
        assert res.evidence_score >= 3
        assert res.trap_risk == "genuine_expansion"

    def test_is_structure_holding_crash(self):
        strategy = SpikeSniperStrategy()
        dates = pd.date_range("2026-10-01", periods=40, freq="5min")
        # Crash 1000: ceiling at 6070.0, price touches 6069.0 multiple times but closes below 6065.0
        df = pd.DataFrame({
            "open": [6060.0] * 35 + [6064.0, 6066.0, 6068.0, 6067.0, 6062.0],
            "high": [6065.0] * 35 + [6068.0, 6069.5, 6070.0, 6069.0, 6065.0],
            "low": [6058.0] * 35 + [6062.0, 6064.0, 6065.0, 6061.0, 6055.0],
            "close": [6062.0] * 35 + [6066.0, 6067.0, 6066.0, 6063.0, 6056.0],
            "atr": [3.5] * 40,
            "rsi": [55.0] * 40,
        }, index=dates)

        is_holding, msg, touches, ceiling = strategy.is_structure_holding(
            "Crash 1000 Index", Direction.SELL, entry_price=6065.0, df=df
        )
        assert is_holding is True
        assert touches >= 3
        assert ceiling == 6070.0

        # Break the ceiling with strong close above 6070 + buffer
        df_broken = df.copy()
        df_broken.iloc[-1, df_broken.columns.get_loc("close")] = 6073.0
        is_holding_b, msg_b, _, _ = strategy.is_structure_holding(
            "Crash 1000 Index", Direction.SELL, entry_price=6065.0, df=df_broken
        )
        assert is_holding_b is False
        assert "CEILING_BROKEN" in msg_b

    def test_elastic_gating_clean_trend(self):
        from filters.system_one.hub import SystemOneHub
        from strategy.synthetic_system_one import SyntheticSystemOneEvaluator, ExecutionTier

        hub = SystemOneHub(enabled=True, provider_name="nanojev", shadow_mode=False, device="cpu")
        evaluator = SyntheticSystemOneEvaluator(hub=hub, shadow_mode=False, min_evidence_score=3, min_confidence=0.50)

        dates = pd.date_range("2026-10-01", periods=60, freq="5min")
        # Clean trending low chop data
        df_m5 = pd.DataFrame({
            "open": np.linspace(100.0, 150.0, 60),
            "high": np.linspace(101.0, 151.0, 60),
            "low": np.linspace(99.5, 149.5, 60),
            "close": np.linspace(100.5, 150.5, 60),
            "atr": [1.5] * 60,
            "rsi": [52.0] * 60,
            "chop": [45.0] * 60, # Clean trend (< 55)
            "ker": [0.42] * 60,  # High efficiency (> 0.30)
            "ema_fast": np.linspace(100.0, 150.0, 60),
            "ema_med": np.linspace(95.0, 145.0, 60),
            "ema_slow": np.linspace(90.0, 140.0, 60),
            "ema_med_slope": [0.15] * 60,
        }, index=dates)

        res = evaluator.evaluate_setup(
            symbol="Volatility 75 Index",
            direction=Direction.BUY,
            entry_price=150.5,
            sl_price=148.5,
            tp_price=155.0,
            target_rr=2.2,
            df_m5=df_m5,
            df_m15=None,
            df_h1=None,
            confluences=("INSTITUTIONAL_VALUE_ZONE_RETEST", "M5_PULLBACK_EMA20", "ABSORPTION_WICK_CONFIRMED"),
        )
        assert res.approved is True
        assert res.tier in (ExecutionTier.TIER_A_ALPHA.value, ExecutionTier.TIER_B_SURGICAL.value)


