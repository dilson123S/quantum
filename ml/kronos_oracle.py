"""Kronos Foundation Model Oracle for K-line Trajectory Forecasting.

Treats candlestick sequences as financial language to predict multi-step
forward trajectories and validate algorithmic trade setups.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import numpy as np
import pandas as pd
import torch

logger = logging.getLogger(__name__)

# Global singleton cache for model and tokenizer to prevent re-loading weights
_KRONOS_CACHE: Dict[str, Any] = {
    "tokenizer": None,
    "model": None,
    "predictor": None,
    "model_name": None,
    "tokenizer_name": None,
    "device": None,
}


@dataclass
class KronosVerdict:
    """Quantitative forecast verdict from Kronos foundation model."""
    approved: bool
    confidence: float
    drift_pct: float
    projected_mfe_points: float
    projected_mae_points: float
    sl_breach_projected: bool
    tp_reach_projected: bool
    horizon_bars: int
    elapsed_seconds: float
    reason: str
    projected_candles: List[Dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "confidence": round(self.confidence, 4),
            "drift_pct": round(self.drift_pct, 4),
            "projected_mfe_points": round(self.projected_mfe_points, 2),
            "projected_mae_points": round(self.projected_mae_points, 2),
            "sl_breach_projected": self.sl_breach_projected,
            "tp_reach_projected": self.tp_reach_projected,
            "horizon_bars": self.horizon_bars,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "reason": self.reason,
            "candles_count": len(self.projected_candles),
        }


class KronosOracle:
    """Inference engine wrapping Kronos Transformer for trade validation."""

    def __init__(
        self,
        model_name: str = "NeoQuasar/Kronos-small",
        tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-base",
        device: str = "cpu",
        max_context: int = 512,
    ):
        self.model_name = model_name
        self.tokenizer_name = tokenizer_name
        self.device = device
        self.max_context = max_context
        self._ensure_loaded()

    def _ensure_loaded(self):
        """Loads and caches the Kronos foundation model."""
        global _KRONOS_CACHE
        if (
            _KRONOS_CACHE["predictor"] is not None
            and _KRONOS_CACHE["model_name"] == self.model_name
            and _KRONOS_CACHE["device"] == self.device
        ):
            self.tokenizer = _KRONOS_CACHE["tokenizer"]
            self.model = _KRONOS_CACHE["model"]
            self.predictor = _KRONOS_CACHE["predictor"]
            return

        try:
            from ml.kronos import Kronos, KronosTokenizer, KronosPredictor

            logger.info("Initializing Kronos Foundation Model: %s on %s...", self.model_name, self.device)
            t0 = time.time()
            tokenizer = KronosTokenizer.from_pretrained(self.tokenizer_name)
            model = Kronos.from_pretrained(self.model_name)
            
            # Select device
            target_device = self.device
            if target_device == "cuda" and not torch.cuda.is_available():
                logger.warning("CUDA requested for Kronos but unavailable. Falling back to CPU.")
                target_device = "cpu"
                
            model = model.to(target_device)
            model.eval()

            predictor = KronosPredictor(model, tokenizer, max_context=self.max_context)
            t_load = time.time() - t0

            _KRONOS_CACHE["tokenizer"] = tokenizer
            _KRONOS_CACHE["model"] = model
            _KRONOS_CACHE["predictor"] = predictor
            _KRONOS_CACHE["model_name"] = self.model_name
            _KRONOS_CACHE["tokenizer_name"] = self.tokenizer_name
            _KRONOS_CACHE["device"] = target_device

            self.tokenizer = tokenizer
            self.model = model
            self.predictor = predictor
            logger.info("Kronos Foundation Model ready in %.2fs", t_load)
        except Exception as exc:
            logger.error("Failed to load Kronos Foundation Model: %s", exc, exc_info=True)
            self.tokenizer = None
            self.model = None
            self.predictor = None

    @property
    def is_ready(self) -> bool:
        return self.predictor is not None

    def forecast_trajectory(
        self,
        df: pd.DataFrame,
        pred_len: int = 12,
        lookback: int = 200,
        temperature: float = 0.7,
        top_p: float = 0.9,
        sample_count: int = 2,
    ) -> Optional[pd.DataFrame]:
        """Generates forward K-line trajectory for the next pred_len bars."""
        if not self.is_ready or df is None or len(df) < 50:
            return None

        try:
            # Prepare required columns
            df_slice = df.iloc[-min(len(df), lookback):].copy()
            for col in ["open", "high", "low", "close"]:
                if col not in df_slice.columns:
                    logger.warning("Missing required column '%s' for Kronos", col)
                    return None

            if "volume" not in df_slice.columns:
                df_slice["volume"] = 100.0

            if "amount" not in df_slice.columns:
                df_slice["amount"] = df_slice["close"] * df_slice["volume"]

            # Ensure valid timestamps as a pd.Series with datetime dtype
            if "time" in df_slice.columns:
                timestamps = pd.Series(pd.to_datetime(df_slice["time"].values))
            elif "timestamps" in df_slice.columns:
                timestamps = pd.Series(pd.to_datetime(df_slice["timestamps"].values))
            elif "timestamp" in df_slice.columns:
                timestamps = pd.Series(pd.to_datetime(df_slice["timestamp"].values))
            elif isinstance(df_slice.index, pd.DatetimeIndex):
                timestamps = pd.Series(df_slice.index.values)
            else:
                timestamps = pd.Series(pd.date_range(end=pd.Timestamp.now(), periods=len(df_slice), freq="15min"))

            timestamps = pd.to_datetime(timestamps)

            # Determine frequency delta
            if len(timestamps) > 1:
                step_delta = timestamps.iloc[-1] - timestamps.iloc[-2]
                if step_delta.total_seconds() <= 0:
                    step_delta = pd.Timedelta(minutes=15)
            else:
                step_delta = pd.Timedelta(minutes=15)

            last_ts = timestamps.iloc[-1]
            future_timestamps = pd.Series(pd.date_range(start=last_ts + step_delta, periods=pred_len, freq=step_delta))

            x_df = df_slice[["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)
            x_ts = timestamps.reset_index(drop=True)
            y_ts = future_timestamps.reset_index(drop=True)

            with torch.no_grad():
                pred_df = self.predictor.predict(
                    df=x_df,
                    x_timestamp=x_ts,
                    y_timestamp=y_ts,
                    pred_len=pred_len,
                    T=temperature,
                    top_p=top_p,
                    sample_count=sample_count,
                    verbose=False,
                )
            return pred_df
        except Exception as exc:
            logger.warning("Kronos trajectory forecast failed: %s", exc)
            return None

    def evaluate_setup(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        df: pd.DataFrame,
        horizon_bars: int = 12,
        lookback_bars: int = 200,
        min_confidence: float = 0.60,
        temperature: float = 0.7,
        sample_count: int = 2,
    ) -> KronosVerdict:
        """Evaluates trade setup alignment against Kronos autoregressive forward paths."""
        t0 = time.time()
        if not self.is_ready:
            return KronosVerdict(
                approved=True,
                confidence=0.5,
                drift_pct=0.0,
                projected_mfe_points=0.0,
                projected_mae_points=0.0,
                sl_breach_projected=False,
                tp_reach_projected=False,
                horizon_bars=horizon_bars,
                elapsed_seconds=time.time() - t0,
                reason="Kronos model not initialized (bypassed)",
            )

        pred_df = self.forecast_trajectory(
            df=df,
            pred_len=horizon_bars,
            lookback=lookback_bars,
            temperature=temperature,
            top_p=0.9,
            sample_count=sample_count,
        )

        if pred_df is None or len(pred_df) == 0:
            return KronosVerdict(
                approved=True,
                confidence=0.5,
                drift_pct=0.0,
                projected_mfe_points=0.0,
                projected_mae_points=0.0,
                sl_breach_projected=False,
                tp_reach_projected=False,
                horizon_bars=horizon_bars,
                elapsed_seconds=time.time() - t0,
                reason="Kronos forecast unavailable (bypassed)",
            )

        # Analyze predicted price path
        norm_dir = direction.upper().strip()
        last_pred_close = float(pred_df["close"].iloc[-1])
        proj_max_high = float(pred_df["high"].max())
        proj_min_low = float(pred_df["low"].min())

        # Net Drift
        drift = last_pred_close - entry_price
        drift_pct = (drift / entry_price) * 100.0 if entry_price > 0 else 0.0

        # MFE and MAE
        if norm_dir == "BUY":
            mfe = max(0.0, proj_max_high - entry_price)
            mae = max(0.0, entry_price - proj_min_low)
            dir_aligned = drift > 0
            sl_breach = sl_price is not None and (proj_min_low <= sl_price)
            tp_reach = tp_price is not None and (proj_max_high >= tp_price)
        else:  # SELL
            mfe = max(0.0, entry_price - proj_min_low)
            mae = max(0.0, proj_max_high - entry_price)
            dir_aligned = drift < 0
            sl_breach = sl_price is not None and (proj_max_high >= sl_price)
            tp_reach = tp_price is not None and (proj_min_low <= tp_price)

        # Confidence heuristic [0.0 - 1.0]:
        # 1. Base alignment: 0.50
        # 2. Directional alignment adds +0.20
        # 3. MFE > MAE ratio adds up to +0.20
        # 4. TP reached adds +0.10
        # 5. SL breach penalizes -0.30
        conf = 0.50
        if dir_aligned:
            conf += 0.20
        else:
            conf -= 0.15

        if mae > 0:
            ratio = mfe / mae
            if ratio >= 2.0:
                conf += 0.20
            elif ratio >= 1.2:
                conf += 0.10
            elif ratio < 0.6:
                conf -= 0.15
        elif mfe > 0:
            conf += 0.20

        if tp_reach:
            conf += 0.10

        if sl_breach:
            conf -= 0.30

        conf = float(np.clip(conf, 0.05, 0.99))
        approved = (conf >= min_confidence) and (not sl_breach)

        # Build clean candle projection records
        candles = []
        for idx, row in pred_df.iterrows():
            candles.append({
                "time": str(idx),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
            })

        if not dir_aligned:
            reason = f"Direction divergent (proj drift: {drift_pct:+.2f}%)"
        elif sl_breach:
            reason = f"SL breach projected ({proj_min_low:.2f} <= {sl_price:.2f})" if norm_dir == "BUY" else f"SL breach projected ({proj_max_high:.2f} >= {sl_price:.2f})"
        elif conf < min_confidence:
            reason = f"Confidence {conf:.1%} below threshold {min_confidence:.1%}"
        else:
            reason = f"Favorable trajectory (Conf: {conf:.1%}, MFE/MAE: {mfe:.1f}/{mae:.1f} pts)"

        elapsed = time.time() - t0
        return KronosVerdict(
            approved=approved,
            confidence=conf,
            drift_pct=drift_pct,
            projected_mfe_points=mfe,
            projected_mae_points=mae,
            sl_breach_projected=sl_breach,
            tp_reach_projected=tp_reach,
            horizon_bars=horizon_bars,
            elapsed_seconds=elapsed,
            reason=reason,
            projected_candles=candles,
        )
