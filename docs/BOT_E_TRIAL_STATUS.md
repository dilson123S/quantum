# Bot E: estado del ensayo y evidencia

**Actualizado:** 2026-09-23 15:33, hora de Bogotá  
**Cuenta:** ICMarketsSC-Demo, login 52984982  
**Alcance:** USDJPY M15, confirmación H1 y filtro de tendencia H4. Solo demo; `allow_live_trading: false`.

## Estado en vivo

- Bot E se reinició a las 15:31 bajo el supervisor `run_demo_bots.py --only-e` después de los ensayos de frecuencia; A–D siguen en sus procesos previos y no se duplicaron. MT5 informó conexión a ICMarketsSC-Demo, login 52984982, balance/equity $18,483.86.
- Antes del reinicio no había posiciones ni órdenes pendientes con su magic 26092655. El log confirmó conexión y descarga de velas después del inicio. El PID E se reconcilió en `data/fleet_runtime.json`; la API ahora muestra flota RUNNING, E ONLINE, modo DEMO, cero posiciones y cero cierres en la ejecución actual.
- Las señales internas recientes de E marcaron 69 y 61 frente al mínimo 75; en esas señales estructura (20/18) y tendencia (21/15) pasaron, mientras momentum y volumen quedaron en 5/15. Otra señal reciente marcó 49, con tendencia 3/15 y alineación H1 de 0. Ninguna fue operable bajo los filtros actuales.
- El radar del dashboard es un cálculo orientativo por perfil. No representa la decisión interna completa ni confirma una orden.
- El loop conserva la gestión de riesgo/posiciones cada 2 s, pero ahora sondea solo la última vela M15 cerrada; descarga y limpia el histórico completo únicamente cuando detecta una vela nueva. Se verificó en el log: 650 barras al análisis inicial y 1 barra en los sondeos siguientes. Esto reduce lecturas y ruido del log, sin alterar el score ni el criterio de entrada.
- Las velas de ICMarkets ahora se normalizan de hora de servidor a UTC en `data/mt5_time.py`, `data/downloader.py`, `execution/mt5_connector.py` y `backtest_runner.py`. El tick MT5 bruto observado era 22:50, el reloj local UTC 19:50 y la última vela bruta 22:30; tras convertir UTC+3, quedaron tick 19:50 y barra 19:30. El helper aplica UTC+2/UTC+3 según DST de Nueva York solo al servidor ICMarkets y ajusta también los límites de consulta histórica. La conversión fue compilada y comprobada contra el tick/vela actuales.
- El estado del proveedor de calendario del dashboard es **no confirmado**. La configuración de noticias está activada; no interpretar “ON” como calendario comprobado. El filtro del bot evalúa disponibilidad por separado.

## Experimentos históricos exploratorios

> **Advertencia de validez:** todos los resultados v0–v13 de la tabla y las evaluaciones OOS anteriores se generaron antes de corregir el reloj del servidor ICMarkets. Esos backtests trataban marcas UTC+2/UTC+3 como si fueran UTC mientras aplicaban filtros horarios UTC. Se conservan abajo para trazabilidad, pero quedan **supersedidos y no deben usarse como evidencia de rentabilidad**. Los ensayos v14–v17 usan timestamps normalizados; tampoco son validación final, pues las noticias históricas no están disponibles.

PF sigue la definición del motor: ganancias monetarias brutas divididas por pérdidas monetarias brutas. El porcentaje ganador cuenta cierres con más de +0.05R sobre todos los trades; los cierres entre −0.05R y +0.05R cuentan como break-even. DD es la máxima caída acumulada en R recalculada desde el CSV. Los intervalos ya se examinaron durante la exploración y **no son un holdout independiente**.

