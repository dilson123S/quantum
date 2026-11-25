"""Typed configuration loader using Pydantic and PyYAML."""
from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml
import os
from dotenv import load_dotenv

from core.constants import (
    RiskMode, TrailingMode, VolumeMode, EntryMode, BotMode, TIMEFRAME_MAP
)

# Load environment variables
load_dotenv()


class BotConfig(BaseModel):
    name: str = "QuantTradingBot"
    magic_number: int = 26081901
    # A missing or malformed configuration must observe the market, never
    # route an order.  Demo/live both require explicit opt-in.
    mode: BotMode = BotMode.PAPER
    allow_live_trading: bool = False
    # Populated by ``load_config`` from the immutable validation profile.
    # ``None`` preserves the small unit-test adapter API; a loaded profile is
    # always explicitly True or False and therefore fail-closed.
    execution_authorized: Optional[bool] = None
    max_clock_skew_seconds: float = 5.0
    # Profiles must not share an execution journal.  The dashboard relies on
    # this separation to display and audit the intraday experiment correctly.
    journal_path: str = "data/trading_bot.db"
    comment_tag: Optional[str] = None


class MarketConfig(BaseModel):
    symbols: List[str] = Field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"])


class TimeframeConfig(BaseModel):
    higher: str = "H4"
    setup: str = "H1"
    entry: str = "M15"
    daily: str = "D1"
    weekly: str = "W1"
    monthly: str = "MN1"
    confirmation: str = "M30"
    gap: str = "H2"

    @field_validator("higher", "setup", "entry", "daily", "weekly", "monthly", "confirmation", "gap")
    @classmethod
    def validate_timeframe(cls, v: str) -> str:
        if v not in TIMEFRAME_MAP:
            raise ValueError(f"Invalid timeframe '{v}'. Must be one of {list(TIMEFRAME_MAP.keys())}")
        return v


class ScoreConfig(BaseModel):
    minimum: float = 75.0
    min_structure: float = 18.0
    min_trend: float = 15.0
    min_volume: float = 0.0
    structure_weight: float = 30.0
    trend_weight: float = 25.0
    momentum_weight: float = 15.0
    volume_weight: float = 15.0
    volatility_weight: float = 15.0


class ConfirmationConfig(BaseModel):
    """Optional multi-timeframe confirmation for a signal.

    It is deliberately disabled for the existing baseline so enabling a new
    profile cannot silently change that strategy.  The intraday profile uses
    it to require that the M15 trend agrees with an M5 entry candidate.
    """
    require_setup_trend_alignment: bool = False
    setup_min_trend_score: float = 0.0
    require_higher_trend_alignment: bool = False
    higher_min_trend_score: float = 0.0
    select_highest_passing_direction: bool = False


class TelemetryConfig(BaseModel):
    """Observation settings; these never force a trade."""
    log_rejected_score_candidates: bool = False
    target_daily_trades: int = 0


class DecisionContextConfig(BaseModel):
    """Observational context settings; never an entry permission."""
    enabled: bool = True
    shadow_mode: bool = True
    history_bars: int = 650
    equal_level_tolerance_atr: float = 0.15
    fib_zone_atr: float = 0.15
    smc_max_age_bars: int = 40
    smc_min_gap_atr: float = 0.10
    sweep_min_penetration_atr: float = 0.05


class RegimeFilterConfig(BaseModel):
    """Point-in-time regime policy; shadow is mandatory by default."""
    enabled: bool = False
    shadow_mode: bool = True
    min_adx: float = 20.0
    min_bb_width_ratio: float = 0.90
    max_range_bb_width_ratio: float = 1.05
    block_ranging: bool = True
    block_low_volatility: bool = True
    # Empty preserves the broad legacy policy.  A research profile may use a
    # closed allow-list to test one strategy per market regime.
    allowed_regimes: List[str] = Field(default_factory=list)
    # Optional directional allow-list inside a regime, e.g. TRANSITIONAL: [BUY].
    # Empty preserves existing behavior.
    allowed_directions_by_regime: Dict[str, List[str]] = Field(default_factory=dict)


class SMCFilterConfig(BaseModel):
    """Optional SMC confluence policy, retained in shadow until OOS tested."""
    enabled: bool = False
    shadow_mode: bool = True
    require_sweep: bool = False
    require_fvg_or_order_block: bool = False


