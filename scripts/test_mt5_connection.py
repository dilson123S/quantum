"""MT5 Terminal connection and diagnostic test script."""
import sys
import os

# Set standard output encoding to utf-8 if supported
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import MetaTrader5 as mt5
from datetime import datetime, timezone
import pandas as pd


def run_mt5_diagnostic():
    print("=" * 60)
    print("       QUANT TRADING BOT -- MT5 DIAGNOSTIC TEST")
    print("=" * 60)

    # 1. Initialize MT5
    print("\n[1/5] Inicializando MetaTrader 5...")
    if not mt5.initialize():
        error_code, error_msg = mt5.last_error()
        print(f"[ERROR] No se pudo conectar con MetaTrader 5: {error_msg} (Codigo {error_code})")
        print("\nConsejo:")
        print("1. Abre tu terminal MetaTrader 5 en Windows e inicia sesion en tu cuenta Demo.")
        print("2. Vuelve a ejecutar este script.")
        return False
    print("[OK] MetaTrader 5 inicializado correctamente.")

    # 2. Terminal Info
    print("\n[2/5] Comprobando estado del terminal...")
    term = mt5.terminal_info()
    if term:
        print(f"  * Conectado al servidor: {'SI' if term.connected else 'NO'}")
        print(f"  * Trading algoritmico permitido: {'SI' if term.trade_allowed else 'NO [!] (Habilitar en Herramientas > Opciones > Asesores Expertos)'}")
        print(f"  * Ruta del terminal: {term.path}")
        print(f"  * Broker / Compania: {term.company}")
    else:
        print("[!] No se pudo obtener informacion del terminal.")

    # 3. Account Info
    print("\n[3/5] Consultando informacion de cuenta...")
    account = mt5.account_info()
    if account:
        trade_mode_str = "DEMO" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL else "CONTEST"
        print(f"  * Cuenta: #{account.login}")
        print(f"  * Servidor: {account.server}")
        print(f"  * Moneda: {account.currency}")
        print(f"  * Balance: ${account.balance:,.2f}")
        print(f"  * Equity: ${account.equity:,.2f}")
        print(f"  * Margen libre: ${account.margin_free:,.2f}")
        print(f"  * Apalancamiento: 1:{account.leverage}")
        print(f"  * Trading permitido en cuenta: {'SI' if account.trade_allowed else 'NO'}")
        print(f"  * Tipo de cuenta: {trade_mode_str}")
    else:
        print("[ERROR] No se pudo obtener informacion de la cuenta.")

    # 4. Symbol & Market Data Verification
    symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    print(f"\n[4/5] Verificando cotizaciones y descarga de velas ({', '.join(symbols)})...")

    for symbol in symbols:
        selected = mt5.symbol_select(symbol, True)
        if not selected:
            print(f"  [!] {symbol}: No se pudo seleccionar en Market Watch.")
            continue

        sym_info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 5)

        if sym_info and tick and rates is not None and len(rates) > 0:
            spread_pts = (tick.ask - tick.bid) / (sym_info.point if sym_info.point > 0 else 0.00001)
            print(f"  [OK] {symbol:7s} | Bid: {tick.bid:<8.5f} | Ask: {tick.ask:<8.5f} | Spread: {spread_pts:4.1f} pts | Velas M15: {len(rates)} OK")
        else:
            print(f"  [!] {symbol}: Datos no disponibles o mercado cerrado.")

    # 5. Summary
    print("\n[5/5] Resumen:")
    print("=" * 60)
    if term and term.connected and account and account.trade_allowed:
        print("[EXITO] MetaTrader 5 esta conectado y listo para operar.")
        print("\nPara iniciar el bot en modo PAPER (simulacion en vivo sin ordenes reales):")
        print("  python main.py --mode paper")
        print("\nPara iniciar el bot en modo DEMO (abriendo ordenes en tu cuenta Demo):")
        print("  python main.py --mode demo")
    else:
        print("[!] Revisa las advertencias anteriores antes de iniciar en modo DEMO.")
    print("=" * 60)

    mt5.shutdown()
    return True


if __name__ == "__main__":
    run_mt5_diagnostic()