| Variante | Ventana de operaciones | Cierres | Resultado | PF | Ganadoras | DD máx. (R) | Lectura |
|---|---|---:|---:|---:|---:|---:|---|
| v0 base | 2024-05 a 2026-05 | 371 | −19.40R | 0.794 | 32.3% | 20.78R | Rechazar |
| v1 alineación H1 | 2025-05 a 2026-05 | 106 | +0.84R | 1.033 | 34.0% | 4.86R | Marginal; no valida ventaja |
| v2 alineación estricta | 2025-06 a 2026-05 | 70 | −1.28R | 0.923 | 31.4% | 3.37R | Rechazar; muestra menor |
| v3 salida por estancamiento | 2025-05 a 2026-05 | 111 | +1.90R | 1.078 | 38.7% | 5.22R | Mejor variante observada; ventaja pequeña y frágil |
| v4 estrés de costes sobre v3 | 2025-05 a 2026-05 | 111 | +1.34R | 1.054 | 38.7% | 5.54R | Sigue apenas positivo en este intervalo |
| v5 score 70 | 2025-05 a 2026-05 | 111 | +2.08R | 1.086 | 39.6% | 5.22R | No aumentó trades; mismas entradas que v3 |
| v6 entrada pullback | 2025-05 a 2026-05 | 126 | −9.12R | 0.734 | 37.3% | 15.66R | +13.5% de frecuencia; pérdida fuerte, rechazar |
| v7 vigencia pendiente 8 velas | 2025-05 a 2026-05 | 118 | +1.19R | 1.047 | 39.0% | 7.06R | +7 cierres vs v3, con menor retorno y mayor DD; rechazar |
| v8 aceptar régimen transicional | 2025-05 a 2026-05 | 160 | +1.43R | 1.036 | 38.1% | 9.36R | +44% de cierres, PF más bajo y DD mayor; rechazar |
| v9 transicional solo BUY | 2025-05 a 2026-05 | 137 | +4.21R | 1.130 | 37.2% | 6.05R | Mejora in-sample tras inspeccionar v8; no aceptada |
| v9 transicional solo BUY, año anterior | 2024-05 a 2025-05 | 131 | −5.36R | 0.834 | 34.4% | 9.52R | Negativo; no generaliza |
| v10 entrada M5 (OOS único) | 2026-06 a 2026-09 | 56 | −2.44R | 0.861 | 32.1% | 7.32R | Resultado negativo y muestra pequeña; rechazar |
| v11 objetivo 1.5R | 2025-05 a 2026-05 | 112 | +0.04R | 1.001 | 39.3% | 6.06R | Sin mejora de neto ni frecuencia; rechazar |
| v12 estrategia completa, incluida rama asiática | 2025-05 a 2026-05 | 123 | +0.14R | 1.008 | 38.2% | 7.12R | Casi neutra; rama asiática perjudica el resultado |
| v12 control estructural, rama asiática apagada | 2025-05 a 2026-05 | 111 | +1.90R | 1.078 | 38.7% | 5.22R | Réplica del v3; no elimina inestabilidad entre años |
| v12 estrategia completa, año anterior | 2024-05 a 2025-05 | 136 | −16.60R | 0.594 | 32.4% | 17.47R | Negativa; rama asiática −11.15R en 19 operaciones |
| v13 H4 confirmación ≥15, reciente | 2025-05 a 2026-05 | 64 | −1.82R | 0.873 | 37.5% | 5.10R | Menos trades que control (111); resultado peor |
| v13 H4 confirmación ≥15, previo | 2024-05 a 2025-05 | 78 | −2.73R | 0.842 | 35.9% | 5.73R | Negativa en ambos periodos; descartar |
| v13 H4 confirmación ≥20, reciente | 2025-05 a 2026-05 | 38 | −0.18R | 0.978 | 39.5% | 3.38R | Casi plana, pero −66% de operaciones frente al control |
| v13 H4 confirmación ≥20, previo | 2024-05 a 2025-05 | 62 | +1.04R | 1.085 | 41.9% | 3.44R | Mejora solo en este periodo; muestra pequeña y no robusta |
| v14 baseline estructural, UTC corregido, reciente | 2025-05 a 2026-05 | 131 | +0.99R | 1.035 | 38.2% | 3.98R | Muestra exploratoria; bootstrap P(R>0)=56.1% |
| v14 baseline estructural, UTC corregido, previo | 2024-05 a 2025-05 | 137 | −8.75R | 0.718 | 35.8% | 11.82R | Negativo; bootstrap P(R>0)=8.0% |
| v15 H4 ≥20, UTC corregido, reciente | 2025-05 a 2026-05 | 44 | −0.04R | 0.995 | 36.4% | 3.38R | Casi plana, pero −66% operaciones; bootstrap P(R>0)=48.2% |
| v15 H4 ≥20, UTC corregido, previo | 2024-05 a 2025-05 | 71 | −1.39R | 0.901 | 42.3% | 5.86R | No mejora; bootstrap P(R>0)=36.9% |
| v16 aceptar TRANSITIONAL, UTC corregido, reciente | 2025-05 a 2026-05 | 193 | −3.62R | 0.931 | 35.8% | 10.56R | +47% operaciones frente a v14; retorno y DD peores; P(R>0)=34.6% |
| v17 TRANSITIONAL solo BUY, UTC corregido, reciente | 2025-05 a 2026-05 | 169 | +0.01R | 1.004 | 35.5% | 7.52R | +29% operaciones, prácticamente sin retorno; P(R>0)=49.2% |
| v17 TRANSITIONAL solo BUY, UTC corregido, previo | 2024-05 a 2025-05 | 164 | −6.35R | 0.828 | 37.2% | 13.07R | Negativo; P(R>0)=19.4% |
| v3, año anterior | 2024-05 a 2025-05 | 118 | −6.51R | 0.767 | 33.9% | 8.44R | Rechaza estabilidad entre regímenes |

