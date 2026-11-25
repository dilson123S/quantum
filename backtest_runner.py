"""
QuantTradingBot Backtest Runner.

Downloads 1 month of historical data from MetaTrader 5 and runs the
walk-forward backtester using the exact same logic as the live bot.

Usage:
    python backtest_runner.py
    python backtest_runner.py --period 1M --symbols EURUSD,GBPUSD,USDJPY,XAUUSD
    python backtest_runner.py --period 3M --symbols XAUUSD --balance 10000
"""
import argparse
import sys
import os
import time
import logging
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Fix Windows console encoding
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import pandas as pd

from core.config_loader import load_config
from core.constants import TIMEFRAME_MAP, HOLDOUT_START
from backtest.engine import BacktestEngine, BacktestResult, InstrumentSpec
from backtest.robustness import bootstrap_trade_outcomes
from data.cleaner import DataCleaner
from data.mt5_time import normalize_mt5_bar_times, to_mt5_query_time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("BacktestRunner")

# Suppress noisy sub-loggers
logging.getLogger("indicators").setLevel(logging.WARNING)
logging.getLogger("strategy").setLevel(logging.WARNING)
logging.getLogger("risk").setLevel(logging.WARNING)
logging.getLogger("core").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------
def download_historical_data(
    symbol: str, timeframe: str, months: int = 1, end_time: datetime | None = None
) -> pd.DataFrame:
    """Download historical OHLCV bars from MT5."""
    import MetaTrader5 as mt5

    tf_value = TIMEFRAME_MAP.get(timeframe)
    if tf_value is None:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    end_time = end_time or datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=months * 31)

    logger.info(
        f"  Downloading {symbol} {timeframe} from "
        f"{start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}..."
    )

    mt5.symbol_select(symbol, True)
    account = mt5.account_info()
    server = getattr(account, "server", "") if account else ""
    columns = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']
    # Long M5 range requests exceed the connected terminal's accepted query
    # window on some ICMarkets builds. Fetch bounded UTC chunks instead.
    chunked = timeframe == "M5" and (end_time - start_time) > timedelta(days=90)
    raw_chunks = []
    if chunked:
        cursor = start_time
        while cursor < end_time:
            chunk_end = min(cursor + timedelta(days=28), end_time)
            rates = mt5.copy_rates_range(
                symbol, tf_value,
                to_mt5_query_time(cursor, server),
                to_mt5_query_time(chunk_end, server),
            )
            if rates is not None and len(rates) > 0:
                chunk = pd.DataFrame(rates, columns=columns)
                chunk['time'] = normalize_mt5_bar_times(chunk['time'], server)
                chunk = chunk[(chunk['time'] >= cursor) & (chunk['time'] < chunk_end)]
                if not chunk.empty:
                    raw_chunks.append(chunk)
            cursor = chunk_end
        if not raw_chunks:
            raise RuntimeError(f"Failed to download {symbol} {timeframe} in chunks: {mt5.last_error()}")
        df = pd.concat(raw_chunks, ignore_index=True)
        df = df.drop_duplicates(subset=["time"]).sort_values("time")
        logger.info("  Downloaded long %s history as %s bounded chunks", timeframe, len(raw_chunks))
    else:
        query_start = to_mt5_query_time(start_time, server)
        query_end = to_mt5_query_time(end_time, server)
        rates = mt5.copy_rates_range(symbol, tf_value, query_start, query_end)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Failed to download {symbol} {timeframe}: {mt5.last_error()}")
        df = pd.DataFrame(rates, columns=columns)
        df['time'] = normalize_mt5_bar_times(df['time'], server)
    # Ensure strict upper bound: never include bars at or after end_time
    df = df[(df['time'] >= start_time) & (df['time'] < end_time)]
    df = df.set_index('time', drop=False)

    logger.info(f"  ✓ {len(df)} bars downloaded for {symbol}")
    return df


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------
def print_report(result: BacktestResult, balance: float) -> None:
    """Print a nicely formatted console report."""
    w = 60
    sep = "═" * w

    print(f"\n{sep}")
    print(f"  📊 BACKTEST RESULT — {result.symbol}")
    print(sep)
    print(f"  Período     : {result.period_start.strftime('%Y-%m-%d')} → {result.period_end.strftime('%Y-%m-%d')}")
    print(f"  Timeframe   : {result.timeframe}")
    print(f"  Barras      : {result.total_bars:,}")
    print(f"  Balance ini : ${balance:,.2f}")
    print(sep)

    final = balance + result.total_profit_money
    color_profit = "\033[92m" if result.total_profit_money >= 0 else "\033[91m"
    reset = "\033[0m"

    updated_str = f" | {result.pending_updated} actualizados" if result.pending_updated else ""
    print(f"  Stops pendientes: {result.pending_created} creados{updated_str} | {result.pending_filled} activados | {result.pending_expired} expirados | {result.pending_invalidated} invalidados")
    if result.pending_created > 0:
        fill_pct = result.pending_filled / result.pending_created * 100
        exp_pct = result.pending_expired / result.pending_created * 100
        inv_pct = result.pending_invalidated / result.pending_created * 100
        print(f"  Embudo pending  : {fill_pct:.1f}% activadas | {exp_pct:.1f}% expiradas | {inv_pct:.1f}% invalidadas")
    skip_pct = (result.signals_skipped_serialization / result.total_bars * 100) if result.total_bars else 0.0
    print(f"  Serialización   : {result.signals_skipped_serialization:,} barras saltadas por orden/trade activo ({skip_pct:.1f}%)")
    cb_total = result.signals_skipped_circuit_breaker + result.signals_skipped_daily_loss + result.signals_skipped_drawdown
    if cb_total > 0:
        print(f"  Frenos de riesgo: {result.signals_skipped_circuit_breaker} consec.losses | {result.signals_skipped_daily_loss} pérd.diaria | {result.signals_skipped_drawdown} drawdown")
    print(f"  Ganadoras       : {result.winning_trades}  ({result.win_rate}%)")
    print(f"  Perdedoras      : {result.losing_trades}")
    print(f"  Break-Even      : {result.breakeven_trades}")
    print(f"  {color_profit}Profit/Loss     : ${result.total_profit_money:+,.2f}  ({result.total_profit_r:+.1f} R){reset}")
    print(f"  Balance final   : ${final:,.2f}")
    print(f"  Profit Factor   : {result.profit_factor}")
    print(f"  Max Drawdown    : {result.max_drawdown_pct:.1f}%")
    print(f"  Mejor trade     : {result.best_trade_r:+.2f} R")
    print(f"  Peor trade      : {result.worst_trade_r:+.2f} R")
    print(f"  Duración prom.  : {result.avg_trade_duration_minutes:.0f} min")
    print(sep)

    if result.trades:
        print(f"\n  {'#':>3}  {'Dirección':^10} {'Entry':>10} {'Exit':>10} {'SL':>10} {'TP':>10} {'R':>7} {'P/L $':>9}  {'Razón':<14} {'Score':>5}")
        print(f"  {'─'*3}  {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*7} {'─'*9}  {'─'*14} {'─'*5}")
        for idx, t in enumerate(result.trades, 1):
            r_color = "\033[92m" if t.profit_r > 0 else "\033[91m" if t.profit_r < 0 else "\033[93m"
            digits = 3 if "JPY" in t.symbol else 2 if "XAU" in t.symbol else 5
            print(
                f"  {idx:>3}  {t.direction:^10} "
                f"{t.entry_price:>10.{digits}f} "
                f"{(t.exit_price or 0):>10.{digits}f} "
                f"{t.sl_price:>10.{digits}f} "
                f"{t.tp_price:>10.{digits}f} "
                f"{r_color}{t.profit_r:>+7.2f}\033[0m "
                f"{r_color}${t.profit_money:>+8.2f}\033[0m  "
                f"{t.exit_reason:<14} "
                f"{t.total_score:>5.0f}"
            )
        print()


