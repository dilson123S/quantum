"""Quantitative Backtest Suite: Baseline Spike Strategy vs. System 1 Decision Engine.

Compares the baseline Hawkes Jump-Diffusion Spike Strategy against the newly implemented
Open-Source System 1 Decision Engine (NanoJev / Laya) on real MetaTrader 5 Deriv historical data.

Generates comparative institutional metrics:
- Trades Count, Win Rate (%), Profit Factor
- Net R & Net USD PnL
- Max Drawdown (R and USD)
- Filter Efficacy: False Positives avoided (Losses saved), True Positives preserved (Wins retained)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.constants import Direction
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal
from strategy.synthetic_system_one import SyntheticSystemOneEvaluator, SyntheticEvaluationResult
from filters.system_one.hub import SystemOneHub

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("System1Backtest")


@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    pnl_r: float
    pnl_cash: float
    exit_reason: str
    setup_name: str
    confluences: str
    system_one_approved: bool = True
    evidence_score: int = 4
    confidence: float = 0.80


@dataclass
class FilteredTrade:
    time: str
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    evidence_score: int
    confidence: float
    reasons: List[str]
    confluences: str
    hypothetical_outcome: str = "UNKNOWN"
    hypothetical_pnl_r: float = 0.0


def fetch_mt5_data(
    symbol: str, count_m5: int = 20000, start_pos: int = 0
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetch M5, M15, and H1 bars directly from Deriv MT5 terminal."""
    if not MT5_AVAILABLE:
        return None, None, None

    rates_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, start_pos, count_m5)
    if rates_m5 is None or len(rates_m5) == 0:
        return None, None, None

    df_m5 = pd.DataFrame(rates_m5)
    df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")

    t_start = df_m5["time"].iloc[0] - pd.Timedelta(days=5)
    t_end = df_m5["time"].iloc[-1]

    rates_m15 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, t_start, t_end)
    rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, t_start, t_end)

    df_m15 = pd.DataFrame(rates_m15) if rates_m15 is not None and len(rates_m15) > 0 else None
    if df_m15 is not None:
        df_m15["time"] = pd.to_datetime(df_m15["time"], unit="s")

    df_h1 = pd.DataFrame(rates_h1) if rates_h1 is not None and len(rates_h1) > 0 else None
    if df_h1 is not None:
        df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s")

    return df_m5, df_m15, df_h1


def simulate_trade_path(
    df_m5: pd.DataFrame,
    start_idx: int,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    point: float,
    max_holding_bars: int = 12,
) -> Tuple[str, float, str]:
    """Forward simulate a trade path to determine exact outcome (SL, TP, Spike, Time Decay)."""
    curr_sl = sl
    initial_risk = abs(entry - sl)
    tp_dist = abs(tp - entry)
    n_bars = len(df_m5)
    bars_held = 0

    for j in range(start_idx, min(start_idx + max_holding_bars + 1, n_bars)):
        bar = df_m5.iloc[j]
        c_open = float(bar["open"])
        c_high = float(bar["high"])
        c_low = float(bar["low"])
        c_close = float(bar["close"])
        bar_time_str = str(bar["time"])
        bars_held += 1

        peak_gain = (c_high - entry) if direction == "BUY" else (entry - c_low)
        curr_gain = (c_close - entry) if direction == "BUY" else (entry - c_close)

        hit_sl = (direction == "BUY" and c_low <= curr_sl) or (direction == "SELL" and c_high >= curr_sl)
        hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

        is_spike = bool(bar.get("is_boom_spike", False)) if direction == "BUY" else bool(bar.get("is_crash_spike", False))
        if is_spike:
            hit_tp = True

        if hit_sl and hit_tp and not is_spike:
            if abs(c_open - curr_sl) < abs(c_open - tp):
                hit_tp = False
            else:
                hit_sl = False

        if hit_tp:
            actual_gain = abs(c_close - entry) if is_spike else tp_dist
            pnl_r = max(1.0, actual_gain / initial_risk) if initial_risk > 0 else 2.0
            return "WIN_SPIKE" if is_spike else "WIN_TP", round(pnl_r, 2), bar_time_str

        if hit_sl:
            sl_diff = (curr_sl - entry) if direction == "BUY" else (entry - curr_sl)
            pnl_r = round(sl_diff / initial_risk, 2) if initial_risk > 0 else -1.0
            reason = "EXIT_PROFIT_LOCK" if pnl_r > 0 else "LOSS_SL"
            return reason, pnl_r, bar_time_str

        if bars_held >= max_holding_bars and curr_gain <= 0:
            pnl_r = -round(abs(curr_gain) / initial_risk, 2) if initial_risk > 0 else -0.5
            return "EXIT_TIME_DECAY", max(-1.0, pnl_r), bar_time_str

        # Lock profit if reached 85% of TP
        if tp_dist > 0 and peak_gain >= tp_dist * 0.85:
            lock_dist = peak_gain * 0.50
            target_sl = entry + lock_dist if direction == "BUY" else entry - lock_dist
            if (direction == "BUY" and target_sl > curr_sl) or (direction == "SELL" and target_sl < curr_sl):
                curr_sl = target_sl

    return "EXIT_TIME_LIMIT", 0.0, str(df_m5.iloc[min(start_idx + max_holding_bars, n_bars - 1)]["time"])


