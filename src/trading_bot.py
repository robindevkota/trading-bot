"""
MTF PRO Trading Bot
Main orchestrator — live trading and backtesting
"""

import pandas as pd
import numpy as np
import yaml
import logging
import json
import time
import threading
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pathlib import Path

try:
    import schedule
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False

from .data.mt5_connector import create_connector
from .strategies.counter_trend_entry import (
    ProTrendPositionManager,
    EntrySignal,
    SignalDirection,
)
from .strategies.mtf_pro_entry import MTFProEntryGenerator
from .risk.position_manager import (
    RiskManager,
    TradeExecutor,
    TradeStatus,
    Position,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slice_up_to(df: pd.DataFrame, timestamp: pd.Timestamp) -> pd.DataFrame:
    """Return all rows with index <= timestamp."""
    if df is None or df.empty:
        return pd.DataFrame()
    return df[df.index <= timestamp]


def _is_duplicate_signal(new_sig: EntrySignal, existing: List[EntrySignal],
                          price_tol: float = 0.0005) -> bool:
    for s in existing[-10:]:
        if (s.symbol == new_sig.symbol and
                s.direction == new_sig.direction and
                abs(s.entry_price - new_sig.entry_price) < price_tol):
            return True
    return False


# ---------------------------------------------------------------------------
# TradingBot
# ---------------------------------------------------------------------------

class TradingBot:
    """
    MTF PRO trading bot orchestrator.

    Supports:
    - Live trading via MetaTrader 5
    - Walk-forward backtesting on historical data
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = self._load_config(config_path)
        self._setup_logging()

        # Data connector (real MT5 or mock)
        self.connector = create_connector(self.config)

        # Signal generators — one per symbol, each merging global + symbol-specific params
        strat_cfg    = self.config.get('strategy', {})
        base_cfg     = strat_cfg.get('mtf_pro', {})
        symbol_cfgs  = strat_cfg.get('symbol_params', {})
        symbols      = self.config.get('trading', {}).get('symbols', [])
        self._generators: Dict[str, MTFProEntryGenerator] = {}
        for sym in symbols:
            merged = {**base_cfg, **symbol_cfgs.get(sym, {})}
            self._generators[sym] = MTFProEntryGenerator(merged)
        # fallback generator (global params) for symbols not in the list
        self._default_generator = MTFProEntryGenerator(base_cfg)

        # Risk management
        rm_cfg = self.config.get('risk_management', {})
        bt_cfg = self.config.get('backtesting', {})
        self.risk_manager = RiskManager(
            account_balance=bt_cfg.get('initial_balance', 10000),
            risk_per_trade_percent=rm_cfg.get('max_risk_per_trade', 1.0),
            max_daily_loss_percent=rm_cfg.get('max_daily_loss', 5.0),
            max_drawdown_percent=rm_cfg.get('max_drawdown', 15.0),
            use_trailing_stop=rm_cfg.get('use_trailing_stop', False),
            trailing_stop_activation_rr=rm_cfg.get('trailing_stop_activation', 1.5),
        )
        self.trade_executor = TradeExecutor(
            self.risk_manager,
            spread_pips=bt_cfg.get('spread', 2.0),
            slippage_pips=bt_cfg.get('slippage', 1.0),
        )
        self.position_manager = ProTrendPositionManager(
            self.trade_executor, self.risk_manager
        )

        # State
        self.positions:     List[Position]    = []
        self.trade_history: List[Position]    = []
        self.all_signals:   List[EntrySignal] = []
        self.equity_curve:  List[Dict]        = []
        self.pending_signals: List[Dict]      = []
        self.is_running = False

        self.logger.info("TradingBot initialized")

    # ------------------------------------------------------------------
    # Configuration & logging
    # ------------------------------------------------------------------

    def _load_config(self, path: str) -> dict:
        with open(path, 'r') as f:
            return yaml.safe_load(f)

    def _setup_logging(self):
        log_cfg = self.config.get('logging', {})
        level = getattr(logging, log_cfg.get('level', 'INFO'))
        self.logger = logging.getLogger('TradingBot')
        self.logger.setLevel(level)
        fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        if not self.logger.handlers:
            ch = logging.StreamHandler()
            ch.setFormatter(fmt)
            self.logger.addHandler(ch)

            if log_cfg.get('file', True):
                log_dir = Path(log_cfg.get('log_dir', 'logs'))
                log_dir.mkdir(exist_ok=True)
                fh = logging.FileHandler(
                    log_dir / f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                )
                fh.setFormatter(fmt)
                self.logger.addHandler(fh)

    # ------------------------------------------------------------------
    # Live trading loop
    # ------------------------------------------------------------------

    def run(self):
        """Start live trading loop (requires MT5)."""
        if not self.connector.connect():
            self.logger.error("Cannot connect to MT5 — aborting")
            return

        self.is_running = True
        self.logger.info("Trading bot started (LIVE mode)")

        if SCHEDULE_AVAILABLE:
            self._start_scheduler()

        while self.is_running:
            try:
                self._live_scan()
                self._live_update_positions()
                self._save_results()
                time.sleep(60)
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt — stopping")
                self.stop()
            except Exception as e:
                self.logger.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(5)

    def stop(self):
        self.is_running = False
        self.connector.disconnect()
        self._save_results()
        self.logger.info("Bot stopped")

    def _start_scheduler(self):
        schedule.every().day.at("08:00").do(self._daily_analysis)
        schedule.every().day.at("12:00").do(self._midday_analysis)
        schedule.every().day.at("16:00").do(self._afternoon_analysis)

        def _run():
            while self.is_running:
                schedule.run_pending()
                time.sleep(1)

        threading.Thread(target=_run, daemon=True).start()

    def _live_scan(self):
        """Fetch live data and generate signals for all configured symbols."""
        for symbol in self.config['trading']['symbols']:
            try:
                data = self.connector.fetch_all_timeframes(symbol)
                if len(data) < 2:
                    continue
                signals = self._generate_signals_from_data(symbol, data)
                self._process_signals(signals, live=True)
            except Exception as e:
                self.logger.error(f"Error scanning {symbol}: {e}", exc_info=True)

    def _live_update_positions(self):
        """
        Live position management — runs every 60 s in the main loop.

        Step 1: sync with MT5 — detect positions closed by SL/TP and
                update the bot's internal state so new trades can open.
        Step 2: for remaining open positions, check TP1 and move SL to
                breakeven on MT5 when the first target is reached.
        """
        self._sync_positions_from_mt5()

        for position in self.positions[:]:
            try:
                tick = self.connector.get_current_price(position.symbol)
                if not tick:
                    continue
                current_price = (tick['bid'] + tick['ask']) / 2
                position.calculate_pnl(current_price)
                self._check_live_tp1(position, current_price)
            except Exception as e:
                self.logger.error(f"Error updating #{position.ticket}: {e}")

    def _sync_positions_from_mt5(self):
        """
        Reconcile internal position list with what MT5 actually has open.

        When MT5 closes a position via SL or TP, the bot's internal list
        still shows it as OPEN.  This method detects that and marks it
        closed so that the risk manager allows the next trade.
        """
        try:
            live = self.connector.get_open_positions()
        except Exception as e:
            self.logger.warning(f"MT5 position sync error: {e}")
            return

        live_tickets = {p['ticket'] for p in live}

        for pos in self.positions[:]:
            if not pos.ticket or pos.ticket in live_tickets:
                continue  # still open on MT5, nothing to do

            # MT5 closed this position — get price and reason
            close_price, close_status = self._get_mt5_close_info(pos)

            pos.status     = close_status
            pos.close_time = datetime.now()
            pos.calculate_pnl(close_price)
            realized = pos.unrealized_pnl
            pos.realized_pnl = realized

            # Update risk metrics so daily-loss / drawdown limits stay accurate
            self.risk_manager.metrics.account_balance += realized
            self.risk_manager.metrics.daily_pnl       += realized
            if realized > 0:
                self.risk_manager.metrics.daily_wins   += 1
            else:
                self.risk_manager.metrics.daily_losses += 1
            self.risk_manager.metrics.daily_trades += 1

            self.trade_history.append(pos)
            self.positions.remove(pos)

            result_str = "WIN" if realized > 0 else "LOSS"
            self.logger.info(
                f"TRADE CLOSED [{result_str}] (MT5 sync) "
                f"{pos.direction.value} {pos.symbol} @ {close_price:.5f}  "
                f"PnL=${realized:+.2f}  status={close_status.value}"
            )

    def _get_mt5_close_info(self, pos: Position):
        """
        Return (close_price, TradeStatus) for a position that MT5 already closed.

        Tries MT5 deal history first; falls back to current bid/ask if unavailable.
        """
        close_price = None

        try:
            import MetaTrader5 as _mt5
            deals = _mt5.history_deals_get(position=pos.ticket)
            if deals and len(deals) >= 2:
                close_price = float(deals[-1].price)
        except Exception:
            pass

        if close_price is None:
            tick = self.connector.get_current_price(pos.symbol)
            if tick:
                close_price = (tick['bid'] + tick['ask']) / 2
            else:
                close_price = pos.stop_loss  # safest fallback

        # Determine win/loss by comparing close to TP (±0.5 pip tolerance)
        tol = 5e-4
        if pos.direction == SignalDirection.LONG:
            status = (TradeStatus.TAKE_PROFIT
                      if close_price >= pos.take_profit - tol
                      else TradeStatus.STOPPED_OUT)
        else:
            status = (TradeStatus.TAKE_PROFIT
                      if close_price <= pos.take_profit + tol
                      else TradeStatus.STOPPED_OUT)
        return close_price, status

    def _check_live_tp1(self, pos: Position, current_price: float):
        """
        Move SL to breakeven on MT5 when price first reaches the TP1 level.

        In live mode we don't partial-close (requires a counter-order which
        complicates lot tracking).  Instead we just slide the stop to entry,
        locking in a scratch-at-worst result while the runner targets TP2.
        """
        if not pos.targets or not pos.ticket:
            return
        if pos.targets_hit[0]:
            return  # already triggered

        tp1 = pos.targets[0]
        tp1_reached = (
            (pos.direction == SignalDirection.LONG  and current_price >= tp1) or
            (pos.direction == SignalDirection.SHORT and current_price <= tp1)
        )
        if not tp1_reached:
            return

        pos.targets_hit[0] = True
        be = pos.entry_price

        if self.connector.modify_position(pos.ticket, sl=be, tp=pos.take_profit):
            pos.stop_loss = be
            self.logger.info(
                f"TP1 hit — SL moved to breakeven {be:.5f}  "
                f"#{pos.ticket} {pos.symbol}"
            )
        else:
            self.logger.warning(
                f"TP1 hit but failed to modify SL for #{pos.ticket} {pos.symbol}"
            )

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def _generate_signals_from_data(self, symbol: str,
                                     data: Dict[str, pd.DataFrame]) -> List[EntrySignal]:
        """Run the MTF PRO generator on the provided data."""
        h4  = data.get('h4',  pd.DataFrame())
        h1  = data.get('h1',  pd.DataFrame())
        m15 = data.get('m15', pd.DataFrame())
        if h4.empty or m15.empty:
            return []
        try:
            gen = self._generators.get(symbol, self._default_generator)
            return gen.generate_signals(symbol, h4, m15, h1_df=h1)
        except Exception as e:
            self.logger.warning(f"MTF PRO error ({symbol}): {e}", exc_info=True)
            return []

    def _process_signals(self, signals: List[EntrySignal], live: bool = False):
        """Validate, log, and execute entry signals."""
        for signal in signals:
            if _is_duplicate_signal(signal, self.all_signals):
                continue
            self.all_signals.append(signal)

            self.logger.info(
                f"SIGNAL {signal.symbol} {signal.direction.value.upper()} "
                f"@ {signal.entry_price:.5f}  SL={signal.stop_loss:.5f}  "
                f"TP={signal.take_profit:.5f}  RR={signal.rr_ratio:.2f}  "
                f"conf={signal.confidence:.0%}  [{signal.phase}]"
            )

            can_open, reason = self.risk_manager.can_open_position(signal, self.positions)
            if not can_open:
                self.logger.debug(f"Rejected: {reason}")
                continue

            size = self.risk_manager.calculate_position_for_signal(signal)

            if live:
                order_type = 'buy' if signal.direction == SignalDirection.LONG else 'sell'
                result = self.connector.place_order(
                    symbol=signal.symbol, order_type=order_type, volume=size,
                    sl=signal.stop_loss, tp=signal.take_profit,
                )
                if result:
                    pos = self.risk_manager.create_position(signal, size)
                    pos.ticket = result['ticket']
                    self._attach_targets(signal, pos)
                    self.positions.append(pos)
                    self.logger.info(f"Opened live #{pos.ticket}: {size:.2f} lots")
            else:
                pos = self.trade_executor.execute_market_order(signal, size)
                self._attach_targets(signal, pos)
                self.positions.append(pos)
                self.logger.info(
                    f"TRADE OPENED #{len(self.trade_history)+len(self.positions)} "
                    f"{signal.direction.value} {signal.symbol} @ {pos.entry_price:.5f} "
                    f"SL={signal.stop_loss:.5f} TP={signal.take_profit:.5f} "
                    f"size={size:.4f} lots"
                )

    @staticmethod
    def _attach_targets(signal: EntrySignal, position: Position):
        """Attach TP1/TP2 dual-target metadata to a position."""
        tp1      = getattr(signal, 'tp1_price', 0.0)
        tp2      = getattr(signal, 'tp2_price', 0.0)
        tp1_frac = getattr(signal, 'tp1_close_pct', 0.50)
        if tp1 and tp2:
            vol_tp1 = position.volume * tp1_frac
            vol_tp2 = position.volume - vol_tp1
            position.targets        = [tp1, tp2]
            position.target_volumes = [vol_tp1, vol_tp2]
            position.targets_hit    = [False, False]

    # ------------------------------------------------------------------
    # Position update (shared by live + backtest)
    # ------------------------------------------------------------------

    def _update_single_position(self, position: Position,
                                 current_price: float,
                                 current_time: datetime = None,
                                 m1_df: pd.DataFrame = None):
        if position.status != TradeStatus.OPEN:
            return

        # Dual-TP partial close and breakeven management
        if position.targets:
            self.position_manager.update(position, current_price, m1_df)

        # Standard SL/TP check
        should_stop, status = self.risk_manager.check_stop_out(position, current_price)
        if should_stop:
            realized = self.trade_executor.close_position(position, current_price)
            position.status     = status
            position.close_time = current_time or datetime.now()
            self.trade_history.append(position)
            if position in self.positions:
                self.positions.remove(position)
            result_str = "WIN" if realized > 0 else "LOSS"
            self.logger.info(
                f"TRADE CLOSED [{result_str}] {position.direction.value} {position.symbol} "
                f"@ {current_price:.5f}  PnL=${realized:+.2f}  "
                f"status={status.value}  trades_so_far={len(self.trade_history)}"
            )
            return

        # Standard trailing stop (disabled by default for MTF PRO)
        if self.risk_manager.use_trailing_stop:
            new_stop = self.risk_manager.update_trailing_stop(position, current_price)
            if new_stop:
                position.stop_loss = new_stop

        position.calculate_pnl(current_price)

    # ------------------------------------------------------------------
    # Scheduled analysis stubs
    # ------------------------------------------------------------------

    def _daily_analysis(self):
        self.logger.info("Daily analysis — scanning bias")

    def _midday_analysis(self):
        self.logger.info("Midday analysis check")

    def _afternoon_analysis(self):
        self.logger.info("Afternoon session review")

    # ------------------------------------------------------------------
    # Backtesting
    # ------------------------------------------------------------------

    def run_backtest(self, symbol: str,
                     start_date: str,
                     end_date: str,
                     initial_balance: float = 10000,
                     mode: str = 'mtf_pro') -> Dict:
        """
        Walk-forward backtest for one symbol.

        Iterates 15M bars from start_date → end_date.
        For each bar: slice all TF data up to that time, generate signals,
        update open positions at the bar's close price.
        """
        self.logger.info(f"Backtest: {symbol}  {start_date} -> {end_date}")

        # Reset state
        self.positions       = []
        self.trade_history   = []
        self.all_signals     = []
        self.equity_curve    = []
        self.pending_signals = []
        self.risk_manager.metrics.account_balance   = initial_balance
        self.risk_manager.metrics.account_equity    = initial_balance
        self.risk_manager.metrics.peak_equity       = initial_balance
        self.risk_manager.metrics.daily_pnl         = 0.0
        self.risk_manager.metrics.daily_wins        = 0
        self.risk_manager.metrics.daily_losses      = 0
        self.risk_manager.metrics.daily_trades      = 0
        self.risk_manager.metrics.current_drawdown  = 0.0
        self.risk_manager.metrics.max_drawdown      = 0.0
        self.risk_manager.metrics.daily_loss_limit  = (
            initial_balance * (self.risk_manager._max_daily_loss_pct / 100)
        )
        self.risk_manager.next_ticket = 1

        if not self.connector.connect():
            self.logger.error("Cannot connect for backtest data")
            return {}

        data = self.connector.fetch_historical_range(symbol, start_date, end_date)
        if not data or 'm15' not in data or data['m15'].empty:
            self.logger.error("No 15M data — cannot backtest")
            return {}

        m15_full = data['m15']
        start_dt = pd.Timestamp(start_date)
        end_dt   = pd.Timestamp(end_date)
        m15_test = m15_full[
            (m15_full.index >= start_dt) & (m15_full.index <= end_dt)
        ]

        if m15_test.empty:
            self.logger.error("No 15M bars in specified date range")
            return {}

        self.logger.info(f"Walking forward through {len(m15_test)} bars...")

        # Precompute start position for O(1) 15M slicing
        m15_full_arr     = m15_full.index
        _test_start_pos  = m15_full_arr.searchsorted(m15_test.index[0])
        _m15_window      = 300

        # Timeframe cache — re-slice when each bar closes
        _tf_cache: Dict[str, pd.DataFrame] = {}
        _tf_last:  Dict[str, pd.Timestamp] = {}
        _tf_interval = {'h4': timedelta(hours=4), 'h1': timedelta(hours=1)}

        n_bars          = len(m15_test)
        _progress_every = max(500, n_bars // 20)
        import time as _time
        _t0           = _time.time()
        _prev_date    = None

        # Cooldown: block re-entry near a recently stopped-out level for 48 bars (~12H)
        _COOLDOWN_BARS  = 48
        _COOLDOWN_PIPS  = 0.0050
        _zone_cooldowns: List[Dict] = []

        for i, (bar_time, bar) in enumerate(m15_test.iterrows()):
            # Daily PnL reset
            _cur_date = bar_time.date()
            if _prev_date is not None and _cur_date != _prev_date:
                self.risk_manager.metrics.account_balance += self.risk_manager.metrics.daily_pnl
                self.risk_manager.metrics.daily_pnl        = 0.0
                self.risk_manager.metrics.daily_wins       = 0
                self.risk_manager.metrics.daily_losses     = 0
                self.risk_manager.metrics.daily_trades     = 0
                self.risk_manager.metrics.daily_loss_limit = (
                    self.risk_manager.metrics.account_balance *
                    (self.risk_manager._max_daily_loss_pct / 100)
                )
            _prev_date = _cur_date

            # Progress logging
            if i > 0 and i % _progress_every == 0:
                elapsed = _time.time() - _t0
                pct     = i / n_bars
                eta     = (elapsed / pct) * (1 - pct)
                self.logger.info(
                    f"  Progress: {i}/{n_bars} bars ({pct:.0%})  "
                    f"elapsed={elapsed:.0f}s  eta={eta:.0f}s  "
                    f"trades={len(self.trade_history)}"
                )

            # Refresh H4/H1 slices only when a new bar has closed
            for tf, interval in _tf_interval.items():
                if tf not in _tf_cache or (bar_time - _tf_last[tf]) >= interval:
                    raw   = data.get(tf, pd.DataFrame())
                    limit = {'h4': 600, 'h1': 240}[tf]
                    _tf_cache[tf] = _slice_up_to(raw, bar_time).iloc[:-1].tail(limit)
                    _tf_last[tf]  = bar_time

            h4  = _tf_cache.get('h4', pd.DataFrame())
            h1  = _tf_cache.get('h1', pd.DataFrame())
            # 15M: O(1) integer slice
            _end   = _test_start_pos + i + 1
            _start = max(0, _end - _m15_window)
            m15    = m15_full.iloc[_start:_end]

            if len(h4) < 50 or len(m15) < 50:
                continue

            current_price = float(bar['close'])
            current_high  = float(bar['high'])
            current_low   = float(bar['low'])
            current_high  = float(bar['high'])
            current_low   = float(bar['low'])

            # --- Pending limit order fills ---
            for pending in self.pending_signals[:]:
                sig       = pending['signal']
                bars_left = pending['bars_left']
                size      = pending['size']
                filled = (
                    (sig.direction == SignalDirection.LONG  and current_low  <= sig.entry_price) or
                    (sig.direction == SignalDirection.SHORT and current_high >= sig.entry_price)
                )
                if filled and not self.positions:
                    can_open, _ = self.risk_manager.can_open_position(sig, self.positions)
                    if can_open:
                        pos = self.trade_executor.execute_market_order(sig, size)
                        self._attach_targets(sig, pos)
                        self.positions.append(pos)
                        self.logger.info(
                            f"TRADE OPENED #{pos.ticket} {sig.direction.value} {sig.symbol}"
                            f" @ {pos.entry_price:.5f} SL={pos.stop_loss:.5f}"
                            f" TP={pos.take_profit:.5f} size={size:.4f} lots"
                        )
                    self.pending_signals.remove(pending)
                elif filled or bars_left <= 0:
                    self.pending_signals.remove(pending)
                else:
                    pending['bars_left'] -= 1

            # --- Cooldown countdown ---
            for cd in _zone_cooldowns[:]:
                cd['bars_left'] -= 1
                if cd['bars_left'] <= 0:
                    _zone_cooldowns.remove(cd)

            # --- Position update (worst-case intrabar price) ---
            for position in self.positions[:]:
                worst_price = (current_low  if position.direction == SignalDirection.LONG
                               else current_high)
                self._update_single_position(position, worst_price, bar_time, pd.DataFrame())
                if position.status == TradeStatus.STOPPED_OUT:
                    _zone_cooldowns.append({
                        'level':     position.entry_price,
                        'direction': position.direction.value,
                        'bars_left': _COOLDOWN_BARS,
                    })
                    self.logger.debug(
                        f"Cooldown set: {position.direction.value} near "
                        f"{position.entry_price:.5f} for {_COOLDOWN_BARS} bars"
                    )

            # --- Signal analysis: every 2H (8 × 15M bars), no open position ---
            if i % 8 == 0 and not self.positions and not self.pending_signals:
                bar_data   = {'h4': h4, 'h1': h1, 'm15': m15}
                new_signals = self._generate_signals_from_data(symbol, bar_data)
                for sig in new_signals:
                    if _is_duplicate_signal(sig, self.all_signals):
                        continue
                    in_cooldown = any(
                        cd['direction'] == sig.direction.value and
                        abs(sig.entry_price - cd['level']) <= _COOLDOWN_PIPS
                        for cd in _zone_cooldowns
                    )
                    if in_cooldown:
                        self.logger.debug(
                            f"Cooldown skip: {sig.direction.value} @ {sig.entry_price:.5f}"
                        )
                        continue
                    self.all_signals.append(sig)
                    self.logger.info(
                        f"SIGNAL {sig.symbol} {sig.direction.value.upper()}"
                        f" @ {sig.entry_price:.5f}  SL={sig.stop_loss:.5f}"
                        f" TP={sig.take_profit:.5f}  RR={sig.rr_ratio:.2f}"
                        f"  conf={sig.confidence:.0%}  [{sig.phase}]"
                    )
                    can_open, reason = self.risk_manager.can_open_position(sig, self.positions)
                    if not can_open:
                        self.logger.debug(f"Rejected: {reason}")
                        continue
                    size = self.risk_manager.calculate_position_for_signal(sig)
                    self.pending_signals.append({'signal': sig, 'size': size, 'bars_left': 480})
                    break  # one pending order at a time

            # Equity snapshot once per day (~96 × 15M bars)
            if i % 96 == 0:
                unrealized = sum(p.unrealized_pnl for p in self.positions)
                equity = (self.risk_manager.metrics.account_balance +
                          self.risk_manager.metrics.daily_pnl + unrealized)
                self.equity_curve.append({
                    'time':           str(bar_time),
                    'equity':         round(equity, 2),
                    'balance':        round(self.risk_manager.metrics.account_balance +
                                           self.risk_manager.metrics.daily_pnl, 2),
                    'open_positions': len(self.positions),
                })

        self.logger.info(
            f"Backtest done — signals={len(self.all_signals)}, "
            f"trades={len(self.trade_history)}, still_open={len(self.positions)}"
        )
        return self._calculate_results()

    def _calculate_results(self) -> Dict:
        if not self.trade_history:
            return {'total_trades': 0, 'win_rate': 0.0, 'total_pnl': 0.0,
                    'max_drawdown': 0.0, 'profit_factor': 0.0,
                    'expectancy': 0.0, 'final_balance': 0.0, 'equity_curve': []}

        wins   = [p for p in self.trade_history if p.realized_pnl > 0]
        losses = [p for p in self.trade_history if p.realized_pnl <= 0]
        n      = len(self.trade_history)

        win_rate   = len(wins) / n
        total_pnl  = sum(p.realized_pnl for p in self.trade_history)
        avg_win    = sum(p.realized_pnl for p in wins)   / len(wins)   if wins   else 0
        avg_loss   = abs(sum(p.realized_pnl for p in losses) / len(losses)) if losses else 0
        gross_win  = sum(p.realized_pnl for p in wins)
        gross_loss = abs(sum(p.realized_pnl for p in losses))

        phases = {}
        for p in self.trade_history:
            ph = getattr(p, 'phase', '') or 'Unknown'
            if ph not in phases:
                phases[ph] = {'wins': 0, 'losses': 0, 'pnl': 0.0}
            if p.realized_pnl > 0:
                phases[ph]['wins'] += 1
            else:
                phases[ph]['losses'] += 1
            phases[ph]['pnl'] = round(phases[ph]['pnl'] + p.realized_pnl, 2)
        for ph in phases:
            t = phases[ph]['wins'] + phases[ph]['losses']
            phases[ph]['total']    = t
            phases[ph]['win_rate'] = round(phases[ph]['wins'] / t, 4) if t else 0

        return {
            'total_trades':  n,
            'wins':          len(wins),
            'losses':        len(losses),
            'win_rate':      round(win_rate, 4),
            'total_pnl':     round(total_pnl, 2),
            'avg_win':       round(avg_win,   2),
            'avg_loss':      round(avg_loss,  2),
            'profit_factor': round(gross_win / gross_loss, 2) if gross_loss else 0,
            'expectancy':    round((win_rate * avg_win) - ((1 - win_rate) * avg_loss), 2),
            'max_drawdown':  round(self.risk_manager.metrics.max_drawdown, 2),
            'final_balance': round(self.risk_manager.metrics.account_balance +
                                   self.risk_manager.metrics.daily_pnl, 2),
            'equity_curve':  self.equity_curve,
            'by_entry_type': phases,
        }

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _save_results(self, append: bool = True):
        if not self.config.get('output', {}).get('save_trades', True):
            return
        results_dir = Path(self.config['output'].get('results_dir', 'results'))
        results_dir.mkdir(exist_ok=True)

        history_path = results_dir / 'trade_history.json'
        existing = []
        if append and history_path.exists():
            try:
                with open(history_path) as f:
                    existing = json.load(f)
            except Exception:
                existing = []
        new_trades = [p.to_dict() for p in self.trade_history]
        with open(history_path, 'w') as f:
            json.dump(existing + new_trades, f, indent=2)

        with open(results_dir / 'open_positions.json', 'w') as f:
            json.dump([p.to_dict() for p in self.positions], f, indent=2)
        with open(results_dir / 'risk_summary.json', 'w') as f:
            json.dump(self.risk_manager.get_risk_summary(), f, indent=2,
                      default=lambda o: bool(o) if isinstance(o, (bool,)) else str(o))
        if self.equity_curve:
            with open(results_dir / 'equity_curve.json', 'w') as f:
                json.dump(self.equity_curve, f, indent=2)

    def generate_report(self, results: Dict) -> str:
        lines = [
            "=" * 52,
            "  MTF PRO STRATEGY — BACKTEST REPORT",
            "=" * 52,
            f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "  PERFORMANCE",
            f"  Total Trades  : {results.get('total_trades', 0)}",
            f"  Win Rate      : {results.get('win_rate', 0):.1%}",
            f"  Total PnL     : ${results.get('total_pnl', 0):.2f}",
            f"  Final Balance : ${results.get('final_balance', 0):.2f}",
            "",
            "  BREAKDOWN",
            f"  Wins          : {results.get('wins', 0)}",
            f"  Losses        : {results.get('losses', 0)}",
            f"  Avg Win       : ${results.get('avg_win', 0):.2f}",
            f"  Avg Loss      : ${results.get('avg_loss', 0):.2f}",
            f"  Profit Factor : {results.get('profit_factor', 0):.2f}",
            f"  Expectancy    : ${results.get('expectancy', 0):.2f} / trade",
            "",
            "  RISK",
            f"  Max Drawdown  : {results.get('max_drawdown', 0):.2f}%",
            "=" * 52,
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backtester (multi-symbol)
# ---------------------------------------------------------------------------

class Backtester:
    """Runs backtests across multiple symbols and aggregates results."""

    def __init__(self, config_path: str = "config/config.yaml"):
        self._config_path = config_path
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        self.logger = logging.getLogger('Backtester')

    def _run_single(self, symbol: str, start_date: str, end_date: str,
                    mode: str, initial_balance: float,
                    out: Dict, lock: threading.Lock) -> None:
        self.logger.info(f"=== Starting {symbol} ===")
        bot     = TradingBot(self._config_path)
        results = bot.run_backtest(symbol, start_date, end_date,
                                   initial_balance=initial_balance, mode=mode)
        with lock:
            out[symbol] = (bot, results)
        self.logger.info(f"=== Done {symbol} ===")

    def run_full_backtest(self, symbols: List[str] = None,
                          start_date: str = None,
                          end_date: str = None,
                          mode: str = None) -> Dict:
        bt_cfg     = self.config.get('backtesting', {})
        symbols    = symbols    or self.config['trading']['symbols']
        start_date = start_date or bt_cfg.get('start_date', '2023-01-01')
        end_date   = end_date   or bt_cfg.get('end_date',   '2024-12-31')
        mode       = mode       or self.config.get('strategy', {}).get('mode', 'mtf_pro')
        init_bal   = bt_cfg.get('initial_balance', 10000)

        combined = {'symbols': {}, 'total_trades': 0, 'total_pnl': 0.0,
                    'winning_symbols': 0, 'losing_symbols': 0}

        results_dir = Path(self.config['output'].get('results_dir', 'results'))
        results_dir.mkdir(exist_ok=True)

        out:  Dict = {}
        lock  = threading.Lock()
        threads = [
            threading.Thread(target=self._run_single,
                             args=(sym, start_date, end_date, mode, init_bal, out, lock),
                             daemon=True)
            for sym in symbols
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        first_symbol = True
        for symbol in symbols:
            if symbol not in out:
                continue
            bot, results = out[symbol]
            if not results:
                continue
            combined['symbols'][symbol] = results
            combined['total_trades'] += results.get('total_trades', 0)
            combined['total_pnl']    += results.get('total_pnl', 0.0)
            if results.get('total_pnl', 0) > 0:
                combined['winning_symbols'] += 1
            else:
                combined['losing_symbols'] += 1

            print(bot.generate_report(results))
            bot._save_results(append=not first_symbol)
            first_symbol = False

        n = combined['total_trades']
        if n > 0:
            print("=" * 52)
            print("  COMBINED RESULTS — ALL SYMBOLS")
            print("=" * 52)
            print(f"  Symbols       : {', '.join(symbols)}")
            print(f"  Period        : {start_date} -> {end_date}")
            print(f"  Total Trades  : {n}")
            print(f"  Total PnL     : ${combined['total_pnl']:+.2f}")
            print(f"  Winning Syms  : {combined['winning_symbols']}/{len(symbols)}")
            print("=" * 52)

        self.logger.info(
            f"Full backtest complete — {combined['total_trades']} total trades, "
            f"PnL=${combined['total_pnl']:.2f}"
        )
        return combined


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _print_aggregate_stats(history_path: Path):
    """Read trade_history.json and print merged stats."""
    if not history_path.exists():
        print("No trade_history.json found.")
        return
    try:
        with open(history_path) as f:
            trades = json.load(f)
    except Exception as e:
        print(f"Could not read trade history: {e}")
        return

    if not trades:
        print("Trade history is empty.")
        return

    wins      = [t for t in trades if t.get('realized_pnl', 0) > 0]
    losses    = [t for t in trades if t.get('realized_pnl', 0) <= 0]
    n         = len(trades)
    total_pnl = sum(t.get('realized_pnl', 0) for t in trades)
    avg_win   = sum(t.get('realized_pnl', 0) for t in wins)   / len(wins)   if wins   else 0
    avg_loss  = abs(sum(t.get('realized_pnl', 0) for t in losses) / len(losses)) if losses else 0
    gross_win  = sum(t.get('realized_pnl', 0) for t in wins)
    gross_loss = abs(sum(t.get('realized_pnl', 0) for t in losses))

    times      = [t.get('open_time') or t.get('entry_time') for t in trades
                  if t.get('open_time') or t.get('entry_time')]
    date_range = f"{min(times)[:10]} -> {max(times)[:10]}" if times else "unknown"

    print("=" * 52)
    print("  AGGREGATE RESULTS (all chunks combined)")
    print("=" * 52)
    print(f"  Period        : {date_range}")
    print(f"  Total Trades  : {n}")
    print(f"  Wins          : {len(wins)}")
    print(f"  Losses        : {len(losses)}")
    print(f"  Win Rate      : {len(wins)/n:.1%}")
    print(f"  Total PnL     : ${total_pnl:.2f}")
    print(f"  Avg Win       : ${avg_win:.2f}")
    print(f"  Avg Loss      : ${avg_loss:.2f}")
    if gross_loss:
        print(f"  Profit Factor : {gross_win/gross_loss:.2f}")
    else:
        print(f"  Profit Factor : inf")
    print(f"  Expectancy    : ${(len(wins)/n*avg_win) - (len(losses)/n*avg_loss):.2f} / trade")

    phases: dict = {}
    for t in trades:
        ph = t.get('phase', '') or 'Unknown'
        if ph not in phases:
            phases[ph] = {'wins': 0, 'losses': 0, 'pnl': 0.0}
        if t.get('realized_pnl', 0) > 0:
            phases[ph]['wins'] += 1
        else:
            phases[ph]['losses'] += 1
        phases[ph]['pnl'] += t.get('realized_pnl', 0)
    if phases:
        print("")
        print("  BY ENTRY TYPE")
        for ph, st in sorted(phases.items()):
            tot = st['wins'] + st['losses']
            wr  = st['wins'] / tot if tot else 0
            print(f"  {ph:<26} W:{st['wins']} L:{st['losses']}  WR:{wr:.0%}  PnL:${st['pnl']:+.0f}")
    print("=" * 52)


def main():
    import argparse
    from datetime import date as dtdate
    parser = argparse.ArgumentParser(description='MTF PRO Trading Bot')
    parser.add_argument('--mode',       choices=['live', 'backtest'], default='backtest')
    parser.add_argument('--config',     default='config/config.yaml')
    parser.add_argument('--symbol',     help='Single symbol for backtest')
    parser.add_argument('--start',      help='Backtest start YYYY-MM-DD')
    parser.add_argument('--end',        help='Backtest end YYYY-MM-DD')
    parser.add_argument('--plot',       action='store_true', help='Generate charts')
    parser.add_argument('--chunk-days', type=int, default=0,
                        help='Split date range into chunks of N days (0 = no chunking).')
    parser.add_argument('--fresh',      action='store_true',
                        help='Clear existing trade_history.json before starting')
    parser.add_argument('--merge',      action='store_true',
                        help='Print aggregate stats from existing trade_history.json and exit')
    args = parser.parse_args()

    results_dir  = Path('results')
    history_path = results_dir / 'trade_history.json'

    if args.merge:
        _print_aggregate_stats(history_path)
        return

    if args.fresh and history_path.exists():
        history_path.unlink()
        print("Cleared existing trade_history.json")

    if args.mode == 'backtest':
        with open(args.config) as _f:
            _cfg = yaml.safe_load(_f)
        _init_bal = _cfg.get('backtesting', {}).get('initial_balance', 10000)

        if args.symbol:
            start_str = args.start or '2023-01-01'
            end_str   = args.end   or '2024-12-31'

            if args.chunk_days > 0:
                chunk_size  = timedelta(days=args.chunk_days)
                chunk_start = dtdate.fromisoformat(start_str)
                chunk_end_d = dtdate.fromisoformat(end_str)
                chunk_num   = 0

                while chunk_start < chunk_end_d:
                    chunk_end = min(chunk_start + chunk_size - timedelta(days=1), chunk_end_d)
                    chunk_num += 1
                    print(f"\n{'='*52}")
                    print(f"  CHUNK {chunk_num}: {chunk_start} -> {chunk_end}")
                    print(f"{'='*52}")

                    bot = TradingBot(args.config)
                    results = bot.run_backtest(
                        args.symbol, str(chunk_start), str(chunk_end),
                        initial_balance=_init_bal,
                    )
                    if results and results.get('total_trades', 0) > 0:
                        print(bot.generate_report(results))
                        bot._save_results(append=True)
                    else:
                        print(f"  (no trades in this chunk)")

                    chunk_start = chunk_end + timedelta(days=1)

                print(f"\nAll {chunk_num} chunks done.")
                _print_aggregate_stats(history_path)

            else:
                bot = TradingBot(args.config)
                results = bot.run_backtest(
                    args.symbol,
                    start_str,
                    end_str,
                    initial_balance=_init_bal,
                )
                print(bot.generate_report(results))
                bot._save_results(append=False)

                if args.plot:
                    try:
                        from .visualization.chart_plotter import ChartPlotter
                        plotter = ChartPlotter(results_dir='results')
                        plotter.plot_equity_curve(results.get('equity_curve', []))
                        plotter.plot_performance_summary(results)
                        print("Charts saved to results/")
                    except Exception as e:
                        print(f"Charting error: {e}")
        else:
            if history_path.exists() and not args.fresh:
                history_path.unlink()
            backtester = Backtester(args.config)
            backtester.run_full_backtest(start_date=args.start, end_date=args.end)
    else:
        bot = TradingBot(args.config)
        bot.run()


if __name__ == '__main__':
    main()
