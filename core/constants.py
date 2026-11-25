from enum import Enum, IntEnum
from datetime import datetime, timezone

HOLDOUT_START = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class TradeState(str, Enum):
    SIGNAL = "SIGNAL"
    VALIDATED = "VALIDATED"
    ORDER_PENDING = "ORDER_PENDING"
    OPEN = "OPEN"
    BREAK_EVEN = "BREAK_EVEN"
    PARTIAL = "PARTIAL"
    TRAILING = "TRAILING"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"

class SignalClassification(str, Enum):
    VERY_WEAK = "VERY_WEAK"       # 0-49
    WEAK = "WEAK"                 # 50-64
    MODERATE = "MODERATE"         # 65-74
    GOOD = "GOOD"                 # 75-84
    EXCELLENT = "EXCELLENT"       # 85-94
    EXCEPTIONAL = "EXCEPTIONAL"   # 95-100

    @classmethod
    def from_score(cls, score: float) -> 'SignalClassification':
        if score >= 95: return cls.EXCEPTIONAL
        if score >= 85: return cls.EXCELLENT
        if score >= 75: return cls.GOOD
        if score >= 65: return cls.MODERATE
        if score >= 50: return cls.WEAK
        return cls.VERY_WEAK
    
    @property
    def is_tradeable(self) -> bool:
        return self in (self.GOOD, self.EXCELLENT, self.EXCEPTIONAL)

class RiskMode(str, Enum):
    PERCENT = "percent"
    MONEY = "money"

class TrailingMode(str, Enum):
    ATR = "atr"
    STRUCTURE = "structure"
    HYBRID = "hybrid"
    OFF = "off"

class VolumeMode(str, Enum):
    TICK = "tick"
    REAL = "real"

class EntryMode(str, Enum):
    BREAKOUT = "breakout"
    PULLBACK = "pullback"

class BotMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"

class MarketRegime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"

class SwingType(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"

class SwingClassification(str, Enum):
    HH = "HH"  # Higher High
    HL = "HL"  # Higher Low
    LH = "LH"  # Lower High
    LL = "LL"  # Lower Low
    UNCLASSIFIED = "UNCLASSIFIED"

class StructureBreakType(str, Enum):
    BOS = "BOS"    # Break of Structure
    CHOCH = "CHOCH" # Change of Character / MSS

class DrawdownState(str, Enum):
    NORMAL = "NORMAL"
    REDUCED_RISK = "REDUCED_RISK"
    STOPPED = "STOPPED"

# Timeframe mapping - string names to MT5 constants
# We store the integer values so this module can work without MT5 imported
TIMEFRAME_MAP = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 16385,
    "H2": 16386,
    "H3": 16387,
    "H4": 16388,
    "H6": 16390,
    "H8": 16392,
    "H12": 16396,
    "D1": 16408,
    "W1": 32769,
    "MN1": 49153,
}

# Trade return codes
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_INVALID = 10013
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_INVALID_FILL = 10030

# Retryable return codes
RETRYABLE_RETCODES = {TRADE_RETCODE_REQUOTE, 10012}  # requote, price off

# Magic number
DEFAULT_MAGIC_NUMBER = 26081901

# Score weights
SCORE_WEIGHTS = {
    "structure": 30,
    "trend": 25,
    "momentum": 15,
    "volume": 15,
    "volatility": 15,
}
