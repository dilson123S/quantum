import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone

def inspect_bars(ticket, entry_time_epoch, timeframe_name, tf):
    t_dt = datetime.fromtimestamp(entry_time_epoch, tz=timezone.utc)
    bars = mt5.copy_rates_from("Boom 500 Index", tf, t_dt, 30)
    if bars is None or len(bars) == 0:
        print(f"No bars for {timeframe_name}")
        return
    df = pd.DataFrame(bars)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    print(f"\n--- Ticket {ticket} ({timeframe_name}) Around Entry ({t_dt}) ---")
    print(df[['time', 'open', 'high', 'low', 'close', 'tick_volume']].tail(15))

if mt5.initialize():
    # Ticket 9829245429 (Oct 5 14:07 UTC)
    inspect_bars(9829245429, 1791209262, "M5", mt5.TIMEFRAME_M5)
    inspect_bars(9829245429, 1791209262, "M1", mt5.TIMEFRAME_M1)

    # Ticket 9830027488 (Oct 6 04:25 UTC)
    inspect_bars(9830027488, 1791260702, "M5", mt5.TIMEFRAME_M5)
    inspect_bars(9830027488, 1791260702, "M1", mt5.TIMEFRAME_M1)
    mt5.shutdown()
