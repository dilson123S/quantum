import sqlite3

for db_name in ["data/journal_bot_a.db", "data/journal_bot_b.db", "data/journal_bot_c.db", "data/journal_bot_d.db"]:
    try:
        con = sqlite3.connect(db_name)
        cur = con.cursor()
        print(f"\n==================== {db_name} ====================")
        rows = cur.execute("""
            SELECT rejection_reasons, count(*) 
            FROM signal_log 
            WHERE was_traded=0 
            GROUP BY rejection_reasons 
            ORDER BY count(*) DESC 
            LIMIT 8
        """).fetchall()
        for r in rows:
            print(f"Count {r[1]}: {r[0]}")
    except Exception as e:
        print(f"Error reading {db_name}: {e}")
