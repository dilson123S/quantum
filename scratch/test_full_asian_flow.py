import sys, os
sys.path.insert(0, os.path.abspath("."))

from core.config_loader import load_config
from core.constants import Direction
from strategy.asian_breakdown import AsianBreakdownStrategy, AsianBreakdownSignal

print("=== TEST 1: Config Loading for All 4 Gold Bots ===")
for bot_dir in ["config/bot_a_gold", "config/bot_b_gold", "config/bot_c_gold", "config/bot_d_gold"]:
    cfg = load_config(bot_dir)
    ab_cfg = getattr(cfg, "asian_breakdown", None)
    assert ab_cfg is not None, f"AsianBreakdown missing in {bot_dir}"
    assert ab_cfg.enabled == True, f"AsianBreakdown disabled in {bot_dir}"
    assert ab_cfg.require_ml_approval == False, f"require_ml_approval should be False in {bot_dir}"
    print(f"[{cfg.bot.name}] Asian Breakdown Enabled: {ab_cfg.enabled} | min_rr: {ab_cfg.min_rr} | require_ml: {ab_cfg.require_ml_approval}")

print("-> Todos los 4 bots tienen Asian Breakdown activo y configurado OK!")

print("\n=== TEST 2: Signal Object Creation & Scoring ===")
cfg_a = load_config("config/bot_a_gold")
strat = AsianBreakdownStrategy(cfg_a)
dummy_signal = AsianBreakdownSignal(
    direction=Direction.SELL,
    entry_price=4353.25,
    sl_price=4361.47,
    tp_price=4334.25,
    level_name="ASIA_LOW",
    level_price=4357.8,
    target_name="PREVIOUS_DAY_LOW",
    target_price=4334.25,
    risk_reward=2.31,
)
full_sig = strat.build_breakdown_signal("XAUUSD", "H1", dummy_signal)
assert full_sig.direction == Direction.SELL
assert full_sig.score_result.total_score == 85.0
assert full_sig.score_result.classification.value == "EXCELLENT"
assert full_sig.score_result.structure.score == 30.0
assert full_sig.score_result.trend.score == 20.0
print(f"Signal ID: {full_sig.signal_id}")
print(f"Score Total: {full_sig.score_result.total_score} | Clasificación: {full_sig.score_result.classification.value}")
print("-> Creación de Señal OK!")

print("\n=== TEST 3: Ejecución de pytest de la suite ===")
import subprocess
res = subprocess.run([sys.executable, "-m", "pytest", "tests/test_sweep_strategy.py", "-q"], capture_output=True, text=True)
print(res.stdout)
assert res.returncode == 0, f"Pytest fallo: {res.stderr}"

print("¡TODAS LAS PRUEBAS DE FLUJO ASIAN BREAKDOWN PASARON CON ÉXITO!")
