"""Build a point-in-time, execution-aligned ML training dataset.

The model is an observer by default. Labels simulate the same structural
stop entry, SL and TP used by the bot; a favourable future price move alone
does not make a sample positive.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd

from core.config_loader import AppConfig, load_config
from core.constants import Direction
from data.cleaner import DataCleaner
from data.downloader import DataDownloader
from indicators.momentum import calculate_all_momentum_indicators
from indicators.structure import get_market_structure
from indicators.trend import calculate_all_trend_indicators
from indicators.volatility import calculate_all_volatility_indicators
from indicators.volume import calculate_all_volume_indicators
from ml.features import extract_features
from risk.stop_loss import StopLossCalculator
from risk.take_profit import TakeProfitCalculator
from strategy.scorer import ScoreEngine

logger = logging.getLogger(__name__)


def build_dataset(
    symbols: List[str],
    timeframe: str = "M15",
    months: int = 6,
    bars_per_download: int = 5000,
    lookahead_bars: int = 20,
    target_atr_multiple: float = 1.5,
    output_path: str = "data/ml_dataset.csv",
    config: Optional[AppConfig] = None,
) -> pd.DataFrame:
    """Download a chronological multi-symbol dataset and label executable setups.

    ``target_atr_multiple`` remains for backwards compatibility. The target is
    now the configured TP, so it cannot silently diverge from live execution.
    """
    del target_atr_multiple
    cfg = config or load_config("config")
    downloader, cleaner = DataDownloader(), DataCleaner()
    start = datetime.now(timezone.utc) - timedelta(days=max(1, months) * 30)
    end = datetime.now(timezone.utc)
    all_rows: List[pd.DataFrame] = []

    for symbol in symbols:
        try:
            raw = downloader.download_rates_range(symbol, timeframe, start, end)
            # Some brokers restrict range history. The fallback is explicitly
            # logged rather than pretending that a shorter sample is N months.
            if raw is None or len(raw) < 300:
                raw = downloader.download_rates(
                    symbol, timeframe, count=bars_per_download, closed_only=True
                )
                logger.warning(
                    "[ML Dataset] %s range unavailable; using only %s returned closed bars.",
                    symbol, 0 if raw is None else len(raw),
                )
            if raw is None or len(raw) < 300:
                logger.warning("[ML Dataset] Insufficient history for %s.", symbol)
                continue

            # Downloader deliberately retains ``time`` both as index and
            # column. Reset it before sort so pandas cannot bind ambiguously.
            raw = raw.reset_index(drop=True).sort_values("time").drop_duplicates("time").iloc[:-1].copy()
            df, quality = cleaner.clean(raw)
            if not quality.get("quality_ok", True):
                logger.warning("[ML Dataset] %s failed data-quality checks.", symbol)
                continue
            df = _add_indicators(df, cfg)
            df = df.dropna(subset=["atr", "ema_fast", "ema_slow", "rsi"]).reset_index(drop=True)
            setup_df = _download_labeled_timeframe(downloader, cleaner, symbol, cfg.timeframes.setup, start, end, cfg)
            htf_df = _download_labeled_timeframe(downloader, cleaner, symbol, cfg.timeframes.higher, start, end, cfg)
            rows = _label_dataframe(df, symbol, timeframe, cfg, lookahead_bars, setup_df, htf_df)
            if not rows.empty:
                all_rows.append(rows)
                logger.info("[ML Dataset] %s: %s executable samples.", symbol, len(rows))
        except Exception:
            logger.exception("[ML Dataset] Error processing %s", symbol)

    if not all_rows:
        return pd.DataFrame()
    combined = pd.concat(all_rows, ignore_index=True)
    combined = combined.sort_values(["time", "symbol"]).reset_index(drop=True)
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    combined.to_csv(output_path, index=False)
    logger.info(
        "[ML Dataset] Saved %s chronological samples to %s (positive %.1f%%).",
        len(combined), output_path, combined["label"].mean() * 100,
    )
    return combined


def _add_indicators(df: pd.DataFrame, cfg: AppConfig) -> pd.DataFrame:
    df = calculate_all_trend_indicators(
        df, cfg.trend.ema_fast, cfg.trend.ema_medium,
        cfg.trend.ema_slow, cfg.trend.adx_period,
    )
    df = calculate_all_momentum_indicators(
        df, cfg.momentum.rsi_period, cfg.momentum.macd_fast,
        cfg.momentum.macd_slow, cfg.momentum.macd_signal,
    )
    df = calculate_all_volatility_indicators(
        df, cfg.volatility.atr_period, cfg.volatility.atr_avg_period,
    )
    volume_col = "real_volume" if str(getattr(cfg.volume.volume_mode, "value", cfg.volume.volume_mode)).lower() == "real" else "tick_volume"
    return calculate_all_volume_indicators(
        df, volume_period=cfg.volume.period, volume_col=volume_col, use_obv=cfg.volume.use_obv,
    )


def _download_labeled_timeframe(
    downloader: DataDownloader, cleaner: DataCleaner, symbol: str, timeframe: str,
    start: datetime, end: datetime, cfg: AppConfig,
) -> Optional[pd.DataFrame]:
    """Fetch a separate, correctly closed MTF history for point-in-time labels."""
    raw = downloader.download_rates_range(symbol, timeframe, start, end)
    if raw is None or len(raw) < 100:
        return None
    raw = raw.reset_index(drop=True).sort_values("time").drop_duplicates("time").iloc[:-1].copy()
    clean, quality = cleaner.clean(raw)
    if not quality.get("quality_ok", True):
        return None
    return _add_indicators(clean, cfg)


def _label_dataframe(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    cfg: AppConfig,
    lookahead_bars: int = 20,
    df_setup: Optional[pd.DataFrame] = None,
    df_htf: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Create samples using only data available at each signal-bar close."""
    rows: List[dict] = []
    scorer = ScoreEngine(cfg)
    sl_calc, tp_calc = StopLossCalculator(), TakeProfitCalculator()
    point = _point_size_for(symbol)
    # Structure only considers ``max_swing_age`` recent bars. Restricting the
    # point-in-time window keeps a six-month M15 build tractable while the
    # indicators themselves were calculated over the full *past* series.
    history_bars = max(250, cfg.structure.max_swing_age + 50)
    warmup = max(history_bars, cfg.structure.swing_lookback + cfg.structure.swing_confirmation + 20)

    for i in range(warmup, len(df) - lookahead_bars):
        window = df.iloc[i - history_bars + 1:i + 1]
        structure = get_market_structure(
            df=window,
            atr_series=window["atr"],
            swing_lookback=cfg.structure.swing_lookback,
            swing_confirmation=cfg.structure.swing_confirmation,
            bos_min_distance_atr=cfg.structure.bos_min_distance_atr,
            bos_close_confirmation=cfg.structure.bos_close_confirmation,
            use_bos=cfg.structure.use_bos,
            use_choch=cfg.structure.use_choch,
            max_swing_age=cfg.structure.max_swing_age,
        )
        bar_time = current_time = window.iloc[-1]["time"]
        setup_slice = _closed_mtf_slice(df_setup, bar_time, cfg.timeframes.setup)
        htf_slice = _closed_mtf_slice(df_htf, bar_time, cfg.timeframes.higher)
        # A profile requiring MTF confirmation cannot be truthfully labelled
        # from the entry window alone.
        if cfg.confirmation.require_setup_trend_alignment and (setup_slice is None or setup_slice.empty):
            continue
        score, signal = scorer.calculate_score(
            symbol, timeframe, window, setup_slice, htf_slice, structure, cfg
        )
        if signal is None or score is None:
            continue

        current = window.iloc[-1]
        direction = signal.direction
        swing = structure.last_swing_high if direction == Direction.BUY else structure.last_swing_low
        if swing is None:
            continue
        buffer = max(float(cfg.trade.breakout_buffer_points), 1.0) * point
        entry = float(swing.price) + buffer if direction == Direction.BUY else float(swing.price) - buffer
        # A structural pending stop must still be ahead of current price.
        if (direction == Direction.BUY and entry <= float(current["close"])) or (
            direction == Direction.SELL and entry >= float(current["close"])
        ):
            continue

        atr = float(current["atr"])
        spread_price = max(0.0, float(current.get("spread", 0.0))) * point
        sl = sl_calc.calculate_sl(direction, entry, structure, atr, spread_price, cfg.volatility, point)
        tp = tp_calc.calculate_tp(direction, entry, sl, structure, atr, cfg.trade)
        expiry_bars = max(1, int(cfg.trade.breakout_expiry_bars))
        label, filled = _breakout_outcome(
            df.iloc[i + 1:i + 1 + lookahead_bars], direction, entry, sl, tp, expiry_bars=expiry_bars
        )
        features = extract_features(
            window.iloc[-60:],
            structure_score=score.structure.score,
            trend_score=score.trend.score,
            spread_points=float(current.get("spread", 0.0)),
            atr_value=atr,
            point_size=point,
        )
        rows.append({
            "symbol": symbol,
            "time": current["time"],
            "direction": direction.value,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "filled": int(filled),
            "label": int(label),
            **features,
        })
    return pd.DataFrame(rows)


