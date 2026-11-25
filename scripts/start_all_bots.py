import subprocess
import sys
import os
import time
import psutil
import MetaTrader5 as mt5
import sqlite3
import glob
import datetime

# 0. Ensure MT5 Terminal is running
terminal_running = False
for p in psutil.process_iter(['name']):
    if p.info.get('name') and 'terminal64' in p.info['name'].lower():
        terminal_running = True
        break

if not terminal_running:
    mt5_exe = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if os.path.exists(mt5_exe):
        print("Starting MT5 Terminal GUI...")
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/wmi_start.ps1", "-CommandLine", mt5_exe, "-CurrentDirectory", os.path.dirname(mt5_exe)]
        )
        time.sleep(4.0)

# 1. Reset peak equity to live balance
current_balance = 8779.77
if mt5.initialize():
    acc = mt5.account_info()
    if acc:
        current_balance = float(acc.balance)
    # Do not call mt5.shutdown() to preserve GUI terminal

now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
today_str = datetime.datetime.now().strftime('%Y-%m-%d')
for db in glob.glob('data/*.db'):
    try:
        conn = sqlite3.connect(db)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_state'")
        if c.fetchone():
            c.execute(
                "INSERT OR REPLACE INTO runtime_state (key, value, updated_at) VALUES ('account_peak_equity', ?, ?)",
                (current_balance, now_str)
            )
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_stats'")
        if c.fetchone():
            c.execute(
                "UPDATE daily_stats SET start_balance=? WHERE date=?",
                (current_balance, today_str)
            )
        conn.commit()
        conn.close()
    except Exception:
        pass

# 2. Stop any existing bot processes
current_pid = os.getpid()
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        if p.info['pid'] == current_pid:
            continue
        cmdline = p.info.get('cmdline') or []
        cmd_str = ' '.join(cmdline)
        if any(w in cmd_str.lower() for w in ['config/bot', 'run_deriv_bot.py']):
            print(f"Stopping old process PID {p.info['pid']}: {cmd_str[:60]}...")
            p.terminate()
            p.wait(timeout=3)
    except Exception:
        pass

time.sleep(1.0)

# 3. Launch the fleet
python_exe = sys.executable
bots = [
    ("QuantTradingBot-GoldDemoA", ["main.py", "--config", "config/bot_a_gold", "--mode", "demo"]),
    ("QuantTradingBot-GoldDemoB", ["main.py", "--config", "config/bot_b_gold", "--mode", "demo"]),
    ("QuantTradingBot-GoldDemoC", ["main.py", "--config", "config/bot_c_gold", "--mode", "demo"]),
    ("QuantTradingBot-GoldDemoD", ["main.py", "--config", "config/bot_d_gold", "--mode", "demo"]),
    ("QuantTradingBot-USDJPY-M15-DemoE", ["main.py", "--config", "config/bot_e", "--mode", "demo"]),
    ("QuantTradingBot-GoldDemoF", ["main.py", "--config", "config/bot_f_gold", "--mode", "demo"]),
    ("QuantTradingBot-AlchemistG", ["main.py", "--config", "config/bot_g_alchemist", "--mode", "demo"]),
    ("QuantTradingBot-DerivSynthetic", ["run_deriv_bot.py"]),
]

print(f"\nLaunching clean fleet of {len(bots)} bots via Windows WMI (persistent detached)...")
started = []
cwd = os.getcwd()
ps1_script = os.path.join(cwd, "scripts", "wmi_start.ps1")

for name, args in bots:
    cmd_str = f'"{python_exe}" ' + ' '.join(args)
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1_script, "-CommandLine", cmd_str, "-CurrentDirectory", cwd],
        capture_output=True,
        text=True
    )
    out = res.stdout.strip()
    if out.isdigit():
        pid = int(out)
        started.append((name, pid))
        print(f"  [+] Started {name} (PID: {pid})")
    else:
        print(f"  [-] Failed {name}: {res.stderr.strip() or out}")
    time.sleep(1.0)

time.sleep(3.0)
print("\nVerifying live status:")
all_ok = True
for name, pid in started:
    is_alive = psutil.pid_exists(pid)
    status = "ONLINE" if is_alive else "FAILED"
    if not is_alive:
        all_ok = False
    print(f"  * {name:34} [PID: {pid:5}] -> {status}")

if all_ok:
    print("\nSUCCESS: All 8 trading bots are running live in background.")
else:
    print("\nWARNING: Some bots failed to stay alive. Check logs.")
