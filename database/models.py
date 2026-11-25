"""SQLAlchemy ORM models for trade persistence."""
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, JSON, Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone
import uuid


class Base(DeclarativeBase):
    pass


class TradeDB(Base):
    """Stores every trade executed by the bot."""
    __tablename__ = 'trades'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    signal_id = Column(String(64), nullable=False, index=True)
    
    # Core info
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    direction = Column(String(4), nullable=False)  # BUY / SELL
    state = Column(String(20), nullable=False, default='SIGNAL')
    
    # Prices
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=False)
    tp_price = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    entered_at = Column(DateTime, nullable=True)
    exited_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    
    # Scores
    total_score = Column(Float, nullable=False)
    structure_score = Column(Float, nullable=False)
    trend_score = Column(Float, nullable=False)
    momentum_score = Column(Float, nullable=False)
    volume_score = Column(Float, nullable=False)
    volatility_score = Column(Float, nullable=False)
    
    # Market context at entry
    spread_at_entry = Column(Float, nullable=True)
    atr_value = Column(Float, nullable=True)
    rsi_value = Column(Float, nullable=True)
    adx_value = Column(Float, nullable=True)
    macd_value = Column(Float, nullable=True)
    market_regime = Column(String(20), nullable=True)
    
    # Risk
    risk_percent = Column(Float, nullable=False)
    risk_money = Column(Float, nullable=False)
    
    # Results
    profit = Column(Float, nullable=True)
    profit_r = Column(Float, nullable=True)  # Profit in R-multiples
    
    # MT5 info
    ticket = Column(Integer, nullable=True)
    magic_number = Column(Integer, nullable=True)
    
    # Features snapshot (JSON blob)
    features_at_entry = Column(JSON, nullable=True)
    
    # Comment
    comment = Column(Text, nullable=True)


class DailyStatsDB(Base):
    """Daily trading statistics."""
    __tablename__ = 'daily_stats'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), unique=True, nullable=False, index=True)  # YYYY-MM-DD
    
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    
    gross_profit = Column(Float, default=0.0)
    gross_loss = Column(Float, default=0.0)
    net_profit = Column(Float, default=0.0)
    
    max_drawdown = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)
    
    start_balance = Column(Float, nullable=True)
    end_balance = Column(Float, nullable=True)
    
    consecutive_losses = Column(Integer, default=0)
    max_consecutive_losses = Column(Integer, default=0)


class RuntimeStateDB(Base):
    """Small durable key/value state for safety invariants across restarts."""
    __tablename__ = 'runtime_state'

    key = Column(String(80), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SignalLogDB(Base):
    """Log of all signals detected, including rejected ones."""
    __tablename__ = 'signal_log'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(String(64), unique=True, nullable=False, index=True)
    
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    direction = Column(String(4), nullable=False)
    
    total_score = Column(Float, nullable=False)
    structure_score = Column(Float, nullable=False)
    trend_score = Column(Float, nullable=False)
    momentum_score = Column(Float, nullable=False)
    volume_score = Column(Float, nullable=False)
    volatility_score = Column(Float, nullable=False)
    
    classification = Column(String(20), nullable=False)
    
    was_traded = Column(Boolean, default=False)
    rejection_reasons = Column(Text, nullable=True)  # Comma-separated reasons
    
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class DecisionContextDB(Base):
    """Immutable closed-bar context attached to one observed signal."""
    __tablename__ = 'decision_context'

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(String(64), unique=True, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    direction = Column(String(4), nullable=False)
    regime = Column(String(24), nullable=True)
    session = Column(String(24), nullable=True)
    context = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class PatternMemoryDB(Base):
    """Stores statistical empirical patterns discovered and tracked by the Neural Memory Bank."""
    __tablename__ = 'pattern_memory'

    pattern_id = Column(String(64), primary_key=True)
    name = Column(String(100), nullable=False)
    category = Column(String(30), nullable=False)  # "PULLBACK", "TIMING", "TRAILING", "SESSION"
    version = Column(String(10), default="v1.0")
    lifecycle_state = Column(String(20), default="SHADOW")  # "SHADOW", "CANDIDATE", "VALIDATING", "ACTIVE", "REJECTED", "ROLLBACK"
    is_negative_rule = Column(Boolean, default=False)  # True for anti-patterns (vetos)

    observations = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    breakevens = Column(Integer, default=0)
    losses = Column(Integer, default=0)

    # Rigorous statistical transparency
    win_rate = Column(Float, default=0.0)      # Strict: wins / total * 100
    be_rate = Column(Float, default=0.0)       # Strict: breakevens / total * 100
    loss_rate = Column(Float, default=0.0)     # Strict: losses / total * 100
    outcome_score = Column(Float, default=0.0) # Weighted effectiveness: (W + 0.5*BE) / total * 100
    success_rate = Column(Float, default=0.0)  # Preserved for backward compatibility

    confidence = Column(String(20), default="EN_OBSERVACION")  # "EN_OBSERVACION", "BAJA", "MEDIA", "ALTA"
    min_samples_required = Column(Integer, default=15)
    is_active = Column(Boolean, default=False)
    consecutive_losses = Column(Integer, default=0)  # Trigger for automatic rollback

    # Empirical validation & contrast group
    lift_ratio = Column(Float, default=1.0)
    contrast_matrix = Column(JSON, nullable=True)     # {"tp": int, "fp": int, "fn": int, "tn": int}
    baseline_metrics = Column(JSON, nullable=True)    # Prior metrics before adjustment
    treatment_metrics = Column(JSON, nullable=True)   # Metrics with rule applied
    regime_matrix = Column(JSON, nullable=True)       # ATR and session dynamic ranges
    validation_method = Column(String(100), nullable=True)
    rejection_reason_audit = Column(Text, nullable=True)
    ci_lower = Column(Float, default=0.0)
    ci_upper = Column(Float, default=0.0)
    ci_display = Column(String(30), default="[0.0% – 0.0%]")

    # Primary Metric: Expectancy in R-multiples
    avg_win_r = Column(Float, default=0.0)
    avg_loss_r = Column(Float, default=0.0)
    expectancy_r = Column(Float, default=0.0)

    modifications = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    last_observation = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
