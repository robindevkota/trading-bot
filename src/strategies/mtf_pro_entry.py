"""
MTF PRO Entry Signal Generator — 3-Timeframe Multi-Model  v2

Timeframe structure:
  1D  (resampled from 4H)  — Trend bias via EMA21/EMA50 + daily RSI
  4H                       — POI identification (Order Block zones)
  15M                      — Entry trigger (scored multi-model confluence)

Direction scanning:
  Pro-trend    — trade WITH daily bias, 4H structure HH/HL (LONG) or LH/LL (SHORT)
  Counter-trend— trade AGAINST daily bias when 4H shows the opposite structure

Entry scoring (require ≥ min_entry_score for pro-trend, ≥ ct_min_score for counter):
  CHoCH  (+2): proper Change of Character — 15M lower-high broken (LONG) or
               higher-low broken (SHORT). Strongest reversal confirmation.
  Sweep  (+2): liquidity sweep of SSL/BSL with body close-back.
  BOS    (+1): any 15M swing-high/low break (weaker, needs other confluence).
  EMA20  (+1): 15M wick to EMA20 with close-back (only if sweep didn't fire).
  Flip   (+1): OB zone coincides with a previous structural swing level.

v3 — Photon LQ entry models (all default OFF; enable via config):
  HTFSweep (+2, use_htf_sweep):  15M bar wicks through the 4H zone's own
               low/high (the HTF structural level) and body-closes back inside.
               Photon EM 1 / EM 4 — "HTF Leg / POI Sweep".
  EqL bonus (+1, use_eql_bonus): the level taken by the Sweep model is an
               equal-lows/highs cluster (2+ swing points within eql_tol_pips) —
               engineered liquidity. Photon EM 2b — "Equal Lows/Highs".
  Inducement filter (require_inducement): only allow entry if price built AND
               swept a pullback swing (PBID) on the near side of the zone before
               arriving. Photon EM 2a — inducement/trap requirement.

Stop Loss  : structural — below OB zone_low (LONG) with ATR buffer
TP1 / TP2  : 1.5R / 3.0R  (close 50% at TP1, move SL to breakeven, run TP2)
"""

import logging
import pandas as pd
import numpy as np
from typing import List, Optional, Dict, Tuple
from datetime import datetime

from .counter_trend_entry import EntrySignal, EntryType, SignalDirection