def run_spike_simulation(
    symbol: str,
    df_m5: pd.DataFrame,
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    spike_strat: SpikeSniperStrategy,
    s1_evaluator: Optional[SyntheticSystemOneEvaluator] = None,
    risk_per_trade_usd: float = 75.0,
    point: float = 0.01,
) -> Tuple[List[BacktestTrade], List[FilteredTrade]]:
    """Run bar-by-bar simulation for spike sniper with optional System 1 gating."""
    trades: List[BacktestTrade] = []
    filtered: List[FilteredTrade] = []
    active_trade: Optional[Dict[str, Any]] = None
    bars_held = 0

    df_m5_calc = spike_strat.calculate_indicators(df_m5)
    df_m15_calc = spike_strat.calculate_indicators(df_m15) if df_m15 is not None else None
    if df_m15_calc is not None and "ema_50" not in df_m15_calc.columns:
        df_m15_calc["ema_50"] = df_m15_calc["close"].ewm(span=50, adjust=False).mean()

    df_h1_calc = df_h1.copy() if df_h1 is not None else None
    if df_h1_calc is not None and "ema_50" not in df_h1_calc.columns:
        df_h1_calc["ema_50"] = df_h1_calc["close"].ewm(span=50, adjust=False).mean()

    n_bars = len(df_m5_calc)
    start_bar = 50

    m15_times = df_m15_calc["time"].values if df_m15_calc is not None and not df_m15_calc.empty else None
    h1_times = df_h1_calc["time"].values if df_h1_calc is not None and not df_h1_calc.empty else None

    for i in range(start_bar, n_bars):
        curr_bar = df_m5_calc.iloc[i]
        c_open = float(curr_bar["open"])
        c_high = float(curr_bar["high"])
        c_low = float(curr_bar["low"])
        c_close = float(curr_bar["close"])
        bar_time = curr_bar["time"]
        bar_time_str = str(bar_time)

        # 1. Manage Active Trade
        if active_trade is not None:
            bars_held += 1
            direction = active_trade["direction"]
            entry = active_trade["entry"]
            sl = active_trade["current_sl"]
            tp = active_trade["tp"]
            initial_risk = active_trade["initial_risk"]
            tp_dist = active_trade["tp_dist"]

            peak_gain = (c_high - entry) if direction == "BUY" else (entry - c_low)
            curr_gain = (c_close - entry) if direction == "BUY" else (entry - c_close)

            hit_sl = (direction == "BUY" and c_low <= sl) or (direction == "SELL" and c_high >= sl)
            hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

            is_spike = bool(curr_bar.get("is_boom_spike", False)) if direction == "BUY" else bool(curr_bar.get("is_crash_spike", False))
            if is_spike:
                hit_tp = True

            if hit_sl and hit_tp and not is_spike:
                if abs(c_open - sl) < abs(c_open - tp):
                    hit_tp = False
                else:
                    hit_sl = False

            if hit_tp:
                actual_gain = abs(c_close - entry) if is_spike else tp_dist
                pnl_r = max(1.0, actual_gain / initial_risk) if initial_risk > 0 else 2.0
                trades.append(BacktestTrade(
                    symbol=symbol,
                    direction=direction,
                    entry_time=active_trade["entry_time"],
                    exit_time=bar_time_str,
                    entry_price=entry,
                    exit_price=c_close if is_spike else tp,
                    sl_price=sl,
                    tp_price=tp,
                    pnl_r=round(pnl_r, 2),
                    pnl_cash=round(pnl_r * risk_per_trade_usd, 2),
                    exit_reason="WIN_SPIKE" if is_spike else "WIN_TP",
                    setup_name=active_trade["setup_name"],
                    confluences=active_trade["confluences"],
                    system_one_approved=active_trade.get("s1_approved", True),
                    evidence_score=active_trade.get("evidence_score", 4),
                    confidence=active_trade.get("confidence", 0.80),
                ))
                active_trade = None
                continue

            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r = round(sl_diff / initial_risk, 2) if initial_risk > 0 else -1.0
                reason = "EXIT_PROFIT_LOCK" if pnl_r > 0 else "LOSS_SL"

                trades.append(BacktestTrade(
                    symbol=symbol,
                    direction=direction,
                    entry_time=active_trade["entry_time"],
                    exit_time=bar_time_str,
                    entry_price=entry,
                    exit_price=sl,
                    sl_price=sl,
                    tp_price=tp,
                    pnl_r=pnl_r,
                    pnl_cash=round(pnl_r * risk_per_trade_usd, 2),
                    exit_reason=reason,
                    setup_name=active_trade["setup_name"],
                    confluences=active_trade["confluences"],
                    system_one_approved=active_trade.get("s1_approved", True),
                    evidence_score=active_trade.get("evidence_score", 4),
                    confidence=active_trade.get("confidence", 0.80),
                ))
                active_trade = None
                continue

            if bars_held >= 12 and curr_gain <= 0:
                pnl_r = -round(abs(curr_gain) / initial_risk, 2) if initial_risk > 0 else -0.5
                trades.append(BacktestTrade(
                    symbol=symbol,
                    direction=direction,
                    entry_time=active_trade["entry_time"],
                    exit_time=bar_time_str,
                    entry_price=entry,
                    exit_price=c_close,
                    sl_price=sl,
                    tp_price=tp,
                    pnl_r=max(-1.0, pnl_r),
                    pnl_cash=round(max(-1.0, pnl_r) * risk_per_trade_usd, 2),
                    exit_reason="EXIT_TIME_DECAY",
                    setup_name=active_trade["setup_name"],
                    confluences=active_trade["confluences"],
                    system_one_approved=active_trade.get("s1_approved", True),
                    evidence_score=active_trade.get("evidence_score", 4),
                    confidence=active_trade.get("confidence", 0.80),
                ))
                active_trade = None
                continue

            if tp_dist > 0 and peak_gain >= tp_dist * 0.85:
                lock_dist = peak_gain * 0.50
                target_sl = entry + lock_dist if direction == "BUY" else entry - lock_dist
                if (direction == "BUY" and target_sl > sl) or (direction == "SELL" and target_sl < sl):
                    active_trade["current_sl"] = target_sl

            continue

        # 2. Look for Candidate Setup
        if h1_times is not None:
            idx_h1 = int(np.searchsorted(h1_times, np.datetime64(bar_time), side="right"))
            h1_slice = df_h1_calc.iloc[max(0, idx_h1 - 100):idx_h1] if idx_h1 > 0 else None
        else:
            h1_slice = None

        if m15_times is not None:
            idx_m15 = int(np.searchsorted(m15_times, np.datetime64(bar_time), side="right"))
            m15_slice = df_m15_calc.iloc[max(0, idx_m15 - 100):idx_m15] if idx_m15 > 0 else None
        else:
            m15_slice = None

        m5_slice = df_m5_calc.iloc[max(0, i - 80):i + 1]

        sig = spike_strat.detect_spike_setup(symbol, m5_slice, df_higher=m15_slice, df_h1=h1_slice, point=point)
        if sig is None:
            continue

        # 3. System 1 Evaluation Gate
        evidence_score = 4
        confidence = 0.80
        if s1_evaluator is not None:
            s1_res = s1_evaluator.evaluate_setup(
                symbol=symbol,
                direction=sig.direction,
                entry_price=sig.entry_price,
                sl_price=sig.sl_price,
                tp_price=sig.tp_price,
                target_rr=sig.target_rr,
                df_m5=m5_slice,
                df_m15=m15_slice,
                df_h1=h1_slice,
                confluences=sig.confluences,
                point=point,
            )
            evidence_score = s1_res.evidence_score
            confidence = s1_res.confidence

            if not s1_res.approved:
                # Setup was rejected by System 1. Simulate hypothetical path to test efficacy
                hyp_reason, hyp_pnl_r, _ = simulate_trade_path(
                    df_m5=df_m5_calc,
                    start_idx=i + 1,
                    direction=sig.direction.value,
                    entry=sig.entry_price,
                    sl=sig.sl_price,
                    tp=sig.tp_price,
                    point=point,
                    max_holding_bars=12,
                )
                filtered.append(FilteredTrade(
                    time=bar_time_str,
                    symbol=symbol,
                    direction=sig.direction.value,
                    entry_price=sig.entry_price,
                    sl_price=sig.sl_price,
                    tp_price=sig.tp_price,
                    evidence_score=evidence_score,
                    confidence=confidence,
                    reasons=s1_res.rejection_reasons,
                    confluences=", ".join(sig.confluences),
                    hypothetical_outcome=hyp_reason,
                    hypothetical_pnl_r=hyp_pnl_r,
                ))
                continue

        # Trade approved (either baseline mode or approved by System 1)
        risk_pts = abs(sig.entry_price - sig.sl_price)
        active_trade = {
            "direction": sig.direction.value,
            "entry": sig.entry_price,
            "initial_sl": sig.sl_price,
            "current_sl": sig.sl_price,
            "tp": sig.tp_price,
            "initial_risk": risk_pts,
            "tp_dist": abs(sig.tp_price - sig.entry_price),
            "entry_time": bar_time_str,
            "setup_name": sig.setup_name,
            "confluences": ", ".join(sig.confluences),
            "s1_approved": True,
            "evidence_score": evidence_score,
            "confidence": confidence,
        }
        bars_held = 0

    return trades, filtered


