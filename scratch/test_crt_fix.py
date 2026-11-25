import sys
sys.path.insert(0, ".")
import pandas as pd
from datetime import datetime, timezone, timedelta
from core.config_loader import load_config
from backtest_bot_g import run_single_backtest
import MetaTrader5 as mt5
import strategy.alchemist as alch

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

orig_ny = alch.evaluate_new_york_crt_setup

def strict_ny_crt(bar, df_setup_h1, current_time_utc, config, htf_zones, point):
    if df_setup_h1 is None or len(df_setup_h1) < 4:
        return None
    current_hour_utc = current_time_utc.hour
    if current_hour_utc not in (15, 16):
        return None
    times_utc = pd.to_datetime(df_setup_h1["time"], utc=True)
    current_date = current_time_utc.date()
    c13, c14 = None, None
    for i in range(len(df_setup_h1) - 1, -1, -1):
        t = times_utc.iloc[i]
        if t.date() == current_date:
            if t.hour == 13 and c13 is None:
                c13 = df_setup_h1.iloc[i]
            elif t.hour == 14 and c14 is None:
                c14 = df_setup_h1.iloc[i]
    if c13 is None or c14 is None:
        return None
    c13_high, c13_low = float(c13["high"]), float(c13["low"])
    c14_close = float(c14["close"])
    if c14_close > c13_high:
        direction = alch.Direction.BUY
    elif c14_close < c13_low:
        direction = alch.Direction.SELL
    else:
        return None
    return orig_ny(bar, df_setup_h1, current_time_utc, config, htf_zones, point)

alch.evaluate_new_york_crt_setup = strict_ny_crt

cfg = load_config("config/bot_g_alchemist")
now = datetime.now(timezone.utc)
oos_start = now - timedelta(days=60)
is_start = oos_start - timedelta(days=90)

res_is = run_single_backtest(cfg, "XAUUSD", is_start, oos_start)
print(f"IS (3 months): Trades: {res_is['total_trades']}, WR: {res_is['win_rate']}%, PF: {res_is['profit_factor']}, R: {res_is['total_profit_r']:+.2f}R, P/L: ${res_is['total_profit_money']:+.2f}")

res_oos = run_single_backtest(cfg, "XAUUSD", oos_start, now)
print(f"OOS (2 months): Trades: {res_oos['total_trades']}, WR: {res_oos['win_rate']}%, PF: {res_oos['profit_factor']}, R: {res_oos['total_profit_r']:+.2f}R, P/L: ${res_oos['total_profit_money']:+.2f}")
mt5.shutdown()
