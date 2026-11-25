"""Comparative Institutional Backtest for Deriv Synthetic Fleet: WITH BREAKOUT vs WITHOUT BREAKOUT.

Simulates 25,000 Out-of-Sample (OOS) M5 bars across:
1. Volatilities: Volatility 75, Volatility 100, Volatility 50, Volatility 25, Volatility 10
2. Boom & Crash: Boom 1000, Crash 1000, Boom 500, Crash 500

Compares side-by-side:
- Configuration A: WITH BREAKOUT (Breakout models active)
- Configuration B: WITHOUT BREAKOUT (Pure SMC / Structural Rejection with absorption)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.constants import Direction
from strategy.synthetic_volatility import SyntheticVolatilityStrategy, SyntheticVolatilitySignal
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("FleetBreakoutComparison")


@dataclass
class FleetTrade:
    mode: str  # "WITH_BREAKOUT" or "NO_BREAKOUT"
    category: str  # "VOLATILITY" or "BOOM_CRASH"
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


def fetch_symbol_data(
    symbol: str, count_m5: int = 25000
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetch M5, M15, and H1 bars directly from Deriv MT5 terminal."""
    if not MT5_AVAILABLE:
        return None, None, None

    rates_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, count_m5)
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


def simulate_volatility_symbol(
    symbol: str,
    df_m5: pd.DataFrame,
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    enable_breakout: bool,
    enable_mean_reversion: bool,
    slippage_points: float,
    point: float,
    risk_per_trade_usd: float = 75.0,
) -> List[FleetTrade]:
    """Simulate volatility index with or without breakout."""
    mode_str = "WITH_BREAKOUT" if enable_breakout else "NO_BREAKOUT"
    strat = SyntheticVolatilityStrategy(
        enable_breakout=enable_breakout,
        enable_mean_reversion=enable_mean_reversion,
        target_rr=2.0,
        partial_rr=1.30,
        min_rr=1.40,
    )

    df_m5_calc = strat.calculate_indicators(df_m5)
    df_m15_calc = strat.calculate_indicators(df_m15) if df_m15 is not None else None
    df_h1_calc = strat.calculate_indicators(df_h1) if df_h1 is not None else None

    h1_times = df_h1_calc["time"].values if df_h1_calc is not None else None
    m15_times = df_m15_calc["time"].values if df_m15_calc is not None else None

    trades: List[FleetTrade] = []
    active_trade: Optional[Dict[str, Any]] = None
    bars_held = 0

    def calc_cash(pnl_r_val: float, risk_pts: float) -> Tuple[float, float, float, float]:
        gross = pnl_r_val * risk_per_trade_usd
        slip = (slippage_points / max(risk_pts, 0.0001)) * risk_per_trade_usd
        net = gross - slip
        return round(gross, 2), round(slip, 2), round(net, 2), round(net / risk_per_trade_usd, 2)

    for i in range(40, len(df_m5_calc)):
        curr_bar = df_m5_calc.iloc[i]
        c_open = float(curr_bar["open"])
        c_high = float(curr_bar["high"])
        c_low = float(curr_bar["low"])
        c_close = float(curr_bar["close"])
        bar_time_str = str(curr_bar["time"])

        if active_trade is not None:
            bars_held += 1
            direction = active_trade["direction"]
            entry = active_trade["entry"]
            sl = active_trade["current_sl"]
            tp = active_trade["tp"]
            partial_tp = active_trade["partial_tp"]
            initial_risk = active_trade["initial_risk"]
            has_partial = active_trade["has_partial"]

            hit_sl = (direction == "BUY" and c_low <= sl) or (direction == "SELL" and c_high >= sl)
            hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

            if hit_sl and hit_tp:
                if abs(c_open - sl) < abs(c_open - tp):
                    hit_tp = False
                else:
                    hit_sl = False

            if not has_partial:
                hit_partial = (direction == "BUY" and c_high >= partial_tp) or (direction == "SELL" and c_low <= partial_tp)
                if hit_partial:
                    active_trade["has_partial"] = True
                    be_level = entry + (initial_risk * 0.10) if direction == "BUY" else entry - (initial_risk * 0.10)
                    active_trade["current_sl"] = be_level

            peak_r = (c_high - entry) / initial_risk if direction == "BUY" else (entry - c_low) / initial_risk
            if peak_r >= 1.5:
                trail_sl = c_high - (initial_risk * 0.8) if direction == "BUY" else c_low + (initial_risk * 0.8)
                if direction == "BUY" and trail_sl > active_trade["current_sl"]:
                    active_trade["current_sl"] = trail_sl
                elif direction == "SELL" and trail_sl < active_trade["current_sl"]:
                    active_trade["current_sl"] = trail_sl

            time_exit = bars_held >= 24

            if hit_tp:
                pnl_r_raw = active_trade["target_rr"] if not has_partial else (active_trade["target_rr"] * 0.5 + 1.30 * 0.5)
                gc, sc, nc, nr = calc_cash(pnl_r_raw, initial_risk)
                trades.append(FleetTrade(
                    mode=mode_str, category="VOLATILITY", symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=tp, sl_price=sl, tp_price=tp,
                    pnl_r=nr, gross_cash=gc, slippage_cost=sc, net_cash=nc,
                    exit_reason="WIN_TP", strategy=active_trade["setup_model"]
                ))
                active_trade = None
                continue

            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r_raw = round(sl_diff / initial_risk, 2)
                if has_partial:
                    pnl_r_raw = 1.30 * 0.5 + pnl_r_raw * 0.5
                reason = "EXIT_BE_LOCK" if pnl_r_raw >= 0 else "LOSS_SL"
                gc, sc, nc, nr = calc_cash(pnl_r_raw, initial_risk)
                trades.append(FleetTrade(
                    mode=mode_str, category="VOLATILITY", symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=sl, sl_price=sl, tp_price=tp,
                    pnl_r=nr, gross_cash=gc, slippage_cost=sc, net_cash=nc,
                    exit_reason=reason, strategy=active_trade["setup_model"]
                ))
                active_trade = None
                continue

            elif time_exit:
                curr_gain = (c_close - entry) if direction == "BUY" else (entry - c_close)
                pnl_r_raw = round(curr_gain / initial_risk, 2)
                gc, sc, nc, nr = calc_cash(pnl_r_raw, initial_risk)
                trades.append(FleetTrade(
                    mode=mode_str, category="VOLATILITY", symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close, sl_price=sl, tp_price=tp,
                    pnl_r=nr, gross_cash=gc, slippage_cost=sc, net_cash=nc,
                    exit_reason="EXIT_TIME_EXPIRY", strategy=active_trade["setup_model"]
                ))
                active_trade = None
                continue

            continue

        # Look for Entry
        h1_slice = None
        if h1_times is not None:
            idx_h1 = int(np.searchsorted(h1_times, np.datetime64(curr_bar["time"]), side="right"))
            h1_slice = df_h1_calc.iloc[max(0, idx_h1 - 80):idx_h1] if idx_h1 > 0 else None

        m15_slice = None
        if m15_times is not None:
            idx_m15 = int(np.searchsorted(m15_times, np.datetime64(curr_bar["time"]), side="right"))
            m15_slice = df_m15_calc.iloc[max(0, idx_m15 - 80):idx_m15] if idx_m15 > 0 else None

        m5_slice = df_m5_calc.iloc[max(0, i - 60):i + 1]

        sig = strat.evaluate_volatility_setup(symbol, m5_slice, df_m15=m15_slice, df_h1=h1_slice, point=point)
        if sig is not None:
            risk_pts = abs(sig.entry_price - sig.sl_price)
            active_trade = {
                "direction": sig.direction.value,
                "entry": sig.entry_price,
                "current_sl": sig.sl_price,
                "tp": sig.tp_price,
                "partial_tp": sig.partial_tp_price,
                "initial_risk": risk_pts,
                "target_rr": sig.risk_reward,
                "has_partial": False,
                "entry_time": bar_time_str,
                "setup_model": sig.setup_model,
            }
            bars_held = 0

    return trades


