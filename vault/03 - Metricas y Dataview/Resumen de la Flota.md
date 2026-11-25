---
titulo: Resumen de Rendimiento de la Flota
tags:
  - metricas
  - dataview
  - reportes
  - rendimiento
---

# 📊 Resumen de Rendimiento de la Flota Multi-Bot

<div class="quantum-grid">
  <div class="quantum-card">
    <div class="quantum-card-title">Operaciones Totales</div>
    <div class="quantum-card-value cyan">9</div>
    <div class="quantum-card-sub">Septiembre 17 - 18, 2026</div>
  </div>
  <div class="quantum-card">
    <div class="quantum-card-title">Tasa de Acierto</div>
    <div class="quantum-card-value green">100.0%</div>
    <div class="quantum-card-sub">9 Victorias · 0 Pérdidas</div>
  </div>
  <div class="quantum-card">
    <div class="quantum-card-title">Beneficio Realizado</div>
    <div class="quantum-card-value green">+$219.01 USD</div>
    <div class="quantum-card-sub">Balance: $19,012.43 USD</div>
  </div>
  <div class="quantum-card">
    <div class="quantum-card-title">Profit Factor</div>
    <div class="quantum-card-value purple">∞</div>
    <div class="quantum-card-sub">Sin pérdidas registradas</div>
  </div>
</div>

---

## ⚡ Consultas Dinámicas (Dataview Engine)

### 1. Todas las Operaciones Registradas en el Diario

```dataview
TABLE file.link as "Ticket / Autopsia", fecha as "Fecha", bot as "Bot", direccion as "Tipo", volumen as "Lotes", profit_usd as "P&L ($)", profit_r as "R", tipo_salida as "Salida"
FROM "02 - Diario de Trades"
SORT fecha DESC
```

### 2. Rendimiento Consolidado por Bot

```dataview
TABLE length(rows) as "Total Trades", sum(rows.profit_usd) as "P&L Total USD", round(average(rows.profit_r), 2) as "R Promedio"
FROM "02 - Diario de Trades"
GROUP BY bot
```

### 3. Distribución por Mecanismo de Salida

```dataview
TABLE length(rows) as "Frecuencia", sum(rows.profit_usd) as "USD Cobrado"
FROM "02 - Diario de Trades"
GROUP BY tipo_salida
```

---

## 📋 Registro Histórico Consolidado (Tabla Fija)

| Fecha / Hora | Bot | Ticket | Dirección | Volumen | Entrada | Salida | P&L ($) | R Múltiplo | Mecanismo de Salida | Autopsia |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: |
| `2026-09-17 18:10` | [[Bot B - Max Beneficio\|BOT-B]] | `1941365708` | **SELL** | 0.05 | 4,359.98 | 4,343.51 | **+$82.35** | +0.88R | TAKE PROFIT | [[2026-09-17_BOT-B_Ticket_1941365708\|Ver]] |
| `2026-09-17 21:09` | [[Bot D - Estancamiento\|BOT-D]] | `1941028414` | **SELL** | 0.03 | 4,356.37 | 4,344.65 | **+$36.42** | +0.39R | ESTANCAMIENTO | [[2026-09-18_BOT-D_Ticket_1941028414\|Ver]] |
| `2026-09-17 23:00` | [[Bot A - Alta Frecuencia\|BOT-A]] | `1941364966` | **SELL** | 0.03 | 4,361.02 | 4,360.41 | **+$37.09** | +0.50R | TP PARCIAL + BE | [[2026-09-18_BOT-A_Ticket_1941364966\|Ver]] |
| `2026-09-18 02:17` | [[Bot C - Sniper\|BOT-C]] | `1941028404` | **SELL** | 0.03 | 4,355.99 | 4,355.17 | **+$3.72** | +0.05R | MANUAL VERDE | [[2026-09-18_BOT-C_Ticket_1941028404\|Ver]] |
| `2026-09-18 02:17` | [[Bot A - Alta Frecuencia\|BOT-A]] | `1941028408` | **SELL** | 0.03 | 4,356.07 | 4,355.15 | **+$4.02** | +0.05R | MANUAL VERDE | [[2026-09-18_BOT-A_Ticket_1941028408\|Ver]] |
| `2026-09-18 02:17` | [[Bot B - Max Beneficio\|BOT-B]] | `1941028430` | **SELL** | 0.03 | 4,356.42 | 4,354.86 | **+$5.94** | +0.08R | MANUAL VERDE | [[2026-09-18_BOT-B_Ticket_1941028430\|Ver]] |
| `2026-09-18 02:44` | [[Bot C - Sniper\|BOT-C]] | `1942315276` | **BUY** | 0.03 | 4,359.19 | 4,363.60 | **+$13.23** | +0.15R | MANUAL VERDE | [[2026-09-18_BOT-C_Ticket_1942315276\|Ver]] |
| `2026-09-18 02:44` | [[Bot A - Alta Frecuencia\|BOT-A]] | `1942316175` | **BUY** | 0.03 | 4,359.42 | 4,365.15 | **+$17.19** | +0.19R | MANUAL VERDE | [[2026-09-18_BOT-A_Ticket_1942316175\|Ver]] |
| `2026-09-18 03:15` | [[Bot D - Estancamiento\|BOT-D]] | `1942315774` | **BUY** | 0.03 | 4,359.29 | 4,365.64 | **+$19.05** | +0.21R | MANUAL VERDE | [[2026-09-18_BOT-D_Ticket_1942315774\|Ver]] |

---

## 🔗 Navegación Rápida
- [[00 - Inicio|🏠 Panel de Inicio]]
- [[Reglas de Riesgo y Cosecha Inteligente|🛡️ Reglas de Cosecha]]
- [[2026-09-18 - Leccion de Cosecha y Retrocesos en Oro|💡 Bitácora: Lección de Retrocesos]]
