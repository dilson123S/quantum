"""Explainable regime and Smart Money Concepts quality policy.

The policy is intentionally independent from the score engine.  It can run in
shadow mode, persist its reasons, and later be enabled unchanged in both live
planning and the backtest.  That separation prevents quietly tuning a score
threshold to a particular sample.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from core.config_loader import AppConfig
from core.constants import Direction


@dataclass(frozen=True)
class MarketQualityAssessment:
    """Outcome split by policy so a shadow rule cannot accidentally block."""

    regime_reasons: List[str]
    smc_reasons: List[str]
    active_reasons: List[str]
    shadow_reasons: List[str]

    @property
    def approved(self) -> bool:
        return not self.active_reasons

    def as_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "regime_reasons": self.regime_reasons,
            "smc_reasons": self.smc_reasons,
            "active_reasons": self.active_reasons,
            "shadow_reasons": self.shadow_reasons,
        }


class MarketQualityFilter:
    """Assess regime and SMC confluence from an immutable closed-bar context."""

    def __init__(self, config: AppConfig):
        self.config = config

    def assess(self, context: Dict[str, Any], direction: Direction) -> MarketQualityAssessment:
        regime = context.get("regime", {}) if context else {}
        smc = context.get("smc", {}) if context else {}
        regime_reasons: List[str] = []
        smc_reasons: List[str] = []
        label = str(regime.get("label", "UNKNOWN"))

        allowed = {item.strip().upper() for item in self.config.regime_filter.allowed_regimes}
        if allowed and label not in allowed:
            regime_reasons.append(f"REGIME_{label}_NOT_ALLOWED")
        direction_str = str(direction.value if hasattr(direction, "value") else direction).upper()
        direction_allowlist = {
            regime_name.strip().upper(): {item.strip().upper() for item in directions}
            for regime_name, directions in self.config.regime_filter.allowed_directions_by_regime.items()
        }
        regime_directions = direction_allowlist.get(label)
        if regime_directions and direction_str not in regime_directions:
            regime_reasons.append(f"REGIME_{label}_DIRECTION_{direction_str}_NOT_ALLOWED")
        # Liquidity guard config (Fix 1, 2, 3)
        liquidity_guard_cfg = getattr(self.config, "liquidity_guard", None)
        liquidity = context.get("liquidity", {}) if context else {}
        sweep = liquidity.get("sweep", {}) if liquidity else {}
        macro_dir = context.get("macro_direction")
        level_name = str(sweep.get("level", "LEVEL")) if sweep.get("detected") else ""

        # Institutional macro expansion: trade direction matches higher timeframe trend (H4/D1)
        # when expanding through Asian session boundaries or previous day boundaries
        is_macro_expansion = (
            (direction in (Direction.SELL, "SELL") and macro_dir in (Direction.SELL, "SELL") and level_name in ("ASIA_LOW", "PREVIOUS_DAY_LOW")) or
            (direction in (Direction.BUY, "BUY") and macro_dir in (Direction.BUY, "BUY") and level_name in ("ASIA_HIGH", "PREVIOUS_DAY_HIGH"))
        )

        if self.config.regime_filter.block_ranging and label == "RANGING":
            # Breakouts and macro-aligned expansions naturally originate from a consolidation/ranging regime.
            # Do not block if the trade is confirmed by structural break, macro expansion, or macro trend alignment,
            # BUT require real expansion momentum: ADX must be at least 18.0 to prevent entering dead chop!
            adx_val = float(regime.get("adx") or 0.0)
            dir_str = str(direction.value if hasattr(direction, "value") else direction).upper()
            is_trend_aligned = bool(macro_dir and str(macro_dir).upper() == dir_str)
            has_breakout_confirmation = bool(
                (context.get("structure_score", 0.0) >= 20.0 or is_macro_expansion or is_trend_aligned)
                and adx_val >= 18.0
            )
            if not has_breakout_confirmation:
                regime_reasons.append("REGIME_RANGING" if adx_val >= 18.0 else f"REGIME_RANGING_LOW_ADX_{adx_val:.1f}")
        if self.config.regime_filter.block_low_volatility and label == "LOW_VOLATILITY":
            regime_reasons.append("REGIME_LOW_VOLATILITY")

        # --- Fix 3: Require FVG or OB to be *in_zone* for SMC confluencia to count ---
        #     Dirección alineada sin estar dentro de la zona = confluencia falsa.
        require_in_zone = getattr(liquidity_guard_cfg, "require_smc_in_zone", True) if liquidity_guard_cfg else True
        if require_in_zone:
            fvg = smc.get("fair_value_gap", {})
            ob = smc.get("order_block", {})
            fvg_in_zone = fvg.get("in_zone", False) if isinstance(fvg, dict) else False
            ob_in_zone = ob.get("in_zone", False) if isinstance(ob, dict) else False
            fvg_aligned = smc.get("fvg_aligned", False) and fvg_in_zone
            ob_aligned = smc.get("order_block_aligned", False) and ob_in_zone
        else:
            fvg_aligned = smc.get("fvg_aligned", False)
            ob_aligned = smc.get("order_block_aligned", False)

        if self.config.smc_filter.require_sweep and not smc.get("sweep_aligned", False):
            smc_reasons.append(f"SMC_NO_{direction.value}_LIQUIDITY_SWEEP")
        if self.config.smc_filter.require_fvg_or_order_block and not (fvg_aligned or ob_aligned):
            smc_reasons.append(f"SMC_NO_{direction.value}_FVG_OR_OB_IN_ZONE")

        # Anti-trap guard: Reject breakout orders entering directly into an opposite liquidity sweep
        if sweep.get("detected"):
            sweep_dir = sweep.get("direction")
            if not is_macro_expansion:
                if (direction in (Direction.BUY, "BUY") and sweep_dir == "SELL") or (
                    direction in (Direction.SELL, "SELL") and sweep_dir == "BUY"
                ):
                    smc_reasons.append(f"SMC_CONTRA_LIQUIDITY_SWEEP_{level_name}")

        # Shared liquidity data for Fix 1 and Fix 2
        nearest = liquidity.get("nearest_level", {}) if liquidity else {}
        nearest_name = nearest.get("name", "") if nearest else ""
        is_daily_level = nearest_name in ("PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW")
        dist_atr = nearest.get("distance_atr", 999.0) if nearest else 999.0
        sweep_confirmed = sweep.get("detected", False)

        # --- Fix 1: Block entries within 0.6 ATR of PDH/PDL without confirmed sweep ---
        #     Zona de stop hunt institucional. Aplica al entrar CONTRA el nivel sin confirmación
        #     de ruptura. Si la orden está en expansión macro o ya cruzó el nivel, permitir.
        pdx_threshold = getattr(liquidity_guard_cfg, "pdx_proximity_atr", 0.6) if liquidity_guard_cfg else 0.6
        if is_daily_level and dist_atr < pdx_threshold and not sweep_confirmed and not is_macro_expansion:
            pdl_val = liquidity.get("previous_day", {}).get("low")
            pdh_val = liquidity.get("previous_day", {}).get("high")
            close_val = context.get("close")
            is_breakout_beyond = False
            if close_val is not None:
                if nearest_name == "PREVIOUS_DAY_LOW" and pdl_val is not None and close_val < float(pdl_val) and direction in (Direction.SELL, "SELL"):
                    is_breakout_beyond = True
                elif nearest_name == "PREVIOUS_DAY_HIGH" and pdh_val is not None and close_val > float(pdh_val) and direction in (Direction.BUY, "BUY"):
                    is_breakout_beyond = True

            if not is_breakout_beyond:
                smc_reasons.append(
                    f"SMC_STOP_HUNT_ZONE_{nearest_name}_dist_{dist_atr:.2f}ATR"
                )

        # --- Fix 2: Block breakout toward daily levels during Asia session ---
        #     Asia = liquidez institucional mínima. Los breakouts de PDH/PDL en Asia
        #     son casi siempre trampas de retail que luego revierten en Londres/NY.
        session_label = str(context.get("session", {}).get("label", "")) if context else ""
        block_asia = getattr(liquidity_guard_cfg, "block_asia_daily_breakouts", True) if liquidity_guard_cfg else True
        if block_asia and session_label == "ASIA" and is_daily_level:
            # SELL rompiendo hacia abajo del PDL, o BUY rompiendo hacia arriba del PDH
            pdl_sell_trap = nearest_name == "PREVIOUS_DAY_LOW" and direction in (Direction.SELL, "SELL")
            pdh_buy_trap  = nearest_name == "PREVIOUS_DAY_HIGH" and direction in (Direction.BUY, "BUY")
            if pdl_sell_trap or pdh_buy_trap:
                smc_reasons.append(
                    f"SMC_ASIA_SESSION_DAILY_LEVEL_TRAP_{nearest_name}"
                )

        active_reasons: List[str] = []
        shadow_reasons: List[str] = []
        if self.config.regime_filter.enabled:
            (shadow_reasons if self.config.regime_filter.shadow_mode else active_reasons).extend(regime_reasons)
        if self.config.smc_filter.enabled:
            (shadow_reasons if self.config.smc_filter.shadow_mode else active_reasons).extend(smc_reasons)
        return MarketQualityAssessment(regime_reasons, smc_reasons, active_reasons, shadow_reasons)