def simulate_boom_crash_symbol(
    symbol: str,
    df_m5: pd.DataFrame,
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    breakout_mode: bool,
    slippage_points: float = 2.0,
    risk_per_trade_usd: float = 75.0,
) -> List[FleetTrade]:
    """Simulate Boom/Crash.
    - breakout_mode=True: Momentum breakout entry (buying after Donchian high breakout on Boom, selling after Donchian low breakout on Crash)
    - breakout_mode=False: Structural absorption at demand/supply zone with buyer/seller wick rejection.
    """
    mode_str = "WITH_BREAKOUT" if breakout_mode else "NO_BREAKOUT"
    is_boom = "BOOM" in symbol.upper()
    direction = "BUY" if is_boom else "SELL"

    strat = SpikeSniperStrategy(max_holding_bars=12, target_rr=2.0, min_rr=1.50, sl_buffer_atr=1.0)
    df_m5_calc = strat.calculate_indicators(df_m5)
    df_h1_calc = strat.calculate_indicators(df_h1) if df_h1 is not None else None
    df_m15_calc = strat.calculate_indicators(df_m15) if df_m15 is not None else None

    # Donchian 20 for breakout mode
    df_m5_calc["donchian_h20"] = df_m5_calc["high"].rolling(20).max().shift(1)
    df_m5_calc["donchian_l20"] = df_m5_calc["low"].rolling(20).min().shift(1)

    h1_times = df_h1_calc["time"].values if df_h1_calc is not None else None
    m15_times = df_m15_calc["time"].values if df_m15_calc is not None else None

    trades: List[FleetTrade] = []
    active_trade: Optional[Dict[str, Any]] = None
    bars_held = 0

    def calc_cash(pnl_r_val: float, risk_pts: float) -> Tuple[float, float, float, float]:
        gross = pnl_r_val * risk_per_trade_usd
        slip = (slippage_points / max(risk_pts, 0.0001)) * risk_per_trade_usd
        net = gross - slip
        return round(gross, 2), round(slip, 2), round(net, 2), round(net / risk_per_trade_usd, 2)

    for i in range(40, len(df_m5_calc)):
        curr_bar = df_m5_calc.iloc[i]
        c_open = float(curr_bar["open"])
        c_high = float(curr_bar["high"])
        c_low = float(curr_bar["low"])
        c_close = float(curr_bar["close"])
        c_atr = float(curr_bar.get("atr", 1.0))
        bar_time_str = str(curr_bar["time"])

        if active_trade is not None:
            bars_held += 1
            entry = active_trade["entry"]
            sl = active_trade["current_sl"]
            tp = active_trade["tp"]
            initial_risk = active_trade["initial_risk"]

            is_spike = bool(curr_bar.get("is_boom_spike", False)) if is_boom else bool(curr_bar.get("is_crash_spike", False))
            hit_sl = (direction == "BUY" and c_low <= sl) or (direction == "SELL" and c_high >= sl)
            hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

            if is_spike:
                hit_tp = True

            if hit_tp:
                actual_gain = abs(c_close - entry) if is_spike else abs(tp - entry)
                pnl_r_raw = max(1.2, actual_gain / initial_risk) if initial_risk > 0 else 2.0
                gc, sc, nc, nr = calc_cash(pnl_r_raw, initial_risk)
                trades.append(FleetTrade(
                    mode=mode_str, category="BOOM_CRASH", symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close if is_spike else tp,
                    sl_price=sl, tp_price=tp, pnl_r=nr, gross_cash=gc, slippage_cost=sc, net_cash=nc,
                    exit_reason="WIN_SPIKE" if is_spike else "WIN_TP", strategy=active_trade["setup_name"]
                ))
                active_trade = None
                continue

            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r_raw = round(sl_diff / initial_risk, 2)
                gc, sc, nc, nr = calc_cash(pnl_r_raw, initial_risk)
                trades.append(FleetTrade(
                    mode=mode_str, category="BOOM_CRASH", symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=sl, sl_price=sl, tp_price=tp,
                    pnl_r=nr, gross_cash=gc, slippage_cost=sc, net_cash=nc,
                    exit_reason="LOSS_SL", strategy=active_trade["setup_name"]
                ))
                active_trade = None
                continue

            elif bars_held >= 12:
                curr_gain = (c_close - entry) if direction == "BUY" else (entry - c_close)
                pnl_r_raw = round(curr_gain / initial_risk, 2)
                gc, sc, nc, nr = calc_cash(pnl_r_raw, initial_risk)
                trades.append(FleetTrade(
                    mode=mode_str, category="BOOM_CRASH", symbol=symbol, direction=direction,
                    entry_time=active_trade["entry_time"], exit_time=bar_time_str,
                    entry_price=entry, exit_price=c_close, sl_price=sl, tp_price=tp,
                    pnl_r=nr, gross_cash=gc, slippage_cost=sc, net_cash=nc,
                    exit_reason="EXIT_TIME_DECAY", strategy=active_trade["setup_name"]
                ))
                active_trade = None
                continue

            continue

        # Look for Entry Candidate
        if breakout_mode:
            # BREAKOUT MODE: Buy when breaking Donchian 20 High (Boom) or sell when breaking Donchian 20 Low (Crash)
            prev = df_m5_calc.iloc[i - 1]
            prev_close = float(prev["close"])
            dh = float(curr_bar["donchian_h20"])
            dl = float(curr_bar["donchian_l20"])

            sig_breakout = False
            if is_boom and prev_close < dh and c_close >= dh:
                sig_breakout = True
                sl_p = c_close - (c_atr * 1.5)
                tp_p = c_close + ((c_close - sl_p) * 2.0)
            elif not is_boom and prev_close > dl and c_close <= dl:
                sig_breakout = True
                sl_p = c_close + (c_atr * 1.5)
                tp_p = c_close - ((sl_p - c_close) * 2.0)

            if sig_breakout:
                risk_pts = abs(c_close - sl_p)
                active_trade = {
                    "direction": direction,
                    "entry": c_close,
                    "current_sl": sl_p,
                    "tp": tp_p,
                    "initial_risk": risk_pts,
                    "entry_time": bar_time_str,
                    "setup_name": "BOOM_CRASH_BREAKOUT_CHASE",
                }
                bars_held = 0

        else:
            # NO BREAKOUT MODE: Structural Demand/Supply Absorption
            h1_slice = None
            if h1_times is not None:
                idx_h1 = int(np.searchsorted(h1_times, np.datetime64(curr_bar["time"]), side="right"))
                h1_slice = df_h1_calc.iloc[max(0, idx_h1 - 80):idx_h1] if idx_h1 > 0 else None

            m15_slice = None
            if m15_times is not None:
                idx_m15 = int(np.searchsorted(m15_times, np.datetime64(curr_bar["time"]), side="right"))
                m15_slice = df_m15_calc.iloc[max(0, idx_m15 - 80):idx_m15] if idx_m15 > 0 else None

            m5_slice = df_m5_calc.iloc[max(0, i - 60):i + 1]
            sig = strat.detect_spike_setup(symbol, m5_slice, df_higher=m15_slice, df_h1=h1_slice)
            if sig is not None:
                active_trade = {
                    "direction": sig.direction.value,
                    "entry": sig.entry_price,
                    "current_sl": sig.sl_price,
                    "tp": sig.tp_price,
                    "initial_risk": sig.risk_points,
                    "entry_time": bar_time_str,
                    "setup_name": sig.setup_name,
                }
                bars_held = 0

    return trades


