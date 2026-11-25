import sqlite3

conn = sqlite3.connect('data/journal_demo.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("""
    SELECT id, ticket, symbol, timeframe, direction, state, entry_price, exit_price, sl_price, tp_price, volume, risk_money, profit, profit_r, created_at, exited_at
    FROM trades
    WHERE symbol LIKE '%BTC%'
    ORDER BY id ASC
""")
rows = cursor.fetchall()
print(f"Total BTC trades in DB: {len(rows)}")
wins, losses = 0, 0
total_pnl, total_r = 0.0, 0.0
for r in rows:
    pnl = r['profit'] if r['profit'] is not None else 0.0
    pr = r['profit_r'] if r['profit_r'] is not None else 0.0
    if r['state'] == 'CLOSED':
        total_pnl += pnl
        total_r += pr
        if pnl > 0: wins += 1
        else: losses += 1
    print(f"#{r['id']} | T={r['ticket']} | {r['timeframe']} {r['direction']} | State={r['state']} | In={r['entry_price']} | Out={r['exit_price']} | SL={r['sl_price']} | PnL=${pnl:.2f} ({pr:.2f}R) | Created={r['created_at']}")

print(f"\nStats: Closed={wins+losses} (Wins: {wins}, Losses: {losses}) | Total PnL: ${total_pnl:.2f} | Total R: {total_r:.2f}R")
conn.close()
