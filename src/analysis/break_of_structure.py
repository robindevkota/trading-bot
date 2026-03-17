"""
Break of Structure (BoS) Detection Module
Identifies trend changes and continuations using BoS methodology
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum

from .swing_detection import SwingDetector, SwingPoint, SwingType


class TrendDirection(Enum):
    """Trend direction enumeration"""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class BreakOfStructure:
    """Represents a Break of Structure event"""
    index: int
    timestamp: pd.Timestamp
    direction: TrendDirection
    previous_swing: SwingPoint
    current_swing: SwingPoint
    structure_broken: SwingType  # The swing type that was broken
    momentum_strength: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'index': self.index,
            'timestamp': str(self.timestamp),
            'direction': self.direction.value,
            'previous_swing_price': self.previous_swing.price,
            'current_swing_price': self.current_swing.price,
            'structure_broken': self.structure_broken.value,
            'momentum_strength': self.momentum_strength
        }


class BreakOfStructureDetector:
    """
    Detects Break of Structure (BoS) events.
    
    A BoS occurs when price breaks above a previous swing high (bullish)
    or below a previous swing low (bearish), indicating trend continuation
    or reversal.
    """
    
    def __init__(self, swing_detector: Optional[SwingDetector] = None):
        """
        Initialize the BoS detector.
        
        Args:
            swing_detector: Optional SwingDetector instance. If None, creates default.
        """
        self.swing_detector = swing_detector or SwingDetector()
    
    def detect_bos(self, df: pd.DataFrame) -> List[BreakOfStructure]:
        """
        Detect all BoS events in the price data.
        
        Args:
            df: DataFrame with OHLC data
            
        Returns:
            List of BreakOfStructure events
        """
        swings = self.swing_detector.detect_swings(df)
        
        if len(swings) < 2:
            return []
        
        bos_events = []
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        close_col = 'close' if 'close' in df.columns else 'Close'
        
        for i in range(1, len(swings)):
            prev_swing = swings[i - 1]
            curr_swing = swings[i]
            
            # Check for bullish BoS: current swing high breaks above previous swing high
            if (prev_swing.swing_type == SwingType.HIGH and 
                curr_swing.swing_type == SwingType.HIGH and
                curr_swing.price > prev_swing.price):
                
                # Confirm with close price breaking above
                close_above_prev_high = self._check_close_above_level(
                    df, prev_swing.index, curr_swing.index, prev_swing.price, high_col, close_col
                )
                
                if close_above_prev_high:
                    momentum = self._calculate_momentum(df, curr_swing, TrendDirection.BULLISH)
                    bos = BreakOfStructure(
                        index=curr_swing.index,
                        timestamp=df.index[curr_swing.index] if hasattr(df.index, '__getitem__') else pd.Timestamp.now(),
                        direction=TrendDirection.BULLISH,
                        previous_swing=prev_swing,
                        current_swing=curr_swing,
                        structure_broken=SwingType.HIGH,
                        momentum_strength=momentum
                    )
                    bos_events.append(bos)
            
            # Check for bearish BoS: current swing low breaks below previous swing low
            elif (prev_swing.swing_type == SwingType.LOW and 
                  curr_swing.swing_type == SwingType.LOW and
                  curr_swing.price < prev_swing.price):
                
                # Confirm with close price breaking below
                close_below_prev_low = self._check_close_below_level(
                    df, prev_swing.index, curr_swing.index, prev_swing.price, low_col, close_col
                )
                
                if close_below_prev_low:
                    momentum = self._calculate_momentum(df, curr_swing, TrendDirection.BEARISH)
                    bos = BreakOfStructure(
                        index=curr_swing.index,
                        timestamp=df.index[curr_swing.index] if hasattr(df.index, '__getitem__') else pd.Timestamp.now(),
                        direction=TrendDirection.BEARISH,
                        previous_swing=prev_swing,
                        current_swing=curr_swing,
                        structure_broken=SwingType.LOW,
                        momentum_strength=momentum
                    )
                    bos_events.append(bos)
        
        return bos_events
    
    def _check_close_above_level(self, df: pd.DataFrame, start_idx: int, 
                                  end_idx: int, level: float, high_col: str, 
                                  close_col: str) -> bool:
        """Check if price closed above a level between two indices"""
        if end_idx >= len(df):
            return False
        
        # Check if high broke level and close confirmed
        range_data = df.iloc[start_idx:end_idx + 1]
        
        # High must break above level
        if range_data[high_col].max() <= level:
            return False
        
        # At least one close should be above level
        return (range_data[close_col] > level).any()
    
    def _check_close_below_level(self, df: pd.DataFrame, start_idx: int,
                                  end_idx: int, level: float, low_col: str,
                                  close_col: str) -> bool:
        """Check if price closed below a level between two indices"""
        if end_idx >= len(df):
            return False
        
        range_data = df.iloc[start_idx:end_idx + 1]
        
        # Low must break below level
        if range_data[low_col].min() >= level:
            return False
        
        # At least one close should be below level
        return (range_data[close_col] < level).any()
    
    def _calculate_momentum(self, df: pd.DataFrame, swing: SwingPoint, 
                           direction: TrendDirection) -> float:
        """Calculate momentum strength of a BoS event"""
        if len(df) < swing.index + 10:
            return 0.0
        
        close_col = 'close' if 'close' in df.columns else 'Close'
        
        # Calculate momentum as rate of change
        lookback = min(10, swing.index)
        
        if swing.index < lookback:
            return 0.0
        
        current_close = df[close_col].iloc[swing.index]
        past_close = df[close_col].iloc[swing.index - lookback]
        
        if direction == TrendDirection.BULLISH:
            momentum = (current_close - past_close) / past_close * 100
        else:
            momentum = (past_close - current_close) / past_close * 100
        
        return momentum
    
    def detect_sequential_bos(self, df: pd.DataFrame, min_bos: int = 2) -> List[List[BreakOfStructure]]:
        """
        Detect sequential BoS events (double BoS).
        
        Args:
            df: DataFrame with OHLC data
            min_bos: Minimum number of sequential BoS to detect
            
        Returns:
            List of lists, where each inner list contains sequential BoS events
        """
        bos_events = self.detect_bos(df)
        
        if len(bos_events) < min_bos:
            return []
        
        sequential_bos = []
        current_sequence = [bos_events[0]]
        
        for i in range(1, len(bos_events)):
            current_bos = bos_events[i]
            prev_bos = current_sequence[-1]
            
            # Check if this BoS continues in the same direction
            if current_bos.direction == prev_bos.direction:
                current_sequence.append(current_bos)
            else:
                # Check if we have enough sequential BoS
                if len(current_sequence) >= min_bos:
                    sequential_bos.append(current_sequence.copy())
                current_sequence = [current_bos]
        
        # Don't forget the last sequence
        if len(current_sequence) >= min_bos:
            sequential_bos.append(current_sequence)
        
        return sequential_bos
    
    def get_latest_bos(self, df: pd.DataFrame) -> Optional[BreakOfStructure]:
        """Get the most recent BoS event"""
        bos_events = self.detect_bos(df)
        return bos_events[-1] if bos_events else None
    
    def detect_trend_change(self, df: pd.DataFrame) -> Tuple[Optional[BreakOfStructure], TrendDirection]:
        """
        Detect if there's a trend change based on BoS.
        
        Returns:
            Tuple of (latest BoS event, current trend direction)
        """
        bos_events = self.detect_bos(df)
        
        if not bos_events:
            return None, TrendDirection.NEUTRAL
        
        latest_bos = bos_events[-1]
        
        # Current trend is opposite to the last BoS (counter-trend concept)
        if latest_bos.direction == TrendDirection.BULLISH:
            current_trend = TrendDirection.BEARISH  # Bearish trend waiting for bullish reversal
        else:
            current_trend = TrendDirection.BULLISH
        
        return latest_bos, current_trend


class ChangeOfCharacterDetector:
    """
    Detects Change of Character (CoC) - a more aggressive form of BoS.
    
    CoC represents a stronger shift in market structure, often characterized by:
    - Larger price rejection candles
    - V-shape formations
    - Momentum acceleration
    """
    
    def __init__(self, swing_detector: Optional[SwingDetector] = None,
                 rejection_threshold: float = 0.5):
        """
        Initialize CoC detector.
        
        Args:
            swing_detector: Optional SwingDetector instance
            rejection_threshold: Threshold for candle rejection (0-1)
        """
        self.swing_detector = swing_detector or SwingDetector()
        self.rejection_threshold = rejection_threshold
    
    def detect_coc(self, df: pd.DataFrame) -> List[dict]:
        """
        Detect Change of Character events.
        
        Args:
            df: DataFrame with OHLC data
            
        Returns:
            List of CoC events with details
        """
        coc_events = []
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        open_col = 'open' if 'open' in df.columns else 'Open'
        close_col = 'close' if 'close' in df.columns else 'Close'
        
        # Calculate candle characteristics
        body_size = abs(df[close_col] - df[open_col])
        upper_wick = df[high_col] - df[[open_col, close_col]].max(axis=1)
        lower_wick = df[[open_col, close_col]].min(axis=1) - df[low_col]
        candle_range = df[high_col] - df[low_col]
        
        # Calculate rejection ratio (wick to body)
        rejection_ratio = (upper_wick + lower_wick) / (candle_size := (body_size + 0.0001))
        
        for i in range(len(df)):
            # Look for V-shape formations (sharp reversals)
            if i >= 10:
                # Check for bullish reversal (V-shape)
                if self._is_v_shape(df, i, high_col, low_col, close_col):
                    coc_events.append({
                        'index': i,
                        'timestamp': df.index[i] if hasattr(df.index, '__getitem__') else None,
                        'type': 'bullish_v_shape',
                        'rejection_ratio': rejection_ratio.iloc[i],
                        'strength': self._calculate_coc_strength(df, i, TrendDirection.BULLISH)
                    })
                
                # Check for bearish reversal (inverted V-shape)
                elif self._is_inverted_v_shape(df, i, high_col, low_col, close_col):
                    coc_events.append({
                        'index': i,
                        'timestamp': df.index[i] if hasattr(df.index, '__getitem__') else None,
                        'type': 'bearish_v_shape',
                        'rejection_ratio': rejection_ratio.iloc[i],
                        'strength': self._calculate_coc_strength(df, i, TrendDirection.BEARISH)
                    })
                
                # Check for strong rejection candle
                if rejection_ratio.iloc[i] > self.rejection_threshold:
                    body_ratio = body_size.iloc[i] / candle_range.iloc[i]
                    
                    if df[close_col].iloc[i] > df[open_col].iloc[i] and body_ratio > 0.6:
                        coc_events.append({
                            'index': i,
                            'timestamp': df.index[i] if hasattr(df.index, '__getitem__') else None,
                            'type': 'bullish_rejection',
                            'rejection_ratio': rejection_ratio.iloc[i],
                            'strength': self._calculate_coc_strength(df, i, TrendDirection.BULLISH)
                        })
                    elif df[close_col].iloc[i] < df[open_col].iloc[i] and body_ratio > 0.6:
                        coc_events.append({
                            'index': i,
                            'timestamp': df.index[i] if hasattr(df.index, '__getitem__') else None,
                            'type': 'bearish_rejection',
                            'rejection_ratio': rejection_ratio.iloc[i],
                            'strength': self._calculate_coc_strength(df, i, TrendDirection.BEARISH)
                        })
        
        return coc_events
    
    def _is_v_shape(self, df: pd.DataFrame, index: int, high_col: str,
                   low_col: str, close_col: str) -> bool:
        """Detect V-shape formation (sharp reversal from lows)"""
        if index < 5:
            return False
        
        close_col = 'close' if 'close' in df.columns else 'Close'
        
        # Check recent lows
        lookback = 5
        range_data = df.iloc[index - lookback:index + 1]
        
        lowest_point = range_data[low_col].idxmin()
        lowest_idx = range_data.index.get_loc(lowest_point)
        
        # Low should be near current bar (forming V bottom)
        if lowest_idx < lookback - 2:
            return False
        
        # Price should have risen significantly from low
        current_close = df[close_col].iloc[index]
        low_price = range_data[low_col].min()
        
        rise_percent = (current_close - low_price) / low_price * 100
        
        return rise_percent > 1.0  # At least 1% rise from low
    
    def _is_inverted_v_shape(self, df: pd.DataFrame, index: int, high_col: str,
                             low_col: str, close_col: str) -> bool:
        """Detect inverted V-shape formation (sharp reversal from highs)"""
        if index < 5:
            return False
        
        close_col = 'close' if 'close' in df.columns else 'Close'
        
        lookback = 5
        range_data = df.iloc[index - lookback:index + 1]
        
        highest_point = range_data[high_col].idxmax()
        highest_idx = range_data.index.get_loc(highest_point)
        
        if highest_idx < lookback - 2:
            return False
        
        high_price = range_data[high_col].max()
        current_close = df[close_col].iloc[index]
        
        drop_percent = (high_price - current_close) / high_price * 100
        
        return drop_percent > 1.0
    
    def _calculate_coc_strength(self, df: pd.DataFrame, index: int,
                               direction: TrendDirection) -> float:
        """Calculate CoC strength based on multiple factors"""
        close_col = 'close' if 'close' in df.columns else 'Close'
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        
        if len(df) < index + 5:
            return 0.0
        
        # Factor 1: Price momentum
        lookback = min(5, index)
        current_close = df[close_col].iloc[index]
        past_close = df[close_col].iloc[index - lookback]
        
        if direction == TrendDirection.BULLISH:
            momentum = (current_close - past_close) / past_close * 100
        else:
            momentum = (past_close - current_close) / past_close * 100
        
        # Factor 2: Candle size
        body_size = abs(df[close_col].iloc[index] - df['open'].iloc[index] if 'open' in df.columns else df['Open'].iloc[index])
        candle_range = df[high_col].iloc[index] - df[low_col].iloc[index]
        size_ratio = body_size / candle_range if candle_range > 0 else 0
        
        # Factor 3: Wick rejection
        upper_wick = df[high_col].iloc[index] - max(df[close_col].iloc[index], df['open'].iloc[index] if 'open' in df.columns else df['Open'].iloc[index])
        lower_wick = min(df[close_col].iloc[index], df['open'].iloc[index] if 'open' in df.columns else df['Open'].iloc[index]) - df[low_col].iloc[index]
        
        if direction == TrendDirection.BULLISH:
            wick_ratio = lower_wick / candle_range if candle_range > 0 else 0
        else:
            wick_ratio = upper_wick / candle_range if candle_range > 0 else 0
        
        # Combined strength score
        strength = (momentum * 0.4 + size_ratio * 30 + wick_ratio * 20)
        
        return strength
    
    def get_latest_coc(self, df: pd.DataFrame) -> Optional[dict]:
        """Get the most recent CoC event"""
        coc_events = self.detect_coc(df)
        return coc_events[-1] if coc_events else None


if __name__ == "__main__":
    # Example usage
    import pandas as pd
    import numpy as np
    
    # Generate sample data
    np.random.seed(42)
    dates = pd.date_range(start="2024-01-01", periods=200, freq="1H")
    
    # Create price data with some structure
    close_prices = 100 + np.cumsum(np.random.randn(200) * 0.5)
    high_prices = close_prices + np.abs(np.random.randn(200) * 0.3)
    low_prices = close_prices - np.abs(np.random.randn(200) * 0.3)
    
    df = pd.DataFrame({
        'open': close_prices + np.random.randn(200) * 0.1,
        'high': high_prices,
        'low': low_prices,
        'close': close_prices
    }, index=dates)
    
    # Detect BoS
    bos_detector = BreakOfStructureDetector()
    bos_events = bos_detector.detect_bos(df)
    
    print(f"Detected {len(bos_events)} BoS events:")
    for bos in bos_events:
        print(f"  {bos.direction.value}: Price broke {bos.structure_broken.value} at {bos.current_swing.price:.2f}")
    
    # Detect sequential BoS
    sequences = bos_detector.detect_sequential_bos(df, min_bos=2)
    print(f"\nDetected {len(sequences)} double BoS sequences")
    
    # Detect CoC
    coc_detector = ChangeOfCharacterDetector()
    coc_events = coc_detector.detect_coc(df)
    print(f"\nDetected {len(coc_events)} CoC events")