def save_report_csv(results: list, output_dir: str = "data") -> str:
    """Save all trades to a CSV file."""
    rows = []
    for res in results:
        for t in res.trades:
            rows.append({
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
                "total_score": t.total_score,
                "classification": t.classification,
                "entry_strategy": t.entry_strategy,
                "structure": t.structure_score,
                "trend": t.trend_score,
                "momentum": t.momentum_score,
                "volume_score": t.volume_score,
                "volatility": t.volatility_score,
                "regime_label": t.regime_label,
                "smc_sweep_aligned": t.smc_sweep_aligned,
                "smc_fvg_aligned": t.smc_fvg_aligned,
                "smc_order_block_aligned": t.smc_order_block_aligned,
                "shadow_reasons": "; ".join(t.shadow_reasons),
            })

    if not rows:
        return ""

    path = Path(output_dir)
    path.mkdir(exist_ok=True)
    filename = path / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    pd.DataFrame(rows).to_csv(filename, index=False)
    return str(filename)


def save_candidate_audit_csv(rows: list, output_dir: str = "data") -> str:
    """Save a fixed-horizon, no-fill-assumption candidate diagnostic."""
    if not rows:
        return ""
    exported = []
    for row in rows:
        item = row.__dict__.copy()
        item["rejection_reasons"] = "; ".join(item["rejection_reasons"])
        exported.append(item)
    path = Path(output_dir)
    path.mkdir(exist_ok=True)
    filename = path / f"candidate_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    pd.DataFrame(exported).to_csv(filename, index=False)
    return str(filename)


