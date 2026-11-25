import sys
import os
import json
import concurrent.futures
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

# Fix Windows console encoding
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from core.config_loader import load_config
from core.constants import EntryMode, Direction, TIMEFRAME_MAP, HOLDOUT_START
from backtest.engine import BacktestEngine, InstrumentSpec, PendingBreakout
from data.cleaner import DataCleaner
from filters.ml_filter import MLFilter
from backtest.robustness import bootstrap_trade_outcomes


class ExperimentBacktestEngine(BacktestEngine):
    def __init__(self, config, initial_balance=10000.0, instrument_specs=None, ml_filter=None, apply_memory_mods=False):
        super().__init__(config, initial_balance, instrument_specs)
        self.ml_filter = ml_filter
        self.apply_memory_mods = apply_memory_mods

    def _plan_pending_breakout(
        self,
        symbol,
        window,
        htf_slice,
        setup_slice,
        structure,
        bar_time,
        balance,
        daily_pnl,
        consecutive_losses,
        spread_points,
        spec,
        result=None,
    ):
        last = window.iloc[-1]
        atr = float(last.get("atr", 0.0) or 0.0)
        if atr <= 0:
            return None

        score, signal = self.score_engine.calculate_score(
            symbol=symbol,
            timeframe=self.config.timeframes.entry,
            df_entry=window,
            df_setup=setup_slice,
            df_htf=htf_slice,
            structure_analysis=structure,
            config=self.config,
        )
        if score is None or signal is None:
            return None

        # Memory Bank Spike Exhaustion Veto
        if self.apply_memory_mods:
            body = abs(float(last["close"]) - float(last["open"]))
            if body > 3.0 * atr:
                return None

        from strategy.decision_context import build_decision_context
        decision_context = build_decision_context(
            window, structure, signal.direction,
            min_atr_ratio=self.config.volatility.min_atr_ratio,
            max_atr_ratio=self.config.volatility.max_atr_ratio,
            adx_min=self.config.trend.adx_min,
            equal_level_tolerance_atr=self.config.decision_context.equal_level_tolerance_atr,
            fib_zone_atr=self.config.decision_context.fib_zone_atr,
            smc_max_age_bars=self.config.decision_context.smc_max_age_bars,
            smc_min_gap_atr=self.config.decision_context.smc_min_gap_atr,
            sweep_min_penetration_atr=self.config.decision_context.sweep_min_penetration_atr,
            min_bb_width_ratio=self.config.regime_filter.min_bb_width_ratio,
            max_range_bb_width_ratio=self.config.regime_filter.max_range_bb_width_ratio,
        )
        assessment = self.market_quality_filter.assess(decision_context, signal.direction)
        if not assessment.approved:
            return None

        context = self.context_filter.check_all(
            symbol=symbol,
            direction=signal.direction,
            current_time_utc=bar_time,
            current_spread_points=spread_points,
            atr_value=atr,
            point_size=spec.point,
            current_equity=balance,
            current_balance=balance,
            daily_pnl=daily_pnl,
            consecutive_losses=consecutive_losses,
            open_positions=[],
            price_history_dict={symbol: window},
            symbol_config=self.config.symbols.get(symbol),
        )
        if not context.approved:
            if result is not None:
                for reason in context.reasons:
                    if "Consecutive losses" in reason:
                        result.signals_skipped_circuit_breaker += 1
                    elif "Daily loss" in reason:
                        result.signals_skipped_daily_loss += 1
                    elif "drawdown" in reason.lower():
                        result.signals_skipped_drawdown += 1
            return None

        # ML Filter Evaluation
        if self.ml_filter and self.ml_filter.is_active and not self.ml_filter.shadow_mode:
            approved, prob, reason = self.ml_filter.evaluate(
                symbol=symbol,
                direction=signal.direction.value,
                df=window,
                structure_score=score.structure.score,
                trend_score=score.trend.score,
                spread_points=spread_points,
                atr_value=atr,
                point_size=spec.point,
                total_score=score.total_score,
            )
            if not approved:
                return None

        direction = signal.direction
        entry_mode = getattr(self.config.trade, "entry_mode", EntryMode.BREAKOUT)
        close = float(last["close"])
        spread_price = spread_points * spec.point

        swing = structure.last_swing_high if direction == Direction.BUY else structure.last_swing_low
        if swing is None:
            return None

        buffer_pts = float(self.config.trade.breakout_buffer_points)
        if self.apply_memory_mods:
            buffer_pts += 5.0

        buffer = max(buffer_pts, 1.0) * spec.point
        trigger = float(swing.price) + buffer if direction == Direction.BUY else float(swing.price) - buffer
        if direction == Direction.BUY and trigger <= close + spread_price:
            return None
        if direction == Direction.SELL and trigger >= close:
            return None
        expiry_bars = int(self.config.trade.breakout_expiry_bars)

        sl = self.sl_calc.calculate_sl(
            direction=direction,
            entry_price=trigger,
            structure_analysis=structure,
            atr_value=atr,
            spread_price=spread_price,
            config=self.config.volatility,
            symbol_point=spec.point,
        )
        tp = self.tp_calc.calculate_tp(
            direction=direction,
            entry_price=trigger,
            sl_price=sl,
            structure_analysis=structure,
            atr_value=atr,
            config=self.config.trade,
        )
        if (direction == Direction.BUY and not (sl < trigger < tp)) or (
            direction == Direction.SELL and not (tp < trigger < sl)
        ):
            return None

        risk_money = self.pos_sizer.calculate_risk_money(
            balance, self.config.risk, score.classification, consecutive_losses
        )
        volume = self.pos_sizer.calculate_lot(
            symbol=symbol,
            risk_money=risk_money,
            sl_distance_points=abs(trigger - sl) / spec.point,
            tick_value=spec.tick_value,
            tick_size=spec.tick_size,
            point=spec.point,
            volume_min=spec.volume_min,
            volume_max=spec.volume_max,
            volume_step=spec.volume_step,
            min_trade_volume=float(getattr(self.config.risk, "min_trade_volume", 0.01)),
        )
        if volume <= 0:
            return None

        regime_label = str(decision_context.get("regime", {}).get("label", "UNKNOWN"))
        smc_alignment = decision_context.get("smc", {})
        smc_sweep_aligned = bool(smc_alignment.get("sweep_aligned", False))
        smc_fvg_aligned = bool(smc_alignment.get("fvg_aligned", False))
        smc_order_block_aligned = bool(smc_alignment.get("order_block_aligned", False))
        shadow_reasons = list(assessment.shadow_reasons)

        expires_at = bar_time + self._timeframe_delta(self.config.timeframes.entry) * expiry_bars
        return PendingBreakout(
            symbol=symbol,
            direction=direction,
            trigger_price=round(trigger, self._digits(spec.point)),
            sl_price=round(sl, self._digits(spec.point)),
            tp_price=round(tp, self._digits(spec.point)),
            volume=volume,
            risk_money=risk_money,
            created_at=bar_time,
            expires_at=expires_at,
            invalidation_price=sl,
            total_score=score.total_score,
            classification=score.classification.value,
            structure_score=score.structure.score,
            trend_score=score.trend.score,
            momentum_score=score.momentum.score,
            volume_score=score.volume.score,
            volatility_score=score.volatility.score,
            regime_label=regime_label,
            smc_sweep_aligned=smc_sweep_aligned,
            smc_fvg_aligned=smc_fvg_aligned,
            smc_order_block_aligned=smc_order_block_aligned,
            shadow_reasons=shadow_reasons,
        )


