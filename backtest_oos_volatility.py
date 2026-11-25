"""Quantitative Institutional Out-of-Sample (OOS) Validation Suite for Volatility Indices.

Performs strict Walk-Forward Out-of-Sample evaluation on 50,000 M5 bars (~173.6 days)
from MetaTrader 5 Deriv server across the continuous Volatility synthetic indices:
1. Volatility 75 Index
2. Volatility 100 Index
3. Volatility 50 Index
4. Volatility 25 Index
5. Volatility 10 Index

Methodological rigor:
- IN-SAMPLE (IS): Bars 25,000 to 50,000 (Calibration Period, ~86.8 days)
- OUT-OF-SAMPLE (OOS): Bars 0 to 25,000 (Pure Blind Unseen Period, ~86.8 days, untouched)
- Slippage & Costs: Deducted realistic points per trade based on index scale.
- Comprehensive Institutional Metrics:
  * Max Equity Drawdown %
  * Average Win / Average Loss (R & USD)
  * Expectancy per trade (E[R] & E[$])
  * Max Consecutive Losing Trades (Racha Máxima)
  * Annualized Sharpe Ratio (24/7 continuous 365-day basis)
  * Recovery Factor (Net Profit / Max DD)
  * Weekly Profit Factor Breakdown (Consistency across each calendar week)
  * Walk-Forward Efficiency (WFE) & IS vs OOS comparative degradation analysis
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.constants import Direction
from strategy.synthetic_volatility import SyntheticVolatilityStrategy, SyntheticVolatilitySignal

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("VolatilityOOS")


@dataclass
class VolatilityOOSTrade:
    period: str  # "IS" or "OOS"
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    pnl_r: float
    gross_cash: float
    slippage_cost: float
    net_cash: float
    exit_reason: str
    strategy: str
    confluences: str


def fetch_mt5_volatility_data(
    symbol: str, count_m5: int = 25000, start_pos: int = 0
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetch M5, M15, and H1 bars directly from Deriv MT5 for specific position/slice."""
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


