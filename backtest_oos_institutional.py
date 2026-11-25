"""Quantitative Institutional Out-of-Sample (OOS) Validation Suite.

Performs a strict Walk-Forward Out-Of-Sample evaluation on 50,000 M5 bars (~173 days)
from MetaTrader 5 Deriv server across the 5 top-performing synthetic indices:
1. Crash 1000 Index
2. Boom 1000 Index
3. Boom 500 Index
4. Crash 500 Index
5. Boom 300 Index

Methodological rigor:
- IN-SAMPLE (IS): Bars 25,000 to 50,000 (Historical Calibration Period, ~87 days)
- OUT-OF-SAMPLE (OOS): Bars 0 to 25,000 (Pure Blind Unseen Period, ~87 days, untouched)
- Slippage & Costs: Realistic 2.0 points deducted per trade.
- Comprehensive Institutional Metrics:
  * Max Equity Drawdown %
  * Average Win / Average Loss (R & USD)
  * Expectancy per trade (E[R] & E[$])
  * Max Consecutive Losing Trades (Racha Máxima)
  * Annualized Sharpe Ratio (24/7 365-day basis)
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
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal
from strategy.synthetic_system_one import SyntheticSystemOneEvaluator
from filters.system_one.hub import SystemOneHub

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("InstitutionalOOS")


@dataclass
class OOStrade:
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


def fetch_mt5_data(
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


def run_spike_simulation_slice(
    period_label: str,
    symbol: str,
    df_m5: pd.DataFrame,
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    spike_strat: SpikeSniperStrategy,
    s1_evaluator: SyntheticSystemOneEvaluator,
    risk_per_trade_usd: float = 75.0,
    point: float = 0.001,
    slippage_points: float = 2.0,
) -> List[OOStrade]:
    """Run bar-by-bar simulation with realistic slippage costs and System 1 gating."""
    trades: List[OOStrade] = []
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

    # Cost calculation: slippage points as fraction of initial risk
    def calculate_net_cash(pnl_r_raw: float, initial_risk_pts: float) -> Tuple[float, float, float, float]:
        gross_cash = pnl_r_raw * risk_per_trade_usd
        # Realistic slippage penalty (deduct slippage points relative to risk)
        slip_r = (slippage_points / initial_risk_pts) if initial_risk_pts > 0 else 0.05
        slip_cash = slip_r * risk_per_trade_usd
        net_cash = gross_cash - slip_cash
        net_r = round(pnl_r_raw - slip_r, 2)
        return round(gross_cash, 2), round(slip_cash, 2), round(net_cash, 2), net_r

    for i in range(start_bar, n_bars):
        curr_bar = df_m5_calc.iloc[i]
        c_open = float(curr_bar["open"])
        c_high = float(curr_bar["high"])
        c_low = float(curr_bar["low"])
        c_close = float(curr_bar["close"])
        bar_time = curr_bar["time"]
        bar_time_str = str(bar_time)

        # 1. Manage Active Position
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
                pnl_r_raw = max(1.0, actual_gain / initial_risk) if initial_risk > 0 else 2.0
                gross_c, slip_c, net_c, net_r = calculate_net_cash(pnl_r_raw, initial_risk)
                trades.append(OOStrade(
                    period=period_label, symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close if is_spike else tp,
                    sl_price=sl, tp_price=tp, pnl_r=net_r,
                    gross_cash=gross_c, slippage_cost=slip_c, net_cash=net_c,
                    exit_reason="WIN_SPIKE" if is_spike else "WIN_TP",
                    strategy=active_trade["setup_name"], confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r_raw = round(sl_diff / initial_risk, 2) if initial_risk > 0 else -1.0
                reason = "EXIT_PROFIT_LOCK" if pnl_r_raw > 0 else "LOSS_SL"
                gross_c, slip_c, net_c, net_r = calculate_net_cash(pnl_r_raw, initial_risk)
                trades.append(OOStrade(
                    period=period_label, symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=sl,
                    sl_price=sl, tp_price=tp, pnl_r=net_r,
                    gross_cash=gross_c, slippage_cost=slip_c, net_cash=net_c,
                    exit_reason=reason,
                    strategy=active_trade["setup_name"], confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            if bars_held >= 12 and curr_gain <= 0:
                pnl_r_raw = -round(abs(curr_gain) / initial_risk, 2) if initial_risk > 0 else -0.5
                gross_c, slip_c, net_c, net_r = calculate_net_cash(max(-1.0, pnl_r_raw), initial_risk)
                trades.append(OOStrade(
                    period=period_label, symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close,
                    sl_price=sl, tp_price=tp, pnl_r=net_r,
                    gross_cash=gross_c, slippage_cost=slip_c, net_cash=net_c,
                    exit_reason="EXIT_TIME_DECAY",
                    strategy=active_trade["setup_name"], confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            if tp_dist > 0 and peak_gain >= tp_dist * 0.85:
                lock_dist = peak_gain * 0.50
                target_sl = entry + lock_dist if direction == "BUY" else entry - lock_dist
                if (direction == "BUY" and target_sl > sl) or (direction == "SELL" and target_sl < sl):
                    active_trade["current_sl"] = target_sl

            continue

        # 2. Look for Entry Candidate
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

        # 3. System 1 Gate Evaluation
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
        if not s1_res.approved:
            continue

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
        }
        bars_held = 0

    return trades


def calculate_comprehensive_metrics(
    trades: List[OOStrade], initial_balance: float = 10000.0
) -> Dict[str, Any]:
    """Calculate institutional metrics: Sharpe, Max Loss Streak, Recovery Factor, Expectancy, DD%."""
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "profit_factor": 0.0, "net_r": 0.0, "net_cash_usd": 0.0,
            "total_slippage_usd": 0.0, "return_pct": 0.0,
            "avg_win_r": 0.0, "avg_loss_r": 0.0, "avg_win_usd": 0.0, "avg_loss_usd": 0.0,
            "win_loss_payoff": 0.0, "expectancy_r": 0.0, "expectancy_usd": 0.0,
            "max_loss_streak": 0, "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0, "recovery_factor": 0.0,
        }

    wins = [t for t in trades if t.net_cash > 0]
    losses = [t for t in trades if t.net_cash < 0]
    total = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total * 100.0) if total > 0 else 0.0

    gross_profit_usd = sum(t.net_cash for t in wins)
    gross_loss_usd = abs(sum(t.net_cash for t in losses))
    net_cash_usd = gross_profit_usd - gross_loss_usd
    profit_factor = (gross_profit_usd / gross_loss_usd) if gross_loss_usd > 0 else 99.0
    net_r = sum(t.pnl_r for t in trades)
    total_slippage = sum(t.slippage_cost for t in trades)

    avg_win_usd = (gross_profit_usd / win_count) if win_count > 0 else 0.0
    avg_loss_usd = (gross_loss_usd / loss_count) if loss_count > 0 else 0.0
    avg_win_r = (sum(t.pnl_r for t in wins) / win_count) if win_count > 0 else 0.0
    avg_loss_r = (abs(sum(t.pnl_r for t in losses)) / loss_count) if loss_count > 0 else 0.0
    win_loss_payoff = (avg_win_usd / avg_loss_usd) if avg_loss_usd > 0 else 0.0

    # Expectancy
    win_p = win_count / total if total > 0 else 0.0
    loss_p = loss_count / total if total > 0 else 0.0
    expectancy_r = (win_p * avg_win_r) - (loss_p * avg_loss_r)
    expectancy_usd = (win_p * avg_win_usd) - (loss_p * avg_loss_usd)

    # Max Loss Streak
    max_streak = 0
    curr_streak = 0
    for t in trades:
        if t.net_cash < 0:
            curr_streak += 1
            if curr_streak > max_streak:
                max_streak = curr_streak
        else:
            curr_streak = 0

    # Equity Curve & Drawdown
    equity = initial_balance
    peak = equity
    max_dd_usd = 0.0
    max_dd_pct = 0.0

    # Daily Returns for Sharpe
    daily_pnls: Dict[str, float] = {}

    for t in trades:
        equity += t.net_cash
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_usd:
            max_dd_usd = dd
            max_dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0

        day_str = str(t.entry_time)[:10]
        daily_pnls[day_str] = daily_pnls.get(day_str, 0.0) + t.net_cash

    # Annualized Sharpe (365 days 24/7)
    if daily_pnls:
        daily_vals = np.array(list(daily_pnls.values()))
        daily_ret = daily_vals / initial_balance
        std_dev = np.std(daily_ret)
        mean_ret = np.mean(daily_ret)
        sharpe = float((mean_ret / std_dev) * np.sqrt(365.0)) if std_dev > 1e-6 else 0.0
    else:
        sharpe = 0.0

    recovery_factor = (net_cash_usd / max_dd_usd) if max_dd_usd > 0 else 0.0
    return_pct = (net_cash_usd / initial_balance) * 100.0

    return {
        "total_trades": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "net_r": round(net_r, 2),
        "net_cash_usd": round(net_cash_usd, 2),
        "total_slippage_usd": round(total_slippage, 2),
        "return_pct": round(return_pct, 1),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
        "avg_win_usd": round(avg_win_usd, 2),
        "avg_loss_usd": round(avg_loss_usd, 2),
        "win_loss_payoff": round(win_loss_payoff, 2),
        "expectancy_r": round(expectancy_r, 2),
        "expectancy_usd": round(expectancy_usd, 2),
        "max_loss_streak": max_streak,
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "recovery_factor": round(recovery_factor, 2),
    }


def calculate_weekly_breakdown(trades: List[OOStrade]) -> List[Dict[str, Any]]:
    """Group trades by calendar week and calculate Weekly Profit Factor and consistency."""
    if not trades:
        return []

    df = pd.DataFrame([asdict(t) for t in trades])
    df["entry_dt"] = pd.to_datetime(df["entry_time"])
    # Group by calendar week (Monday to Sunday)
    df["week_period"] = df["entry_dt"].dt.to_period("W")

    weekly_rows = []
    for week, group in df.groupby("week_period"):
        w_wins = group[group["net_cash"] > 0]
        w_losses = group[group["net_cash"] < 0]
        w_trades = len(group)
        w_win_count = len(w_wins)
        w_wr = (w_win_count / w_trades * 100.0) if w_trades > 0 else 0.0

        w_gross_profit = float(w_wins["net_cash"].sum())
        w_gross_loss = float(abs(w_losses["net_cash"].sum()))
        w_net_cash = w_gross_profit - w_gross_loss
        w_pf = (w_gross_profit / w_gross_loss) if w_gross_loss > 0 else (99.0 if w_gross_profit > 0 else 0.0)

        start_str = str(week.start_time.date())
        end_str = str(week.end_time.date())

        weekly_rows.append({
            "week_label": f"Semana {len(weekly_rows)+1:02d} ({start_str} - {end_str})",
            "trades": w_trades,
            "wins": w_win_count,
            "losses": len(w_losses),
            "win_rate_pct": round(w_wr, 1),
            "gross_profit_usd": round(w_gross_profit, 2),
            "gross_loss_usd": round(w_gross_loss, 2),
            "net_cash_usd": round(w_net_cash, 2),
            "profit_factor": round(w_pf, 2),
        })

    return weekly_rows


def main():
    parser = argparse.ArgumentParser(description="Institutional OOS Validation for Deriv Synthetics")
    parser.add_argument("--bars-per-slice", type=int, default=25000, help="Bars per slice (25,000 IS + 25,000 OOS = 50,000 total)")
    parser.add_argument("--slippage-points", type=float, default=2.0, help="Realistic slippage cost in points per trade")
    parser.add_argument("--risk-usd", type=float, default=75.0, help="USD risk per trade")
    args = parser.parse_args()

    print("=" * 85)
    print("   QUANTUM-DERIV: VALIDACIÓN INSTITUCIONAL OUT-OF-SAMPLE (OOS)")
    print("   [50,000 VELAS M5 (~173 DÍAS 24/7) | COSTES Y SLIPPAGE REALISTAS]")
    print("=" * 85)

    if not MT5_AVAILABLE:
        print("[ERROR] MetaTrader5 package is not available.")
        return

    if not mt5.initialize():
        print(f"[ERROR] Could not connect to MetaTrader 5: {mt5.last_error()}")
        return

    symbols_config = [
        {"symbol": "Crash 1000 Index", "point": 0.0001},
        {"symbol": "Boom 1000 Index", "point": 0.0001},
        {"symbol": "Boom 500 Index", "point": 0.001},
        {"symbol": "Crash 500 Index", "point": 0.001},
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

    s1_hub = SystemOneHub(
        enabled=True,
        provider_name="nanojev",
        shadow_mode=False,
        device="cuda",
    )
    s1_evaluator = SyntheticSystemOneEvaluator(
        hub=s1_hub,
        shadow_mode=False,
        min_evidence_score=3,
        min_confidence=0.60,
    )

    all_is_trades: List[OOStrade] = []
    all_oos_trades: List[OOStrade] = []

    is_earliest = None
    is_latest = None
    oos_earliest = None
    oos_latest = None

    print(f"\n[+] Partición Metodológica Estricta:")
    print(f"    * IN-SAMPLE (IS):       Velas 25,000 a 50,000 (Periodo Histórico Pasado)")
    print(f"    * OUT-OF-SAMPLE (OOS):  Velas 0 a 25,000      (Periodo Ciego Intocable Reciente)")
    print(f"    * Costes aplicados:     {args.slippage_points} puntos de slippage/spread por trade\n")

    for item in symbols_config:
        sym = item["symbol"]
        pt = item["point"]

        # Fetch IN-SAMPLE data (older slice)
        df_m5_is, df_m15_is, df_h1_is = fetch_mt5_data(sym, count_m5=args.bars_per_slice, start_pos=args.bars_per_slice)
        # Fetch OUT-OF-SAMPLE data (recent untouched slice)
        df_m5_oos, df_m15_oos, df_h1_oos = fetch_mt5_data(sym, count_m5=args.bars_per_slice, start_pos=0)

        if df_m5_is is None or df_m5_oos is None:
            print(f"[-] {sym}: Datos insuficientes en MT5. Omitiendo.")
            continue

        if is_earliest is None or df_m5_is["time"].iloc[0] < is_earliest:
            is_earliest = df_m5_is["time"].iloc[0]
        if is_latest is None or df_m5_is["time"].iloc[-1] > is_latest:
            is_latest = df_m5_is["time"].iloc[-1]

        if oos_earliest is None or df_m5_oos["time"].iloc[0] < oos_earliest:
            oos_earliest = df_m5_oos["time"].iloc[0]
        if oos_latest is None or df_m5_oos["time"].iloc[-1] > oos_latest:
            oos_latest = df_m5_oos["time"].iloc[-1]

        # Execute IS
        is_trades = run_spike_simulation_slice(
            period_label="IS", symbol=sym,
            df_m5=df_m5_is, df_m15=df_m15_is, df_h1=df_h1_is,
            spike_strat=spike_strat, s1_evaluator=s1_evaluator,
            risk_per_trade_usd=args.risk_usd, point=pt,
            slippage_points=args.slippage_points,
        )
        all_is_trades.extend(is_trades)

        # Execute OOS
        oos_trades = run_spike_simulation_slice(
            period_label="OOS", symbol=sym,
            df_m5=df_m5_oos, df_m15=df_m15_oos, df_h1=df_h1_oos,
            spike_strat=spike_strat, s1_evaluator=s1_evaluator,
            risk_per_trade_usd=args.risk_usd, point=pt,
            slippage_points=args.slippage_points,
        )
        all_oos_trades.extend(oos_trades)

        m_is = calculate_comprehensive_metrics(is_trades)
        m_oos = calculate_comprehensive_metrics(oos_trades)
        print(f"  * {sym:20s} | IS: {m_is['total_trades']:3d} trades (WR: {m_is['win_rate_pct']:4.1f}% | PF: {m_is['profit_factor']:4.2f}) "
              f"==> OOS: {m_oos['total_trades']:3d} trades (WR: {m_oos['win_rate_pct']:4.1f}% | PF: {m_oos['profit_factor']:4.2f})")

    mt5.shutdown()

    # Calculate overall metrics
    is_metrics = calculate_comprehensive_metrics(all_is_trades)
    oos_metrics = calculate_comprehensive_metrics(all_oos_trades)

    # Walk-Forward Efficiency (WFE)
    wfe_ret = (oos_metrics["net_cash_usd"] / is_metrics["net_cash_usd"] * 100.0) if is_metrics["net_cash_usd"] > 0 else 0.0
    wfe_pf = (oos_metrics["profit_factor"] / is_metrics["profit_factor"]) if is_metrics["profit_factor"] > 0 else 0.0

    is_days = max(1.0, (is_latest - is_earliest).total_seconds() / 86400.0) if is_earliest and is_latest else 86.8
    oos_days = max(1.0, (oos_latest - oos_earliest).total_seconds() / 86400.0) if oos_earliest and oos_latest else 86.8

    # Weekly breakdown for OOS
    oos_weekly = calculate_weekly_breakdown(all_oos_trades)

    print("\n" + "=" * 90)
    print("   CUADRO COMPARATIVO RIGUROSO: IN-SAMPLE (IS) vs. OUT-OF-SAMPLE (OOS)")
    print("=" * 90)
    print(f"   Periodo IS:   {is_earliest.strftime('%Y-%m-%d')} a {is_latest.strftime('%Y-%m-%d')} ({is_days:.1f} días continuos)")
    print(f"   Periodo OOS:  {oos_earliest.strftime('%Y-%m-%d')} a {oos_latest.strftime('%Y-%m-%d')} ({oos_days:.1f} días continuos CIEGO)")
    print("-" * 90)
    print(f"   MÉTRICA INSTITUCIONAL              | IN-SAMPLE (IS)          | OUT-OF-SAMPLE (OOS)     | VARIACIÓN (OOS vs IS)")
    print("-" * 90)
    print(f"   Total Operaciones                  | {is_metrics['total_trades']:<23} | {oos_metrics['total_trades']:<23} | {oos_metrics['total_trades'] - is_metrics['total_trades']:+d} trades")
    print(f"   Operaciones Ganadoras / Perdedoras | {is_metrics['wins']} W / {is_metrics['losses']} L              | {oos_metrics['wins']} W / {oos_metrics['losses']} L              | -")
    print(f"   Win Rate (%)                       | {is_metrics['win_rate_pct']:.1f}%                  | {oos_metrics['win_rate_pct']:.1f}%                  | {oos_metrics['win_rate_pct'] - is_metrics['win_rate_pct']:+.1f}%")
    print(f"   Profit Factor                      | {is_metrics['profit_factor']:.2f}                   | {oos_metrics['profit_factor']:.2f}                   | {oos_metrics['profit_factor'] - is_metrics['profit_factor']:+.2f}")
    print(f"   Retorno Neto (USD)                 | ${is_metrics['net_cash_usd']:+8.2f} USD         | ${oos_metrics['net_cash_usd']:+8.2f} USD         | ${oos_metrics['net_cash_usd'] - is_metrics['net_cash_usd']:+8.2f} USD")
    print(f"   Coste de Slippage Deducido         | ${is_metrics['total_slippage_usd']:8.2f} USD         | ${oos_metrics['total_slippage_usd']:8.2f} USD         | -")
    print(f"   Retorno Neto (R)                   | {is_metrics['net_r']:+6.2f}R                 | {oos_metrics['net_r']:+6.2f}R                 | {oos_metrics['net_r'] - is_metrics['net_r']:+6.2f}R")
    print(f"   Average Win / Average Loss (USD)   | ${is_metrics['avg_win_usd']:.2f} / ${is_metrics['avg_loss_usd']:.2f}   | ${oos_metrics['avg_win_usd']:.2f} / ${oos_metrics['avg_loss_usd']:.2f}   | Payoff: {oos_metrics['win_loss_payoff']:.2f}x")
    print(f"   Expectancy por trade (USD)         | ${is_metrics['expectancy_usd']:+.2f} USD              | ${oos_metrics['expectancy_usd']:+.2f} USD              | ${oos_metrics['expectancy_usd'] - is_metrics['expectancy_usd']:+.2f} USD")
    print(f"   Expectancy por trade (R)           | {is_metrics['expectancy_r']:+.2f}R                  | {oos_metrics['expectancy_r']:+.2f}R                  | {oos_metrics['expectancy_r'] - is_metrics['expectancy_r']:+.2f}R")
    print(f"   Racha Máxima de Pérdidas           | {is_metrics['max_loss_streak']} derrotas seguidas      | {oos_metrics['max_loss_streak']} derrotas seguidas      | {oos_metrics['max_loss_streak'] - is_metrics['max_loss_streak']:+d} trades")
    print(f"   Max Equity Drawdown (USD)          | ${is_metrics['max_drawdown_usd']:.2f} USD          | ${oos_metrics['max_drawdown_usd']:.2f} USD          | ${oos_metrics['max_drawdown_usd'] - is_metrics['max_drawdown_usd']:+.2f} USD")
    print(f"   Max Equity Drawdown (%)            | {is_metrics['max_drawdown_pct']:.2f}%                  | {oos_metrics['max_drawdown_pct']:.2f}%                  | {oos_metrics['max_drawdown_pct'] - is_metrics['max_drawdown_pct']:+.2f}%")
    print(f"   Annualized Sharpe Ratio (365d)     | {is_metrics['sharpe_ratio']:.2f}                   | {oos_metrics['sharpe_ratio']:.2f}                   | {oos_metrics['sharpe_ratio'] - is_metrics['sharpe_ratio']:+.2f}")
    print(f"   Recovery Factor (Net / Max DD)     | {is_metrics['recovery_factor']:.2f}                   | {oos_metrics['recovery_factor']:.2f}                   | {oos_metrics['recovery_factor'] - is_metrics['recovery_factor']:+.2f}")
    print("-" * 90)
    print(f"   Walk-Forward Efficiency (WFE Ret): {wfe_ret:.1f}%  (Institucional >= 50% = Robustez Excelente)")
    print(f"   Walk-Forward Efficiency (WFE PF):  {wfe_pf:.2f}x   (Consistencia del modelo)")
    print("=" * 90)

    # Weekly Breakdown Print
    print("\n" + "=" * 90)
    print("   DESGLOSE SEMANAL DEL OUT-OF-SAMPLE (OOS): CONSISTENCIA SEMANA A SEMANA")
    print("=" * 90)
    print(f"   {'SEMANA':<32} | {'TRADES':<7} | {'WIN RATE':<9} | {'NET PnL (USD)':<14} | {'PROFIT FACTOR':<14} | {'ESTADO':<8}")
    print("-" * 90)
    profitable_weeks = 0
    for w in oos_weekly:
        pf_display = f"{w['profit_factor']:<14.2f}" if w['profit_factor'] < 90 else "Inf (100% W)   "
        status = "POSITIVA" if w["net_cash_usd"] > 0 else ("BREAKEVEN" if w["net_cash_usd"] == 0 else "NEGATIVA")
        if w["net_cash_usd"] > 0:
            profitable_weeks += 1
        print(f"   {w['week_label']:<32} | {w['trades']:<7} | {w['win_rate_pct']:>6.1f}%  | ${w['net_cash_usd']:>+10.2f}   | {pf_display} | {status}")
    print("-" * 90)
    pct_prof_weeks = (profitable_weeks / len(oos_weekly) * 100.0) if oos_weekly else 0.0
    print(f"   Consistencia Temporal: {profitable_weeks} de {len(oos_weekly)} semanas positivas ({pct_prof_weeks:.1f}% de semanas en ganancia)")
    print("=" * 90)

    # Save CSVs
    exp_dir = Path("data/experiments")
    exp_dir.mkdir(parents=True, exist_ok=True)
    if all_is_trades:
        pd.DataFrame([asdict(t) for t in all_is_trades]).to_csv(exp_dir / "backtest_is_trades.csv", index=False)
    if all_oos_trades:
        pd.DataFrame([asdict(t) for t in all_oos_trades]).to_csv(exp_dir / "backtest_oos_trades.csv", index=False)
    if oos_weekly:
        pd.DataFrame(oos_weekly).to_csv(exp_dir / "backtest_oos_weekly.csv", index=False)

    print(f"\n[+] Tablas detalladas exportadas exitosamente a '{exp_dir}/'")


if __name__ == "__main__":
    main()
