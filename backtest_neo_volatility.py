"""Quantitative Institutional Backtest: Baseline Strategies vs. Neo (System 1 PyTorch Engine).

Compares the baseline quantitative strategies (Institutional Trend Pullback & Volatility Expansion
for continuous indices, and Hawkes Jump-Diffusion Spike Sniper for Boom/Crash) against the active
System 1 "Neo" Decision Engine (NanoJev PyTorch Neural Evaluator) on real MetaTrader 5 Deriv data.

Generates comprehensive comparative institutional metrics:
- Total Trades, Win Rate (%), Profit Factor
- Net R & Net USD PnL
- Max Drawdown (R and USD)
- Filter Precision: Losses Avoided (False positives blocked) vs. Wins Missed
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.constants import Direction
from strategy.synthetic_volatility import SyntheticVolatilityStrategy, SyntheticVolatilitySignal
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal
from strategy.synthetic_system_one import SyntheticSystemOneEvaluator, SyntheticEvaluationResult
from filters.system_one.hub import SystemOneHub

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("NeoBacktest")


@dataclass
class NeoBacktestTrade:
    symbol: str
    category: str
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
    strategy_model: str
    confluences: str
    neo_approved: bool = True
    evidence_score: int = 4
    confidence: float = 0.85
    tier: str = "TIER_A_ALPHA"
    risk_multiplier: float = 1.0


@dataclass
class NeoFilteredTrade:
    time: str
    symbol: str
    category: str
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
    symbol: str, count_m5: int = 15000, start_pos: int = 0
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetch M5, M15, and H1 bars directly from Deriv MT5."""
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


def simulate_volatility_path(
    df_m5: pd.DataFrame,
    start_idx: int,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    partial_tp: float,
    partial_rr: float,
    target_rr: float,
    max_bars: int = 24,
) -> Tuple[str, float]:
    """Forward simulate a volatility trade path."""
    curr_sl = sl
    initial_risk = abs(entry - sl)
    has_partial = False
    bars_held = 0

    for j in range(start_idx, min(start_idx + max_bars + 1, len(df_m5))):
        bar = df_m5.iloc[j]
        c_open = float(bar["open"])
        c_high = float(bar["high"])
        c_low = float(bar["low"])
        c_close = float(bar["close"])
        bars_held += 1

        hit_sl = (direction == "BUY" and c_low <= curr_sl) or (direction == "SELL" and c_high >= curr_sl)
        hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

        if not has_partial:
            hit_partial = (direction == "BUY" and c_high >= partial_tp) or (direction == "SELL" and c_low <= partial_tp)
            if hit_partial:
                has_partial = True
                be_level = entry + (initial_risk * 0.15) if direction == "BUY" else entry - (initial_risk * 0.15)
                curr_sl = be_level

        peak_r = (c_high - entry) / initial_risk if direction == "BUY" else (entry - c_low) / initial_risk
        if peak_r >= 1.5:
            trail_sl = c_high - (initial_risk * 0.70) if direction == "BUY" else c_low + (initial_risk * 0.70)
            if direction == "BUY" and trail_sl > curr_sl:
                curr_sl = trail_sl
            elif direction == "SELL" and trail_sl < curr_sl:
                curr_sl = trail_sl

        if hit_tp:
            pnl_r = target_rr if not has_partial else (target_rr * 0.5 + partial_rr * 0.5)
            return "WIN_TP", round(pnl_r, 2)

        if hit_sl:
            sl_diff = (curr_sl - entry) if direction == "BUY" else (entry - curr_sl)
            pnl_r = round(sl_diff / initial_risk, 2)
            if has_partial:
                pnl_r = partial_rr * 0.5 + pnl_r * 0.5
            return ("EXIT_BE_LOCK" if pnl_r >= 0 else "LOSS_SL"), pnl_r

        if bars_held >= max_bars:
            diff = (c_close - entry) if direction == "BUY" else (entry - c_close)
            pnl_r = round(diff / initial_risk, 2)
            return "EXIT_TIME", pnl_r

    return "EXIT_TIME", 0.0


