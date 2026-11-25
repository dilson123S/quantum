import sys
sys.path.insert(0, '.')
import MetaTrader5 as mt5
import pandas as pd
import datetime
from strategy.synthetic_volatility import SyntheticVolatilityStrategy

mt5.initialize()
strat = SyntheticVolatilityStrategy(
    ema_fast=20, ema_medium=50, ema_slow=200, atr_period=14,
    sl_buffer_atr=0.70, target_rr=2.20, partial_rr=1.35, min_rr=1.40
)

# Fetch bars around that time
# 2026-10-05 21:30 local is 2026-10-06 02:30 UTC
rates_m5 = mt5.copy_rates_from_pos('Volatility 75 Index', mt5.TIMEFRAME_M5, 0, 120)
rates_m15 = mt5.copy_rates_from_pos('Volatility 75 Index', mt5.TIMEFRAME_M15, 0, 80)
rates_h1 = mt5.copy_rates_from_pos('Volatility 75 Index', mt5.TIMEFRAME_H1, 0, 50)

df_m5_all = pd.DataFrame(rates_m5)
df_m5_all['time_utc'] = pd.to_datetime(df_m5_all['time'], unit='s')
df_m15_all = pd.DataFrame(rates_m15)
df_m15_all['time_utc'] = pd.to_datetime(df_m15_all['time'], unit='s')
df_h1_all = pd.DataFrame(rates_h1)
df_h1_all['time_utc'] = pd.to_datetime(df_h1_all['time'], unit='s')

print("Evaluating bars from 02:30 UTC to 03:30 UTC:")
# Find index in df_m5_all corresponding to 02:30 UTC
for i in range(len(df_m5_all) - 30, len(df_m5_all)):
    t = df_m5_all.iloc[i]['time_utc']
    if t < pd.Timestamp('2026-10-06 02:30') or t > pd.Timestamp('2026-10-06 03:35'):
        continue
    
    sub_m5 = df_m5_all.iloc[:i+1].copy()
    # slice m15 and h1 up to t
    sub_m15 = df_m15_all[df_m15_all['time_utc'] <= t].copy()
    sub_h1 = df_h1_all[df_h1_all['time_utc'] <= t].copy()
    
    # Calculate indicators
    df_calc = strat.calculate_indicators(sub_m5.copy())
    curr = df_calc.iloc[-1]
    
    # Check H1 trend
    h1_calc = strat.calculate_indicators(sub_h1.copy())
    h1_bar = h1_calc.iloc[-2] if len(h1_calc) >= 2 else h1_calc.iloc[-1]
    h1_bull = float(h1_bar["close"]) > float(h1_bar["ema_med"])
    
    # Check M15 trend
    m15_calc = strat.calculate_indicators(sub_m15.copy())
    m15_bar = m15_calc.iloc[-2] if len(m15_calc) >= 2 else m15_calc.iloc[-1]
    m15_bull = float(m15_bar["close"]) > float(m15_bar["ema_med"])
    
    # Try strategy
    sig = strat.evaluate_volatility_setup('Volatility 75 Index', sub_m5, sub_m15, sub_h1)
    
    c_close = curr['close']
    ema_fast = curr['ema_fast']
    ema_med = curr['ema_med']
    ema_slow = curr['ema_slow']
    rsi = curr['rsi']
    squeeze = curr.get('squeeze_on', False)
    
    sig_str = f"SIGNAL: {sig.direction.value} {sig.setup_model}" if sig else "None"
    print(f"Bar {t} | Close: {c_close:.2f} | H1_bull: {h1_bull} | M15_bull: {m15_bull} | EMA20>50: {ema_fast > ema_med} | RSI: {rsi:.1f} | Sig: {sig_str}")
