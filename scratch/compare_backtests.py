import pandas as pd

df1 = pd.read_csv('data/backtest_20260921_213109.csv')
df2 = pd.read_csv('data/backtest_20260921_215414.csv')

print(f"Backtest 1 (All hours): {len(df1)} trades | Net $: {df1['profit_money'].sum():.2f} | Total R: {df1['profit_r'].sum():.2f}")
print(f"Backtest 2 (Excluded hours 9-12): {len(df2)} trades | Net $: {df2['profit_money'].sum():.2f} | Total R: {df2['profit_r'].sum():.2f}")

# Find trades in df1 not in df2
df1['key'] = df1['entry_time'] + '_' + df1['direction']
df2['key'] = df2['entry_time'] + '_' + df2['direction']

missing_in_2 = df1[~df1['key'].isin(df2['key'])]
new_in_2 = df2[~df2['key'].isin(df1['key'])]

print(f"\nTrades in Backtest 1 that were eliminated in Backtest 2: {len(missing_in_2)}")
print(f"Profit of eliminated trades: ${missing_in_2['profit_money'].sum():.2f} ({missing_in_2['profit_r'].sum():.2f} R)")
print(f"Eliminated Wins: {len(missing_in_2[missing_in_2['profit_r'] > 0])} | Losses: {len(missing_in_2[missing_in_2['profit_r'] <= 0])}")

print(f"\nTrades newly created in Backtest 2 (butterfly effect): {len(new_in_2)}")
print(f"Profit of new trades: ${new_in_2['profit_money'].sum():.2f} ({new_in_2['profit_r'].sum():.2f} R)")
print(f"New Wins: {len(new_in_2[new_in_2['profit_r'] > 0])} | Losses: {len(new_in_2[new_in_2['profit_r'] <= 0])}")

print("\nDetail of top winning trades eliminated:")
top_wins_elim = missing_in_2[missing_in_2['profit_r'] > 0].sort_values('profit_money', ascending=False)
print(top_wins_elim[['entry_time', 'direction', 'profit_r', 'profit_money', 'exit_reason']])
