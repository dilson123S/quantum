"""Deterministic, safety-first answers for the dashboard voice interface.

The browser handles microphone capture and speech synthesis.  This module only
receives text and works from a fresh, local bot snapshot; it never places an
order or changes bot configuration.
"""
from collections import Counter
import re
import unicodedata
from typing import Any, Dict, List


def _normalise(value: str) -> str:
    text = unicodedata.normalize("NFD", value.lower())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _reason_counts(signals: List[Dict[str, Any]]) -> Counter:
    counts: Counter = Counter()
    for signal in signals:
        reason = str(signal.get("rejection_reasons") or "")
        if "Outside trading session" in reason:
            counts["fuera de sesión"] += 1
        if "Spread/ATR" in reason:
            counts["spread/ATR alto"] += 1
        if "exceeds max allowed" in reason:
            counts["spread absoluto alto"] += 1
        if "setup_trend=" in reason:
            counts["confirmación MTF insuficiente"] += 1
        if "SHADOW_REJECTED_SCORE" in reason:
            counts["score o confirmaciones insuficientes"] += 1
        if "PAPER_CANDIDATE" in reason:
            counts["modo paper"] += 1
    return counts


class VoiceAssistant:
    """Answer operational questions only from supplied, current facts."""

    def answer(
        self,
        question: str,
        account: Dict[str, Any],
        intraday: Dict[str, Any],
        symbols: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        query = _normalise(question)
        mode = str(account.get("mode", "PAPER")).upper()
        rejected = [row for row in signals if row.get("execution_status") == "REJECTED"]
        reason_counts = _reason_counts(rejected)
        best = max(symbols, key=lambda item: float(item.get("total_score", 0.0)), default=None)

        # Voice must never become an alternate order-entry channel. This is
        # deliberately checked before natural-language fallbacks.
        trade_words = ("compra", "comprar", "vende", "vender", "abre", "abrir", "cierra", "cerrar", "orden", "operacion", "operación")
        if any(word in query for word in trade_words) and any(
            word in query for word in ("ahora", "ejecut", "haz", "manda", "coloca", "pon")
        ):
            return {
                "intent": "blocked_trade_command",
                "answer": (
                    "No ejecutaré órdenes por voz. El control de voz es informativo y no puede "
                    "anular el modo PAPER, la validación, el riesgo ni los filtros. Puedo explicar "
                    "qué condición falta para una entrada segura."
                ),
                "sources": ["safety_policy"],
            }

        if any(term in query for term in ("estado", "estan corriendo", "estan funcionando", "bots")):
            return {
                "intent": "bot_status",
                "answer": (
                    f"El bot conservador y el intradía están en modo {mode}. "
                    f"Hay {account.get('open_positions_count', 0)} posiciones abiertas del bot. "
                    f"El perfil intradía tiene validación {intraday.get('validation', {}).get('status', 'DESCONOCIDA')}."
                ),
                "sources": ["account", "intraday_profile"],
            }

        if any(term in query for term in ("mercado", "como esta", "cómo está", "radar", "mejor oportunidad")):
            if best is None:
                answer = "No hay datos de mercado disponibles ahora mismo."
            else:
                direction = best.get("direction", "NEUTRAL")
                score = float(best.get("total_score", 0.0))
                tradeable = "sí cumple el score" if best.get("is_tradeable") else "todavía no cumple todos los mínimos"
                answer = (
                    f"El radar actual muestra como mayor score a {best.get('symbol')} en dirección {direction}, "
                    f"con {score:.0f} sobre 100. Según el radar, {tradeable}. "
                    "Esto no equivale a una orden: sesión, spread, riesgo y confirmaciones siguen siendo obligatorios."
                )
            return {"intent": "market_snapshot", "answer": answer, "sources": ["market_snapshot"]}

        if any(term in query for term in ("por que no", "porque no", "por qué no", "no hace trade", "no opera", "no ha operado", "no entr")):
            reasons = ", ".join(f"{label}: {count}" for label, count in reason_counts.most_common(3)) or "no hay rechazos registrados"
            answer = (
                f"No hay operaciones enviadas a MT5 porque el sistema está en modo {mode}. "
                f"En la auditoría reciente, los motivos dominantes son: {reasons}."
            )
            return {"intent": "why_no_trade", "answer": answer, "sources": ["account", "signal_audit"]}

        if any(term in query for term in ("rechaz", "senal", "señal", "auditoria", "auditoría")):
            latest = rejected[0] if rejected else None
            if latest:
                answer = (
                    f"La última señal rechazada fue {latest.get('symbol')} {latest.get('direction')} con "
                    f"score {float(latest.get('total_score', 0)):.0f}. Motivo: "
                    f"{latest.get('rejection_reasons') or 'no registrado'}."
                )
            else:
                answer = "No hay señales rechazadas en la auditoría reciente."
            return {"intent": "signal_audit", "answer": answer, "sources": ["signal_audit"]}

        if any(term in query for term in ("que falta", "qué falta", "para entrar", "para operar", "condicion", "condición")):
            latest = rejected[0] if rejected else None
            details = latest.get("rejection_reasons") if latest else "no hay una señal reciente que evaluar"
            answer = (
                "Para considerar una entrada deben coincidir: vela cerrada, score y estructura mínimos, "
                "confirmación MTF, sesión activa, spread aceptable, riesgo de cartera disponible y breakout válido. "
                f"En el último rechazo, falta: {details}."
            )
            return {"intent": "entry_conditions", "answer": answer, "sources": ["signal_audit", "risk_policy"]}

        return {
            "intent": "unsupported",
            "answer": (
                "Puedo responder sobre estado del mercado, por qué no opera, auditoría de señales y qué falta "
                "para entrar. También puedo leer el estado de los bots. No acepto órdenes ni cambios de riesgo por voz."
            ),
            "sources": ["voice_help"],
        }
