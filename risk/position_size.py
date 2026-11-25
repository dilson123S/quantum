"""Position sizing and lot size calculation."""
import math
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from core.constants import SignalClassification, RiskMode
from core.config_loader import RiskConfig


class PositionSizeCalculator:
    """Calculates position sizes and risk parameters."""

    def __init__(self):
        pass

    def calculate_lot(
        self,
        symbol: str,
        risk_money: float,
        sl_distance_points: float,
        tick_value: float,
        tick_size: float,
        point: float,
        volume_min: float,
        volume_max: float,
        volume_step: float,
        min_trade_volume: Optional[float] = None
    ) -> float:
        """Calculate the volume (lot size) for a trade based on risk money and stop loss distance."""
        # A zero risk budget is a hard stop, never a request to trade the
        # broker minimum.  Returning the minimum lot here used to turn a
        # circuit-breaker decision into a real exposure.
        if risk_money <= 0 or sl_distance_points <= 0:
            return 0.0

        # Normalize point and tick values
        point_factor = (tick_size / point) if point > 0 and tick_size > 0 else 1.0
        value_per_point = tick_value / point_factor if point_factor > 0 else 1.0

        risk_per_lot = sl_distance_points * value_per_point
        if risk_per_lot <= 0:
            return 0.0

        raw_volume = risk_money / risk_per_lot

        # Quantize to volume step
        if volume_step > 0:
            # Broker steps are not necessarily powers of ten (for example
            # 0.25). Decimal avoids both binary float drift and log10-based
            # precision mistakes.
            step = Decimal(str(volume_step))
            steps = (Decimal(str(raw_volume)) / step).to_integral_value(rounding=ROUND_DOWN)
            quantized_volume = float(steps * step)
        else:
            quantized_volume = round(raw_volume, 2)

        if min_trade_volume is not None and min_trade_volume > volume_min:
            # If a custom minimum trade volume is configured (e.g. 0.02 to allow 0.01 partial closes)
            # and the raw calculation produced at least the broker's volume_min, elevate to min_trade_volume
            if quantized_volume >= volume_min and quantized_volume < min_trade_volume:
                quantized_volume = min_trade_volume

        # Do not round a too-small order *up* to the broker minimum: that
        # would exceed the configured cash-risk budget.  The caller/guard
        # treats 0.0 as "skip this trade".
        if quantized_volume < volume_min:
            return 0.0

        return min(quantized_volume, volume_max)

    def calculate_risk_money(
        self,
        balance: float,
        risk_config: RiskConfig,
        score_classification: SignalClassification,
        consecutive_losses: int
    ) -> float:
        """Calculate the amount of money to risk for a trade."""
        if balance <= 0:
            return 0.0

        mode = getattr(risk_config, "mode", RiskMode.PERCENT)
        is_percent_mode = (mode == RiskMode.PERCENT or str(mode).lower() == "percent")

        if is_percent_mode:
            base_risk_pct = getattr(risk_config, "risk_percent", 1.0)
            max_risk_pct = getattr(risk_config, "max_risk_per_trade", 1.0)
            effective_pct = min(base_risk_pct, max_risk_pct)
            base_risk = balance * (effective_pct / 100.0)
        else:
            base_risk = getattr(risk_config, "risk_money", 20.0)

        configured_scores = getattr(risk_config, "risk_by_score", {}) or {}
        if configured_scores:
            score_multiplier = float(configured_scores.get(score_classification.value, 0.0))
        elif score_classification in (SignalClassification.EXCEPTIONAL, SignalClassification.EXCELLENT):
            score_multiplier = 1.0
        elif score_classification == SignalClassification.GOOD:
            score_multiplier = 0.75
        elif score_classification == SignalClassification.MODERATE:
            score_multiplier = 0.50
        else:
            score_multiplier = 0.25

        # Multiplier based on consecutive losses
        max_cons_losses = getattr(risk_config, "max_consecutive_losses", 0)
        if max_cons_losses <= 0:
            loss_multiplier = 1.0
        else:
            configured_losses = getattr(risk_config, "consecutive_loss_risk_reduction", {}) or {}
            if configured_losses:
                applicable = [int(key) for key in configured_losses if int(key) <= consecutive_losses]
                loss_multiplier = float(configured_losses[max(applicable)]) if applicable else 1.0
            elif consecutive_losses <= 1:
                loss_multiplier = 1.0
            elif consecutive_losses == 2:
                loss_multiplier = 0.75
            else:
                loss_multiplier = 0.50

        risk_money = base_risk * score_multiplier * loss_multiplier
        return round(risk_money, 2)