def simulate_spike_path(
    df_m5: pd.DataFrame,
    start_idx: int,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    target_rr: float,
    max_bars: int = 12,
) -> Tuple[str, float]:
    """Forward simulate a spike sniper trade path."""
    curr_sl = sl
    initial_risk = abs(entry - sl)
    bars_held = 0

    for j in range(start_idx, min(start_idx + max_bars + 1, len(df_m5))):
        bar = df_m5.iloc[j]
        c_high = float(bar["high"])
        c_low = float(bar["low"])
        c_close = float(bar["close"])
        bars_held += 1

        is_spike = bool(bar.get("is_boom_spike", False)) if direction == "BUY" else bool(bar.get("is_crash_spike", False))
        hit_sl = (direction == "BUY" and c_low <= curr_sl) or (direction == "SELL" and c_high >= curr_sl)
        hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp) or is_spike

        if hit_tp:
            return "WIN_SPIKE" if is_spike else "WIN_TP", round(target_rr, 2)
        if hit_sl:
            return "LOSS_SL", -1.0
        if bars_held >= max_bars:
            diff = (c_close - entry) if direction == "BUY" else (entry - c_close)
            return "EXIT_TIME", round(diff / initial_risk, 2)

    return "EXIT_TIME", 0.0


def run_symbol_simulation(
    symbol: str,
    category: str,
    df_m5: pd.DataFrame,
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    vol_strat: SyntheticVolatilityStrategy,
    spike_strat: SpikeSniperStrategy,
    s1_evaluator: Optional[SyntheticSystemOneEvaluator] = None,
    risk_usd: float = 75.0,
    point: float = 0.01,
) -> Tuple[List[NeoBacktestTrade], List[NeoFilteredTrade]]:
    """Simulate candle-by-candle with optional Neo/System-1 gating."""
    trades: List[NeoBacktestTrade] = []
    filtered: List[NeoFilteredTrade] = []
    active_trade: Optional[Dict[str, Any]] = None
    bars_held = 0

    df_m5_calc = vol_strat.calculate_indicators(df_m5) if category == "volatility" else spike_strat.calculate_indicators(df_m5)
    df_m15_calc = vol_strat.calculate_indicators(df_m15) if (df_m15 is not None and category == "volatility") else (spike_strat.calculate_indicators(df_m15) if df_m15 is not None else None)
    df_h1_calc = vol_strat.calculate_indicators(df_h1) if (df_h1 is not None and category == "volatility") else (spike_strat.calculate_indicators(df_h1) if df_h1 is not None else None)

    h1_times = df_h1_calc["time"].values if df_h1_calc is not None else None
    m15_times = df_m15_calc["time"].values if df_m15_calc is not None else None

    for i in range(50, len(df_m5_calc)):
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
            target_rr = active_trade["target_rr"]
            partial_tp = active_trade["partial_tp"]
            partial_rr = active_trade["partial_rr"]
            has_partial = active_trade["has_partial"]

            hit_sl = (direction == "BUY" and c_low <= sl) or (direction == "SELL" and c_high >= sl)
            hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

            if category == "boom_crash":
                is_spike = bool(curr_bar.get("is_boom_spike", False)) if direction == "BUY" else bool(curr_bar.get("is_crash_spike", False))
                if is_spike:
                    hit_tp = True

            if hit_sl and hit_tp:
                if abs(c_open - sl) < abs(c_open - tp):
                    hit_tp = False
                else:
                    hit_sl = False

            if not has_partial and category == "volatility":
                hit_partial = (direction == "BUY" and c_high >= partial_tp) or (direction == "SELL" and c_low <= partial_tp)
                if hit_partial:
                    active_trade["has_partial"] = True
                    be_level = entry + (initial_risk * 0.15) if direction == "BUY" else entry - (initial_risk * 0.15)
                    active_trade["current_sl"] = be_level
                    sl = be_level

            peak_r = (c_high - entry) / initial_risk if direction == "BUY" else (entry - c_low) / initial_risk
            if peak_r >= 1.5:
                trail_sl = c_high - (initial_risk * 0.70) if direction == "BUY" else c_low + (initial_risk * 0.70)
                if direction == "BUY" and trail_sl > active_trade["current_sl"]:
                    active_trade["current_sl"] = trail_sl
                elif direction == "SELL" and trail_sl < active_trade["current_sl"]:
                    active_trade["current_sl"] = trail_sl

            max_holding = 24 if category == "volatility" else 12
            time_exit = bars_held >= max_holding
            risk_mult = active_trade.get("risk_multiplier", 1.0)
            trade_tier = active_trade.get("tier", "TIER_A_ALPHA")

            if hit_tp:
                pnl_r = target_rr if not has_partial else (target_rr * 0.5 + partial_rr * 0.5)
                trades.append(NeoBacktestTrade(
                    symbol=symbol, category=category, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=tp, sl_price=sl, tp_price=tp,
                    pnl_r=round(pnl_r, 2), pnl_cash=round(pnl_r * risk_usd * risk_mult, 2),
                    exit_reason="WIN_TP", strategy_model=active_trade["model"],
                    confluences=active_trade["confluences"],
                    neo_approved=active_trade.get("neo_approved", True),
                    evidence_score=active_trade.get("evidence_score", 4),
                    confidence=active_trade.get("confidence", 0.85),
                    tier=trade_tier,
                    risk_multiplier=risk_mult,
                ))
                active_trade = None
                continue

            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r = round(sl_diff / initial_risk, 2)
                if has_partial:
                    pnl_r = partial_rr * 0.5 + pnl_r * 0.5
                reason = "EXIT_BE_LOCK" if pnl_r >= 0 else "LOSS_SL"
                trades.append(NeoBacktestTrade(
                    symbol=symbol, category=category, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=sl, sl_price=sl, tp_price=tp,
                    pnl_r=round(pnl_r, 2), pnl_cash=round(pnl_r * risk_usd * risk_mult, 2),
                    exit_reason=reason, strategy_model=active_trade["model"],
                    confluences=active_trade["confluences"],
                    neo_approved=active_trade.get("neo_approved", True),
                    evidence_score=active_trade.get("evidence_score", 4),
                    confidence=active_trade.get("confidence", 0.85),
                    tier=trade_tier,
                    risk_multiplier=risk_mult,
                ))
                active_trade = None
                continue

            elif time_exit:
                diff = (c_close - entry) if direction == "BUY" else (entry - c_close)
                pnl_r = round(diff / initial_risk, 2)
                trades.append(NeoBacktestTrade(
                    symbol=symbol, category=category, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close, sl_price=sl, tp_price=tp,
                    pnl_r=round(pnl_r, 2), pnl_cash=round(pnl_r * risk_usd * risk_mult, 2),
                    exit_reason="EXIT_TIME", strategy_model=active_trade["model"],
                    confluences=active_trade["confluences"],
                    neo_approved=active_trade.get("neo_approved", True),
                    evidence_score=active_trade.get("evidence_score", 4),
                    confidence=active_trade.get("confidence", 0.85),
                    tier=trade_tier,
                    risk_multiplier=risk_mult,
                ))
                active_trade = None
                continue
            continue

        # 2. Look for Candidate
        if h1_times is not None:
            idx_h1 = int(np.searchsorted(h1_times, np.datetime64(bar_time), side="right"))
            h1_slice = df_h1_calc.iloc[max(0, idx_h1 - 80):idx_h1] if idx_h1 > 0 else None
        else:
            h1_slice = None

        if m15_times is not None:
            idx_m15 = int(np.searchsorted(m15_times, np.datetime64(bar_time), side="right"))
            m15_slice = df_m15_calc.iloc[max(0, idx_m15 - 80):idx_m15] if idx_m15 > 0 else None
        else:
            m15_slice = None

        m5_slice = df_m5_calc.iloc[max(0, i - 60):i + 1]

        if category == "volatility":
            sig = vol_strat.evaluate_volatility_setup(symbol, m5_slice, df_m15=m15_slice, df_h1=h1_slice, point=point)
        else:
            sig = spike_strat.detect_spike_setup(symbol, m5_slice, df_higher=m15_slice, df_h1=h1_slice, point=point)

        if sig is None:
            continue

        sig_direction = sig.direction
        sig_entry = sig.entry_price
        sig_sl = sig.sl_price
        sig_tp = sig.tp_price
        sig_rr = getattr(sig, "risk_reward", getattr(sig, "target_rr", 2.0))
        sig_partial_tp = getattr(sig, "partial_tp_price", sig_entry)
        sig_confluences = getattr(sig, "confluences", ())
        sig_model = getattr(sig, "setup_model", getattr(sig, "setup_name", "UNKNOWN"))

        # 3. NEO / SYSTEM-1 DECISION GATE
        neo_approved = True
        evidence_score = 4
        confidence = 0.85
        if s1_evaluator is not None:
            s1_res = s1_evaluator.evaluate_setup(
                symbol=symbol,
                direction=sig_direction,
                entry_price=sig_entry,
                sl_price=sig_sl,
                tp_price=sig_tp,
                target_rr=sig_rr,
                df_m5=m5_slice,
                df_m15=m15_slice,
                df_h1=h1_slice,
                confluences=sig_confluences,
                point=point,
            )
            neo_approved = s1_res.approved
            evidence_score = s1_res.evidence_score
            confidence = s1_res.confidence
            tier = s1_res.tier
            risk_multiplier = s1_res.risk_multiplier

            if s1_res.target_rr_cap is not None:
                sig_rr = min(sig_rr, s1_res.target_rr_cap)
                risk_pts_temp = abs(sig_entry - sig_sl)
                sig_tp = sig_entry + (risk_pts_temp * sig_rr) if sig_direction == Direction.BUY else sig_entry - (risk_pts_temp * sig_rr)

            if not neo_approved:
                # Track hypothetical outcome of rejected trade
                if category == "volatility":
                    p_rr = round(abs(sig_partial_tp - sig_entry) / max(abs(sig_entry - sig_sl), 0.001), 2)
                    hyp_reason, hyp_pnl_r = simulate_volatility_path(
                        df_m5=df_m5_calc, start_idx=i + 1, direction=sig_direction.value,
                        entry=sig_entry, sl=sig_sl, tp=sig_tp, partial_tp=sig_partial_tp,
                        partial_rr=p_rr, target_rr=sig_rr, max_bars=24,
                    )
                else:
                    hyp_reason, hyp_pnl_r = simulate_spike_path(
                        df_m5=df_m5_calc, start_idx=i + 1, direction=sig_direction.value,
                        entry=sig_entry, sl=sig_sl, tp=sig_tp, target_rr=sig_rr, max_bars=12,
                    )

                filtered.append(NeoFilteredTrade(
                    time=bar_time_str, symbol=symbol, category=category, direction=sig_direction.value,
                    entry_price=sig_entry, sl_price=sig_sl, tp_price=sig_tp,
                    evidence_score=evidence_score, confidence=confidence,
                    reasons=s1_res.rejection_reasons, confluences=", ".join(sig_confluences),
                    hypothetical_outcome=hyp_reason, hypothetical_pnl_r=hyp_pnl_r,
                ))
                continue

        risk_pts = abs(sig_entry - sig_sl)
        part_rr = round(abs(sig_partial_tp - sig_entry) / max(risk_pts, 0.001), 2) if category == "volatility" else 1.30
        active_trade = {
            "direction": sig_direction.value,
            "entry": sig_entry,
            "initial_sl": sig_sl,
            "current_sl": sig_sl,
            "tp": sig_tp,
            "partial_tp": sig_partial_tp,
            "initial_risk": risk_pts,
            "target_rr": sig_rr,
            "partial_rr": part_rr,
            "has_partial": False,
            "entry_time": bar_time_str,
            "model": sig_model,
            "confluences": ", ".join(sig_confluences),
            "neo_approved": neo_approved,
            "evidence_score": evidence_score,
            "confidence": confidence,
            "tier": tier if s1_evaluator is not None else "TIER_A_ALPHA",
            "risk_multiplier": risk_multiplier if s1_evaluator is not None else 1.0,
        }
        bars_held = 0

    return trades, filtered


