import glob
import sqlite3
import datetime
import MetaTrader5 as mt5

current_balance = 8779.77
if mt5.initialize():
    acc = mt5.account_info()
    if acc:
        current_balance = float(acc.balance)
    mt5.shutdown()

print(f"Resetting account_peak_equity baseline to {current_balance}...")
now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

for db in glob.glob('data/*.db'):
    try:
        conn = sqlite3.connect(db)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_state'")
        if c.fetchone():
            c.execute(
                "INSERT OR REPLACE INTO runtime_state (key, value, updated_at) VALUES ('account_peak_equity', ?, ?)",
                (current_balance, now_str)
            )
            conn.commit()
            print(f"Updated account_peak_equity in {db} to {current_balance}")
        conn.close()
    except Exception as e:
        print(f"Error updating {db}: {e}")

print("Equity baseline reset complete.")
