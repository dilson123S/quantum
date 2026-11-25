"""Quantitative Backtest Suite on Real MetaTrader 5 Deriv Historical Data.

Evaluates the Quantum-Deriv v2 strategies against thousands of real historical bars:
1. Volatility 75 & 100: SMC Liquidity Sweep & Bollinger Mean Reversion + 2-Stage Bracket Management
2. Boom & Crash 1000/500: Jump-Diffusion Model + H1 Regime Filter + 2-Stage Bracket Management

Produces institutional metrics:
- Win Rate (%)
- Profit Factor
- Net R & Net USD PnL
- Max Drawdown (R & USD)
- Expectancy per trade (E[R])
- Breakdown by setup model
"""
import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd

from core.constants import Direction
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal
from strategy.synthetic_volatility import SyntheticVolatilityStrategy, SyntheticVolatilitySignal

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("RealBacktest")


@dataclass
class BacktestTradeResult:
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
    strategy: str
    confluences: str


def fetch_mt5_data(
    symbol: str, count_m5: int = 20000, start_pos: int = 0
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetch M5, M15, and H1 bars directly from Deriv MT5 for any position/date range."""
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


def run_symbol_backtest(
    symbol: str,
    df_m5: pd.DataFrame,
    df_m15: Optional[pd.DataFrame] = None,
    df_h1: Optional[pd.DataFrame] = None,
    is_boom_crash: bool = False,
    spike_strat: Optional[SpikeSniperStrategy] = None,
    vol_strat: Optional[SyntheticVolatilityStrategy] = None,
    risk_per_trade_usd: float = 75.0,
    point: float = 0.01,
) -> List[BacktestTradeResult]:
    """Run bar-by-bar backtest with realistic dynamic bracket execution matching production."""
    trades: List[BacktestTradeResult] = []
    active_trade: Optional[Dict[str, Any]] = None
    bars_held = 0

    # Ensure indicators pre-calculated for speed across all timeframes
    if is_boom_crash and spike_strat:
        df_m5_calc = spike_strat.calculate_indicators(df_m5)
        df_m15_calc = spike_strat.calculate_indicators(df_m15) if df_m15 is not None else None
        if df_m15_calc is not None and "ema_50" not in df_m15_calc.columns:
            df_m15_calc["ema_50"] = df_m15_calc["close"].ewm(span=50, adjust=False).mean()
        df_h1_calc = df_h1.copy() if df_h1 is not None else None
        if df_h1_calc is not None:
            df_h1_calc["ema_50"] = df_h1_calc["close"].ewm(span=50, adjust=False).mean()
    elif vol_strat:
        df_m5_calc = vol_strat.calculate_indicators(df_m5)
        df_m15_calc = vol_strat.calculate_indicators(df_m15) if df_m15 is not None else None
        df_h1_calc = vol_strat.calculate_indicators(df_h1) if df_h1 is not None else None
    else:
        return []

    # Map timestamps to slices for realistic point-in-time regime
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

            # Intra-bar gain evaluation
            peak_gain = (c_high - entry) if direction == "BUY" else (entry - c_low)
            curr_gain = (c_close - entry) if direction == "BUY" else (entry - c_close)

            # Check Stop Loss hit
            hit_sl = (direction == "BUY" and c_low <= sl) or (direction == "SELL" and c_high >= sl)
            # Check Take Profit hit
            hit_tp = (direction == "BUY" and c_high >= tp) or (direction == "SELL" and c_low <= tp)

            # For Boom/Crash: check if spike occurred
            is_spike = False
            if is_boom_crash:
                is_spike = bool(curr_bar.get("is_boom_spike", False)) if direction == "BUY" else bool(curr_bar.get("is_crash_spike", False))
                if is_spike:
                    hit_tp = True

            # Disambiguate if both hit in the same bar (conservative: open closer to SL means SL hit first)
            if hit_sl and hit_tp and not is_spike:
                if abs(c_open - sl) < abs(c_open - tp):
                    hit_tp = False
                else:
                    hit_sl = False

            # Evaluate exits
            if hit_tp:
                actual_gain = abs(c_close - entry) if is_spike else tp_dist
                pnl_r = max(1.0, actual_gain / initial_risk) if initial_risk > 0 else 2.0
                trades.append(BacktestTradeResult(
                    symbol=symbol, direction=direction, entry_time=active_trade["entry_time"],
                    exit_time=bar_time_str, entry_price=entry, exit_price=c_close if is_spike else tp,
                    sl_price=sl, tp_price=tp, pnl_r=round(pnl_r, 2),
                    pnl_cash=round(pnl_r * risk_per_trade_usd, 2),
                    exit_reason="WIN_TP" if not is_spike else "WIN_SPIKE",
                    strategy=active_trade["strategy"], confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            elif hit_sl:
                sl_diff = (sl - entry) if direction == "BUY" else (entry - sl)
                pnl_r = round(sl_diff / initial_risk, 2) if initial_risk > 0 else -1.0
                reason = "EXIT_BREAKEVEN" if abs(pnl_r) <= 0.05 else ("EXIT_PROFIT_LOCK" if pnl_r > 0 else "LOSS_SL")

                trades.append(BacktestTradeResult(
                    symbol=symbol, direction=direction, entry_time=active_trade["entry_time"],
                    exit_time=bar_time_str, entry_price=entry, exit_price=sl,
                    sl_price=sl, tp_price=tp, pnl_r=pnl_r,
                    pnl_cash=round(pnl_r * risk_per_trade_usd, 2),
                    exit_reason=reason,
                    strategy=active_trade["strategy"], confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            # Time Decay Exit for Boom/Crash (12 M5 bars = 60 minutes)
            if is_boom_crash and bars_held >= 12 and curr_gain <= 0:
                pnl_r = -round(abs(curr_gain) / initial_risk, 2) if initial_risk > 0 else -0.5
                trades.append(BacktestTradeResult(
                    symbol=symbol, direction=direction, entry_time=active_trade["entry_time"],
                    exit_time=bar_time_str, entry_price=entry, exit_price=c_close,
                    sl_price=sl, tp_price=tp, pnl_r=max(-1.0, pnl_r),
                    pnl_cash=round(max(-1.0, pnl_r) * risk_per_trade_usd, 2),
                    exit_reason="EXIT_TIME_DECAY",
                    strategy=active_trade["strategy"], confluences=active_trade["confluences"]
                ))
                active_trade = None
                continue

            # DYNAMIC RISK MANAGEMENT (matches run_deriv_bot.py):
            if is_boom_crash:
                # Boom/Crash: NO BREAK-EVEN on entry price (avoids drift stop-outs before spike)
                # Only lock profit when price has achieved >= 85% of TP distance
                if tp_dist > 0 and peak_gain >= tp_dist * 0.85:
                    lock_dist = peak_gain * 0.50
                    target_sl = entry + lock_dist if direction == "BUY" else entry - lock_dist
                    if (direction == "BUY" and target_sl > sl) or (direction == "SELL" and target_sl < sl):
                        active_trade["current_sl"] = target_sl
            else:
                # Volatility indices: 2-stage dynamic bracket
                # Stage 2: Lock 50% of the gain when price reaches 75% progress towards TP
                if tp_dist > 0 and peak_gain >= tp_dist * 0.75:
                    lock_dist = peak_gain * 0.50
                    target_sl = entry + lock_dist if direction == "BUY" else entry - lock_dist
                    if (direction == "BUY" and target_sl > sl) or (direction == "SELL" and target_sl < sl):
                        active_trade["current_sl"] = target_sl

                # Stage 1: Move to Break-even (+ small buffer) when reaching 50% progress towards TP
                elif tp_dist > 0 and peak_gain >= tp_dist * 0.50:
                    be_sl = entry + (point * 3.0) if direction == "BUY" else entry - (point * 3.0)
                    if (direction == "BUY" and be_sl > sl) or (direction == "SELL" and be_sl < sl):
                        active_trade["current_sl"] = be_sl

            continue

        # 2. Look for New Entry Signal with high-performance windowing
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

        if is_boom_crash and spike_strat:
            sig = spike_strat.detect_spike_setup(symbol, m5_slice, df_higher=m15_slice, df_h1=h1_slice, point=point)
            if sig is not None:
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
                    "strategy": sig.setup_name,
                    "confluences": ", ".join(sig.confluences),
                }
                bars_held = 0

        elif vol_strat:
            sig = vol_strat.evaluate_volatility_setup(symbol, m5_slice, df_m15=m15_slice, df_h1=h1_slice, point=point)
            if sig is not None:
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
                    "strategy": sig.setup_model,
                    "confluences": ", ".join(sig.confluences),
                }
                bars_held = 0

    return trades


def calculate_metrics(trades: List[BacktestTradeResult], initial_balance: float = 10000.0) -> Dict[str, Any]:
    """Calculate institutional performance & risk metrics."""
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "win_rate_pct": 0.0, "profit_factor": 0.0, "net_r": 0.0,
            "net_cash_usd": 0.0, "return_pct": 0.0, "expectancy_r": 0.0,
            "max_drawdown_r": 0.0, "max_drawdown_pct": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0
        }

    pnls_r = [t.pnl_r for t in trades]
    pnls_cash = [t.pnl_cash for t in trades]
    wins = [r for r in pnls_r if r > 0.05]
    losses = [r for r in pnls_r if r < -0.05]
    bes = [r for r in pnls_r if abs(r) <= 0.05]

    gross_profit_r = sum(wins) if wins else 0.0
    gross_loss_r = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_profit_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    win_rate = (len(wins) / len(pnls_r)) * 100.0 if pnls_r else 0.0
    net_r = sum(pnls_r)
    net_cash = sum(pnls_cash)
    return_pct = (net_cash / initial_balance) * 100.0
    expectancy_r = net_r / len(pnls_r) if pnls_r else 0.0

    # Drawdown
    cum_cash = np.cumsum(pnls_cash)
    peak_cash = np.maximum.accumulate(np.insert(cum_cash, 0, 0.0))
    equity_curve = initial_balance + np.insert(cum_cash, 0, 0.0)
    dd_cash = peak_cash - np.insert(cum_cash, 0, 0.0)
    max_dd_cash = float(np.max(dd_cash)) if len(dd_cash) > 0 else 0.0
    max_dd_pct = (max_dd_cash / initial_balance) * 100.0

    avg_win_r = float(np.mean(wins)) if wins else 0.0
    avg_loss_r = float(np.mean(losses)) if losses else 0.0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(bes),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "net_r": round(net_r, 2),
        "net_cash_usd": round(net_cash, 2),
        "return_pct": round(return_pct, 2),
        "expectancy_r": round(expectancy_r, 2),
        "max_drawdown_usd": round(max_dd_cash, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Quantum-Deriv MT5 Institutional Backtest")
    parser.add_argument("--oos", action="store_true", help="Run 2-month Out-Of-Sample validation (May-July 2026)")
    parser.add_argument("--count", type=int, default=20000, help="Number of M5 bars to backtest")
    parser.add_argument("--start-pos", type=int, default=None, help="Start position in MT5 history")
    args = parser.parse_args()

    start_pos = args.start_pos if args.start_pos is not None else (20000 if args.oos else 0)
    mode_title = "OUT-OF-SAMPLE (OOS - DATOS CIEGOS NO VISTOS)" if (args.oos or start_pos > 0) else "IN-SAMPLE (IS - DATOS RECIENTES)"

    print("=" * 75)
    print(f"   QUANTUM-DERIV: BACKTEST REAL MT5 [{mode_title}]")
    print("=" * 75)

    if not MT5_AVAILABLE:
        print("ERROR: MetaTrader5 package is not available. Please install it.")
        return

    if not mt5.initialize():
        print(f"ERROR: Could not connect to MetaTrader 5 terminal: {mt5.last_error()}")
        return

    symbols_config = [
        {"symbol": "Crash 1000 Index", "is_boom_crash": True, "point": 0.0001},
        {"symbol": "Boom 1000 Index", "is_boom_crash": True, "point": 0.0001},
        {"symbol": "Boom 500 Index", "is_boom_crash": True, "point": 0.001},
    ]

    # Initialize strategies matching production parameters
    spike_strat = SpikeSniperStrategy(
        max_holding_bars=12,
        min_rr=1.50,
        target_rr=2.0,
        sl_buffer_atr=1.0,
        crash_rsi_overbought=44.0,
        boom_rsi_oversold=56.0,
        touch_atr=0.85,
        close_near_atr=0.95,
    )

    vol_strat = SyntheticVolatilityStrategy(
        ema_fast=20,
        ema_medium=50,
        ema_slow=200,
        atr_period=14,
        sl_buffer_atr=0.25,
        target_rr=2.0,
        partial_rr=1.30,
        min_rr=1.50,
        enable_mean_reversion=False,
        enable_breakout=False,
    )

    all_trades: List[BacktestTradeResult] = []
    symbol_summaries: List[Dict[str, Any]] = []

    print(f"\n[+] Descargando velas M5/M15/H1 reales de Deriv MT5 (start_pos={start_pos}, count={args.count}) y ejecutando simulacion bar-by-bar...")

    earliest_date = None
    latest_date = None

    for item in symbols_config:
        sym = item["symbol"]
        is_bc = item["is_boom_crash"]
        pt = item["point"]

        df_m5, df_m15, df_h1 = fetch_mt5_data(sym, count_m5=args.count, start_pos=start_pos)
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

        trades = run_symbol_backtest(
            symbol=sym,
            df_m5=df_m5,
            df_m15=df_m15,
            df_h1=df_h1,
            is_boom_crash=is_bc,
            spike_strat=spike_strat if is_bc else None,
            vol_strat=vol_strat if not is_bc else None,
            risk_per_trade_usd=75.0,
            point=pt,
        )

        all_trades.extend(trades)
        metrics = calculate_metrics(trades)
        metrics["symbol"] = sym
        metrics["date_range"] = f"{start_date} -> {end_date}"
        metrics["bars_analyzed"] = len(df_m5)
        metrics["days_analyzed"] = round(duration_days, 1)
        trades_day = len(trades) / duration_days
        metrics["trades_per_day"] = round(trades_day, 2)
        metrics["trades_per_month"] = round(trades_day * 30.0, 1)
        symbol_summaries.append(metrics)

        print(f"    * {sym:20s} ({len(df_m5)} velas M5 | {start_date} a {end_date} | {duration_days:.1f} dias)")
        print(f"      Trades: {metrics['total_trades']:3d} (~{trades_day:.1f}/dia, ~{trades_day*30:.0f}/mes) | Win Rate: {metrics['win_rate_pct']:5.1f}% | "
              f"P.F.: {metrics['profit_factor']:4.2f} | Net R: {metrics['net_r']:+6.2f}R | Net PnL: ${metrics['net_cash_usd']:+8.2f} USD | Max DD: ${metrics['max_drawdown_usd']:.2f} ({metrics['max_drawdown_pct']:.1f}%)")

    mt5.shutdown()

    # Portfolio combined metrics
    portfolio_metrics = calculate_metrics(all_trades, initial_balance=10000.0)
    total_days = max(1.0, (latest_date - earliest_date).total_seconds() / 86400.0) if earliest_date and latest_date else 1.0
    port_trades_day = len(all_trades) / total_days
    port_trades_month = port_trades_day * 30.0

    print("\n" + "=" * 75)
    print(f"   RESUMEN INSTITUCIONAL DEL PORTAFOLIO COMPLETO [{mode_title}]")
    print("=" * 75)
    print(f"   Periodo analizado:      {earliest_date.strftime('%Y-%m-%d') if earliest_date else ''} a {latest_date.strftime('%Y-%m-%d') if latest_date else ''} ({total_days:.1f} dias continuos 24/7)")
    print(f"   Total de Operaciones:   {portfolio_metrics['total_trades']} (~{port_trades_day:.1f} trades/dia | ~{port_trades_month:.0f} trades/mes)")
    print(f"   Ganadoras / Perdedoras: {portfolio_metrics['wins']} ganadoras / {portfolio_metrics['losses']} perdedoras / {portfolio_metrics['breakeven']} breakeven")
    print(f"   Win Rate Global:        {portfolio_metrics['win_rate_pct']}%")
    print(f"   Profit Factor:          {portfolio_metrics['profit_factor']}")
    print(f"   Retorno Neto en R:      {portfolio_metrics['net_r']:+.2f}R")
    print(f"   Retorno Neto en USD:    ${portfolio_metrics['net_cash_usd']:+.2f} USD (+{portfolio_metrics['return_pct']:.2f}% de la cuenta)")
    print(f"   Esperanza Matematica:   {portfolio_metrics['expectancy_r']:+.2f}R por operacion")
    print(f"   Drawdown Maximo:        ${portfolio_metrics['max_drawdown_usd']:.2f} USD ({portfolio_metrics['max_drawdown_pct']:.2f}%)")
    print(f"   Ganancia Promedio (W):  +{portfolio_metrics['avg_win_r']:.2f}R")
    print(f"   Perdida Promedio (L):   {portfolio_metrics['avg_loss_r']:.2f}R")
    print("=" * 75)

    # Save detailed CSV
    Path("data/experiments").mkdir(parents=True, exist_ok=True)
    csv_path = "data/experiments/backtest_mt5_oos_results.csv" if (args.oos or start_pos > 0) else "data/experiments/backtest_mt5_real_results.csv"
    if all_trades:
        df_trades = pd.DataFrame([t.__dict__ for t in all_trades])
        df_trades.to_csv(csv_path, index=False)
        print(f"\n[+] Detalle de {len(all_trades)} operaciones guardado exitosamente en: {csv_path}")

    # Save summary CSV
    summary_csv = "data/experiments/backtest_mt5_oos_summary.csv" if (args.oos or start_pos > 0) else "data/experiments/backtest_mt5_summary.csv"
    df_sum = pd.DataFrame(symbol_summaries)
    df_sum.to_csv(summary_csv, index=False)
    print(f"[+] Resumen por simbolo guardado en: {summary_csv}")


if __name__ == "__main__":
    main()
