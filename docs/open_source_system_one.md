# Open-Source System 1 Decision Architecture (Laya, Kev, NanoJev)

Esta arquitectura integra modelos de decisión de **"Sistema 1" (no autorregresivos, estructurados y de ultra-baja latencia)** como alternativas 100% de código abierto y gratuitas al API de pago de TypeSafe Jev.

---

## 1. Los Tres Modelos Integrados

### ⚡ 1. Laya (Convai Innovations) — *El Rey de la Velocidad*
* **Arquitectura:** Codificador ModernBERT / ONNX (421M parámetros).
* **Velocidad:** **10 ms a 40 ms** en ejecución local sobre GPU (NVIDIA RTX 3050) o CPU.
* **Propósito:** Decisiones binarias instantáneas y filtrado de reglas por vela cerrada:
  * ¿La evidencia técnica apoya la tesis de entrada? (`ALIGNED` / `CONTRADICTORY`)
  * Calidad de la evidencia (Escala 1 a 5).
  * Riesgo de trampa institucional / fakeout (`POTENTIAL_TRAP` vs `GENUINE_EXPANSION`).
* **Costo:** **$0.00 USD (100% Local y Open Source)**.

### 🧠 2. Kev (Jared Palmer) — *Razonamiento Macro y Contextual*
* **Arquitectura:** Modelos Qwen de 0.6B a 9B (Kev-4B).
* **Propósito:** Análisis contextual profundo:
  * Correlación con el calendario macroeconómico (ForexFactory).
  * Narrativa multi-sesión (Asia, Londres, New York) y minutas de la Fed.
  * Formato compatible directamente con el esquema `/v1/systemone`.
* **Despliegue:** Servidor local (vLLM / Ollama) o endpoints OpenRouter.

### 📊 3. NanoJev — *Monitoreo Multiactivo por Lotes (Batching)*
* **Arquitectura:** 0.6B ultra-optimizada.
* **Propósito:** Evalúa en **un solo paso paralelo de tensor** todos los activos vigilados:
  * `[Crash 1000, Boom 1000, Boom 500, XAUUSD, USDJPY]` procesados simultáneamente.
  * Ideal para la flota de bots donde se analizan múltiples gráficos al mismo tiempo.

---

## 2. Configuración en los Bots (`settings.yaml`)

Cualquier perfil de bot puede activar el motor en su archivo `settings.yaml`:

```yaml
jev:
  enabled: true
  provider: laya      # Opciones: laya | kev | nanojev
  shadow_mode: true   # true: registra opiniones sin bloquear; false: filtro activo
  model: laya-base
  device: cuda        # cuda (GPU RTX 3050) o cpu
  timeout_seconds: 2.0
```

---

## 3. Formato de Salida Tipada

Cada evaluación genera un objeto tipado `SystemOneDecision`:

```json
{
  "signal_id": "98ac7848a4ad3dcc",
  "provider": "Laya",
  "latency_ms": 18.4,
  "thesis_alignment": "aligned",
  "thesis_confidence": 0.82,
  "evidence_score": 4,
  "evidence_confidence": 0.78,
  "trap_risk": "genuine_expansion",
  "execution_effect": "none_shadow_only"
}
```

---

## 4. Estructura del Código

* [`filters/system_one/base.py`](file:///c:/Users/Asus/Documents/quantum/filters/system_one/base.py): Contratos tipados, esquemas de preguntas y objeto de decisión.
* [`filters/system_one/laya_provider.py`](file:///c:/Users/Asus/Documents/quantum/filters/system_one/laya_provider.py): Conector local ONNX/ModernBERT de Laya.
* [`filters/system_one/kev_provider.py`](file:///c:/Users/Asus/Documents/quantum/filters/system_one/kev_provider.py): Conector de razonamiento macro Kev.
* [`filters/system_one/nanojev_provider.py`](file:///c:/Users/Asus/Documents/quantum/filters/system_one/nanojev_provider.py): Motor de inferencia paralela por lotes.
* [`filters/system_one/hub.py`](file:///c:/Users/Asus/Documents/quantum/filters/system_one/hub.py): Orquestador desacoplado con colas asíncronas para no interferir con MT5.
