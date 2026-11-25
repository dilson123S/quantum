# Diagnóstico integral de la flota actual y antecedentes demo

**Fecha:** 2026-09-22  
**Objetivo:** medir los perfiles actuales de oro A–D y los perfiles BTC disponibles en el proyecto con la misma ventana y sensibilidad de costos usadas para Bot B. El extracto demo se revisa solo como antecedente de versiones anteriores; según la aclaración del usuario, no contiene operaciones de la flota actual.
**Seguridad de ejecución:** solo se descargó historia de MT5 y se ejecutaron backtests; no se modificaron parámetros productivos ni se enviaron órdenes.

## Método

- XAUUSD con datos H1/H4/D1 compartidos; período de dos años con corte exclusivo antes del 2026-06-01 para mantener fuera el holdout protegido.
- Cada perfil A, B, C y D se simuló por separado con saldo inicial de $10,000 y sus archivos actuales `settings.yaml`, `symbols.yaml` y `risk.yaml`.
- Se repitió cada perfil con comisión, slippage y gap slippage aumentados 20%. El spread por vela proviene de la misma historia descargada y no se elevó en esta sensibilidad.
- El backtester estándar **no aplica `MLFilter`**. Por ello las corridas evalúan la estrategia técnica y gestión de salida, no los distintos umbrales ML configurados en A/B/C/D.
- `run_demo_bots.py` inicia A–D por defecto y añade BTC H1 solo con `--include-crypto`; BTC H4 no forma parte del lanzador actual. Ambos perfiles BTC se evaluaron por separado. La actividad del extracto demo corresponde a una generación previa, según la aclaración del usuario, por lo que sus resultados no se atribuyen a los perfiles actuales.
- Todos los resultados son históricos exploratorios, no OOS. El bootstrap se presenta como descripción de esta muestra in-sample y no cambia esa clasificación.

El script reproducible es [audit_fleet_profiles.py](C:/Users/Asus/Documents/quantum/scripts/audit_fleet_profiles.py). El resumen tabular está en [fleet_audit_summary_20260922.csv](C:/Users/Asus/Documents/quantum/data/experiments/fleet_audit_summary_20260922.csv); cada ejecución tiene un manifiesto con hashes y un CSV de trades en `data/experiments` y `data`.

## Resultados por bot

Cada P&L corresponde a una corrida independiente con $10,000 iniciales; no se deben sumar como si esa fuera la cuenta MT5 compartida.

| Bot | Costos | Trades | Win rate | P&L | R netas | Expectativa | PF | DD máx. | Bootstrap p05 (R) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | Base | 151 | 45.0% | +$6,986.36 | +27.37 | +0.1812R | 1.861 | 8.76% | +12.86 |
| A | +20% | 151 | 45.0% | +$6,965.15 | +27.31 | +0.1808R | 1.857 | 8.78% | +12.78 |
| B | Base | 151 | 45.0% | +$6,986.36 | +27.37 | +0.1812R | 1.861 | 8.76% | +12.86 |
| B | +20% | 151 | 45.0% | +$6,965.15 | +27.31 | +0.1808R | 1.857 | 8.78% | +12.78 |
| C | Base | 151 | 45.0% | +$6,986.36 | +27.37 | +0.1812R | 1.861 | 8.76% | +12.86 |
| C | +20% | 151 | 45.0% | +$6,965.15 | +27.31 | +0.1808R | 1.857 | 8.78% | +12.78 |
| D | Base | 175 | 50.3% | +$5,380.02 | +22.00 | +0.1257R | 1.988 | 7.82% | +11.10 |
| D | +20% | 175 | 50.3% | +$5,358.97 | +21.93 | +0.1253R | 1.982 | 7.85% | +11.02 |

El estrés de comisión y slippage cambió poco estas simulaciones. Esa estabilidad solo cubre los parámetros alterados; no prueba fills reales ni costos en eventos de volatilidad. A–C tienen exactamente la misma secuencia de operaciones en el motor estándar. D comparte la dirección y hora de entrada de 148 de las 151 operaciones de A–C; sus diferencias de salida reducen la ganancia neta histórica, mientras elevan PF y reducen DD.

### BTC H1 opcional

| Perfil | Costos | Trades | Win rate | P&L | R netas | PF | DD máx. | Bootstrap p05 (R) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC H1 | Base | 92 | 32.6% | +$981.29 | +10.32 | 1.409 | 3.2% | −2.54 |
| BTC H1 | +20% | 92 | 32.6% | +$974.43 | +10.25 | 1.406 | 3.2% | −2.61 |
| BTC H4 | Base | 28 | 39.3% | +$673.33 | +7.25 | 2.263 | 1.4% | +0.37 |
| BTC H4 | +20% | 28 | 39.3% | +$672.53 | +7.24 | 2.260 | 1.4% | +0.37 |

BTC H1 conserva resultado neto positivo en este IS, pero no alcanza 100 operaciones y el percentil 5 del bootstrap queda negativo. Bajo el protocolo del proyecto no es evidencia suficiente para aprobarlo. BTC H4 también muestra resultado positivo, pero 28 operaciones son insuficientes para validarlo aunque su p05 sea positivo. Ningún perfil BTC se sumó a la cartera hipotética A–D porque no se simuló una cuenta conjunta ni su interacción de riesgo.

### Flota con asignación hipotética 25% por bot

Como referencia, agregué las cuatro secuencias después de asignar 25% del capital a cada bot y escalar su P&L a una cartera inicial de $10,000. Es una aproximación de cuatro sleeves independientes: no reproduce una cuenta con equity compartida, el cálculo de lotaje conjunto, límites globales ni drawdown flotante.

