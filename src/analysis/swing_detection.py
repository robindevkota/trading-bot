"""
Swing Detection Module for Counter Trend Trading System
Identifies Swing Highs and Swing Lows on price charts
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum


class SwingType(Enum):
    """Types of swing points"""
    HIGH = "high"
    LOW = "low"
    NONE = "none"


@dataclass
class SwingPoint:
    """Represents a swing point on the chart"""
    index: int
    price: float
    swing_type: SwingType
    timestamp: Optional[pd.Timestamp] = None
    strength: float = 1.0  # Strength of the swing (number of candles on each side)
    
    def to_dict(self) -> dict:
        return {
            'index': self.index,
            'price': self.price,
            'swing_type': self.swing_type.value,
            'timestamp': str(self.timestamp) if self.timestamp else None,
            'strength': self.strength
        }


class SwingDetector:
    """
    Detects swing highs and swing lows on price data.
    
    Uses a pivot-based approach where a swing high is a high that is higher 
    than 'left_bars' candles to the left and 'right_bars' candles to the right.
    """
    
    def __init__(self, left_bars: int = 5, right_bars: int = 5, min_bars_between_swings: int = 3):
        """
        Initialize the swing detector.
        
        Args:
            left_bars: Number of bars to check to the left of potential swing
            right_bars: Number of bars to check to the right of potential swing
            min_bars_between_swings: Minimum bars between consecutive swings
        """
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.min_bars_between_swings = min_bars_between_swings
    
    def detect_swings(self, df: pd.DataFrame) -> List[SwingPoint]:
        """
        Detect all swing highs and lows in the price data.
        
        Args:
            df: DataFrame with 'high' and 'low' columns
            
        Returns:
            List of SwingPoint objects
        """
        if len(df) < self.left_bars + self.right_bars + 1:
            return []
        
        swings = []
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        
        for i in range(self.left_bars, len(df) - self.right_bars):
            is_high = self._is_swing_high(df, i, high_col)
            is_low = self._is_swing_low(df, i, low_col)
            
            if is_high:
                strength = self._calculate_strength(df, i, high_col)
                swing = SwingPoint(
                    index=i,
                    price=df[high_col].iloc[i],
                    swing_type=SwingType.HIGH,
                    timestamp=df.index[i] if hasattr(df.index, '__getitem__') else None,
                    strength=strength
                )
                swings.append(swing)
            elif is_low:
                strength = self._calculate_strength(df, i, low_col)
                swing = SwingPoint(
                    index=i,
                    price=df[low_col].iloc[i],
                    swing_type=SwingType.LOW,
                    timestamp=df.index[i] if hasattr(df.index, '__getitem__') else None,
                    strength=strength
                )
                swings.append(swing)
        
        return self._merge_close_swings(swings)
    
    def _is_swing_high(self, df: pd.DataFrame, index: int, high_col: str) -> bool:
        """Check if the current bar is a swing high"""
        current_high = df[high_col].iloc[index]
        
        # Check left bars
        left_start = max(0, index - self.left_bars)
        left_highs = df[high_col].iloc[left_start:index].values
        if len(left_highs) > 0 and current_high <= max(left_highs):
            return False
        
        # Check right bars
        right_end = min(len(df), index + self.right_bars + 1)
        right_highs = df[high_col].iloc[index+1:right_end].values
        if len(right_highs) > 0 and current_high < max(right_highs):
            return False
        
        return True
    
    def _is_swing_low(self, df: pd.DataFrame, index: int, low_col: str) -> bool:
        """Check if the current bar is a swing low"""
        current_low = df[low_col].iloc[index]
        
        # Check left bars
        left_start = max(0, index - self.left_bars)
        left_lows = df[low_col].iloc[left_start:index].values
        if len(left_lows) > 0 and current_low >= min(left_lows):
            return False
        
        # Check right bars
        right_end = min(len(df), index + self.right_bars + 1)
        right_lows = df[low_col].iloc[index+1:right_end].values
        if len(right_lows) > 0 and current_low > min(right_lows):
            return False
        
        return True
    
    def _calculate_strength(self, df: pd.DataFrame, index: int, price_col: str) -> float:
        """Calculate the strength of a swing point based on price penetration"""
        current_price = df[price_col].iloc[index]
        
        if price_col == 'high':
            # For swing highs, strength is how much it protrudes above neighbors
            left_end = max(0, index - self.left_bars)
            right_end = min(len(df), index + self.right_bars + 1)
            
            left_max = df[price_col].iloc[left_end:index].max() if left_end < index else current_price
            right_max = df[price_col].iloc[index+1:right_end].max() if index + 1 < right_end else current_price
            
            neighbor_max = max(left_max, right_max)
            if neighbor_max > 0:
                return (current_price - neighbor_max) / neighbor_max * 100 + 1
        else:
            # For swing lows
            left_end = max(0, index - self.left_bars)
            right_end = min(len(df), index + self.right_bars + 1)
            
            left_min = df[price_col].iloc[left_end:index].min() if left_end < index else current_price
            right_min = df[price_col].iloc[index+1:right_end].min() if index + 1 < right_end else current_price
            
            neighbor_min = min(left_min, right_min)
            if neighbor_min > 0:
                return (neighbor_min - current_price) / neighbor_min * 100 + 1
        
        return 1.0
    
    def _merge_close_swings(self, swings: List[SwingPoint]) -> List[SwingPoint]:
        """Merge swings that are too close together, keeping the stronger one"""
        if len(swings) <= 1:
            return swings
        
        merged = []
        current_group = [swings[0]]
        
        for i in range(1, len(swings)):
            if swings[i].index - current_group[-1].index < self.min_bars_between_swings:
                current_group.append(swings[i])
            else:
                # Keep the strongest swing in the group
                strongest = max(current_group, key=lambda s: s.strength)
                merged.append(strongest)
                current_group = [swings[i]]
        
        # Don't forget the last group
        if current_group:
            strongest = max(current_group, key=lambda s: s.strength)
            merged.append(strongest)
        
        return merged
    
    def get_latest_swing(self, df: pd.DataFrame, swing_type: SwingType = None) -> Optional[SwingPoint]:
        """
        Get the most recent swing point.
        
        Args:
            df: DataFrame with price data
            swing_type: Optional filter for swing type
            
        Returns:
            Most recent SwingPoint or None
        """
        swings = self.detect_swings(df)
        
        if swing_type:
            swings = [s for s in swings if s.swing_type == swing_type]
        
        return swings[-1] if swings else None
    
    def get_swing_highs(self, df: pd.DataFrame) -> List[SwingPoint]:
        """Get only swing highs"""
        return [s for s in self.detect_swings(df) if s.swing_type == SwingType.HIGH]
    
    def get_swing_lows(self, df: pd.DataFrame) -> List[SwingPoint]:
        """Get only swing lows"""
        return [s for s in self.detect_swings(df) if s.swing_type == SwingType.LOW]
    
    def get_swing_range(self, df: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        """
        Get the current swing range (highest high and lowest low).
        
        Returns:
            Tuple of (highest_high, lowest_low)
        """
        swings = self.detect_swings(df)
        
        highs = [s.price for s in swings if s.swing_type == SwingType.HIGH]
        lows = [s.price for s in swings if s.swing_type == SwingType.LOW]
        
        return (max(highs) if highs else None, min(lows) if lows else None)


class AdaptiveSwingDetector(SwingDetector):
    """
    Swing detector with adaptive parameters based on volatility.
    Uses ATR to dynamically adjust swing detection parameters.
    """
    
    def __init__(self, left_bars: int = 5, right_bars: int = 5, 
                 min_bars_between_swings: int = 3, atr_period: int = 14):
        super().__init__(left_bars, right_bars, min_bars_between_swings)
        self.atr_period = atr_period
    
    def _calculate_atr_multiplier(self, df: pd.DataFrame) -> float:
        """Calculate ATR-based multiplier for swing detection"""
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        close_col = 'close' if 'close' in df.columns else 'Close'
        
        # Calculate True Range
        tr1 = df[high_col] - df[low_col]
        tr2 = abs(df[high_col] - df[close_col].shift(1))
        tr3 = abs(df[low_col] - df[close_col].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.rolling(window=self.atr_period).mean()
        current_atr = atr.iloc[-1]
        
        # Normalize ATR relative to price
        avg_price = df[close_col].iloc[-1]
        atr_percent = current_atr / avg_price if avg_price > 0 else 0.1
        
        # Convert to multiplier (higher volatility = more bars needed)
        return 1.0 + (atr_percent * 10)
    
    def detect_swings(self, df: pd.DataFrame) -> List[SwingPoint]:
        """Detect swings with volatility-adjusted parameters"""
        if len(df) < self.left_bars + self.right_bars + self.atr_period:
            return []
        
        multiplier = self._calculate_atr_multiplier(df)
        
        # Temporarily adjust parameters
        original_left = self.left_bars
        original_right = self.right_bars
        
        self.left_bars = int(self.left_bars * multiplier)
        self.right_bars = int(self.right_bars * multiplier)
        
        swings = super().detect_swings(df)
        
        # Restore original parameters
        self.left_bars = original_left
        self.right_bars = original_right
        
        return swings


if __name__ == "__main__":
    # Example usage
    import pandas as pd
    import numpy as np
    
    # Generate sample data
    np.random.seed(42)
    dates = pd.date_range(start="2024-01-01", periods=100, freq="1H")
    
    # Create price data with some trend and volatility
    close_prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    high_prices = close_prices + np.abs(np.random.randn(100) * 0.3)
    low_prices = close_prices - np.abs(np.random.randn(100) * 0.3)
    
    df = pd.DataFrame({
        'open': close_prices + np.random.randn(100) * 0.1,
        'high': high_prices,
        'low': low_prices,
        'close': close_prices
    }, index=dates)
    
    # Detect swings
    detector = SwingDetector(left_bars=5, right_bars=5)
    swings = detector.detect_swings(df)
    
    print(f"Detected {len(swings)} swing points:")
    for swing in swings:
        print(f"  {swing.swing_type.value}: {swing.price:.2f} at index {swing.index}")
    
    # Get latest swing
    latest_high = detector.get_latest_swing(df, SwingType.HIGH)
    latest_low = detector.get_latest_swing(df, SwingType.LOW)
    
    if latest_high:
        print(f"\nLatest Swing High: {latest_high.price:.2f}")
    if latest_low:
        print(f"Latest Swing Low: {latest_low.price:.2f}")
