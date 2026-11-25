import sys
sys.path.insert(0, '.')
import MetaTrader5 as mt5
import pandas as pd
from strategy.synthetic_volatility import SyntheticVolatilityStrategy

mt5.initialize()
strat = SyntheticVolatilityStrategy(
    ema_fast=20, ema_medium=50, ema_slow=200, atr_period=14,
    sl_buffer_atr=0.70, target_rr=2.20, partial_rr=1.35, min_rr=1.40
)

rates_m5 = mt5.copy_rates_from_pos('Volatility 75 Index', mt5.TIMEFRAME_M5, 0, 120)
rates_m15 = mt5.copy_rates_from_pos('Volatility 75 Index', mt5.TIMEFRAME_M15, 0, 80)
rates_h1 = mt5.copy_rates_from_pos('Volatility 75 Index', mt5.TIMEFRAME_H1, 0, 50)

df_m5_all = pd.DataFrame(rates_m5)
df_m5_all['time_utc'] = pd.to_datetime(df_m5_all['time'], unit='s')
df_m15_all = pd.DataFrame(rates_m15)
df_m15_all['time_utc'] = pd.to_datetime(df_m15_all['time'], unit='s')
df_h1_all = pd.DataFrame(rates_h1)
df_h1_all['time_utc'] = pd.to_datetime(df_h1_all['time'], unit='s')

target_times = ['2026-10-06 02:55', '2026-10-06 03:00', '2026-10-06 03:05', '2026-10-06 03:10']

for target_t in target_times:
    print(f"\n=================== BARRA {target_t} ===================")
    sub_m5 = df_m5_all[df_m5_all['time_utc'] <= pd.Timestamp(target_t)].copy()
    sub_m15 = df_m15_all[df_m15_all['time_utc'] <= pd.Timestamp(target_t)].copy()
    sub_h1 = df_h1_all[df_h1_all['time_utc'] <= pd.Timestamp(target_t)].copy()
    
    df_calc = strat.calculate_indicators(sub_m5.copy())
    curr = df_calc.iloc[-1]
    prev = df_calc.iloc[-2]
    atr = curr['atr']
    c_close = curr['close']
    c_open = curr['open']
    ema_fast = curr['ema_fast']
    ema_med = curr['ema_med']
    ema_slow = curr['ema_slow']
    
    # 1. Check Sweep
    sw = strat.detect_liquidity_sweep(df_calc, lookback=25)
    print(f"1. Sweep detection: {sw}")
    
    # 2. Check Trend Pullback condition
    # Bullish condition: (ema_fast > ema_med) and (c_close > ema_slow and ema_med > ema_slow)
    cond_cross = (ema_fast > ema_med)
    cond_slow = (c_close > ema_slow and ema_med > ema_slow)
    print(f"2. Pullback trend: ema_fast>med: {cond_cross}, close>slow & med>slow: {cond_slow} (c_close={c_close:.1f}, ema_slow={ema_slow:.1f})")
    
    # Check if price touched EMA-20:
    recent_lows = float(df_calc["low"].iloc[-5:-1].min())
    recent_closes = float(df_calc["close"].iloc[-5:-1].min())
    touched_ema20 = recent_lows <= ema_fast and recent_closes >= (ema_med - 0.25 * atr)
    rsi_range = (36.0 <= curr['rsi'] <= 56.0)
    rejection = (c_close > c_open and c_close >= ema_fast)
    print(f"   touched_ema20: {touched_ema20}, rsi_range(36-56): {rsi_range} (rsi={curr['rsi']:.1f}), rejection: {rejection}")
    
    # 3. Check Squeeze
    squeeze_recent = bool(df_calc["squeeze_on"].iloc[-8:-1].any())
    bb_upper = curr['bb_upper']
    atr_sma = curr['atr_sma']
    print(f"3. Squeeze recent: {squeeze_recent}, close > bb_upper: {c_close > bb_upper}, atr >= 1.05*atr_sma: {atr >= (atr_sma * 1.05)}")
