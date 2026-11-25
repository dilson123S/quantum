import sys
sys.path.insert(0, ".")
from datetime import datetime, timezone, timedelta
from core.config_loader import load_config
from backtest_bot_g import run_single_backtest
import MetaTrader5 as mt5
import strategy.alchemist as alch

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

# Apply both:
# 1. Sweep depth filter
orig_london = alch.evaluate_london_killzone_setup
def filtered_london_sweep(bar, df_entry, current_time_utc, config, htf_zones, point):
    setup = orig_london(bar, df_entry, current_time_utc, config, htf_zones, point)
    if setup is None:
        return None
    atr = float(bar.get("atr", 0.0) or 0.0)
    if atr > 0:
        if setup.direction.value == "BUY":
            depth = setup.liquidity_price - (setup.sl_price + atr * float(config.stop_buffer_atr))
        else:
            depth = (setup.sl_price - atr * float(config.stop_buffer_atr)) - setup.liquidity_price
        if depth < atr * 0.15 or depth > atr * 1.50:
            return None
    return setup
alch.evaluate_london_killzone_setup = filtered_london_sweep

# 2. Daily candle directional filter (Rule 4: Trade with the Daily bias)
orig_detect = alch.detect_alchemist_setup
def detect_with_daily_trend(df_entry, df_setup, df_higher, df_daily, df_weekly, config, **kwargs):
    setup = orig_detect(df_entry, df_setup, df_higher, df_daily, df_weekly, config, **kwargs)
    if setup is None:
        return None
    if df_daily is not None and len(df_daily) >= 2:
        last_d = df_daily.iloc[-1]
        d_bull = float(last_d["close"]) > float(last_d["open"])
        # If daily is bullish, only take BUY. If daily is bearish, only take SELL.
        if setup.direction.value == "BUY" and not d_bull:
            return None
        if setup.direction.value == "SELL" and d_bull:
            return None
    return setup
alch.detect_alchemist_setup = detect_with_daily_trend

cfg = load_config("config/bot_g_alchemist")
now = datetime.now(timezone.utc)
oos_start = now - timedelta(days=60)
is_start = oos_start - timedelta(days=90)

print("Running Backtest with Alchemist Rule 4 (Daily Trend Direction)...", flush=True)
res_is = run_single_backtest(cfg, "XAUUSD", is_start, oos_start)
print(f"IS (3m) Daily Trend Filter : Trades: {res_is['total_trades']}, WR: {res_is['win_rate']}%, PF: {res_is['profit_factor']}, R: {res_is['total_profit_r']:+.2f}R, P/L: ${res_is['total_profit_money']:+.2f}", flush=True)

res_oos = run_single_backtest(cfg, "XAUUSD", oos_start, now)
print(f"OOS (2m) Daily Trend Filter: Trades: {res_oos['total_trades']}, WR: {res_oos['win_rate']}%, PF: {res_oos['profit_factor']}, R: {res_oos['total_profit_r']:+.2f}R, P/L: ${res_oos['total_profit_money']:+.2f}", flush=True)
mt5.shutdown()
