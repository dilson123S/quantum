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

today_str = datetime.datetime.now().strftime('%Y-%m-%d')
now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

print(f"Resetting start_balance to {current_balance} for {today_str}...")

for db in glob.glob('data/*.db'):
    try:
        conn = sqlite3.connect(db)
        c = conn.cursor()
        
        # 1. Update runtime_state
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_state'")
        if c.fetchone():
            c.execute(
                "INSERT OR REPLACE INTO runtime_state (key, value, updated_at) VALUES ('account_peak_equity', ?, ?)",
                (current_balance, now_str)
            )
            
        # 2. Update daily_stats start_balance
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_stats'")
        if c.fetchone():
            c.execute(
                "UPDATE daily_stats SET start_balance=? WHERE date=?",
                (current_balance, today_str)
            )
            
        conn.commit()
        conn.close()
        print(f"  [OK] Reset {db}")
    except Exception as e:
        print(f"  [ERR] {db}: {e}")

print("Daily baseline reset complete.")
