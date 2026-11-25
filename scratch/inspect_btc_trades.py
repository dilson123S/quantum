import sqlite3
import json

conn = sqlite3.connect('data/journal_demo.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=== ALL BTCUSD TRADES EVER RECORDED ===")
cursor.execute("""
    SELECT id, ticket, symbol, timeframe, direction, state, entry_price, exit_price, sl_price, tp_price, volume, risk_money, profit, profit_r, created_at, exited_at, comment
    FROM trades
    WHERE symbol LIKE '%BTC%'
    ORDER BY id ASC
""")
btc_trades = cursor.fetchall()
total_pnl = 0.0
total_r = 0.0
wins = 0
losses = 0
for t in btc_trades:
    pnl = t['profit'] or 0.0
    r = t['profit_r'] or 0.0
    if t['state'] == 'CLOSED':
        total_pnl += pnl
        total_r += r
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    print(f"ID={t['id']} | Ticket={t['ticket']} | TF={t['timeframe']} | Dir={t['direction']} | State={t['state']} | Entry={t['entry_price']} | Exit={t['exit_price']} | SL={t['sl_price']} | PnL=${pnl:.2f} ({r:.2f}R) | Date={t['created_at']}")

print(f"\nBTC TOTAL CLOSED TRADES: {wins + losses} (Wins: {wins}, Losses: {losses}, WinRate: {wins/(wins+losses)*100 if (wins+losses) else 0:.1f}%)")
print(f"BTC ACCUMULATED PnL: ${total_pnl:.2f} | Total R: {total_r:.2f}R")

print("\n=== DECISION CONTEXT FOR BTCUSD AROUND 2026-09-15 18:00 ===")
cursor.execute("""
    SELECT * FROM decision_context
    WHERE symbol = 'BTCUSD'
    ORDER BY created_at DESC
    LIMIT 5
""")
for r in cursor.fetchall():
    d = dict(r)
    print(f"ID={d['id']}, Sig={d['signal_id']}, TF={d['timeframe']}, Dir={d['direction']}, Regime={d['regime']}, Date={d['created_at']}")
    if d.get('context'):
        try:
            ctx = json.loads(d['context'])
            print(json.dumps(ctx, indent=2))
        except:
            print(d['context'])

conn.close()
