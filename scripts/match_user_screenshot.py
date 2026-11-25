import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone, timedelta

if mt5.initialize():
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=2)

    # Search in M5
    rates = mt5.copy_rates_range("Boom 500 Index", mt5.TIMEFRAME_M5, start_dt, end_dt)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)

    # Look for deals
    deals = mt5.history_deals_get(start_dt, end_dt)
    boom_deals = [d for d in deals if "Boom 500" in d.symbol and d.entry == 0]
    print(f"Entry deals found: {len(boom_deals)}")
    for d in boom_deals:
        d_time = datetime.fromtimestamp(d.time, tz=timezone.utc)
        print(f"\nDeal ticket={d.ticket}, order={d.order}, time={d_time}, price={d.price}, vol={d.volume}, comment={d.comment}")
        # print 5 bars before and 10 bars after in M5
        idx = df[df['time'] <= d_time].index[-1]
        sub = df.loc[max(0, idx-5):min(len(df)-1, idx+10)].copy()
        sub['is_entry'] = sub['time'] == df.loc[idx, 'time']
        sub['body'] = sub['close'] - sub['open']
        print(sub[['time', 'open', 'high', 'low', 'close', 'body', 'is_entry']])

    mt5.shutdown()
