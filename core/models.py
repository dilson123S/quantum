from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from uuid import UUID, uuid4
import hashlib

from core.constants import (
    SwingType, SwingClassification, StructureBreakType, Direction,
    SignalClassification, TradeState, MarketRegime
)


class SwingPoint(BaseModel):
    index: int
    price: float
    swing_type: SwingType
    classification: SwingClassification
    bar_index: int


class StructureBreak(BaseModel):
    break_type: StructureBreakType
    direction: Direction
    price: float
    bar_index: int
    swing_broken: SwingPoint


class StructureAnalysis(BaseModel):
    swings: List[SwingPoint] = Field(default_factory=list)
    bos_list: List[StructureBreak] = Field(default_factory=list)
    choch_list: List[StructureBreak] = Field(default_factory=list)
    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None
    structure_direction: Optional[Direction] = None
    # Index of the final closed bar supplied to the detector.  It makes the
    # configured swing-age rule testable instead of assuming every swing is
    # fresh merely because it is the latest item in a list.
    last_bar_index: int = -1


class ComponentScore(BaseModel):
    name: str
    score: float
    max_score: float
    details: Dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    direction: Direction
    total_score: float
    classification: SignalClassification
    structure: ComponentScore
    trend: ComponentScore
    momentum: ComponentScore
    volume: ComponentScore
    volatility: ComponentScore
    timestamp: datetime


class Signal(BaseModel):
    symbol: str
    timeframe: str
    direction: Direction
    score_result: ScoreResult
    created_at: datetime
    entry_price: Optional[float] = None
    
    @property
    def signal_id(self) -> str:
        timestamp_iso = self.created_at.isoformat()
        raw_id = f"{self.symbol}_{self.timeframe}_{timestamp_iso}_{self.direction.value}"
        return hashlib.sha256(raw_id.encode()).hexdigest()[:16]


class TradeSetup(BaseModel):
    signal: Signal
    entry_price: float
    sl_price: float
    tp_price: float
    volume: float
    risk_percent: float
    risk_money: float
    risk_reward_ratio: float
    is_pending: bool = False
    expiration: Optional[datetime] = None
    invalidation_price: Optional[float] = None


class TradeRecord(BaseModel):
    model_config = ConfigDict(frozen=False)
    
    trade_id: UUID = Field(default_factory=uuid4)
    state: TradeState = TradeState.SIGNAL
    signal_id: str
    symbol: str
    timeframe: str
    direction: Direction
    
    # Prices
    entry_price: float
    exit_price: Optional[float] = None
    sl_price: float
    tp_price: float
    volume: float
    spread_at_entry: float
    
    # Timestamps
    created_at: datetime
    entered_at: Optional[datetime] = None
    exited_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    
    # Metrics
    risk_percent: float
    risk_money: float
    profit: Optional[float] = None
    profit_r: Optional[float] = None

    # Broker identifiers.  These are indispensable for reconciling process
    # restarts with the authoritative MT5 position/deal history.
    ticket: Optional[int] = None
    magic_number: Optional[int] = None
    comment: Optional[str] = None
    
    # Context
    market_regime: MarketRegime
    all_scores: ScoreResult
    features_at_entry: Dict[str, float] = Field(default_factory=dict)
    
    # Technical values
    atr_value: Optional[float] = None
    rsi_value: Optional[float] = None
    adx_value: Optional[float] = None
    macd_value: Optional[float] = None


class MarketSnapshot(BaseModel):
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    spread: float
    spread_points: float
    last_price: float
    tick_volume: float


class FilterResult(BaseModel):
    approved: bool
    reasons: List[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    success: bool
    ticket: Optional[int] = None
    retcode: Optional[int] = None
    message: str
    price: Optional[float] = None
    volume: Optional[float] = None
