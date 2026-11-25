"""Unified multi-asset launcher for XAUUSD (Gold) and BTCUSD (Bitcoin) live demo bots."""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from core.config_loader import load_config


FLEET_RUNTIME_FILE = Path("data/fleet_runtime.json")


def _write_fleet_runtime(processes, started_at, *, preserve_existing=False):
    """Keep the dashboard's process marker aligned with launched demo bots."""
    existing = {}
    if preserve_existing:
        try:
            existing = json.loads(FLEET_RUNTIME_FILE.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            existing = {}

    pid_map = dict(existing.get("processes", {})) if preserve_existing else {}
    process_started = dict(existing.get("process_started_local", {})) if preserve_existing else {}
    for bot_id, _name, _cmd, process, launched_at in processes:
        if not bot_id:
            continue
        pid_map[bot_id] = process.pid
        process_started[bot_id] = launched_at.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")

    if preserve_existing and existing.get("started_local"):
        started_local = existing["started_local"]
        recorded_started_at = existing.get("started_at") or started_at.isoformat(timespec="seconds")
    else:
        started_local = started_at.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
        recorded_started_at = started_at.astimezone().isoformat(timespec="seconds")

    marker = {
        "processes": pid_map,
        "started_at": recorded_started_at,
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "run_demo_bots launcher",
        "started_local": started_local,
        "process_started_local": process_started,
    }
    try:
        FLEET_RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = FLEET_RUNTIME_FILE.with_suffix(FLEET_RUNTIME_FILE.suffix + ".tmp")
        temporary.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        temporary.replace(FLEET_RUNTIME_FILE)
    except OSError as exc:
        print(f"[WARN] No se pudo actualizar el estado del dashboard: {exc}")


def _demo_authorized(config_dir):
    """Do not supervise demo profiles blocked by their validation gate."""
    try:
        status = load_config(config_dir).validation.status.strip().upper()
    except Exception as exc:
        print(f"[SKIP] {config_dir}: no se pudo validar el perfil ({exc}).")
        return False
    if status not in {"APPROVED", "DEMO_APPROVED"}:
        print(f"[SKIP] {config_dir}: validación '{status}' bloquea ejecución DEMO.")
        return False
    return True

def main():
    parser = argparse.ArgumentParser(description="Multi-Asset QuantTradingBot Launcher")
    parser.add_argument("--include-crypto", action="store_true", help="Also launch Crypto (BTC) bot")
    parser.add_argument("--only-e", action="store_true", help="Supervise only experimental Bot E; attach no duplicate A-D processes")
    parser.add_argument("--only-fleet-2", action="store_true", help="Supervise only Fleet 2 (Bots A2, B2, C2, D2)")
    parser.add_argument("--fleet-2", action="store_true", help="Include Fleet 2 alongside primary fleet")
    parser.add_argument("--all-fleets", action="store_true", help="Launch all fleets (Flota 1: A-G + Flota 2: A2-D2)")
    args = parser.parse_args()

    print("=" * 70)
    print("   QUANT TRADING BOT -- FLOTAS EN PARALELO (A-G + A2-D2)")
    print("=" * 70)
    
    bots = []
    if args.only_e:
        bots.append(("BOT-E", "Bot E [USDJPY M15 · Confirmación H1 · 0.25% riesgo]", ["main.py", "--config", "config/bot_e", "--mode", "demo"]))
    elif args.only_fleet_2:
        bots.append(("BOT-A2", "Bot A2 [Alta Frecuencia · ML >= 0.45 · Sep 17]", ["main.py", "--config", "config/bot_a2_gold", "--mode", "demo"]))
        bots.append(("BOT-B2", "Bot B2 [Máximo Rendimiento · ML >= 0.55 · Sep 17]", ["main.py", "--config", "config/bot_b2_gold", "--mode", "demo"]))
        bots.append(("BOT-C2", "Bot C2 [Francotirador Sniper · ML >= 0.70 · Sep 17]", ["main.py", "--config", "config/bot_c2_gold", "--mode", "demo"]))
        bots.append(("BOT-D2", "Bot D2 [Control Estancamiento · Sin ML · Sep 17]", ["main.py", "--config", "config/bot_d2_gold", "--mode", "demo"]))
    else:
        bots.append(("BOT-A", "Bot A [Alta Frecuencia · ML >= 0.45]", ["main.py", "--config", "config/bot_a_gold", "--mode", "demo"]))
        bots.append(("BOT-B", "Bot B [Máximo Rendimiento · ML >= 0.55]", ["main.py", "--config", "config/bot_b_gold", "--mode", "demo"]))
        bots.append(("BOT-C", "Bot C [Francotirador Sniper · ML >= 0.70]", ["main.py", "--config", "config/bot_c_gold", "--mode", "demo"]))
        bots.append(("BOT-D", "Bot D [Control Estancamiento · Sin ML]", ["main.py", "--config", "config/bot_d_gold", "--mode", "demo"]))
        bots.append(("BOT-E", "Bot E [USDJPY M15 · Confirmación H1 · 0.25% riesgo]", ["main.py", "--config", "config/bot_e", "--mode", "demo"]))
        bots.append(("BOT-F", "Bot F [Permisivo · Liquidity 0.3 ATR · ML Shadow]", ["main.py", "--config", "config/bot_f_gold", "--mode", "demo"]))
        bots.append(("BOT-G", "Bot G [Alchemist Puro · MSNR + CRT · #26082517]", ["main.py", "--config", "config/bot_g_alchemist", "--mode", "demo"]))
        bots.append(("BOT-DERIV", "Bot Deriv [Sintéticos · Vol75/Vol100/Boom/Crash]", ["run_deriv_bot.py"]))
        if args.fleet_2 or args.all_fleets:
            bots.append(("BOT-A2", "Bot A2 [Alta Frecuencia · ML >= 0.45 · Sep 17]", ["main.py", "--config", "config/bot_a2_gold", "--mode", "demo"]))
            bots.append(("BOT-B2", "Bot B2 [Máximo Rendimiento · ML >= 0.55 · Sep 17]", ["main.py", "--config", "config/bot_b2_gold", "--mode", "demo"]))
            bots.append(("BOT-C2", "Bot C2 [Francotirador Sniper · ML >= 0.70 · Sep 17]", ["main.py", "--config", "config/bot_c2_gold", "--mode", "demo"]))
            bots.append(("BOT-D2", "Bot D2 [Control Estancamiento · Sin ML · Sep 17]", ["main.py", "--config", "config/bot_d2_gold", "--mode", "demo"]))
    
    if args.include_crypto:
        bots.append((None, "Bitcoin H1 (QuantTradingBot-BtcDemo)", ["main.py", "--config", "config/demo_btc", "--mode", "demo"]))

    # Telegram Interactive Bot Listener
    if not args.only_e:
        bots.append((None, "Telegram Bot Listener (Quantum)", ["telegram_bot.py"]))

    # A blocked profile exits during startup; supervising it would create an
    # endless restart loop without ever making the bot operational.
    bots = [
        (bot_id, name, cmd)
        for bot_id, name, cmd in bots
        if "--config" not in cmd or _demo_authorized(cmd[cmd.index("--config") + 1])
    ]

    processes = []
    run_started_at = datetime.now().astimezone()
    for idx, (bot_id, name, cmd) in enumerate(bots, 1):
        print(f"[{idx}/{len(bots)}] Iniciando {name}...")
        full_cmd = [sys.executable] + cmd
        launched_at = datetime.now().astimezone()
        p = subprocess.Popen(full_cmd)
        processes.append((bot_id, name, cmd, p, launched_at))
        _write_fleet_runtime(processes, run_started_at, preserve_existing=args.only_e)
        time.sleep(2)

    print(f"\n[OK] {len(processes)} bot(s) en ejecución concurrentemente (Supervisión Activa 24/7).")
    print("Presiona Ctrl+C para detener todos los bots de forma segura.\n")
    
    try:
        while True:
            time.sleep(5)
            for i, (bot_id, name, cmd, p, _launched_at) in enumerate(processes):
                ret = p.poll()
                if ret is not None:
                    print(f"[SUPERVISOR] {name} se detuvo (código {ret}). Reiniciando en 10s...")
                    time.sleep(10)
                    launched_at = datetime.now().astimezone()
                    new_p = subprocess.Popen([sys.executable] + cmd)
                    processes[i] = (bot_id, name, cmd, new_p, launched_at)
                    _write_fleet_runtime(processes, run_started_at, preserve_existing=args.only_e)
    except KeyboardInterrupt:
        print("\n[INFO] Deteniendo todos los bots...")
        for _bot_id, name, cmd, p, _launched_at in processes:
            p.terminate()
        for _bot_id, name, cmd, p, _launched_at in processes:
            p.wait()
        print("[OK] Todos los bots se han detenido correctamente.")

if __name__ == "__main__":
    main()
