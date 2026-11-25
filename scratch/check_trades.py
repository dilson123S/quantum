import glob
import sqlite3
import os
import datetime
import MetaTrader5 as mt5

print("--- CHECKING MT5 DEALS & ORDERS ---")
if mt5.initialize():
    # Try fetching without filter or from long ago
    deals = mt5.history_deals_get(datetime.datetime(2020, 1, 1), datetime.datetime.now() + datetime.timedelta(days=2))
    print(f"MT5 total history deals: {len(deals) if deals else 0}")
    if deals:
        for d in deals[-5:]:
            print(f"Deal: ticket={d.ticket}, sym={d.symbol}, type={d.type}, vol={d.volume}, price={d.price}, profit={d.profit}, comment={d.comment}, time={datetime.datetime.fromtimestamp(d.time)}")

    orders = mt5.history_orders_get(datetime.datetime(2020, 1, 1), datetime.datetime.now() + datetime.timedelta(days=2))
    print(f"MT5 total history orders: {len(orders) if orders else 0}")
    if orders:
        for o in orders[-5:]:
            print(f"Order: ticket={o.ticket}, sym={o.symbol}, type={o.type}, state={o.state}, comment={o.comment}, time={datetime.datetime.fromtimestamp(o.time_setup)}")
            
    positions = mt5.positions_get()
    print(f"MT5 current open positions: {len(positions) if positions else 0}")
    if positions:
        for p in positions:
            print(f"Open pos: ticket={p.ticket}, sym={p.symbol}, vol={p.volume}, open_price={p.price_open}, curr={p.price_current}, profit={p.profit}, comment={p.comment}")

print("\n--- CHECKING SQLITE DATABASES ---")
for db in sorted(glob.glob("data/*.db")):
    try:
        conn = sqlite3.connect(db)
        cursor = conn.cursor()
        tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"\nDB: {db} (tables: {tables})")
        for tbl in tables:
            count = cursor.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            if count > 0:
                print(f"  Table {tbl}: {count} rows")
                last_rows = cursor.execute(f"SELECT * FROM {tbl} ORDER BY rowid DESC LIMIT 3").fetchall()
                cols = [d[0] for d in cursor.description]
                for r in last_rows:
                    print(f"    {tbl} row: {dict(zip(cols, r))}")
    except Exception as e:
        print(f"  Error reading {db}: {e}")