def simulate_volatility_slice(
    symbol: str,
    df_m5: pd.DataFrame,
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    strategy: SyntheticVolatilityStrategy,
    period_label: str = "OOS",
    slippage_points: float = 2.0,
    risk_per_trade_usd: float = 75.0,
    point: float = 0.01,
) -> List[VolatilityOOSTrade]:
    """Execute candle-by-candle simulation on continuous Volatility indices."""
    trades: List[VolatilityOOSTrade] = []
    active_trade: Optional[Dict[str, Any]] = None
    bars_held = 0

    df_m5_calc = strategy.calculate_indicators(df_m5)
    df_m15_calc = strategy.calculate_indicators(df_m15) if df_m15 is not None else None
    df_h1_calc = strategy.calculate_indicators(df_h1) if df_h1 is not None else None

    h1_times = df_h1_calc["time"].values if df_h1_calc is not None else None
    m15_times = df_m15_calc["time"].values if df_m15_calc is not None else None

    def calculate_net_cash(pnl_r_val: float, risk_pts: float) -> Tuple[float, float, float, float]:
        """Deduct realistic spread/slippage cost in USD and net R."""
        gross_cash = pnl_r_val * risk_per_trade_usd
        slip_cost = (slippage_points / max(risk_pts, 0.001)) * risk_per_trade_usd
        net_cash = gross_cash - slip_cost
        net_r = net_cash / risk_per_trade_usd
        return round(gross_cash, 2), round(slip_cost, 2), round(net_cash, 2), round(net_r, 2)

    for i in range(40, len(df_m5_calc)):
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
            partial_tp = active_trade["partial_tp"]
            initial_risk = active_trade["initial_risk"]
            has_partial = active_trade["has_partial"]

            # Check SL and TP
            hit_sl = (direction == "BUY" and c_low <= sl) or (direction == "SELL" and c_high >= sl)
            hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

            # Conflict resolution: closer to open
            if hit_sl and hit_tp:
                if abs(c_open - sl) < abs(c_open - tp):
                    hit_tp = False
                else:
                    hit_sl = False

            # Check Partial Take Profit
            if not has_partial:
                hit_partial = (direction == "BUY" and c_high >= partial_tp) or (direction == "SELL" and c_low <= partial_tp)
                if hit_partial:
                    active_trade["has_partial"] = True
                    # Move SL to Breakeven (+0.15 R buffer)
                    be_level = entry + (initial_risk * 0.15) if direction == "BUY" else entry - (initial_risk * 0.15)
                    active_trade["current_sl"] = be_level

            # Trailing stop activation (when price reaches trailing_activation_r, trail by 0.70 R)
            peak_r = (c_high - entry) / initial_risk if direction == "BUY" else (entry - c_low) / initial_risk
            act_r = active_trade.get("trailing_activation_r", 1.5)
            if peak_r >= act_r:
                trail_sl = c_high - (initial_risk * 0.70) if direction == "BUY" else c_low + (initial_risk * 0.70)
                if direction == "BUY" and trail_sl > active_trade["current_sl"]:
                    active_trade["current_sl"] = trail_sl
                elif direction == "SELL" and trail_sl < active_trade["current_sl"]:
                    active_trade["current_sl"] = trail_sl

            # Max holding bars (e.g. 24 bars = 2 hours)
            time_exit = bars_held >= 24

            p_rr = active_trade.get("partial_rr", 1.35)
            if hit_tp:
                # Full TP reached
                pnl_r_raw = active_trade["target_rr"] if not has_partial else (active_trade["target_rr"] * 0.5 + p_rr * 0.5)
                gross_c, slip_c, net_c, net_r = calculate_net_cash(pnl_r_raw, initial_risk)
                trades.append(VolatilityOOSTrade(
                    period=period_label, symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=tp, sl_price=sl, tp_price=tp,
                    pnl_r=net_r, gross_cash=gross_c, slippage_cost=slip_c, net_cash=net_c,
                    exit_reason="WIN_TP", strategy=active_trade["setup_model"],
                    confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r_raw = round(sl_diff / initial_risk, 2)
                if has_partial:
                    pnl_r_raw = p_rr * 0.5 + pnl_r_raw * 0.5
                reason = "EXIT_BREAKEVEN_LOCK" if pnl_r_raw >= 0 else "LOSS_SL"
                gross_c, slip_c, net_c, net_r = calculate_net_cash(pnl_r_raw, initial_risk)
                trades.append(VolatilityOOSTrade(
                    period=period_label, symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=sl, sl_price=sl, tp_price=tp,
                    pnl_r=net_r, gross_cash=gross_c, slippage_cost=slip_c, net_cash=net_c,
                    exit_reason=reason, strategy=active_trade["setup_model"],
                    confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            elif time_exit:
                curr_gain = (c_close - entry) if direction == "BUY" else (entry - c_close)
                pnl_r_raw = round(curr_gain / initial_risk, 2)
                gross_c, slip_c, net_c, net_r = calculate_net_cash(pnl_r_raw, initial_risk)
                trades.append(VolatilityOOSTrade(
                    period=period_label, symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close, sl_price=sl, tp_price=tp,
                    pnl_r=net_r, gross_cash=gross_c, slippage_cost=slip_c, net_cash=net_c,
                    exit_reason="EXIT_TIME_EXPIRY", strategy=active_trade["setup_model"],
                    confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            continue

        # 2. Look for Entry Candidate
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

        sig = strategy.evaluate_volatility_setup(symbol, m5_slice, df_m15=m15_slice, df_h1=h1_slice, point=point)
        if sig is None:
            continue

        risk_pts = abs(sig.entry_price - sig.sl_price)
        active_trade = {
            "direction": sig.direction.value,
            "entry": sig.entry_price,
            "initial_sl": sig.sl_price,
            "current_sl": sig.sl_price,
            "tp": sig.tp_price,
            "partial_tp": sig.partial_tp_price,
            "initial_risk": risk_pts,
            "target_rr": sig.risk_reward,
            "partial_rr": round(abs(sig.partial_tp_price - sig.entry_price) / max(risk_pts, 0.001), 2),
            "trailing_activation_r": sig.trailing_activation_r,
            "has_partial": False,
            "entry_time": bar_time_str,
            "setup_model": sig.setup_model,
            "confluences": ", ".join(sig.confluences),
        }
        bars_held = 0

    return trades


def calculate_comprehensive_volatility_metrics(
    trades: List[VolatilityOOSTrade], initial_balance: float = 10000.0
) -> Dict[str, Any]:
    """Calculate institutional metrics: Sharpe, Max Loss Streak, Recovery Factor, Expectancy, DD%."""
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "profit_factor": 0.0, "net_r": 0.0, "net_cash_usd": 0.0,
            "total_slippage_usd": 0.0, "return_pct": 0.0,
            "avg_win_r": 0.0, "avg_loss_r": 0.0, "avg_win_usd": 0.0, "avg_loss_usd": 0.0,
            "win_loss_ratio_r": 0.0, "expectancy_r": 0.0, "expectancy_usd": 0.0,
            "max_consecutive_losses": 0, "max_consecutive_wins": 0,
            "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
            "recovery_factor": 0.0, "annualized_sharpe": 0.0,
            "weekly_stats": {},
        }

    wins = [t for t in trades if t.net_cash > 0]
    losses = [t for t in trades if t.net_cash <= 0]

    win_count = len(wins)
    loss_count = len(losses)
    total_trades = len(trades)
    win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

    gross_profit = sum(t.net_cash for t in wins)
    gross_loss = abs(sum(t.net_cash for t in losses))
    net_cash = gross_profit - gross_loss
    net_r = sum(t.pnl_r for t in trades)
    total_slippage = sum(t.slippage_cost for t in trades)

    profit_factor = round(gross_profit / max(gross_loss, 0.001), 2)
    return_pct = round((net_cash / initial_balance) * 100.0, 2)

    avg_win_r = round(float(np.mean([t.pnl_r for t in wins])), 2) if wins else 0.0
    avg_loss_r = round(abs(float(np.mean([t.pnl_r for t in losses]))), 2) if losses else 0.0
    avg_win_usd = round(gross_profit / max(win_count, 1), 2)
    avg_loss_usd = round(gross_loss / max(loss_count, 1), 2)

    win_loss_ratio_r = round(avg_win_r / max(avg_loss_r, 0.001), 2)

    prob_w = win_count / total_trades
    prob_l = loss_count / total_trades
    expectancy_r = round((prob_w * avg_win_r) - (prob_l * avg_loss_r), 3)
    expectancy_usd = round((prob_w * avg_win_usd) - (prob_l * avg_loss_usd), 2)

    # Max Streaks
    max_consec_losses = 0
    curr_losses = 0
    max_consec_wins = 0
    curr_wins = 0
    for t in trades:
        if t.net_cash <= 0:
            curr_losses += 1
            curr_wins = 0
            max_consec_losses = max(max_consec_losses, curr_losses)
        else:
            curr_wins += 1
            curr_losses = 0
            max_consec_wins = max(max_consec_wins, curr_wins)

    # Equity Curve & Drawdown
    equity_curve = [initial_balance]
    peak = initial_balance
    max_dd_usd = 0.0
    max_dd_pct = 0.0

    for t in trades:
        eq = equity_curve[-1] + t.net_cash
        equity_curve.append(eq)
        if eq > peak:
            peak = eq
        dd_usd = peak - eq
        dd_pct = (dd_usd / peak) * 100.0 if peak > 0 else 0.0
        max_dd_usd = max(max_dd_usd, dd_usd)
        max_dd_pct = max(max_dd_pct, dd_pct)

    recovery_factor = round(net_cash / max(max_dd_usd, 1.0), 2) if net_cash > 0 else 0.0

    # Annualized Sharpe (Daily aggregation across 24/7 continuous synthetic market)
    trade_df = pd.DataFrame([asdict(t) for t in trades])
    trade_df["exit_time_dt"] = pd.to_datetime(trade_df["exit_time"])
    trade_df["date"] = trade_df["exit_time_dt"].dt.date
    daily_pnl = trade_df.groupby("date")["net_cash"].sum()

    if len(daily_pnl) > 5 and daily_pnl.std() > 0:
        annualized_sharpe = round(float((daily_pnl.mean() / daily_pnl.std()) * np.sqrt(365.0)), 2)
    else:
        annualized_sharpe = 0.0

    # Weekly Consistency Breakdown
    trade_df["year_week"] = trade_df["exit_time_dt"].dt.strftime("%Y-W%U")
    weekly_group = trade_df.groupby("year_week")

    weekly_stats = {}
    profitable_weeks = 0
    total_weeks = 0

    for week_label, grp in weekly_group:
        w_wins = grp[grp["net_cash"] > 0]
        w_losses = grp[grp["net_cash"] <= 0]
        w_gp = w_wins["net_cash"].sum()
        w_gl = abs(w_losses["net_cash"].sum())
        w_net = w_gp - w_gl
        w_pf = round(w_gp / max(w_gl, 0.01), 2) if w_gl > 0 else (99.0 if w_gp > 0 else 0.0)

        total_weeks += 1
        if w_net > 0:
            profitable_weeks += 1

        weekly_stats[week_label] = {
            "trades": len(grp),
            "win_rate_pct": round(len(w_wins) / len(grp) * 100.0, 1),
            "net_cash_usd": round(w_net, 2),
            "profit_factor": w_pf,
            "profitable": bool(w_net > 0),
        }

    weekly_consistency_pct = round((profitable_weeks / max(total_weeks, 1)) * 100.0, 1)

    return {
        "total_trades": total_trades,
        "wins": win_count,
        "losses": loss_count,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": profit_factor,
        "net_r": round(net_r, 2),
        "net_cash_usd": round(net_cash, 2),
        "total_slippage_usd": round(total_slippage, 2),
        "return_pct": return_pct,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
        "avg_win_usd": avg_win_usd,
        "avg_loss_usd": avg_loss_usd,
        "win_loss_ratio_r": win_loss_ratio_r,
        "expectancy_r": expectancy_r,
        "expectancy_usd": expectancy_usd,
        "max_consecutive_losses": max_consec_losses,
        "max_consecutive_wins": max_consec_wins,
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "recovery_factor": recovery_factor,
        "annualized_sharpe": annualized_sharpe,
        "profitable_weeks": profitable_weeks,
        "total_weeks": total_weeks,
        "weekly_consistency_pct": weekly_consistency_pct,
        "weekly_breakdown": weekly_stats,
    }


def run_full_volatility_oos_suite(count_bars: int = 10000, selected_symbols: Optional[List[str]] = None):
    """Run IS vs OOS validation across Volatility continuous indices."""
    if not MT5_AVAILABLE:
        print("[ERROR] MetaTrader 5 not available.")
        return

    if not mt5.initialize():
        print("[ERROR] Could not connect to MetaTrader 5 terminal.")
        return

    print("=" * 80)
    print("   QUANTUM-DERIV: INSTITUTIONAL OUT-OF-SAMPLE (OOS) VALIDATION SUITE")
    print("            CONTINUOUS VOLATILITY INDICES (SURGICAL SMC & EXPANSION)        ")
    print("=" * 80)

    symbols_config = [
        {"symbol": "Volatility 75 Index", "point": 0.01, "slippage": 16.0, "desc": "V75 (High Volatility Continuous)"},
        {"symbol": "Volatility 100 Index", "point": 0.01, "slippage": 0.30, "desc": "V100 (Max Volatility Continuous)"},
        {"symbol": "Volatility 50 Index", "point": 0.0001, "slippage": 0.025, "desc": "V50 (Balanced Volatility Continuous)"},
        {"symbol": "Volatility 25 Index", "point": 0.001, "slippage": 0.35, "desc": "V25 (Medium Low Volatility Continuous)"},
        {"symbol": "Volatility 10 Index", "point": 0.001, "slippage": 0.20, "desc": "V10 (Low Volatility Continuous)"},
    ]

    if selected_symbols:
        symbols_config = [s for s in symbols_config if s["symbol"] in selected_symbols or any(sel.lower() in s["symbol"].lower() for sel in selected_symbols)]

    strategy = SyntheticVolatilityStrategy(
        ema_fast=20,
        ema_medium=50,
        ema_slow=200,
        atr_period=14,
        sl_buffer_atr=0.25,
        target_rr=2.20,
        partial_rr=1.35,
        min_rr=1.40,
        trailing_activation_r=1.50,
        trailing_distance_r=0.70,
        enable_trend_pullback=True,
        enable_squeeze_expansion=True,
        enable_pullback_sweep=True,
        enable_mean_reversion=False,
        enable_breakout=False,
    )

    all_is_trades: List[VolatilityOOSTrade] = []
    all_oos_trades: List[VolatilityOOSTrade] = []
    symbol_reports: Dict[str, Any] = {}

    for sym_info in symbols_config:
        sym = sym_info["symbol"]
        pt = sym_info["point"]
        slip = sym_info["slippage"]
        desc = sym_info["desc"]

        print(f"\n>>> Processing: {sym} ({desc})")
        print(f"    Downloading {count_bars*2} M5 bars + M15 + H1 from Deriv server...")

        # OOS Slice: Bars 0 to count_bars (recent unseen data)
        df_m5_oos, df_m15_oos, df_h1_oos = fetch_mt5_volatility_data(sym, count_m5=count_bars, start_pos=0)
        # IS Slice: Bars count_bars to 2*count_bars (historical calibration data)
        df_m5_is, df_m15_is, df_h1_is = fetch_mt5_volatility_data(sym, count_m5=count_bars, start_pos=count_bars)

        if df_m5_oos is None or df_m5_is is None:
            print(f"    [WARN] Failed to fetch full {count_bars*2} bars for {sym}. Skipping.")
            continue

        print(f"    [OK] OOS Range: {df_m5_oos['time'].iloc[0]} to {df_m5_oos['time'].iloc[-1]} ({len(df_m5_oos)} bars)")
        print(f"    [OK] IS Range:  {df_m5_is['time'].iloc[0]} to {df_m5_is['time'].iloc[-1]} ({len(df_m5_is)} bars)")

        # Simulate IS
        trades_is = simulate_volatility_slice(
            sym, df_m5_is, df_m15_is, df_h1_is, strategy,
            period_label="IS", slippage_points=slip, point=pt
        )
        # Simulate OOS
        trades_oos = simulate_volatility_slice(
            sym, df_m5_oos, df_m15_oos, df_h1_oos, strategy,
            period_label="OOS", slippage_points=slip, point=pt
        )

        all_is_trades.extend(trades_is)
        all_oos_trades.extend(trades_oos)

        m_is = calculate_comprehensive_volatility_metrics(trades_is)
        m_oos = calculate_comprehensive_volatility_metrics(trades_oos)

        wfe = round(m_oos["profit_factor"] / max(m_is["profit_factor"], 0.01), 2)

        symbol_reports[sym] = {
            "desc": desc,
            "IS": m_is,
            "OOS": m_oos,
            "WFE": wfe,
        }

        print(f"    IS Results:  Trades={m_is['total_trades']}, WR={m_is['win_rate_pct']}%, PF={m_is['profit_factor']}, Net=${m_is['net_cash_usd']} USD, MaxDD={m_is['max_drawdown_pct']}%")
        print(f"    OOS Results: Trades={m_oos['total_trades']}, WR={m_oos['win_rate_pct']}%, PF={m_oos['profit_factor']}, Net=${m_oos['net_cash_usd']} USD, MaxDD={m_oos['max_drawdown_pct']}%")
        print(f"    Walk-Forward Efficiency (WFE): {wfe:.2f} ({'PASSED' if wfe >= 0.70 else 'DEGRADED'})")

    # Do not call mt5.shutdown() here as live trading bots share the MT5 terminal session

    # Portfolio Aggregation
    port_is = calculate_comprehensive_volatility_metrics(all_is_trades, initial_balance=25000.0)
    port_oos = calculate_comprehensive_volatility_metrics(all_oos_trades, initial_balance=25000.0)
    port_wfe = round(port_oos["profit_factor"] / max(port_is["profit_factor"], 0.01), 2)

    return {
        "symbols": symbol_reports,
        "portfolio": {
            "IS": port_is,
            "OOS": port_oos,
            "WFE": port_wfe,
        },
        "all_is_trades": [asdict(t) for t in all_is_trades],
        "all_oos_trades": [asdict(t) for t in all_oos_trades],
    }


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description="Backtest Volatility OOS Institutional Suite")
    parser.add_argument("--bars", type=int, default=10000, help="Number of M5 bars per slice (default: 10000)")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols to test (e.g. '75,100')")
    args = parser.parse_args()

    selected = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    res = run_full_volatility_oos_suite(count_bars=args.bars, selected_symbols=selected)
    if res:
        # Save summary report
        Path("data").mkdir(exist_ok=True)
        summary = {
            "symbols": res["symbols"],
            "portfolio": res["portfolio"],
        }
        with open("data/volatility_oos_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        print("\nFull OOS report saved to data/volatility_oos_summary.json")
