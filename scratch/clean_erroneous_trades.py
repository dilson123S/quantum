import sqlite3

tickets = (1949200419, 1949201162, 1949201416, 1949201596)
signals = ('c7e53b0be08321c6', '1de6ebb78414f590', 'f3d97e75a30f9af5', '4dfe652db06d2885')

for b in ['a', 'b', 'c', 'd']:
    db = f"data/journal_bot_{b}.db"
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.execute(f"DELETE FROM trades WHERE ticket IN {tickets}")
    deleted_trades = c.rowcount
    c.execute(f"DELETE FROM signal_log WHERE signal_id IN {signals}")
    deleted_signals = c.rowcount
    c.execute("DELETE FROM daily_stats WHERE date = '2026-09-21'")
    
    # Restore pattern memory to only winning trades
    c.execute("SELECT COUNT(*) FROM trades WHERE profit > 0")
    wins = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE profit < 0")
    losses = c.fetchone()[0]
    c.execute("SELECT SUM(profit) FROM trades WHERE profit IS NOT NULL")
    net_p = c.fetchone()[0] or 0.0
    print(f"Bot {b.upper()}: deleted {deleted_trades} trades, {deleted_signals} signals. Current ledger: {wins} wins, {losses} losses, net P&L: ${net_p:.2f}")
    conn.commit()
    conn.close()
