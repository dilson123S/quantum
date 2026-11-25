import sqlite3
import glob
import json

print("=== REAL DEMO JOURNAL TRADES ===")
for db in sorted(glob.glob("data/journal_bot_*.db")):
    try:
        conn = sqlite3.connect(db)
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(profit), SUM(profit_r) FROM trades WHERE state='CLOSED'")
        row = c.fetchone()
        c.execute("SELECT COUNT(*) FROM trades WHERE state='CLOSED' AND profit > 0")
        wins = c.fetchone()[0]
        cnt = row[0] or 0
        wr = (wins / cnt * 100) if cnt else 0.0
        p = row[1] if row[1] is not None else 0.0
        r = row[2] if row[2] is not None else 0.0
        print(f"{db:<25} -> Trades: {cnt:<3} | Wins: {wins:<3} ({wr:5.1f}%) | Net: ${p:+8.2f} | R: {r:+6.2f}")
    except Exception as e:
        print(f"{db} -> Error: {e}")

print("\n=== HISTORICAL BACKTEST MANIFESTS ===")
for f in sorted(glob.glob("data/experiments/*.json")):
    try:
        with open(f, "r") as fp:
            d = json.load(fp)
            cfg = list(d.get("config_sha256", {}).keys())
            cfg_name = cfg[0] if cfg else "unknown"
            sm = d.get("summary_metrics", {})
            trades = sm.get("total_trades", 0)
            profit = sm.get("total_profit_money", 0)
            prof_r = sm.get("total_profit_r", 0)
            is_oos = d.get("is_oos_evaluation", False)
            eval_start = d.get("evaluation_start")
            period = d.get("period", "?")
            print(f"{f:<45} | {cfg_name:<30} | {period} | OOS:{is_oos} | Trades: {trades:<3} | Net: ${profit:+8.2f} | R: {prof_r:+5.2f}")
    except Exception as e:
        pass
