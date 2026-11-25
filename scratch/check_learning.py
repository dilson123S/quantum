import sqlite3
import glob

print("=== CHECKING PATTERN MEMORY IN ALL DATABASES ===")
for f in sorted(glob.glob("data/*.db")):
    conn = sqlite3.connect(f)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "pattern_memory" in tables:
        count = cur.execute("SELECT count(*) FROM pattern_memory").fetchone()[0]
        print(f"{f}: {count} rows in pattern_memory")
        if count > 0:
            cols = [d[0] for d in cur.execute("SELECT * FROM pattern_memory LIMIT 1").description]
            print(f"  Columns: {cols}")
            rows = cur.execute("SELECT * FROM pattern_memory LIMIT 5").fetchall()
            for r in rows:
                print(f"  {dict(zip(cols, r))}")
    conn.close()
