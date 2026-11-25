import sys
sys.path.insert(0, ".")
from datetime import datetime, timezone, timedelta
from core.config_loader import load_config
from backtest_bot_g import run_single_backtest
import MetaTrader5 as mt5
import backtest.engine as engine_mod

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

orig_detect = engine_mod.detect_alchemist_setup

def detect_with_htf_trend(df_entry, df_setup, df_higher, df_daily, df_weekly, config, **kwargs):
    setup = orig_detect(df_entry, df_setup, df_higher, df_daily, df_weekly, config, **kwargs)
    if setup is None:
        return None
    # Check H4 trend alignment
    if config.require_htf_alignment and df_higher is not None and len(df_higher) >= 20:
        last_h4 = df_higher.iloc[-1]
        h4_close = float(last_h4["close"])
        h4_ema = float(last_h4.get("ema_medium", 0.0) or last_h4.get("ema_fast", 0.0) or 0.0)
        if h4_ema > 0:
            if setup.direction.value == "BUY" and h4_close < h4_ema:
                return None
            if setup.direction.value == "SELL" and h4_close > h4_ema:
                return None
    return setup

engine_mod.detect_alchemist_setup = detect_with_htf_trend

cfg = load_config("config/bot_g_alchemist")
cfg.trade.stagnation_exit_bars = 0
cfg.trade.trailing = "off"
cfg.trade.break_even_r = 1.0
cfg.trade.partial_close = True
cfg.trade.partial_r = 1.5
cfg.trade.partial_percent = 50
cfg.alchemist.require_htf_alignment = True

end = datetime(2026, 6, 1, tzinfo=timezone.utc)
start = end - timedelta(days=30)
res = run_single_backtest(cfg, "XAUUSD", start, end)

print(f"WITH REAL H4 FILTER: Trades: {res['total_trades']}, WR: {res['win_rate']}%, PF: {res['profit_factor']}, R: {res['total_profit_r']:+.2f}R, P/L: ${res['total_profit_money']:+.2f}")
for t in res["trades"]:
    time_str = t.entry_time.strftime("%Y-%m-%d %H:%M")
    print(f"  {time_str} | {t.direction} | R: {t.profit_r:+.2f}R | $ {t.profit_money:+.2f} | Reason: {t.exit_reason}")
mt5.shutdown()
