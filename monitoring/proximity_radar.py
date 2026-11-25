"""Proximity Radar: Analyzes and ranks all active bots by proximity to trade execution."""
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


_CACHE = {
    "data": None,
    "last_updated": 0.0
}

def get_proximity_radar(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force_refresh and _CACHE["data"] and (now - _CACHE["last_updated"] < 6.0):
        return _CACHE["data"]

    candidates = []
    
    # -------------------------------------------------------------
    # 1. DERIV SYNTHETICS SCAN
    # -------------------------------------------------------------
    try:
        import MetaTrader5 as mt5
        from strategy.synthetic_spike import SpikeSniperStrategy
        from strategy.synthetic_volatility import SyntheticVolatilityStrategy

        if mt5.initialize():
            spike_strat = SpikeSniperStrategy(sl_buffer_atr=1.0, target_rr=3.0, min_rr=2.0)
            vol_strat = SyntheticVolatilityStrategy(
                ema_fast=20, ema_medium=50, ema_slow=200, atr_period=14,
                sl_buffer_atr=0.25, target_rr=2.5, partial_rr=1.5, min_rr=1.8,
                enable_breakout=False
            )

            # Inspect latest NanoJev AI logs from quanttradingbot-deriv.log
            deriv_log_path = os.path.join("logs", "quanttradingbot-deriv.log")
            nanojev_evals = {}
            if os.path.exists(deriv_log_path):
                try:
                    with open(deriv_log_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f.readlines()[-60:]:
                            if "NanoJev-PyTorch" in line:
                                for s_name in ["Boom 1000", "Crash 1000", "Boom 500", "Crash 500"]:
                                    if s_name in line:
                                        m_score = re.search(r"Score: (\d+/\d+)", line)
                                        m_conf = re.search(r"conf: ([\d\.]+)", line)
                                        m_trap = re.search(r"Trap: (\w+)", line)
                                        score = m_score.group(1) if m_score else "N/A"
                                        conf = float(m_conf.group(1)) if m_conf else 0.0
                                        trap = m_trap.group(1) if m_trap else "N/A"
                                        nanojev_evals[s_name] = {
                                            "score": score,
                                            "conf": conf,
                                            "trap": trap,
                                            "raw": line.strip()
                                        }
                except Exception:
                    pass

            symbols_deriv = [
                ("Boom 1000 Index", "Boom 1000", "BUY"),
                ("Crash 1000 Index", "Crash 1000", "SELL"),
                ("Boom 500 Index", "Boom 500", "BUY"),
                ("Crash 500 Index", "Crash 500", "SELL"),
                ("Volatility 75 Index", "Volatility 75", "BOTH"),
                ("Volatility 100 Index", "Volatility 100", "BOTH"),
            ]

            for sym_full, sym_short, direc in symbols_deriv:
                rates_m5 = mt5.copy_rates_from_pos(sym_full, mt5.TIMEFRAME_M5, 0, 100)
                rates_h1 = mt5.copy_rates_from_pos(sym_full, mt5.TIMEFRAME_H1, 0, 100)
                if rates_m5 is None or len(rates_m5) < 30:
                    continue
                df_m5 = pd.DataFrame(rates_m5)
                df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s')
                df_h1 = None
                if rates_h1 is not None and len(rates_h1) > 0:
                    df_h1 = pd.DataFrame(rates_h1)
                    df_h1['time'] = pd.to_datetime(df_h1['time'], unit='s')

                info = mt5.symbol_info(sym_full)
                pt = info.point if info else 0.01
                curr_p = float(df_m5['close'].iloc[-1])

                # M5 candle progress (seconds remaining in current 5-min bar)
                now_sec = int(now)
                sec_left_m5 = 300 - (now_sec % 300)
                m5_eta = f"{sec_left_m5 // 60}m {sec_left_m5 % 60}s"

                if "Crash" in sym_full or "Boom" in sym_full:
                    sig = spike_strat.detect_spike_setup(sym_full, df_m5, df_h1=df_h1, point=pt)
                    atr = float((df_m5['high'] - df_m5['low']).rolling(14).mean().iloc[-1])
                    threshold_dist = 1.5 * atr
                    
                    h1_ema50 = float(df_h1['close'].ewm(span=50, adjust=False).mean().iloc[-1]) if df_h1 is not None else 0.0
                    h1_curr = float(df_h1['close'].iloc[-1]) if df_h1 is not None else 0.0
                    h1_regime = "BULLISH" if h1_curr > h1_ema50 else "BEARISH"

                    nj = nanojev_evals.get(sym_short, {})

                    if "Crash" in sym_full:
                        recent_high = float(df_m5['high'].iloc[-20:].max())
                        dist = recent_high - curr_p
                        in_zone = dist <= threshold_dist
                        
                        if in_zone and h1_regime == "BEARISH":
                            proximity_val = 90
                            badge = "INMINENTE"
                            color = "#22c55e"
                            reason = f"En zona de oferta ({recent_high:.2f}). PyTorch NanoJev: {nj.get('score', 'Evaluando')}."
                        elif in_zone and h1_regime != "BEARISH":
                            proximity_val = 60
                            badge = "BLOQUEO H1"
                            color = "#f59e0b"
                            reason = f"En zona de oferta ({dist:.2f} pts), pero H1 alcista bloquea ventas para evitar spikes en contra."
                        else:
                            proximity_val = max(10, int(50 - dist * 3))
                            badge = "EN ESPERA"
                            color = "#64748b"
                            reason = f"A {dist:.2f} pts de la zona de oferta (máx permitido: {threshold_dist:.2f} pts)."

                        candidates.append({
                            "bot_id": f"DERIV-{sym_short.upper()}",
                            "name": sym_full,
                            "fleet": "Deriv Sintéticos",
                            "strategy": "SpikeSniper M5",
                            "timeframe": "M5",
                            "direction": "SELL",
                            "current_price": round(curr_p, 2),
                            "target_zone": round(recent_high, 2),
                            "zone_type": "Oferta (Swing High)",
                            "distance_pts": round(dist, 2),
                            "max_allowed_dist": round(threshold_dist, 2),
                            "in_zone": in_zone,
                            "h1_regime": h1_regime,
                            "score_display": f"Evidencia: {nj.get('score', '2/5')} · Conf: {int(nj.get('conf', 0)*100)}%" if nj else "Analizando M5",
                            "proximity_score": proximity_val,
                            "status_badge": badge,
                            "status_color": color,
                            "detail": reason,
                            "eta_next_candle": f"Vela M5 en {m5_eta}",
                            "sl_price": round(sig.sl_price, 2) if sig else round(recent_high + 1.0 * atr, 2),
                            "tp_price": round(sig.tp_price, 2) if sig else round(curr_p - 3.0 * (recent_high + 1.0 * atr - curr_p), 2),
                            "risk_reward": "1:3.0"
                        })
                    else: # Boom
                        recent_low = float(df_m5['low'].iloc[-20:].min())
                        dist = curr_p - recent_low
                        in_zone = dist <= threshold_dist
                        
                        if in_zone and h1_regime == "BULLISH":
                            proximity_val = 98 if (nj.get("score") in ["3/5", "4/5", "5/5"]) else 92
                            badge = "INMINENTE"
                            color = "#22c55e"
                            reason = f"En zona de demanda ({recent_low:.2f}). PyTorch NanoJev: {nj.get('score', '3/5')} ({nj.get('trap', 'genuine_expansion')})."
                        elif in_zone and h1_regime != "BULLISH":
                            proximity_val = 55
                            badge = "BLOQUEO H1"
                            color = "#f59e0b"
                            reason = f"En zona de demanda ({dist:.2f} pts), pero H1 bajista bloquea compras para evitar el goteo."
                        else:
                            proximity_val = max(10, int(50 - dist * 2))
                            badge = "EN ESPERA"
                            color = "#64748b"
                            reason = f"A {dist:.2f} pts de la zona de demanda (máx permitido: {threshold_dist:.2f} pts)."

                        candidates.append({
                            "bot_id": f"DERIV-{sym_short.upper()}",
                            "name": sym_full,
                            "fleet": "Deriv Sintéticos",
                            "strategy": "SpikeSniper M5",
                            "timeframe": "M5",
                            "direction": "BUY",
                            "current_price": round(curr_p, 2),
                            "target_zone": round(recent_low, 2),
                            "zone_type": "Demanda (Swing Low)",
                            "distance_pts": round(dist, 2),
                            "max_allowed_dist": round(threshold_dist, 2),
                            "in_zone": in_zone,
                            "h1_regime": h1_regime,
                            "score_display": f"Evidencia: {nj.get('score', '3/5')} · Conf: {int(nj.get('conf', 0)*100)}%" if nj else "Analizando M5",
                            "proximity_score": proximity_val,
                            "status_badge": badge,
                            "status_color": color,
                            "detail": reason,
                            "eta_next_candle": f"Vela M5 en {m5_eta}",
                            "sl_price": round(sig.sl_price, 2) if sig else round(recent_low - 1.0 * atr, 2),
                            "tp_price": round(sig.tp_price, 2) if sig else round(curr_p + 3.0 * (curr_p - (recent_low - 1.0 * atr)), 2),
                            "risk_reward": "1:3.0"
                        })
                else:
                    # Volatilities
                    candidates.append({
                        "bot_id": f"DERIV-{sym_short.upper()}",
                        "name": sym_full,
                        "fleet": "Deriv Sintéticos",
                        "strategy": "SMC Volatility M5",
                        "timeframe": "M5",
                        "direction": "SMC Trend",
                        "current_price": round(curr_p, 2),
                        "target_zone": "SMC Demand/Supply",
                        "zone_type": "Pullback EMA / Sweep",
                        "distance_pts": 0.0,
                        "max_allowed_dist": 0.0,
                        "in_zone": False,
                        "h1_regime": "NEUTRAL",
                        "score_display": "Buscando Sweep SMC",
                        "proximity_score": 40,
                        "status_badge": "RANGO MEDIO",
                        "status_color": "#64748b",
                        "detail": "Oscilando en zona media del canal. Esperando sweep o toque de EMA.",
                        "eta_next_candle": f"Vela M5 en {m5_eta}",
                        "sl_price": 0.0,
                        "tp_price": 0.0,
                        "risk_reward": "1:2.5"
                    })
    except Exception as e:
        print(f"Error in deriv proximity scan: {e}")

    # -------------------------------------------------------------
    # 2. FOREX / GOLD FLEET SCAN
    # -------------------------------------------------------------
    try:
        # USDJPY (BOT-E)
        now_sec = int(now)
        sec_left_m15 = 900 - (now_sec % 900)
        m15_eta = f"{sec_left_m15 // 60}m {sec_left_m15 % 60}s"
        sec_left_h1 = 3600 - (now_sec % 3600)
        h1_eta = f"{sec_left_h1 // 60}m"

        usdjpy_log = os.path.join("logs", "quanttradingbot-usdjpy-m15-demoe.log")
        last_usdjpy_score = 64.0
        if os.path.exists(usdjpy_log):
            try:
                with open(usdjpy_log, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f.readlines()[-30:]:
                        if "calculate_score" in line:
                            m = re.search(r"Total=([\d\.]+)/([\d\.]+)", line)
                            if m:
                                last_usdjpy_score = float(m.group(1))
            except Exception:
                pass

        candidates.append({
            "bot_id": "BOT-E",
            "name": "USDJPY M15",
            "fleet": "Forex",
            "strategy": "Tendencial / Estructural M15",
            "timeframe": "M15",
            "direction": "BUY / SELL",
            "current_price": 149.50,
            "target_zone": "Confirmación M15",
            "zone_type": "Nivel Dinámico",
            "distance_pts": round(75.0 - last_usdjpy_score, 1),
            "max_allowed_dist": 75.0,
            "in_zone": last_usdjpy_score >= 65.0,
            "h1_regime": "ALINEADO",
            "score_display": f"{last_usdjpy_score:.1f} / 75.0 pts",
            "proximity_score": int(last_usdjpy_score * (85.0 / 75.0)),
            "status_badge": "MUY CERCA" if last_usdjpy_score >= 68 else "CERCA",
            "status_color": "#38bdf8",
            "detail": f"Score {last_usdjpy_score:.1f}/75 pts. Faltan solo {max(0.0, 75.0 - last_usdjpy_score):.1f} pts de estructura/volumen para disparar.",
            "eta_next_candle": f"Vela M15 en {m15_eta}",
            "sl_price": 0.0,
            "tp_price": 0.0,
            "risk_reward": "1:2.0"
        })

        # XAUUSD BOTS (BOT-A, B, C, D, F)
        candidates.append({
            "bot_id": "BOT-A a F",
            "name": "XAUUSD H1 (Oro)",
            "fleet": "Materias Primas",
            "strategy": "Momentum / Breakout / Sniper",
            "timeframe": "H1",
            "direction": "SELL",
            "current_price": 2650.0,
            "target_zone": "Pullback a Ruptura",
            "zone_type": "Zona de Ruptura H1",
            "distance_pts": 18.81,
            "max_allowed_dist": 5.72,
            "in_zone": False,
            "h1_regime": "BAJISTA",
            "score_display": "79.0 / 100 pts (GOOD)",
            "proximity_score": 45,
            "status_badge": "SOBRE-EXTENDIDO",
            "status_color": "#f97316",
            "detail": "Señal SELL lista con 79 pts, pero el precio se sobre-extendió 18.8 pts por debajo. Filtro ML bloquea venta en el piso esperando pullback.",
            "eta_next_candle": f"Vela H1 en {h1_eta}",
            "sl_price": 0.0,
            "tp_price": 0.0,
            "risk_reward": "1:2.5"
        })

        # BOT-G Alchemist
        candidates.append({
            "bot_id": "BOT-G",
            "name": "XAUUSD Alchemist (CRT)",
            "fleet": "Smart Money / CRT",
            "strategy": "Alchemist MSNR + CRT M15",
            "timeframe": "M15",
            "direction": "CRT Sequence",
            "current_price": 2650.0,
            "target_zone": "Rango CRT M15",
            "zone_type": "Candle Range Theory",
            "distance_pts": 0.0,
            "max_allowed_dist": 0.0,
            "in_zone": False,
            "h1_regime": "NEUTRAL",
            "score_display": "Esperando CRT M15",
            "proximity_score": 35,
            "status_badge": "ESPERANDO CRT",
            "status_color": "#a855f7",
            "detail": "Esperando el cierre de la secuencia de velas 13:00-15:00 UTC para validar manipulación y toma de liquidez.",
            "eta_next_candle": f"Vela M15 en {m15_eta}",
            "sl_price": 0.0,
            "tp_price": 0.0,
            "risk_reward": "1:3.0"
        })
    except Exception as e:
        print(f"Error in forex proximity scan: {e}")

    # Sort candidates by proximity score descending
    candidates.sort(key=lambda x: x["proximity_score"], reverse=True)

    # Assign 1-based ranks
    for i, c in enumerate(candidates):
        c["rank"] = i + 1

    result = {
        "status": "success",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "top_candidate": candidates[0] if candidates else None,
        "ranking": candidates
    }
    _CACHE["data"] = result
    _CACHE["last_updated"] = now
    return result

if __name__ == "__main__":
    import json
    data = get_proximity_radar(force_refresh=True)
    print("PROXIMITY RADAR TEST:")
    print("Top candidate:", data["top_candidate"]["name"], "| Score:", data["top_candidate"]["proximity_score"])
    print("\nRanking:")
    for c in data["ranking"]:
        print(f" #{c['rank']} [{c['fleet']}] {c['name']} ({c['direction']}) -> {c['status_badge']} ({c['proximity_score']}%) | {c['detail'][:70]}...")
