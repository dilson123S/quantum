import sys

with open('logs/quanttradingbot-alchemistg.log', 'r', encoding='utf-8', errors='ignore') as f:
    matched = []
    for line in f:
        if any(k in line for k in ['ALCHEMIST', 'Alchemist', 'Killzone', 'CRT', 'REJECT', 'SIGNAL', 'TRADE']):
            matched.append(line.rstrip())
    print(f"Total matching lines in Bot G: {len(matched)}")
    for l in matched[-20:]:
        print(l.encode('ascii', errors='backslashreplace').decode('ascii'))
