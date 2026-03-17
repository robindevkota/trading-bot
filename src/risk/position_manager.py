"""
Risk Management Module
Position sizing, risk calculations, and trade management
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from enum import Enum
from datetime import datetime

from ..strategies.counter_trend_entry import EntrySignal, SignalDirection


class OrderType(Enum):
    """Types of orders"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TradeStatus(Enum):
    """Status of a trade"""
    PENDING = "pending"
    OPEN = "open"
    PARTIAL = "partial"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    STOPPED_OUT = "stopped_out"
    TAKE_PROFIT = "take_profit"


@dataclass
class Position:
    """Represents an open position"""
    ticket: int
    symbol: str
    direction: SignalDirection
    entry_price: float
    current_price: float
    volume: float  # In lots (initial full size)
    stop_loss: float
    take_profit: float
    entry_time: datetime
    status: TradeStatus

    # Calculated fields
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    risk_amount: float = 0.0
    reward_amount: float = 0.0
    rr_ratio: float = 0.0
    current_rr: float = 0.0
    close_time: Optional[datetime] = None

    # Trailing stop
    trailing_stop_active: bool = False
    trailing_stop_distance: float = 0.0
    best_price: float = 0.0

    # Multi-target support (Pro Trend)
    # targets[i] = price level, target_volumes[i] = lots to close at that level
    targets: list = None           # [T1_price, T2_price, T3_price]
    target_volumes: list = None    # [T1_lots,  T2_lots,  T3_lots]
    targets_hit: list = None       # [False, False, False]
    remaining_volume: float = 0.0  # Lots still open after partial closes

    # Scale-in support (Pro Trend)
    scale_in_price: float = 0.0   # Price level to add second position tranche
    scale_in_volume: float = 0.0  # Lots to add
    scale_in_done: bool = False

    # Entry model tracking
    phase: str = ""

    def __post_init__(self):
        if self.targets is None:
            self.targets = []
        if self.target_volumes is None:
            self.target_volumes = []
        if self.targets_hit is None:
            self.targets_hit = []
        if self.remaining_volume == 0.0:
            self.remaining_volume = self.volume
    
    def calculate_pnl(self, current_price: float = None):
        """Calculate unrealized PnL"""
        if current_price is not None:
            self.current_price = current_price
        
        _jpy_pairs = {'USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'CADJPY', 'CHFJPY', 'NZDJPY'}
        _contract  = {'XAUUSD': 100, 'XAGUSD': 5000}.get(self.symbol, 100000)
        _divisor   = self.current_price if self.symbol in _jpy_pairs and self.current_price else 1.0
        if self.direction == SignalDirection.LONG:
            self.unrealized_pnl = (self.current_price - self.entry_price) * self.volume * _contract / _divisor
        else:
            self.unrealized_pnl = (self.entry_price - self.current_price) * self.volume * _contract / _divisor
        
        # Update current RR (guard against zero denominator after SL moved to BE)
        long_denom  = self.entry_price - self.stop_loss
        short_denom = self.stop_loss   - self.entry_price
        if self.direction == SignalDirection.LONG:
            self.current_rr = ((self.current_price - self.entry_price) / long_denom
                               if long_denom != 0 else 0.0)
        else:
            self.current_rr = ((self.entry_price - self.current_price) / short_denom
                               if short_denom != 0 else 0.0)
    
    @property
    def risk_reward_ratio(self) -> float:
        """Calculate risk-reward ratio"""
        if self.direction == SignalDirection.LONG:
            denom = self.entry_price - self.stop_loss
            return (self.take_profit - self.entry_price) / denom if denom != 0 else 0.0
        else:
            denom = self.stop_loss - self.entry_price
            return (self.entry_price - self.take_profit) / denom if denom != 0 else 0.0
    
    def to_dict(self) -> dict:
        return {
            'ticket': self.ticket,
            'symbol': self.symbol,
            'direction': self.direction.value,
            'phase': self.phase,
            'entry_price': self.entry_price,
            'current_price': self.current_price,
            'volume': self.volume,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'entry_time': str(self.entry_time),
            'status': self.status.value,
            'unrealized_pnl': self.unrealized_pnl,
            'realized_pnl': self.realized_pnl,
            'rr_ratio': self.rr_ratio,
            'current_rr': self.current_rr
        }


@dataclass
class RiskMetrics:
    """Account-level risk metrics"""
    account_balance: float
    account_equity: float
    account_currency: str = "USD"
    
    # Daily metrics
    daily_pnl: float = 0.0
    daily_wins: int = 0
    daily_losses: int = 0
    daily_trades: int = 0
    
    # Drawdown
    peak_equity: float = 0.0
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0          # running tracker (highest DD seen)
    max_drawdown_limit: float = 15.0   # hard limit — never modified after init

    # Risk limits
    daily_loss_limit: float = 0.0
    max_risk_per_trade: float = 0.0
    max_open_positions: int = 0
    
    # Position tracking
    open_positions: List[Position] = field(default_factory=list)
    pending_signals: List[EntrySignal] = field(default_factory=list)
    
    @property
    def free_margin(self) -> float:
        """Calculate free margin"""
        used_margin = sum(pos.volume * 100000 * pos.entry_price * 0.02 for pos in self.open_positions)  # 2% margin per lot
        return self.account_equity - used_margin
    
    @property
    def margin_level(self) -> float:
        """Calculate margin level percentage"""
        used_margin = sum(pos.volume * 100000 * pos.entry_price * 0.02 for pos in self.open_positions)
        if used_margin == 0:
            return 100.0
        return self.account_equity / used_margin * 100
    
    @property
    def total_exposure(self) -> float:
        """Calculate total dollar exposure"""
        return sum(abs(pos.unrealized_pnl) for pos in self.open_positions)
    
    def update_drawdown(self):
        """Update drawdown metrics"""
        if self.account_equity > self.peak_equity:
            self.peak_equity = self.account_equity

        if self.peak_equity > 0:
            self.current_drawdown = (self.peak_equity - self.account_equity) / self.peak_equity * 100
        # max_drawdown = running tracker only; max_drawdown_limit is never touched
        self.max_drawdown = max(self.max_drawdown, self.current_drawdown)


class PositionSizer:
    """
    Calculates position size based on risk parameters.
    """
    
    def __init__(self, account_balance: float = 10000,
                 risk_per_trade_percent: float = 1.0,
                 max_risk_percent: float = 2.0):
        """
        Initialize position sizer.
        
        Args:
            account_balance: Starting account balance
            risk_per_trade_percent: % of account to risk per trade
            max_risk_percent: Maximum % of account at risk
        """
        self.account_balance = account_balance
        self.risk_per_trade_percent = risk_per_trade_percent
        self.max_risk_percent = max_risk_percent
    
    def calculate_position_size(self,
                               entry_price: float,
                               stop_loss: float,
                               symbol: str,
                               account_balance: float = None) -> float:
        """
        Calculate position size in lots.
        
        Args:
            entry_price: Planned entry price
            stop_loss: Stop loss price
            symbol: Trading symbol
            account_balance: Optional account balance override
            
        Returns:
            Position size in standard lots
        """
        if account_balance is None:
            account_balance = self.account_balance
        
        # Calculate risk amount
        risk_amount = account_balance * (self.risk_per_trade_percent / 100)
        
        # Calculate stop loss distance
        if entry_price == 0:
            return 0.0
        
        sl_distance = abs(entry_price - stop_loss)
        
        if sl_distance == 0:
            return 0.0
        
        # Calculate pip value (pass entry_price for JPY-quoted pairs)
        pip_value = self._get_pip_value(symbol, entry_price)
        
        # Calculate position size
        # Risk = PositionSize * SL_Distance * PipValue
        # PositionSize = Risk / (SL_Distance * PipValue)
        
        position_size = risk_amount / (sl_distance * pip_value)

        # Round to 2 decimal places (0.01 lot = 1K units)
        position_size = round(position_size, 2)

        # Clamp between min and max lot size
        position_size = max(0.01, min(position_size, 5.0))  # hard cap at 5 lots
        
        return position_size
    
    def calculate_scaled_position_size(self,
                                       entry_price: float,
                                       stop_loss: float,
                                       target_rr: float,
                                       symbol: str,
                                       account_balance: float = None,
                                       scale_in_percent: float = 50.0) -> Tuple[float, float]:
        """
        Calculate scaled position sizes.
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            target_rr: Target risk-reward ratio
            symbol: Trading symbol
            account_balance: Account balance
            scale_in_percent: % of position to take on first entry
            
        Returns:
            Tuple of (first_position_size, second_position_size)
        """
        # Calculate total position size based on risk
        total_size = self.calculate_position_size(
            entry_price, stop_loss, symbol, account_balance
        )
        
        # Scale in percentages
        first_size = total_size * (scale_in_percent / 100)
        second_size = total_size - first_size
        
        return first_size, second_size
    
    # Contract size per lot for non-standard instruments
    _CONTRACT_SIZES = {
        'XAUUSD': 100,    # Gold: 100 troy oz per lot
        'XAGUSD': 5000,   # Silver: 5000 oz per lot
        'XBRUSD': 100,    # Brent crude: 100 barrels per lot
        'XTIUSD': 100,    # WTI crude: 100 barrels per lot
    }
    _JPY_PAIRS = {
        'USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY',
        'CADJPY', 'CHFJPY', 'NZDJPY',
    }

    def _get_pip_value(self, symbol: str, entry_price: float = 1.0) -> float:
        """Return dollar value per 1 price-unit move per standard lot.

        Formula: position_size = risk_$ / (sl_distance * pip_value)

        Forex USD-quote (EURUSD, GBPUSD …): pip_value = 100,000
        Forex JPY-quote (USDJPY …):          pip_value = 100,000 / price
        Gold (XAUUSD):                        pip_value = 100  (100 oz/lot)
        """
        contract = self._CONTRACT_SIZES.get(symbol, 100000)
        if symbol in self._JPY_PAIRS and entry_price:
            return contract / entry_price
        return float(contract)


class RiskManager:
    """
    Manages overall risk for the trading system.
    
    Key responsibilities:
    - Validate trades against risk limits
    - Calculate position sizes
    - Monitor drawdown
    - Manage trailing stops
    - Enforce daily loss limits
    """
    
    def __init__(self,
                 account_balance: float = 10000,
                 risk_per_trade_percent: float = 1.0,
                 max_daily_loss_percent: float = 5.0,
                 max_drawdown_percent: float = 15.0,
                 max_open_trades: int = 3,
                 use_trailing_stop: bool = True,
                 trailing_stop_activation_rr: float = 1.5):
        """
        Initialize risk manager.
        
        Args:
            account_balance: Starting account balance
            risk_per_trade_percent: % to risk per trade
            max_daily_loss_percent: Max daily loss % before stopping
            max_drawdown_percent: Max drawdown % before stopping
            max_open_trades: Maximum concurrent open trades
            use_trailing_stop: Whether to use trailing stops
            trailing_stop_activation_rr: RR ratio to activate trailing stop
        """
        self._max_daily_loss_pct = max_daily_loss_percent  # store % for daily reset
        self.metrics = RiskMetrics(
            account_balance=account_balance,
            account_equity=account_balance,
            peak_equity=account_balance,
            daily_loss_limit=account_balance * (max_daily_loss_percent / 100),
            max_drawdown=0.0,                        # running tracker starts at 0
            max_drawdown_limit=max_drawdown_percent, # hard limit stays fixed
            max_risk_per_trade=risk_per_trade_percent,
            max_open_positions=max_open_trades
        )
        
        self.position_sizer = PositionSizer(
            account_balance=account_balance,
            risk_per_trade_percent=risk_per_trade_percent
        )
        
        self.use_trailing_stop = use_trailing_stop
        self.trailing_stop_activation_rr = trailing_stop_activation_rr
        self.next_ticket = 1
    
    def can_open_position(self, signal: EntrySignal,
                         current_positions: List[Position] = None) -> Tuple[bool, str]:
        """
        Check if a new position can be opened.
        
        Args:
            signal: Entry signal to validate
            current_positions: Current open positions
            
        Returns:
            Tuple of (can_open, reason)
        """
        if current_positions is None:
            current_positions = self.metrics.open_positions
        
        # Check daily loss limit
        if self.metrics.daily_pnl <= -self.metrics.daily_loss_limit:
            return False, f"Daily loss limit reached: {self.metrics.daily_pnl:.2f}"
        
        # Check max drawdown
        if self.metrics.current_drawdown >= self.metrics.max_drawdown_limit:
            return False, f"Max drawdown reached: {self.metrics.current_drawdown:.1f}%"
        
        # Check max open positions
        if len(current_positions) >= self.metrics.max_open_positions:
            return False, f"Max open positions reached: {len(current_positions)}"
        
        # Check if already have position in same symbol
        existing = [p for p in current_positions if p.symbol == signal.symbol]
        if existing:
            return False, f"Already have position in {signal.symbol}"
        
        # Reject signals with dangerously tight stop loss (< 10 pips)
        sl_distance = abs(signal.entry_price - signal.stop_loss)
        if sl_distance < 0.0010:   # 10 pips minimum for EURUSD-type pairs
            return False, f"SL too tight: {sl_distance:.5f} ({sl_distance*10000:.1f} pips)"

        # Check RR ratio
        if signal.rr_ratio < 2.0:
            return False, f"RR ratio too low: {signal.rr_ratio:.2f}"

        # Check confidence (score=3/6=50% minimum; score=2 BOS-only signals lose too often)
        if signal.confidence < 0.40:
            return False, f"Confidence too low: {signal.confidence:.1%}"
        
        return True, "All checks passed"
    
    def calculate_position_for_signal(self, signal: EntrySignal,
                                     account_balance: float = None) -> float:
        """
        Calculate position size for a signal.
        
        Args:
            signal: Entry signal
            account_balance: Optional account balance
            
        Returns:
            Position size in lots
        """
        if account_balance is None:
            account_balance = self.metrics.account_balance
        
        return self.position_sizer.calculate_position_size(
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            symbol=signal.symbol,
            account_balance=account_balance
        )
    
    def create_position(self, signal: EntrySignal,
                       volume: float = None) -> Position:
        """
        Create a new position from a signal.
        
        Args:
            signal: Entry signal
            volume: Optional volume override
            
        Returns:
            New Position object
        """
        if volume is None:
            volume = self.calculate_position_for_signal(signal)
        
        position = Position(
            ticket=self.next_ticket,
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            current_price=signal.entry_price,
            volume=volume,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            entry_time=datetime.now(),
            status=TradeStatus.OPEN,
            risk_amount=abs(signal.entry_price - signal.stop_loss) * volume * 100000,
            reward_amount=abs(signal.take_profit - signal.entry_price) * volume * 100000,
            rr_ratio=signal.rr_ratio,
            best_price=signal.entry_price,
            phase=getattr(signal, 'phase', '')
        )
        
        self.next_ticket += 1
        
        return position
    
    def update_trailing_stop(self, position: Position,
                            current_price: float) -> Optional[float]:
        """
        Update trailing stop if applicable.
        
        Args:
            position: Position to update
            current_price: Current price
            
        Returns:
            New stop loss price or None if not applicable
        """
        if not self.use_trailing_stop:
            return None
        
        # Check if trailing should activate
        position.calculate_pnl(current_price)
        
        if position.current_rr < self.trailing_stop_activation_rr:
            return None
        
        # Calculate new stop
        if position.direction == SignalDirection.LONG:
            # For longs, trail below price
            atr_trailing = abs(current_price - position.stop_loss) * 0.5
            new_stop = current_price - atr_trailing
            
            # Only move stop in profit direction
            if new_stop > position.stop_loss:
                return new_stop
        else:
            # For shorts, trail above price
            atr_trailing = abs(position.stop_loss - current_price) * 0.5
            new_stop = current_price + atr_trailing
            
            if new_stop < position.stop_loss:
                return new_stop
        
        return None
    
    def check_targets(self, position: Position,
                      current_price: float) -> Tuple[bool, int, float]:
        """
        Check whether any multi-target level has been hit.

        Returns:
            (target_hit, target_index, volume_to_close)
            target_hit is False when nothing is hit.
        """
        if not position.targets:
            return False, -1, 0.0

        for i, (target, hit) in enumerate(zip(position.targets, position.targets_hit)):
            if hit:
                continue
            if position.direction == SignalDirection.LONG and current_price >= target:
                return True, i, position.target_volumes[i]
            elif position.direction == SignalDirection.SHORT and current_price <= target:
                return True, i, position.target_volumes[i]

        return False, -1, 0.0

    def check_scale_in(self, position: Position,
                       current_price: float) -> bool:
        """
        Return True if the scale-in price level has been reached and
        the scale-in has not been executed yet.
        """
        if position.scale_in_done or position.scale_in_price == 0.0:
            return False
        if position.direction == SignalDirection.LONG:
            return current_price <= position.scale_in_price
        else:
            return current_price >= position.scale_in_price

    def move_stop_to_breakeven(self, position: Position):
        """Move stop loss to breakeven (entry price)."""
        if position.direction == SignalDirection.LONG:
            if position.stop_loss < position.entry_price:
                position.stop_loss = position.entry_price
        else:
            if position.stop_loss > position.entry_price:
                position.stop_loss = position.entry_price

    def check_stop_out(self, position: Position,
                       current_price: float) -> Tuple[bool, TradeStatus]:
        """
        Check if position should be stopped out.
        
        Args:
            position: Position to check
            current_price: Current price
            
        Returns:
            Tuple of (should_stop_out, new_status)
        """
        position.calculate_pnl(current_price)
        
        if position.direction == SignalDirection.LONG:
            if current_price <= position.stop_loss:
                return True, TradeStatus.STOPPED_OUT
            elif position.take_profit and current_price >= position.take_profit:
                return True, TradeStatus.TAKE_PROFIT
        else:
            if current_price >= position.stop_loss:
                return True, TradeStatus.STOPPED_OUT
            elif position.take_profit and current_price <= position.take_profit:
                return True, TradeStatus.TAKE_PROFIT
        
        return False, TradeStatus.OPEN
    
    def update_metrics(self, position: Position, closed: bool = False,
                      realized_pnl: float = 0.0):
        """Update account metrics after trade"""
        if closed:
            position.status = TradeStatus.CLOSED
            position.realized_pnl = realized_pnl
            self.metrics.daily_pnl += realized_pnl
            self.metrics.daily_trades += 1
            
            if realized_pnl > 0:
                self.metrics.daily_wins += 1
            else:
                self.metrics.daily_losses += 1
            
            # Remove from open positions
            if position in self.metrics.open_positions:
                self.metrics.open_positions.remove(position)
        
        # Update equity
        self.metrics.account_equity = self.metrics.account_balance + self.metrics.daily_pnl
        self.metrics.update_drawdown()
    
    def get_risk_summary(self) -> dict:
        """Get current risk summary"""
        return {
            'account_balance': self.metrics.account_balance,
            'account_equity': self.metrics.account_equity,
            'daily_pnl': self.metrics.daily_pnl,
            'daily_wins': self.metrics.daily_wins,
            'daily_losses': self.metrics.daily_losses,
            'daily_trades': self.metrics.daily_trades,
            'current_drawdown': self.metrics.current_drawdown,
            'max_drawdown': self.metrics.max_drawdown,
            'open_positions': len(self.metrics.open_positions),
            'free_margin': self.metrics.free_margin,
            'margin_level': self.metrics.margin_level,
            'can_trade': self.metrics.daily_pnl > -self.metrics.daily_loss_limit
        }


class TradeExecutor:
    """
    Simulates trade execution for backtesting.
    In live trading, this would interface with the broker API.
    """
    
    def __init__(self, risk_manager: RiskManager,
                 spread_pips: float = 1.0,
                 slippage_pips: float = 0.5):
        """
        Initialize trade executor.
        
        Args:
            risk_manager: Risk manager instance
            spread_pips: Average spread in pips
            slippage_pips: Slippage in pips
        """
        self.risk_manager = risk_manager
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips
    
    def execute_market_order(self, signal: EntrySignal,
                            volume: float) -> Position:
        """Execute a market order with simulated spread/slippage"""
        # Apply spread
        spread_adjustment = self.spread_pips * 0.0001
        
        if signal.direction == SignalDirection.LONG:
            # Buy at ask (higher)
            execution_price = signal.entry_price + spread_adjustment
        else:
            # Sell at bid (lower)
            execution_price = signal.entry_price - spread_adjustment
        
        # Create position
        position = self.risk_manager.create_position(signal, volume)
        position.entry_price = execution_price
        
        # Recalculate risk with actual entry
        position.risk_amount = abs(execution_price - signal.stop_loss) * volume * 100000
        position.reward_amount = abs(signal.take_profit - execution_price) * volume * 100000
        position.rr_ratio = position.reward_amount / position.risk_amount
        
        # Add to open positions
        self.risk_manager.metrics.open_positions.append(position)
        
        return position
    
    def execute_limit_order(self, signal: EntrySignal,
                           volume: float) -> Position:
        """Execute a limit order (same as market for simulation)"""
        return self.execute_market_order(signal, volume)
    
    def partial_close(self, position: Position,
                      volume_to_close: float,
                      current_price: float,
                      target_index: int = -1) -> float:
        """
        Partially close a position at the given volume and price.

        Returns the realized PnL for the closed portion.
        Marks the target as hit and updates remaining_volume.
        """
        spread_adjustment = self.spread_pips * 0.0001
        _jpy_pairs = {'USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'CADJPY', 'CHFJPY', 'NZDJPY'}
        _contract  = {'XAUUSD': 100, 'XAGUSD': 5000}.get(position.symbol, 100000)
        _divisor   = current_price if position.symbol in _jpy_pairs and current_price else 1.0

        if position.direction == SignalDirection.LONG:
            close_price = current_price - spread_adjustment
            pnl = (close_price - position.entry_price) * volume_to_close * _contract / _divisor
        else:
            close_price = current_price + spread_adjustment
            pnl = (position.entry_price - close_price) * volume_to_close * _contract / _divisor

        position.realized_pnl += pnl
        position.remaining_volume = max(0.0, position.remaining_volume - volume_to_close)
        self.risk_manager.metrics.daily_pnl += pnl

        # Mark target as hit
        if target_index >= 0 and target_index < len(position.targets_hit):
            position.targets_hit[target_index] = True

        if position.remaining_volume <= 0.001:
            position.status = TradeStatus.TAKE_PROFIT
            self.risk_manager.update_metrics(position, closed=True, realized_pnl=0.0)

        return pnl

    def close_position(self, position: Position,
                       current_price: float = None) -> float:
        """Close a position and return realized PnL"""
        if current_price is None:
            current_price = position.current_price
        
        # Apply spread for closing
        spread_adjustment = self.spread_pips * 0.0001
        
        if position.direction == SignalDirection.LONG:
            # Close by selling at bid
            close_price = current_price - spread_adjustment
        else:
            # Close by buying at ask
            close_price = current_price + spread_adjustment
        
        # Calculate PnL (handle JPY pairs and non-standard contract sizes)
        _jpy_pairs = {'USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'CADJPY', 'CHFJPY', 'NZDJPY'}
        _contract  = {'XAUUSD': 100, 'XAGUSD': 5000}.get(position.symbol, 100000)
        _divisor   = close_price if position.symbol in _jpy_pairs and close_price else 1.0
        if position.direction == SignalDirection.LONG:
            realized_pnl = (close_price - position.entry_price) * position.volume * _contract / _divisor
        else:
            realized_pnl = (position.entry_price - close_price) * position.volume * _contract / _divisor
        
        # Update metrics
        self.risk_manager.update_metrics(position, closed=True, realized_pnl=realized_pnl)
        
        return realized_pnl


if __name__ == "__main__":
    # Example usage
    from ..strategies.counter_trend_entry import EntrySignal, SignalDirection
    
    # Create risk manager
    rm = RiskManager(
        account_balance=10000,
        risk_per_trade_percent=1.0,
        max_daily_loss_percent=5.0,
        max_drawdown_percent=15.0,
        max_open_trades=3
    )
    
    print("Initial Risk Summary:")
    summary = rm.get_risk_summary()
    for key, value in summary.items():
        print(f"  {key}: {value}")
    
    # Create a test signal
    signal = EntrySignal(
        timestamp=datetime.now(),
        symbol="EURUSD",
        direction=SignalDirection.LONG,
        entry_type=None,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1200,
        confidence=0.8,
        rr_ratio=2.67,
        timeframe="15M",
        phase="PHASE 7",
        criteria_met=["Weekly bias identified", "15M POI identified"]
    )
    
    # Check if can open
    can_open, reason = rm.can_open_position(signal)
    print(f"\nCan open position: {can_open} - {reason}")
    
    if can_open:
        # Calculate position size
        size = rm.calculate_position_for_signal(signal)
        print(f"Position size: {size:.2f} lots")
        
        # Execute trade
        executor = TradeExecutor(rm, spread_pips=1.0)
        position = executor.execute_market_order(signal, size)
        
        print(f"\nOpened position:")
        print(f"  Ticket: {position.ticket}")
        print(f"  Entry: {position.entry_price:.5f}")
        print(f"  Stop: {position.stop_loss:.5f}")
        print(f"  Target: {position.take_profit:.5f}")
        print(f"  Size: {position.volume:.2f} lots")
        print(f"  Risk: ${position.risk_amount:.2f}")
