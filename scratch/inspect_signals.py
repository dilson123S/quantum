import sqlite3

con = sqlite3.connect('data/journal_bot_a.db')
cur = con.cursor()
rows = cur.execute('''
    SELECT signal_id, direction, total_score, created_at, rejection_reasons 
    FROM signal_log 
    WHERE rejection_reasons LIKE "%MARKET_QUALITY_FILTER%" 
    ORDER BY id DESC 
    LIMIT 10
''').fetchall()

for r in rows:
    print(f"[{r[3]}] {r[1]} Score: {r[2]} | Rejections: {r[4]}")
