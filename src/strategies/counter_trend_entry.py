"""
Shared signal models and position manager for the MTF PRO strategy.
"""

import logging
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional, Dict
from enum import Enum
from datetime import datetime


class EntryType(Enum):
    EXTREME = "extreme"
    ABOVE_EXTREME = "above_extreme"
    BREAKOUT = "breakout"


class SignalDirection(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class EntrySignal:
    """Represents an entry signal."""
    timestamp: datetime
    symbol: str
    direction: SignalDirection
    entry_type: EntryType
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float       # 0–1
    rr_ratio: float
    timeframe: str
    phase: str
    criteria_met: List[str]
    poi_zone: Optional[Dict] = None
    bos_events: Optional[List[Dict]] = None
    coc_event: Optional[Dict] = None

    @property
    def risk(self) -> float:
        if self.direction == SignalDirection.LONG:
            return self.entry_price - self.stop_loss
        return self.stop_loss - self.entry_price

    @property
    def reward(self) -> float:
        if self.direction == SignalDirection.LONG:
            return self.take_profit - self.entry_price
        return self.entry_price - self.take_profit

    def to_dict(self) -> dict:
        return {
            'timestamp':    str(self.timestamp),
            'symbol':       self.symbol,
            'direction':    self.direction.value,
            'entry_type':   self.entry_type.value,
            'entry_price':  self.entry_price,
            'stop_loss':    self.stop_loss,
            'take_profit':  self.take_profit,
            'confidence':   self.confidence,
            'rr_ratio':     self.rr_ratio,
            'timeframe':    self.timeframe,
            'phase':        self.phase,
            'criteria_met': self.criteria_met,
            'risk':         self.risk,
            'reward':       self.reward,
        }


class ProTrendPositionManager:
    """
    Manages dual-TP exit logic for MTF-PRO positions.

    On every bar call update() to:
    - Close TP1 volume at first target and move SL to breakeven.
    - Close TP2 volume (remainder) at second target.
    """

    def __init__(self, trade_executor, risk_manager):
        self.executor = trade_executor
        self.rm = risk_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, position, current_price: float,
               m1_df: pd.DataFrame = None) -> List[str]:
        """Check targets and act. Returns list of actions taken."""
        actions = []
        if not position.targets:
            return actions

        hit, idx, vol = self.rm.check_targets(position, current_price)
        if hit and vol > 0:
            pnl = self.executor.partial_close(position, vol, current_price, idx)
            actions.append(f"T{idx+1} hit: closed {vol:.2f} lots, PnL={pnl:.2f}")
            self.logger.info(actions[-1])
            if idx == 0:
                self.rm.move_stop_to_breakeven(position)
                actions.append("SL moved to breakeven")

        return actions
