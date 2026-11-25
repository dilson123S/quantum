import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone

if mt5.initialize():
    t_dt = datetime(2026, 10, 5, 14, 7, tzinfo=timezone.utc)
    
    # H1
    h1_bars = mt5.copy_rates_from("Boom 500 Index", mt5.TIMEFRAME_H1, t_dt, 60)
    df_h1 = pd.DataFrame(h1_bars)
    df_h1['time'] = pd.to_datetime(df_h1['time'], unit='s', utc=True)
    df_h1['ema50'] = df_h1['close'].ewm(span=50, adjust=False).mean()
    print("=== H1 at Oct 5 14:07 ===")
    print(df_h1.tail(5)[['time', 'open', 'high', 'low', 'close', 'ema50']])

    # M15
    m15_bars = mt5.copy_rates_from("Boom 500 Index", mt5.TIMEFRAME_M15, t_dt, 60)
    df_m15 = pd.DataFrame(m15_bars)
    df_m15['time'] = pd.to_datetime(df_m15['time'], unit='s', utc=True)
    df_m15['ema50'] = df_m15['close'].ewm(span=50, adjust=False).mean()
    print("\n=== M15 at Oct 5 14:07 ===")
    print(df_m15.tail(5)[['time', 'open', 'high', 'low', 'close', 'ema50']])

    # M5
    m5_bars = mt5.copy_rates_from("Boom 500 Index", mt5.TIMEFRAME_M5, t_dt, 60)
    df_m5 = pd.DataFrame(m5_bars)
    df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s', utc=True)
    df_m5['ema50'] = df_m5['close'].ewm(span=50, adjust=False).mean()
    print("\n=== M5 at Oct 5 14:07 ===")
    print(df_m5.tail(5)[['time', 'open', 'high', 'low', 'close', 'ema50']])

    mt5.shutdown()
