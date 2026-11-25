"""Run a comparable historical and +20% cost audit for the gold bot fleet.

This research utility downloads one shared XAUUSD history, then evaluates the
current A/B/C/D configs independently with the standard BacktestEngine. It is
an exploratory simulation, not an untouched OOS evaluation. The standard
engine does not apply the live MLFilter.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5
import pandas as pd

from backtest.engine import BacktestEngine, InstrumentSpec
from backtest_runner import (
    _frame_hash,
    download_historical_data,
    save_pending_events_csv,
    save_report_csv,
)
from core.config_loader import load_config
from data.cleaner import DataCleaner
from backtest.robustness import bootstrap_trade_outcomes


CONFIGS = {letter: ROOT / "config" / f"bot_{letter}_gold" for letter in "abcd"}
END_TIME = pd.Timestamp("2026-06-01", tz="UTC").to_pydatetime()
INITIAL_BALANCE = 10_000.0
COST_STRESS = 1.20


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_profile(letter: str, stress: bool, frames, specs, input_hashes):
    config_dir = CONFIGS[letter]
    cfg = load_config(str(config_dir))
    if stress:
        cfg.backtest.commission_per_lot_round_turn *= COST_STRESS
        cfg.backtest.slippage_points *= COST_STRESS
        cfg.backtest.gap_slippage_points *= COST_STRESS

    engine = BacktestEngine(cfg, initial_balance=INITIAL_BALANCE, instrument_specs=specs)
    result = engine.run("XAUUSD", frames["H1"], frames["D1"], df_setup=frames["H4"])
    bootstrap = bootstrap_trade_outcomes([trade.profit_r for trade in result.trades])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"fleet-{letter.upper()}-{'cost120' if stress else 'base'}-{stamp}"
    report_csv = save_report_csv([result])
    pending_csv = save_pending_events_csv([result])

    config_hashes = {
        f"config/bot_{letter}_gold/{name}": sha256(config_dir / name)
        for name in ("settings.yaml", "symbols.yaml", "risk.yaml")
    }
    manifest = {
        "experiment_id": tag,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "in-sample / exploratory fleet profile comparison; holdout excluded",
        "is_oos_evaluation": False,
        "period": "2Y",
        "evaluation_start": None,
        "data_cutoff_exclusive": END_TIME.isoformat(),
        "bot": f"BOT-{letter.upper()}",
        "symbol": "XAUUSD",
        "initial_balance_per_bot": INITIAL_BALANCE,
        "config_sha256": config_hashes,
        "input_sha256": input_hashes,
        "instrument_specs": {symbol: spec.__dict__ for symbol, spec in specs.items()},
        "cost_assumptions": cfg.backtest.model_dump(),
        "stress_multiplier_applied_to_commission_and_slippage": COST_STRESS if stress else 1.0,
        "summary_metrics": {
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "win_rate_pct": result.win_rate,
            "total_profit_money": result.total_profit_money,
            "total_profit_r": result.total_profit_r,
            "avg_profit_r": result.avg_profit_r,
            "profit_factor": result.profit_factor,
            "max_drawdown_pct": result.max_drawdown_pct,
            "bootstrap_p05_r": bootstrap.p05_total_r,
            "bootstrap_probability_positive": bootstrap.probability_positive_r,
            "bootstrap_sufficient_sample": bootstrap.sufficient_sample,
            "ml_applied": False,
        },
        "report_csv": report_csv,
        "pending_events_csv": pending_csv,
    }
    manifest_path = ROOT / "data" / "experiments" / f"{tag}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(
        f"DONE BOT-{letter.upper()} {'+20% COST' if stress else 'BASE'}: "
        f"n={result.total_trades} net=${result.total_profit_money:.2f} "
        f"R={result.total_profit_r:.2f} PF={result.profit_factor:.3f} "
        f"DD={result.max_drawdown_pct:.2f}% manifest={manifest_path.name}",
        flush=True,
    )
    return result, manifest_path


def main():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        end = END_TIME
        raw = {
            tf: download_historical_data("XAUUSD", tf, months=24, end_time=end)
            for tf in ("H1", "H4", "D1")
        }
        cleaner = DataCleaner()
        frames = {tf: cleaner.clean(df)[0] for tf, df in raw.items()}
        input_hashes = {f"XAUUSD:{tf}": _frame_hash(df) for tf, df in raw.items()}

        info = mt5.symbol_info("XAUUSD")
        if info is None:
            raise RuntimeError("MT5 has no XAUUSD contract metadata")
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
        print(
            "SHARED DATA: " + ", ".join(f"{tf}={len(frames[tf])} bars" for tf in frames),
            flush=True,
        )
        rows = []
        for letter in "abcd":
            for stress in (False, True):
                result, manifest_path = run_profile(
                    letter, stress, frames, specs, input_hashes
                )
                bootstrap = bootstrap_trade_outcomes(
                    [trade.profit_r for trade in result.trades]
                )
                rows.append({
                    "bot": f"BOT-{letter.upper()}",
                    "scenario": "cost_plus_20pct" if stress else "base",
                    "trades": result.total_trades,
                    "wins": result.winning_trades,
                    "losses": result.losing_trades,
                    "win_rate_pct": result.win_rate,
                    "net_usd_per_10000": result.total_profit_money,
                    "total_r": result.total_profit_r,
                    "expectancy_r": result.avg_profit_r,
                    "profit_factor": result.profit_factor,
                    "max_drawdown_pct": result.max_drawdown_pct,
                    "bootstrap_p05_r": bootstrap.p05_total_r,
                    "bootstrap_probability_positive": bootstrap.probability_positive_r,
                    "bootstrap_sufficient_sample": bootstrap.sufficient_sample,
                    "manifest": str(manifest_path.relative_to(ROOT)),
                })
        output = ROOT / "data" / "experiments" / "fleet_audit_summary_20260922.csv"
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"SUMMARY: {output}", flush=True)
    finally:
        pass  # mt5.shutdown() - Preserved live MT5 connection


if __name__ == "__main__":
    main()
