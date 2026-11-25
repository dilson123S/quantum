"""Kronos Foundation Model Meta-Filter for Trading Bot Execution.

Evaluates trade setups using Kronos K-line trajectory forecasting.
Provides both SHADOW mode (telemetry logging) and ACTIVE mode (directional veto).
"""
import logging
from typing import Optional, Tuple, Dict, Any
import pandas as pd

from ml.kronos_oracle import KronosOracle, KronosVerdict

logger = logging.getLogger(__name__)


class KronosFilter:
    """Trade filter powered by Kronos autoregressive Foundation Model."""

    def __init__(
        self,
        enabled: bool = False,
        shadow_mode: bool = True,
        model_name: str = "NeoQuasar/Kronos-small",
        tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-base",
        device: str = "cpu",
        lookback_bars: int = 200,
        forecast_horizon: int = 12,
        temperature: float = 0.7,
        sample_count: int = 2,
        min_confidence: float = 0.60,
    ):
        self.enabled = enabled
        self.shadow_mode = shadow_mode
        self.model_name = model_name
        self.tokenizer_name = tokenizer_name
        self.device = device
        self.lookback_bars = lookback_bars
        self.forecast_horizon = forecast_horizon
        self.temperature = temperature
        self.sample_count = sample_count
        self.min_confidence = min_confidence

        self.oracle: Optional[KronosOracle] = None
        if self.enabled:
            try:
                self.oracle = KronosOracle(
                    model_name=self.model_name,
                    tokenizer_name=self.tokenizer_name,
                    device=self.device,
                )
                logger.info(
                    "KronosFilter active (Model: %s, Mode: %s, Horizon: %d bars)",
                    self.model_name,
                    "SHADOW" if self.shadow_mode else "ACTIVE",
                    self.forecast_horizon,
                )
            except Exception as exc:
                logger.warning("Failed to initialize KronosFilter: %s", exc)
                self.oracle = None

    @property
    def is_active(self) -> bool:
        return self.enabled and self.oracle is not None and self.oracle.is_ready

    def evaluate(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        df: pd.DataFrame,
    ) -> Tuple[bool, Optional[float], str, Optional[Dict[str, Any]]]:
        """Evaluates trade setup against Kronos foundation trajectory.

        Returns:
            (approved: bool, confidence: float or None, reason: str, verdict_dict: dict or None)
        """
        if not self.enabled:
            return True, None, "KronosFilter disabled", None

        if not self.is_active:
            return True, None, "KronosFilter model unavailable", None

        verdict: KronosVerdict = self.oracle.evaluate_setup(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            df=df,
            horizon_bars=self.forecast_horizon,
            lookback_bars=self.lookback_bars,
            min_confidence=self.min_confidence,
            temperature=self.temperature,
            sample_count=self.sample_count,
        )

        verdict_dict = verdict.to_dict()

        if self.shadow_mode:
            # SHADOW MODE: Always approve, but log and record verdict
            status = "PASS" if verdict.approved else "WOULD_BLOCK"
            logger.info(
                "[KRONOS SHADOW] %s %s @ %.2f | Conf: %.1f%% | Drift: %+.2f%% | "
                "MFE: %.1f pts | MAE: %.1f pts | Verdict: %s (%s) [%.2fs]",
                symbol,
                direction.upper(),
                entry_price,
                verdict.confidence * 100.0,
                verdict.drift_pct,
                verdict.projected_mfe_points,
                verdict.projected_mae_points,
                status,
                verdict.reason,
                verdict.elapsed_seconds,
            )
            return True, verdict.confidence, f"Shadow: {verdict.reason}", verdict_dict

        else:
            # ACTIVE MODE: Strictly enforce approval
            if verdict.approved:
                logger.info(
                    "[KRONOS ACTIVE APPROVED] %s %s @ %.2f | Conf: %.1f%% | %s",
                    symbol,
                    direction.upper(),
                    entry_price,
                    verdict.confidence * 100.0,
                    verdict.reason,
                )
                return True, verdict.confidence, verdict.reason, verdict_dict
            else:
                logger.warning(
                    "[KRONOS ACTIVE BLOCKED] %s %s @ %.2f | Conf: %.1f%% | Veto: %s",
                    symbol,
                    direction.upper(),
                    entry_price,
                    verdict.confidence * 100.0,
                    verdict.reason,
                )
                return False, verdict.confidence, f"Kronos Veto: {verdict.reason}", verdict_dict
