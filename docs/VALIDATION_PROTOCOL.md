# Protocolo de validación y control de sobreajuste

Este documento es una regla operativa: una configuración no puede pasar a
demo ni a capital real porque tenga el mejor resultado de una búsqueda.

## 1. Congelar primero

Antes de ejecutar un backtest de validación, registra en un commit o una copia
inmutable:

- `settings.yaml`, `symbols.yaml` y `risk.yaml`;
- broker, tipo de cuenta, comisión, spread y contrato por símbolo;
- rango histórico, zona horaria y fuente de datos;
- hipótesis de slippage, gap y prioridad intrabar.

El runner genera `data/experiments/<id>.json`, con hashes de estos insumos. Si
cambia cualquier parámetro tras mirar un resultado OOS, ese resultado queda
marcado como exploratorio y se necesita un nuevo bloque OOS intacto.

## 2. Separación temporal fija

Usa como mínimo tres bloques cronológicos, sin mezclar velas:

| Bloque | Uso permitido |
|---|---|
| Desarrollo (60%) | Diseñar reglas y eliminar errores de implementación. |
| Validación (20%) | Elegir una sola configuración desde una lista predefinida. |
| Holdout (20%) | Ejecutar una vez; no tocar parámetros después. |

Para el holdout, conserva barras anteriores sólo para calentar indicadores y
ejecuta, por ejemplo:

```powershell
python backtest_runner.py --period 1Y --oos-start 2026-01-01 --experiment-name baseline-v1
```

El motor no crea señales anteriores a `--oos-start`; las barras anteriores
sólo alimentan EMA, ATR y estructura.

## 3. Costes y reglas conservadoras

La simulación usa spread por vela, contrato real leído de MT5, comisión por
lote, slippage adverso y slippage adicional de gaps. Una vela que toca SL y TP
se cuenta como SL. Un breakout se simula como una orden stop pendiente:
no se transforma en entrada a mercado, expira y se invalida como en el bot.

Actualiza costes únicamente desde extractos de IC Markets. No los reduzcas
para mejorar el resultado; cada cambio exige un nuevo manifiesto.

## 4. Criterios de aceptación

Antes de pasar a demo, exige simultáneamente en el holdout combinado y por
símbolo:

- expectativa neta positiva y PF neto > 1.10;
- drawdown dentro del límite de riesgo, con margen; 
- al menos 100 operaciones de holdout o evidencia adicional de varios
  regímenes independientes;
- ningún símbolo o mes es el único responsable del beneficio;
- la estrategia sigue positiva ante ±20% de comisión/slippage y cambios
  moderados de buffer/SL/TP.

El runner también imprime un bootstrap reproducible de los R realizados. Su
percentil 5% debe ser positivo para apoyar un resultado robusto, pero un
bootstrap con menos de 100 operaciones OOS se marca siempre como insuficiente.

Una vez aprobada, ejecuta forward test demo con el mismo manifiesto, sin
retocar reglas, y compara precio de fill, spread, comisión y resultado real
contra la simulación. El ML permanece en `shadow_mode` hasta que demuestre una
mejora neta fuera de muestra frente al baseline sin ML.

## 5. Perfiles de alta frecuencia

Un objetivo de frecuencia es una métrica de evaluación, jamás una condición
para forzar una orden. Antes de demo, el perfil debe demostrar tanto el ritmo
objetivo como los criterios de aceptación anteriores. Si alcanza muchos trades
pero la expectativa es negativa, queda rechazado: no se corrigen umbrales con
el mismo bloque histórico. Véase `INTRADAY_PROFILE.md` para el experimento M5.
