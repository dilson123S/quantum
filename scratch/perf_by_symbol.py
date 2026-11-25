import sqlite3
import pandas as pd

conn = sqlite3.connect('data/journal_demo.db')

df = pd.read_sql_query("""
    SELECT symbol, timeframe, direction, state, profit, profit_r, risk_money, created_at
    FROM trades
    WHERE state = 'CLOSED'
""", conn)

print("=== OVERALL PERFORMANCE BY SYMBOL ===")
for sym, group in df.groupby('symbol'):
    total_trades = len(group)
    wins = len(group[group['profit'] > 0])
    losses = len(group[group['profit'] <= 0])
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    total_profit = group['profit'].sum()
    total_r = group['profit_r'].sum()
    avg_win = group[group['profit'] > 0]['profit'].mean() if wins > 0 else 0
    avg_loss = group[group['profit'] <= 0]['profit'].mean() if losses > 0 else 0
    profit_factor = abs(group[group['profit'] > 0]['profit'].sum() / group[group['profit'] <= 0]['profit'].sum()) if losses > 0 and group[group['profit'] <= 0]['profit'].sum() != 0 else float('inf')

    print(f"\n--- {sym} ---")
    print(f"Total Trades: {total_trades} (Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%)")
    print(f"Total Profit: ${total_profit:.2f} | Total R: {total_r:.2f}R")
    print(f"Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f} | Profit Factor: {profit_factor:.2f}")

    # By direction
    for direction, dgroup in group.groupby('direction'):
        dwins = len(dgroup[dgroup['profit'] > 0])
        dlosses = len(dgroup[dgroup['profit'] <= 0])
        dwin_rate = (dwins / len(dgroup)) * 100 if len(dgroup) > 0 else 0
        dpnl = dgroup['profit'].sum()
        dr = dgroup['profit_r'].sum()
        print(f"   [{direction}]: {len(dgroup)} trades (W:{dwins}, L:{dlosses}, WR:{dwin_rate:.1f}%) -> PnL: ${dpnl:.2f} ({dr:.2f}R)")

    # By timeframe
    for tf, tfgroup in group.groupby('timeframe'):
        twins = len(tfgroup[tfgroup['profit'] > 0])
        tlosses = len(tfgroup[tfgroup['profit'] <= 0])
        twin_rate = (twins / len(tfgroup)) * 100 if len(tfgroup) > 0 else 0
        tpnl = tfgroup['profit'].sum()
        tr = tfgroup['profit_r'].sum()
        print(f"   [TF {tf}]: {len(tfgroup)} trades (W:{twins}, L:{tlosses}, WR:{twin_rate:.1f}%) -> PnL: ${tpnl:.2f} ({tr:.2f}R)")

conn.close()
