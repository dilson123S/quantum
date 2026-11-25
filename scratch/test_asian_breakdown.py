import sys, os
sys.path.insert(0, os.path.abspath("."))

from core.config_loader import load_config
from core.constants import Direction
from execution.mt5_connector import MT5Connector
from data.downloader import DataDownloader
from data.cleaner import DataCleaner
from indicators.trend import calculate_all_trend_indicators
from indicators.volatility import calculate_all_volatility_indicators
from strategy.asian_breakdown import AsianBreakdownStrategy
from strategy.decision_context import build_decision_context
from indicators.structure import get_market_structure

connector = MT5Connector()
if not connector.initialize():
    print("Could not initialize MT5!")
    sys.exit(1)

downloader = DataDownloader()
cleaner = DataCleaner()

print("Connected to MT5 successfully.")
df_h1_raw = downloader.download_rates("XAUUSD", "H1", count=100)
df_h4_raw = downloader.download_rates("XAUUSD", "H4", count=100)
df_d1_raw = downloader.download_rates("XAUUSD", "D1", count=100)

df_h1, _ = cleaner.clean(df_h1_raw)
df_h4, _ = cleaner.clean(df_h4_raw)
df_d1, _ = cleaner.clean(df_d1_raw)

cfg = load_config("config/bot_a_gold")

df_h4 = calculate_all_trend_indicators(
    df_h4,
    ema_fast_period=cfg.trend.ema_fast,
    ema_medium_period=cfg.trend.ema_medium,
    ema_slow_period=cfg.trend.ema_slow,
    adx_period=cfg.trend.adx_period,
)
df_h1 = calculate_all_trend_indicators(
    df_h1,
    ema_fast_period=cfg.trend.ema_fast,
    ema_medium_period=cfg.trend.ema_medium,
    ema_slow_period=cfg.trend.ema_slow,
    adx_period=cfg.trend.adx_period,
)
from indicators.momentum import calculate_all_momentum_indicators
from indicators.volume import calculate_all_volume_indicators
df_h1 = calculate_all_momentum_indicators(
    df_h1,
    rsi_period=cfg.momentum.rsi_period,
    macd_fast=cfg.momentum.macd_fast,
    macd_slow=cfg.momentum.macd_slow,
    macd_signal=cfg.momentum.macd_signal
)
df_h1 = calculate_all_volatility_indicators(
    df_h1,
    atr_period=cfg.volatility.atr_period,
    atr_avg_period=cfg.volatility.atr_avg_period
)
df_h1 = calculate_all_volume_indicators(
    df_h1,
    volume_period=cfg.volume.period,
    volume_col="tick_volume",
    use_obv=cfg.volume.use_obv
)

last_h4 = df_h4.iloc[-1]
print(f"H4 Latest bar: Close={last_h4['close']}, EMA20={last_h4['ema_fast']:.2f}, EMA50={last_h4['ema_medium']:.2f}, EMA200={last_h4['ema_slow']:.2f}")

# Look at recent H1 bars from today
print("\nRecent H1 bars:")
for i in range(-10, 0):
    b = df_h1.iloc[i]
    print(f"Bar {b['time']}: Open={b['open']:.2f}, High={b['high']:.2f}, Low={b['low']:.2f}, Close={b['close']:.2f}, ATR={b.get('atr', 0):.2f}")

# Let's test AsianBreakdownStrategy on each of the last 10 H1 bars
strat = AsianBreakdownStrategy(cfg)
print("\nTesting AsianBreakdownStrategy on recent bars:")
for i in range(-8, 0):
    sub_df = df_h1.iloc[:len(df_h1) + i + 1].copy()
    atr_series = sub_df['atr']
    struct = get_market_structure(
        df=sub_df,
        atr_series=atr_series,
        swing_lookback=cfg.structure.swing_lookback,
        swing_confirmation=cfg.structure.swing_confirmation,
        bos_min_distance_atr=cfg.structure.bos_min_distance_atr,
        bos_close_confirmation=cfg.structure.bos_close_confirmation,
        use_bos=cfg.structure.use_bos,
        use_choch=cfg.structure.use_choch,
        max_swing_age=cfg.structure.max_swing_age,
    )
    ctx = build_decision_context(
        sub_df, struct, Direction.SELL,
        min_atr_ratio=cfg.volatility.min_atr_ratio,
        max_atr_ratio=cfg.volatility.max_atr_ratio,
        adx_min=cfg.trend.adx_min,
        equal_level_tolerance_atr=cfg.decision_context.equal_level_tolerance_atr,
        fib_zone_atr=cfg.decision_context.fib_zone_atr,
        smc_max_age_bars=cfg.decision_context.smc_max_age_bars,
        smc_min_gap_atr=cfg.decision_context.smc_min_gap_atr,
        sweep_min_penetration_atr=cfg.decision_context.sweep_min_penetration_atr,
        min_bb_width_ratio=cfg.regime_filter.min_bb_width_ratio,
        max_range_bb_width_ratio=cfg.regime_filter.max_range_bb_width_ratio,
    )
    sig = strat.detect_asian_breakdown(
        symbol="XAUUSD",
        timeframe="H1",
        df=sub_df,
        decision_context=ctx,
        df_setup=df_h4,
        df_htf=df_d1,
        digits=2,
    )
    bar_time = sub_df.iloc[-1]['time']
    asia = ctx.get('liquidity', {}).get('asia', {})
    print(f"Bar {bar_time} | Asia Low: {asia.get('low')} Complete: {asia.get('complete')} | Result: {sig}")
    if sig is not None:
        from ml.features import extract_features
        feat = extract_features(
            sub_df,
            structure_score=30.0,
            trend_score=20.0,
            spread_points=15.0,
            atr_value=float(sub_df['atr'].iloc[-1]),
            point_size=0.01,
        )
        print("   >>> Features:", feat)
        from filters.ml_filter import MLFilter
        ml = MLFilter(enabled=True, shadow_mode=False, min_confidence=0.55)
        approved, prob, reason = ml.evaluate(
            symbol="XAUUSD",
            direction=sig.direction.value,
            df=sub_df,
            structure_score=30.0,
            trend_score=20.0,
            spread_points=15.0,
            atr_value=float(sub_df['atr'].iloc[-1]),
            point_size=0.01,
            total_score=85.0
        )
        print(f"   >>> ML Evaluation: Approved={approved}, Prob={f'{prob*100:.1f}%' if prob else 'N/A'}, Reason={reason}")

connector.shutdown()