def calculate_metrics(trades: List[BacktestTrade], initial_balance: float = 10000.0) -> Dict[str, Any]:
    """Calculate institutional performance & risk metrics."""
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "win_rate_pct": 0.0, "profit_factor": 0.0, "net_r": 0.0,
            "net_cash_usd": 0.0, "return_pct": 0.0, "expectancy_r": 0.0,
            "max_drawdown_r": 0.0, "max_drawdown_pct": 0.0, "max_drawdown_usd": 0.0,
            "avg_win_r": 0.0, "avg_loss_r": 0.0,
        }

    wins = [t for t in trades if t.pnl_r > 0.05]
    losses = [t for t in trades if t.pnl_r < -0.05]
    bes = [t for t in trades if -0.05 <= t.pnl_r <= 0.05]

    win_count = len(wins)
    loss_count = len(losses)
    total = len(trades)
    win_rate = (win_count / total * 100.0) if total > 0 else 0.0

    gross_profit_r = sum(t.pnl_r for t in wins)
    gross_loss_r = abs(sum(t.pnl_r for t in losses))
    net_r = gross_profit_r - gross_loss_r
    profit_factor = (gross_profit_r / gross_loss_r) if gross_loss_r > 0 else (99.0 if gross_profit_r > 0 else 0.0)

    gross_profit_usd = sum(t.pnl_cash for t in wins)
    gross_loss_usd = abs(sum(t.pnl_cash for t in losses))
    net_cash_usd = gross_profit_usd - gross_loss_usd
    return_pct = (net_cash_usd / initial_balance) * 100.0

    avg_win_r = (gross_profit_r / win_count) if win_count > 0 else 0.0
    avg_loss_r = (gross_loss_r / loss_count) if loss_count > 0 else 0.0
    loss_rate = loss_count / total if total > 0 else 0.0
    expectancy_r = ((win_rate / 100.0) * avg_win_r) - (loss_rate * avg_loss_r)

    # Equity Curve Drawdown
    equity = initial_balance
    peak = equity
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    eq_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0

    for t in trades:
        equity += t.pnl_cash
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_usd:
            max_dd_usd = dd
            max_dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0

        eq_r += t.pnl_r
        if eq_r > peak_r:
            peak_r = eq_r
        dd_r = peak_r - eq_r
        if dd_r > max_dd_r:
            max_dd_r = dd_r

    return {
        "total_trades": total,
        "wins": win_count,
        "losses": loss_count,
        "breakeven": len(bes),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "net_r": round(net_r, 2),
        "net_cash_usd": round(net_cash_usd, 2),
        "return_pct": round(return_pct, 2),
        "expectancy_r": round(expectancy_r, 2),
        "max_drawdown_r": round(max_dd_r, 2),
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Backtest Quantum-Deriv: Baseline vs System 1")
    parser.add_argument("--count", type=int, default=20000, help="Number of M5 bars (~70 continuous days)")
    parser.add_argument("--start-pos", type=int, default=0, help="MT5 history start position (0 = latest)")
    parser.add_argument("--provider", type=str, default="nanojev", choices=["nanojev", "laya"], help="System 1 provider")
    parser.add_argument("--risk-usd", type=float, default=75.0, help="Risk USD per trade")
    args = parser.parse_args()

    print("=" * 80)
    print("   QUANTUM-DERIV: BACKTEST INSTITUCIONAL COMPARATIVO")
    print(f"   [BASELINE SPIKE SNIPER vs. MOTOR SYSTEM 1 ({args.provider.upper()})]")
    print("=" * 80)

    if not MT5_AVAILABLE:
        print("[ERROR] MetaTrader5 no esta instalado en este entorno de Python.")
        return

    if not mt5.initialize():
        print(f"[ERROR] No se pudo conectar a MetaTrader 5: {mt5.last_error()}")
        return

    symbols_config = [
        {"symbol": "Boom 500 Index", "point": 0.001},
        {"symbol": "Crash 500 Index", "point": 0.001},
        {"symbol": "Boom 1000 Index", "point": 0.0001},
        {"symbol": "Crash 1000 Index", "point": 0.0001},
        {"symbol": "Boom 300 Index", "point": 0.001},
    ]

    spike_strat = SpikeSniperStrategy(
        max_holding_bars=12,
        min_rr=1.50,
        target_rr=2.0,
        sl_buffer_atr=1.0,
        crash_rsi_overbought=38.0,
        boom_rsi_oversold=62.0,
        touch_atr=1.20,
        close_near_atr=1.30,
    )

    # Initialize System 1 Hub and Evaluator (Active Gate)
    s1_hub = SystemOneHub(
        enabled=True,
        provider_name=args.provider,
        shadow_mode=False,
        device="cuda",
    )
    s1_evaluator = SyntheticSystemOneEvaluator(
        hub=s1_hub,
        shadow_mode=False,
        min_evidence_score=3,
        min_confidence=0.60,
    )

    all_baseline_trades: List[BacktestTrade] = []
    all_s1_trades: List[BacktestTrade] = []
    all_filtered_trades: List[FilteredTrade] = []
    comparison_rows: List[Dict[str, Any]] = []

    earliest_date = None
    latest_date = None

    print(f"\n[+] Descargando datos M5/M15/H1 de MT5 ({args.count} velas por simbolo) y ejecutando simulaciones...")

    for item in symbols_config:
        sym = item["symbol"]
        pt = item["point"]

        df_m5, df_m15, df_h1 = fetch_mt5_data(sym, count_m5=args.count, start_pos=args.start_pos)
        if df_m5 is None or len(df_m5) < 100:
            print(f"[-] {sym}: Datos insuficientes en MT5. Omitiendo.")
            continue

        start_time = df_m5["time"].iloc[0]
        end_time = df_m5["time"].iloc[-1]
        start_date = start_time.strftime("%Y-%m-%d")
        end_date = end_time.strftime("%Y-%m-%d")

        if earliest_date is None or start_time < earliest_date:
            earliest_date = start_time
        if latest_date is None or end_time > latest_date:
            latest_date = end_time

        duration_days = max(1.0, (end_time - start_time).total_seconds() / 86400.0)

        # PASS 1: Baseline (Spike Sniper sin filtro System 1)
        base_trades, _ = run_spike_simulation(
            symbol=sym,
            df_m5=df_m5,
            df_m15=df_m15,
            df_h1=df_h1,
            spike_strat=spike_strat,
            s1_evaluator=None,
            risk_per_trade_usd=args.risk_usd,
            point=pt,
        )

        # PASS 2: Con filtro System 1
        s1_trades, filtered = run_spike_simulation(
            symbol=sym,
            df_m5=df_m5,
            df_m15=df_m15,
            df_h1=df_h1,
            spike_strat=spike_strat,
            s1_evaluator=s1_evaluator,
            risk_per_trade_usd=args.risk_usd,
            point=pt,
        )

        all_baseline_trades.extend(base_trades)
        all_s1_trades.extend(s1_trades)
        all_filtered_trades.extend(filtered)

        m_base = calculate_metrics(base_trades)
        m_s1 = calculate_metrics(s1_trades)

        # Filter analysis
        total_filtered = len(filtered)
        losses_saved = sum(1 for f in filtered if f.hypothetical_pnl_r < 0)
        wins_missed = sum(1 for f in filtered if f.hypothetical_pnl_r > 0)
        filter_precision = (losses_saved / total_filtered * 100.0) if total_filtered > 0 else 0.0

        print(f"\n---> {sym.upper()} ({len(df_m5)} velas M5 | {start_date} a {end_date} | {duration_days:.1f} dias)")
        print(f"     [BASELINE]: Trades: {m_base['total_trades']:2d} | Win Rate: {m_base['win_rate_pct']:4.1f}% | "
              f"PF: {m_base['profit_factor']:4.2f} | Net R: {m_base['net_r']:+6.2f}R | Net USD: ${m_base['net_cash_usd']:+8.2f} | Max DD: ${m_base['max_drawdown_usd']:.2f}")
        print(f"     [SYSTEM-1]: Trades: {m_s1['total_trades']:2d} | Win Rate: {m_s1['win_rate_pct']:4.1f}% | "
              f"PF: {m_s1['profit_factor']:4.2f} | Net R: {m_s1['net_r']:+6.2f}R | Net USD: ${m_s1['net_cash_usd']:+8.2f} | Max DD: ${m_s1['max_drawdown_usd']:.2f}")
        print(f"     [FILTRADO]: Total Bloqueados: {total_filtered} | Perdidas Evitadas (Falsos Disparos): {losses_saved} | "
              f"Ganadoras Rechazadas: {wins_missed} | Precision Filtro: {filter_precision:.1f}%")

        comparison_rows.append({
            "symbol": sym,
            "duration_days": round(duration_days, 1),
            "base_trades": m_base["total_trades"],
            "base_win_rate": m_base["win_rate_pct"],
            "base_pf": m_base["profit_factor"],
            "base_net_r": m_base["net_r"],
            "base_net_usd": m_base["net_cash_usd"],
            "base_max_dd_usd": m_base["max_drawdown_usd"],
            "s1_trades": m_s1["total_trades"],
            "s1_win_rate": m_s1["win_rate_pct"],
            "s1_pf": m_s1["profit_factor"],
            "s1_net_r": m_s1["net_r"],
            "s1_net_usd": m_s1["net_cash_usd"],
            "s1_max_dd_usd": m_s1["max_drawdown_usd"],
            "filtered_total": total_filtered,
            "losses_saved": losses_saved,
            "wins_missed": wins_missed,
            "filter_precision_pct": round(filter_precision, 1),
        })

    mt5.shutdown()

    # Portfolio level comparison
    port_base = calculate_metrics(all_baseline_trades)
    port_s1 = calculate_metrics(all_s1_trades)

    total_days = max(1.0, (latest_date - earliest_date).total_seconds() / 86400.0) if earliest_date and latest_date else 1.0
    port_total_filtered = len(all_filtered_trades)
    port_losses_saved = sum(1 for f in all_filtered_trades if f.hypothetical_pnl_r < 0)
    port_wins_missed = sum(1 for f in all_filtered_trades if f.hypothetical_pnl_r > 0)
    port_filter_precision = (port_losses_saved / port_total_filtered * 100.0) if port_total_filtered > 0 else 0.0

    print("\n" + "=" * 80)
    print("   RESUMEN INSTITUCIONAL GLOBAL DEL PORTAFOLIO COMPLETO")
    print("=" * 80)
    print(f"   Periodo Total Analizado:  {earliest_date.strftime('%Y-%m-%d') if earliest_date else ''} a {latest_date.strftime('%Y-%m-%d') if latest_date else ''} ({total_days:.1f} dias continuos)")
    print("-" * 80)
    print(f"   METRICA                   | BASELINE (Sin Filtro)   | SYSTEM 1 ({args.provider.upper()})        | DELTA / IMPACTO")
    print("-" * 80)
    print(f"   Total de Trades           | {port_base['total_trades']:<23} | {port_s1['total_trades']:<23} | {port_s1['total_trades'] - port_base['total_trades']:+d} trades ({port_total_filtered} filtrados)")
    print(f"   Operaciones Ganadoras     | {port_base['wins']:<23} | {port_s1['wins']:<23} | {port_s1['wins'] - port_base['wins']:+d} trades")
    print(f"   Operaciones Perdedoras    | {port_base['losses']:<23} | {port_s1['losses']:<23} | {port_s1['losses'] - port_base['losses']:+d} (Perdidas evitadas: {port_losses_saved})")
    print(f"   Win Rate                  | {port_base['win_rate_pct']:.1f}%                  | {port_s1['win_rate_pct']:.1f}%                  | {port_s1['win_rate_pct'] - port_base['win_rate_pct']:+.1f}%")
    print(f"   Profit Factor             | {port_base['profit_factor']:.2f}                   | {port_s1['profit_factor']:.2f}                   | {port_s1['profit_factor'] - port_base['profit_factor']:+.2f}")
    print(f"   Retorno Neto (R)          | {port_base['net_r']:+.2f}R                 | {port_s1['net_r']:+.2f}R                 | {port_s1['net_r'] - port_base['net_r']:+.2f}R")
    print(f"   Retorno Neto (USD)        | ${port_base['net_cash_usd']:+.2f} USD         | ${port_s1['net_cash_usd']:+.2f} USD         | ${port_s1['net_cash_usd'] - port_base['net_cash_usd']:+.2f} USD")
    print(f"   Esperanza E[R] / trade    | {port_base['expectancy_r']:+.2f}R                  | {port_s1['expectancy_r']:+.2f}R                  | {port_s1['expectancy_r'] - port_base['expectancy_r']:+.2f}R")
    print(f"   Drawdown Maximo (USD)     | ${port_base['max_drawdown_usd']:.2f} USD ({port_base['max_drawdown_pct']:.1f}%) | ${port_s1['max_drawdown_usd']:.2f} USD ({port_s1['max_drawdown_pct']:.1f}%) | ${port_s1['max_drawdown_usd'] - port_base['max_drawdown_usd']:+.2f} USD")
    print(f"   Drawdown Maximo (R)       | {port_base['max_drawdown_r']:.2f}R                 | {port_s1['max_drawdown_r']:.2f}R                 | {port_s1['max_drawdown_r'] - port_base['max_drawdown_r']:+.2f}R")
    print("-" * 80)
    print(f"   Eficacia Filtro System 1: {port_losses_saved} perdidas bloqueadas de {port_total_filtered} intentos | Precision: {port_filter_precision:.1f}%")
    print("=" * 80)

    # Export CSV files
    exp_dir = Path("data/experiments")
    exp_dir.mkdir(parents=True, exist_ok=True)

    if all_baseline_trades:
        pd.DataFrame([t.__dict__ for t in all_baseline_trades]).to_csv(exp_dir / "backtest_system1_baseline_trades.csv", index=False)
    if all_s1_trades:
        pd.DataFrame([t.__dict__ for t in all_s1_trades]).to_csv(exp_dir / "backtest_system1_active_trades.csv", index=False)
    if all_filtered_trades:
        pd.DataFrame([f.__dict__ for f in all_filtered_trades]).to_csv(exp_dir / "backtest_system1_filtered_trades.csv", index=False)
    if comparison_rows:
        pd.DataFrame(comparison_rows).to_csv(exp_dir / "backtest_system1_comparison.csv", index=False)

    print(f"\n[+] Resultados detallados exportados exitosamente a '{exp_dir}/'")


if __name__ == "__main__":
    main()
