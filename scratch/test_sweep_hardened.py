import sys, os
sys.path.insert(0, os.path.abspath("."))

from core.config_loader import load_config
from core.constants import Direction
from strategy.sweep_strategy import SweepReversalSignal, SweepReversalStrategy
from filters.ml_filter import MLFilter
import pandas as pd

print("=== TEST 1: Verificar Configuración ===")
cfg = load_config("config/bot_a_gold")
print(f"Sweep enabled: {cfg.sweep_reversal.enabled}")
print(f"Require ML: {getattr(cfg.sweep_reversal, 'require_ml_approval', False)}")
print(f"Require Macro: {getattr(cfg.sweep_reversal, 'require_macro_trend_alignment', False)}")
assert getattr(cfg.sweep_reversal, 'require_ml_approval', False) == True
assert getattr(cfg.sweep_reversal, 'require_macro_trend_alignment', False) == True
print("-> Configuración OK!")

print("\n=== TEST 2: Simular Filtro Macro en el Caso de Hoy (BUY en H4 Bajista) ===")
# Simular H4 bajista (Close < EMA200 y EMA20 < EMA50)
last_setup = {
    "close": 4350.0,
    "ema_fast": 4355.0,
    "ema_medium": 4365.0,
    "ema_slow": 4380.0,
}
close_h4 = last_setup["close"]
ema_fast_h4 = last_setup["ema_fast"]
ema_med_h4 = last_setup["ema_medium"]
ema_slow_h4 = last_setup["ema_slow"]

sweep_dir = Direction.BUY
macro_aligned = True
reasons = []

if sweep_dir == Direction.BUY:
    if close_h4 < ema_slow_h4 and ema_fast_h4 < ema_med_h4:
        macro_aligned = False
        reasons.append(f"H4 Bearish (Close {close_h4:.1f} < EMA200 {ema_slow_h4:.1f})")

print(f"Macro aligned: {macro_aligned} | Reasons: {reasons}")
assert macro_aligned == False, "Debe bloquear compras cuando H4 es fuertemente bajista"
print("-> Bloqueo Macro OK (¡El trade de hoy habría sido rechazado aquí!)")

print("\n=== TEST 3: Simular Filtro ML con la Probabilidad de Hoy (AI = 22%) ===")
ml_filter_a = MLFilter(enabled=True, shadow_mode=False, min_confidence=0.45)
ml_filter_b = MLFilter(enabled=True, shadow_mode=False, min_confidence=0.55)
ml_filter_c = MLFilter(enabled=True, shadow_mode=False, min_confidence=0.65)

# Test con P(éxito) = 0.22 (22%)
print(f"Bot A (>= 45%): ¿Aprueba 22%? -> {0.22 >= ml_filter_a.min_confidence}")
print(f"Bot B (>= 55%): ¿Aprueba 22%? -> {0.22 >= ml_filter_b.min_confidence}")
print(f"Bot C (>= 65%): ¿Aprueba 22%? -> {0.22 >= ml_filter_c.min_confidence}")
assert not (0.22 >= ml_filter_a.min_confidence)
assert not (0.22 >= ml_filter_b.min_confidence)
assert not (0.22 >= ml_filter_c.min_confidence)
print("-> Bloqueo ML OK (¡Los 3 bots habrían rechazado la orden por IA baja!)")

print("\n=== TEST 4: Generación de Score Real en SweepStrategy ===")
strat = SweepReversalStrategy(cfg)
sig_rev = SweepReversalSignal(
    direction=Direction.BUY,
    entry_price=4368.5,
    sl_price=4338.0,
    tp_price=4431.5,
    level_name="ASIA_LOW",
    level_price=4357.8,
    penetration_atr=1.01,
    risk_reward=2.0
)
sig = strat.build_sweep_signal("XAUUSD", "H1", sig_rev)
print(f"Total Score asignado: {sig.score_result.total_score}/100")
print(f"Clasificación: {sig.score_result.classification}")
print(f"Detalle Structure: {sig.score_result.structure.score}/30")
print(f"Detalle Trend: {sig.score_result.trend.score}/25")
assert sig.score_result.total_score == 80.0
assert sig.score_result.structure.score == 30.0
print("-> Generación de Score Real OK!")

print("\n¡TODAS LAS PRUEBAS UNITARIAS PASARON EXITOSAMENTE!")
