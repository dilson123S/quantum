import glob, sqlite3

for db in sorted(glob.glob("data/journal_bot_*.db")):
    conn = sqlite3.connect(db)
    try:
        r = conn.execute("SELECT value FROM runtime_state WHERE key='account_peak_equity'").fetchone()
        print(f"{db}: Peak Equity = {r[0] if r else 'None'}")
    except Exception as e:
        print(f"{db}: {e}")
    conn.close()
