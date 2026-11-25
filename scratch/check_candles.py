import sys, os
sys.path.insert(0, os.path.abspath("."))
from execution.mt5_connector import MT5Connector
import pandas as pd
import datetime

conn = MT5Connector()
if conn.initialize():
    import MetaTrader5 as mt5
    rates = mt5.copy_rates_from_pos('XAUUSD', mt5.TIMEFRAME_H1, 0, 10)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    print("=== H1 CANDLES (Latest 10) ===")
    for _, r in df.iterrows():
        print(f"{r['time']} | Open: {r['open']:.2f} | High: {r['high']:.2f} | Low: {r['low']:.2f} | Close: {r['close']:.2f}")

    rates_m15 = mt5.copy_rates_from_pos('XAUUSD', mt5.TIMEFRAME_M15, 0, 24)
    df15 = pd.DataFrame(rates_m15)
    df15['time'] = pd.to_datetime(df15['time'], unit='s')
    print("\n=== M15 CANDLES (Recent 6 hours) ===")
    for _, r in df15.iterrows():
        print(f"{r['time']} | Open: {r['open']:.2f} | High: {r['high']:.2f} | Low: {r['low']:.2f} | Close: {r['close']:.2f}")
    conn.shutdown()
else:
    print("Failed to init MT5")