Archivos de trazabilidad: `data/experiments/bot-e-v*.json` y los CSV `data/backtest_20260923_*.csv` referenciados por cada manifiesto. Costes base registrados: comisión $7 por lote ida y vuelta, slippage 1 punto y gap slippage 2 puntos. v4 eleva esos costes en 20%.

El ensayo v5 cambió solamente el score mínimo de 75 a 70 sobre las mismas barras de v3. Produjo los mismos 111 horarios/direcciones de entrada y las mismas razones de salida: bajar el score **no generó más operaciones**. El R agregado pasó de +1.90 a +2.08, pero cambió el `tick_value` descargado desde MT5 (0.631345 a 0.632023), alterando el tamaño de las posiciones. Por ello no atribuyo esa diferencia de resultado al umbral; es una corrida de sensibilidad sin mejora demostrada de frecuencia.

El ensayo v6 conservó score 75 y los mismos archivos de barras, cambiando solo `entry_mode` a `pullback`. Sí aumentó los cierres a 126, pero perdió $57.15 (−9.12R), con PF 0.7339 y caída máxima de 15.66R. Solo 27.4% de las órdenes pendientes se activaron. No se traslada a la configuración demo.

El ensayo v7 cambió únicamente `breakout_expiry_bars` de 4 a 8 sobre la misma ventana histórica de v3. Tuvo 118 cierres (+7), +1.19R, PF monetario 1.0474 y DD máximo recalculado de 7.06R, frente a 111 cierres, +1.90R, PF 1.078 y 5.22R de DD en v3. Su bootstrap dio percentil 5 de −8.84R y P(R>0) de 57.8%. Aumentar la vigencia produjo más cierres, pero peor resultado y mayor caída; se rechaza y no se aplica a demo. Es in-sample, no evidencia independiente. Manifiesto y archivos: `data/experiments/bot-e-v7-expiry8-exploratory-20260923T182259Z.json`, `data/backtest_20260923_132259.csv` y `data/pending_events_20260923_132259.csv`.

La auditoría de candidatos v3 evaluó 24,986 cierres de vela y el movimiento direccional cierre-a-cierre en 12 velas (3 horas), antes de costes. En candidatos con score 70–75, el retorno medio fue −0.063 ATR y 47.0% terminó positivo; no respalda bajar el score. Las observaciones se solapan y no son trades ni expectativa realizable. CSV: `data/candidate_audit_20260923_133626.csv`; manifiesto: `data/experiments/bot-e-v3-candidate-audit-20260923T183626Z.json`.

