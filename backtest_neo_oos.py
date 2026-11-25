"""Quantitative Institutional Out-Of-Sample (OOS) Validation: Neo Híbrido.

Executes a strict Walk-Forward / Out-Of-Sample validation on 10,000 M5 bars
strictly prior to the In-Sample period (bars 10,000 to 20,000, ~July 28 to Sept 1, 2026).

Compares:
1. Baseline Cuantitativo Puro (OOS)
2. Neo Híbrido Definitivo (OOS)
3. Walk-Forward Stability (IS vs OOS Degradation & Efficiency)
4. Surgical Loss Autopsy & Tier Allocation (Tier A vs Tier B vs Tier C)
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
from strategy.synthetic_system_one import SyntheticSystemOneEvaluator, SyntheticEvaluationResult, ExecutionTier
from filters.system_one.hub import SystemOneHub

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("NeoOOS")


@dataclass
class OOSTrade:
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
    tier: str
    risk_multiplier: float
    bars_held: int
    neo_approved: bool = True
    evidence_score: int = 4
    confidence: float = 0.85


@dataclass
class OOSFilteredTrade:
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
    tier: str
    hypothetical_outcome: str = "UNKNOWN"
    hypothetical_pnl_r: float = 0.0


def fetch_mt5_data(
    symbol: str, count_m5: int = 10000, start_pos: int = 10000
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetch M5, M15, and H1 bars directly from Deriv MT5 for OOS range."""
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
    """Forward simulate a single Volatility trade."""
    initial_risk = abs(entry - sl)
    if initial_risk <= 0:
        return "ERROR", 0.0

    current_sl = sl
    has_partial = False
    pnl_r = 0.0

    for step in range(1, max_bars + 1):
        idx = start_idx + step
        if idx >= len(df_m5):
            diff = (df_m5.iloc[-1]["close"] - entry) if direction == "BUY" else (entry - df_m5.iloc[-1]["close"])
            return "EXIT_TIME", round(diff / initial_risk, 2)

        bar = df_m5.iloc[idx]
        b_high = float(bar["high"])
        b_low = float(bar["low"])

        hit_sl = (direction == "BUY" and b_low <= current_sl) or (direction == "SELL" and b_high >= current_sl)
        hit_tp = (direction == "BUY" and b_high >= tp) or (direction == "SELL" and b_low <= tp)

        if hit_sl and hit_tp:
            return ("WIN_TP", target_rr) if abs(float(bar["open"]) - tp) < abs(float(bar["open"]) - current_sl) else ("LOSS_SL", -1.0)

        if not has_partial:
            hit_partial = (direction == "BUY" and b_high >= partial_tp) or (direction == "SELL" and b_low <= partial_tp)
            if hit_partial:
                has_partial = True
                be_level = entry + (initial_risk * 0.15) if direction == "BUY" else entry - (initial_risk * 0.15)
                current_sl = be_level

        peak_r = (b_high - entry) / initial_risk if direction == "BUY" else (entry - b_low) / initial_risk
        if peak_r >= 1.5:
            trail_sl = b_high - (initial_risk * 0.70) if direction == "BUY" else b_low + (initial_risk * 0.70)
            if direction == "BUY" and trail_sl > current_sl:
                current_sl = trail_sl
            elif direction == "SELL" and trail_sl < current_sl:
                current_sl = trail_sl

        if hit_tp:
            pnl_r = target_rr if not has_partial else (target_rr * 0.5 + partial_rr * 0.5)
            return "WIN_TP", round(pnl_r, 2)

        if hit_sl:
            sl_diff = (current_sl - entry) if direction == "BUY" else (entry - current_sl)
            pnl_r = sl_diff / initial_risk
            if has_partial:
                pnl_r = partial_rr * 0.5 + pnl_r * 0.5
            return ("EXIT_BE_LOCK", round(pnl_r, 2)) if pnl_r >= 0 else ("LOSS_SL", round(pnl_r, 2))

    last_bar = df_m5.iloc[min(start_idx + max_bars, len(df_m5) - 1)]
    diff = (float(last_bar["close"]) - entry) if direction == "BUY" else (entry - float(last_bar["close"]))
    return "EXIT_TIME", round(diff / initial_risk, 2)


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
    """Forward simulate a single Boom or Crash trade."""
    initial_risk = abs(entry - sl)
    if initial_risk <= 0:
        return "ERROR", 0.0

    spike_col = "is_boom_spike" if direction == "BUY" else "is_crash_spike"

    for step in range(1, max_bars + 1):
        idx = start_idx + step
        if idx >= len(df_m5):
            diff = (df_m5.iloc[-1]["close"] - entry) if direction == "BUY" else (entry - df_m5.iloc[-1]["close"])
            return "EXIT_TIME", round(diff / initial_risk, 2)

        bar = df_m5.iloc[idx]
        b_high = float(bar["high"])
        b_low = float(bar["low"])

        is_spike = bool(bar.get(spike_col, False)) if spike_col in bar else False
        hit_sl = (direction == "BUY" and b_low <= sl) or (direction == "SELL" and b_high >= sl)
        hit_tp = (direction == "BUY" and b_high >= tp) or (direction == "SELL" and b_low <= tp)

        if is_spike or hit_tp:
            return "WIN_TP", target_rr

        if hit_sl:
            return "LOSS_SL", -1.0

        if step >= 6:
            c_close = float(bar["close"])
            drift_loss = (entry - c_close) if direction == "BUY" else (c_close - entry)
            if drift_loss >= (initial_risk * 0.40):
                return "LOSS_SL", round(-drift_loss / initial_risk, 2)

    last_bar = df_m5.iloc[min(start_idx + max_bars, len(df_m5) - 1)]
    diff = (float(last_bar["close"]) - entry) if direction == "BUY" else (entry - float(last_bar["close"]))
    return "EXIT_TIME", round(diff / initial_risk, 2)


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
) -> Tuple[List[OOSTrade], List[OOSFilteredTrade]]:
    """Simulate candle-by-candle with Neo 3-Tier Multi-Dimensional Arbitrator."""
    trades: List[OOSTrade] = []
    filtered: List[OOSFilteredTrade] = []
    active_trades: List[Dict[str, Any]] = []

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

        # 1. Manage Active Trades (Multi-Layer / Scale-In capable)
        surviving_trades: List[Dict[str, Any]] = []
        for t in active_trades:
            t["bars_held"] += 1
            direction = t["direction"]
            entry = t["entry"]
            sl = t["current_sl"]
            tp = t["tp"]
            initial_risk = t["initial_risk"]
            target_rr = t["target_rr"]
            partial_tp = t["partial_tp"]
            partial_rr = t["partial_rr"]
            has_partial = t["has_partial"]
            trade_tier = t.get("tier", "TIER_A_ALPHA")
            risk_mult = t.get("risk_multiplier", 1.0)
            bars_held = t["bars_held"]

            hit_sl = (direction == "BUY" and c_low <= sl) or (direction == "SELL" and c_high >= sl)
            hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

            if category == "boom_crash":
                is_spike = bool(curr_bar.get("is_boom_spike", False)) if direction == "BUY" else bool(curr_bar.get("is_crash_spike", False))
                if is_spike:
                    hit_tp = True

                # Dynamic Hawkes Hazard Rate Decay:
                if bars_held >= 6 and not hit_tp:
                    drift_loss = (entry - c_close) if direction == "BUY" else (c_close - entry)
                    if drift_loss >= (initial_risk * 0.40):
                        hit_sl = True
                        sl = c_close

            if hit_sl and hit_tp:
                if abs(c_open - sl) < abs(c_open - tp):
                    hit_tp = False
                else:
                    hit_sl = False

            if not has_partial and category == "volatility":
                hit_partial = (direction == "BUY" and c_high >= partial_tp) or (direction == "SELL" and c_low <= partial_tp)
                if hit_partial:
                    t["has_partial"] = True
                    be_level = entry + (initial_risk * 0.15) if direction == "BUY" else entry - (initial_risk * 0.15)
                    t["current_sl"] = be_level
                    sl = be_level

            peak_r = (c_high - entry) / initial_risk if direction == "BUY" else (entry - c_low) / initial_risk
            if peak_r >= 1.5:
                trail_sl = c_high - (initial_risk * 0.70) if direction == "BUY" else c_low + (initial_risk * 0.70)
                if direction == "BUY" and trail_sl > t["current_sl"]:
                    t["current_sl"] = trail_sl
                elif direction == "SELL" and trail_sl < t["current_sl"]:
                    t["current_sl"] = trail_sl

            max_holding = 24 if category == "volatility" else 12
            time_exit = bars_held >= max_holding

            if hit_tp:
                pnl_r = target_rr if not has_partial else (target_rr * 0.5 + partial_rr * 0.5)
                trades.append(OOSTrade(
                    symbol=symbol, category=category, direction=direction,
                    entry_time=t["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=tp, sl_price=sl, tp_price=tp,
                    pnl_r=round(pnl_r, 2), pnl_cash=round(pnl_r * risk_usd * risk_mult, 2),
                    exit_reason="WIN_TP", strategy_model=t["model"],
                    confluences=t["confluences"],
                    tier=trade_tier, risk_multiplier=risk_mult, bars_held=bars_held,
                    neo_approved=t.get("neo_approved", True),
                    evidence_score=t.get("evidence_score", 4),
                    confidence=t.get("confidence", 0.85),
                ))
            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r = round(sl_diff / initial_risk, 2)
                if has_partial:
                    pnl_r = partial_rr * 0.5 + pnl_r * 0.5
                reason = "EXIT_BE_LOCK" if pnl_r >= 0 else "LOSS_SL"
                trades.append(OOSTrade(
                    symbol=symbol, category=category, direction=direction,
                    entry_time=t["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=sl, sl_price=sl, tp_price=tp,
                    pnl_r=round(pnl_r, 2), pnl_cash=round(pnl_r * risk_usd * risk_mult, 2),
                    exit_reason=reason, strategy_model=t["model"],
                    confluences=t["confluences"],
                    tier=trade_tier, risk_multiplier=risk_mult, bars_held=bars_held,
                    neo_approved=t.get("neo_approved", True),
                    evidence_score=t.get("evidence_score", 4),
                    confidence=t.get("confidence", 0.85),
                ))
            elif time_exit:
                diff = (c_close - entry) if direction == "BUY" else (entry - c_close)
                pnl_r = round(diff / initial_risk, 2)
                trades.append(OOSTrade(
                    symbol=symbol, category=category, direction=direction,
                    entry_time=t["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close, sl_price=sl, tp_price=tp,
                    pnl_r=round(pnl_r, 2), pnl_cash=round(pnl_r * risk_usd * risk_mult, 2),
                    exit_reason="EXIT_TIME", strategy_model=t["model"],
                    confluences=t["confluences"],
                    tier=trade_tier, risk_multiplier=risk_mult, bars_held=bars_held,
                    neo_approved=t.get("neo_approved", True),
                    evidence_score=t.get("evidence_score", 4),
                    confidence=t.get("confidence", 0.85),
                ))
            else:
                surviving_trades.append(t)

        active_trades = surviving_trades

        # Capacity check: maximum 2 concurrent positions (Base + Scale-In)
        if len(active_trades) >= 2:
            continue

        # Scale-In Eligibility Gate (Risk-Free Pyramid or Two-Stage Retest)
        is_scale_in = len(active_trades) == 1
        if is_scale_in:
            base_t = active_trades[0]
            b_dir = base_t["direction"]
            b_entry = base_t["entry"]
            b_sl = base_t["current_sl"]
            b_init_risk = base_t["initial_risk"]
            b_gain = (c_close - b_entry) if b_dir == "BUY" else (b_entry - c_close)
            sl_at_be = (b_dir == "BUY" and b_sl >= b_entry) or (b_dir == "SELL" and b_sl <= b_entry)
            is_risk_free = sl_at_be or (b_gain >= b_init_risk * 0.90)

            # Option A: Risk-Free Pyramiding on winning run
            can_scale = is_risk_free
            # Option B: Two-Stage Retest (volatility only, shallow pullback <= 0.65R)
            if not can_scale and category == "volatility" and b_gain < 0:
                dd_r = abs(b_gain) / b_init_risk
                if dd_r <= 0.65:
                    can_scale = True

            if not can_scale:
                continue

        # 2. Check for New Setup Trigger
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
        tier = "TIER_A_ALPHA"
        risk_multiplier = 1.0

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
                # Track hypothetical outcome of rejected trade (Tier C Veto)
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

                filtered.append(OOSFilteredTrade(
                    time=bar_time_str, symbol=symbol, category=category, direction=sig_direction.value,
                    entry_price=sig_entry, sl_price=sig_sl, tp_price=sig_tp,
                    evidence_score=evidence_score, confidence=confidence,
                    reasons=s1_res.rejection_reasons, confluences=", ".join(sig_confluences),
                    tier=tier, hypothetical_outcome=hyp_reason, hypothetical_pnl_r=hyp_pnl_r,
                ))
                continue

        if is_scale_in:
            risk_multiplier = min(risk_multiplier, 0.50)
            sig_model = f"{sig_model}-L2"

        risk_pts = abs(sig_entry - sig_sl)
        part_rr = round(abs(sig_partial_tp - sig_entry) / max(risk_pts, 0.001), 2) if category == "volatility" else 1.30
        active_trades.append({
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
            "tier": tier,
            "risk_multiplier": risk_multiplier,
            "bars_held": 0,
        })

    return trades, filtered


def calculate_metrics(trades: List[OOSTrade], initial_balance: float = 10000.0) -> Dict[str, Any]:
    """Calculate institutional performance metrics."""
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "profit_factor": 0.0, "net_r": 0.0, "net_cash_usd": 0.0,
            "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
            "avg_trade_usd": 0.0, "profit_r_sum": 0.0, "loss_r_sum": 0.0,
        }

    wins = [t for t in trades if t.pnl_cash > 0]
    losses = [t for t in trades if t.pnl_cash <= 0]
    win_count = len(wins)
    win_rate = (win_count / total_trades) * 100.0

    gross_profit = sum(t.pnl_cash for t in wins)
    gross_loss = abs(sum(t.pnl_cash for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    net_r = round(sum(t.pnl_r for t in trades), 2)
    net_cash = round(sum(t.pnl_cash for t in trades), 2)
    avg_trade = round(net_cash / total_trades, 2)

    # Peak-to-valley drawdown
    balance = initial_balance
    peak = balance
    max_dd_usd = 0.0

    for t in trades:
        balance += t.pnl_cash
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
        "avg_trade_usd": avg_trade,
    }


def analyze_losing_trades(trades: List[OOSTrade]) -> Dict[str, Any]:
    """Perform surgical autopsy on all losing trades in OOS."""
    losses = [t for t in trades if t.pnl_cash < 0]
    if not losses:
        return {"total_losses": 0, "categories": {}}

    autopsy = {
        "total_losses": len(losses),
        "total_loss_usd": round(sum(t.pnl_cash for t in losses), 2),
        "by_tier": {
            "TIER_A_ALPHA": sum(1 for t in losses if t.tier == "TIER_A_ALPHA"),
            "TIER_B_SURGICAL": sum(1 for t in losses if t.tier == "TIER_B_SURGICAL"),
        },
        "by_model": {},
        "by_symbol": {},
        "holding_duration": {
            "avg_bars": round(float(np.mean([t.bars_held for t in losses])), 1),
            "max_bars": max(t.bars_held for t in losses),
            "min_bars": min(t.bars_held for t in losses),
        },
    }

    for t in losses:
        autopsy["by_model"][t.strategy_model] = autopsy["by_model"].get(t.strategy_model, 0) + 1
        autopsy["by_symbol"][t.symbol] = autopsy["by_symbol"].get(t.symbol, 0) + 1

    return autopsy


def main():
    parser = argparse.ArgumentParser(description="Out-Of-Sample Validation: Neo Híbrido")
    parser.add_argument("--bars", type=int, default=10000, help="M5 bars per symbol (default: 10000)")
    parser.add_argument("--start-pos", type=int, default=10000, help="Start offset in history (default: 10000)")
    parser.add_argument("--output", type=str, default="data/neo_oos_summary.json", help="Output summary path")
    args = parser.parse_args()

    print("=" * 85, flush=True)
    print("   QUANTUM-DERIV: VALIDACION INSTITUCIONAL OUT-OF-SAMPLE (OOS) - NEO HIBRIDO", flush=True)
    print(f"   VENTANA EVALUADA: {args.bars} VELAS M5 (DESFASE HISTORICO: start_pos={args.start_pos})", flush=True)
    print("=" * 85, flush=True)

    if not MT5_AVAILABLE:
        print("[ERROR] MetaTrader5 no disponible.", flush=True)
        return
    if not mt5.initialize():
        print("[ERROR] MT5 connection failed.", flush=True)
        return

    # Configuration across the 6 production portfolio assets
    symbols_config = [
        {"symbol": "Crash 1000 Index", "category": "boom_crash", "point": 0.0001},
        {"symbol": "Boom 1000 Index", "category": "boom_crash", "point": 0.0001},
        {"symbol": "Crash 500 Index", "category": "boom_crash", "point": 0.001},
        {"symbol": "Boom 500 Index", "category": "boom_crash", "point": 0.001},
        {"symbol": "Volatility 75 Index", "category": "volatility", "point": 0.01},
        {"symbol": "Volatility 100 Index", "category": "volatility", "point": 0.01},
    ]

    vol_strat = SyntheticVolatilityStrategy(
        ema_fast=20, ema_medium=50, ema_slow=200, atr_period=14, sl_buffer_atr=0.70,
        target_rr=2.20, partial_rr=1.35, min_rr=1.40,
        enable_trend_pullback=True, enable_squeeze_expansion=True, enable_pullback_sweep=True,
        enable_breakout=True,
    )
    spike_strat = SpikeSniperStrategy(
        max_holding_bars=16, min_rr=1.40, target_rr=2.0, sl_buffer_atr=1.5,
        crash_rsi_overbought=52.0, boom_rsi_oversold=48.0,
        touch_atr=1.00, close_near_atr=1.35, min_wick_ratio=0.15,
    )

    s1_hub = SystemOneHub(enabled=True, provider_name="nanojev", shadow_mode=False, device="cpu")
    s1_evaluator = SyntheticSystemOneEvaluator(hub=s1_hub, shadow_mode=False, min_evidence_score=3, min_confidence=0.58)

    all_base_trades: List[OOSTrade] = []
    all_neo_trades: List[OOSTrade] = []
    all_filtered: List[OOSFilteredTrade] = []
    comparison_table = []

    print(f"\n[+] Descargando datos OOS y ejecutando simulaciones comparativas...\n", flush=True)

    for sc in symbols_config:
        sym = sc["symbol"]
        cat = sc["category"]
        pt = sc["point"]

        df_m5, df_m15, df_h1 = fetch_mt5_data(sym, count_m5=args.bars, start_pos=args.start_pos)
        if df_m5 is None or len(df_m5) < 100:
            print(f"[-] {sym}: Datos insuficientes en OOS. Omitiendo.", flush=True)
            continue

        start_dt = df_m5["time"].iloc[0].strftime("%Y-%m-%d %H:%M")
        end_dt = df_m5["time"].iloc[-1].strftime("%Y-%m-%d %H:%M")

        # 1. BASELINE PURO (OOS)
        base_t, _ = run_symbol_simulation(
            sym, cat, df_m5, df_m15, df_h1, vol_strat, spike_strat,
            s1_evaluator=None, risk_usd=75.0, point=pt
        )

        # 2. NEO HÍBRIDO (OOS)
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

        # Tier Breakdown for this symbol
        tier_a_t = [t for t in neo_t if t.tier == "TIER_A_ALPHA"]
        tier_b_t = [t for t in neo_t if t.tier == "TIER_B_SURGICAL"]
        m_ta = calculate_metrics(tier_a_t)
        m_tb = calculate_metrics(tier_b_t)

        print(f"--> {sym.upper()} [OOS: {start_dt} a {end_dt}]", flush=True)
        print(f"    [BASELINE OOS]: Trades: {m_b['total_trades']:3d} | WR: {m_b['win_rate_pct']:4.1f}% | PF: {m_b['profit_factor']:4.2f} | Net USD: ${m_b['net_cash_usd']:+8.2f} | MaxDD: ${m_b['max_drawdown_usd']:.2f}", flush=True)
        print(f"    [CON NEO OOS]:  Trades: {m_n['total_trades']:3d} | WR: {m_n['win_rate_pct']:4.1f}% | PF: {m_n['profit_factor']:4.2f} | Net USD: ${m_n['net_cash_usd']:+8.2f} | MaxDD: ${m_n['max_drawdown_usd']:.2f}", flush=True)
        print(f"    [TIER A (100%)]: Trades: {m_ta['total_trades']:3d} | WR: {m_ta['win_rate_pct']:4.1f}% | Net USD: ${m_ta['net_cash_usd']:+8.2f}", flush=True)
        print(f"    [TIER B (50%)]:  Trades: {m_tb['total_trades']:3d} | WR: {m_tb['win_rate_pct']:4.1f}% | Net USD: ${m_tb['net_cash_usd']:+8.2f}", flush=True)
        print(f"    [TIER C VETO]:   Bloqueados: {tot_filt:2d} | Pérdidas Evitadas: {losses_saved:2d} | Ganadas Rechazadas: {wins_missed:2d} | Precisión: {prec:.1f}%\n", flush=True)

        comparison_table.append({
            "symbol": sym, "category": cat,
            "period_start": start_dt, "period_end": end_dt,
            "base_metrics": m_b,
            "neo_metrics": m_n,
            "tier_a_metrics": m_ta,
            "tier_b_metrics": m_tb,
            "tier_c_filtered": {"total": tot_filt, "losses_saved": losses_saved, "wins_missed": wins_missed, "precision_pct": round(prec, 1)},
        })

    # Portfolio Total OOS
    port_b = calculate_metrics(all_base_trades)
    port_n = calculate_metrics(all_neo_trades)

    tier_a_all = [t for t in all_neo_trades if t.tier == "TIER_A_ALPHA"]
    tier_b_all = [t for t in all_neo_trades if t.tier == "TIER_B_SURGICAL"]
    port_ta = calculate_metrics(tier_a_all)
    port_tb = calculate_metrics(tier_b_all)

    tot_filt_all = len(all_filtered)
    losses_saved_all = sum(1 for f in all_filtered if f.hypothetical_pnl_r < 0)
    wins_missed_all = sum(1 for f in all_filtered if f.hypothetical_pnl_r > 0)
    prec_all = (losses_saved_all / tot_filt_all * 100.0) if tot_filt_all > 0 else 0.0

    # Surgical loss autopsy
    loss_autopsy = analyze_losing_trades(all_neo_trades)

    # In-Sample reference data for Walk-Forward Efficiency (from earlier 10,000 bars backtest)
    is_pf = 1.32
    is_net_usd = 15586.76
    is_wr = 46.2

    wfe = round(port_n["profit_factor"] / is_pf, 2) if is_pf > 0 else 1.0
    alpha_retention = round((port_n["net_cash_usd"] / is_net_usd) * 100.0, 1) if is_net_usd > 0 else 0.0

    print("=" * 85, flush=True)
    print("   RESUMEN INSTITUCIONAL GLOBAL OUT-OF-SAMPLE (OOS) - NEO HIBRIDO", flush=True)
    print("=" * 85, flush=True)
    print(f"   MÉTRICA                    | BASELINE OOS (Sin Neo) | NEO HÍBRIDO OOS (Nuevo) | IMPACTO / DELTA", flush=True)
    print("-" * 85, flush=True)
    print(f"   Total de Operaciones       | {port_b['total_trades']:<22} | {port_n['total_trades']:<23} | {port_n['total_trades'] - port_b['total_trades']:+d} ({tot_filt_all} vetadas Tier C)", flush=True)
    print(f"   Operaciones Ganadoras      | {port_b['wins']:<22} | {port_n['wins']:<23} | {port_n['wins'] - port_b['wins']:+d}", flush=True)
    print(f"   Operaciones Perdedoras     | {port_b['losses']:<22} | {port_n['losses']:<23} | {port_n['losses'] - port_b['losses']:+d} (Pérdidas evitadas: {losses_saved_all})", flush=True)
    print(f"   Win Rate (%)               | {port_b['win_rate_pct']:.1f}%                  | {port_n['win_rate_pct']:.1f}%                   | {port_n['win_rate_pct'] - port_b['win_rate_pct']:+.1f}%", flush=True)
    print(f"   Profit Factor              | {port_b['profit_factor']:.2f}                   | {port_n['profit_factor']:.2f}                    | {port_n['profit_factor'] - port_b['profit_factor']:+.2f}", flush=True)
    print(f"   Retorno Neto (USD)         | ${port_b['net_cash_usd']:+.2f} USD         | ${port_n['net_cash_usd']:+.2f} USD          | ${port_n['net_cash_usd'] - port_b['net_cash_usd']:+.2f} USD", flush=True)
    print(f"   Drawdown Máximo (USD)      | ${port_b['max_drawdown_usd']:.2f} USD ({port_b['max_drawdown_pct']:.1f}%) | ${port_n['max_drawdown_usd']:.2f} USD ({port_n['max_drawdown_pct']:.1f}%)  | ${port_n['max_drawdown_usd'] - port_b['max_drawdown_usd']:+.2f} USD", flush=True)
    print("-" * 85, flush=True)
    print(f"   [DESGLOSE DE TIERS EN OOS]:", flush=True)
    print(f"     Tier A (Alfa 100%):      {port_ta['total_trades']} trades | WR: {port_ta['win_rate_pct']:.1f}% | Net USD: ${port_ta['net_cash_usd']:+.2f} | PF: {port_ta['profit_factor']:.2f}", flush=True)
    print(f"     Tier B (Quirúrgico 50%): {port_tb['total_trades']} trades | WR: {port_tb['win_rate_pct']:.1f}% | Net USD: ${port_tb['net_cash_usd']:+.2f} | PF: {port_tb['profit_factor']:.2f}", flush=True)
    print(f"     Tier C (Veto Total 0%):  {tot_filt_all} bloqueados | Pérdidas Evitadas: {losses_saved_all} | Precisión: {prec_all:.1f}%", flush=True)
    print("-" * 85, flush=True)
    print(f"   [MÉTRICAS WALK-FORWARD IS vs OOS]:", flush=True)
    print(f"     Walk-Forward Efficiency (WFE): {wfe:.2f} (PF OOS {port_n['profit_factor']:.2f} / PF IS {is_pf:.2f})", flush=True)
    print(f"     Retención de Alfa:             {alpha_retention:.1f}% (${port_n['net_cash_usd']:+.2f} OOS vs ${is_net_usd:+.2f} IS)", flush=True)
    print(f"     Estabilidad de Win Rate:       {port_n['win_rate_pct']:.1f}% OOS vs {is_wr:.1f}% IS (Delta: {port_n['win_rate_pct'] - is_wr:+.1f}%)", flush=True)
    print("=" * 85, flush=True)

    # Save summary report
    Path("data").mkdir(exist_ok=True)
    summary = {
        "portfolio_baseline_oos": port_b,
        "portfolio_neo_oos": port_n,
        "portfolio_tier_a_oos": port_ta,
        "portfolio_tier_b_oos": port_tb,
        "walk_forward_metrics": {
            "wfe": wfe,
            "alpha_retention_pct": alpha_retention,
            "is_pf": is_pf,
            "oos_pf": port_n["profit_factor"],
            "is_net_usd": is_net_usd,
            "oos_net_usd": port_n["net_cash_usd"],
            "is_wr": is_wr,
            "oos_wr": port_n["win_rate_pct"],
        },
        "efficacy": {
            "total_filtered": tot_filt_all,
            "losses_saved": losses_saved_all,
            "wins_missed": wins_missed_all,
            "precision_pct": prec_all,
        },
        "loss_autopsy": loss_autopsy,
        "symbols": comparison_table,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n[OK] Reporte exportado a {args.output}", flush=True)


if __name__ == "__main__":
    main()
