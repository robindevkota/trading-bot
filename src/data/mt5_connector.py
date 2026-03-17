"""
MetaTrader 5 Data Connector
Handles MT5 connection, historical data fetching, and live order execution.
"""

import pandas as pd
import numpy as np
import logging
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

logger = logging.getLogger(__name__)

# Timeframe string → MT5 constant mapping
TIMEFRAME_MAP = {
    '1W':  32769,  # mt5.TIMEFRAME_W1
    '1D':  16408,  # mt5.TIMEFRAME_D1
    '4H':  16388,  # mt5.TIMEFRAME_H4
    '1H':  16385,  # mt5.TIMEFRAME_H1
    '15M': 15,     # mt5.TIMEFRAME_M15
    '5M':  5,      # mt5.TIMEFRAME_M5
    '1M':  1,      # mt5.TIMEFRAME_M1
}

# Default bar count per timeframe for live analysis
DEFAULT_BAR_COUNT = {
    '1W':  104,   # ~2 years
    '1D':  500,   # ~2 years
    '4H':  1000,
    '15M': 5000,
    '1M':  2000,
}


class MT5Connector:
    """
    Manages the connection to MetaTrader 5 and all data/order operations.

    Usage:
        connector = MT5Connector(account=12345, password='pass', server='Demo')
        if connector.connect():
            data = connector.fetch_all_timeframes('EURUSD')
    """

    def __init__(self, account: int = 0, password: str = "",
                 server: str = "", path: str = "", timeout: int = 60000):
        self.account = account
        self.password = password
        self.server = server
        self.path = path
        self.timeout = timeout
        self.connected = False
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Initialize MT5 terminal and optionally log in."""
        if not MT5_AVAILABLE:
            self.logger.error("MetaTrader5 package not installed. Run: pip install MetaTrader5")
            return False

        # Step 1: attach to already-running terminal (no credentials)
        init_kwargs = {}
        if self.path:
            init_kwargs['path'] = self.path
        if self.timeout:
            init_kwargs['timeout'] = self.timeout

        if not mt5.initialize(**init_kwargs):
            self.logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False

        # Step 2: check if already logged into the correct account
        info = mt5.account_info()
        if info is not None and info.login == self.account:
            self.logger.info(f"MT5 attached to running terminal — account {self.account} on {self.server}")
            self.connected = True
            return True

        # Step 3: not logged in yet — call login() explicitly
        if self.account and self.password:
            login_kwargs = {'login': self.account, 'password': self.password}
            if self.server:
                login_kwargs['server'] = self.server
            if not mt5.login(**login_kwargs):
                self.logger.error(f"MT5 login failed: {mt5.last_error()}")
                mt5.shutdown()
                return False

        self.logger.info(f"MT5 connected — account {self.account} on {self.server}")
        self.connected = True
        return True

    def disconnect(self):
        """Shut down MT5 connection."""
        if MT5_AVAILABLE:
            mt5.shutdown()
        self.connected = False
        self.logger.info("MT5 disconnected")

    def is_connected(self) -> bool:
        """Return True if MT5 terminal is connected."""
        if not self.connected or not MT5_AVAILABLE:
            return False
        info = mt5.terminal_info()
        return info is not None and info.connected

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_account_info(self) -> Optional[Dict]:
        """Return current account balance, equity, margin, etc."""
        if not self.connected or not MT5_AVAILABLE:
            return None
        info = mt5.account_info()
        if info is None:
            return None
        return {
            'balance': info.balance,
            'equity': info.equity,
            'margin': info.margin,
            'free_margin': info.margin_free,
            'margin_level': info.margin_level,
            'currency': info.currency,
            'profit': info.profit,
        }

    def get_current_price(self, symbol: str) -> Optional[Dict]:
        """Return current bid/ask/last for a symbol."""
        if not self.connected or not MT5_AVAILABLE:
            return None
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {
            'bid': tick.bid,
            'ask': tick.ask,
            'last': tick.last,
            'time': datetime.fromtimestamp(tick.time),
        }

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        """Return symbol metadata (digits, pip value, contract size, etc.)."""
        if not MT5_AVAILABLE:
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return {
            'name': info.name,
            'digits': info.digits,
            'point': info.point,
            'trade_tick_size': info.trade_tick_size,
            'trade_contract_size': info.trade_contract_size,
            'currency_base': info.currency_base,
            'currency_profit': info.currency_profit,
            'spread': info.spread,
            'volume_min': info.volume_min,
            'volume_max': info.volume_max,
            'volume_step': info.volume_step,
        }

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def fetch_ohlcv(self, symbol: str, timeframe: str,
                    count: int = None,
                    start_date: datetime = None,
                    end_date: datetime = None) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV bars from MT5.

        Args:
            symbol:     e.g. 'EURUSD'
            timeframe:  '1W' | '1D' | '4H' | '15M' | '1M'
            count:      number of most-recent bars (ignores date args if set)
            start_date: range start
            end_date:   range end (None = now)

        Returns:
            DataFrame with index=datetime, columns=[open,high,low,close,volume]
        """
        if not self.connected or not MT5_AVAILABLE:
            self.logger.error("Not connected to MT5")
            return None

        tf = self._get_tf_constant(timeframe)
        if tf is None:
            return None

        if count is not None:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        elif start_date is not None and end_date is not None:
            rates = mt5.copy_rates_range(symbol, tf, start_date, end_date)
        elif start_date is not None:
            bars = DEFAULT_BAR_COUNT.get(timeframe, 500)
            rates = mt5.copy_rates_from(symbol, tf, start_date, bars)
        else:
            bars = DEFAULT_BAR_COUNT.get(timeframe, 500)
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)

        if rates is None or len(rates) == 0:
            self.logger.warning(f"No data returned for {symbol} {timeframe}: {mt5.last_error()}")
            return None

        return self._rates_to_df(rates)

    def fetch_all_timeframes(self, symbol: str,
                             start_date: datetime = None,
                             end_date: datetime = None) -> Dict[str, pd.DataFrame]:
        """
        Fetch all required timeframes for the strategy.

        Returns dict with keys: 'weekly', 'daily', 'h4', 'm15'
        (15M is the execution timeframe; M1 is not used)
        """
        tf_map = {
            'weekly': '1W',
            'daily':  '1D',
            'h4':     '4H',
            'h1':     '1H',
            'm15':    '15M',
        }
        result = {}
        for key, tf in tf_map.items():
            df = self.fetch_ohlcv(symbol, tf, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                result[key] = df
                self.logger.debug(f"  {symbol} {tf}: {len(df)} bars")
            else:
                self.logger.warning(f"  {symbol} {tf}: no data")
        return result

    def fetch_historical_range(self, symbol: str,
                               start_date: str,
                               end_date: str) -> Dict[str, pd.DataFrame]:
        """
        Fetch historical data for backtesting.

        Higher timeframes (W1/D1/H4) get a 3-year lookback for bias context.
        M15 is limited to 120 days before start to avoid MT5 request size limits.
        M1 is not used — 15M is the execution timeframe.
        """
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end   = datetime.strptime(end_date,   '%Y-%m-%d')

        # Large buffer for higher TFs so weekly/daily bias is well-established
        htf_start = start - timedelta(days=3 * 365)
        # Small buffer for M15 warm-up (300 bars × 15min = 75h ≈ 4 days).
        # Keep this short: MT5 demo M15 history starts ~May 2022; a large buffer
        # on an early start date causes "Invalid params" / empty response.
        m15_start = start - timedelta(days=7)

        self.logger.info(f"Fetching historical data for {symbol}: {start_date} → {end_date}")

        # H1 buffer: enough context for 1H OB lookback (80 bars = ~3 days)
        h1_start = start - timedelta(days=30)

        result = {}
        for key, tf, s in [
            ('weekly', '1W',  htf_start),
            ('daily',  '1D',  htf_start),
            ('h4',     '4H',  htf_start),
            ('h1',     '1H',  h1_start),
            ('m15',    '15M', m15_start),
        ]:
            df = self.fetch_ohlcv(symbol, tf, start_date=s, end_date=end)
            if df is not None and not df.empty:
                result[key] = df
                self.logger.info(f"  {symbol} {tf}: {len(df)} bars  "
                                 f"{df.index[0].date()} → {df.index[-1].date()}")
            else:
                self.logger.warning(f"  {symbol} {tf}: no data")
        return result

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def place_order(self, symbol: str, order_type: str, volume: float,
                    price: float = None, sl: float = None, tp: float = None,
                    comment: str = "CT_Bot") -> Optional[Dict]:
        """
        Place a market order on MT5.

        Args:
            symbol:     Trading symbol
            order_type: 'buy' or 'sell'
            volume:     Lot size
            price:      Entry price (None = current market price)
            sl:         Stop loss price
            tp:         Take profit price

        Returns:
            Dict with ticket/volume/price or None on failure
        """
        if not self.connected or not MT5_AVAILABLE:
            self.logger.error("Not connected to MT5")
            return None

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            self.logger.error(f"Symbol not found: {symbol}")
            return None

        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                self.logger.error(f"Cannot select symbol: {symbol}")
                return None

        tick = mt5.symbol_info_tick(symbol)

        if order_type.lower() == 'buy':
            mt5_type = mt5.ORDER_TYPE_BUY
            exec_price = tick.ask if price is None else price
        else:
            mt5_type = mt5.ORDER_TYPE_SELL
            exec_price = tick.bid if price is None else price

        request = {
            "action":      mt5.TRADE_ACTION_DEAL,
            "symbol":      symbol,
            "volume":      float(volume),
            "type":        mt5_type,
            "price":       exec_price,
            "deviation":   20,
            "magic":       12345,
            "comment":     comment,
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if sl is not None:
            request['sl'] = sl
        if tp is not None:
            request['tp'] = tp

        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.error(f"Order failed [{result.retcode}]: {result.comment}")
            return None

        self.logger.info(f"Order placed: {order_type.upper()} {volume} {symbol} @ {result.price}")
        return {
            'ticket':  result.order,
            'volume':  result.volume,
            'price':   result.price,
            'comment': result.comment,
        }

    def close_position(self, ticket: int) -> bool:
        """Close an open position by ticket number."""
        if not self.connected or not MT5_AVAILABLE:
            return False

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            self.logger.error(f"Position {ticket} not found")
            return False

        pos  = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)

        if pos.type == mt5.POSITION_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action":      mt5.TRADE_ACTION_DEAL,
            "symbol":      pos.symbol,
            "volume":      pos.volume,
            "type":        order_type,
            "position":    ticket,
            "price":       price,
            "deviation":   20,
            "magic":       12345,
            "comment":     "CT_Bot_Close",
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.error(f"Close failed [{result.retcode}]: {result.comment}")
            return False

        self.logger.info(f"Position {ticket} closed")
        return True

    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        """Modify SL/TP of an open position."""
        if not self.connected or not MT5_AVAILABLE:
            return False

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False

        pos = positions[0]
        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   pos.symbol,
            "position": ticket,
            "sl":       sl if sl is not None else pos.sl,
            "tp":       tp if tp is not None else pos.tp,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def get_open_positions(self) -> List[Dict]:
        """Return all currently open positions."""
        if not self.connected or not MT5_AVAILABLE:
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        return [
            {
                'ticket':        pos.ticket,
                'symbol':        pos.symbol,
                'type':          'buy' if pos.type == mt5.POSITION_TYPE_BUY else 'sell',
                'volume':        pos.volume,
                'price_open':    pos.price_open,
                'price_current': pos.price_current,
                'sl':            pos.sl,
                'tp':            pos.tp,
                'profit':        pos.profit,
                'comment':       pos.comment,
            }
            for pos in positions
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_tf_constant(self, timeframe: str):
        """Return the MT5 timeframe constant for a string key."""
        if not MT5_AVAILABLE:
            return TIMEFRAME_MAP.get(timeframe)

        tf_map = {
            '1W':  mt5.TIMEFRAME_W1,
            '1D':  mt5.TIMEFRAME_D1,
            '4H':  mt5.TIMEFRAME_H4,
            '1H':  mt5.TIMEFRAME_H1,
            '15M': mt5.TIMEFRAME_M15,
            '5M':  mt5.TIMEFRAME_M5,
            '1M':  mt5.TIMEFRAME_M1,
        }
        tf = tf_map.get(timeframe)
        if tf is None:
            self.logger.error(f"Unknown timeframe: {timeframe}")
        return tf

    @staticmethod
    def _rates_to_df(rates) -> pd.DataFrame:
        """Convert MT5 numpy rates array to a clean OHLCV DataFrame."""
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.set_index('time')
        df = df.rename(columns={'tick_volume': 'volume'})
        keep = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        return df[keep]


class MockConnector:
    """
    Offline mock connector that generates synthetic OHLCV data.
    Used for testing/development when MT5 is not available.
    """

    def __init__(self):
        self.connected = True
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info("Using MockConnector — synthetic data only")

    def connect(self) -> bool:
        return True

    def disconnect(self):
        pass

    def is_connected(self) -> bool:
        return True

    def get_account_info(self) -> Dict:
        return {
            'balance': 10000.0,
            'equity': 10000.0,
            'margin': 0.0,
            'free_margin': 10000.0,
            'margin_level': 0.0,
            'currency': 'USD',
            'profit': 0.0,
        }

    def get_current_price(self, symbol: str) -> Dict:
        price = 1.1000
        return {'bid': price, 'ask': price + 0.0002, 'last': price, 'time': datetime.now()}

    def fetch_ohlcv(self, symbol: str, timeframe: str,
                    count: int = None, start_date=None, end_date=None) -> pd.DataFrame:
        return self._generate_synthetic(timeframe, count or DEFAULT_BAR_COUNT.get(timeframe, 500))

    def fetch_all_timeframes(self, symbol: str,
                             start_date=None, end_date=None) -> Dict[str, pd.DataFrame]:
        return {
            'weekly': self._generate_synthetic('1W', 104),
            'daily':  self._generate_synthetic('1D', 500),
            'h4':     self._generate_synthetic('4H', 300),
            'h1':     self._generate_synthetic('1H', 1200),
            'm15':    self._generate_synthetic('15M', 5000),
        }

    def fetch_historical_range(self, symbol: str,
                               start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        from datetime import datetime as dt, timedelta
        end = dt.fromisoformat(str(end_date)) if end_date else dt.now()
        start = dt.fromisoformat(str(start_date)) if start_date else (end - timedelta(days=730))
        # 1-year lookback: ensures ≥5 full 84-day oscillation cycles in weekly data
        # so swing detector reliably finds HH/HL patterns
        lookback = start - timedelta(days=365)
        return {
            'weekly': self._generate_range('1W', lookback, end),
            'daily':  self._generate_range('1D', lookback, end),
            'h4':     self._generate_range('4H', lookback, end),
            'h1':     self._generate_range('1H', lookback, end),
            'm15':    self._generate_range('15M', lookback, end),
        }

    def place_order(self, *args, **kwargs) -> Optional[Dict]:
        self.logger.warning("MockConnector: order_send not implemented")
        return None

    def close_position(self, ticket: int) -> bool:
        return True

    def modify_position(self, *args, **kwargs) -> bool:
        return True

    def get_open_positions(self) -> List[Dict]:
        return []

    @staticmethod
    def _generate_synthetic(timeframe: str, count: int, end_dt=None) -> pd.DataFrame:
        """Generate a synthetic trending OHLCV series."""
        np.random.seed(42)
        freq_map = {
            '1W': 'W-SUN', '1D': 'D', '4H': '4h', '15M': '15min', '1M': 'min'
        }
        freq = freq_map.get(timeframe, 'h')
        end = end_dt if end_dt is not None else datetime.now()
        dates = pd.date_range(end=end, periods=count, freq=freq)
        n = len(dates)  # actual length (may differ from count in pandas 3.x)

        # Random walk with slight upward drift
        returns = np.random.randn(n) * 0.002 + 0.0001
        close = 1.1000 * np.exp(np.cumsum(returns))
        high  = close + np.abs(np.random.randn(n) * 0.001)
        low   = close - np.abs(np.random.randn(n) * 0.001)
        open_ = close + np.random.randn(n) * 0.0005

        return pd.DataFrame({
            'open':   open_,
            'high':   high,
            'low':    low,
            'close':  close,
            'volume': np.random.randint(100, 10000, n).astype(float),
        }, index=dates)

    @staticmethod
    def _generate_range(timeframe: str, start_dt, end_dt) -> pd.DataFrame:
        """Generate synthetic OHLCV data between start_dt and end_dt.
        Uses oscillating wave + trend to produce clear HH/HL/LH/LL swing structure.
        """
        np.random.seed(42)
        freq_map = {
            '1W': 'W-SUN', '1D': 'D', '4H': '4h', '15M': '15min', '1M': 'min'
        }
        # bar widths in minutes (for wave period scaling)
        minutes_map = {'1W': 10080, '1D': 1440, '4H': 240, '15M': 15, '1M': 1}

        freq = freq_map.get(timeframe, 'h')
        dates = pd.date_range(start=start_dt, end=end_dt, freq=freq)
        n = len(dates)
        if n == 0:
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

        bar_min = minutes_map.get(timeframe, 60)
        noise_scale = max(0.0001, 0.0008 * (bar_min / 60) ** 0.25)

        # Days since reference date — reliable, consistent regardless of date range
        ref = pd.Timestamp('2023-01-01')
        days = ((dates - ref).total_seconds() / 86400).values  # float days

        # Cycle period: long enough that half-cycle ≥ 6 bars for ALL timeframes
        # 1W bar = 7 days; need ≥ 6 weekly bars per half → half_days ≥ 42 → cycle ≥ 84 days
        cycle_days = 84.0
        phase = 2 * np.pi * (days % cycle_days) / cycle_days

        # Smooth sine wave (not triangle) → very clear smooth swings
        wave = np.sin(phase)          # −1 to +1

        # Large amplitude (5%) overwhelms noise so swing detector reliably finds peaks
        # Gentle 3% uptrend over 2 years to create HH/HL (not LH/LL)
        amp = 0.05
        trend_total = 0.03
        trend = trend_total * days / 730
        # Reduce noise to a tiny fraction of amplitude to avoid disrupting swings
        tiny_noise = np.random.randn(n) * 0.0002
        close = 1.1000 + trend + amp * wave + tiny_noise

        spread = noise_scale * 2
        high  = close + np.abs(np.random.randn(n) * spread)
        low   = close - np.abs(np.random.randn(n) * spread)
        open_ = np.roll(close, 1);  open_[0] = close[0]

        return pd.DataFrame({
            'open':   open_,
            'high':   high,
            'low':    low,
            'close':  close,
            'volume': np.random.randint(100, 10000, n).astype(float),
        }, index=dates)


def create_connector(config: dict) -> 'MT5Connector | MockConnector':
    """
    Factory function: returns a real MT5Connector or MockConnector
    depending on whether MT5 is available and credentials are configured.
    """
    mt5_cfg = config.get('mt5', {})
    account  = mt5_cfg.get('account', 0)
    password = mt5_cfg.get('password', '')
    server   = mt5_cfg.get('server', '')

    if MT5_AVAILABLE and (account or server):
        connector = MT5Connector(
            account=account,
            password=password,
            server=server,
            path=mt5_cfg.get('path', ''),
            timeout=mt5_cfg.get('timeout', 60000),
        )
    else:
        connector = MockConnector()

    return connector