La variante v8 permitió `TRANSITIONAL` manteniendo los demás filtros: subió de 111 a 160 cierres, pero quedó en +1.43R, PF 1.0356 y DD 9.36R. Hubo 59 cierres transicionales por +1.11R y desplazamiento de operaciones TRENDING por serialización. La segmentación BUY/SELL de ese mismo período motivó v9, que habilita solo BUY en transición. v9 logró +4.21R con 137 cierres en 2025–26, pero perdió −5.36R, PF 0.8343 y DD 9.52R en el año anterior; queda rechazada por falta de estabilidad. Se agregó al esquema `regime_filter.allowed_directions_by_regime`; vacío conserva el comportamiento previo y ninguna configuración activa lo usa. Manifiestos: `data/experiments/bot-e-v8-transitional-exploratory-20260923T184049Z.json`, `data/experiments/bot-e-v9-transitional-buy-only-exploratory-20260923T184536Z.json` y `data/experiments/bot-e-v9-transitional-buy-only-prior-year-20260923T184748Z.json`.

La variante v10 cambió solo la entrada de M15 a M5, conservando H1/H4. La descarga de un año M5 falló y no cuenta como resultado. Con el histórico disponible y reglas fijadas, la evaluación única desde 2026-06-01 hasta 2026-09-23 registró 56 cierres, −2.44R, PF 0.8605 y bootstrap P(R>0) 34.3%; se rechaza. Manifiesto: `data/experiments/bot-e-v10-m5-entry-oos-june-sep-single-20260923T185206Z.json`.

La variante v11 mantuvo M15 y cambió solo el objetivo de 2R a 1.5R. En 2025–26 hizo 112 cierres, +0.04R, PF 1.0013 y bootstrap P(R>0) 50.9%; se rechaza. Manifiesto: `data/experiments/bot-e-v11-rr15-exploratory-20260923T185441Z.json`.


La investigación de huso horario encontró un desfase de +3 h en el timestamp bruto del servidor ICMarkets durante septiembre, mientras que en enero aplica +2 h y en julio +3 h. `data/mt5_time.py` corrige la conversión histórica y la consulta por rango. Los backtests v14 reejecutaron la ruptura estructural con Asian desactivado, noticias apagadas para replay, coste base y datos UTC ya normalizados. Resultado reciente: 131 operaciones, +0.99R, PF 1.0354, DD 3.98R, bootstrap P(R>0)=56.1%. Año previo: 137 operaciones, −8.75R, PF 0.7177, DD 11.82R, P(R>0)=8.0%. La inestabilidad permanece.

El filtro H4 de v15, repetido con el reloj corregido, produjo 44 operaciones/−0.04R/PF 0.9954 en el año reciente y 71/−1.39R/PF 0.9013 en el previo. Redujo mucho la frecuencia y no mejoró el resultado de manera consistente; queda apagado en la configuración activa. Manifiestos: `data/experiments/` con prefijos `bot-e-v14-timefix-` y `bot-e-v15-h4-20-`.

Para aumentar frecuencia se reejecutó con UTC correcto la apertura del régimen `TRANSITIONAL`. V16 elevó operaciones de 131 a 193 (+47%), pero perdió −3.62R, PF 0.931, con DD 10.56R. V17 permitió BUY solamente en transición: reciente 169 operaciones, +0.01R, PF 1.004; año previo 164 operaciones, −6.35R, PF 0.828. Ninguna alcanza mejor frecuencia con ventaja rentable estable; ambas quedan descartadas. CSV: `data/backtest_20260923_151652.csv`, `data/backtest_20260923_152407.csv`, `data/backtest_20260923_153101.csv`. Manifiestos: `data/experiments/` con prefijo `bot-e-v16-transitional-utc-` y `bot-e-v17-trans-buy-utc-`.

## Evaluación OOS de USDJPY: junio–septiembre de 2026 — pre-normalización, invalidada

Se evaluó una sola vez, sin ajustar después, la estrategia activa de ruptura con score 75, alineación H1, mínimos de estructura/tendencia y salida por estancamiento. En USDJPY obtuvo **25 cierres, +2.4R, PF 1.5103, 9 ganadoras, 10 perdedoras y 6 break-even** entre 2026-06-01 y 2026-09-22. El bootstrap registró percentil 5 de −2.60R y probabilidad de resultado positivo de 76.4%; el backtester la marca **NO ACEPTAR** porque la muestra OOS tiene menos de 100 operaciones.

