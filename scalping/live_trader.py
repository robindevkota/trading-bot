"""
3-Candle Scalper v2.1 -- Live Trader
Runs on MT5 demo. Monitors EURUSD + NZDUSD on 5M bars.
Reads all config from config/strategy.yaml and config/mt5.yaml

Safety features:
  - Never opens more than 1 position per symbol
  - Pullback lock persisted to lock_state.json (survives restarts)
  - All signals and orders logged to trades_live.csv and trader.log
  - Dry-run mode: set DRY_RUN = True to simulate without placing orders
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import yaml, json, time, logging
from pathlib import Path
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent
with open(BASE / "config" / "mt5.yaml")      as f: MT5_CFG = yaml.safe_load(f)["mt5"]
with open(BASE / "config" / "strategy.yaml") as f: S       = yaml.safe_load(f)["strategy"]

SYMBOLS           = S["symbols"]
SESSIONS          = [tuple(x) for x in S["sessions"]]
ATR_PERIOD        = S["atr_period"]
MIN_C1_ATR_MULT   = S["min_c1_atr_mult"]
MIN_BODY_PCT      = S["min_body_pct"]
TP_MULT           = S["tp_body_mult"]          # dict per symbol
PULLBACK_MULT     = S["pullback_atr_mult"]      # dict per symbol
RISK_PCT          = S["risk_pct"]["forex"]
EMA_FAST          = S["ema_fast"]
EMA_SLOW          = S["ema_slow"]

LOCK_FILE         = BASE / "lock_state.json"
LOG_FILE          = BASE / "trader.log"
TRADE_LOG         = BASE / "trades_live.csv"

DRY_RUN           = False   # set True to test signals without placing orders
BAR_SECONDS       = 5 * 60  # 5M = 300 seconds

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger()

# ── LOCK STATE (persisted) ────────────────────────────────────────────────────

def load_locks():
    if LOCK_FILE.exists():
        with open(LOCK_FILE) as f:
            return json.load(f)
    return {sym: {"locked": False, "dir": None, "threshold": None} for sym in SYMBOLS}

def save_locks(locks):
    with open(LOCK_FILE, "w") as f:
        json.dump(locks, f, indent=2)

# ── MT5 CONNECTION ────────────────────────────────────────────────────────────

def connect():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    if not mt5.login(MT5_CFG["account"], password=MT5_CFG["password"], server=MT5_CFG["server"]):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
    info = mt5.account_info()
    log.info(f"Connected: {info.login} | {info.server} | Balance: ${info.balance:,.2f} | Equity: ${info.equity:,.2f}")
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
    time.sleep(1)

# ── DATA ──────────────────────────────────────────────────────────────────────

def get_bars(symbol, tf, n):
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df

def calc_atr(df):
    h = df["high"].values; l = df["low"].values; c = df["close"].values
    pc = np.empty_like(c); pc[0] = c[0]; pc[1:] = c[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    alpha = 1.0 / ATR_PERIOD
    atr = np.empty_like(tr); atr[0] = tr[0]
    for i in range(1, len(tr)):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i-1]
    return atr

def get_bias(symbol):
    """Returns +1 bullish, -1 bearish, 0 neutral based on 1H EMA."""
    h1 = get_bars(symbol, mt5.TIMEFRAME_H1, 300)
    if h1 is None:
        return 0
    c = h1["close"].values
    a_fast = 2 / (EMA_FAST + 1); a_slow = 2 / (EMA_SLOW + 1)
    ema_f = np.empty_like(c); ema_f[0] = c[0]
    ema_s = np.empty_like(c); ema_s[0] = c[0]
    for i in range(1, len(c)):
        ema_f[i] = a_fast * c[i] + (1 - a_fast) * ema_f[i-1]
        ema_s[i] = a_slow * c[i] + (1 - a_slow) * ema_s[i-1]
    last_close = c[-1]
    if ema_f[-1] > ema_s[-1] and last_close > ema_f[-1]:
        return 1
    if ema_f[-1] < ema_s[-1] and last_close < ema_f[-1]:
        return -1
    return 0

# ── SESSION CHECK ─────────────────────────────────────────────────────────────

def in_session():
    now = datetime.now(timezone.utc)
    h = now.hour
    return any(s <= h < e for s, e in SESSIONS)

# ── SIGNAL DETECTION ──────────────────────────────────────────────────────────

def check_signal(symbol, locks):
    """
    Returns signal dict or None.
    Uses the last 4 closed 5M bars (index 1-4 from latest).
    Bar 0 = currently forming (skip). Bar 1 = C3, Bar 2 = C2, Bar 3 = C1.
    """
    m5 = get_bars(symbol, mt5.TIMEFRAME_M5, 50)
    if m5 is None or len(m5) < 5:
        return None

    atr_arr = calc_atr(m5)

    # Use bars 1,2,3 as C3,C2,C1 (bar 0 is still forming)
    c1 = m5.iloc[-4]; c2 = m5.iloc[-3]; c3 = m5.iloc[-2]
    atr = atr_arr[-2]   # ATR at C3

    if np.isnan(atr) or atr == 0:
        return None

    # Pullback lock check
    lk = locks[symbol]
    if lk["locked"] and lk["threshold"] is not None:
        tick = mt5.symbol_info_tick(symbol)
        mid  = (tick.bid + tick.ask) / 2
        if lk["dir"] == "long"  and mid <= lk["threshold"]:
            lk["locked"] = False
            log.info(f"[{symbol}] Pullback confirmed — UNLOCKED")
        elif lk["dir"] == "short" and mid >= lk["threshold"]:
            lk["locked"] = False
            log.info(f"[{symbol}] Pullback confirmed — UNLOCKED")
    if lk["locked"]:
        return None

    # Session filter
    if not in_session():
        return None

    # Trend filter
    bias = get_bias(symbol)
    if bias == 0:
        return None

    # Pattern filter
    def body(row): return abs(row["close"] - row["open"])
    c1b = body(c1); c2b = body(c2); c3b = body(c3)

    if bias == 1:
        if not (c1["close"] > c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"]):
            return None
        if c1b < MIN_C1_ATR_MULT * atr:            return None
        if c2["close"] <= c1["close"]:              return None
        if c3["close"] <= c2["close"]:              return None
        if c2b < MIN_BODY_PCT * c1b:                return None
        if c3b < MIN_BODY_PCT * c1b:                return None
        direction = "long"
    else:
        if not (c1["close"] < c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"]):
            return None
        if c1b < MIN_C1_ATR_MULT * atr:            return None
        if c2["close"] >= c1["close"]:              return None
        if c3["close"] >= c2["close"]:              return None
        if c2b < MIN_BODY_PCT * c1b:                return None
        if c3b < MIN_BODY_PCT * c1b:                return None
        direction = "short"

    avg_body = (c1b + c2b + c3b) / 3
    tp_dist  = avg_body * TP_MULT.get(symbol, 1.5)
    sl       = c3["low"]  if direction == "long" else c3["high"]

    return {
        "symbol":    symbol,
        "direction": direction,
        "sl":        sl,
        "tp_dist":   tp_dist,
        "atr":       atr,
        "c3_time":   c3["time"],
    }

# ── POSITION CHECK ────────────────────────────────────────────────────────────

def has_open_position(symbol):
    positions = mt5.positions_get(symbol=symbol)
    return positions is not None and len(positions) > 0

# ── ORDER PLACEMENT ───────────────────────────────────────────────────────────

def place_order(signal, locks):
    symbol    = signal["symbol"]
    direction = signal["direction"]
    sl        = signal["sl"]
    tp_dist   = signal["tp_dist"]
    atr       = signal["atr"]

    info  = mt5.symbol_info(symbol)
    tick  = mt5.symbol_info_tick(symbol)
    acct  = mt5.account_info()

    digits   = info.digits
    point    = info.point
    contract = info.trade_contract_size   # usually 100000 for forex

    if direction == "long":
        entry    = tick.ask
        sl_price = round(sl, digits)
        tp_price = round(entry + tp_dist, digits)
        order_type = mt5.ORDER_TYPE_BUY
    else:
        entry    = tick.bid
        sl_price = round(sl, digits)
        tp_price = round(entry - tp_dist, digits)
        order_type = mt5.ORDER_TYPE_SELL

    sl_dist = abs(entry - sl_price)
    if sl_dist < point:
        log.warning(f"[{symbol}] SL distance too small ({sl_dist}) — skip")
        return

    # Position sizing — 2% risk
    risk_amount = acct.balance * RISK_PCT
    lot = risk_amount / (sl_dist * contract)
    lot = max(info.volume_min, round(lot / info.volume_step) * info.volume_step)
    lot = min(lot, info.volume_max)

    log.info(f"[{symbol}] SIGNAL {direction.upper()} | entry~{entry:.5f} "
             f"SL={sl_price:.5f} TP={tp_price:.5f} lot={lot:.2f}")

    if DRY_RUN:
        log.info(f"[{symbol}] DRY RUN — order not sent")
        _log_trade(signal, entry, sl_price, tp_price, lot, "DRY_RUN")
    else:
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       lot,
            "type":         order_type,
            "price":        entry,
            "sl":           sl_price,
            "tp":           tp_price,
            "deviation":    10,
            "magic":        20241,
            "comment":      "3CandleScalper",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"[{symbol}] Order filled — ticket #{result.order}")
            _log_trade(signal, entry, sl_price, tp_price, lot, f"#{result.order}")
        else:
            log.error(f"[{symbol}] Order FAILED — retcode={result.retcode} | {result.comment}")
            return

    # Set pullback lock
    pullback_mult = PULLBACK_MULT.get(symbol, 1.0)
    locks[symbol]["locked"]    = True
    locks[symbol]["dir"]       = direction
    locks[symbol]["threshold"] = (entry - pullback_mult * atr if direction == "long"
                                  else entry + pullback_mult * atr)
    save_locks(locks)

# ── TRADE LOG ─────────────────────────────────────────────────────────────────

def _log_trade(signal, entry, sl, tp, lot, ticket):
    row = {
        "time":      datetime.now(timezone.utc).isoformat(),
        "symbol":    signal["symbol"],
        "direction": signal["direction"],
        "entry":     round(entry, 5),
        "sl":        round(sl, 5),
        "tp":        round(tp, 5),
        "lot":       lot,
        "ticket":    ticket,
    }
    df = pd.DataFrame([row])
    df.to_csv(TRADE_LOG, mode="a", index=False,
              header=not TRADE_LOG.exists())

# ── MONITOR CLOSED POSITIONS ──────────────────────────────────────────────────

def update_locks_from_closed(locks):
    """
    Check if any position that was locked has now been closed by MT5
    (SL or TP hit). The lock was already set when we opened — it stays
    until the pullback threshold is reached. Nothing extra needed here,
    but we log the closure.
    """
    # Could enhance by reading MT5 history for exact exit prices
    pass

# ── WAIT FOR BAR CLOSE ───────────────────────────────────────────────────────

def seconds_to_next_bar():
    """Seconds until the next 5M bar opens."""
    now = datetime.now(timezone.utc).timestamp()
    return BAR_SECONDS - (now % BAR_SECONDS)

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("  3-Candle Scalper v2.1 -- Live Trader Starting")
    log.info(f"  Pairs   : {SYMBOLS}")
    log.info(f"  DRY RUN : {DRY_RUN}")
    log.info("=" * 60)

    connect()
    locks = load_locks()
    log.info(f"Lock state loaded: { {s: locks[s]['locked'] for s in SYMBOLS} }")

    while True:
        wait = seconds_to_next_bar()
        log.info(f"Next bar in {wait:.0f}s — sleeping...")
        time.sleep(wait + 2)   # +2s buffer for bar to fully close on server

        now_utc = datetime.now(timezone.utc)
        log.info(f"--- Bar check {now_utc.strftime('%Y-%m-%d %H:%M UTC')} ---")

        # Re-verify connection
        if mt5.account_info() is None:
            log.warning("MT5 disconnected — reconnecting...")
            connect()

        for symbol in SYMBOLS:
            try:
                # Skip if already in a position for this symbol
                if has_open_position(symbol):
                    log.info(f"[{symbol}] Position open — skip signal check")
                    continue

                signal = check_signal(symbol, locks)

                if signal:
                    log.info(f"[{symbol}] Signal confirmed: {signal['direction'].upper()}")
                    place_order(signal, locks)
                else:
                    lk = locks[symbol]
                    reason = "locked" if lk["locked"] else "no pattern"
                    if not in_session():
                        reason = "out of session"
                    log.info(f"[{symbol}] No signal ({reason})")

            except Exception as e:
                log.error(f"[{symbol}] Error: {e}", exc_info=True)

        save_locks(locks)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Trader stopped by user.")
        mt5.shutdown()
    except Exception as e:
        log.critical(f"Fatal error: {e}", exc_info=True)
        mt5.shutdown()
