import sys
sys.path.insert(0, ".")
import MetaTrader5 as mt5
import pandas as pd
from strategy.synthetic_spike import SpikeSniperStrategy
from strategy.synthetic_volatility import SyntheticVolatilityStrategy
from backtest_real_mt5 import fetch_mt5_data, run_symbol_backtest, calculate_metrics

if not mt5.initialize():
    print("MT5 init failed", flush=True)
    sys.exit(1)

symbols = [
    ("Boom 1000 Index", True, 0.0001),
    ("Crash 1000 Index", True, 0.0001),
    ("Boom 500 Index", True, 0.001),
    ("Crash 500 Index", True, 0.001),
    ("Volatility 75 Index", False, 0.01),
    ("Volatility 100 Index", False, 0.01),
]

print("=== DESCARGANDO DATOS REALES DE DERIV MT5 (2,500 VELAS M5 = ~8.7 DIAS) ===", flush=True)
cached = {}
for sym, is_bc, pt in symbols:
    m5, m15, h1 = fetch_mt5_data(sym, count_m5=2500, start_pos=0)
    cached[sym] = (m5, m15, h1, is_bc, pt)
    print(f"  [OK] {sym}: {len(m5) if m5 is not None else 0} barras M5 descargadas", flush=True)
mt5.shutdown()

# Evaluemos la estrategia con condiciones relajadas:
# 1. Proximidad: touch_atr=1.00, close_near_atr=1.35
# 2. Mecha: min_wick_ratio=0.15 (rechazo del 15% en vez de 25%)
# 3. RSI: Boom oversold 48.0, Crash overbought 52.0
# 4. Volatility: Breakout habilitado (enable_breakout=True)

spike_relaxed = SpikeSniperStrategy(
    max_holding_bars=12,
    min_rr=1.40,
    target_rr=2.0,
    sl_buffer_atr=1.0,
    crash_rsi_overbought=50.0,
    boom_rsi_oversold=50.0,
    touch_atr=1.00,
    close_near_atr=1.35,
    min_wick_ratio=0.15,
)

vol_relaxed = SyntheticVolatilityStrategy(
    ema_fast=20, ema_medium=50, ema_slow=200, atr_period=14,
    sl_buffer_atr=0.25, target_rr=2.0, partial_rr=1.30, min_rr=1.40,
    enable_mean_reversion=False,
    enable_breakout=True,
)

print("\n=== EJECUTANDO BACKTEST CON CONDICIONES RELAJADAS EQUILIBRADAS ===", flush=True)
all_trades = []
days = 8.68

for sym, (m5, m15, h1, is_bc, pt) in cached.items():
    if m5 is None or len(m5) < 100: continue
    trades = run_symbol_backtest(
        symbol=sym, df_m5=m5, df_m15=m15, df_h1=h1,
        is_boom_crash=is_bc,
        spike_strat=spike_relaxed if is_bc else None,
        vol_strat=vol_relaxed if not is_bc else None,
        risk_per_trade_usd=75.0, point=pt
    )
    all_trades.extend(trades)
    m = calculate_metrics(trades)
    t_d = len(trades) / days
    print(f"  {sym:<20}: {len(trades):2d} trades ({t_d:.1f}/dia) | WR: {m['win_rate_pct']:5.1f}% | PF: {m['profit_factor']:.2f} | Net R: {m['net_r']:+5.1f}R | PnL: ${m['net_cash_usd']:+7.2f} USD | Max DD: ${m['max_drawdown_usd']:.2f}", flush=True)

tot_m = calculate_metrics(all_trades)
tot_td = len(all_trades) / days
print("\n" + "=" * 70, flush=True)
print(f"   RESULTADO GLOBAL PORTAFOLIO SINTETICO RELAJADO (6 SIMBOLOS)", flush=True)
print("=" * 70, flush=True)
print(f"   Periodo analizado:      Ultimos {days:.1f} dias continuos en vivo (24/7)", flush=True)
print(f"   Total Operaciones:      {tot_m['total_trades']} trades ({tot_td:.1f} trades/dia | ~{tot_td*30:.0f} trades/mes)", flush=True)
print(f"   Ganadas / Perdidas:     {tot_m['wins']} / {tot_m['losses']}", flush=True)
print(f"   Win Rate Global:        {tot_m['win_rate_pct']:.2f}%", flush=True)
print(f"   Profit Factor:          {tot_m['profit_factor']:.2f}", flush=True)
print(f"   Retorno Neto (R):       {tot_m['net_r']:+.2f}R", flush=True)
print(f"   Retorno Neto (USD):     ${tot_m['net_cash_usd']:+.2f} USD", flush=True)
print(f"   Drawdown Maximo:        ${tot_m['max_drawdown_usd']:.2f} ({tot_m['max_drawdown_pct']:.2f}%)", flush=True)
print(f"   Esperanza Matematica:   {tot_m['expectancy_r']:+.2f}R por trade", flush=True)
print("=" * 70, flush=True)