Esta OOS se ejecutó con una copia de la configuración activa, pero con `news_filter: false`: no existe replay histórico del calendario, por lo que no sería válido usar las noticias actuales para filtrar velas históricas. Es un resultado preliminar de la estrategia, no una validación exacta de toda la configuración demo. Manifiesto y datos: `data/experiments/bot-e-current-rules-oos-june-sep-20260923T180438Z.json`, `data/backtest_20260923_130438.csv` y `data/pending_events_20260923_130438.csv`. El holdout queda cerrado para este tuning.

## Evaluación OOS de otros majors — pre-normalización, invalidada

Con el mismo intervalo, score y reglas de entrada/salida, EURUSD registró 35 cierres, −2.3R, PF 0.8015 y 34.3% ganadoras; GBPUSD registró 28 cierres, −1.3R, PF 0.8471 y 32.1% ganadoras. En conjunto son 63 operaciones exploratorias; bootstrap: percentil 5 de −12.22R y probabilidad de resultado positivo de 25.8%. El evaluador marca NO ACEPTAR por muestra OOS menor de 100.

La simulación procesa cada símbolo con saldo propio y declara que no es una cartera combinada, así que no atribuyo su P&L agregado a una cuenta compartida. Ambos pares fueron negativos en este holdout; no los agrego al Bot E demo. Manifiesto y operaciones: `data/experiments/bot-e-major-pairs-oos-june-sep-20260923T181017Z.json` y `data/backtest_20260923_131017.csv`. La prueba usa la misma copia con calendario histórico apagado y no modifica la configuración demo.

## A–D: flota XAUUSD — resultados pre-normalización, pendientes de repetir

La auditoría exploratoria de dos años (corte exclusivo 2026-06-01) también reporta resultados positivos en históricos: A, B y C registran 151 cierres, +27.37R, PF 1.861 y DD 8.76%; D registra 175 cierres, +22.00R, PF 1.988 y DD 7.82%. Con costes +20%, PF queda entre 1.857 y 1.982, con resultados casi iguales.

Estos números **no certifican la flota actual**: los manifiestos los clasifican como exploratorios, sin holdout (`is_oos_evaluation: false`) y reportan `ml_applied: false`. Además, las descargas MT5 de esa auditoría preceden la corrección del reloj ICMarkets, así que las ventanas de sesión también pudieron desplazarse. Hay que recalcular A–D antes de apoyarse en esos P&L. A, B y C tienen resultados idénticos a pesar de sus umbrales ML distintos, por lo que esa prueba no compara el efecto real de sus filtros ML. La telemetría de versiones anteriores en demo tampoco se atribuye a estas configuraciones actuales. Fuente: `data/experiments/fleet_audit_summary_20260922.csv` y sus manifiestos.

## Diferencia entre la variante probada y el proceso activo

La configuración activa conserva la estructura estratégica de v3, pero **no es idéntica** a la configuración del backtest. El manifiesto v3 y la configuración actual tienen hashes distintos. Entre los cambios visibles están:

- El ensayo v3 tenía filtro de noticias apagado; E lo tiene encendido.
- v3 permitía pérdida diaria de 1%, drawdown máximo 5% y cuatro pérdidas consecutivas; E usa 0.5%, 3% y tres.
- El grupo de correlación de v3 era USD; E limita el grupo a JPY.
- La estrategia sigue en modo breakout, con confirmación de tendencia H1, mínimo de score 75 y salida por estancamiento a ocho velas si no progresa 0.3R.

Por eso, los resultados de v3/v4 ayudan a elegir hipótesis, pero no predicen exactamente el comportamiento de la configuración activa.

La brecha de cobertura del simulador se atendió parcialmente: el motor ya simula la entrada de `AsianBreakdownStrategy` y exporta `entry_strategy` al CSV. La comparación v12 incluyó la ruta completa y un control con esa ruta apagada. En 2025–26, la ruta asiática produjo 13 operaciones por −1.88R (PF en R 0.774; DD 4.96R) y desplazó parte de las rupturas estructurales; el total fue apenas +0.14R (PF monetario 1.008; DD 7.12R). En 2024–25, la ruta asiática produjo 19 operaciones por −11.15R (PF en R 0.168; DD 10.33R), con BUY y SELL negativos; la estrategia total perdió −16.60R (PF monetario 0.594; DD 17.47R). La ruta estructural por sí sola replicó v3 en 2025–26 (+1.90R) y siguió negativa el año anterior (−6.51R). Estos resultados respaldan apagar `asian_breakdown` para el ensayo demo actual, no declaran rentable la estrategia estructural.