def calculate_metrics(trades: List[NeoBacktestTrade], initial_balance: float = 10000.0) -> Dict[str, Any]:
    """Calculate institutional performance metrics."""
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "profit_factor": 0.0, "net_r": 0.0, "net_cash_usd": 0.0,
            "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
        }

    wins = [t for t in trades if t.pnl_r > 0]
    losses = [t for t in trades if t.pnl_r <= 0]

    win_count = len(wins)
    total_trades = len(trades)
    win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

    gross_profit = sum(t.pnl_cash for t in wins)
    gross_loss = abs(sum(t.pnl_cash for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    net_r = round(sum(t.pnl_r for t in trades), 2)
    net_cash = round(sum(t.pnl_cash for t in trades), 2)

    # Peak-to-valley drawdown
    balance = initial_balance
    equity_curve = [balance]
    peak = balance
    max_dd_usd = 0.0

    for t in trades:
        balance += t.pnl_cash
        equity_curve.append(balance)
        if balance > peak:
            peak = balance
        dd = peak - balance
        if dd > max_dd_usd:
            max_dd_usd = dd

    max_dd_pct = (max_dd_usd / peak * 100.0) if peak > 0 else 0.0

    return {
        "total_trades": total_trades,
        "wins": win_count,
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "net_r": net_r,
        "net_cash_usd": net_cash,
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Backtest: Baseline vs Neo (System 1)")
    parser.add_argument("--bars", type=int, default=10000, help="M5 bars per symbol (default: 10000)")
    parser.add_argument("--symbols", type=str, default="", help="Comma separated symbols")
    args = parser.parse_args()

    print("=" * 80)
    print("   QUANTUM-DERIV: VALIDACION COMPARATIVA [BASELINE vs. NEO (SYSTEM 1)]")
    print("   ARQUITECTURA INSTITUCIONAL: VOLATILITY PULLBACKS & EXPANSION + BOOM/CRASH")
    print("=" * 80)

    if not MT5_AVAILABLE:
        print("[ERROR] MetaTrader5 no disponible.")
        return
    if not mt5.initialize():
        print("[ERROR] MT5 connection failed.")
        return

    # Configuration across portfolio
    symbols_config = [
        {"symbol": "Volatility 100 Index", "category": "volatility", "point": 0.01},
        {"symbol": "Volatility 75 Index", "category": "volatility", "point": 0.01},
        {"symbol": "Volatility 10 Index", "category": "volatility", "point": 0.001},
        {"symbol": "Crash 1000 Index", "category": "boom_crash", "point": 0.0001},
        {"symbol": "Boom 1000 Index", "category": "boom_crash", "point": 0.0001},
        {"symbol": "Crash 500 Index", "category": "boom_crash", "point": 0.001},
        {"symbol": "Boom 500 Index", "category": "boom_crash", "point": 0.001},
    ]

    if args.symbols:
        sel = [s.strip().lower() for s in args.symbols.split(",")]
        symbols_config = [s for s in symbols_config if any(x in s["symbol"].lower() for x in sel)]

    vol_strat = SyntheticVolatilityStrategy(
        ema_fast=20, ema_medium=50, ema_slow=200, atr_period=14, sl_buffer_atr=0.25,
        target_rr=2.20, partial_rr=1.35, min_rr=1.40,
        enable_trend_pullback=True, enable_squeeze_expansion=True, enable_pullback_sweep=True,
    )
    spike_strat = SpikeSniperStrategy(
        max_holding_bars=12, min_rr=1.40, target_rr=2.0, sl_buffer_atr=1.0,
        crash_rsi_overbought=52.0, boom_rsi_oversold=48.0,
        touch_atr=1.00, close_near_atr=1.35, min_wick_ratio=0.15,
    )

    # Initialize Neo / System 1 Hub and Evaluator (Active Gate)
    s1_hub = SystemOneHub(enabled=True, provider_name="nanojev", shadow_mode=False, device="cpu")
    s1_evaluator = SyntheticSystemOneEvaluator(hub=s1_hub, shadow_mode=False, min_evidence_score=3, min_confidence=0.60)

    all_base_trades = []
    all_neo_trades = []
    all_filtered = []
    comparison_table = []

    print(f"\n[+] Descargando {args.bars} velas M5 por simbolo y ejecutando simulaciones comparativas...\n")

    for sc in symbols_config:
        sym = sc["symbol"]
        cat = sc["category"]
        pt = sc["point"]

        df_m5, df_m15, df_h1 = fetch_mt5_data(sym, count_m5=args.bars)
        if df_m5 is None or len(df_m5) < 100:
            print(f"[-] {sym}: Datos insuficientes. Omitiendo.")
            continue

        start_dt = df_m5["time"].iloc[0].strftime("%Y-%m-%d")
        end_dt = df_m5["time"].iloc[-1].strftime("%Y-%m-%d")

        # 1. BASELINE (Sin Neo)
        base_t, _ = run_symbol_simulation(
            sym, cat, df_m5, df_m15, df_h1, vol_strat, spike_strat,
            s1_evaluator=None, risk_usd=75.0, point=pt
        )

        # 2. NEO SYSTEM 1 (Con Neo)
        neo_t, filt = run_symbol_simulation(
            sym, cat, df_m5, df_m15, df_h1, vol_strat, spike_strat,
            s1_evaluator=s1_evaluator, risk_usd=75.0, point=pt
        )

        all_base_trades.extend(base_t)
        all_neo_trades.extend(neo_t)
        all_filtered.extend(filt)

        m_b = calculate_metrics(base_t)
        m_n = calculate_metrics(neo_t)

        tot_filt = len(filt)
        losses_saved = sum(1 for f in filt if f.hypothetical_pnl_r < 0)
        wins_missed = sum(1 for f in filt if f.hypothetical_pnl_r > 0)
        prec = (losses_saved / tot_filt * 100.0) if tot_filt > 0 else 0.0

        print(f"--> {sym.upper()} ({len(df_m5)} barras M5 | {start_dt} a {end_dt})")
        print(f"    [BASELINE]: Trades: {m_b['total_trades']:3d} | WR: {m_b['win_rate_pct']:4.1f}% | PF: {m_b['profit_factor']:4.2f} | Net R: {m_b['net_r']:+6.2f}R | Net USD: ${m_b['net_cash_usd']:+8.2f} | MaxDD: ${m_b['max_drawdown_usd']:.2f}")
        print(f"    [CON NEO]:  Trades: {m_n['total_trades']:3d} | WR: {m_n['win_rate_pct']:4.1f}% | PF: {m_n['profit_factor']:4.2f} | Net R: {m_n['net_r']:+6.2f}R | Net USD: ${m_n['net_cash_usd']:+8.2f} | MaxDD: ${m_n['max_drawdown_usd']:.2f}")
        print(f"    [FILTRADO]: Bloqueados: {tot_filt:2d} | Perdidas Evitadas: {losses_saved:2d} | Ganadas Rechazadas: {wins_missed:2d} | Precision: {prec:.1f}%\n")

        comparison_table.append({
            "symbol": sym, "category": cat,
            "base_trades": m_b["total_trades"], "base_wr": m_b["win_rate_pct"], "base_pf": m_b["profit_factor"], "base_net_r": m_b["net_r"], "base_net_usd": m_b["net_cash_usd"],
            "neo_trades": m_n["total_trades"], "neo_wr": m_n["win_rate_pct"], "neo_pf": m_n["profit_factor"], "neo_net_r": m_n["net_r"], "neo_net_usd": m_n["net_cash_usd"],
            "filtered_total": tot_filt, "losses_saved": losses_saved, "wins_missed": wins_missed, "precision_pct": round(prec, 1),
        })

    # Portfolio global
    port_b = calculate_metrics(all_base_trades)
    port_n = calculate_metrics(all_neo_trades)

    tot_filt_all = len(all_filtered)
    losses_saved_all = sum(1 for f in all_filtered if f.hypothetical_pnl_r < 0)
    wins_missed_all = sum(1 for f in all_filtered if f.hypothetical_pnl_r > 0)
    prec_all = (losses_saved_all / tot_filt_all * 100.0) if tot_filt_all > 0 else 0.0

    print("=" * 80)
    print("   RESUMEN INSTITUCIONAL GLOBAL: BASELINE vs. NEO (PORTAFOLIO COMPLETO)")
    print("=" * 80)
    print(f"   METRICA                   | BASELINE (Sin Neo)      | CON NEO (System 1 Activo) | IMPACTO / DELTA")
    print("-" * 80)
    print(f"   Total de Operaciones      | {port_b['total_trades']:<23} | {port_n['total_trades']:<25} | {port_n['total_trades'] - port_b['total_trades']:+d} ({tot_filt_all} filtradas)")
    print(f"   Operaciones Ganadoras     | {port_b['wins']:<23} | {port_n['wins']:<25} | {port_n['wins'] - port_b['wins']:+d}")
    print(f"   Operaciones Perdedoras    | {port_b['losses']:<23} | {port_n['losses']:<25} | {port_n['losses'] - port_b['losses']:+d} (Perdidas evitadas: {losses_saved_all})")
    print(f"   Win Rate (%)              | {port_b['win_rate_pct']:.1f}%                  | {port_n['win_rate_pct']:.1f}%                    | {port_n['win_rate_pct'] - port_b['win_rate_pct']:+.1f}%")
    print(f"   Profit Factor             | {port_b['profit_factor']:.2f}                   | {port_n['profit_factor']:.2f}                     | {port_n['profit_factor'] - port_b['profit_factor']:+.2f}")
    print(f"   Retorno Neto (R)          | {port_b['net_r']:+.2f}R                 | {port_n['net_r']:+.2f}R                   | {port_n['net_r'] - port_b['net_r']:+.2f}R")
    print(f"   Retorno Neto (USD)        | ${port_b['net_cash_usd']:+.2f} USD         | ${port_n['net_cash_usd']:+.2f} USD           | ${port_n['net_cash_usd'] - port_b['net_cash_usd']:+.2f} USD")
    print(f"   Drawdown Maximo (USD)     | ${port_b['max_drawdown_usd']:.2f} USD ({port_b['max_drawdown_pct']:.1f}%) | ${port_n['max_drawdown_usd']:.2f} USD ({port_n['max_drawdown_pct']:.1f}%)   | ${port_n['max_drawdown_usd'] - port_b['max_drawdown_usd']:+.2f} USD")
    print("-" * 80)
    print(f"   Eficacia de Neo (System 1): {losses_saved_all} perdidas bloqueadas de {tot_filt_all} intentos | Precision: {prec_all:.1f}%")
    print("=" * 80)

    # Save summary report
    Path("data").mkdir(exist_ok=True)
    summary = {
        "portfolio_baseline": port_b,
        "portfolio_neo": port_n,
        "efficacy": {
            "total_filtered": tot_filt_all,
            "losses_saved": losses_saved_all,
            "wins_missed": wins_missed_all,
            "precision_pct": prec_all,
        },
        "symbols": comparison_table,
    }
    with open("data/neo_backtest_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print("\n[OK] Reporte completo exportado a data/neo_backtest_summary.json")


if __name__ == "__main__":
    main()
