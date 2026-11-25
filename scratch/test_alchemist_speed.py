import sys
sys.path.insert(0, ".")
import time
from datetime import datetime, timezone, timedelta
from core.config_loader import load_config
from backtest_bot_g import run_single_backtest
import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

cfg = load_config('config/bot_g_alchemist')
end = datetime(2026, 6, 1, tzinfo=timezone.utc)
start = end - timedelta(days=30)
t0 = time.time()
res = run_single_backtest(cfg, 'XAUUSD', start, end)
dt = time.time() - t0
print(f"COMPLETED in {dt:.2f}s! Trades: {res['total_trades']}, WR: {res['win_rate']}%, PF: {res['profit_factor']}, R: {res['total_profit_r']}R, P/L: ${res['total_profit_money']}")
mt5.shutdown()
