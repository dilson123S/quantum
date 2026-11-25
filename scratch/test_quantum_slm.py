import urllib.request
import json
import time

prompt = (
    "You are Kev, the Quantum AI System 1 Decision Model. Analyze this trading candidate state "
    "and respond STRICTLY in JSON format with keys: thesis_alignment (aligned/mixed/contradictory/insufficient), "
    "thesis_confidence (0.0-1.0), evidence_score (1-5), evidence_confidence (0.0-1.0), "
    "trap_risk (genuine_expansion/potential_trap/overextended_exhaustion), trap_confidence (0.0-1.0), "
    "and reason (concise institutional explanation).\n\n"
    "Candidate:\n"
    "{\n"
    '  "symbol": "XAUUSD",\n'
    '  "direction": "BUY",\n'
    '  "entry_price": 4280.00,\n'
    '  "timeframe": "H1",\n'
    '  "macro_timeframe": "H4/D1",\n'
    '  "adx": 14.2,\n'
    '  "session": "ASIAN_RANGE",\n'
    '  "smc_pattern": "RANGE_BREAKOUT"\n'
    "}"
)

payload = {
    "model": "quantum",
    "messages": [{"role": "user", "content": prompt}],
    "temperature": 0.1,
}

print("[+] Sending TRAP test setup (Asian session + ADX 14.2) to Ollama 'quantum'...")
t0 = time.time()
req = urllib.request.Request(
    "http://localhost:11434/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=30) as resp:
    res = json.loads(resp.read().decode("utf-8"))
    elapsed = time.time() - t0
    content = res["choices"][0]["message"]["content"]
    print(f"[OK] Response received in {elapsed:.2f}s:")
    print("=" * 60)
    print(content)
    print("=" * 60)