def save_pending_events_csv(results: list, output_dir: str = "data") -> str:
    """Save all pending order lifecycle events (CREATED, FILLED, EXPIRED, INVALIDATED) to CSV."""
    rows = []
    for res in results:
        for ev in res.pending_events:
            rows.append(ev.__dict__)
    if not rows:
        return ""
    path = Path(output_dir)
    path.mkdir(exist_ok=True)
    filename = path / f"pending_events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    pd.DataFrame(rows).to_csv(filename, index=False)
    return str(filename)


def _frame_hash(df: pd.DataFrame) -> str:
    """Fingerprint exact input bars so an experiment can be audited later."""
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(df, index=True).values.tobytes())
    return digest.hexdigest()


def _file_hash(path: str) -> str:
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def save_experiment_manifest(
    experiment_name: str,
    args,
    config,
    source_hashes: dict,
    contract_specs: dict,
    report_path: str,
    candidate_audit_path: str = "",
    pending_events_path: str = "",
    summary_metrics: dict = None,
    code_hashes: dict = None,
) -> str:
    """Persist an immutable record of inputs and assumptions for one run."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    experiment_dir = Path("data") / "experiments"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    config_root = Path(args.config)
    config_files = [
        str(config_root / "settings.yaml"),
        str(config_root / "symbols.yaml"),
        str(config_root / "risk.yaml"),
    ]
    is_oos = bool(args.oos_start)
    default_purpose = (
        "OOS holdout evaluation; do not tune parameters after viewing this result"
        if is_oos
        else "in-sample / exploratory analysis; sealed holdout strictly excluded"
    )
    manifest = {
        "experiment_id": f"{experiment_name}-{timestamp}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": default_purpose,
        "is_oos_evaluation": is_oos,
        "period": args.period,
        "symbols": args.symbols,
        "evaluation_start": args.oos_start,
        "entry_timeframe": config.timeframes.entry,
        "setup_timeframe": config.timeframes.setup,
        "higher_timeframe": config.timeframes.higher,
        "confirmation_timeframe": config.timeframes.confirmation,
        "gap_timeframe": config.timeframes.gap,
        "config_sha256": {path: _file_hash(path) for path in config_files if Path(path).exists()},
        "code_sha256": code_hashes or {},
        "input_sha256": source_hashes,
        "instrument_specs": contract_specs,
        "cost_assumptions": config.backtest.model_dump(),
        "summary_metrics": summary_metrics or {},
        "report_csv": report_path,
        "report_sha256": _file_hash(report_path) if report_path and Path(report_path).exists() else None,
        "candidate_audit_csv": candidate_audit_path,
        "candidate_audit_sha256": _file_hash(candidate_audit_path) if candidate_audit_path and Path(candidate_audit_path).exists() else None,
        "pending_events_csv": pending_events_path,
        "pending_events_sha256": _file_hash(pending_events_path) if pending_events_path and Path(pending_events_path).exists() else None,
    }
    path = experiment_dir / f"{manifest['experiment_id']}.json"
    with open(path, "w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, default=str)
    return str(path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="QuantTradingBot Backtester")
    parser.add_argument("--config", default="config", help="Config directory")
    parser.add_argument("--period", default="1M", help="Backtest period: 1W, 1M, 2M, 3M, 6M, 1Y")
    parser.add_argument("--symbols", default=None, help="Symbols (comma-separated). Default: from config")
    parser.add_argument("--balance", type=float, default=10000.0, help="Starting balance in USD")
    parser.add_argument("--oos-start", default=None, help="Untouched holdout start, ISO date e.g. 2026-07-01")
    parser.add_argument("--end-date", default=None, help="Explicit data cutoff date, ISO format e.g. 2026-06-01")
    parser.add_argument("--experiment-name", default="holdout", help="Immutable experiment-manifest prefix")
    parser.add_argument("--candidate-audit", action="store_true", help="Export all closed-bar score/filter decisions with a fixed 12-bar forward diagnostic.")

    args = parser.parse_args()

    # Parse period
    period_map = {"1M": 1, "2M": 2, "3M": 3, "6M": 6, "1Y": 12, "2Y": 24, "3Y": 36, "5Y": 60}
    period_key = args.period.upper()
    months = period_map.get(period_key, 1)
    days_override = 7 if period_key == "1W" else None

    # Load config
    config = load_config(args.config)
    source_root = Path(__file__).resolve().parent
    code_files = (
        "backtest_runner.py", "backtest/engine.py", "strategy/alchemist.py",
        "strategy/msnr_storyline.py",
        "core/config_loader.py", "main.py",
    )
    code_hashes = {name: _file_hash(str(source_root / name)) for name in code_files}
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else config.market.symbols
    
    # Holdout boundary handling
    evaluation_start = None
    if args.oos_start:
        parsed_start = pd.Timestamp(args.oos_start)
        parsed_start = parsed_start.tz_localize("UTC") if parsed_start.tzinfo is None else parsed_start.tz_convert("UTC")
        evaluation_start = parsed_start.to_pydatetime()
        if args.end_date:
            parsed_end = pd.Timestamp(args.end_date)
            parsed_end = parsed_end.tz_localize("UTC") if parsed_end.tzinfo is None else parsed_end.tz_convert("UTC")
            hard_end = parsed_end.to_pydatetime()
        else:
            hard_end = datetime.now(timezone.utc)
    elif args.end_date:
        parsed_end = pd.Timestamp(args.end_date)
        parsed_end = parsed_end.tz_localize("UTC") if parsed_end.tzinfo is None else parsed_end.tz_convert("UTC")
        hard_end = parsed_end.to_pydatetime()
    else:
        # Default for in-sample / exploratory runs: NEVER download or see bars on or after HOLDOUT_START
        hard_end = HOLDOUT_START

    # Initialize MT5
    import MetaTrader5 as mt5
    if not mt5.initialize():
        logger.critical(f"❌ Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    if account:
        logger.info(f"MT5 connected — Account #{account.login} (${account.balance:,.2f})")
    
    # Header
    print("\n" + "█" * 60)
    print("  🔬 QuantTradingBot — BACKTESTER")
    period_label = "1 semana" if days_override is not None else f"{months} mes(es)"
    print(f"  Período: {period_label}  |  Balance: ${args.balance:,.2f}")
    print(f"  Símbolos: {', '.join(symbols)}")
    print(f"  Timeframe: {config.timeframes.entry}")
    print(f"  Score mínimo: {config.score.minimum} pts")
    if args.oos_start:
        print(f"  MODO OOS HOLDOUT: Evaluando desde {args.oos_start} hasta {hard_end.strftime('%Y-%m-%d')}")
    else:
        print(f"  MODO IN-SAMPLE: Corte superior estricto en {hard_end.strftime('%Y-%m-%d')} (Holdout protegido)")
    print(f"  Costes: comisión ${config.backtest.commission_per_lot_round_turn}/lot RT | slippage {config.backtest.slippage_points} pts")
    print("█" * 60)

    cleaner = DataCleaner()
    specs = {}
    for symbol in symbols:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if info is None:
            logger.warning("No contract metadata for %s; offline fallback will be used.", symbol)
            continue
        if info.point <= 0 or info.trade_tick_size <= 0 or info.trade_tick_value <= 0:
            logger.warning("Incomplete contract metadata for %s; offline fallback will be used.", symbol)
            continue
        specs[symbol] = InstrumentSpec(
            point=float(info.point),
            tick_size=float(info.trade_tick_size),
            tick_value=float(info.trade_tick_value),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
        )
    engine = BacktestEngine(config, initial_balance=args.balance, instrument_specs=specs)
    all_results: list = []
    candidate_audit_rows: list = []
    source_hashes: dict = {}

    total_start = time.time()

    for symbol in symbols:
        try:
            logger.info(f"\n{'─'*40}")
            logger.info(f"Processing {symbol}...")

            # Download entry TF data with strict upper boundary
            history_months = months if days_override is None else 1
            df_raw = download_historical_data(symbol, config.timeframes.entry, history_months, end_time=hard_end)
            if days_override is not None:
                entry_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_override)
                df_raw = df_raw[df_raw["time"] >= entry_cutoff]
            source_hashes[f"{symbol}:{config.timeframes.entry}"] = _frame_hash(df_raw)
            df_clean, quality = cleaner.clean(df_raw)
            logger.info(f"  Cleaned: {len(df_clean)} bars (quality: {quality.get('quality_ok', True)})")

            # Download setup timeframe with strict upper boundary
            df_setup = None
            try:
                df_setup_raw = download_historical_data(symbol, config.timeframes.setup, history_months, end_time=hard_end)
                if days_override is not None:
                    setup_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_override + 3)
                    df_setup_raw = df_setup_raw[df_setup_raw["time"] >= setup_cutoff]
                source_hashes[f"{symbol}:{config.timeframes.setup}"] = _frame_hash(df_setup_raw)
                df_setup, _ = cleaner.clean(df_setup_raw)
            except Exception:
                logger.warning(f"  Could not download setup data for {symbol}, continuing without it.")

            # Download HTF data with strict upper boundary
            df_htf = None
            try:
                df_htf_raw = download_historical_data(symbol, config.timeframes.higher, history_months, end_time=hard_end)
                if days_override is not None:
                    htf_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_override + 12)
                    df_htf_raw = df_htf_raw[df_htf_raw["time"] >= htf_cutoff]
                source_hashes[f"{symbol}:{config.timeframes.higher}"] = _frame_hash(df_htf_raw)
                df_htf, _ = cleaner.clean(df_htf_raw)
            except Exception:
                logger.warning(f"  Could not download HTF data for {symbol}, continuing without it.")

            df_daily = df_weekly = df_monthly = None
            df_confirmation = df_gap = None
            if config.alchemist.enabled:
                for timeframe, count, output_name in (
                    (config.timeframes.daily, history_months + 2, "daily"),
                    (config.timeframes.weekly, history_months + 2, "weekly"),
                    (config.timeframes.monthly, max(18, history_months + 2), "monthly"),
                    (config.timeframes.confirmation, history_months + 2, "confirmation"),
                    (config.timeframes.gap, history_months + 2, "gap"),
                ):
                    try:
                        ref_raw = download_historical_data(symbol, timeframe, count, end_time=hard_end)
                        source_hashes[f"{symbol}:{timeframe}"] = _frame_hash(ref_raw)
                        ref_clean, _ = cleaner.clean(ref_raw)
                        if output_name == "daily":
                            df_daily = ref_clean
                        elif output_name == "weekly":
                            df_weekly = ref_clean
                        elif output_name == "confirmation":
                            df_confirmation = ref_clean
                        elif output_name == "gap":
                            df_gap = ref_clean
                        else:
                            df_monthly = ref_clean
                        logger.info(f"  Cleaned: {len(ref_clean)} {timeframe} reference bars")
                    except Exception:
                        logger.warning("  Could not download %s reference data for %s.", timeframe, symbol)

            # Run backtest
            t0 = time.time()
            result = engine.run(
                symbol, df_clean, df_htf, df_setup=df_setup,
                evaluation_start=evaluation_start, df_daily=df_daily, df_weekly=df_weekly,
                df_monthly=df_monthly, df_confirmation=df_confirmation, df_gap=df_gap,
            )
            elapsed = time.time() - t0

            logger.info(f"  ✓ Backtest complete in {elapsed:.1f}s — {result.total_trades} trades")
            print_report(result, args.balance)
            all_results.append(result)
            if args.candidate_audit:
                candidate_audit_rows.extend(engine.diagnose_candidates(
                    symbol, df_clean, df_htf, evaluation_start=evaluation_start,
                    df_setup=df_setup,
                ))

        except Exception as e:
            logger.error(f"  ✗ Error backtesting {symbol}: {e}", exc_info=True)

    # Summary across all symbols
    total_elapsed = time.time() - total_start
    total_trades = sum(r.total_trades for r in all_results)
    total_profit = sum(r.total_profit_money for r in all_results)
    total_r = sum(r.total_profit_r for r in all_results)
    total_pending_created = sum(r.pending_created for r in all_results)
    total_pending_updated = sum(r.pending_updated for r in all_results)
    total_pending_filled = sum(r.pending_filled for r in all_results)
    total_pending_expired = sum(r.pending_expired for r in all_results)
    total_pending_invalidated = sum(r.pending_invalidated for r in all_results)
    total_skipped = sum(r.signals_skipped_serialization for r in all_results)
    total_skipped_cb = sum(r.signals_skipped_circuit_breaker for r in all_results)
    total_skipped_daily = sum(r.signals_skipped_daily_loss for r in all_results)
    total_skipped_dd = sum(r.signals_skipped_drawdown for r in all_results)

    print("\n" + "█" * 60)
    print("  📈 RESUMEN GLOBAL DEL BACKTEST")
    print("█" * 60)
    print(f"  Símbolos procesados : {len(all_results)}")
    print(f"  Total operaciones   : {total_trades}")
    updated_str = f" | {total_pending_updated} actualizados" if total_pending_updated else ""
    print(f"  Stops pendientes    : {total_pending_created} creados{updated_str} | {total_pending_filled} activados | {total_pending_expired} expirados | {total_pending_invalidated} invalidados")
    if total_pending_created > 0:
        fill_pct = total_pending_filled / total_pending_created * 100
        exp_pct = total_pending_expired / total_pending_created * 100
        inv_pct = total_pending_invalidated / total_pending_created * 100
        print(f"  Embudo global       : {fill_pct:.1f}% activadas | {exp_pct:.1f}% expiradas | {inv_pct:.1f}% invalidadas")
    print(f"  Barras serializadas : {total_skipped:,} saltadas con orden/posición activa")
    total_cb_all = total_skipped_cb + total_skipped_daily + total_skipped_dd
    if total_cb_all > 0:
        print(f"  Frenos de riesgo    : {total_skipped_cb} consec.losses | {total_skipped_daily} pérd.diaria | {total_skipped_dd} drawdown")
    total_missed_r = sum(r.opportunity_cost_r_missed_target for r in all_results)
    total_target_reached = sum(r.count_expired_target_reached for r in all_results)
    if total_target_reached > 0:
        print(f"  Targets no retrocedidos: {total_target_reached} órdenes | Costo de oportunidad: +{total_missed_r:.1f} R")
    color = "\033[92m" if total_profit >= 0 else "\033[91m"
    print(f"  {color}Suma por símbolo    : ${total_profit:+,.2f}  ({total_r:+.1f} R)\033[0m")
    print("  Nota: no es una cartera combinada; cada símbolo se simula con su propio saldo.")
    print(f"  Tiempo de ejecución : {total_elapsed:.1f}s")
    outcomes_r = [trade.profit_r for result in all_results for trade in result.trades]
    bootstrap = bootstrap_trade_outcomes(outcomes_r)
    print(
        f"  Bootstrap (5% R)   : {bootstrap.p05_total_r:+.2f} R | "
        f"P(R>0): {bootstrap.probability_positive_r:.1%} | n={bootstrap.trade_count}"
    )
    if not bootstrap.sufficient_sample:
        print("  [NO ACEPTAR] Muestra <100 trades OOS: resultado exploratorio, no evidencia de edge.")

    summary_metrics = {
        "total_trades": total_trades,
        "total_profit_money": round(total_profit, 2),
        "total_profit_r": round(total_r, 2),
        "signals_skipped_circuit_breaker": total_skipped_cb,
        "signals_skipped_daily_loss": total_skipped_daily,
        "signals_skipped_drawdown": total_skipped_dd,
        "count_expired_target_reached": total_target_reached,
        "opportunity_cost_r_missed_target": round(total_missed_r, 2),
    }

    # Save CSV
    csv_path = save_report_csv(all_results)
    if csv_path:
        print(f"  CSV guardado en     : {csv_path}")
    pending_events_path = save_pending_events_csv(all_results)
    if pending_events_path:
        print(f"  Eventos pendientes  : {pending_events_path}")
    candidate_audit_path = save_candidate_audit_csv(candidate_audit_rows)
    if candidate_audit_path:
        print(f"  Auditoría candidatos: {candidate_audit_path}")
    manifest_path = save_experiment_manifest(
        args.experiment_name, args, config, source_hashes,
        {symbol: spec.__dict__ for symbol, spec in specs.items()}, csv_path, candidate_audit_path,
        pending_events_path, summary_metrics=summary_metrics,
        code_hashes=code_hashes,
    )
    print(f"  Manifiesto inmutable: {manifest_path}")

    print("█" * 60 + "\n")

    # mt5.shutdown()  # Preserved live MT5 session


if __name__ == "__main__":
    main()
