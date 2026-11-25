import sqlite3
import MetaTrader5 as mt5
from datetime import datetime, timezone

print("="*70)
print("              AUDITORIA FORENSE DE LOS TRADES DEL 21 DE OCTUBRE")
print("="*70)

# 1. MT5 Account State
if mt5.initialize():
    acc = mt5.account_info()
    if acc:
        print(f"Cuenta: {acc.login} ({acc.server})")
        print(f"Balance Actual: ${acc.balance:,.2f} USD")
        print(f"Equidad Actual: ${acc.equity:,.2f} USD")
        print(f"Margen Libre:   ${acc.margin_free:,.2f} USD")
    
    from_date = datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc)
    to_date = datetime(2026, 10, 21, 23, 59, 59, tzinfo=timezone.utc)
    deals = mt5.history_deals_get(from_date, to_date)
    print("\n--- DEALS EN MT5 (20-21 OCT) ---")
    total_profit_oct21 = 0.0
    for d in deals or []:
        d_time = datetime.fromtimestamp(d.time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        d_type = 'BUY' if d.type == 0 else ('SELL' if d.type == 1 else str(d.type))
        entry = 'IN' if d.entry == 0 else ('OUT' if d.entry == 1 else 'IN/OUT')
        if d_time >= '2026-10-21':
            total_profit_oct21 += d.profit
        print(f"Deal #{d.ticket} | {d_time} UTC | {d.symbol:7} | {d_type:4} {entry:4} | Vol: {d.volume:4.2f} | Px: {d.price:7.2f} | PnL: ${d.profit:7.2f} | Magic: {d.magic} | {d.comment}")
    print(f"\n>> TOTAL GANANCIA NETA 21 DE OCTUBRE: ${total_profit_oct21:,.2f} USD <<")

# 2. Database Trade Records
print("\n--- REGISTROS EN JOURNALS SQLITE ---")
dbs = [
    ('Bot A (Gold)', 'data/journal_bot_a.db'),
    ('Bot B (Gold)', 'data/journal_bot_b.db'),
    ('Bot C (Gold)', 'data/journal_bot_c.db'),
    ('Bot D (Gold)', 'data/journal_bot_d.db'),
    ('Bot F (Gold)', 'data/journal_bot_f.db'),
]

for bot_name, db_path in dbs:
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        query = "SELECT ticket, symbol, direction, volume, entered_at, entry_price, exited_at, exit_price, sl_price, tp_price, profit, profit_r, total_score, structure_score, trend_score, momentum_score, comment FROM trades WHERE entered_at >= '2026-10-21' ORDER BY entered_at DESC"
        rows = c.execute(query).fetchall()
        for r in rows:
            print(f"\n[{bot_name}] Ticket #{r[0]}:")
            print(f"  Dirección: {r[2]} {r[1]} | Volumen: {r[3]} lotes")
            print(f"  Entrada:   {r[4]} @ {r[5]}")
            print(f"  Salida:    {r[6]} @ {r[7]}")
            print(f"  SL: {r[8]} | TP: {r[9]}")
            print(f"  Resultado: +${r[10]:.2f} USD (+{r[11]:.2f}R)")
            print(f"  Scores: Total={r[12]}, Estructura={r[13]}, Tendencia={r[14]}, Momentum={r[15]}")
            print(f"  Comentario: {r[16]}")
        conn.close()
    except Exception as e:
        print(f"[{bot_name}] Error: {e}")

print("\n" + "="*70)
