"""Score Engine aggregating all 5 confirmations (0 to 100)."""
import pandas as pd
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import logging

from core.constants import Direction, SignalClassification
from core.models import ScoreResult, Signal, ComponentScore, StructureAnalysis
from core.config_loader import AppConfig
from strategy.structure_strategy import StructureStrategy
from strategy.trend_strategy import TrendStrategy
from strategy.momentum_strategy import MomentumStrategy
from strategy.volume_strategy import VolumeStrategy
from strategy.volatility_strategy import VolatilityStrategy

logger = logging.getLogger(__name__)


class ScoreEngine:
    """Combines Structure, Trend, Momentum, Volume, and Volatility confirmations."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config
        self.structure_strat = StructureStrategy(max_score=30.0)
        self.trend_strat = TrendStrategy(max_score=25.0)
        self.momentum_strat = MomentumStrategy(max_score=15.0)
        self.volume_strat = VolumeStrategy(max_score=15.0)
        self.volatility_strat = VolatilityStrategy(max_score=15.0)

    def evaluate_direction(
        self,
        direction: Direction,
        df_entry: pd.DataFrame,
        df_htf: Optional[pd.DataFrame],
        structure_analysis: StructureAnalysis,
        config: AppConfig
    ) -> ScoreResult:
        """Calculate total score for a single direction."""
        # 1. Structure (30 pts)
        struct_score = self.structure_strat.calculate_score(
            analysis=structure_analysis,
            direction=direction,
            max_swing_age=config.structure.max_swing_age
        )

        # 2. Trend (25 pts)
        trend_score = self.trend_strat.calculate_score(
            df=df_entry,
            direction=direction,
            adx_min=config.trend.adx_min,
            adx_strong=config.trend.adx_strong
        )

        # 3. Momentum (15 pts)
        mom_score = self.momentum_strat.calculate_score(
            df=df_entry,
            direction=direction,
            rsi_bull_min=config.momentum.rsi_bull_min,
            rsi_bull_strong=config.momentum.rsi_bull_strong,
            rsi_bear_max=config.momentum.rsi_bear_max,
            rsi_bear_strong=config.momentum.rsi_bear_strong
        )

        # 4. Volume (15 pts)
        vol_score = self.volume_strat.calculate_score(
            df=df_entry,
            direction=direction,
            volume_mode=config.volume.volume_mode,
            min_ratio=config.volume.min_ratio,
            strong_ratio=config.volume.strong_ratio,
            use_obv=config.volume.use_obv
        )

        # 5. Volatility (15 pts)
        volat_score = self.volatility_strat.calculate_score(
            df_entry=df_entry,
            df_htf=df_htf,
            min_atr_ratio=config.volatility.min_atr_ratio,
            max_atr_ratio=config.volatility.max_atr_ratio
        )

        # Component implementations describe their evidence on their native
        # 30/25/15 scale.  Apply configured weights here so YAML settings are
        # real policy rather than decorative, while preserving relative score.
        self._rescale(struct_score, float(config.score.structure_weight))
        self._rescale(trend_score, float(config.score.trend_weight))
        self._rescale(mom_score, float(config.score.momentum_weight))
        self._rescale(vol_score, float(config.score.volume_weight))
        self._rescale(volat_score, float(config.score.volatility_weight))

        total_pts = (
            struct_score.score +
            trend_score.score +
            mom_score.score +
            vol_score.score +
            volat_score.score
        )

        classification = SignalClassification.from_score(total_pts)

        return ScoreResult(
            direction=direction,
            total_score=round(total_pts, 2),
            classification=classification,
            structure=struct_score,
            trend=trend_score,
            momentum=mom_score,
            volume=vol_score,
            volatility=volat_score,
            timestamp=datetime.now(timezone.utc)
        )

    @staticmethod
    def _rescale(component: ComponentScore, configured_max: float) -> None:
        native_max = float(component.max_score)
        if native_max <= 0 or configured_max < 0:
            return
        component.score = round(component.score / native_max * configured_max, 4)
        component.max_score = configured_max

    @staticmethod
    def build_signal(
        symbol: str,
        timeframe: str,
        df_entry: pd.DataFrame,
        score_result: ScoreResult,
        created_at: Optional[datetime] = None,
    ) -> Signal:
        """Build the deterministic candidate shared by routing and telemetry."""
        last_price = float(df_entry['close'].iloc[-1]) if 'close' in df_entry.columns else None
        if created_at is None:
            created_at = datetime.now(timezone.utc)
        elif hasattr(created_at, "to_pydatetime"):
            created_at = created_at.to_pydatetime()
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        return Signal(
            symbol=symbol,
            timeframe=timeframe,
            direction=score_result.direction,
            score_result=score_result,
            created_at=created_at,
            entry_price=last_price,
        )

    def calculate_score(
        self,
        symbol: str,
        timeframe: str,
        df_entry: pd.DataFrame,
        df_setup: Optional[pd.DataFrame],
        df_htf: Optional[pd.DataFrame],
        structure_analysis: StructureAnalysis,
        config: Optional[AppConfig] = None,
        created_at: Optional[datetime] = None,
    ) -> Tuple[Optional[ScoreResult], Optional[Signal]]:
        """Evaluate both BUY and SELL, select the highest valid score, and check thresholds."""
        cfg = config or self.config
        if cfg is None:
            raise ValueError("AppConfig must be provided to ScoreEngine")

        # Evaluate BUY and SELL
        buy_result = self.evaluate_direction(Direction.BUY, df_entry, df_htf, structure_analysis, cfg)
        sell_result = self.evaluate_direction(Direction.SELL, df_entry, df_htf, structure_analysis, cfg)

        # Apply all direction-specific gates before choosing the winning side.
        # Otherwise a high-scoring M15 direction can fail H1/H4 alignment and
        # suppress a lower-scoring direction that actually satisfies every
        # configured rule.
        directional_results = (buy_result, sell_result)
        if cfg.confirmation.select_highest_passing_direction:
            approved_results = [
                result for result in directional_results
                if self._direction_passes_filters(result, df_setup, df_htf, cfg)
            ]
        else:
            # Preserve the existing one-winner behavior unless a profile opts
            # into the corrected MTF-aware direction selection.
            raw_leader = max(directional_results, key=lambda result: result.total_score)
            approved_results = (
                [raw_leader]
                if self._direction_passes_filters(raw_leader, df_setup, df_htf, cfg)
                else []
            )
        if approved_results:
            best_result = max(approved_results, key=lambda result: result.total_score)
        else:
            # Preserve useful rejection telemetry when neither side qualifies.
            best_result = max(directional_results, key=lambda result: result.total_score)

        signal = self.build_signal(symbol, timeframe, df_entry, best_result, created_at=created_at) if approved_results else None
        if signal is None:
            logger.debug(
                f"[{symbol} {timeframe}] Score rejected: Total={best_result.total_score}/{cfg.score.minimum}, "
                f"Struct={best_result.structure.score}/{cfg.score.min_structure}, Trend={best_result.trend.score}/{cfg.score.min_trend}, "
                f"Vol={best_result.volume.score}/{getattr(cfg.score, 'min_volume', 0.0)}, "
                f"Setup={best_result.trend.details.get('setup_trend_score', 'N/A')}"
            )
            return best_result, None

        logger.info(
            f"[{symbol} {timeframe}] 🎯 SIGNAL GENERATED: {best_result.direction.value} "
            f"Score: {best_result.total_score:.1f}/100 ({best_result.classification.value})"
        )
        return best_result, signal

    def _direction_passes_filters(
        self,
        result: ScoreResult,
        df_setup: Optional[pd.DataFrame],
        df_htf: Optional[pd.DataFrame],
        cfg: AppConfig,
    ) -> bool:
        """Evaluate component and timeframe gates independently for one side."""
        min_struct = cfg.score.min_structure
        min_trend = cfg.score.min_trend
        min_vol = getattr(cfg.score, "min_volume", 0.0)
        min_total = cfg.score.minimum
        struct_ok = result.structure.score >= min_struct
        trend_ok = result.trend.score >= min_trend
        vol_ok = result.volume.score >= min_vol
        total_ok = result.total_score >= min_total

        # A fast entry timeframe is useful only when its setup timeframe is
        # known to agree.  This is an explicit opt-in: the legacy baseline
        # keeps its original behaviour and has no hidden MTF requirement.
        setup_ok = True
        confirmation = cfg.confirmation
        if confirmation.require_setup_trend_alignment:
            if df_setup is None or df_setup.empty:
                setup_ok = False
                result.trend.details["setup_trend_score"] = None
                result.trend.details["setup_trend_aligned"] = False
            else:
                setup_trend = self.trend_strat.calculate_score(
                    df=df_setup,
                    direction=result.direction,
                    adx_min=cfg.trend.adx_min,
                    adx_strong=cfg.trend.adx_strong,
                )
                result.trend.details["setup_trend_score"] = setup_trend.score
                result.trend.details["setup_trend_aligned"] = (
                    setup_trend.score >= confirmation.setup_min_trend_score
                )
                setup_ok = result.trend.details["setup_trend_aligned"]

        higher_ok = True
        if confirmation.require_higher_trend_alignment:
            if df_htf is None or df_htf.empty:
                higher_ok = False
                result.trend.details["higher_trend_score"] = None
                result.trend.details["higher_trend_aligned"] = False
            else:
                higher_trend = self.trend_strat.calculate_score(
                    df=df_htf,
                    direction=result.direction,
                    adx_min=cfg.trend.adx_min,
                    adx_strong=cfg.trend.adx_strong,
                )
                result.trend.details["higher_trend_score"] = higher_trend.score
                result.trend.details["higher_trend_aligned"] = (
                    higher_trend.score >= confirmation.higher_min_trend_score
                )
                higher_ok = result.trend.details["higher_trend_aligned"]

        result.trend.details["directional_filters_passed"] = bool(
            struct_ok and trend_ok and vol_ok and total_ok and setup_ok and higher_ok
        )
        return result.trend.details["directional_filters_passed"]
