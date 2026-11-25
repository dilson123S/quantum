import glob
import sqlite3
import os
import datetime
import MetaTrader5 as mt5

print("=== 1. ULTIMOS TRADES EN BASES DE DATOS DE ORO ===")
for db in sorted(glob.glob("data/journal_bot_*.db")):
    try:
        conn = sqlite3.connect(db)
        cursor = conn.cursor()
        tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "trades" in tables:
            count = cursor.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            print(f"{db}: {count} trades")
            if count > 0:
                last = cursor.execute("SELECT ticket, symbol, direction, entry_price, open_time, status, profit FROM trades ORDER BY rowid DESC LIMIT 1").fetchone()
                print(f"   Ultimo: {last}")
    except Exception as e:
        print(f"Error {db}: {e}")

print("\n=== 2. ESTADO DEL BROKER PARA XAUUSD ===")
if mt5.initialize():
    symbol = "XAUUSD"
    s_info = mt5.symbol_info(symbol)
    if s_info:
        print(f"Symbol: {symbol}")
        print(f"  Visible: {s_info.visible}")
        print(f"  Trade Mode: {s_info.trade_mode} (0=disabled, 4=full)")
        print(f"  Spread: {s_info.spread} points")
        print(f"  Bid: {s_info.bid}, Ask: {s_info.ask}")
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            print(f"  Tick time: {datetime.datetime.fromtimestamp(tick.time)} (UTC: {datetime.datetime.utcfromtimestamp(tick.time) if hasattr(datetime.datetime, 'utcfromtimestamp') else 'N/A'})")
    else:
        print(f"Symbol {symbol} not found!")

print("\n=== 3. ULTIMAS LINEAS DE LOGS DE BOT-A, B, C, D, F, G ===")
log_files = [
    "logs/quanttradingbot-golddemoa.log",
    "logs/quanttradingbot-golddemob.log",
    "logs/quanttradingbot-golddemoc.log",
    "logs/quanttradingbot-golddemod.log",
    "logs/quanttradingbot-golddemof.log",
    "logs/quanttradingbot-alchemistg.log",
]
for lf in log_files:
    if os.path.exists(lf):
        print(f"\n--- {lf} ---")
        with open(lf, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            for line in lines[-12:]:
                print(line.rstrip())
    else:
        print(f"\n--- {lf} NOT FOUND ---")
