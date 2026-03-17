"""
Point of Interest (POI) / Zone Detection Module
Identifies supply and demand zones for counter-trend trading
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from enum import Enum

from .swing_detection import SwingDetector, SwingPoint, SwingType
from .break_of_structure import BreakOfStructureDetector, TrendDirection


class ZoneType(Enum):
    """Types of trading zones"""
    DEMAND = "demand"  # Buy zone (price historically bounces up)
    SUPPLY = "supply"  # Sell zone (price historically drops down)
    PREMIUM = "premium"  # Upper half of range (sell area)
    DISCOUNT = "discount"  # Lower half of range (buy area)
    EQUILIBRIUM = "equilibrium"  # Midpoint


@dataclass
class Zone:
    """Represents a trading zone"""
    zone_type: ZoneType
    high: float
    low: float
    start_index: int
    end_index: int
    strength: float = 1.0
    touches: int = 0
    last_touch_price: Optional[float] = None
    last_touch_index: Optional[int] = None
    flipped: bool = False
    original_type: Optional[ZoneType] = None
    liquidity_below: bool = False  # For demand zones
    liquidity_above: bool = False  # For supply zones
    has_structure_confluence: bool = False
    
    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2
    
    @property
    def range_size(self) -> float:
        return self.high - self.low
    
    def contains(self, price: float, buffer: float = 0.0) -> bool:
        """Check if price is within the zone"""
        return self.low - buffer <= price <= self.high + buffer
    
    def is_above_equilibrium(self, equilibrium: float) -> bool:
        """Check if zone is above equilibrium"""
        return self.midpoint > equilibrium
    
    def is_below_equilibrium(self, equilibrium: float) -> bool:
        """Check if zone is below equilibrium"""
        return self.midpoint < equilibrium
    
    def to_dict(self) -> dict:
        return {
            'zone_type': self.zone_type.value,
            'high': self.high,
            'low': self.low,
            'midpoint': self.midpoint,
            'range_size': self.range_size,
            'strength': self.strength,
            'touches': self.touches,
            'flipped': self.flipped,
            'has_structure_confluence': self.has_structure_confluence
        }


class ZoneDetector:
    """
    Detects and manages trading zones (supply and demand).
    
    Zones are areas where price has historically shown reaction,
    indicating potential reversal points for counter-trend trading.
    """
    
    def __init__(self, swing_detector: Optional[SwingDetector] = None,
                 zone_buffer_percent: float = 0.1):
        """
        Initialize zone detector.
        
        Args:
            swing_detector: Optional SwingDetector instance
            zone_buffer_percent: Buffer around swing points as % of price
        """
        self.swing_detector = swing_detector or SwingDetector()
        self.zone_buffer_percent = zone_buffer_percent
    
    def detect_zones_from_swings(self, df: pd.DataFrame, 
                                  zone_type: ZoneType = None) -> List[Zone]:
        """
        Detect zones based on swing points.
        
        Args:
            df: DataFrame with OHLC data
            zone_type: Optional filter for zone type
            
        Returns:
            List of Zone objects
        """
        swings = self.swing_detector.detect_swings(df)
        
        if len(swings) < 2:
            return []
        
        zones = []
        
        # Group swings by type to find reaction zones
        highs = [s for s in swings if s.swing_type == SwingType.HIGH]
        lows = [s for s in swings if s.swing_type == SwingType.LOW]
        
        # Create demand zones from swing lows
        demand_zones = self._create_demand_zones(df, lows)
        zones.extend(demand_zones)
        
        # Create supply zones from swing highs
        supply_zones = self._create_supply_zones(df, highs)
        zones.extend(supply_zones)
        
        # Filter by zone type if specified
        if zone_type:
            zones = [z for z in zones if z.zone_type == zone_type]
        
        return sorted(zones, key=lambda z: z.strength, reverse=True)
    
    def _create_demand_zones(self, df: pd.DataFrame, 
                             swing_lows: List[SwingPoint]) -> List[Zone]:
        """Create demand zones from swing lows"""
        zones = []
        
        for i in range(len(swing_lows) - 1):
            low = swing_lows[i]

            # Calculate buffer
            buffer = low.price * (self.zone_buffer_percent / 100)

            # Find the next swing high after this low (search all swings, not just lows)
            next_high = None
            for j in range(i + 1, len(swing_lows)):
                if swing_lows[j].swing_type == SwingType.LOW and swing_lows[j].price > low.price:
                    # Use the higher price as the "top" of the demand zone
                    next_high = swing_lows[j]
                    break
            
            if next_high:
                # Calculate zone strength based on the move up from this low
                move_up = (next_high.price - low.price) / low.price * 100
                strength = min(1.0, move_up / 5.0)  # Normalize to 0-1
                
                zone = Zone(
                    zone_type=ZoneType.DEMAND,
                    high=low.price + buffer,
                    low=low.price - buffer * 2,  # Larger buffer below
                    start_index=low.index,
                    end_index=next_high.index,
                    strength=strength,
                    touches=1
                )
                zones.append(zone)
        
        return zones
    
    def _create_supply_zones(self, df: pd.DataFrame,
                             swing_highs: List[SwingPoint]) -> List[Zone]:
        """Create supply zones from swing highs"""
        zones = []
        
        for i in range(len(swing_highs) - 1):
            high = swing_highs[i]
            
            # Calculate buffer
            buffer = high.price * (self.zone_buffer_percent / 100)
            
            # Find the next swing low after this high
            next_low = None
            for j in range(i + 1, len(swing_highs)):
                if swing_highs[j].swing_type == SwingType.LOW:
                    next_low = swing_highs[j]
                    break
            
            if next_low:
                # Calculate zone strength based on the move down from this high
                move_down = (high.price - next_low.price) / high.price * 100
                strength = min(1.0, move_down / 5.0)
                
                zone = Zone(
                    zone_type=ZoneType.SUPPLY,
                    high=high.price + buffer * 2,  # Larger buffer above
                    low=high.price - buffer,
                    start_index=high.index,
                    end_index=next_low.index,
                    strength=strength,
                    touches=1
                )
                zones.append(zone)
        
        return zones
    
    def detect_premium_discount_zones(self, df: pd.DataFrame) -> Tuple[Zone, Zone, float]:
        """
        Detect premium and discount zones with equilibrium.
        
        Args:
            df: DataFrame with OHLC data
            
        Returns:
            Tuple of (Premium Zone, Discount Zone, Equilibrium Price)
        """
        swings = self.swing_detector.detect_swings(df)
        
        if len(swings) < 2:
            return None, None, 0.0
        
        # Get recent swing high and low
        recent_highs = [s for s in swings if s.swing_type == SwingType.HIGH]
        recent_lows = [s for s in swings if s.swing_type == SwingType.LOW]
        
        if not recent_highs or not recent_lows:
            return None, None, 0.0
        
        highest_high = max(recent_highs[-5:], key=lambda s: s.price).price
        lowest_low = min(recent_lows[-5:], key=lambda s: s.price).price
        
        # Calculate equilibrium
        equilibrium = (highest_high + lowest_low) / 2
        
        # Define premium and discount zones
        premium_zone = Zone(
            zone_type=ZoneType.PREMIUM,
            high=highest_high,
            low=equilibrium,
            start_index=0,
            end_index=len(df) - 1,
            strength=0.5
        )
        
        discount_zone = Zone(
            zone_type=ZoneType.DISCOUNT,
            high=equilibrium,
            low=lowest_low,
            start_index=0,
            end_index=len(df) - 1,
            strength=0.5
        )
        
        return premium_zone, discount_zone, equilibrium
    
    def detect_poi(self, df: pd.DataFrame, equilibrium: float,
                   zone_type: ZoneType = ZoneType.DISCOUNT) -> List[Zone]:
        """
        Detect Point of Interest (POI) zones.
        
        POI zones are demand zones below equilibrium for counter-trend buying,
        or supply zones above equilibrium for counter-trend selling.
        
        Args:
            df: DataFrame with OHLC data
            equilibrium: Equilibrium price level
            zone_type: Type of POI to detect
            
        Returns:
            List of POI zones
        """
        zones = self.detect_zones_from_swings(df)
        
        if zone_type == ZoneType.DISCOUNT:
            # POI for buying: demand zones below equilibrium
            poi_zones = [z for z in zones if z.zone_type == ZoneType.DEMAND 
                        and z.midpoint < equilibrium]
        elif zone_type == ZoneType.PREMIUM:
            # POI for selling: supply zones above equilibrium
            poi_zones = [z for z in zones if z.zone_type == ZoneType.SUPPLY
                        and z.midpoint > equilibrium]
        else:
            poi_zones = zones
        
        # Sort by strength
        return sorted(poi_zones, key=lambda z: z.strength, reverse=True)
    
    def validate_zone(self, df: pd.DataFrame, zone: Zone) -> Dict:
        """
        Validate a zone with multiple confluence factors.
        
        Returns:
            Dictionary with validation results
        """
        validation = {
            'flip_zone': self._check_flip_zone(df, zone),
            'structure_zone': self._check_structure_zone(df, zone),
            'liquidity_sweep': self._check_liquidity_sweep(df, zone),
            'inducement_zone': self._check_inducement(df, zone),
            'confluence_score': 0.0
        }
        
        # Calculate confluence score
        factors = [
            validation['flip_zone']['confirmed'],
            validation['structure_zone']['confirmed'],
            validation['liquidity_sweep']['confirmed'],
            validation['inducement_zone']['confirmed']
        ]
        
        validation['confluence_score'] = sum(factors) / len(factors)
        
        return validation
    
    def _check_flip_zone(self, df: pd.DataFrame, zone: Zone) -> dict:
        """Check if zone has flipped from supply to demand or vice versa"""
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        
        # Check recent price action around zone
        zone_range = df.iloc[max(0, zone.end_index - 20):zone.end_index + 1]
        
        if zone.zone_type == ZoneType.DEMAND:
            # For demand zone, check if price previously reacted from supply above
            highs_near_zone = zone_range[high_col][zone_range[low_col] <= zone.high * 1.01]
            flipped = len(highs_near_zone) > 0
        else:
            lows_near_zone = zone_range[low_col][zone_range[high_col] >= zone.low * 0.99]
            flipped = len(lows_near_zone) > 0
        
        return {'confirmed': flipped, 'details': 'Zone flipped from opposite type'}
    
    def _check_structure_zone(self, df: pd.DataFrame, zone: Zone) -> dict:
        """Check if zone aligns with structure (double bottom/top, etc.)"""
        swing_detector = SwingDetector(left_bars=5, right_bars=5)
        swings = swing_detector.detect_swings(df)
        
        # Check for double bottom/top near zone
        if zone.zone_type == ZoneType.DEMAND:
            lows_near_zone = [s for s in swings 
                             if s.swing_type == SwingType.LOW 
                             and zone.contains(s.price)]
            has_structure = len(lows_near_zone) >= 2
        else:
            highs_near_zone = [s for s in swings 
                              if s.swing_type == SwingType.HIGH 
                              and zone.contains(s.price)]
            has_structure = len(highs_near_zone) >= 2
        
        return {'confirmed': has_structure, 
                'details': f'Double {"bottom" if zone.zone_type == ZoneType.DEMAND else "top"} found'}
    
    def _check_liquidity_sweep(self, df: pd.DataFrame, zone: Zone) -> dict:
        """Check if liquidity has been swept above/below the zone"""
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        
        if zone.zone_type == ZoneType.DEMAND:
            # Check if price swept below the zone
            lows_below = df[low_col][df[low_col] < zone.low - (zone.range_size * 0.1)]
            swept = len(lows_below) > 0
            zone.liquidity_below = swept
        else:
            highs_above = df[high_col][df[high_col] > zone.high + (zone.range_size * 0.1)]
            swept = len(highs_above) > 0
            zone.liquidity_above = swept
        
        return {'confirmed': swept, 
                'details': f'Liquidity {"below" if zone.zone_type == ZoneType.DEMAND else "above"} zone swept'}
    
    def _check_inducement(self, df: pd.DataFrame, zone: Zone) -> dict:
        """Check for inducement zones that validate this zone"""
        all_zones = self.detect_zones_from_swings(df)
        
        if zone.zone_type == ZoneType.DEMAND:
            # Look for supply zone above that was induced
            supply_above = [z for z in all_zones 
                           if z.zone_type == ZoneType.SUPPLY 
                           and z.low > zone.high]
            has_inducement = len(supply_above) > 0
        else:
            demand_below = [z for z in all_zones 
                           if z.zone_type == ZoneType.DEMAND 
                           and z.high < zone.low]
            has_inducement = len(demand_below) > 0
        
        return {'confirmed': has_inducement,
                'details': f'Inducement zone {"above" if zone.zone_type == ZoneType.DEMAND else "below"} found'}
    
    def refine_zone(self, df: pd.DataFrame, zone: Zone) -> Zone:
        """
        Refine zone based on price reaction and validation.
        
        Args:
            df: DataFrame with OHLC data
            zone: Original zone
            
        Returns:
            Refined zone with updated properties
        """
        validation = self.validate_zone(df, zone)
        
        # Update zone properties based on validation
        zone.has_structure_confluence = validation['structure_zone']['confirmed']
        
        # Adjust zone boundaries based on rejection candles
        high_col = 'high' if 'high' in df.columns else 'High'
        low_col = 'low' if 'low' in df.columns else 'Low'
        
        # Find the most recent rejection within the zone
        zone_candles = df.iloc[max(0, zone.end_index - 10):zone.end_index + 1]
        
        if zone.zone_type == ZoneType.DEMAND:
            # Find the low of the rejection
            rejection_low = zone_candles[low_col].min()
            zone.low = min(zone.low, rejection_low)
            zone.touches += 1
            zone.last_touch_price = rejection_low
        else:
            rejection_high = zone_candles[high_col].max()
            zone.high = max(zone.high, rejection_high)
            zone.touches += 1
            zone.last_touch_price = rejection_high
        
        zone.last_touch_index = zone.end_index
        
        return zone
    
    def merge_overlapping_zones(self, zones: List[Zone]) -> List[Zone]:
        """Merge overlapping zones of the same type"""
        if len(zones) <= 1:
            return zones
        
        merged = []
        zones = sorted(zones, key=lambda z: z.strength, reverse=True)
        
        while zones:
            current = zones.pop(0)
            remaining = []
            
            for other in zones:
                # Check for overlap
                if (current.high >= other.low and current.low <= other.high):
                    # Merge: keep the stronger zone, expand range
                    new_high = max(current.high, other.high)
                    new_low = min(current.low, other.low)
                    current.high = new_high
                    current.low = new_low
                    current.touches += other.touches
                    current.strength = max(current.strength, other.strength)
                else:
                    remaining.append(other)
            
            merged.append(current)
            zones = remaining
        
        return merged


class MultiTimeframeZoneManager:
    """
    Manages zones across multiple timeframes.
    
    For counter-trend trading, we need:
    - Weekly POI (macro reference)
    - Daily refined POI
    - 4H POI for entry
    - 15M POI for precise entry
    """
    
    def __init__(self):
        """Initialize multi-timeframe zone manager"""
        self.zone_detector = ZoneDetector()
        self.weekly_zones = []
        self.daily_zones = []
        self.h4_zones = []
        self.m15_zones = []
    
    def collect_all_timeframe_zones(self, 
                                   weekly_df: pd.DataFrame,
                                   daily_df: pd.DataFrame,
                                   h4_df: pd.DataFrame,
                                   m15_df: pd.DataFrame) -> dict:
        """
        Collect zones from all timeframes.
        
        Args:
            weekly_df: Weekly OHLC data
            daily_df: Daily OHLC data
            h4_df: 4-hour OHLC data
            m15_df: 15-minute OHLC data
            
        Returns:
            Dictionary with zones from each timeframe
        """
        # Weekly zones (macro reference)
        self.weekly_zones = self.zone_detector.detect_zones_from_swings(weekly_df)
        
        # Daily zones (refinement)
        self.daily_zones = self.zone_detector.detect_zones_from_swings(daily_df)
        
        # 4H zones (entry structure)
        self.h4_zones = self.zone_detector.detect_zones_from_swings(h4_df)
        
        # 15M zones (precise entry)
        self.m15_zones = self.zone_detector.detect_zones_from_swings(m15_df)
        
        return {
            'weekly': [z.to_dict() for z in self.weekly_zones],
            'daily': [z.to_dict() for z in self.daily_zones],
            'h4': [z.to_dict() for z in self.h4_zones],
            'm15': [z.to_dict() for z in self.m15_zones]
        }
    
    def get_aligned_zones(self) -> List[Zone]:
        """
        Find zones that align across timeframes.
        
        Returns:
            List of aligned zones with higher confidence
        """
        aligned = []
        
        # Check if daily zone aligns with weekly
        for daily_zone in self.daily_zones:
            for weekly_zone in self.weekly_zones:
                if (daily_zone.zone_type == weekly_zone.zone_type and
                    self._zones_overlap(daily_zone, weekly_zone)):
                    # Boost strength for alignment
                    daily_zone.strength *= 1.5
                    daily_zone.has_structure_confluence = True
                    aligned.append(daily_zone)
        
        # Check if 4H zone aligns with daily
        for h4_zone in self.h4_zones:
            for daily_zone in self.daily_zones:
                if (h4_zone.zone_type == daily_zone.zone_type and
                    self._zones_overlap(h4_zone, daily_zone)):
                    h4_zone.strength *= 1.5
                    h4_zone.has_structure_confluence = True
                    aligned.append(h4_zone)
        
        return aligned
    
    def _zones_overlap(self, zone1: Zone, zone2: Zone) -> bool:
        """Check if two zones overlap"""
        return zone1.high >= zone2.low and zone2.high >= zone1.low
    
    def get_best_entry_zone(self) -> Tuple[Optional[Zone], str]:
        """
        Get the best entry zone based on confluence.
        
        Returns:
            Tuple of (best zone, timeframe)
        """
        aligned = self.get_aligned_zones()
        
        if not aligned:
            # Fall back to strongest 15M zone
            if self.m15_zones:
                return self.m15_zones[0], '15M'
            return None, 'none'
        
        # Return the strongest aligned zone
        best = max(aligned, key=lambda z: z.strength)
        
        if best in self.m15_zones:
            return best, '15M'
        elif best in self.h4_zones:
            return best, '4H'
        else:
            return best, 'Daily'


if __name__ == "__main__":
    # Example usage
    import pandas as pd
    import numpy as np
    
    # Generate sample data
    np.random.seed(42)
    dates = pd.date_range(start="2024-01-01", periods=500, freq="1H")
    
    close_prices = 100 + np.cumsum(np.random.randn(500) * 0.5)
    high_prices = close_prices + np.abs(np.random.randn(500) * 0.3)
    low_prices = close_prices - np.abs(np.random.randn(500) * 0.3)
    
    df = pd.DataFrame({
        'open': close_prices + np.random.randn(500) * 0.1,
        'high': high_prices,
        'low': low_prices,
        'close': close_prices
    }, index=dates)
    
    # Detect zones
    detector = ZoneDetector()
    zones = detector.detect_zones_from_swings(df)
    
    print(f"Detected {len(zones)} zones:")
    for zone in zones[:5]:  # Show first 5
        print(f"  {zone.zone_type.value}: {zone.low:.2f} - {zone.high:.2f} (strength: {zone.strength:.2f})")
    
    # Detect premium/discount
    premium, discount, eq = detector.detect_premium_discount_zones(df)
    if premium:
        print(f"\nEquilibrium: {eq:.2f}")
        print(f"Premium Zone: {premium.low:.2f} - {premium.high:.2f}")
        print(f"Discount Zone: {discount.low:.2f} - {discount.high:.2f}")
    
    # Get POI for counter-trend buying
    pois = detector.detect_poi(df, eq, ZoneType.DISCOUNT)
    print(f"\nDetected {len(pois)} POI (discount zones for buying)")
    
    # Validate first POI
    if pois:
        validation = detector.validate_zone(df, pois[0])
        print(f"\nPOI Validation:")
        for key, value in validation.items():
            if isinstance(value, dict):
                print(f"  {key}: {value}")
