"""Run empirical trial & error across 12 months to fix London/NY timing and verify profitability."""
import os
import sys
from datetime import datetime, timezone, timedelta

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath("."))
import MetaTrader5 as mt5
from core.config_loader import load_config
from backtest_bot_g import run_single_backtest, fetch_historical_bars, get_symbol_spec
from data.cleaner import DataCleaner

if not mt5.initialize():
    print(f"Error MT5: {mt5.last_error()}", flush=True)
    sys.exit(1)

base_config = load_config("config/bot_g_alchemist")
symbol = "XAUUSD"
now = datetime.now(timezone.utc)
start_1y = now - timedelta(days=365)
buffer_start = start_1y - timedelta(days=60)

print("Downloading 1-year data cache...", flush=True)
cleaner = DataCleaner()
df_m15 = fetch_historical_bars(symbol, base_config.timeframes.entry, buffer_start, now)
df_h1 = fetch_historical_bars(symbol, base_config.timeframes.setup, buffer_start, now)
df_h4 = fetch_historical_bars(symbol, base_config.timeframes.higher, buffer_start, now)
df_d1 = fetch_historical_bars(symbol, base_config.timeframes.daily, buffer_start - timedelta(days=180), now)
df_w1 = fetch_historical_bars(symbol, base_config.timeframes.weekly, buffer_start - timedelta(days=365), now)
df_mn1 = fetch_historical_bars(symbol, base_config.timeframes.monthly, buffer_start - timedelta(days=730), now)

cached = {
    symbol: {
        "entry": cleaner.clean(df_m15)[0],
        "setup": cleaner.clean(df_h1)[0],
        "htf": cleaner.clean(df_h4)[0],
        "daily": cleaner.clean(df_d1)[0],
        "weekly": cleaner.clean(df_w1)[0],
        "monthly": cleaner.clean(df_mn1)[0],
    }
}
print(f"Cache loaded: {len(cached[symbol]['entry'])} M15 bars.", flush=True)

# Test Scenarios
# Baseline: Current settings
import copy

scenarios = [
    ("1. Baseline Actual (max_dd=20)", {"max_dd": 20.0}),
    ("2. Solo Londres (08-10 GMT) sin NY CRT", {"max_dd": 20.0, "ny_enabled": False}),
    ("3. Londres (08-10 GMT) + NY CRT con min_confluences=3", {"max_dd": 20.0, "ny_enabled": True, "min_conf": 3}),
    ("4. Solo Londres (08-10 GMT) con TP=2.5R", {"max_dd": 20.0, "ny_enabled": False, "target_rr": 2.5, "rr": 2.5}),
]

for name, params in scenarios:
    cfg = copy.deepcopy(base_config)
    cfg.risk.max_drawdown = params.get("max_dd", 20.0)
    if "ny_enabled" in params:
        cfg.alchemist.new_york_crt_enabled = params["ny_enabled"]
    if "min_conf" in params:
        cfg.alchemist.min_confluences = params["min_conf"]
    if "target_rr" in params:
        cfg.alchemist.target_rr = params["target_rr"]
        cfg.trade.risk_reward = params["rr"]

    res = run_single_backtest(cfg, symbol, start_1y, now, 10000.0, cached_data=cached)
    print(f"\n[{name}]")
    print(f"  Trades: {res['total_trades']:<3} | WinRate: {res['win_rate']:>5.1f}% | PF: {res['profit_factor']:>5.2f} | "
          f"R: {res['total_profit_r']:>+6.2f}R | P/L: ${res['total_profit_money']:>+8.2f} | MaxDD: {res['max_drawdown_pct']:>4.1f}%", flush=True)

mt5.shutdown()
