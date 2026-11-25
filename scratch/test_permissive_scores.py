import sys
import os
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import load_config
from core.constants import TIMEFRAME_MAP
from scratch.run_score_70_backtest import ExperimentBacktestEngine, download_data, InstrumentSpec, DataCleaner
from filters.ml_filter import MLFilter

if not mt5.initialize():
    print("MT5 failed")
    sys.exit(1)

cleaner = DataCleaner()
end_time = datetime(2026, 9, 16, tzinfo=timezone.utc)
h1 = download_data("XAUUSD", "H1", months=24, end_time=end_time)
h4 = download_data("XAUUSD", "H4", months=24, end_time=end_time)
d1 = download_data("XAUUSD", "D1", months=24, end_time=end_time)

df_h1, _ = cleaner.clean(h1)
df_h4, _ = cleaner.clean(h4)
df_d1, _ = cleaner.clean(d1)

info = mt5.symbol_info("XAUUSD")
specs = {
    "XAUUSD": InstrumentSpec(
        point=float(info.point),
        tick_size=float(info.trade_tick_size),
        tick_value=float(info.trade_tick_value),
        volume_min=float(info.volume_min),
        volume_max=float(info.volume_max),
        volume_step=float(info.volume_step),
    )
}

scores_to_test = [75.0, 70.0, 65.0, 60.0, 55.0, 50.0]

print("=" * 105)
print("  🔬 AUDITORÍA DE PERMISIVIDAD DEL SCORE BASE DE ENTRADA (BOT-B · ML 0.55 · SL 2.0x · 24 MESES)")
print("=" * 105)
header = f"  {'Score Base Mínimo':<25} │ {'Trades':>6} │ {'W/L':>7} │ {'WinRate':>7} │ {'PF':>6} │ {'Net Profit':>12} │ {'Retorno':>8} │ {'Max DD':>7}"
print(header)
print("  " + "─" * 101)

for score_val in scores_to_test:
    cfg = load_config("config/bot_b_gold")
    cfg.score.minimum = score_val
    cfg.ml.min_confidence = 0.55
    cfg.volatility.sl_multiplier = 2.0
    setattr(cfg.volatility, "sl_buffer_atr", 0.25)
    cfg.trade.break_even_r = 1.2
    cfg.trade.partial_close = True
    cfg.trade.partial_r = 1.5
    cfg.trade.risk_reward = 2.0
    cfg.trade.stagnation_exit_bars = 0
    
    ml_f = MLFilter(enabled=True, shadow_mode=False, model_path="data/ml_model.joblib", min_confidence=0.55)
    engine = ExperimentBacktestEngine(config=cfg, initial_balance=10000.0, instrument_specs=specs, ml_filter=ml_f)
    res = engine.run("XAUUSD", df_h1, df_htf=df_d1, df_setup=df_h4)
    
    trades = res.trades
    n = len(trades)
    wins = [t for t in trades if t.profit_money > 0]
    losses = [t for t in trades if t.profit_money < 0]
    wr = len(wins) / n * 100.0 if n else 0
    p_money = sum(t.profit_money for t in trades)
    p_r = sum(t.profit_r for t in trades)
    gross_w = sum(t.profit_money for t in wins)
    gross_l = abs(sum(t.profit_money for t in losses))
    pf = gross_w / gross_l if gross_l > 0 else 99.9
    
    bal = 10000.0
    peak = 10000.0
    max_dd = 0.0
    for t in trades:
        bal += t.profit_money
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100.0
        if dd > max_dd:
            max_dd = dd
        
    print(f"  Score >= {score_val:.0f} pts ({score_val:.0f}%)      │ {n:>6} │ {len(wins):>3}/{len(losses):<3} │ {wr:>6.1f}% │ {pf:>6.2f} │ ${p_money:>11.2f} │ {p_r:>+7.1f}R │ {max_dd:>6.2f}%")

print("=" * 105)
mt5.shutdown()