class SweepReversalConfig(BaseModel):
    """Exploitation strategy for institutional liquidity sweeps / false breakouts."""
    enabled: bool = False
    min_penetration_atr: float = 0.05
    min_rr: float = 2.0
    sl_buffer_atr: float = 0.25
    target_opposite_level: bool = True
    require_ml_approval: bool = True
    require_macro_trend_alignment: bool = True
    allow_exhaustion_reversals: bool = True
    max_reversal_adx: float = 35.0


class AlchemistConfig(BaseModel):
    """Mechanical MSNR/SMC/liquidity/CRT/ICT entry model; inactive by default."""
    enabled: bool = False
    entry_model: Literal["crt_m15", "msnr_storyline_h1"] = "crt_m15"
    london_sweep_enabled: bool = True
    new_york_crt_enabled: bool = True
    h4_m30_enabled: bool = True
    h4_h2_gap_enabled: bool = True
    min_confluences: int = 2
    min_confluences_after_loss: int = 3
    max_poi_distance_atr: float = 0.50
    stop_buffer_atr: float = 0.10
    minimum_rr: float = 1.50
    target_rr: float = 2.00
    h1_break_lookback: int = 5
    allow_higher_storyline_fallback: bool = False
    require_fresh_msnr: bool = False
    london_open_hour: int = 8
    london_close_hour: int = 10
    new_york_crt_hour: int = 9
    avoid_mondays: bool = True
    avoid_asian_session: bool = True
    require_htf_alignment: bool = False
    require_htf_poi: bool = False
    require_m15_structure_shift: bool = False
    require_m15_entry_pattern: bool = False
    max_stop_atr: float = 2.50


class AsianBreakdownConfig(BaseModel):
    """Exploitation strategy for Asian session range expansions/breakdowns aligned with macro trend."""
    enabled: bool = True
    min_rr: float = 1.8
    sl_buffer_atr: float = 0.25
    target_daily_level: bool = True
    require_ml_approval: bool = False


class LiquidityGuardConfig(BaseModel):
    """Protects against stop-hunt traps at key daily levels (PDH/PDL).

    Fix 1: pdx_proximity_atr — block entry when price is within this ATR
           distance of PDH/PDL and no sweep has been confirmed yet.
    Fix 2: block_asia_daily_breakouts — block SELL toward PDL or BUY toward
           PDH that originates during the Asia session (low institutional flow).
    Fix 3: require_smc_in_zone — only count FVG/OB as real confluencia when
           price is actually inside the zone (in_zone=True).
    Fix 4: max_pending_hours — cancel pending breakout orders that have been
           waiting unfilled for more than this many hours.
    """
    enabled: bool = True
    pdx_proximity_atr: float = 0.6
    block_asia_daily_breakouts: bool = True
    require_smc_in_zone: bool = True
    max_pending_hours: float = 2.0


class ValidationConfig(BaseModel):
    """Evidence status exposed by read-only operational dashboards."""
    status: str = "UNVALIDATED"
    summary: str = "No validation result has been recorded."
    experiment_id: Optional[str] = None
    exploratory_trades: int = 0
    exploratory_total_r: Optional[float] = None


class StructureConfig(BaseModel):
    swing_lookback: int = 3
    swing_confirmation: int = 2
    bos_min_distance_atr: float = 0.10
    bos_close_confirmation: bool = True
    use_choch: bool = True
    use_bos: bool = True
    max_swing_age: int = 100


class TrendConfig(BaseModel):
    ema_fast: int = 20
    ema_medium: int = 50
    ema_slow: int = 200
    adx_period: int = 14
    adx_min: float = 20.0
    adx_strong: float = 25.0


class MomentumConfig(BaseModel):
    rsi_period: int = 14
    rsi_bull_min: float = 50.0
    rsi_bull_strong: float = 55.0
    rsi_bear_max: float = 50.0
    rsi_bear_strong: float = 45.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9


class VolumeConfig(BaseModel):
    period: int = 20
    min_ratio: float = 1.20
    strong_ratio: float = 1.50
    use_obv: bool = True
    volume_mode: VolumeMode = VolumeMode.TICK


