import pandas as pd

df = pd.read_csv('data/backtest_20260921_213109.csv')
df['hour'] = pd.to_datetime(df['entry_time']).dt.hour

def calc(sub, name):
    wins = sub[sub['profit_r'] > 0]
    losses = sub[sub['profit_r'] <= 0]
    gp = wins['profit_money'].sum()
    gl = abs(losses['profit_money'].sum())
    pf = gp / gl if gl > 0 else 0
    wr = len(wins) / len(sub) * 100 if len(sub) > 0 else 0
    eq = sub['profit_money'].cumsum()
    dd = (eq.cummax() - eq).max() if len(sub) > 0 else 0
    print(f"{name:40s} | Trades: {len(sub):3d} | WR: {wr:4.1f}% | PF: {pf:4.2f} | Net $: {sub['profit_money'].sum():8.2f} | Total R: {sub['profit_r'].sum():5.2f} | Max DD: {dd:6.2f}")

print("Session Optimization Comparison:")
calc(df, "Baseline (All Hours)")
calc(df[(df['hour'] >= 13) & (df['hour'] <= 19)], "Option 1: NY Core (13:00 - 19:00 UTC)")
calc(df[(df['hour'] >= 13) & (df['hour'] <= 21)], "Option 2: NY Extended (13:00 - 21:00 UTC)")
calc(df[(df['hour'] == 8) | ((df['hour'] >= 13) & (df['hour'] <= 19))], "Option 3: London Open (08) + NY Core")
calc(df[~df['hour'].isin([9, 10, 11, 12])], "Option 4: Exclude Dead Zone (09:00-12:00)")