def _closed_mtf_slice(data: Optional[pd.DataFrame], entry_time, timeframe: str) -> Optional[pd.DataFrame]:
    if data is None or data.empty:
        return None
    if hasattr(entry_time, "to_pydatetime"):
        entry_time = entry_time.to_pydatetime()
    if getattr(entry_time, "tzinfo", None) is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    cutoff = entry_time - timedelta(minutes=minutes.get(timeframe, 15))
    result = data[data["time"] <= cutoff]
    return result.iloc[-250:] if not result.empty else None


def _breakout_outcome(
    future: pd.DataFrame,
    direction: Direction,
    entry: float,
    sl: float,
    tp: float,
    expiry_bars: int = 4,
) -> tuple[int, bool]:
    """Conservative OHLC simulation: the SL wins an intra-bar tie."""
    filled = False
    for bar_idx, (_, bar) in enumerate(future.iterrows()):
        high, low = float(bar["high"]), float(bar["low"])
        if not filled:
            if bar_idx >= expiry_bars:
                return 0, False
            triggered = high >= entry if direction == Direction.BUY else low <= entry
            invalidated = low <= sl if direction == Direction.BUY else high >= sl
            # OHLC cannot order both events. Match the live/backtest
            # stop-first policy: the position is treated as filled and lost.
            if invalidated and triggered:
                return 0, True
            if invalidated:
                return 0, False
            filled = triggered
            if not filled:
                continue
        if direction == Direction.BUY:
            if low <= sl:
                return 0, True
            if high >= tp:
                return 1, True
        else:
            if high >= sl:
                return 0, True
            if low <= tp:
                return 1, True
    return 0, filled


def _point_size_for(symbol: str) -> float:
    symbol = symbol.upper()
    if "JPY" in symbol:
        return 0.001
    if symbol.startswith(("XAU", "XAG")):
        return 0.01
    return 0.00001
