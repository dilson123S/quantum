import glob
import sqlite3
import datetime
import MetaTrader5 as mt5

print("=== MT5 XAUUSD SYMBOL INFO ===")
if mt5.initialize():
    s_info = mt5.symbol_info("XAUUSD")
    if s_info:
        print(f"XAUUSD spread={s_info.spread}, bid={s_info.bid}, ask={s_info.ask}, trade_mode={s_info.trade_mode}")
        tick = mt5.symbol_info_tick("XAUUSD")
        if tick:
            print(f"Tick time: {datetime.datetime.fromtimestamp(tick.time)}")
    else:
        print("XAUUSD not found in MT5!")

print("\n=== GOLD DATABASES TRADES ===")
for db in sorted(glob.glob("data/journal_bot_*.db") + glob.glob("data/journal_*.db")):
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "trades" in tables:
        count = cursor.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        cursor.execute("SELECT * FROM trades LIMIT 1")
        cols = [d[0] for d in cursor.description]
        last = cursor.execute("SELECT * FROM trades ORDER BY rowid DESC LIMIT 1").fetchone() if count > 0 else None
        last_dict = dict(zip(cols, last)) if last else None
        print(f"\n{db} ({count} trades):")
        if last_dict:
            # print selected fields
            summary = {k: last_dict[k] for k in ['id', 'ticket', 'symbol', 'direction', 'state', 'status', 'profit', 'entry_time', 'open_time', 'created_at'] if k in last_dict}
            print(f"   Last trade: {summary}")
            
    if "signal_log" in tables:
        sig_count = cursor.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
        cursor.execute("SELECT * FROM signal_log LIMIT 1")
        cols = [d[0] for d in cursor.description]
        last_sig = cursor.execute("SELECT * FROM signal_log ORDER BY rowid DESC LIMIT 2").fetchall() if sig_count > 0 else []
        print(f"  signal_log ({sig_count} signals):")
        for s in last_sig:
            s_dict = dict(zip(cols, s))
            sig_summary = {k: s_dict[k] for k in ['symbol', 'direction', 'total_score', 'classification', 'was_traded', 'rejection_reasons', 'created_at'] if k in s_dict}
            print(f"    {sig_summary}")
