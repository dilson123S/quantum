"""Comprehensive Backtest Comparison: Fleet 1 vs Fleet 2 vs Original Baseline.

Runs BacktestEngine across:
- Flota 1: BOT-A, BOT-B, BOT-C, BOT-D, BOT-F, BOT-G
- Flota 2: BOT-A2, BOT-B2, BOT-C2, BOT-D2
- Baseline Original: demo_gold (H1 con session 07:00-18:00) y demo_gold_h4 (H4)
"""
import sys
import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import MetaTrader5 as mt5

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config_loader import load_config
from core.constants import TIMEFRAME_MAP
from backtest.engine import BacktestEngine, BacktestResult, InstrumentSpec
from data.cleaner import DataCleaner
from backtest_runner import download_historical_data

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("FleetComparison")


def run_bot(label: str, fleet_name: str, config_dir: str, oos_start: str = "2026-08-01", balance: float = 10000.0) -> dict:
    config = load_config(config_dir)
    symbol = "XAUUSD"
    months = 3
    hard_end = datetime.now(timezone.utc)
    
    parsed_start = pd.Timestamp(oos_start)
    parsed_start = parsed_start.tz_localize("UTC") if parsed_start.tzinfo is None else parsed_start.tz_convert("UTC")
    evaluation_start = parsed_start.to_pydatetime()
    
    cleaner = DataCleaner()
    
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Could not fetch symbol_info for {symbol}")
        
    specs = {
        symbol: InstrumentSpec(
            point=float(info.point),
            tick_size=float(info.trade_tick_size),
            tick_value=float(info.trade_tick_value),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
        )
    }
    
    engine = BacktestEngine(config, initial_balance=balance, instrument_specs=specs)
    
    # Download entry
    df_raw = download_historical_data(symbol, config.timeframes.entry, months, end_time=hard_end)
    df_clean, _ = cleaner.clean(df_raw)
    
    # Download setup
    df_setup = None
    try:
        df_setup_raw = download_historical_data(symbol, config.timeframes.setup, months, end_time=hard_end)
        df_setup, _ = cleaner.clean(df_setup_raw)
    except Exception:
        pass
        
    # Download HTF
    df_htf = None
    try:
        df_htf_raw = download_historical_data(symbol, config.timeframes.higher, months, end_time=hard_end)
        df_htf, _ = cleaner.clean(df_htf_raw)
    except Exception:
        pass
        
    df_daily = df_weekly = df_monthly = None
    df_confirmation = df_gap = None
    if getattr(config, "alchemist", None) and config.alchemist.enabled:
        for tf, cnt, out_name in (
            (config.timeframes.daily, months + 2, "daily"),
            (config.timeframes.weekly, months + 2, "weekly"),
            (config.timeframes.monthly, max(18, months + 2), "monthly"),
            (getattr(config.timeframes, "confirmation", "H1"), months + 2, "confirmation"),
            (getattr(config.timeframes, "gap", "H1"), months + 2, "gap"),
        ):
            try:
                ref_raw = download_historical_data(symbol, tf, cnt, end_time=hard_end)
                ref_clean, _ = cleaner.clean(ref_raw)
                if out_name == "daily":
                    df_daily = ref_clean
                elif out_name == "weekly":
                    df_weekly = ref_clean
                elif out_name == "confirmation":
                    df_confirmation = ref_clean
                elif out_name == "gap":
                    df_gap = ref_clean
                else:
                    df_monthly = ref_clean
            except Exception:
                pass
                
    result: BacktestResult = engine.run(
        symbol, df_clean, df_htf, df_setup=df_setup,
        evaluation_start=evaluation_start, df_daily=df_daily, df_weekly=df_weekly,
        df_monthly=df_monthly, df_confirmation=df_confirmation, df_gap=df_gap,
    )
    
    trades = result.trades
    total_trades = len(trades)
    wins = [t for t in trades if t.profit_money > 0]
    losses = [t for t in trades if t.profit_money < 0]
    bes = [t for t in trades if t.profit_money == 0]
    
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    gross_profit = sum(t.profit_money for t in wins)
    gross_loss = abs(sum(t.profit_money for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    net_profit = result.total_profit_money
    total_r = result.total_profit_r
    
    equity = balance
    peak = balance
    max_dd = 0.0
    for t in trades:
        equity += t.profit_money
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0
        if dd > max_dd:
            max_dd = dd

    return {
        "label": label,
        "fleet": fleet_name,
        "config_dir": config_dir,
        "entry_tf": config.timeframes.entry,
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(bes),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "net_profit_usd": round(net_profit, 2),
        "total_r": round(total_r, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "initial_balance": balance,
        "final_balance": round(balance + net_profit, 2),
    }


def main():
    if not mt5.initialize():
        print("[ERROR] No se pudo inicializar MT5")
        return
        
    runs = [
        # FLOTA 2 (NUEVAS CONFIGURACIONES)
        ("BOT-A2", "Flota 2", "config/bot_a2_gold"),
        ("BOT-B2", "Flota 2", "config/bot_b2_gold"),
        ("BOT-C2", "Flota 2", "config/bot_c2_gold"),
        ("BOT-D2", "Flota 2", "config/bot_d2_gold"),
        # FLOTA 1 (CONFIGURACIONES ORIGINALES DE FLOTA)
        ("BOT-A",  "Flota 1", "config/bot_a_gold"),
        ("BOT-B",  "Flota 1", "config/bot_b_gold"),
        ("BOT-C",  "Flota 1", "config/bot_c_gold"),
        ("BOT-D",  "Flota 1", "config/bot_d_gold"),
        ("BOT-G",  "Flota 1", "config/bot_g_alchemist"),
        # BASELINE HISTORICO PURO (PRE-ALTERACIONES)
        ("BASE-H1", "Backup Baseline", "config/backup_baseline_gold/demo_gold"),
        ("BASE-H4", "Backup Baseline", "config/backup_baseline_gold/demo_gold_h4"),
    ]
    
    oos_start = "2026-08-01"
    print("=" * 95, flush=True)
    print("   QUANTUM: AUDITORIA FORENSE Y COMPARATIVA COMPLETA DE FLOTAS DE ORO (XAUUSD)")
    print(f"   VENTANA EVALUADA: {oos_start} hasta presente (3 Meses OOS Ciego)")
    print("=" * 95, flush=True)
    
    results = []
    for label, fleet, cfg_dir in runs:
        print(f"\n[+] Evaluando {fleet} -> {label} ({cfg_dir})...", flush=True)
        t0 = time.time()
        try:
            res = run_bot(label, fleet, cfg_dir, oos_start=oos_start)
            elapsed = time.time() - t0
            print(f"    ✓ {label:<7} en {elapsed:.1f}s | TF: {res['entry_tf']} | Trades: {res['total_trades']:2d} | WR: {res['win_rate_pct']:4.1f}% | PF: {res['profit_factor']:4.2f} | Net: ${res['net_profit_usd']:+8.2f} USD | DD: {res['max_drawdown_pct']}%", flush=True)
            results.append(res)
        except Exception as e:
            print(f"    ✗ Error en {label}: {e}", flush=True)
            
    print("\n" + "=" * 95, flush=True)
    print("   CUADRO COMPARATIVO FORENSE DE FLOTAS EN ENTORNO OOS")
    print("=" * 95, flush=True)
    print(f"   {'BOT':<8} | {'GRUPO':<15} | {'TF':<4} | {'TRADES':<6} | {'WR (%)':<6} | {'PF':<5} | {'NET USD':<12} | {'MAX DD':<7}", flush=True)
    print("-" * 95, flush=True)
    
    for r in results:
        print(f"   {r['label']:<8} | {r['fleet']:<15} | {r['entry_tf']:<4} | {r['total_trades']:<6} | {r['win_rate_pct']:<5.1f}% | {r['profit_factor']:<5.2f} | ${r['net_profit_usd']:+10.2f} | {r['max_drawdown_pct']:<5.1f}%", flush=True)
        
    print("=" * 95, flush=True)
    
    out_file = Path("data/fleet_forensic_comparison.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\n[OK] Auditoria forense exportada a {out_file}", flush=True)


if __name__ == "__main__":
    main()
