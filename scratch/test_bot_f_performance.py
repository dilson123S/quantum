import sys
import os
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import MetaTrader5 as mt5

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import load_config
from scratch.run_score_70_backtest import ExperimentBacktestEngine, download_data, InstrumentSpec, DataCleaner
from filters.ml_filter import MLFilter

def main():
    if not mt5.initialize():
        print("MT5 failed to initialize", flush=True)
        sys.exit(1)

    print("Auditing BOT-F Historical Performance (24 Months XAUUSD)...", flush=True)
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

    # Load Bot F configuration
    cfg = load_config("config/bot_f_gold")
    
    # We test both:
    # 1. Bot F with London/NY session (07:00 - 18:00)
    # 2. Bot F with Extended 20h session (07:00 - 03:00)
    
    sessions = [
        ("London/NY (07:00-18:00 UTC)", "07:00", "18:00"),
        ("Sesión Extendida 20h (07:00-03:00 UTC)", "07:00", "03:00")
    ]

    for name, s_start, s_end in sessions:
        cfg.filters.trading_start = s_start
        cfg.filters.trading_end = s_end
        if "XAUUSD" in cfg.symbols:
            cfg.symbols["XAUUSD"].session_start = s_start
            cfg.symbols["XAUUSD"].session_end = s_end

        # ML in shadow mode (as requested: does not block trades)
        ml_f = MLFilter(enabled=True, shadow_mode=True, model_path="data/ml_model.joblib", min_confidence=0.55)
        engine = ExperimentBacktestEngine(config=cfg, initial_balance=10000.0, instrument_specs=specs, ml_filter=ml_f)
        res = engine.run("XAUUSD", df_h1, df_htf=df_d1, df_setup=df_h4)

        trades = res.trades
        wins = [t for t in trades if t.profit_money > 0]
        losses = [t for t in trades if t.profit_money < 0]
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

        wr = len(wins) / len(trades) * 100.0 if trades else 0.0
        print(f"[{name}] Trades={len(trades)} | W/L={len(wins)}/{len(losses)} ({wr:.1f}%) | Net=${p_money:,.2f} ({p_r:+.1f}R) | PF={pf:.2f} | MaxDD={max_dd:.2f}%", flush=True)

    mt5.shutdown()

if __name__ == "__main__":
    main()
