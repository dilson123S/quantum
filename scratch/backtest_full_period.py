"""Run backtest of Bot G across 1 year and 2 years on XAUUSD."""
import os
import sys
from datetime import datetime, timezone, timedelta

# Console encoding for Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import MetaTrader5 as mt5
sys.path.insert(0, os.path.abspath("."))

from core.config_loader import load_config

from backtest_bot_g import run_single_backtest, export_trades_to_csv

if not mt5.initialize():
    print(f"Error MT5: {mt5.last_error()}")
    sys.exit(1)

config = load_config("config/bot_g_alchemist")
symbol = "XAUUSD"
now = datetime.now(timezone.utc)

# 1 Year: 365 days
start_1y = now - timedelta(days=365)
print(f"\n=======================================================")
print(f"   BACKTEST BOT G (XAUUSD) — 1 AÑO COMPLETO ({start_1y.strftime('%Y-%m-%d')} a {now.strftime('%Y-%m-%d')})")
print(f"=======================================================")
res_1y = run_single_backtest(config, symbol, start_1y, now, 10000.0)
print(f"Trades Totales : {res_1y['total_trades']}")
print(f"Ganadores      : {res_1y['wins']} ({res_1y['win_rate']}%)")
print(f"Perdedores     : {res_1y['losses']}")
print(f"Breakeven      : {res_1y['be']}")
print(f"Profit Factor  : {res_1y['profit_factor']}")
print(f"Beneficio (R)  : {res_1y['total_profit_r']:+.2f} R")
print(f"Beneficio ($)  : ${res_1y['total_profit_money']:+,.2f}")
print(f"Max Drawdown   : {res_1y['max_drawdown_pct']}%")
print(f"Expectancy     : {res_1y['expectancy_r']:+.2f} R/trade")

export_trades_to_csv(res_1y["trades"], "data/experiments/bot_g_xauusd_1year.csv")

# 2 Years: 730 days
start_2y = now - timedelta(days=730)
print(f"\n=======================================================")
print(f"   BACKTEST BOT G (XAUUSD) — 2 AÑOS COMPLETOS ({start_2y.strftime('%Y-%m-%d')} a {now.strftime('%Y-%m-%d')})")
print(f"=======================================================")
res_2y = run_single_backtest(config, symbol, start_2y, now, 10000.0)
print(f"Trades Totales : {res_2y['total_trades']}")
print(f"Ganadores      : {res_2y['wins']} ({res_2y['win_rate']}%)")
print(f"Perdedores     : {res_2y['losses']}")
print(f"Breakeven      : {res_2y['be']}")
print(f"Profit Factor  : {res_2y['profit_factor']}")
print(f"Beneficio (R)  : {res_2y['total_profit_r']:+.2f} R")
print(f"Beneficio ($)  : ${res_2y['total_profit_money']:+,.2f}")
print(f"Max Drawdown   : {res_2y['max_drawdown_pct']}%")
print(f"Expectancy     : {res_2y['expectancy_r']:+.2f} R/trade")

export_trades_to_csv(res_2y["trades"], "data/experiments/bot_g_xauusd_2years.csv")

mt5.shutdown()
