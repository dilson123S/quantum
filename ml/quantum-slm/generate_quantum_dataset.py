"""Quantum SLM Dataset Generator — Extracts real trade history & institutional SMC/Spike rules.

Generates high-precision instruction tuning dataset in Alpaca format for Soup CLI fine-tuning
on Qwen2.5-3B-Instruct with 4GB VRAM Layer Streaming.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

SYSTEM_INSTRUCTION = (
    "You are Kev, the Quantum AI System 1 Decision Model. Analyze this trading candidate state "
    "and respond STRICTLY in JSON format with keys: thesis_alignment (aligned/mixed/contradictory/insufficient), "
    "thesis_confidence (0.0-1.0), evidence_score (1-5), evidence_confidence (0.0-1.0), "
    "trap_risk (genuine_expansion/potential_trap/overextended_exhaustion), trap_confidence (0.0-1.0), "
    "and reason (concise institutional explanation)."
)


def load_deriv_trades(db_path: str = "data/journal_deriv.db") -> List[Dict[str, Any]]:
    samples = []
    if not os.path.exists(db_path):
        return samples
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT ticket, symbol, direction, entry_price, sl_price, tp_price, profit, status FROM trades")
        for row in c.fetchall():
            ticket, symbol, direction, entry, sl, tp, profit, status = row
            is_win = (profit is not None and profit > 0)
            is_trap = (ticket == 9854947448 or (profit is not None and profit < -10.0))

            if is_trap:
                thesis = "contradictory"
                trap_risk = "potential_trap"
                evidence = 1
                conf = 0.92
                reason = f"{symbol} setup occurred during structural exhaustion. Wick rejection without macro M15 alignment."
            elif is_win:
                thesis = "aligned"
                trap_risk = "genuine_expansion"
                evidence = 4 if (profit or 0) < 50 else 5
                conf = 0.88 + random.uniform(0.01, 0.08)
                reason = f"{symbol} confirmed with high momentum expansion, respecting structural demand/supply."
            else:
                thesis = "mixed"
                trap_risk = "overextended_exhaustion"
                evidence = 2
                conf = 0.65
                reason = f"{symbol} mixed volume and consolidation signals. Marginal risk profile."

            state = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": round(float(entry or 0.0), 3),
                "sl_price": round(float(sl or 0.0), 3),
                "tp_price": round(float(tp or 0.0), 3),
                "timeframe": "M5",
                "macro_timeframe": "M15/H1",
                "volatility_regime": "EXPANDING" if is_win else "CONSOLIDATING",
            }
            decision = {
                "thesis_alignment": thesis,
                "thesis_confidence": round(conf, 2),
                "evidence_score": evidence,
                "evidence_confidence": round(conf, 2),
                "trap_risk": trap_risk,
                "trap_confidence": round(conf, 2),
                "reason": reason,
            }
            samples.append({"instruction": SYSTEM_INSTRUCTION, "input": json.dumps(state, indent=2), "output": json.dumps(decision, indent=2)})
        conn.close()
    except Exception as exc:
        print(f"[WARN] Error reading deriv db: {exc}")
    return samples


def load_gold_trades() -> List[Dict[str, Any]]:
    import glob
    samples = []
    gold_dbs = glob.glob("data/journal_bot_*.db")
    for db_path in gold_dbs:
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT ticket, symbol, direction, entry_price, sl_price, tp_price, profit, profit_r, features_at_entry FROM trades WHERE state='CLOSED'")
            for row in c.fetchall():
                ticket, symbol, direction, entry, sl, tp, profit, profit_r, feats = row
                is_win = (profit is not None and profit > 0)
                parsed_feats = {}
                if feats:
                    try:
                        parsed_feats = json.loads(feats)
                    except Exception:
                        pass

                state = {
                    "symbol": symbol or "XAUUSD",
                    "direction": direction,
                    "entry_price": round(float(entry or 0.0), 2),
                    "sl_price": round(float(sl or 0.0), 2),
                    "tp_price": round(float(tp or 0.0), 2),
                    "timeframe": "H1",
                    "macro_timeframe": "H4/D1",
                    "rsi": round(float(parsed_feats.get("rsi", 52.0)), 2),
                    "adx": round(float(parsed_feats.get("adx", 24.0)), 2),
                    "session": "LONDON_NY_OVERLAP",
                    "smc_sweep_confirmed": True if is_win else False,
                }

                if is_win:
                    decision = {
                        "thesis_alignment": "aligned",
                        "thesis_confidence": 0.94,
                        "evidence_score": 5,
                        "evidence_confidence": 0.92,
                        "trap_risk": "genuine_expansion",
                        "trap_confidence": 0.91,
                        "reason": f"XAUUSD clean liquidity sweep with ADX > 20 trending momentum. H4 alignment confirmed.",
                    }
                else:
                    decision = {
                        "thesis_alignment": "contradictory",
                        "thesis_confidence": 0.75,
                        "evidence_score": 2,
                        "evidence_confidence": 0.70,
                        "trap_risk": "potential_trap",
                        "trap_confidence": 0.80,
                        "reason": f"XAUUSD failed to break key order block. Low volume rejection.",
                    }

                samples.append({"instruction": SYSTEM_INSTRUCTION, "input": json.dumps(state, indent=2), "output": json.dumps(decision, indent=2)})
            conn.close()
        except Exception as exc:
            pass
    return samples


def generate_synthetic_curated_scenarios() -> List[Dict[str, Any]]:
    """Generate high-utility institutional trading scenarios for Gold & Synthetics."""
    scenarios = []

    # 1. Gold London/NY Liquidity Sweeps
    for i in range(120):
        direction = random.choice(["BUY", "SELL"])
        is_clean_sweep = random.random() > 0.35
        adx = random.uniform(22.0, 45.0) if is_clean_sweep else random.uniform(11.0, 19.0)
        session = random.choice(["LONDON_KILLZONE", "NY_KILLZONE", "ASIAN_RANGE", "ASIA_LATE"])
        price = round(random.uniform(4100.0, 4400.0), 2)
        sl_dist = round(random.uniform(10.0, 25.0), 2)
        sl = price - sl_dist if direction == "BUY" else price + sl_dist
        tp = price + (sl_dist * 2.2) if direction == "BUY" else price - (sl_dist * 2.2)

        # Trap rules
        is_asia_trap = ("ASIAN" in session and not is_clean_sweep)
        is_ranging_trap = (adx < 20.0)

        if is_asia_trap or is_ranging_trap:
            thesis = "contradictory"
            evidence = 1
            trap = "potential_trap"
            conf = round(random.uniform(0.85, 0.96), 2)
            reason = f"XAUUSD breakout blocked: Asian session low liquidity and ADX {adx:.1f} indicates choppy range bound trap."
        elif is_clean_sweep:
            thesis = "aligned"
            evidence = random.choice([4, 5])
            trap = "genuine_expansion"
            conf = round(random.uniform(0.88, 0.95), 2)
            reason = f"XAUUSD {session} liquidity sweep confirmed with Order Block mitigation and ADX {adx:.1f} trend momentum."
        else:
            thesis = "mixed"
            evidence = 2
            trap = "overextended_exhaustion"
            conf = round(random.uniform(0.60, 0.75), 2)
            reason = f"XAUUSD structure is overextended near key HTF liquidity pool without clear FVG gap."

        state = {
            "symbol": "XAUUSD",
            "direction": direction,
            "entry_price": price,
            "sl_price": sl,
            "tp_price": tp,
            "timeframe": "H1",
            "macro_timeframe": "H4/D1",
            "adx": round(adx, 1),
            "session": session,
            "smc_pattern": "LIQUIDITY_SWEEP_OB" if is_clean_sweep else "RANGE_BREAKOUT",
        }
        decision = {
            "thesis_alignment": thesis,
            "thesis_confidence": conf,
            "evidence_score": evidence,
            "evidence_confidence": conf,
            "trap_risk": trap,
            "trap_confidence": conf,
            "reason": reason,
        }
        scenarios.append({"instruction": SYSTEM_INSTRUCTION, "input": json.dumps(state, indent=2), "output": json.dumps(decision, indent=2)})

    # 2. Boom & Crash Spike Sniper Scenarios
    for sym, allowed_dir, contr_dir in [("Boom 500 Index", "BUY", "SELL"), ("Crash 500 Index", "SELL", "BUY"), ("Boom 1000 Index", "BUY", "SELL"), ("Crash 1000 Index", "SELL", "BUY")]:
        for i in range(75):
            # Test 1: Counter-spike trade (e.g. SELL on Boom) -> Immediate Veto
            if random.random() < 0.25:
                state = {
                    "symbol": sym,
                    "direction": contr_dir,
                    "timeframe": "M5",
                    "macro_timeframe": "M15/H1",
                    "rsi": round(random.uniform(30.0, 70.0), 1),
                    "setup": "COUNTER_TREND_FADE",
                }
                decision = {
                    "thesis_alignment": "contradictory",
                    "thesis_confidence": 0.99,
                    "evidence_score": 1,
                    "evidence_confidence": 0.98,
                    "trap_risk": "potential_trap",
                    "trap_confidence": 0.99,
                    "reason": f"Strict risk rule: Trading against the spike physics on {sym} ({contr_dir}) is strictly vetoed.",
                }
                scenarios.append({"instruction": SYSTEM_INSTRUCTION, "input": json.dumps(state, indent=2), "output": json.dumps(decision, indent=2)})
                continue

            # Test 2: Valid Spike Accumulation vs Exhaustion Trap
            is_valid_accumulation = random.random() > 0.40
            rsi = random.uniform(35.0, 48.0) if allowed_dir == "BUY" else random.uniform(52.0, 68.0)

            if is_valid_accumulation:
                thesis = "aligned"
                evidence = random.choice([4, 5])
                trap = "genuine_expansion"
                conf = round(random.uniform(0.86, 0.95), 2)
                reason = f"{sym} accumulation zone on M5 with rejection wicks near historical spike level. Favorable R:R."
            else:
                thesis = "contradictory"
                evidence = 1
                trap = "overextended_exhaustion"
                conf = round(random.uniform(0.80, 0.92), 2)
                reason = f"{sym} overdue spike zone with macro M15 downtrend breakdown. High risk of tick decay SL hit."

            state = {
                "symbol": sym,
                "direction": allowed_dir,
                "timeframe": "M5",
                "macro_timeframe": "M15/H1",
                "rsi": round(rsi, 1),
                "wick_absorption_ratio": round(random.uniform(0.20, 0.45) if is_valid_accumulation else 0.05, 2),
                "touch_support": is_valid_accumulation,
            }
            decision = {
                "thesis_alignment": thesis,
                "thesis_confidence": conf,
                "evidence_score": evidence,
                "evidence_confidence": conf,
                "trap_risk": trap,
                "trap_confidence": conf,
                "reason": reason,
            }
            scenarios.append({"instruction": SYSTEM_INSTRUCTION, "input": json.dumps(state, indent=2), "output": json.dumps(decision, indent=2)})

    # 3. Volatility 100 Index Expansion
    for i in range(80):
        direction = random.choice(["BUY", "SELL"])
        ema_aligned = random.random() > 0.35
        squeeze_released = random.random() > 0.40

        if ema_aligned and squeeze_released:
            thesis = "aligned"
            evidence = 5
            trap = "genuine_expansion"
            conf = 0.93
            reason = "Volatility 100 Index Bollinger squeeze expansion with triple EMA trend alignment. Strong institutional flow."
        elif not ema_aligned:
            thesis = "contradictory"
            evidence = 1
            trap = "potential_trap"
            conf = 0.89
            reason = "Volatility 100 Index trend misalignment. Price oscillating across EMA 50 without directional commitment."
        else:
            thesis = "mixed"
            evidence = 3
            trap = "overextended_exhaustion"
            conf = 0.70
            reason = "Volatility 100 Index momentum stalling at ATR extreme band. Partial take recommended."

        state = {
            "symbol": "Volatility 100 Index",
            "direction": direction,
            "timeframe": "M5",
            "macro_timeframe": "H1",
            "ema_fast_above_slow": True if direction == "BUY" and ema_aligned else False,
            "bb_squeeze_expansion": squeeze_released,
        }
        decision = {
            "thesis_alignment": thesis,
            "thesis_confidence": conf,
            "evidence_score": evidence,
            "evidence_confidence": conf,
            "trap_risk": trap,
            "trap_confidence": conf,
            "reason": reason,
        }
        scenarios.append({"instruction": SYSTEM_INSTRUCTION, "input": json.dumps(state, indent=2), "output": json.dumps(decision, indent=2)})

    return scenarios


def main():
    out_dir = Path("ml/quantum-slm")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("==================================================================")
    print("   QUANTUM SLM — GENERANDO DATASET DE ENTRENAMIENTO PARA SOUP")
    print("==================================================================")

    deriv_samples = load_deriv_trades()
    gold_samples = load_gold_trades()
    curated_samples = generate_synthetic_curated_scenarios()

    all_samples = deriv_samples + gold_samples + curated_samples
    random.shuffle(all_samples)

    print(f"[+] Trades reales de Deriv:   {len(deriv_samples)}")
    print(f"[+] Trades reales de Oro:     {len(gold_samples)}")
    print(f"[+] Escenarios SMC/Spikes:    {len(curated_samples)}")
    print(f"[+] Total de Muestras:        {len(all_samples)}")

    split_idx = int(len(all_samples) * 0.90)
    train_samples = all_samples[:split_idx]
    eval_samples = all_samples[split_idx:]

    train_path = out_dir / "quantum_train_alpaca.jsonl"
    eval_path = out_dir / "quantum_eval_alpaca.jsonl"

    with open(train_path, "w", encoding="utf-8") as f:
        for item in train_samples:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    with open(eval_path, "w", encoding="utf-8") as f:
        for item in eval_samples:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"\n[OK] Dataset de Entrenamiento: {train_path} ({len(train_samples)} muestras)")
    print(f"[OK] Dataset de Evaluación:    {eval_path} ({len(eval_samples)} muestras)")
    print("==================================================================")


if __name__ == "__main__":
    main()