class VolatilityConfig(BaseModel):
    atr_period: int = 14
    atr_avg_period: int = 50
    min_atr_ratio: float = 0.80
    max_atr_ratio: float = 2.50
    sl_multiplier: float = 1.50
    sl_buffer_atr: float = 0.15
    max_atr_distance_multiplier: float = 3.5


class RiskConfig(BaseModel):
    mode: RiskMode = RiskMode.PERCENT
    risk_percent: float = 1.0
    risk_money: float = 20.0
    max_daily_loss: float = 2.0
    max_drawdown: float = 10.0
    max_consecutive_losses: int = 0
    max_open_positions: int = 3
    max_positions_per_symbol: int = 1
    max_correlated_positions: int = 2
    max_risk_per_trade: float = 1.0
    max_correlation: float = 0.80
    max_portfolio_risk_percent: float = 3.0
    max_daily_trades: int = 10
    min_trade_volume: float = 0.01
    risk_by_score: Dict[str, float] = Field(default_factory=dict)
    min_component_scores: Dict[str, float] = Field(default_factory=dict)
    correlation_groups: Dict[str, List[str]] = Field(default_factory=dict)
    consecutive_loss_risk_reduction: Dict[int, float] = Field(default_factory=dict)


class TradeConfig(BaseModel):
    risk_reward: float = 2.0
    break_even_r: float = 1.0
    partial_close: bool = True
    partial_r: float = 1.0
    partial_percent: float = 50.0
    trailing: TrailingMode = TrailingMode.HYBRID
    trailing_activation_r: float = 1.0
    trailing_struct_buffer_atr: float = 0.3
    min_trailing_distance_atr: float = 0.8
    trailing_atr_multiplier: float = 2.0
    entry_mode: EntryMode = EntryMode.BREAKOUT
    breakout_buffer_points: float = 5.0
    breakout_expiry_bars: int = 4
    pullback_expiry_bars: int = 8
    update_pending_on_new_signal: bool = False
    stagnation_exit_bars: int = 0
    stagnation_min_progress_r: float = 0.4
    min_risk_reward: float = 1.0


class FilterConfig(BaseModel):
    max_spread: bool = True
    session_filter: bool = True
    news_filter: bool = False
    correlation_filter: bool = True
    trading_start: str = "07:00"
    trading_end: str = "17:00"
    timezone: str = "UTC"
    max_spread_atr_percent: float = 10.0
    news_blackout_before: int = 30
    news_blackout_after: int = 30
    excluded_hours: Optional[List[int]] = None


class SymbolConfig(BaseModel):
    volume_mode: Optional[str] = "tick"
    max_spread_points: Optional[float] = 20.0
    session_start: Optional[str] = "07:00"
    session_end: Optional[str] = "17:00"
    excluded_hours: Optional[List[int]] = None


class MLConfig(BaseModel):
    enabled: bool = True
    shadow_mode: bool = True
    # An accepted model is not enough permission to let it block trades.
    # This separate opt-in remains false until its evidence is reviewed.
    allow_active_filter: bool = False
    model_path: str = "data/ml_model.joblib"
    min_confidence: float = 0.65
    retrain_weeks: int = 4
    training_months: int = 6


class JevConfig(BaseModel):
    """Optional System 1 review (Laya, Kev, NanoJev, or Jev); shadow-only and disabled by default."""
    enabled: bool = False
    provider: str = "laya"  # laya, kev, nanojev
    shadow_mode: bool = True
    model: str = "laya-base"
    timeout_seconds: float = Field(default=3.0, ge=0.5, le=15.0)
    device: str = "cuda"


class KronosConfig(BaseModel):
    """Configuration for Kronos K-line Foundation Model Oracle."""
    enabled: bool = False
    model_name: str = "NeoQuasar/Kronos-small"
    tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-base"
    device: str = "cpu"
    lookback_bars: int = 200
    forecast_horizon: int = 12
    temperature: float = 0.7
    top_p: float = 0.9
    sample_count: int = 2
    min_confidence: float = 0.60
    shadow_mode: bool = True