La simulación de la rama asiática es aproximada y no idéntica al runtime: el replay histórico apaga noticias porque no hay calendario histórico, modela entrada/costes con OHLC y una política intrabar stop-first, y parte de los límites de riesgo no comparte exactamente el mismo estado que los componentes live. Se compiló el motor tras los cambios; los resultados de esta rama deben leerse como exploratorios, no como reproducción bit a bit.

## Decisión provisional

Mantener E solo en demo con 0.25% por operación, `allow_live_trading: false`, ruta asiática explícitamente desactivada y sweep reversal apagado. La confirmación H4 y la extensión TRANSITIONAL quedan apagadas. Las variantes que aumentaron trades no dieron una ventaja: v16 perdió −3.62R con 193 operaciones; v17 casi no ganó en la ventana reciente y perdió −6.35R el año previo. Con reloj corregido, la línea base también varía mucho entre años (+0.99R frente a −8.75R); no hay edge rentable demostrado. El ensayo forward actual todavía no tiene cierres. E está reiniciado con timestamps UTC normalizados y sondeo de una sola vela; el dashboard reporta RUNNING/ONLINE.

La flota A–D tiene riesgo configurado de 2% por operación, hasta seis posiciones por perfil y drawdown límite de 10%, mientras E usa 0.25%, una posición y 3%. Como A–D operan XAUUSD, sus exposiciones también pueden coincidir en el mismo activo. Ese riesgo está configurado en demo y se muestra aquí para evaluación; no se cambió durante este trabajo.

## v18: salida por estancamiento a 5 velas — no promovida

Se comparó una copia de la misma configuración de control v12 (UTC normalizado, Asian apagado, noticias históricas apagadas) cambiando únicamente `stagnation_exit_bars` de 8 a 5; `stagnation_min_progress_r` siguió en 0.3. Frente a v14, el periodo reciente (corte 2026-06-01) pasó de 131 a 134 operaciones (+3), pero de +0.99R/PF 1.0354 a −0.00R/PF 1.0135. En el año anterior (corte 2025-06-01) pasó de 137 a 144 (+7); la pérdida mejoró de −8.75R/PF 0.7177 a −5.35R/PF 0.8168, pero siguió siendo negativa. Bootstrap del periodo reciente: P(R>0)=49.7%, percentil 5 −10.20R; año previo: 19.3%, percentil 5 −15.85R. El cambio no aporta una ventaja estable ni un aumento material de frecuencia, así que **no se aplica a demo**. Manifiestos y CSV: `data/experiments/bot-e-v18-stagnation5-utc-*` y `data/backtest_20260923_153920.csv`, `data/backtest_20260923_154212.csv`.

Tras la prueba, E volvió a arrancar con la configuración activa original (`stagnation_exit_bars: 8`), modo DEMO, `allow_live_trading: false`, riesgo 0.25%, y sin posiciones abiertas. El dashboard confirmó E ONLINE y la flota RUNNING en `ICMarketsSC-Demo` (cuenta 52984982); A–D no se reiniciaron.

## Addendum 2026-09-23 21:37, hora de Bogotá: auditoría de la fuente Alchemist y ensayos recientes

### La fuente pública enseña una variante distinta a la del PDF

