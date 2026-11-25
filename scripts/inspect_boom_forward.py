import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone

def inspect_forward_bars(name, start_dt, count=25, tf=mt5.TIMEFRAME_M5):
    bars = mt5.copy_rates_from("Boom 500 Index", tf, start_dt, count)
    df = pd.DataFrame(bars)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    print(f"\n=== {name} ===")
    print(df[['time', 'open', 'high', 'low', 'close', 'tick_volume']])

if mt5.initialize():
    # Ticket 9829245429 / 9829280543 (Oct 5 from 14:00 to 16:00 UTC)
    inspect_forward_bars("Oct 5 ~14:00 (Ticket 9829245429/80543) M5", datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc), 20, mt5.TIMEFRAME_M5)
    inspect_forward_bars("Oct 5 ~14:00 (Ticket 9829245429/80543) M15", datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc), 15, mt5.TIMEFRAME_M15)
    inspect_forward_bars("Oct 5 ~14:00 (Ticket 9829245429/80543) H1", datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc), 10, mt5.TIMEFRAME_H1)

    # Ticket 9830027488 (Oct 6 from 04:00 to 07:00 UTC)
    inspect_forward_bars("Oct 6 ~04:20 (Ticket 9830027488) M5", datetime(2026, 10, 6, 4, 15, tzinfo=timezone.utc), 20, mt5.TIMEFRAME_M5)
    inspect_forward_bars("Oct 6 ~04:20 (Ticket 9830027488) M15", datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc), 15, mt5.TIMEFRAME_M15)
    inspect_forward_bars("Oct 6 ~04:20 (Ticket 9830027488) H1", datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc), 10, mt5.TIMEFRAME_H1)
    mt5.shutdown()