class BacktestConfig(BaseModel):
    """Explicit, conservative assumptions for deterministic simulation.

    These costs are deliberately visible in versioned configuration. They are
    not optimisation knobs: alter them only from broker statements and record
    a new experiment id before re-running any evaluation.
    """
    commission_per_lot_round_turn: float = 7.0
    slippage_points: float = 1.0
    gap_slippage_points: float = 2.0
    intra_bar_policy: str = "stop_first"


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_prefix="BOT_", env_nested_delimiter="__")

    bot: BotConfig = Field(default_factory=BotConfig)
    market: MarketConfig = Field(default_factory=MarketConfig)
    timeframes: TimeframeConfig = Field(default_factory=TimeframeConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    confirmation: ConfirmationConfig = Field(default_factory=ConfirmationConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    decision_context: DecisionContextConfig = Field(default_factory=DecisionContextConfig)
    regime_filter: RegimeFilterConfig = Field(default_factory=RegimeFilterConfig)
    smc_filter: SMCFilterConfig = Field(default_factory=SMCFilterConfig)
    sweep_reversal: SweepReversalConfig = Field(default_factory=SweepReversalConfig)
    alchemist: AlchemistConfig = Field(default_factory=AlchemistConfig)
    asian_breakdown: AsianBreakdownConfig = Field(default_factory=AsianBreakdownConfig)
    liquidity_guard: LiquidityGuardConfig = Field(default_factory=LiquidityGuardConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    trend: TrendConfig = Field(default_factory=TrendConfig)
    momentum: MomentumConfig = Field(default_factory=MomentumConfig)
    volume: VolumeConfig = Field(default_factory=VolumeConfig)
    volatility: VolatilityConfig = Field(default_factory=VolatilityConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    trade: TradeConfig = Field(default_factory=TradeConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    ml: MLConfig = Field(default_factory=MLConfig)
    jev: JevConfig = Field(default_factory=JevConfig)
    kronos: KronosConfig = Field(default_factory=KronosConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    symbols: Dict[str, SymbolConfig] = Field(default_factory=dict)


def load_config(config_dir: str = "config") -> AppConfig:
    """Load configuration from settings.yaml, symbols.yaml, and risk.yaml."""
    config_dict: Dict[str, Any] = {}

    # 1. Load settings.yaml
    settings_path = os.path.join(config_dir, "settings.yaml")
    if os.path.exists(settings_path):
        with open(settings_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data:
                config_dict.update(data)

    # 2. Load symbols.yaml
    symbols_path = os.path.join(config_dir, "symbols.yaml")
    if os.path.exists(symbols_path):
        with open(symbols_path, "r", encoding="utf-8") as f:
            sym_data = yaml.safe_load(f)
            if sym_data:
                config_dict["symbols"] = {
                    sym: SymbolConfig(**cfg) for sym, cfg in sym_data.items() if isinstance(cfg, dict)
                }

    # 3. Load risk.yaml
    risk_path = os.path.join(config_dir, "risk.yaml")
    if os.path.exists(risk_path):
        with open(risk_path, "r", encoding="utf-8") as f:
            risk_data = yaml.safe_load(f)
            if risk_data and "risk" in risk_data:
                # Risk policy is deliberately one typed object.  Keeping the
                # companion maps outside it silently discarded them under
                # Pydantic's ``extra=ignore`` behaviour.
                config_dict["risk"] = {
                    **risk_data["risk"],
                    **{key: value for key, value in risk_data.items() if key != "risk"},
                }

    config = AppConfig(**config_dict)
    # Legacy risk.yaml component floors are only applied as defaults if not explicitly
    # specified in settings.yaml score section, and never exceed the configured weight ceiling.
    score_dict = config_dict.get("score", {})
    if "min_structure" not in score_dict and "structure" in config.risk.min_component_scores:
        config.score.min_structure = min(float(config.risk.min_component_scores["structure"]), config.score.structure_weight)
    if "min_trend" not in score_dict and "trend" in config.risk.min_component_scores:
        config.score.min_trend = min(float(config.risk.min_component_scores["trend"]), config.score.trend_weight)
    apply_execution_authorization(config)
    return config


def apply_execution_authorization(config: AppConfig) -> None:
    """Derive the one execution permission from mode and validation evidence."""
    status = config.validation.status.strip().upper()
    mode = config.bot.mode
    if mode == BotMode.DEMO:
        config.bot.execution_authorized = status in {"APPROVED", "DEMO_APPROVED"}
    elif mode == BotMode.LIVE:
        config.bot.execution_authorized = status == "LIVE_APPROVED"
    else:
        config.bot.execution_authorized = False
