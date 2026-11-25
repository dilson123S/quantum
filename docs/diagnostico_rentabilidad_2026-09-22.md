# Diagnóstico de rentabilidad y validación — Bot B / flota

**Fecha:** 2026-09-22  
**Alcance:** auditoría de solo lectura de la configuración actual, backtests reproducibles disponibles y extracto de operaciones demo. No se modificó ninguna configuración de trading ni se enviaron órdenes.

## Resultado ejecutivo

El perfil técnico actual de Bot B produjo un backtest in-sample positivo, pero la rentabilidad aún no está validada fuera de muestra y no coincide con el desempeño demo reciente. El dato más representativo de operación es la cuenta demo de la flota: del 16 al 21 de septiembre de 2026, A–D acumularon **−$788.20 realizados** en 20 cierres. No incluye P&L flotante de posiciones abiertas.

La prueba histórica nueva de configuración actual, hasta justo antes del holdout del 1 de junio, dio **151 operaciones, +27.37R, PF 1.861 y DD máximo 8.8%**. Es una prueba exploratoria anterior al holdout, no una validación final. Su drawdown queda a 1.2 puntos porcentuales del límite configurado de 10%.

## Backtests ejecutados

Las tres corridas descargaron las mismas 12,010 velas H1, 3,143 H4 y 524 D1 de XAUUSD, con corte estricto antes del 2026-06-01. La configuración base quedó registrada en `data/experiments/bot-b-current-profile-is-ml-active-20260922T233010Z.json` y la prueba de costos en `data/experiments/bot-b-current-profile-cost-plus-20pct-20260922T233456Z.json`.

| Corrida | Trades | Resultado neto | R netas | PF | DD máx. | Lectura |
|---|---:|---:|---:|---:|---:|---|
| Bot B actual, costos configurados | 151 | +$6,986.36 | +27.37R | 1.861 | 8.8% | Positivo, in-sample |
| Bot B actual, comisión y slippage +20% | 151 | +$6,965.15 | +27.31R | 1.857 | 8.8% | Positivo bajo este estrés acotado |

El estrés incrementó comisión round-turn de $7 a $8.40 por lote, slippage de 1 a 1.2 puntos y gap slippage de 2 a 2.4 puntos. Conservó el spread por vela descargado; por tanto, no representa una prueba de fills realistas ni de deslizamiento extremo. Hubo 16 meses positivos y 8 negativos; marzo de 2026 fue el mejor mes con 22.8% del P&L total, así que el beneficio no depende de un solo mes.

Los dos archivos de trades y sus manifiestos conservan inputs y hashes. El perfil actual usa XAUUSD H1/H4/D1, umbral score 70 y sesión 07:00–03:00 UTC. El backtest anterior de 26 operaciones que dio PF 5.65 usó sesión 07:00–18:00 UTC; no es el perfil desplegado. El archivo llamado `bot_b_score_audit_26trades.csv` contiene 45 trades, no 26. No deben combinarse como si fueran la misma corrida.

## Hallazgo importante sobre ML

Corrí la misma serie una vez con el perfil actual y otra con ML en sombra dentro de una copia temporal. Trades, P&L y hash del CSV resultaron idénticos. La razón está en el código: el `BacktestEngine` de `backtest/engine.py` no invoca `MLFilter`; el backtest estándar evalúa la estrategia técnica y **no prueba el filtro ML que está activo en `config/bot_b_gold/settings.yaml`**.

En cambio, el experimento personalizado que creó el CSV de 26 operaciones sí instancia `MLFilter` activo (`scratch/export_exact_26.py`) y evalúa historia desde 2024 con `data/ml_model.joblib`, entrenado con dataset hasta el 14 de septiembre de 2026. Ese resultado tiene fuga temporal para las fechas históricas y no es evidencia válida del rendimiento del ML. El reporte del modelo además dice `ACCEPTED_SHADOW_ONLY`, mientras la configuración permite el filtro activo. Sus métricas de validación son AUC medio 0.6377, precisión 0.1976 y F1 0.1716; no prueban que el ML aumente P&L neto.

Por ahora no se debe atribuir el +27.37R del backtest al ML. Hace falta una simulación walk-forward del filtro, entrenando en cada fecha únicamente con ejemplos cuyo horizonte de etiqueta ya había terminado. El conjunto XAUUSD actual comienza el 2025-10-06, por lo que no permite evaluar ML desde el inicio del backtest de 2024; los primeros meses deben tratarse como estrategia sin ML.

## Demo de la flota

En `data/mt5_demo_trades_export.csv`, para cierres con los magic actuales A–D entre el 16 y el 21 de septiembre:

| Bot | Cierres | P&L neto realizado |
|---|---:|---:|
| A | 7 | −$232.83 |
| B | 5 | −$264.61 |
| C | 4 | −$195.78 |
| D | 4 | −$94.98 |
| **Flota A–D** | **20** | **−$788.20** |

Las pérdidas de las 17:02 UTC y 22:32 UTC del 21 de septiembre fueron −$352.38 y −$272.89 respectivamente. Son 4 bots perdiendo XAUUSD casi al mismo tiempo; juntas explican aproximadamente 79% de la pérdida demo de A–D en el intervalo. Esto es riesgo correlacionado, no cuatro apuestas independientes. El extracto muestra un saldo neto de volumen de entrada menos salida de aproximadamente 0.23 lotes para esos magic; su P&L flotante no está en el archivo.

Los manifiestos OOS anteriores de Bot B tampoco validan el perfil actual: 14 trades y +$25.05 (+0.27R) desde agosto 1; 5 trades y −$88.86 (−2.37R) desde agosto 21. Son muestras pequeñas y sus hashes de configuración no coinciden con los archivos actuales. Como además ya fueron revisados, no sirven como holdout intacto para volver a ajustar parámetros.

## Acciones recomendadas

1. Mantener el perfil actual congelado y registrar los hashes incluidos en los manifiestos de esta corrida. Cualquier cambio de sesión, ML, SL/TP, trailing o riesgo requiere otra validación futura.
2. No bajar más el score: el barrido disponible de 50 a 75 no agregó operaciones.
3. Mantener ML fuera de la decisión activa hasta medirlo en una prueba walk-forward correcta y compararlo con el mismo perfil técnico sin ML. El backtest estándar actual no puede hacer esa comparación.
4. No elevar riesgo para compensar pérdidas. A–D comparten XAUUSD y sus controles individuales no demuestran un límite de pérdida agregado de cuenta; primero hace falta gobernar la exposición como una cartera.
5. Seguir registrando fills, comisiones, swaps, spread y equity flotante bajo el perfil congelado. La nueva ventana de forward debe mantenerse sin retoques y juzgarse por P&L neto, expectativa R, PF, drawdown de equity y concentración por mes.

## Criterio para mejorar la calificación de rentabilidad

La corrida in-sample positiva y la sensibilidad acotada a costos son puntos a favor del motor técnico. La calificación de rentabilidad sigue limitada por el desempeño demo negativo, drawdown histórico cercano al máximo permitido, ausencia de ML walk-forward y falta de holdout futuro intacto. No hay base objetiva todavía para declarar rentable el sistema desplegado ni para afirmar que subiría la nota cambiando un umbral.
