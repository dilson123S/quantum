import sqlite3
import glob

import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

print("=" * 80)
print("  AUDITORIA DE SENALES Y RECHAZOS DE HOY (2026-09-22)")
print("=" * 80)

for db_path in sorted(glob.glob("data/journal_bot_*.db")):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, symbol, direction, total_score, was_traded, rejection_reasons, created_at FROM signal_log WHERE created_at LIKE '2026-09-22%' ORDER BY id ASC")
    rows = cur.fetchall()
    print(f"\n[DB] Base de Datos: {db_path} (Total senales registradas hoy: {len(rows)})")
    if not rows:
        print("   (Sin señales generadas hoy)")
    for r in rows:
        print(f"   [ID {r[0]}] {r[6]} | {r[1]} {r[2]} | Score: {r[3]}/100 | Traded: {r[4]} | Motivo de rechazo: {r[5]}")
    conn.close()

# Also check trades table
for db_path in sorted(glob.glob("data/journal_bot_*.db")):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, ticket, symbol, direction, state, entry_price, sl_price, tp_price, profit, exit_reason, created_at FROM trades WHERE created_at LIKE '2026-09-22%'")
    rows = cur.fetchall()
    if rows:
        print(f"\n🎯 Trades en {db_path}:")
        for r in rows:
            print(f"   {r}")
    conn.close()

print("\n" + "=" * 80)
