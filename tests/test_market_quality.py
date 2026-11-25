"""Regression tests for closed-bar SMC and market-regime policy."""
from datetime import datetime, timezone

import pandas as pd

from core.config_loader import AppConfig, RegimeFilterConfig, SMCFilterConfig
from core.constants import Direction, StructureBreakType, SwingClassification, SwingType
from core.models import StructureAnalysis, StructureBreak, SwingPoint
from filters.market_quality_filter import MarketQualityFilter
from strategy.decision_context import build_decision_context
from strategy.smc import latest_fair_value_gap, latest_order_block


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "time": pd.date_range("2026-01-02 08:00", periods=5, freq="15min", tz="UTC"),
        "open": [99.7, 100.0, 101.2, 101.3, 101.0],
        "high": [100.0, 100.7, 102.0, 101.7, 101.8],
        "low": [99.0, 99.6, 101.0, 100.5, 100.8],
        "close": [99.5, 100.4, 101.5, 100.7, 101.5],
        "atr": [1.0] * 5,
        "atr_ratio": [1.0] * 5,
        "adx": [10.0] * 5,
        "bb_width_ratio": [0.8] * 5,
    })


def _structure() -> StructureAnalysis:
    broken = SwingPoint(
        index=0, price=100.7, swing_type=SwingType.SWING_HIGH,
        classification=SwingClassification.HH, bar_index=1,
    )
    return StructureAnalysis(
        swings=[broken], last_swing_high=broken,
        bos_list=[StructureBreak(
            break_type=StructureBreakType.BOS, direction=Direction.BUY,
            price=100.7, bar_index=4, swing_broken=broken,
        )], structure_direction=Direction.BUY, last_bar_index=4,
    )


def test_fvg_and_order_block_are_closed_bar_context_not_predictions():
    frame, structure = _frame(), _structure()
    fvg = latest_fair_value_gap(frame, max_age_bars=10, min_gap_atr=0.1)
    order_block = latest_order_block(frame, structure, max_age_bars=10)

    assert fvg["available"] is True
    assert fvg["direction"] == "BUY"
    assert (fvg["lower"], fvg["upper"]) == (100.0, 101.0)
    assert order_block["available"] is True
    assert order_block["direction"] == "BUY"


def test_context_records_sweep_smc_and_bollinger_aware_regime():
    context = build_decision_context(
        _frame(), _structure(), Direction.BUY,
        min_atr_ratio=0.8, max_atr_ratio=2.5, adx_min=20,
    )
    assert context["regime"]["label"] == "RANGING"
    assert context["regime"]["bb_width_ratio"] == 0.8
    assert context["smc"]["fair_value_gap"]["available"] is True
    assert context["smc"]["order_block"]["available"] is True
    assert context["candidate_direction"] == "BUY"


def test_market_quality_filter_never_enforces_a_shadow_rule():
    context = {"regime": {"label": "RANGING"}, "smc": {}}
    shadow_config = AppConfig(regime_filter=RegimeFilterConfig(enabled=True, shadow_mode=True))
    shadow = MarketQualityFilter(shadow_config).assess(context, Direction.BUY)
    assert shadow.approved is True
    assert shadow.shadow_reasons == ["REGIME_RANGING"]

    active_config = AppConfig(regime_filter=RegimeFilterConfig(enabled=True, shadow_mode=False))
    active = MarketQualityFilter(active_config).assess(context, Direction.BUY)
    assert active.approved is False
    assert active.active_reasons == ["REGIME_RANGING"]


def test_regime_allow_list_can_make_a_trend_only_profile_explicit():
    config = AppConfig(regime_filter=RegimeFilterConfig(
        enabled=True, shadow_mode=False, allowed_regimes=["TRENDING"],
    ))
    rejected = MarketQualityFilter(config).assess({"regime": {"label": "TRANSITIONAL"}, "smc": {}}, Direction.BUY)
    accepted = MarketQualityFilter(config).assess({"regime": {"label": "TRENDING"}, "smc": {}}, Direction.BUY)

    assert rejected.active_reasons == ["REGIME_TRANSITIONAL_NOT_ALLOWED"]
    assert accepted.approved is True


def test_smc_filter_requires_only_the_explicit_confluence_selected():
    context = {"regime": {"label": "TRENDING"}, "smc": {"sweep_aligned": True, "fvg_aligned": False, "order_block_aligned": False}}
    config = AppConfig(smc_filter=SMCFilterConfig(
        enabled=True, shadow_mode=False, require_sweep=True, require_fvg_or_order_block=True,
    ))
    assessment = MarketQualityFilter(config).assess(context, Direction.BUY)
    # Reason code updated to reflect that in_zone is now required for real SMC confluence
    assert assessment.active_reasons == ["SMC_NO_BUY_FVG_OR_OB_IN_ZONE"]
