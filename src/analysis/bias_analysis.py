"""
Multi-Timeframe Bias Analysis Module
Determines market bias across Weekly, Daily, 4H, and 15M timeframes
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from enum import Enum

from .swing_detection import SwingDetector, SwingPoint, SwingType
from .break_of_structure import BreakOfStructureDetector, TrendDirection
from .zone_detection import ZoneDetector, Zone, ZoneType


class BiasStrength(Enum):
    """Strength of market bias"""
    STRONG = 3
    MODERATE = 2
    WEAK = 1
    NEUTRAL = 0


@dataclass
class TimeframeBias:
    """Represents bias for a single timeframe"""
    timeframe: str
    trend: TrendDirection
    strength: BiasStrength
    swing_highs: List[SwingPoint]
    swing_lows: List[SwingPoint]
    last_bos_direction: Optional[TrendDirection]
    bos_count: int
    higher_highs: int
    higher_lows: int
    lower_highs: int
    lower_lows: int
    
    @property
    def is_bullish(self) -> bool:
        return self.trend == TrendDirection.BULLISH
    
    @property
    def is_bearish(self) -> bool:
        return self.trend == TrendDirection.BEARISH
    
    @property
    def is_neutral(self) -> bool:
        return self.trend == TrendDirection.NEUTRAL
    
    def to_dict(self) -> dict:
        return {
            'timeframe': self.timeframe,
            'trend': self.trend.value,
            'strength': self.strength.name,
            'last_bos_direction': self.last_bos_direction.value if self.last_bos_direction else None,
            'bos_count': self.bos_count,
            'higher_highs': self.higher_highs,
            'higher_lows': self.higher_lows,
            'lower_highs': self.lower_lows,
            'lower_lows': self.lower_lows
        }


@dataclass
class MultiTimeframeBias:
    """Combined bias analysis across multiple timeframes"""
    weekly_bias: TimeframeBias
    daily_bias: TimeframeBias
    h4_bias: TimeframeBias
    m15_bias: TimeframeBias
    
    # Combined analysis
    aligned_trend: TrendDirection
    alignment_strength: float  # 0-1, how aligned are timeframes
    counter_trend_opportunity: bool
    recommended_entry_timeframe: str
    
    def to_dict(self) -> dict:
        return {
            'weekly': self.weekly_bias.to_dict(),
            'daily': self.daily_bias.to_dict(),
            'h4': self.h4_bias.to_dict(),
            'm15': self.m15_bias.to_dict(),
            'aligned_trend': self.aligned_trend.value,
            'alignment_strength': self.alignment_strength,
            'counter_trend_opportunity': self.counter_trend_opportunity,
            'recommended_entry_timeframe': self.recommended_entry_timeframe
        }


class BiasAnalyzer:
    """
    Analyzes market bias across multiple timeframes.
    
    For counter-trend trading:
    - Weekly bias tells us the macro trend to trade against
    - Daily bias confirms the trend context
    - 4H shows the immediate trend and potential reversal points
    - 15M provides precise entry timing
    """
    
    def __init__(self, swing_lookback: int = 10):
        """
        Initialize bias analyzer.
        
        Args:
            swing_lookback: Number of swings to consider for bias calculation
        """
        self.swing_lookback = swing_lookback
        self.swing_detector = SwingDetector()
        self.bos_detector = BreakOfStructureDetector()
        self.zone_detector = ZoneDetector()
    
    def analyze_timeframe(self, df: pd.DataFrame, 
                          timeframe: str) -> TimeframeBias:
        """
        Analyze bias for a single timeframe.
        
        Args:
            df: OHLC DataFrame
            timeframe: Name of timeframe (e.g., "1W", "1D", "4H", "15M")
            
        Returns:
            TimeframeBias object
        """
        swings = self.swing_detector.detect_swings(df)
        
        # Separate highs and lows
        highs = [s for s in swings if s.swing_type == SwingType.HIGH][-self.swing_lookback:]
        lows = [s for s in swings if s.swing_type == SwingType.LOW][-self.swing_lookback:]
        
        # Analyze structure
        higher_highs, higher_lows = self._count_hh_hl(highs, lows)
        lower_highs, lower_lows = self._count_lh_ll(highs, lows)
        
        # Determine trend from structure
        trend = self._determine_trend_from_structure(
            higher_highs, higher_lows, lower_highs, lower_lows
        )
        
        # Get BoS events
        bos_events = self.bos_detector.detect_bos(df)
        last_bos = bos_events[-1].direction if bos_events else None
        bos_count = len(bos_events)
        
        # Calculate bias strength
        strength = self._calculate_bias_strength(
            trend, higher_highs, higher_lows, lower_highs, lower_lows, bos_count
        )
        
        return TimeframeBias(
            timeframe=timeframe,
            trend=trend,
            strength=strength,
            swing_highs=highs,
            swing_lows=lows,
            last_bos_direction=last_bos,
            bos_count=bos_count,
            higher_highs=higher_highs,
            higher_lows=higher_lows,
            lower_highs=lower_highs,
            lower_lows=lower_lows
        )
    
    def _count_hh_hl(self, highs: List[SwingPoint], 
                    lows: List[SwingPoint]) -> Tuple[int, int]:
        """Count higher highs and higher lows"""
        if len(highs) < 2 or len(lows) < 2:
            return 0, 0
        
        hh_count = sum(1 for i in range(1, len(highs)) if highs[i].price > highs[i-1].price)
        hl_count = sum(1 for i in range(1, len(lows)) if lows[i].price > lows[i-1].price)
        
        return hh_count, hl_count
    
    def _count_lh_ll(self, highs: List[SwingPoint], 
                    lows: List[SwingPoint]) -> Tuple[int, int]:
        """Count lower highs and lower lows"""
        if len(highs) < 2 or len(lows) < 2:
            return 0, 0
        
        lh_count = sum(1 for i in range(1, len(highs)) if highs[i].price < highs[i-1].price)
        ll_count = sum(1 for i in range(1, len(lows)) if lows[i].price < lows[i-1].price)
        
        return lh_count, ll_count
    
    def _determine_trend_from_structure(self, hh: int, hl: int, 
                                        lh: int, ll: int) -> TrendDirection:
        """Determine trend direction from swing structure"""
        if hh >= 2 and hl >= 2 and (hh + hl) > (lh + ll):
            return TrendDirection.BULLISH
        elif ll >= 2 and lh >= 2 and (lh + ll) > (hh + hl):
            return TrendDirection.BEARISH
        else:
            return TrendDirection.NEUTRAL
    
    def _calculate_bias_strength(self, trend: TrendDirection, hh: int, hl: int,
                                lh: int, ll: int, bos_count: int) -> BiasStrength:
        """Calculate the strength of the bias"""
        if trend == TrendDirection.NEUTRAL:
            return BiasStrength.NEUTRAL
        
        total_aligned = hh + hl if trend == TrendDirection.BULLISH else lh + ll
        total_counter = lh + ll if trend == TrendDirection.BULLISH else hh + hl
        
        # Strength based on structure clarity
        if total_aligned >= 4 and total_counter <= 1:
            return BiasStrength.STRONG
        elif total_aligned >= 2 and total_counter <= total_aligned:
            return BiasStrength.MODERATE
        else:
            return BiasStrength.WEAK
    
    def analyze_all_timeframes(self,
                              weekly_df: pd.DataFrame,
                              daily_df: pd.DataFrame,
                              h4_df: pd.DataFrame,
                              m15_df: pd.DataFrame) -> MultiTimeframeBias:
        """
        Analyze bias across all timeframes.
        
        Args:
            weekly_df: Weekly OHLC data
            daily_df: Daily OHLC data
            h4_df: 4-hour OHLC data
            m15_df: 15-minute OHLC data
            
        Returns:
            MultiTimeframeBias object
        """
        # Analyze each timeframe
        weekly_bias = self.analyze_timeframe(weekly_df, "Weekly")
        daily_bias = self.analyze_timeframe(daily_df, "Daily")
        h4_bias = self.analyze_timeframe(h4_df, "4H")
        m15_bias = self.analyze_timeframe(m15_df, "15M")
        
        # Determine aligned trend
        aligned_trend = self._determine_aligned_trend(
            weekly_bias, daily_bias, h4_bias, m15_bias
        )
        
        # Calculate alignment strength
        alignment_strength = self._calculate_alignment_strength(
            weekly_bias, daily_bias, h4_bias, m15_bias, aligned_trend
        )
        
        # Check for counter-trend opportunity
        counter_trend_opportunity = self._check_counter_trend_opportunity(
            weekly_bias, daily_bias, h4_bias, m15_bias
        )
        
        # Determine best entry timeframe
        entry_tf = self._determine_entry_timeframe(
            weekly_bias, daily_bias, h4_bias, m15_bias
        )
        
        return MultiTimeframeBias(
            weekly_bias=weekly_bias,
            daily_bias=daily_bias,
            h4_bias=h4_bias,
            m15_bias=m15_bias,
            aligned_trend=aligned_trend,
            alignment_strength=alignment_strength,
            counter_trend_opportunity=counter_trend_opportunity,
            recommended_entry_timeframe=entry_tf
        )
    
    def _determine_aligned_trend(self, weekly: TimeframeBias,
                                  daily: TimeframeBias,
                                  h4: TimeframeBias,
                                  m15: TimeframeBias) -> TrendDirection:
        """Determine the aligned trend across timeframes"""
        trends = [weekly.trend, daily.trend, h4.trend, m15.trend]
        
        bullish_count = sum(1 for t in trends if t == TrendDirection.BULLISH)
        bearish_count = sum(1 for t in trends if t == TrendDirection.BEARISH)
        
        if bullish_count >= 3:
            return TrendDirection.BULLISH
        elif bearish_count >= 3:
            return TrendDirection.BEARISH
        else:
            # Default to weekly bias if mixed
            return weekly.trend
    
    def _calculate_alignment_strength(self, weekly: TimeframeBias,
                                       daily: TimeframeBias,
                                       h4: TimeframeBias,
                                       m15: TimeframeBias,
                                       aligned_trend: TrendDirection) -> float:
        """Calculate how well timeframes are aligned"""
        if aligned_trend == TrendDirection.NEUTRAL:
            return 0.0
        
        aligned_count = 0
        total_weight = 0
        
        for tf in [weekly, daily, h4, m15]:
            weight = self._get_timeframe_weight(tf.timeframe)
            total_weight += weight
            
            if tf.trend == aligned_trend:
                aligned_count += weight * (tf.strength.value / BiasStrength.STRONG.value)
        
        return aligned_count / total_weight if total_weight > 0 else 0.0
    
    def _get_timeframe_weight(self, timeframe: str) -> float:
        """Get weight for timeframe (higher TF = more weight)"""
        weights = {
            "Weekly": 4.0,
            "Daily": 3.0,
            "4H": 2.0,
            "15M": 1.0
        }
        return weights.get(timeframe, 1.0)
    
    def _check_counter_trend_opportunity(self, weekly: TimeframeBias,
                                         daily: TimeframeBias,
                                         h4: TimeframeBias,
                                         m15: TimeframeBias) -> bool:
        """
        Check if there's a counter-trend trading opportunity.
        
        Counter-trend opportunity exists when:
        1. Higher timeframe shows clear trend (bias)
        2. Lower timeframe shows signs of exhaustion/reversal
        3. Multiple timeframes align for reversal
        """
        # Weekly should have clear bias
        if weekly.strength == BiasStrength.NEUTRAL:
            return False
        
        # Check for reversal signs in lower timeframes
        reversal_signals = 0
        
        # Check 4H for reversal
        if self._shows_reversal_signs(h4):
            reversal_signals += 1
        
        # Check 15M for reversal
        if self._shows_reversal_signs(m15):
            reversal_signals += 1
        
        # Check if lower timeframes are counter to weekly
        counter_to_weekly = 0
        for tf in [daily, h4, m15]:
            if tf.trend != weekly.trend and tf.trend != TrendDirection.NEUTRAL:
                counter_to_weekly += 1
        
        return reversal_signals >= 1 and counter_to_weekly >= 1
    
    def _shows_reversal_signs(self, bias: TimeframeBias) -> bool:
        """Check if a timeframe shows reversal signs"""
        # Double BoS in opposite direction
        if bias.last_bos_direction is not None:
            if bias.trend == TrendDirection.BULLISH and bias.last_bos_direction == TrendDirection.BEARISH:
                return True
            elif bias.trend == TrendDirection.BEARISH and bias.last_bos_direction == TrendDirection.BULLISH:
                return True
        
        # Weakening structure
        if bias.trend == TrendDirection.BULLISH:
            return bias.lower_lows >= 2
        elif bias.trend == TrendDirection.BEARISH:
            return bias.lower_highs >= 2
        
        return False
    
    def _determine_entry_timeframe(self, weekly: TimeframeBias,
                                   daily: TimeframeBias,
                                   h4: TimeframeBias,
                                   m15: TimeframeBias) -> str:
        """Determine the best entry timeframe"""
        # For counter-trend, we typically enter on 15M or 4H
        
        # Check if 15M has reversal signal
        if self._shows_reversal_signs(m15):
            return "15M"
        
        # Check if 4H has reversal signal
        if self._shows_reversal_signs(h4):
            return "4H"
        
        # Default to 4H
        return "4H"
    
    def get_market_summary(self, mtf_bias: MultiTimeframeBias) -> dict:
        """Get a summary of the market analysis"""
        summary = {
            'macro_trend': mtf_bias.weekly_bias.trend.value,
            'macro_strength': mtf_bias.weekly_bias.strength.name,
            'current_trend': mtf_bias.aligned_trend.value,
            'alignment': f"{mtf_bias.alignment_strength:.1%}",
            'counter_trend_setup': mtf_bias.counter_trend_opportunity,
            'entry_timeframe': mtf_bias.recommended_entry_timeframe,
            'bias_details': mtf_bias.to_dict()
        }
        
        return summary


class CounterTrendBiasAnalyzer(BiasAnalyzer):
    """
    Specialized analyzer for counter-trend trading opportunities.
    
    Focuses on identifying when to fade moves against the trend.
    """
    
    def __init__(self, swing_lookback: int = 10):
        super().__init__(swing_lookback)
    
    def find_counter_trend_setup(self,
                                  weekly_df: pd.DataFrame,
                                  daily_df: pd.DataFrame,
                                  h4_df: pd.DataFrame,
                                  m15_df: pd.DataFrame) -> Dict:
        """
        Find counter-trend trading setup.
        
        Returns:
            Dictionary with setup details
        """
        mtf_bias = self.analyze_all_timeframes(
            weekly_df, daily_df, h4_df, m15_df
        )
        
        setup = {
            'found': False,
            'bias': None,
            'entry_zone': None,
            'stop_level': None,
            'target': None,
            'rr_ratio': 0.0,
            'confidence': 0.0
        }
        
        if not mtf_bias.counter_trend_opportunity:
            return setup
        
        # Found a setup
        setup['found'] = True
        setup['bias'] = mtf_bias.to_dict()
        
        # Determine entry direction (opposite to weekly)
        entry_direction = TrendDirection.BEARISH if mtf_bias.weekly_bias.trend == TrendDirection.BULLISH else TrendDirection.BULLISH
        
        # Find entry zone on 15M
        m15_zones = self.zone_detector.detect_zones_from_swings(m15_df)
        
        if entry_direction == TrendDirection.BULLISH:
            # Looking for demand zone below
            entry_zones = [z for z in m15_zones if z.zone_type == ZoneType.DEMAND]
            if entry_zones:
                best_zone = max(entry_zones, key=lambda z: z.strength)
                setup['entry_zone'] = best_zone.to_dict()
                # Stop below the zone
                setup['stop_level'] = best_zone.low - (best_zone.range_size * 0.1)
                # Target: old higher high
                recent_highs = self.swing_detector.get_swing_highs(m15_df)
                if len(recent_highs) >= 2:
                    setup['target'] = recent_highs[-1].price
        else:
            # Looking for supply zone above
            entry_zones = [z for z in m15_zones if z.zone_type == ZoneType.SUPPLY]
            if entry_zones:
                best_zone = max(entry_zones, key=lambda z: z.strength)
                setup['entry_zone'] = best_zone.to_dict()
                setup['stop_level'] = best_zone.high + (best_zone.range_size * 0.1)
                recent_lows = self.swing_detector.get_swing_lows(m15_df)
                if len(recent_lows) >= 2:
                    setup['target'] = recent_lows[-1].price
        
        # Calculate RR if we have both stop and target
        if setup['entry_zone'] and setup['stop_level'] and setup['target']:
            entry_price = setup['entry_zone']['midpoint']
            
            if entry_direction == TrendDirection.BULLISH:
                risk = entry_price - setup['stop_level']
                reward = setup['target'] - entry_price
            else:
                risk = setup['stop_level'] - entry_price
                reward = entry_price - setup['target']
            
            if risk > 0:
                setup['rr_ratio'] = reward / risk
        
        # Confidence based on alignment
        setup['confidence'] = mtf_bias.alignment_strength
        
        return setup


if __name__ == "__main__":
    # Example usage
    import pandas as pd
    import numpy as np
    
    # Generate sample data
    np.random.seed(42)
    
    # Weekly data
    weekly_dates = pd.date_range(start="2023-01-01", periods=52, freq="W")
    weekly_close = 100 + np.cumsum(np.random.randn(52) * 2)
    
    # Daily data
    daily_dates = pd.date_range(start="2023-01-01", periods=365, freq="D")
    daily_close = 100 + np.cumsum(np.random.randn(365) * 0.5)
    
    # Create OHLC for each
    def create_ohlc(closes, dates):
        return pd.DataFrame({
            'open': closes + np.random.randn(len(closes)) * 0.1,
            'high': closes + np.abs(np.random.randn(len(closes)) * 0.3),
            'low': closes - np.abs(np.random.randn(len(closes)) * 0.3),
            'close': closes
        }, index=dates)
    
    weekly_df = create_ohlc(weekly_close, weekly_dates)
    daily_df = create_ohlc(daily_close, daily_dates)
    
    # 4H data (simplified - just extend daily)
    h4_dates = pd.date_range(start="2023-01-01", periods=1000, freq="4H")
    h4_close = 100 + np.cumsum(np.random.randn(1000) * 0.1)
    h4_df = create_ohlc(h4_close, h4_dates)
    
    # 15M data
    m15_dates = pd.date_range(start="2023-01-01", periods=4000, freq="15min")
    m15_close = 100 + np.cumsum(np.random.randn(4000) * 0.02)
    m15_df = create_ohlc(m15_close, m15_dates)
    
    # Analyze
    analyzer = BiasAnalyzer()
    
    print("Analyzing Weekly Bias:")
    weekly_bias = analyzer.analyze_timeframe(weekly_df, "Weekly")
    print(f"  Trend: {weekly_bias.trend.value}")
    print(f"  Strength: {weekly_bias.strength.name}")
    print(f"  HH: {weekly_bias.higher_highs}, HL: {weekly_bias.higher_lows}")
    print(f"  LH: {weekly_bias.lower_highs}, LL: {weekly_bias.lower_lows}")
    
    print("\nAnalyzing Daily Bias:")
    daily_bias = analyzer.analyze_timeframe(daily_df, "Daily")
    print(f"  Trend: {daily_bias.trend.value}")
    print(f"  Strength: {daily_bias.strength.name}")
    
    print("\nFull Multi-Timeframe Analysis:")
    mtf = analyzer.analyze_all_timeframes(weekly_df, daily_df, h4_df, m15_df)
    print(f"  Aligned Trend: {mtf.aligned_trend.value}")
    print(f"  Alignment Strength: {mtf.alignment_strength:.1%}")
    print(f"  Counter-Trend Opportunity: {mtf.counter_trend_opportunity}")
    print(f"  Recommended Entry TF: {mtf.recommended_entry_timeframe}")
    
    # Find counter-trend setup
    ct_analyzer = CounterTrendBiasAnalyzer()
    setup = ct_analyzer.find_counter_trend_setup(weekly_df, daily_df, h4_df, m15_df)
    
    print(f"\nCounter-Trend Setup Found: {setup['found']}")
    if setup['found']:
        print(f"  Confidence: {setup['confidence']:.1%}")
        print(f"  RR Ratio: {setup['rr_ratio']:.2f}")
