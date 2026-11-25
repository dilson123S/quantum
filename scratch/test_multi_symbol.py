import sys
sys.path.insert(0, ".")
from datetime import datetime, timezone, timedelta
from core.config_loader import load_config
from backtest_bot_g import run_single_backtest
import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

cfg = load_config("config/bot_g_alchemist")
end = datetime.now(timezone.utc)
start = end - timedelta(days=90)

print(f"Testing Alchemist Strategy across 90 days ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')})...", flush=True)
for sym in ["EURUSD", "GBPUSD", "XAUUSD"]:
    try:
        res = run_single_backtest(cfg, sym, start, end)
        print(f"[{sym:<6}] Trades: {res['total_trades']:<3} | WR: {res['win_rate']:>5.1f}% | PF: {res['profit_factor']:>5.2f} | R: {res['total_profit_r']:>+6.2f}R | P/L: ${res['total_profit_money']:>+8.2f} | MaxDD: {res['max_drawdown_pct']:>4.1f}%", flush=True)
    except Exception as e:
        print(f"[{sym:<6}] Error: {e}", flush=True)

mt5.shutdown()
