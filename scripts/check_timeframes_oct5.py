import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone, timedelta

if mt5.initialize():
    # Let's inspect M15 for Oct 5 ~14:00
    for tf_name, tf in [("M1", mt5.TIMEFRAME_M1), ("M5", mt5.TIMEFRAME_M5), ("M15", mt5.TIMEFRAME_M15), ("M30", mt5.TIMEFRAME_M30), ("H1", mt5.TIMEFRAME_H1)]:
        # Oct 5 14:00
        bars = mt5.copy_rates_from("Boom 500 Index", tf, datetime(2026, 10, 5, 14, 15, tzinfo=timezone.utc), 10)
        df = pd.DataFrame(bars)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        # also forward bars
        bars_fwd = mt5.copy_rates_from_pos("Boom 500 Index", tf, 0, 100)
        # Let's print bars around 2026-10-05 14:07 in M15
        rates_oct5 = mt5.copy_rates_range("Boom 500 Index", tf, datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc), datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc))
        if rates_oct5 is not None and len(rates_oct5) > 0:
            df_oct5 = pd.DataFrame(rates_oct5)
            df_oct5['time'] = pd.to_datetime(df_oct5['time'], unit='s', utc=True)
            print(f"\n--- {tf_name} Oct 5 (12:00 to 18:00) len={len(df_oct5)} ---")
            print(df_oct5[['time', 'open', 'high', 'low', 'close']].to_string())

    mt5.shutdown()
