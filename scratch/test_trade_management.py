import sys
sys.path.insert(0, ".")
from datetime import datetime, timezone, timedelta
from core.config_loader import load_config
from backtest_bot_g import run_single_backtest
import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 failed")
    exit(1)

cfg = load_config("config/bot_g_alchemist")
cfg.trade.stagnation_exit_bars = 0
cfg.trade.trailing = "off"
cfg.trade.break_even_r = 1.0
cfg.trade.partial_close = True
cfg.trade.partial_r = 1.5
cfg.trade.partial_percent = 50

end = datetime(2026, 6, 1, tzinfo=timezone.utc)
start = end - timedelta(days=30)
res = run_single_backtest(cfg, "XAUUSD", start, end)

print(f"RESULTS: Trades: {res['total_trades']}, WR: {res['win_rate']}%, PF: {res['profit_factor']}, R: {res['total_profit_r']:+.2f}R, P/L: ${res['total_profit_money']:+.2f}")
for t in res['trades']:
    print(f"  {t.direction} | R: {t.profit_r:+.2f}R | $ {t.profit_money:+.2f} | Reason: {t.exit_reason}")
mt5.shutdown()
