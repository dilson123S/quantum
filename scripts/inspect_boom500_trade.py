import sqlite3
import json
import MetaTrader5 as mt5
from datetime import datetime, timedelta

def main():
    print("=== INSPECTING DERIV JOURNAL DB ===")
    conn = sqlite3.connect("data/journal_deriv.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    print("Tables:", tables)

    for t in tables:
        print(f"\n--- TABLE: {t} ---")
        cursor.execute(f"PRAGMA table_info({t})")
        cols = [c[1] for c in cursor.fetchall()]
        print("Cols:", cols)
        cursor.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 10")
        rows = cursor.fetchall()
        for r in rows:
            print(r)

    print("\n=== MT5 DEALS & ORDERS FOR BOOM 500 ===")
    if mt5.initialize():
        now = datetime.now()
        history_from = now - timedelta(days=3)
        deals = mt5.history_deals_get(history_from, now)
        if deals:
            boom_deals = [d for d in deals if "Boom 500" in d.symbol]
            print(f"Found {len(boom_deals)} deals for Boom 500 in last 3 days:")
            for d in boom_deals:
                print(d._asdict())
        else:
            print("No deals found in MT5 history.")
        mt5.shutdown()
    else:
        print("MT5 init failed")

if __name__ == "__main__":
    main()
