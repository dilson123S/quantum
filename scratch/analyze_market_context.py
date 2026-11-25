import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

mt5.initialize()

# Download H4
rates_h4 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H4, 0, 30)
df_h4 = pd.DataFrame(rates_h4)
df_h4["time"] = pd.to_datetime(df_h4["time"], unit="s")
df_h4["ema_fast"] = df_h4["close"].ewm(span=9, adjust=False).mean()
df_h4["ema_med"] = df_h4["close"].ewm(span=21, adjust=False).mean()
df_h4["ema_slow"] = df_h4["close"].ewm(span=50, adjust=False).mean()

print("=== H4 LATEST CANDLES ===")
print(df_h4[["time", "open", "high", "low", "close", "ema_fast", "ema_med", "ema_slow"]].tail(5).to_string())

# Download H1
rates_h1 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 24)
df_h1 = pd.DataFrame(rates_h1)
df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s")

print("\n=== H1 CANDLES TODAY ===")
for idx, r in df_h1.iterrows():
    print(f"{r['time']} | O: {r['open']} | H: {r['high']} | L: {r['low']} | C: {r['close']}")

# Download D1
rates_d1 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 5)
df_d1 = pd.DataFrame(rates_d1)
df_d1["time"] = pd.to_datetime(df_d1["time"], unit="s")
print("\n=== D1 CANDLES ===")
for idx, r in df_d1.iterrows():
    print(f"{r['time']} | O: {r['open']} | H: {r['high']} | L: {r['low']} | C: {r['close']}")

mt5.shutdown()
