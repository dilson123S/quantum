# Perfil intradía M5 — experimento aislado

El perfil `config/intraday` busca **5–6 operaciones al día de media**, no una
cuota obligatoria. Usa entrada M5, confirmación de tendencia M15 y contexto
H1. Es independiente de la estrategia base M15 y arranca en `paper` con
`allow_live_trading: false`.

## Límites de seguridad

- Riesgo máximo por operación: 0.25%.
- Pérdida diaria máxima: 1.0%; drawdown máximo: 5.0%.
- Máximo tres posiciones/órdenes reservadas y una por símbolo.
- Máximo una posición altamente correlacionada adicional.
- ML sólo observa; no bloquea ni habilita operaciones.
- Toda candidata rechazada por score se registra con el motivo
  `SHADOW_REJECTED_SCORE`; este registro no puede abrir una orden.

## Resultado exploratorio inicial — no apto para demo

La primera corrida exploratoria produjo 37 entradas
entre 2026-07-22 y 2026-07-28: 6.8 entradas por día calendario dentro de su
ventana activa. Cumplió la frecuencia, pero cerró aproximadamente **-4.17 R**.
Por tanto, este perfil está **rechazado para ejecución demo** en su estado
actual.

El manifiesto de esa primera corrida no es utilizable como evidencia porque
se detectó y corrigió después un hash de configuración que apuntaba al perfil
base. Cualquier nueva prueba genera el manifiesto con los YAML del perfil que
se haya pasado en `--config`.

Ese resultado no debe usarse para subir/bajar thresholds. Es exploratorio,
la muestra es pequeña y el backtester ejecuta cada símbolo de forma
independiente; no representa aún la cartera conjunta ni sus límites de tres
posiciones.

## Siguiente validación permitida

1. Mantener este perfil sin cambios y medir sus candidatas durante un bloque
   cronológico futuro, o rediseñar una hipótesis concreta antes de elegir un
   nuevo bloque no observado.
2. Exigir 100+ operaciones OOS, expectativa neta positiva, PF > 1.10 y
   resultado positivo por más de un símbolo y mes.
3. Simular una cartera conjunta antes de usar el resultado para decidir
   exposición multi-símbolo.
4. Sólo entonces lanzar explícitamente en demo:

```powershell
python main.py --config config/intraday --mode demo
```

El modo `demo` valida que la cuenta MT5 sea demo; nunca habilita capital real.
