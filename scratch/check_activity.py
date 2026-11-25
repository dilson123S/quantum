import MetaTrader5 as mt5
import sqlite3
import glob
from datetime import datetime

print("=== MT5 CONNECTED ===")
if mt5.initialize():
    acc = mt5.account_info()
    if acc:
        print(f"Account: {acc.login} | Server: {acc.server} | Balance: {acc.balance} | Equity: {acc.equity}")
    
    positions = mt5.positions_get()
    print(f"Open positions: {len(positions) if positions else 0}")
    if positions:
        for p in positions:
            print(f"  {p.symbol} {'BUY' if p.type==0 else 'SELL'} {p.volume} @ {p.price_open} (SL: {p.sl}, TP: {p.tp}) | profit: {p.profit}")

    from_date = datetime(2026, 9, 25)
    to_date = datetime(2026, 10, 5)
    deals = mt5.history_deals_get(from_date, to_date)
    print(f"Deals since 2026-09-25: {len(deals) if deals else 0}")
    if deals:
        for d in deals[-15:]:
            t_str = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  Deal #{d.ticket} | {t_str} | {d.symbol} | type={d.type} | vol={d.volume} | profit={d.profit} | comment={d.comment}")
    mt5.shutdown()
else:
    print("MT5 failed to initialize")

print("\n=== SQLITE JOURNALS ===")
for f in sorted(glob.glob("data/*.db")):
    try:
        conn = sqlite3.connect(f)
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"\nDB: {f} (tables: {tables})")
        for t in tables:
            if "trade" in t.lower() or "journal" in t.lower() or "order" in t.lower():
                count = cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                latest = cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 1").fetchall()
                print(f"  Table '{t}': {count} rows")
                if latest:
                    print(f"    Latest: {latest[0]}")
        conn.close()
    except Exception as e:
        print(f"  Error reading {f}: {e}")
