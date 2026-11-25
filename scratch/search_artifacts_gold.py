import os
import glob
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
brain_dir = r"C:\Users\Asus\.gemini\antigravity\brain\b83674df-f0b2-4991-82dd-b7b329205170"

print("SEARCHING ALL ARTIFACTS FOR GOLD BACKTEST REPORTS...")
for md in sorted(glob.glob(os.path.join(brain_dir, "*.md"))):
    try:
        with open(md, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
            if ("XAUUSD" in text or "oro" in text.lower()) and ("backtest" in text.lower() or "retorno" in text.lower() or "win rate" in text.lower()):
                # print filename
                base = os.path.basename(md)
                matches = []
                for line in text.split("\n"):
                    l_low = line.lower()
                    if any(k in l_low for k in ["profit factor", "win rate", "beneficio", "trades", "drawdown", "retorno"]) and any(c in line for c in ["$", "%", "PF", "R:"]):
                        if len(line.strip()) < 140:
                            matches.append(line.strip())
                if len(matches) >= 3:
                    print(f"\n--- {base} (Matches: {len(matches)}) ---")
                    for m in matches[:10]:
                        print("  ", m)
    except Exception as e:
        pass
