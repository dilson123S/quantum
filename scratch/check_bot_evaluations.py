import sys

def check_log(path, name):
    print(f"\n=================== {name} ===================")
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    eval_lines = []
    for l in lines:
        if any(k in l for k in ['[XAUUSD]', 'Score', 'Signal', 'REJECT', 'SWEEP', 'Filter', 'ScoreEngine', 'regime', 'session']):
            eval_lines.append(l.rstrip())
    for l in eval_lines[-15:]:
        print(l.encode('ascii', errors='backslashreplace').decode('ascii'))

check_log('logs/quanttradingbot-golddemoa.log', 'BOT-A (Alta Frecuencia)')
check_log('logs/quanttradingbot-golddemof.log', 'BOT-F (Permisivo)')
