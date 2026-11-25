import sys
sys.path.insert(0, ".")
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime
from strategy.synthetic_spike import SpikeSniperStrategy, SpikeSignal
from strategy.synthetic_volatility import SyntheticVolatilityStrategy
from backtest_real_mt5 import fetch_mt5_data, run_symbol_backtest, calculate_metrics

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

symbols = [
    ("Boom 1000 Index", True, 0.0001),
    ("Crash 1000 Index", True, 0.0001),
    ("Boom 500 Index", True, 0.001),
    ("Crash 500 Index", True, 0.001),
    ("Volatility 75 Index", False, 0.01),
    ("Volatility 100 Index", False, 0.01),
]

print("=== DOWNLOADING MT5 RATES FOR 6 SYMBOLS (5000 BARS M5) ===")
cached_data = {}
for sym, is_bc, pt in symbols:
    m5, m15, h1 = fetch_mt5_data(sym, count_m5=5000, start_pos=0)
    cached_data[sym] = (m5, m15, h1, is_bc, pt)
    print(f"  {sym}: M5={len(m5) if m5 is not None else 0} bars")

mt5.shutdown()

print("\n=== PARAMETER SWEEP: RELAXING CONDITIONS ===")

configs = [
    {
        "name": "Strict (Actual)",
        "touch_atr": 0.80, "close_near_atr": 1.10, "min_wick": 0.25,
        "boom_rsi": 40.0, "crash_rsi": 60.0,
        "vol_breakout": False, "vol_mr": False
    },
    {
        "name": "Moderada (Equilibrada)",
        "touch_atr": 1.00, "close_near_atr": 1.30, "min_wick": 0.18,
        "boom_rsi": 48.0, "crash_rsi": 52.0,
        "vol_breakout": True, "vol_mr": False
    },
    {
        "name": "Relajada (Fluida)",
        "touch_atr": 1.20, "close_near_atr": 1.50, "min_wick": 0.12,
        "boom_rsi": 52.0, "crash_rsi": 48.0,
        "vol_breakout": True, "vol_mr": True
    },
    {
        "name": "Optima (Sniper + Frecuencia)",
        "touch_atr": 1.10, "close_near_atr": 1.35, "min_wick": 0.15,
        "boom_rsi": 50.0, "crash_rsi": 50.0,
        "vol_breakout": True, "vol_mr": False
    },
]

for cfg in configs:
    spike_strat = SpikeSniperStrategy(
        max_holding_bars=12,
        min_rr=1.40,
        target_rr=2.0,
        sl_buffer_atr=1.0,
        crash_rsi_overbought=cfg["crash_rsi"],
        boom_rsi_oversold=cfg["boom_rsi"],
        touch_atr=cfg["touch_atr"],
        close_near_atr=cfg["close_near_atr"],
        min_wick_ratio=cfg["min_wick"],
    )
    vol_strat = SyntheticVolatilityStrategy(
        ema_fast=20, ema_medium=50, ema_slow=200, atr_period=14,
        sl_buffer_atr=0.25, target_rr=2.0, partial_rr=1.30, min_rr=1.40,
        enable_mean_reversion=cfg["vol_mr"],
        enable_breakout=cfg["vol_breakout"],
    )

    all_trades = []
    days = 17.3 # 5000 M5 bars ~ 17.3 days

    for sym, (m5, m15, h1, is_bc, pt) in cached_data.items():
        if m5 is None or len(m5) < 100: continue
        trades = run_symbol_backtest(
            symbol=sym, df_m5=m5, df_m15=m15, df_h1=h1,
            is_boom_crash=is_bc,
            spike_strat=spike_strat if is_bc else None,
            vol_strat=vol_strat if not is_bc else None,
            risk_per_trade_usd=75.0, point=pt
        )
        all_trades.extend(trades)

    m = calculate_metrics(all_trades)
    t_day = len(all_trades) / days
    print(f"\n--- Config: {cfg['name']} ---")
    print(f"  Touch: {cfg['touch_atr']} | CloseNear: {cfg['close_near_atr']} | Wick: {cfg['min_wick']} | RSI: {cfg['boom_rsi']}/{cfg['crash_rsi']}")
    print(f"  Trades: {m['total_trades']:3d} ({t_day:.1f} trades/dia) | WR: {m['win_rate_pct']:5.1f}% | PF: {m['profit_factor']:.2f} | Net R: {m['net_r']:+6.1f}R | PnL: ${m['net_cash_usd']:+8.2f} USD | Max DD: ${m['max_drawdown_usd']:.2f} ({m['max_drawdown_pct']:.1f}%)")