class MTFProEntryGenerator:
    """
    3-Timeframe Multi-Model Entry Generator with dual-direction capability.

    Call generate_signals(symbol, h4_df, m15_df) each bar scan.
    Returns 0 or 1 EntrySignal per call (highest scoring).
    """

    DEFAULT_CONFIG: Dict = {
        # 1D bias EMAs (applied on daily-resampled 4H data)
        'd_fast_ema':  21,
        'd_slow_ema':  50,

        # 4H Order Block detection
        'h4_ob_lookback':          80,   # bars back to scan (~13 days of 4H)
        'h4_ob_displacement_pips': 15,   # min pips displacement after OB candle
        'h4_ob_body_ratio':        1.5,  # displacement body must be >= ratio x OB body
        'h4_zone_tol_pips':        10,   # tolerance for "inside zone" check (pips)

        # 1H Order Block detection (nested inside 4H zone)
        'h1_ob_lookback':          80,   # bars back to scan on 1H (~3 days)
        'h1_ob_displacement_pips': 8,    # min pips displacement on 1H
        'h1_zone_tol_pips':        5,    # tolerance for "inside 1H zone" check
        'use_h1_ob':               False, # enable 1H intermediate OB (presence filter, SL stays at 4H)

        # 15M EMA
        'm15_ema': 20,

        # Entry model toggles
        'use_choch':        True,
        'use_sweep':        True,
        'use_ema_bounce':   True,
        'use_bos':          True,   # simple BOS (adds 1 pt, supplements CHoCH/sweep)

        # CHoCH / BOS model
        'choch_lookback':    20,   # 15M bars to search for swing
        'choch_swing_pivot':  3,   # bars required each side to confirm swing

        # Sweep model
        'min_wick_pips':      5,
        'require_body_close': True,
        'body_close_pct':     0.50,

        # v3 — Photon LQ entry models (all default OFF)
        'use_htf_sweep':       False,  # EM4: sweep of the 4H zone's own low/high (+2)
        'use_eql_bonus':       False,  # EM2b: swept level is equal lows/highs (+1)
        'eql_tol_pips':        3,      # cluster tolerance for equal lows/highs
        'require_inducement':  False,  # EM2a: PBID must be built + swept before entry
        'inducement_lookback': 48,     # 15M bars (~12h) to search for inducement swing

        # RSI gates (15M)
        'rsi_period':    14,
        'long_min_rsi':  45,   # LONG: 15M RSI must be ≥ 45
        'long_max_rsi':  70,   # LONG: 15M RSI must be ≤ 70 (not chasing rally)
        'short_min_rsi': 35,   # SHORT: 15M RSI must be ≥ 35 (not already oversold)
        'short_max_rsi': 65,   # SHORT: 15M RSI must be ≤ 65 (not chasing drop)

        # Entry confluence scoring
        'min_entry_score':  2,   # min score for pro-trend entry
        'ct_min_score':     4,   # min score for counter-trend entry (needs more confluence)

        # Counter-trend enable
        'use_counter_trend': True,

        # Direction filter (disable SHORT to go LONG-only)
        'use_short_direction': False,

        # SL / TP
        'atr_period':    14,
        'atr_buffer':    0.75,   # SL = zone_anchor - atr_buffer * 4H_ATR
        'min_sl_pips':   15,
        'max_sl_pct':    0.02,
        'tp1_rr':        1.5,
        'tp2_rr':        3.0,
        'tp1_close_pct': 0.50,

        # 4H ADX trend-strength filter (0 = disabled)
        'h4_adx_min': 20,

        # Symbol calibration
        'pip_size': 0.0001,   # 0.0001 for forex; 0.01 for gold
    }

    def __init__(self, config: Optional[Dict] = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def generate_signals(self,
                         symbol: str,
                         h4_df: pd.DataFrame,
                         m15_df: pd.DataFrame,
                         h1_df: Optional[pd.DataFrame] = None) -> List[EntrySignal]:
        """
        Run the full 4-TF scan for both pro-trend and counter-trend opportunities.

        h1_df is optional — if provided and use_h1_ob=True, the 1H OB layer is active.
        Falls back to v1 (4H zone direct entry) when h1_df is absent or empty.

        Returns the highest-scoring signal (0 or 1 per call).
        """
        if len(h4_df) < 50 or len(m15_df) < 30:
            return []

        # Normalise h1 — treat missing/empty as None
        if h1_df is not None and (h1_df.empty or len(h1_df) < 10):
            h1_df = None

        # ── Step 1: Daily bias (2 confluences: EMA cross + RSI) ────────
        daily_df = self._resample_daily(h4_df)
        if len(daily_df) < 10:
            return []
        bias = self._daily_bias(daily_df)
        if bias is None:
            return []   # Neutral — no dominant trend

        # ── Step 2: 4H ADX trend-strength filter ───────────────────────
        adx_threshold = self.config.get('h4_adx_min', 20)
        if adx_threshold > 0 and self._adx(h4_df, period=14) < adx_threshold:
            return []

        # ── Step 3: Scan pro-trend AND counter-trend directions ─────────
        ct_bias = SignalDirection.SHORT if bias == SignalDirection.LONG else SignalDirection.LONG

        best_signal: Optional[EntrySignal] = None
        best_score: int = 0

        # Pro-trend: 4H structure must confirm daily bias
        if self._h4_trend_confirmed(h4_df, bias):
            sig, score = self._scan_direction(symbol, h4_df, m15_df, bias,
                                              is_counter=False, h1_df=h1_df)
            if sig and score > best_score:
                best_signal, best_score = sig, score

        # Counter-trend: 4H structure must confirm the OPPOSITE direction
        if self.config.get('use_counter_trend', True):
            if self._h4_trend_confirmed(h4_df, ct_bias):
                sig, score = self._scan_direction(symbol, h4_df, m15_df, ct_bias,
                                                  is_counter=True, h1_df=h1_df)
                if sig and score > best_score:
                    best_signal, best_score = sig, score

        if best_signal is None:
            return []

        self.logger.info(
            f"SIGNAL {symbol} {best_signal.phase} @ {best_signal.entry_price:.5f}  "
            f"SL={best_signal.stop_loss:.5f}  score={best_score}"
        )
        return [best_signal]

    # ------------------------------------------------------------------ #
    # Direction scanner (shared by pro and counter-trend)                #
    # ------------------------------------------------------------------ #

    def _scan_direction(self,
                        symbol: str,
                        h4_df: pd.DataFrame,
                        m15_df: pd.DataFrame,
                        direction: SignalDirection,
                        is_counter: bool,
                        h1_df: Optional[pd.DataFrame] = None) -> Tuple[Optional[EntrySignal], int]:
        """
        Find entry zones and score all models for a given direction.

        SL is ALWAYS anchored to the 4H OB zone (structural stop).
        When h1_df is provided and use_h1_ob=True, a 1H OB nested inside
        the 4H zone acts as a presence filter / entry timing qualifier only —
        it does NOT change the SL level. This keeps v1 RR intact while
        adding 1H confluence as a label.

        Returns (best_signal, score) or (None, 0).
        """
        # Skip SHORT entries if disabled
        if direction == SignalDirection.SHORT and not self.config.get('use_short_direction', True):
            return None, 0

        close_m15 = m15_df['close'] if 'close' in m15_df.columns else m15_df['Close']
        rsi_now = float(self._rsi(close_m15, self.config['rsi_period']).iloc[-1])

        # ── 15M RSI gates ──
        if direction == SignalDirection.LONG:
            if rsi_now < self.config.get('long_min_rsi', 45):
                return None, 0
            if rsi_now > self.config.get('long_max_rsi', 70):
                return None, 0
        else:
            if rsi_now < self.config.get('short_min_rsi', 35):
                return None, 0
            if rsi_now > self.config.get('short_max_rsi', 65):
                return None, 0

        # ── Find 4H OB zones in this direction ──
        h4_zones = self._find_4h_ob_zones(h4_df, direction)
        if not h4_zones:
            return None, 0

        # ── Is price currently entering / inside a 4H zone? ──
        active_h4_zone = self._find_active_zone(m15_df, h4_zones, direction)
        if active_h4_zone is None:
            return None, 0

        # ── Optional 1H OB: presence filter only — SL stays at 4H zone ──
        # SL is always anchored to active_h4_zone['low'] (structural stop).
        # A valid 1H OB nested inside the 4H zone adds '1H-OB' to criteria
        # for logging but does NOT change the zone used by entry models.
        active_zone = active_h4_zone   # v1 + v2: SL always references 4H zone
        using_h1_ob = False
        if (self.config.get('use_h1_ob', False)
                and h1_df is not None and len(h1_df) >= 10):
            h1_zones = self._find_1h_ob_zones(h1_df, direction, active_h4_zone)
            if h1_zones:
                h1_active = self._find_active_zone(m15_df, h1_zones, direction,
                                                   tol_key='h1_zone_tol_pips')
                if h1_active:
                    using_h1_ob = True   # label only — active_zone unchanged

        # ── v3 Inducement filter (Photon EM2a): PBID built + swept before entry ──
        if self.config.get('require_inducement', False):
            if not self._inducement_swept(m15_df, active_zone, direction):
                return None, 0

        # ── Flip zone bonus ──
        is_flip = self._is_flip_zone(h4_df, active_zone, direction)
        score = 1 if is_flip else 0
        # Note: 1H OB does NOT add to score — it is a presence filter only.
        # SL is always anchored to the 4H zone regardless of 1H OB presence.

        eql_fired = False
        candidates: List[Tuple[int, EntrySignal]] = []

        # ── CHoCH (+2): proper change of character ──
        if self.config.get('use_choch', True):
            choch = self._try_choch(symbol, h4_df, m15_df, direction, active_zone)
            if choch:
                score += 2
                candidates.append((2, choch))

        # ── BOS (+1): simple structure break (only if CHoCH didn't fire) ──
        if self.config.get('use_bos', True) and not any(p == 2 for p, _ in candidates):
            bos = self._try_bos(symbol, h4_df, m15_df, direction, active_zone)
            if bos:
                score += 1
                candidates.append((1, bos))

        # ── Sweep (+2): liquidity sweep with body close-back ──
        if self.config.get('use_sweep', True):
            sweep = self._try_sweep(symbol, h4_df, m15_df, direction, active_zone)
            if sweep:
                score += 2
                candidates.append((2, sweep))
                # v3 EqL bonus (+1): swept level is an equal-lows/highs cluster
                if (self.config.get('use_eql_bonus', False)
                        and getattr(sweep, 'swept_eql', False)):
                    score += 1
                    eql_fired = True

        # ── v3 HTF POI sweep (+2): sweep of the 4H zone's own boundary (EM4) ──
        if self.config.get('use_htf_sweep', False):
            hsw = self._try_htf_sweep(symbol, h4_df, m15_df, direction, active_zone)
            if hsw:
                score += 2
                candidates.append((2, hsw))

        # ── EMA20 bounce (+1): only if sweep didn't fire ──
        if self.config.get('use_ema_bounce', True):
            if not any(s == 'Sweep' for _, s_sig in candidates
                       for s in [getattr(s_sig, 'phase', '').split('-')[-1]]):
                ema = self._try_ema_bounce(symbol, h4_df, m15_df, direction, active_zone)
                if ema:
                    score += 1
                    candidates.append((1, ema))

        if not candidates:
            return None, 0

        # ── Minimum score check ──
        min_score = (self.config.get('ct_min_score', 3)
                     if is_counter else self.config.get('min_entry_score', 2))
        if score < min_score:
            return None, 0

        # ── Pick best signal (highest pts first, then by model preference) ──
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_sig = candidates[0][1]

        # Tag signal with direction type and score
        prefix = 'CT' if is_counter else 'PT'
        model  = best_sig.phase.split('-')[-1] if '-' in best_sig.phase else best_sig.phase
        best_sig.phase = f'{prefix}-{direction.value.upper()}-{model}'
        best_sig.confidence = min(score / 6.0, 1.0)
        # Label all confluences
        best_sig.criteria_met = (
            ['1D-bias', '4H-OB']
            + (['1H-OB'] if using_h1_ob else [])
            + [f'15M-{getattr(s, "phase", "?").split("-")[-1]}' for _, s in candidates]
            + (['flip-zone'] if is_flip else [])
            + (['eql-sweep'] if eql_fired else [])
        )

        return best_sig, score

    # ------------------------------------------------------------------ #
    # Utilities: EMA, RSI, ATR, ADX                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """Compute ADX on df. Returns current ADX value (0-100)."""
        hc = 'high'  if 'high'  in df.columns else 'High'
        lc = 'low'   if 'low'   in df.columns else 'Low'
        cc = 'close' if 'close' in df.columns else 'Close'
        h = df[hc].astype(float)
        l = df[lc].astype(float)
        c = df[cc].astype(float)

        up_move   = h.diff()
        down_move = -l.diff()
        plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        tr = pd.concat(
            [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1
        ).max(axis=1)
        atr_s    = tr.ewm(span=period, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr_s.replace(0, 1e-9)
        minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_s.replace(0, 1e-9)
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        return float(dx.ewm(span=period, adjust=False).mean().iloc[-1])

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
        rs    = gain / loss.replace(0, 1e-9)
        return 100 - (100 / (1 + rs))

    def _atr(self, df: pd.DataFrame) -> float:
        hc = 'high'  if 'high'  in df.columns else 'High'
        lc = 'low'   if 'low'   in df.columns else 'Low'
        cc = 'close' if 'close' in df.columns else 'Close'
        h  = df[hc].astype(float)
        l  = df[lc].astype(float)
        c  = df[cc].astype(float)
        tr = pd.concat(
            [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1
        ).max(axis=1)
        return float(tr.ewm(span=self.config['atr_period'],
                            adjust=False).mean().iloc[-1])

    # ------------------------------------------------------------------ #
    # Step 1: Daily bias (2 confluences)                                  #
    # ------------------------------------------------------------------ #

    def _resample_daily(self, h4_df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate 4H OHLC bars → daily bars using the DatetimeIndex."""
        rename = {}
        for col in ['open', 'high', 'low', 'close']:
            if col not in h4_df.columns and col.capitalize() in h4_df.columns:
                rename[col.capitalize()] = col
        df = h4_df.rename(columns=rename) if rename else h4_df

        if not isinstance(df.index, pd.DatetimeIndex):
            return pd.DataFrame()

        return (
            df[['open', 'high', 'low', 'close']]
            .resample('D')
            .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
            .dropna()
        )

    def _daily_bias(self, daily_df: pd.DataFrame) -> Optional[SignalDirection]:
        """
        Two-confluence daily bias:
          1. EMA21 > EMA50, price above EMA21, EMA21 rising  (or inverse for SHORT)
          2. Daily RSI(14) > 50 for LONG, < 50 for SHORT

        Both confluences must agree — eliminates entries during trend exhaustion
        when RSI has already crossed back against the trend.
        """
        c  = daily_df['close'] if 'close' in daily_df.columns else daily_df['Close']
        fp = min(self.config['d_fast_ema'], len(c) - 1)
        sp = min(self.config.get('d_slow_ema', 50), len(c) - 1)

        ema_f_series = self._ema(c, fp)
        ema_s_series = self._ema(c, sp)
        ema_f_now    = float(ema_f_series.iloc[-1])
        ema_s_now    = float(ema_s_series.iloc[-1])
        slope_bars   = min(10, len(ema_f_series) - 1)
        ema_f_prev   = float(ema_f_series.iloc[-1 - slope_bars])
        last         = float(c.iloc[-1])

        ema_rising  = ema_f_now > ema_f_prev
        ema_falling = ema_f_now < ema_f_prev
        margin      = 0.001   # 0.1% buffer prevents straddling signals

        # Second confluence: daily RSI momentum
        rsi_period = min(14, len(c) - 1)
        rsi_val    = float(self._rsi(c, rsi_period).iloc[-1])

        if (last > ema_f_now * (1 + margin) and ema_rising
                and ema_f_now > ema_s_now and rsi_val > 50):
            return SignalDirection.LONG
        if (last < ema_f_now * (1 - margin) and ema_falling
                and ema_f_now < ema_s_now and rsi_val < 50):
            return SignalDirection.SHORT
        return None

    # ------------------------------------------------------------------ #
    # Step 2: 4H trend structure confirmation                            #
    # ------------------------------------------------------------------ #

    def _h4_trend_confirmed(self, h4_df: pd.DataFrame,
                            bias: SignalDirection) -> bool:
        """
        Verify 4H price structure: HH/HL for LONG, LH/LL for SHORT.
        Uses last 2 confirmed swing highs and lows (3-bar pivot, 40-bar window).
        """
        hc = 'high'  if 'high'  in h4_df.columns else 'High'
        lc = 'low'   if 'low'   in h4_df.columns else 'Low'
        pivot  = 3
        lookbk = min(40, len(h4_df) - pivot - 2)
        n      = len(h4_df)
        swing_highs, swing_lows = [], []

        for i in range(n - pivot - 1, max(pivot, n - lookbk) - 1, -1):
            if i < pivot or i >= n - pivot:
                continue
            h_v = float(h4_df[hc].iloc[i])
            l_v = float(h4_df[lc].iloc[i])
            if h_v == float(h4_df[hc].iloc[max(0, i - pivot): i + pivot + 1].max()):
                if len(swing_highs) < 2:
                    swing_highs.append(h_v)
            if l_v == float(h4_df[lc].iloc[max(0, i - pivot): i + pivot + 1].min()):
                if len(swing_lows) < 2:
                    swing_lows.append(l_v)
            if len(swing_highs) >= 2 and len(swing_lows) >= 2:
                break

        if bias == SignalDirection.LONG:
            hh = len(swing_highs) < 2 or swing_highs[0] > swing_highs[1]
            hl = len(swing_lows)  < 2 or swing_lows[0]  > swing_lows[1]
            return hh and hl
        else:
            lh = len(swing_highs) < 2 or swing_highs[0] < swing_highs[1]
            ll = len(swing_lows)  < 2 or swing_lows[0]  < swing_lows[1]
            return lh and ll

    # ------------------------------------------------------------------ #
    # Step 3: 4H Order Block zone detection                               #
    # ------------------------------------------------------------------ #

    def _find_4h_ob_zones(self, h4_df: pd.DataFrame,
                          bias: SignalDirection) -> List[Dict]:
        """
        Identify active (unmitigated) 4H Order Block zones.

        Demand zone (LONG): bearish 4H candle + strong bullish displacement
                            in next 3 bars. No 4H close below OB low since.
        Supply zone (SHORT): symmetric.

        Returns up to 3 most recent valid zones.
        """
        oc = 'open'  if 'open'  in h4_df.columns else 'Open'
        hc = 'high'  if 'high'  in h4_df.columns else 'High'
        lc = 'low'   if 'low'   in h4_df.columns else 'Low'
        cc = 'close' if 'close' in h4_df.columns else 'Close'

        lookback = min(self.config['h4_ob_lookback'], len(h4_df) - 5)
        min_disp = self.config['h4_ob_displacement_pips'] * self.config['pip_size']
        n        = len(h4_df)
        zones    = []

        for i in range(n - 4, max(n - lookback, 1), -1):
            o_i = float(h4_df[oc].iloc[i])
            h_i = float(h4_df[hc].iloc[i])
            l_i = float(h4_df[lc].iloc[i])
            c_i = float(h4_df[cc].iloc[i])

            post = h4_df.iloc[i + 1: i + 4]
            if post.empty:
                continue

            if bias == SignalDirection.LONG:
                if c_i >= o_i:
                    continue   # OB must be bearish
                if float(post[cc].max()) < h_i + min_disp:
                    continue   # insufficient displacement
                if (h4_df[cc].iloc[i + 1:] < l_i).any():
                    continue   # mitigated
                zones.append({'low': l_i, 'high': h_i, 'age': n - i})

            else:
                if c_i <= o_i:
                    continue   # OB must be bullish
                if float(post[cc].min()) > l_i - min_disp:
                    continue   # insufficient displacement
                if (h4_df[cc].iloc[i + 1:] > h_i).any():
                    continue   # mitigated
                zones.append({'low': l_i, 'high': h_i, 'age': n - i})

            if len(zones) >= 3:
                break

        return zones

    # ------------------------------------------------------------------ #
    # Step 3b: Is price entering an active zone?                         #
    # ------------------------------------------------------------------ #

    def _find_active_zone(self, m15_df: pd.DataFrame,
                          zones: List[Dict],
                          bias: SignalDirection,
                          tol_key: str = 'h4_zone_tol_pips') -> Optional[Dict]:
        """
        Return the first zone that the current 15M bar is touching or inside.
        tol_key selects which tolerance config param to use (4H or 1H).
        """
        hc = 'high'  if 'high'  in m15_df.columns else 'High'
        lc = 'low'   if 'low'   in m15_df.columns else 'Low'
        cc = 'close' if 'close' in m15_df.columns else 'Close'

        bar   = m15_df.iloc[-1]
        bar_h = float(bar[hc])
        bar_l = float(bar[lc])
        bar_c = float(bar[cc])
        tol   = self.config.get(tol_key, 10) * self.config['pip_size']

        for zone in zones:
            if bias == SignalDirection.LONG:
                if bar_l <= zone['high'] + tol and bar_c >= zone['low'] - tol:
                    return zone
            else:
                if bar_h >= zone['low'] - tol and bar_c <= zone['high'] + tol:
                    return zone
        return None

    def _find_1h_ob_zones(self, h1_df: pd.DataFrame,
                          bias: SignalDirection,
                          h4_zone: Dict) -> List[Dict]:
        """
        Identify fresh unmitigated 1H Order Block zones nested inside the 4H macro zone.

        Same detection logic as _find_4h_ob_zones() but:
          - Uses h1_ob_displacement_pips / h1_ob_lookback params
          - Only returns zones whose price range overlaps the 4H zone
            (ensures the 1H OB is a sub-zone, not a random distant level)
        """
        oc = 'open'  if 'open'  in h1_df.columns else 'Open'
        hc = 'high'  if 'high'  in h1_df.columns else 'High'
        lc = 'low'   if 'low'   in h1_df.columns else 'Low'
        cc = 'close' if 'close' in h1_df.columns else 'Close'

        lookback = min(self.config.get('h1_ob_lookback', 80), len(h1_df) - 5)
        min_disp = self.config.get('h1_ob_displacement_pips', 8) * self.config['pip_size']
        n        = len(h1_df)
        zones    = []

        # 4H zone bounds (with small tolerance) for the nesting check
        tol = self.config.get('h4_zone_tol_pips', 10) * self.config['pip_size']
        h4_low  = h4_zone['low']  - tol
        h4_high = h4_zone['high'] + tol

        for i in range(n - 4, max(n - lookback, 1), -1):
            o_i = float(h1_df[oc].iloc[i])
            h_i = float(h1_df[hc].iloc[i])
            l_i = float(h1_df[lc].iloc[i])
            c_i = float(h1_df[cc].iloc[i])

            post = h1_df.iloc[i + 1: i + 4]
            if post.empty:
                continue

            if bias == SignalDirection.LONG:
                if c_i >= o_i:
                    continue   # OB candle must be bearish
                if float(post[cc].max()) < h_i + min_disp:
                    continue   # insufficient displacement
                if (h1_df[cc].iloc[i + 1:] < l_i).any():
                    continue   # mitigated
                # Zone must overlap with (or sit inside) the 4H macro zone
                if h_i < h4_low or l_i > h4_high:
                    continue
                zones.append({'low': l_i, 'high': h_i, 'age': n - i, 'tf': '1H'})

            else:
                if c_i <= o_i:
                    continue
                if float(post[cc].min()) > l_i - min_disp:
                    continue
                if (h1_df[cc].iloc[i + 1:] > h_i).any():
                    continue
                if l_i > h4_high or h_i < h4_low:
                    continue
                zones.append({'low': l_i, 'high': h_i, 'age': n - i, 'tf': '1H'})

            if len(zones) >= 3:
                break

        return zones

    # ------------------------------------------------------------------ #
    # Flip zone bonus                                                      #
    # ------------------------------------------------------------------ #

    def _is_flip_zone(self, h4_df: pd.DataFrame,
                      zone: Dict,
                      bias: SignalDirection) -> bool:
        """
        Returns True if the OB zone coincides with a previous structural swing level,
        indicating a "flip zone" (old resistance → new support, or vice versa).

        LONG: zone mid is near a previous 4H swing HIGH (old resistance now demand)
        SHORT: zone mid is near a previous 4H swing LOW (old support now supply)
        """
        hc = 'high' if 'high' in h4_df.columns else 'High'
        lc = 'low'  if 'low'  in h4_df.columns else 'Low'
        pivot  = 3
        lookbk = min(40, len(h4_df) - pivot - 2)
        n      = len(h4_df)
        tol    = self.config.get('h4_zone_tol_pips', 10) * self.config['pip_size']
        zone_mid = (zone['low'] + zone['high']) / 2

        for i in range(n - pivot - 1, max(pivot, n - lookbk) - 1, -1):
            if i < pivot or i >= n - pivot:
                continue
            if bias == SignalDirection.LONG:
                h_v = float(h4_df[hc].iloc[i])
                if abs(h_v - zone_mid) <= tol:
                    window = h4_df[hc].iloc[max(0, i - pivot): i + pivot + 1]
                    if h_v == float(window.max()):
                        return True
            else:
                l_v = float(h4_df[lc].iloc[i])
                if abs(l_v - zone_mid) <= tol:
                    window = h4_df[lc].iloc[max(0, i - pivot): i + pivot + 1]
                    if l_v == float(window.min()):
                        return True
        return False

    # ------------------------------------------------------------------ #
    # Swing helpers                                                        #
    # ------------------------------------------------------------------ #

    def _find_swing_highs(self, df: pd.DataFrame,
                          lookbk: int, pivot: int,
                          count: int = 2) -> List[float]:
        """Return the `count` most recent confirmed 15M swing highs (newest first)."""
        hc = 'high' if 'high' in df.columns else 'High'
        n  = len(df)
        result = []
        for i in range(n - pivot - 1, max(pivot, n - lookbk) - 1, -1):
            if i < pivot or i >= n - pivot:
                continue
            h_v    = float(df[hc].iloc[i])
            window = df[hc].iloc[max(0, i - pivot): i + pivot + 1]
            if h_v == float(window.max()):
                result.append(h_v)
                if len(result) >= count:
                    break
        return result

    def _find_swing_lows(self, df: pd.DataFrame,
                         lookbk: int, pivot: int,
                         count: int = 2) -> List[float]:
        """Return the `count` most recent confirmed 15M swing lows (newest first)."""
        lc = 'low' if 'low' in df.columns else 'Low'
        n  = len(df)
        result = []
        for i in range(n - pivot - 1, max(pivot, n - lookbk) - 1, -1):
            if i < pivot or i >= n - pivot:
                continue
            l_v    = float(df[lc].iloc[i])
            window = df[lc].iloc[max(0, i - pivot): i + pivot + 1]
            if l_v == float(window.min()):
                result.append(l_v)
                if len(result) >= count:
                    break
        return result

    # ------------------------------------------------------------------ #
    # Entry Model 1: Proper CHoCH (+2 pts)                               #
    # ------------------------------------------------------------------ #

    def _try_choch(self, symbol: str,
                   h4_df: pd.DataFrame, m15_df: pd.DataFrame,
                   bias: SignalDirection,
                   zone: Dict) -> Optional[EntrySignal]:
        """
        Proper Change of Character: requires a lower-high pattern BEFORE the break.

        LONG:  find two most recent 15M swing highs inside lookback.
               If most recent SH < previous SH (lower high formed), and
               current bar closes ABOVE the lower high → CHoCH confirmed.
        SHORT: symmetric with swing lows (higher low pattern broken downward).

        This is more selective than a plain BOS, conferring +2 score.
        """
        cc = 'close' if 'close' in m15_df.columns else 'Close'

        lookbk = min(self.config['choch_lookback'], len(m15_df) - 2)
        pivot  = self.config['choch_swing_pivot']
        bar_c  = float(m15_df[cc].iloc[-1])

        if bias == SignalDirection.LONG:
            swings = self._find_swing_highs(m15_df, lookbk, pivot, count=2)
            if len(swings) < 2:
                return None
            last_sh, prev_sh = swings[0], swings[1]
            # CHoCH requires last swing was a lower high
            if last_sh >= prev_sh:
                return None
            if bar_c <= last_sh:
                return None
            sl_anchor = zone['low']
        else:
            swings = self._find_swing_lows(m15_df, lookbk, pivot, count=2)
            if len(swings) < 2:
                return None
            last_sl, prev_sl = swings[0], swings[1]
            # Bear CHoCH requires last swing was a higher low
            if last_sl <= prev_sl:
                return None
            if bar_c >= last_sl:
                return None
            sl_anchor = zone['high']

        return self._build_signal(
            symbol, m15_df, h4_df, bias, zone,
            entry_price=bar_c,
            sl_anchor=sl_anchor,
            model='CHoCH',
        )

    # ------------------------------------------------------------------ #
    # Entry Model 2: Simple BOS (+1 pt)                                  #
    # ------------------------------------------------------------------ #

    def _try_bos(self, symbol: str,
                 h4_df: pd.DataFrame, m15_df: pd.DataFrame,
                 bias: SignalDirection,
                 zone: Dict) -> Optional[EntrySignal]:
        """
        Break of Structure: current 15M bar closes above (LONG) or below (SHORT)
        the most recent confirmed swing high/low. Less selective than CHoCH — needs
        additional confluence to qualify for entry.
        """
        cc = 'close' if 'close' in m15_df.columns else 'Close'

        lookbk = min(self.config['choch_lookback'], len(m15_df) - 2)
        pivot  = self.config['choch_swing_pivot']
        bar_c  = float(m15_df[cc].iloc[-1])

        if bias == SignalDirection.LONG:
            swings = self._find_swing_highs(m15_df, lookbk, pivot, count=1)
            if not swings or bar_c <= swings[0]:
                return None
            sl_anchor = zone['low']
        else:
            swings = self._find_swing_lows(m15_df, lookbk, pivot, count=1)
            if not swings or bar_c >= swings[0]:
                return None
            sl_anchor = zone['high']

        return self._build_signal(
            symbol, m15_df, h4_df, bias, zone,
            entry_price=bar_c,
            sl_anchor=sl_anchor,
            model='BOS',
        )

    # ------------------------------------------------------------------ #
    # Entry Model 3: Liquidity Sweep (+2 pts)                            #
    # ------------------------------------------------------------------ #

    def _try_sweep(self, symbol: str,
                   h4_df: pd.DataFrame, m15_df: pd.DataFrame,
                   bias: SignalDirection,
                   zone: Dict) -> Optional[EntrySignal]:
        """
        15M bar wicks through SSL/BSL and body-closes back — while inside 4H OB.

        LONG:  wick below SSL by ≥ min_wick_pips, close above SSL, body ≥ 50%
        SHORT: wick above BSL by ≥ min_wick_pips, close below BSL, body ≥ 50%
        """
        hc = 'high'  if 'high'  in m15_df.columns else 'High'
        lc = 'low'   if 'low'   in m15_df.columns else 'Low'
        cc = 'close' if 'close' in m15_df.columns else 'Close'
        oc = 'open'  if 'open'  in m15_df.columns else 'Open'

        bar    = m15_df.iloc[-1]
        bar_h  = float(bar[hc]);  bar_l = float(bar[lc])
        bar_c  = float(bar[cc]);  bar_o = float(bar[oc])
        bar_rng = bar_h - bar_l

        levels   = self._swing_levels(m15_df)
        min_wick = self.config['min_wick_pips'] * self.config['pip_size']
        body_req = self.config['require_body_close']
        body_pct = self.config['body_close_pct']

        swept_level = wick_extreme = None

        if bias == SignalDirection.LONG:
            for lvl in levels.get('ssl', []):
                if bar_l <= lvl - min_wick and bar_c > lvl:
                    if body_req and bar_rng > 0:
                        if abs(bar_c - bar_o) / bar_rng < body_pct:
                            continue
                    swept_level = lvl;  wick_extreme = bar_l;  break
        else:
            for lvl in levels.get('bsl', []):
                if bar_h >= lvl + min_wick and bar_c < lvl:
                    if body_req and bar_rng > 0:
                        if abs(bar_c - bar_o) / bar_rng < body_pct:
                            continue
                    swept_level = lvl;  wick_extreme = bar_h;  break

        if swept_level is None:
            return None

        if bias == SignalDirection.LONG:
            sl_anchor = min(wick_extreme, zone['low'])
        else:
            sl_anchor = max(wick_extreme, zone['high'])

        sig = self._build_signal(
            symbol, m15_df, h4_df, bias, zone,
            entry_price=bar_c,
            sl_anchor=sl_anchor,
            model='Sweep',
        )
        # v3 EqL (Photon EM2b): tag if the swept level is an equal-lows/highs
        # cluster — engineered liquidity is a higher-probability sweep.
        if sig is not None and self.config.get('use_eql_bonus', False):
            sig.swept_eql = self._is_equal_level(m15_df, swept_level, bias)
        return sig

    def _is_equal_level(self, m15_df: pd.DataFrame,
                        level: float,
                        bias: SignalDirection) -> bool:
        """
        True if `level` is part of an equal-lows (LONG) / equal-highs (SHORT)
        cluster: 2+ confirmed 15M swing points within eql_tol_pips of it.
        """
        hc = 'high' if 'high' in m15_df.columns else 'High'
        lc = 'low'  if 'low'  in m15_df.columns else 'Low'
        col    = lc if bias == SignalDirection.LONG else hc
        pivot  = 3
        lookbk = min(30, len(m15_df) - pivot - 2)
        n      = len(m15_df)
        tol    = self.config.get('eql_tol_pips', 3) * self.config['pip_size']

        hits = 0
        for i in range(n - pivot - 1, max(n - lookbk, pivot) - 1, -1):
            if i < pivot or i >= n - pivot:
                continue
            v      = float(m15_df[col].iloc[i])
            window = m15_df[col].iloc[i - pivot: i + pivot + 1]
            is_swing = (v == float(window.min()) if bias == SignalDirection.LONG
                        else v == float(window.max()))
            if is_swing and abs(v - level) <= tol:
                hits += 1
                if hits >= 2:
                    return True
        return False

    def _swing_levels(self, m15_df: pd.DataFrame) -> Dict:
        """Find recent SSL (swing lows) and BSL (swing highs) on 15M."""
        hc = 'high' if 'high' in m15_df.columns else 'High'
        lc = 'low'  if 'low'  in m15_df.columns else 'Low'
        pivot  = 3
        lookbk = min(30, len(m15_df) - pivot - 2)
        n      = len(m15_df)
        bsl, ssl = [], []

        for i in range(n - pivot - 1, max(n - lookbk, pivot) - 1, -1):
            if i < pivot or i >= n - pivot:
                continue
            h_v = float(m15_df[hc].iloc[i])
            l_v = float(m15_df[lc].iloc[i])
            if h_v == float(m15_df[hc].iloc[i - pivot: i + pivot + 1].max()):
                bsl.append(h_v)
            if l_v == float(m15_df[lc].iloc[i - pivot: i + pivot + 1].min()):
                ssl.append(l_v)

        last = float(m15_df[hc].iloc[-1])
        return {
            'bsl': sorted(set(bsl), key=lambda x: abs(x - last))[:5],
            'ssl': sorted(set(ssl), key=lambda x: abs(x - last))[:5],
        }

    # ------------------------------------------------------------------ #
    # v3 Entry Model: HTF POI Sweep (+2 pts) — Photon EM 1 / EM 4        #
    # ------------------------------------------------------------------ #

    def _try_htf_sweep(self, symbol: str,
                       h4_df: pd.DataFrame, m15_df: pd.DataFrame,
                       bias: SignalDirection,
                       zone: Dict) -> Optional[EntrySignal]:
        """
        Sweep of the 4H zone's OWN structural boundary (Photon "HTF Leg/POI
        Sweep"). The 15M bar wicks through the zone low (LONG) / high (SHORT)
        by ≥ min_wick_pips and body-closes back inside the zone.

        This is distinct from _try_sweep, which only sweeps recent 15M swing
        levels — here the liquidity taken is the HTF level itself, the
        highest-quality sweep in the Photon taxonomy.

        SL anchors to the wick extreme (below the swept HTF level).
        """
        hc = 'high'  if 'high'  in m15_df.columns else 'High'
        lc = 'low'   if 'low'   in m15_df.columns else 'Low'
        cc = 'close' if 'close' in m15_df.columns else 'Close'
        oc = 'open'  if 'open'  in m15_df.columns else 'Open'

        bar     = m15_df.iloc[-1]
        bar_h   = float(bar[hc]);  bar_l = float(bar[lc])
        bar_c   = float(bar[cc]);  bar_o = float(bar[oc])
        bar_rng = bar_h - bar_l

        min_wick = self.config['min_wick_pips'] * self.config['pip_size']
        body_req = self.config['require_body_close']
        body_pct = self.config['body_close_pct']

        if bias == SignalDirection.LONG:
            if not (bar_l <= zone['low'] - min_wick and bar_c > zone['low']):
                return None
            if body_req and bar_rng > 0 and abs(bar_c - bar_o) / bar_rng < body_pct:
                return None
            sl_anchor = bar_l
        else:
            if not (bar_h >= zone['high'] + min_wick and bar_c < zone['high']):
                return None
            if body_req and bar_rng > 0 and abs(bar_c - bar_o) / bar_rng < body_pct:
                return None
            sl_anchor = bar_h

        return self._build_signal(
            symbol, m15_df, h4_df, bias, zone,
            entry_price=bar_c,
            sl_anchor=sl_anchor,
            model='HTFSweep',
        )

    # ------------------------------------------------------------------ #
    # v3 Filter: Inducement built + swept (Photon EM 2a "PBID")          #
    # ------------------------------------------------------------------ #

    def _inducement_swept(self, m15_df: pd.DataFrame,
                          zone: Dict,
                          bias: SignalDirection) -> bool:
        """
        Photon inducement rule: before entering at the HTF POI, price must have
        BUILT a pullback swing on the near side of the zone (early buyers/
        sellers trapped = PBID) and then SWEPT it on the way into the zone.

        LONG:  a confirmed 15M swing low above zone_high within the lookback,
               later broken to the downside (price now at/in the zone).
        SHORT: symmetric with a swing high below zone_low.

        Filters "free-fall" arrivals where no early participants were trapped —
        those tend to sweep straight through the POI (Photon EM 1 warning).
        """
        hc = 'high' if 'high' in m15_df.columns else 'High'
        lc = 'low'  if 'low'  in m15_df.columns else 'Low'

        pivot  = self.config.get('choch_swing_pivot', 3)
        lookbk = min(self.config.get('inducement_lookback', 48),
                     len(m15_df) - pivot - 2)
        n      = len(m15_df)

        for i in range(n - pivot - 1, max(pivot, n - lookbk) - 1, -1):
            if i < pivot or i >= n - pivot:
                continue
            if bias == SignalDirection.LONG:
                l_v    = float(m15_df[lc].iloc[i])
                window = m15_df[lc].iloc[i - pivot: i + pivot + 1]
                if l_v != float(window.min()):
                    continue
                if l_v <= zone['high']:
                    continue          # swing must be ABOVE the zone (inducement)
                after = m15_df[lc].iloc[i + 1:]
                if len(after) and float(after.min()) < l_v:
                    return True       # built, then swept on the way down
            else:
                h_v    = float(m15_df[hc].iloc[i])
                window = m15_df[hc].iloc[i - pivot: i + pivot + 1]
                if h_v != float(window.max()):
                    continue
                if h_v >= zone['low']:
                    continue          # swing must be BELOW the zone
                after = m15_df[hc].iloc[i + 1:]
                if len(after) and float(after.max()) > h_v:
                    return True
        return False

    # ------------------------------------------------------------------ #
    # Entry Model 4: EMA20 Bounce (+1 pt)                                #
    # ------------------------------------------------------------------ #

    def _try_ema_bounce(self, symbol: str,
                        h4_df: pd.DataFrame, m15_df: pd.DataFrame,
                        bias: SignalDirection,
                        zone: Dict) -> Optional[EntrySignal]:
        """
        15M bar wicks to the EMA20 and closes back on the correct side —
        while inside the 4H OB zone.
        """
        cc = 'close' if 'close' in m15_df.columns else 'Close'
        hc = 'high'  if 'high'  in m15_df.columns else 'High'
        lc = 'low'   if 'low'   in m15_df.columns else 'Low'
        oc = 'open'  if 'open'  in m15_df.columns else 'Open'

        period = min(self.config['m15_ema'], len(m15_df) - 1)
        ema20  = float(self._ema(m15_df[cc], period).iloc[-1])

        bar    = m15_df.iloc[-1]
        bar_h  = float(bar[hc]);  bar_l = float(bar[lc])
        bar_c  = float(bar[cc]);  bar_o = float(bar[oc])
        bar_rng = bar_h - bar_l

        tol      = 5 * self.config['pip_size']
        body_pct = self.config['body_close_pct']

        if bias == SignalDirection.LONG:
            if not (bar_l <= ema20 + tol and bar_c > ema20):
                return None
            if bar_rng > 0 and abs(bar_c - bar_o) / bar_rng < body_pct:
                return None
            sl_anchor = min(bar_l, zone['low'])
        else:
            if not (bar_h >= ema20 - tol and bar_c < ema20):
                return None
            if bar_rng > 0 and abs(bar_c - bar_o) / bar_rng < body_pct:
                return None
            sl_anchor = max(bar_h, zone['high'])

        return self._build_signal(
            symbol, m15_df, h4_df, bias, zone,
            entry_price=bar_c,
            sl_anchor=sl_anchor,
            model='EMA20',
        )

    # ------------------------------------------------------------------ #
    # Signal builder                                                       #
    # ------------------------------------------------------------------ #

    def _build_signal(self,
                      symbol: str,
                      m15_df: pd.DataFrame,
                      h4_df: pd.DataFrame,
                      bias: SignalDirection,
                      zone: Dict,
                      entry_price: float,
                      sl_anchor: float,
                      model: str) -> Optional[EntrySignal]:
        """
        Construct the EntrySignal from validated entry parameters.

        SL = sl_anchor − atr_buffer × 4H_ATR  (LONG)
           = sl_anchor + atr_buffer × 4H_ATR  (SHORT)
        """
        atr    = self._atr(h4_df)
        buf    = self.config['atr_buffer']
        min_sl = self.config['min_sl_pips'] * self.config['pip_size']

        if bias == SignalDirection.LONG:
            sl   = sl_anchor - buf * atr
            risk = entry_price - sl
        else:
            sl   = sl_anchor + buf * atr
            risk = sl - entry_price

        if risk <= 0 or risk < min_sl:
            return None
        if entry_price > 0 and risk / entry_price > self.config['max_sl_pct']:
            self.logger.debug(
                f"{symbol} SL too wide ({risk/entry_price:.2%}) — skip"
            )
            return None
        max_sl_pips = self.config.get('max_sl_pips', None)
        if max_sl_pips is not None:
            sl_pips = risk / self.config['pip_size']
            if sl_pips > max_sl_pips:
                self.logger.debug(f"{symbol} SL {sl_pips:.1f} pips > max {max_sl_pips} — skip")
                return None

        tp1_rr = self.config['tp1_rr']
        tp2_rr = self.config['tp2_rr']

        if bias == SignalDirection.LONG:
            tp1 = entry_price + risk * tp1_rr
            tp2 = entry_price + risk * tp2_rr
        else:
            tp1 = entry_price - risk * tp1_rr
            tp2 = entry_price - risk * tp2_rr

        ts = m15_df.index[-1]
        if hasattr(ts, 'to_pydatetime'):
            ts = ts.to_pydatetime()
        elif not isinstance(ts, datetime):
            ts = datetime.now()

        sig = EntrySignal(
            timestamp    = ts,
            symbol       = symbol,
            direction    = bias,
            entry_type   = EntryType.EXTREME,
            entry_price  = entry_price,
            stop_loss    = sl,
            take_profit  = tp2,
            confidence   = 0.75,
            rr_ratio     = tp2_rr,
            timeframe    = '15M',
            phase        = f'3TF-{model}',
            criteria_met = ['1D-bias', '4H-OB', f'15M-{model}'],
        )
        sig.tp1_price     = tp1
        sig.tp2_price     = tp2
        sig.tp1_close_pct = self.config['tp1_close_pct']
        return sig
