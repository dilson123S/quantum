import urllib.request
import json

try:
    with urllib.request.urlopen("http://localhost:8000/api/signals?limit=20") as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"Total signals: {len(data)}")
        for s in data[:12]:
            dt = s.get("created_at", "")[:19]
            bot = s.get("bot_name", "UNKNOWN")
            sym = s.get("symbol", "")
            d = s.get("direction", "")
            score = s.get("total_score", 0)
            status = s.get("execution_status", "")
            reason = str(s.get("rejection_reasons", ""))[:50]
            print(f"{dt} | {bot:<18} | {sym:<8} | {d:<4} | Score: {score:>2} | Status: {status:<8} | {reason}")
except Exception as e:
    print("API Error:", e)
