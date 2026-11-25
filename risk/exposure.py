"""Exposure and position count tracking across symbols."""
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PositionData:
    """Structure for tracking a single position."""
    ticket: int
    symbol: str
    volume: float


class ExposureManager:
    """Manages risk exposure and limits across all symbols and open positions."""

    def __init__(self, max_open_positions: int = 8, max_positions_per_symbol: int = 4):
        self.max_open_positions = max_open_positions
        self.max_positions_per_symbol = max_positions_per_symbol
        self.active_positions: Dict[int, PositionData] = {}

    def update_positions(self, positions: List[PositionData]):
        self.active_positions = {pos.ticket: pos for pos in positions}

    @property
    def total_open_positions(self) -> int:
        return len(self.active_positions)

    @property
    def total_open_volume(self) -> float:
        return sum(pos.volume for pos in self.active_positions.values())

    def get_positions_for_symbol(self, symbol: str) -> int:
        return sum(1 for pos in self.active_positions.values() if pos.symbol == symbol)

    def get_volume_for_symbol(self, symbol: str) -> float:
        return sum(pos.volume for pos in self.active_positions.values() if pos.symbol == symbol)

    def can_open_new_position(self, symbol: str) -> bool:
        if self.total_open_positions >= self.max_open_positions:
            return False
        if self.get_positions_for_symbol(symbol) >= self.max_positions_per_symbol:
            return False
        return True

    def can_open_position(self, symbol: str, current_open_symbols: List[str]) -> bool:
        """Helper to check if a position can be opened given a list of currently open symbol strings."""
        if len(current_open_symbols) >= self.max_open_positions:
            return False
        if current_open_symbols.count(symbol) >= self.max_positions_per_symbol:
            return False
        return True