def calc_metrics(trades: List[FleetTrade], initial_balance: float = 10000.0) -> Dict[str, Any]:
    """Calculate institutional performance metrics."""
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "profit_factor": 0.0, "net_usd": 0.0, "max_drawdown_pct": 0.0,
            "max_loss_streak": 0, "expectancy_r": 0.0, "annualized_sharpe": 0.0
        }

    wins = [t for t in trades if t.net_cash > 0]
    losses = [t for t in trades if t.net_cash <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    total_trades = len(trades)
    win_rate = (win_count / total_trades) * 100.0

    gp = sum(t.net_cash for t in wins)
    gl = abs(sum(t.net_cash for t in losses))
    net_usd = gp - gl
    pf = round(gp / max(gl, 0.01), 2)

    avg_win_r = float(np.mean([t.pnl_r for t in wins])) if wins else 0.0
    avg_loss_r = abs(float(np.mean([t.pnl_r for t in losses]))) if losses else 0.0
    pw = win_count / total_trades
    pl = loss_count / total_trades
    exp_r = round((pw * avg_win_r) - (pl * avg_loss_r), 3)

    # Max streak
    max_losses = 0
    curr_l = 0
    for t in trades:
        if t.net_cash <= 0:
            curr_l += 1
            max_losses = max(max_losses, curr_l)
        else:
            curr_l = 0

    # Max Drawdown
    eq = [initial_balance]
    peak = initial_balance
    max_dd_pct = 0.0
    for t in trades:
        cur_eq = eq[-1] + t.net_cash
        eq.append(cur_eq)
        if cur_eq > peak:
            peak = cur_eq
        dd = (peak - cur_eq) / peak * 100.0 if peak > 0 else 0.0
        max_dd_pct = max(max_dd_pct, dd)

    # Sharpe
    tdf = pd.DataFrame([asdict(t) for t in trades])
    tdf["date"] = pd.to_datetime(tdf["exit_time"]).dt.date
    daily = tdf.groupby("date")["net_cash"].sum()
    sharpe = round(float((daily.mean() / daily.std()) * np.sqrt(365.0)), 2) if len(daily) > 5 and daily.std() > 0 else 0.0

    return {
        "trades": total_trades,
        "wins": win_count,
        "losses": loss_count,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": pf,
        "net_usd": round(net_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_loss_streak": max_losses,
        "expectancy_r": exp_r,
        "annualized_sharpe": sharpe,
    }


def run_fleet_comparison():
    if not MT5_AVAILABLE or not mt5.initialize():
        print("[ERROR] MetaTrader 5 not available.")
        return

    print("=" * 110)
    print("      QUANTUM-DERIV: INSTITUTIONAL COMPARISON - WITH BREAKOUT vs WITHOUT BREAKOUT")
    print("                          FULL SYNTHETIC FLEET (OOS 25,000 BARS)                  ")
    print("=" * 110)

    # 1. Volatility Indices configuration
    vol_symbols = [
        {"symbol": "Volatility 75 Index", "point": 0.01, "slippage": 16.0},
        {"symbol": "Volatility 100 Index", "point": 0.01, "slippage": 0.30},
        {"symbol": "Volatility 50 Index", "point": 0.0001, "slippage": 0.025},
        {"symbol": "Volatility 25 Index", "point": 0.001, "slippage": 0.35},
        {"symbol": "Volatility 10 Index", "point": 0.001, "slippage": 0.20},
    ]

    # 2. Boom & Crash configuration
    bc_symbols = [
        {"symbol": "Crash 1000 Index", "slippage": 2.0},
        {"symbol": "Boom 1000 Index", "slippage": 2.0},
        {"symbol": "Crash 500 Index", "slippage": 2.0},
        {"symbol": "Boom 500 Index", "slippage": 2.0},
    ]

    results: Dict[str, Any] = {"volatilities": {}, "boom_crash": {}, "fleet_totals": {}}
    fleet_with_breakout_trades: List[FleetTrade] = []
    fleet_no_breakout_trades: List[FleetTrade] = []

    # A. Process Volatility Indices
    for item in vol_symbols:
        sym = item["symbol"]
        pt = item["point"]
        slip = item["slippage"]
        print(f"\n[VOL] Processing {sym}...")
        df_m5, df_m15, df_h1 = fetch_symbol_data(sym, count_m5=25000)
        if df_m5 is None:
            print(f"  [WARN] Failed to fetch data for {sym}")
            continue

        # Mode A: WITH BREAKOUT (Breakout + Mean Reversion + SMC)
        t_with = simulate_volatility_symbol(
            sym, df_m5, df_m15, df_h1, enable_breakout=True, enable_mean_reversion=True,
            slippage_points=slip, point=pt
        )
        # Mode B: WITHOUT BREAKOUT (Pure SMC)
        t_without = simulate_volatility_symbol(
            sym, df_m5, df_m15, df_h1, enable_breakout=False, enable_mean_reversion=False,
            slippage_points=slip, point=pt
        )

        m_with = calc_metrics(t_with)
        m_without = calc_metrics(t_without)

        results["volatilities"][sym] = {
            "WITH_BREAKOUT": m_with,
            "NO_BREAKOUT": m_without,
        }
        fleet_with_breakout_trades.extend(t_with)
        fleet_no_breakout_trades.extend(t_without)

        print(f"  WITH BREAKOUT: Trades={m_with['trades']}, WR={m_with['win_rate_pct']}%, PF={m_with['profit_factor']}, Net=${m_with['net_usd']}, MaxDD={m_with['max_drawdown_pct']}%")
        print(f"  NO BREAKOUT:   Trades={m_without['trades']}, WR={m_without['win_rate_pct']}%, PF={m_without['profit_factor']}, Net=${m_without['net_usd']}, MaxDD={m_without['max_drawdown_pct']}%")

    # B. Process Boom & Crash Indices
    for item in bc_symbols:
        sym = item["symbol"]
        slip = item["slippage"]
        print(f"\n[BOOM/CRASH] Processing {sym}...")
        df_m5, df_m15, df_h1 = fetch_symbol_data(sym, count_m5=25000)
        if df_m5 is None:
            print(f"  [WARN] Failed to fetch data for {sym}")
            continue

        # Mode A: WITH BREAKOUT (Chasing Donchian Breakout)
        t_with = simulate_boom_crash_symbol(sym, df_m5, df_m15, df_h1, breakout_mode=True, slippage_points=slip)
        # Mode B: WITHOUT BREAKOUT (Absorption Demand/Supply Zones)
        t_without = simulate_boom_crash_symbol(sym, df_m5, df_m15, df_h1, breakout_mode=False, slippage_points=slip)

        m_with = calc_metrics(t_with)
        m_without = calc_metrics(t_without)

        results["boom_crash"][sym] = {
            "WITH_BREAKOUT": m_with,
            "NO_BREAKOUT": m_without,
        }
        fleet_with_breakout_trades.extend(t_with)
        fleet_no_breakout_trades.extend(t_without)

        print(f"  WITH BREAKOUT: Trades={m_with['trades']}, WR={m_with['win_rate_pct']}%, PF={m_with['profit_factor']}, Net=${m_with['net_usd']}, MaxDD={m_with['max_drawdown_pct']}%")
        print(f"  NO BREAKOUT:   Trades={m_without['trades']}, WR={m_without['win_rate_pct']}%, PF={m_without['profit_factor']}, Net=${m_without['net_usd']}, MaxDD={m_without['max_drawdown_pct']}%")

    # Fleet Totals
    total_with = calc_metrics(fleet_with_breakout_trades, initial_balance=50000.0)
    total_without = calc_metrics(fleet_no_breakout_trades, initial_balance=50000.0)

    results["fleet_totals"] = {
        "WITH_BREAKOUT": total_with,
        "NO_BREAKOUT": total_without,
    }

    Path("data").mkdir(exist_ok=True)
    with open("data/fleet_breakout_comparison.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    print("\n" + "=" * 110)
    print("                           FLEET PORTFOLIO TOTALS COMPARISON")
    print("=" * 110)
    print(f"WITH BREAKOUT: Trades={total_with['trades']}, WR={total_with['win_rate_pct']}%, PF={total_with['profit_factor']}, Net=${total_with['net_usd']}, MaxDD={total_with['max_drawdown_pct']}%, Sharpe={total_with['annualized_sharpe']}")
    print(f"NO BREAKOUT:   Trades={total_without['trades']}, WR={total_without['win_rate_pct']}%, PF={total_without['profit_factor']}, Net=${total_without['net_usd']}, MaxDD={total_without['max_drawdown_pct']}%, Sharpe={total_without['annualized_sharpe']}")
    print("=" * 110)
    print("Report saved to data/fleet_breakout_comparison.json")


if __name__ == "__main__":
    run_fleet_comparison()
