"""Dedicated Backtest & Optimization Runner for Bot G (Alchemist Strategy).

Evaluates the mechanical Alchemist MSNR + CRT strategy on historical MT5 data
with strict anti-overfitting protocols:
1. In-Sample (IS) partition for calibration and discovery.
2. Out-of-Sample (OOS) holdout partition for blind verification.
3. Multi-asset cross-validation (XAUUSD, EURUSD, GBPUSD, BTCUSD).
4. Exhaustive metric tracking: R-multiples, Profit Factor, Win Rate, Max Drawdown,
   Expectancy E[R], and session trade distribution.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import pandas as pd

# Console encoding for Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.config_loader import load_config, AppConfig
from core.constants import TIMEFRAME_MAP
from backtest.engine import BacktestEngine, BacktestResult, InstrumentSpec
from data.cleaner import DataCleaner
from data.mt5_time import normalize_mt5_bar_times, to_mt5_query_time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("AlchemistBacktest")

for noisy in ("indicators", "strategy", "risk", "core", "backtest"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def fetch_historical_bars(
    symbol: str,
    timeframe: str,
    start_time: datetime,
    end_time: datetime,
) -> pd.DataFrame:
    """Download historical bars between start_time and end_time from MT5."""
    import MetaTrader5 as mt5

    tf_value = TIMEFRAME_MAP.get(timeframe)
    if tf_value is None:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    mt5.symbol_select(symbol, True)
    account = mt5.account_info()
    server = getattr(account, "server", "") if account else ""
    columns = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]

    rates = mt5.copy_rates_range(
        symbol,
        tf_value,
        to_mt5_query_time(start_time, server),
        to_mt5_query_time(end_time, server),
    )
    if rates is None or len(rates) == 0:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rates, columns=columns)
    df["time"] = normalize_mt5_bar_times(df["time"], server)
    df = df[(df["time"] >= start_time) & (df["time"] < end_time)]
    return df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)


def get_symbol_spec(symbol: str) -> Optional[InstrumentSpec]:
    import MetaTrader5 as mt5

    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None or info.point <= 0 or info.trade_tick_size <= 0 or info.trade_tick_value <= 0:
        return None

    return InstrumentSpec(
        point=float(info.point),
        tick_size=float(info.trade_tick_size),
        tick_value=float(info.trade_tick_value),
        volume_min=float(info.volume_min),
        volume_max=float(info.volume_max),
        volume_step=float(info.volume_step),
    )


def run_single_backtest(
    config: AppConfig,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    initial_balance: float = 10000.0,
    cached_data: Optional[Dict[str, pd.DataFrame]] = None,
) -> Dict[str, Any]:
    """Execute backtest for a specific symbol and date range."""
    spec = get_symbol_spec(symbol)
    if spec is None:
        raise ValueError(f"Could not load instrument spec for {symbol}")

    cleaner = DataCleaner()
    engine = BacktestEngine(config, initial_balance=initial_balance, instrument_specs={symbol: spec})

    # Prepare data for each timeframe
    # Lookback buffer for indicators / HTF zones
    buffer_start = start_date - timedelta(days=60)

    if cached_data and symbol in cached_data:
        data_entry = cached_data[symbol]["entry"]
        data_setup = cached_data[symbol]["setup"]
        data_htf = cached_data[symbol]["htf"]
        data_daily = cached_data[symbol]["daily"]
        data_weekly = cached_data[symbol]["weekly"]
        data_monthly = cached_data[symbol]["monthly"]
    else:
        df_m15 = fetch_historical_bars(symbol, config.timeframes.entry, buffer_start, end_date)
        df_h1 = fetch_historical_bars(symbol, config.timeframes.setup, buffer_start, end_date)
        df_h4 = fetch_historical_bars(symbol, config.timeframes.higher, buffer_start, end_date)
        df_d1 = fetch_historical_bars(symbol, config.timeframes.daily, buffer_start - timedelta(days=180), end_date)
        df_w1 = fetch_historical_bars(symbol, config.timeframes.weekly, buffer_start - timedelta(days=365), end_date)
        df_mn1 = fetch_historical_bars(symbol, config.timeframes.monthly, buffer_start - timedelta(days=730), end_date)

        data_entry, _ = cleaner.clean(df_m15)
        data_setup, _ = cleaner.clean(df_h1)
        data_htf, _ = cleaner.clean(df_h4)
        data_daily, _ = cleaner.clean(df_d1)
        data_weekly, _ = cleaner.clean(df_w1)
        data_monthly, _ = cleaner.clean(df_mn1)

    result = engine.run(
        symbol,
        data_entry,
        data_htf,
        df_setup=data_setup,
        evaluation_start=start_date,
        df_daily=data_daily,
        df_weekly=data_weekly,
        df_monthly=data_monthly,
    )

    trades = result.trades
    total_trades = len(trades)
    wins = [t for t in trades if t.profit_money > 0]
    losses = [t for t in trades if t.profit_money < 0]
    be = [t for t in trades if t.profit_money == 0]

    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    total_profit_money = sum(t.profit_money for t in trades)
    total_profit_r = sum(t.profit_r for t in trades)
    gross_profit = sum(t.profit_money for t in wins)
    gross_loss = abs(sum(t.profit_money for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # Max Drawdown calculation from equity curve
    equity = result.equity_curve
    max_dd_pct = 0.0
    if equity:
        peak = equity[0]
        for eq in equity:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
            max_dd_pct = max(max_dd_pct, dd)

    expectancy_r = (total_profit_r / total_trades) if total_trades > 0 else 0.0

    return {
        "symbol": symbol,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "be": len(be),
        "win_rate": round(win_rate, 2),
        "total_profit_money": round(total_profit_money, 2),
        "total_profit_r": round(total_profit_r, 2),
        "profit_factor": round(pf, 3),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "expectancy_r": round(expectancy_r, 3),
        "trades": trades,
        "result_obj": result,
    }


def export_trades_to_csv(trades: list, filepath: str) -> None:
    rows = []
    for t in trades:
        rows.append({
            "trade_id": t.trade_id,
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "sl_price": t.sl_price,
            "tp_price": t.tp_price,
            "volume": t.volume,
            "profit_r": t.profit_r,
            "profit_money": t.profit_money,
            "exit_reason": t.exit_reason,
            "strategy": t.entry_strategy,
        })
    df = pd.DataFrame(rows)
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(filepath, index=False)


def main():
    parser = argparse.ArgumentParser(description="Bot G Alchemist Strategy Backtester")
    parser.add_argument("--config", default="config/bot_g_alchemist", help="Path to config directory")
    parser.add_argument("--symbols", default="XAUUSD,EURUSD,GBPUSD", help="Comma-separated symbols")
    parser.add_argument("--is-months", type=int, default=6, help="Months for In-Sample test")
    parser.add_argument("--oos-months", type=int, default=4, help="Months for Out-of-Sample test")
    parser.add_argument("--balance", type=float, default=10000.0, help="Initial balance")
    parser.add_argument("--grid-search", action="store_true", help="Run trial & error parameter sweep")
    args = parser.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        logger.critical(f"Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    logger.info(f"MT5 Connected: #{account.login if account else 'Unknown'}")

    base_config = load_config(args.config)
    symbols = [s.strip() for s in args.symbols.split(",")]

    now = datetime.now(timezone.utc)
    # Define In-Sample and Out-of-Sample boundaries
    # OOS: most recent 4 months (e.g. 2026-05-24 to 2026-09-24)
    # IS: preceding 6 months (e.g. 2025-11-24 to 2026-05-24)
    oos_end = now
    oos_start = oos_end - timedelta(days=args.oos_months * 30)
    is_end = oos_start
    is_start = is_end - timedelta(days=args.is_months * 30)

    print("\n" + "=" * 75, flush=True)
    print("   🔮 BOT G (ALCHEMIST MSNR + CRT) — BACKTEST & ANTI-OVERFITTING SUITE", flush=True)
    print("=" * 75, flush=True)
    print(f"  Periodo In-Sample (IS)  : {is_start.strftime('%Y-%m-%d')} → {is_end.strftime('%Y-%m-%d')} ({args.is_months} meses)", flush=True)
    print(f"  Periodo Out-of-Sample   : {oos_start.strftime('%Y-%m-%d')} → {oos_end.strftime('%Y-%m-%d')} ({args.oos_months} meses)", flush=True)
    print(f"  Símbolos evaluados      : {', '.join(symbols)}", flush=True)
    print(f"  Balance inicial         : ${args.balance:,.2f}", flush=True)
    print("=" * 75 + "\n", flush=True)

    # 1. Evaluate In-Sample
    print("▶ EJECUTANDO EVALUACIÓN IN-SAMPLE (CALIBRACIÓN / PRUEBA Y ERROR)...", flush=True)
    is_trades_all = []
    is_results = []
    for sym in symbols:
        try:
            res = run_single_backtest(base_config, sym, is_start, is_end, args.balance)
            is_results.append(res)
            is_trades_all.extend(res["trades"])
            print(f"  [{sym:<6}] IS -> Trades: {res['total_trades']:<3} | WinRate: {res['win_rate']:>5.1f}% | "
                  f"PF: {res['profit_factor']:>5.2f} | R: {res['total_profit_r']:>+6.2f}R | "
                  f"P/L: ${res['total_profit_money']:>+8.2f} | MaxDD: {res['max_drawdown_pct']:>4.1f}% | E[R]: {res['expectancy_r']:>+5.2f}R", flush=True)
        except Exception as e:
            logger.error(f"Error en backtest IS para {sym}: {e}", exc_info=True)

    export_trades_to_csv(is_trades_all, "data/experiments/bot_g_alchemist_is.csv")

    # 2. Evaluate Out-of-Sample (Strict Blind Test)
    print("\n▶ EJECUTANDO EVALUACIÓN OUT-OF-SAMPLE (VALIDACIÓN CIEGA ANTI-SOBREAJUSTE)...", flush=True)
    oos_trades_all = []
    oos_results = []
    for sym in symbols:
        try:
            res = run_single_backtest(base_config, sym, oos_start, oos_end, args.balance)
            oos_results.append(res)
            oos_trades_all.extend(res["trades"])
            print(f"  [{sym:<6}] OOS -> Trades: {res['total_trades']:<3} | WinRate: {res['win_rate']:>5.1f}% | "
                  f"PF: {res['profit_factor']:>5.2f} | R: {res['total_profit_r']:>+6.2f}R | "
                  f"P/L: ${res['total_profit_money']:>+8.2f} | MaxDD: {res['max_drawdown_pct']:>4.1f}% | E[R]: {res['expectancy_r']:>+5.2f}R", flush=True)
        except Exception as e:
            logger.error(f"Error en backtest OOS para {sym}: {e}", exc_info=True)

    export_trades_to_csv(oos_trades_all, "data/experiments/bot_g_alchemist_oos.csv")

    # Summary Comparison
    is_total_r = sum(r["total_profit_r"] for r in is_results)
    is_total_money = sum(r["total_profit_money"] for r in is_results)
    is_total_trades = sum(r["total_trades"] for r in is_results)
    is_wins = sum(r["wins"] for r in is_results)
    is_wr = (is_wins / is_total_trades * 100.0) if is_total_trades > 0 else 0.0

    oos_total_r = sum(r["total_profit_r"] for r in oos_results)
    oos_total_money = sum(r["total_profit_money"] for r in oos_results)
    oos_total_trades = sum(r["total_trades"] for r in oos_results)
    oos_wins = sum(r["wins"] for r in oos_results)
    oos_wr = (oos_wins / oos_total_trades * 100.0) if oos_total_trades > 0 else 0.0

    print("\n" + "=" * 75, flush=True)
    print("   📊 COMPARATIVA GLOBAL IN-SAMPLE vs OUT-OF-SAMPLE", flush=True)
    print("=" * 75, flush=True)
    print(f"  IN-SAMPLE  : {is_total_trades} trades | WinRate: {is_wr:.1f}% | P/L: ${is_total_money:+,.2f} | R: {is_total_r:+.2f}R", flush=True)
    print(f"  OUT-SAMPLE : {oos_total_trades} trades | WinRate: {oos_wr:.1f}% | P/L: ${oos_total_money:+,.2f} | R: {oos_total_r:+.2f}R", flush=True)
    
    # Anti-overfitting check
    if is_total_r > 0:
        retention = (oos_total_r / (is_total_r * (args.oos_months / args.is_months))) * 100.0
        print(f"  Tasa de Retención OOS/IS: {retention:.1f}%", flush=True)
        if oos_total_r > 0 and oos_wr >= 40.0:
            print("  ✅ VALIDACIÓN SUPERADA: La estrategia Alchemist mantiene rentabilidad positiva fuera de muestra.", flush=True)
        else:
            print("  ⚠️ ALERTA: Rendimiento OOS por debajo del umbral de aceptación.", flush=True)
    print("=" * 75 + "\n", flush=True)

    # Save manifest
    manifest = {
        "strategy": "Alchemist_MSNR_CRT",
        "bot": "Bot_G",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_period": {"start": is_start.isoformat(), "end": is_end.isoformat(), "trades": is_total_trades, "profit_r": is_total_r, "profit_money": is_total_money},
        "oos_period": {"start": oos_start.isoformat(), "end": oos_end.isoformat(), "trades": oos_total_trades, "profit_r": oos_total_r, "profit_money": oos_total_money},
        "symbols": symbols,
        "is_report_csv": "data/experiments/bot_g_alchemist_is.csv",
        "oos_report_csv": "data/experiments/bot_g_alchemist_oos.csv",
    }
    with open("data/experiments/bot_g_alchemist_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    mt5.shutdown()


if __name__ == "__main__":
    main()