Revisé las transcripciones de las lecciones públicas de [Alchemist SnR, parte 1](https://www.youtube.com/watch?v=Bwktah4s79k) y [parte 3](https://www.youtube.com/watch?v=CH9iW5caHHk), del canal MR.R. La transcripción automática de YouTube puede contener errores; los puntos siguientes son los que se repiten claramente:

- La parte 1 propone **mensual, semanal y diario para dirección**, con **H4 y H1 para refinar niveles y buscar entradas**; recomienda apartarse de M1/M5/M15. Define MSNR en el gráfico de línea/precios de cierre: formaciones A/V, soportes/resistencias y cambios RBS/SBR.
- La parte 3 pone primero el nivel MSNR y usa la línea de tendencia como confluencia. Describe una entrada de confirmación como rechazo en H4 seguido por ruptura/cierre de nivel y estructura en H1. London y New York son ventanas que el instructor prefiere, pero dice que no son un requisito horario absoluto.
- Para pares FX, la explicación de la parte 1 limita la liquidez SMC a sesiones y máximos/mínimos previos de día, semana y mes; no presenta “inducement” como filtro central.
- El instructor habla de stops dependientes de la zona (en ejemplos, 20–30 pips) y objetivos superiores a 100 pips o al menos 1:4. No deja en estas clases una regla universal de conversión de “pip”, selección algorítmica de storyline/zonas, ni un protocolo completo de salida. Sus afirmaciones verbales de alta precisión no incluyen conteo de operaciones, periodo, costes ni resultados auditables.

Esto **no es la misma variante** que el PDF proporcionado: aquel enfatiza el sweep de London y el CRT NY de 13:00/14:00/15:00 con entrada M15. `strategy/alchemist.py` implementa principalmente esa segunda variante. Por tanto, los resultados de la implementación actual no validan automáticamente la enseñanza MSNR H4/H1 de MR.R, y mezclar ambas en un solo backtest cambiaría la hipótesis. Vídeos: [parte 1](https://www.youtube.com/watch?v=Bwktah4s79k&t=74s) y [parte 3, ejemplo de confirmación H4/H1](https://www.youtube.com/watch?v=CH9iW5caHHk&t=153s).

### Resultados registrados para Alchemist

- **Bot E / USDJPY:** la corrida Alchemist v20 de junio de 2026 en adelante obtuvo 26 cierres y **−13.41R**; la variante solo NY obtuvo 11 y **−2.67R**. No se promueven.
- **Bot G / XAUUSD:** el manifiesto Alchemist ya guardado presenta 23 cierres IS por **−8.69R** y 17 cierres OOS por **+7.91R**. La diferencia de signo no demuestra una ventaja estable; el OOS de 17 operaciones es todavía una muestra corta.
- En la base actual de E hay **27 señales y 0 trades** entre 2026-09-23 17:28 y 2026-09-24 02:29 UTC; las 27 quedaron rechazadas por los filtros de puntuación/alineación. El máximo fue 77/100. Esto mide actividad de señales, no rentabilidad ni una falla de ejecución.

Se probaron muchas variantes de E en esta sesión de investigación; los estudios sobre selección múltiple advierten que escoger la mejor entre muchas configuraciones puede inflar artificialmente el resultado ([Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), [Deflated Sharpe Ratio](https://doi.org/10.2139/ssrn.2460551)). La decisión sigue siendo **no cambiar los parámetros activos ni presentar Alchemist como rentable**. El siguiente candidato requiere fijar una sola variante antes de mirar otro holdout: MSNR H4/H1 de MR.R o el modelo CRT/M15 del PDF. Los elementos discrecionales y la gestión por “pips” deben concretarse para el símbolo elegido antes de codificar una prueba que pueda compararse honestamente.

### Reglas de storyline observadas e implementación de un candidato aislado

La transcripción de la [parte 4](https://www.youtube.com/watch?v=QbyhRLF53RI) precisa que una storyline diaria empieza con rechazo de un nivel D1 y ruptura de estructura H4 en la misma dirección; termina cuando se confirma la secuencia inversa. El instructor permite mantener historias distintas por marco —por ejemplo, semanal alcista y diaria bajista— y buscar la historia diaria hacia una zona semanal. Para una entrada confirmada, H4 rechaza un nivel H4 y H1 confirma con ruptura de open/close o estructura; si la dirección no está clara, espera. También presenta objetivos en niveles cercanos y stops basados en la zona. Sus ejemplos de tres trades diarios y 0.30% son reglas personales, no parámetros universales.

Se agregó la opción independiente `entry_model: msnr_storyline_h1`, separada del modelo CRT/M15. Para que el material discrecional se pueda reproducir, el candidato define rechazo como vela cerrada que toca una zona MSNR A/V y cierra del lado de rechazo; la confirmación de storyline como cierre estructural del marco inferior dentro de ocho velas; confirmación H1 como cierre más allá de los cuerpos de las últimas velas H1 (lookback ajustable; el baseline inicial usó cinco y la versión congelada usa tres); stop más allá del extremo de la vela H4 rechazada; target en el primer nivel MSNR o liquidez previa día/semana/mes/rango asiático; mínimo RR 2. Estas cifras operacionales son decisiones del código, **no reglas atribuidas textualmente al instructor**. Las ventanas se filtran usando solo velas cerradas.

El perfil separado `config/bot_e_msnr_storyline` está en modo **paper**, con `allow_live_trading: false`, estado `UNVALIDATED`, H1 como marco de entrada y journal propio. La configuración actual de Bot E demo y los procesos activos de la flota no se modificaron ni reiniciaron. En el momento de escribir este addendum, el candidato aún no se había backtesteado ni tenía evidencia forward; por eso no se podía llamar rentable. La evaluación debe usar costes realistas y un holdout fijado antes de examinarlo, porque buscar entre muchas variantes puede inflar el resultado ([Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), [Deflated Sharpe Ratio](https://doi.org/10.2139/ssrn.2460551)).

## Addendum 2026-09-23: resultado reservado y sensibilidad H1

La primera versión de este modelo se congeló con ruptura H1 del cuerpo de las últimas 3 velas, RR mínimo 2 y los demás filtros descritos arriba. Su evaluación reservada, USDJPY del 2026-06-01 al 2026-09-22, con calentamiento previo de seis meses, produjo **0 operaciones**. Manifiesto: `data/experiments/bot-e-msnr-storyline-h1look3-oos-jun-sep-freeze-20260924T035243Z.json`. No satisface el objetivo de frecuencia y no se acepta para forward.

Una variante exploratoria cambió solo el lookback de ruptura H1 de 3 velas a 1 en el tramo de desarrollo de dos años (2024-05-19 a 2026-05-31). Produjo **3 operaciones, +0.30R, PF 1.1095**, con 1 ganadora y 2 perdedoras; el bootstrap fue −4.27R en percentil 5 y P(R>0)=59.4% (n=3). La única operación adicional no convirtió la estrategia en una alternativa rentable. CSV: `data/backtest_20260923_225825.csv`; manifiesto: `data/experiments/bot-e-msnr-storyline-h1look1-exploratory-20260924T035825Z.json`. El perfil de investigación vuelve a lookback 3, que es la variante exacta cuyo hash consta en la evaluación reservada. No se cambió `config/bot_e` ni se enviaron órdenes.

Se repitió el modelo congelado de lookback 3 en **XAUUSD** (2024-05-19 a 2026-05-31), ya que las lecciones suelen usar oro como ejemplo. El backtest produjo **0 operaciones**. No se promueve un cambio de USDJPY a XAUUSD ni se añade oro a Bot E; Bot G y la flota activa permanecen intactos. Manifiesto: `data/experiments/bot-e-msnr-storyline-xauusd-h1look3-exploratory-20260924T040619Z.json`.

También se probó un fallback jerárquico que usa storyline D1 y, solo si falta, W1 o MN1, manteniendo los mismos disparadores H4/H1. En el mismo tramo de desarrollo de dos años volvió a producir las mismas **2 operaciones y +1.49R** que el modelo D1, sin subir la frecuencia. No se promueve; la opción queda apagada por defecto y en el perfil paper (`allow_higher_storyline_fallback: false`). Manifiesto: `data/experiments/bot-e-msnr-storyline-macro-fallback-exploratory-20260924T041344Z.json`.

La búsqueda pública de la estrategia encontró las lecciones originales de [Alchemist SnR, parte 1](https://www.youtube.com/watch?v=Bwktah4s79k) y [parte 4](https://www.youtube.com/watch?v=QbyhRLF53RI), además de resúmenes/transcripciones automáticas de terceros. Describen análisis de cierres MSNR en mensual/semanal/diario y confirmación H4/H1; no aportan una serie auditada de operaciones, costes y resultados que demuestre rentabilidad. Las frases promocionales de precisión o éxito no se tratan como evidencia. Como se examinó el periodo junio-septiembre al probar la primera versión, las variantes futuras necesitarán un holdout nuevo estrictamente posterior al 2026-09-23, además de pruebas walk-forward con suficiente cantidad de operaciones.
