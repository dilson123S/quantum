import glob

for bot_name in ['bot_b', 'bot_c', 'bot_d', 'bot_f']:
    print(f"\n==================== LOGS FOR {bot_name.upper()} ====================")
    log_files = glob.glob(f"logs/*{bot_name}*.log*")
    for lf in log_files:
        print(f"Reading {lf}:")
        with open(lf, 'r', encoding='utf-8', errors='ignore') as f:
            matching = []
            for line in f:
                if '2026-10-20 12:3' in line or '2026-10-20 12:2' in line or '9849673' in line:
                    matching.append(line.strip())
            for m in matching[-20:]:
                print(m)
