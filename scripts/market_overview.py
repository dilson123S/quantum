import MetaTrader5 as mt5
import pandas as pd
import numpy as np

if not mt5.initialize():
    print("MT5 initialization failed")
    exit(1)

def analyze_symbol(sym):
    tick = mt5.symbol_info_tick(sym)
    if not tick:
        print(f"No tick for {sym}")
        return
    rates_h1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 60)
    rates_m5 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 60)
    if rates_h1 is None or rates_m5 is None:
        print(f"No rates for {sym}")
        return
    df_h1 = pd.DataFrame(rates_h1)
    df_m5 = pd.DataFrame(rates_m5)
    
    h1_ema50 = df_h1['close'].ewm(span=50).mean().iloc[-1]
    h1_close = df_h1['close'].iloc[-1]
    h1_trend = "ALCISTA (Bullish)" if h1_close > h1_ema50 else "BAJISTA (Bearish)"
    
    tr = np.maximum(df_m5['high'] - df_m5['low'], np.maximum(abs(df_m5['high'] - df_m5['close'].shift(1)), abs(df_m5['low'] - df_m5['close'].shift(1))))
    atr_m5 = tr.rolling(14).mean().iloc[-1]
    
    price = tick.bid
    swing_low_20 = df_m5['low'].iloc[-20:].min()
    swing_high_20 = df_m5['high'].iloc[-20:].max()
    
    print(f"=== {sym} ===")
    print(f"  Precio Bid/Ask: {tick.bid:.2f} / {tick.ask:.2f} (Spread: {tick.ask - tick.bid:.2f})")
    print(f"  Regimen H1: {h1_trend} [Precio: {h1_close:.2f} vs EMA-50: {h1_ema50:.2f}]")
    print(f"  Volatilidad M5 (ATR 14): {atr_m5:.2f}")
    print(f"  Estructura M5 (ultimas 20 velas): Soporte={swing_low_20:.2f} | Resistencia={swing_high_20:.2f}")
    print(f"  Distancia al Soporte: {price - swing_low_20:.2f} pts | Distancia a Resistencia: {swing_high_20 - price:.2f} pts")
    
    # Specific condition check for Boom/Crash
    if "Boom" in sym:
        dist_support = price - swing_low_20
        allowed_dist = 1.5 * atr_m5
        in_zone = dist_support <= allowed_dist
        print(f"  [Regla Boom BUY] Requiere H1 Alcista + Precio a <= {allowed_dist:.2f} pts del Soporte.")
        print(f"    -> H1 Alcista: {'SI' if h1_close > h1_ema50 else 'NO'}")
        print(f"    -> En Zona de Demanda: {'SI (Gatillo Activo)' if in_zone else f'NO (Faltan {dist_support - allowed_dist:.2f} pts de retroceso)'}")
    elif "Crash" in sym:
        dist_res = swing_high_20 - price
        allowed_dist = 1.5 * atr_m5
        in_zone = dist_res <= allowed_dist
        print(f"  [Regla Crash SELL] Requiere H1 Bajista + Precio a <= {allowed_dist:.2f} pts de Resistencia.")
        print(f"    -> H1 Bajista: {'SI' if h1_close < h1_ema50 else 'NO'}")
        print(f"    -> En Zona de Oferta: {'SI (Gatillo Activo)' if in_zone else f'NO (Faltan {dist_res - allowed_dist:.2f} pts de rebote)'}")
    print()

for s in ['Crash 1000 Index', 'Boom 1000 Index', 'Boom 500 Index', 'XAUUSD', 'USDJPY']:
    analyze_symbol(s)