def run_single_scenario(sc, df_h1, df_h4, df_d1, specs):
    cfg = load_config(sc["config_path"])
    cfg.score.minimum = sc["score"]

    # Trade management
    if "be_r" in sc:
        cfg.trade.break_even_r = sc["be_r"]
    if "partial_close" in sc:
        cfg.trade.partial_close = sc["partial_close"]
    if "partial_r" in sc:
        cfg.trade.partial_r = sc["partial_r"]
    if "trailing_r" in sc:
        cfg.trade.trailing_activation_r = sc["trailing_r"]
    if "stagnation_bars" in sc:
        cfg.trade.stagnation_exit_bars = sc["stagnation_bars"]

    ml_f = None
    if sc.get("ml_mode") == "active":
        ml_f = MLFilter(enabled=True, shadow_mode=False, model_path="data/ml_model.joblib", min_confidence=sc["ml_conf"])
    else:
        ml_f = MLFilter(enabled=True, shadow_mode=True, model_path="data/ml_model.joblib", min_confidence=sc.get("ml_conf", 0.55))

    engine = ExperimentBacktestEngine(
        config=cfg,
        initial_balance=10000.0,
        instrument_specs=specs,
        ml_filter=ml_f,
        apply_memory_mods=sc.get("memory", False)
    )

    res = engine.run("XAUUSD", df_h1, df_htf=df_d1, df_setup=df_h4)
    trades = res.trades
    n_trades = len(trades)
    if n_trades == 0:
        return {
            "Escenario": sc["name"],
            "Bloque": sc.get("block", ""),
            "Trades": 0,
            "W/L": "0/0",
            "WinRate": "0.0%",
            "NetProfit": "$0.00",
            "Total_R": "0.00R",
            "PF": "0.00",
            "MaxDD": "0.00%"
        }

    wins = [t for t in trades if t.profit_money > 0]
    losses = [t for t in trades if t.profit_money < 0]
    net_profit = sum(t.profit_money for t in trades)
    total_r = sum(t.profit_r for t in trades)
    wr = len(wins) / n_trades * 100.0
    gross_profit = sum(t.profit_money for t in wins)
    gross_loss = abs(sum(t.profit_money for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.9

    peak = 10000.0
    bal = 10000.0
    max_dd = 0.0
    for t in trades:
        bal += t.profit_money
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100.0
        if dd > max_dd:
            max_dd = dd

    return {
        "Escenario": sc["name"],
        "Bloque": sc.get("block", ""),
        "Trades": n_trades,
        "W/L": f"{len(wins)}/{len(losses)}",
        "WinRate": f"{wr:.1f}%",
        "NetProfit": f"${net_profit:,.2f}",
        "Total_R": f"{total_r:+.1f}R",
        "PF": f"{pf:.2f}",
        "MaxDD": f"{max_dd:.2f}%"
    }


def download_data(symbol, timeframe, months=24, end_time=None):
    tf_val = TIMEFRAME_MAP.get(timeframe)
    end_time = end_time or datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=months * 31)
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_range(symbol, tf_val, start_time, end_time)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Failed to download {symbol} {timeframe}")
    cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']
    df = pd.DataFrame(rates, columns=cols)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df[df['time'] < end_time].set_index('time', drop=False)
    return df


def main():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        sys.exit(1)

    print("=" * 115)
    print("  🔬 AUDITORÍA INTEGRAL: IMPACTO DE SCORE DE ENTRADA AL 70% (24 MESES · XAUUSD)")
    print("=" * 115)
    print("  Descargando 2 años de datos históricos de MT5...")

    cleaner = DataCleaner()
    end_time = HOLDOUT_START

    df_raw_h1 = download_data("XAUUSD", "H1", months=24, end_time=end_time)
    df_clean_h1, _ = cleaner.clean(df_raw_h1)

    df_raw_h4 = download_data("XAUUSD", "H4", months=24, end_time=end_time)
    df_clean_h4, _ = cleaner.clean(df_raw_h4)

    df_raw_d1 = download_data("XAUUSD", "D1", months=24, end_time=end_time)
    df_clean_d1, _ = cleaner.clean(df_raw_d1)

    info = mt5.symbol_info("XAUUSD")
    specs = {
        "XAUUSD": InstrumentSpec(
            point=float(info.point),
            tick_size=float(info.trade_tick_size),
            tick_value=float(info.trade_tick_value),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
        )
    }
    print(f"  ✓ Barras cargadas: H1={len(df_clean_h1)}, H4={len(df_clean_h4)}, D1={len(df_clean_d1)}")
    print("-" * 115)

    # We evaluate both:
    # 1. ESTILO DEJAR CORRER (Sin Stagnation, BE 1.2R, Parcial 1.5R - el preferido por el usuario)
    # 2. ESTILO COSECHA RÁPIDA (Con Stagnation 5 velas, BE 0.8R, Parcial 0.5R)
    scenarios = [
        # BLOQUE 1: ESTILO "DEJAR CORRER" (El preferido por el usuario sin cosechas prematuras)
        {"block": "DEJAR CORRER", "name": "BOT-B · Base (Score 75% | ML 55%)", "config_path": "config/bot_b_gold",
         "score": 75.0, "ml_mode": "active", "ml_conf": 0.55, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},
        {"block": "DEJAR CORRER", "name": "BOT-B · 👉 Score Entrada 70% (ML 55%)", "config_path": "config/bot_b_gold",
         "score": 70.0, "ml_mode": "active", "ml_conf": 0.55, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},
        {"block": "DEJAR CORRER", "name": "BOT-B · 👉 Filtro ML al 70% (Score 75%)", "config_path": "config/bot_b_gold",
         "score": 75.0, "ml_mode": "active", "ml_conf": 0.70, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},
        {"block": "DEJAR CORRER", "name": "BOT-B · 👉 AMBOS al 70% (Score 70% + ML 70%)", "config_path": "config/bot_b_gold",
         "score": 70.0, "ml_mode": "active", "ml_conf": 0.70, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},

        {"block": "DEJAR CORRER", "name": "BOT-A · Base (Score 75% | ML 45%)", "config_path": "config/bot_a_gold",
         "score": 75.0, "ml_mode": "active", "ml_conf": 0.45, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},
        {"block": "DEJAR CORRER", "name": "BOT-A · 👉 Score Entrada 70% (ML 45%)", "config_path": "config/bot_a_gold",
         "score": 70.0, "ml_mode": "active", "ml_conf": 0.45, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},

        {"block": "DEJAR CORRER", "name": "BOT-C · Base Sniper (Score 75% | ML 65%)", "config_path": "config/bot_c_gold",
         "score": 75.0, "ml_mode": "active", "ml_conf": 0.65, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},
        {"block": "DEJAR CORRER", "name": "BOT-C · 👉 Score Entrada 70% (ML 65%)", "config_path": "config/bot_c_gold",
         "score": 70.0, "ml_mode": "active", "ml_conf": 0.65, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},

        {"block": "DEJAR CORRER", "name": "CONTROL SIN ML · Score 75% Técnico Puro", "config_path": "config/bot_b_gold",
         "score": 75.0, "ml_mode": "shadow", "ml_conf": 0.55, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},
        {"block": "DEJAR CORRER", "name": "CONTROL SIN ML · 👉 Score 70% Técnico Puro", "config_path": "config/bot_b_gold",
         "score": 70.0, "ml_mode": "shadow", "ml_conf": 0.55, "be_r": 1.2, "partial_close": True, "partial_r": 1.5, "stagnation_bars": 0},

        # BLOQUE 2: ESTILO ACTUAL (Con Stagnation 5 velas y BE 0.8R)
        {"block": "COSECHA ACTUAL", "name": "BOT-B · Cosecha Actual Base (Score 75% | ML 55%)", "config_path": "config/bot_b_gold",
         "score": 75.0, "ml_mode": "active", "ml_conf": 0.55, "stagnation_bars": 5},
        {"block": "COSECHA ACTUAL", "name": "BOT-B · Cosecha Actual 👉 Score 70% (ML 55%)", "config_path": "config/bot_b_gold",
         "score": 70.0, "ml_mode": "active", "ml_conf": 0.55, "stagnation_bars": 5},
        {"block": "COSECHA ACTUAL", "name": "BOT-B · Cosecha Actual 👉 Filtro ML al 70%", "config_path": "config/bot_b_gold",
         "score": 75.0, "ml_mode": "active", "ml_conf": 0.70, "stagnation_bars": 5},
        {"block": "COSECHA ACTUAL", "name": "BOT-B · Cosecha Actual 👉 AMBOS al 70%", "config_path": "config/bot_b_gold",
         "score": 70.0, "ml_mode": "active", "ml_conf": 0.70, "stagnation_bars": 5},
    ]

    print("  Ejecutando simulación multi-hilo de 14 escenarios comparativos...")
    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        future_to_sc = {executor.submit(run_single_scenario, sc, df_clean_h1, df_clean_d1, df_clean_h4, specs): sc for sc in scenarios}
        for future in concurrent.futures.as_completed(future_to_sc):
            sc = future_to_sc[future]
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                print(f"Error in {sc['name']}: {e}")

    # Sort results in original scenario order
    order_map = {sc["name"]: i for i, sc in enumerate(scenarios)}
    results.sort(key=lambda x: order_map.get(x["Escenario"], 99))

    header = f"  {'Configuración / Experimento':<45} │ {'Trades':>6} │ {'W/L':>7} │ {'WinRate':>7} │ {'PF':>6} │ {'Net Profit':>11} │ {'Retorno':>8} │ {'Max DD':>7}"
    print("\n" + "=" * 115)
    print("  📊 RESULTADOS: ESTILO 'DEJAR CORRER' (Sin Cosechas Prematuras)")
    print("=" * 115)
    print(header)
    print("  " + "─" * 111)
    for r in results:
        if r["Bloque"] == "DEJAR CORRER":
            print(f"  {r['Escenario']:<45} │ {r['Trades']:>6} │ {r['W/L']:>7} │ {r['WinRate']:>7} │ {r['PF']:>6} │ {r['NetProfit']:>11} │ {r['Total_R']:>8} │ {r['MaxDD']:>7}")

    print("\n" + "=" * 115)
    print("  📊 RESULTADOS: ESTILO 'COSECHA ACTUAL' (Con Salida de Estancamiento)")
    print("=" * 115)
    print(header)
    print("  " + "─" * 111)
    for r in results:
        if r["Bloque"] == "COSECHA ACTUAL":
            print(f"  {r['Escenario']:<45} │ {r['Trades']:>6} │ {r['W/L']:>7} │ {r['WinRate']:>7} │ {r['PF']:>6} │ {r['NetProfit']:>11} │ {r['Total_R']:>8} │ {r['MaxDD']:>7}")
    print("=" * 115)

    with open("data/experiments/score_70_comparison.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\n  ✓ Resultados completos guardados en data/experiments/score_70_comparison.json")
    mt5.shutdown()


if __name__ == "__main__":
    main()