| Escenario | Trades sumados | P&L normalizado | Retorno | R normalizadas | PF | DD al cierre |
|---|---:|---:|---:|---:|---:|---:|
| Base | 628 | +$6,584.78 | +65.85% | +26.03R | 1.884 | 7.69% |
| Costos +20% | 628 | +$6,563.61 | +65.64% | +25.96R | 1.880 | 7.71% |

La suma de trades no equivale a 628 apuestas independientes: A–C duplican las mismas operaciones y D comparte casi todas las entradas. La cartera simulada concentra así varias posiciones en el mismo movimiento de XAUUSD.

## Antecedente demo de versiones anteriores (1 de agosto–21 de septiembre de 2026)

El extracto `data/mt5_demo_trades_export.csv` contiene operaciones de una versión anterior de la flota. No debe leerse como resultado demo de A–D ni de los perfiles BTC actuales. En ese historial anterior, los cierres asociados a cuatro magics de oro suman:

| Magic legado | Cierres | P&L realizado |
|---|---:|---:|
| Oro, perfil legado A | 7 | −$232.83 |
| Oro, perfil legado B | 5 | −$264.61 |
| Oro, perfil legado C | 4 | −$195.78 |
| Oro, perfil legado D | 4 | −$94.98 |
| **Cuatro perfiles legados** | **20** | **−$788.20** |

El mismo extracto incluye tres cierres BTC de una generación previa por −$169.40 y otro cierre BTC por −$136.89. El total de esos perfiles anteriores y los cuatro magics de oro es −$1,094.49. La consulta puntual del terminal durante la auditoría encontró cero posiciones abiertas; eso solo describe el estado de la cuenta en ese momento.

En esas versiones anteriores, los cierres de oro del 21 de septiembre a las 17:02 y 22:32 UTC perdieron conjuntamente $625.27, alrededor de 79% de la pérdida de esos cuatro magics en la ventana. Este dato caracteriza el historial legado, no el desempeño actual.

El extracto también registra estrategias antiguas/no mapeadas: ETH −$44.77, SOL −$139.40, USDJPY −$146.35 y magics antiguos de XAU −$678.22. El total de cierres asociados a magics no manuales es −$2,103.23; magic 0 (operaciones manuales/filas de saldo) aporta +$587.09; el neto exportado es −$1,516.14. Estos números describen versiones y actividad previas. El usuario indica además que varios bots anteriores de otros pares y criptomonedas mostraron pérdidas repetidas en demo pese a backtests apenas positivos y fueron descartados; ese antecedente respalda no aprobar variantes con evidencia débil, pero no constituye un resultado de la flota actual.

**Flota actual en demo:** según la aclaración del usuario, todavía no ha ejecutado operaciones demo. Por tanto, no hay una muestra forward de la versión actual que permita medir win rate, expectativa, drawdown o rentabilidad realizada.

## Riesgo, configuración y ML

- Los cuatro archivos de riesgo configuran 2% por operación, límite diario 6%, drawdown máximo 10%, hasta seis posiciones y `max_portfolio_risk_percent: 12%` por perfil. Los journals y controles son separados por bot; estos archivos no acreditan un límite agregado en la cuenta MT5. Si los cuatro riesgos de 2% se aplican sobre el mismo balance, las exposiciones simultáneas pueden sumarse.
- BTC H1 tiene 0.5% de riesgo por operación y máximo una posición según su perfil. BTC H4 tiene configuración propia, pero no forma parte del lanzador actual. Ninguno de los dos tiene operaciones demo actuales según la aclaración del usuario.
- Los magics ETH/SOL/USDJPY y los magics antiguos de XAU del extracto pertenecen a actividad previa/no mapeada; no se cuentan como desempeño de la flota actual.
- A, B y C difieren en sus umbrales ML (0.45, 0.55 y 0.70), pero el backtester estándar los ignora; por eso produce resultados idénticos. D tiene ML en sombra y gestión de salida distinta (BE 0.8R y parcial 0.5R).
- El reporte ML está marcado `ACCEPTED_SHADOW_ONLY`; esa etiqueta no valida uso activo. Las corridas custom de 26 trades sí usaron el modelo final en fechas históricas anteriores a su entrenamiento y tienen fuga temporal, así que no las uso para aprobar el filtro.
- No encontré manifiestos OOS con hashes actuales de los perfiles A, C o D. Los holdouts anteriores de B tampoco son comparables con los hashes actuales ni tienen muestra suficiente.

## Diagnóstico

Las pruebas técnicas in-sample de A–D son positivas y permanecen positivas con el estrés acotado de costos. Eso respalda una ventaja histórica de la lógica compartida, pero no acredita rentabilidad de la flota actual. La generación actual aún no tiene muestra demo; el historial demo negativo citado corresponde a versiones anteriores. El drawdown histórico de A–C queda cerca del límite de 10%, las estrategias de oro están muy correlacionadas y el ML desplegado no está validado por el backtester.

La principal mejora es iniciar y registrar una evaluación forward de la versión actual como cartera real: congelar perfiles y versiones, agregar controles de riesgo a nivel de cuenta y capturar equity flotante, órdenes y fills por magic. Mantener los bots anteriores descartados separados del experimento. Evaluar ML con entrenamiento walk-forward fuera de línea antes de permitir que bloquee operaciones. Hasta obtener muestra forward, la rentabilidad actual permanece sin demostrar.
