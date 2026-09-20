"""
OB + Liquidity Sweep — Live Trader
Timeframe  : M5
Pairs      : EURUSD, AUDUSD
Sessions   : London 07-11 UTC | New York 13-17 UTC
Entry      : Bearish OB sweep — wick below OB low, candle closes back above
Exit       : SL below wick low
             Partial close 50% at 1:1 RR → move SL to breakeven
             Full TP at 2:1 RR (set on MT5 order)

Safety:
  - One position per symbol maximum
  - Position state persisted to ob_state.json (survives restarts)
  - All signals and orders logged to trades_live.csv + trader.log
  - DRY_RUN = True to test signals without placing orders
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import yaml, json, time, logging
from pathlib import Path
from datetime import datetime, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent
with open(BASE / "config" / "mt5.yaml")      as f: MT5_CFG = yaml.safe_load(f)["mt5"]
with open(BASE / "config" / "strategy.yaml") as f: _S      = yaml.safe_load(f)["strategy"]

S = _S["m5"]   # M5 locked live params

SYMBOLS          = _S["symbols"]
SESSIONS         = [tuple(x) for x in _S["sessions"]]
OB_LOOKBACK      = S["ob_lookback"]
IMPULSE_MIN_PIPS = S["impulse_min_pips"]
OB_MAX_AGE       = S["ob_max_age"]
WICK_RATIO_MIN   = S["wick_ratio_min"]
RSI_LEN          = S["rsi_length"]
RSI_MAX          = S["rsi_max_long"]
TP_RR            = S["tp_rr"]
PARTIAL_PCT      = S["partial_pct"]
RISK_PCT         = _S["risk_pct"]

MINTICK     = 0.00001
PIP         = 0.0001
MAGIC       = 20250
BAR_SECONDS = 5 * 60   # M5 = 300 seconds

STATE_FILE  = BASE / "ob_state.json"
LOG_FILE    = BASE / "trader.log"
TRADE_LOG   = BASE / "trades_live.csv"

DRY_RUN     = True    # set True to test without placing orders

# ── LOGGING ────────────────────────────────────────────────────────────────────

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

# ── STATE (persisted across restarts) ──────────────────────────────────────────

def _blank_state():
    return {"ticket": None, "entry": None, "tp1": None,
            "tp2": None, "sl_be": None, "partial_done": False}

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            saved = json.load(f)
        # ensure all symbols present
        for sym in SYMBOLS:
            if sym not in saved:
                saved[sym] = _blank_state()
        return saved
    return {sym: _blank_state() for sym in SYMBOLS}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── MT5 CONNECTION ─────────────────────────────────────────────────────────────

def connect():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    if not mt5.login(MT5_CFG["account"], password=MT5_CFG["password"],
                     server=MT5_CFG["server"]):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
    info = mt5.account_info()
    log.info(f"Connected: {info.login} | {info.server} | "
             f"Balance: ${info.balance:,.2f} | Equity: ${info.equity:,.2f}")
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
    time.sleep(1)

# ── DATA ───────────────────────────────────────────────────────────────────────

def get_bars(symbol, n):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df

def calc_rsi(close):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(alpha=1 / RSI_LEN, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / RSI_LEN, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

# ── SESSION ────────────────────────────────────────────────────────────────────

def in_session():
    h = datetime.now(timezone.utc).hour
    return any(s <= h < e for s, e in SESSIONS)

# ── SIGNAL DETECTION ───────────────────────────────────────────────────────────

def check_signal(symbol):
    """
    Evaluate the last CLOSED M5 bar (bars[-2]).
    bars[-1] is still forming — always skip it.
    Returns signal dict or None.
    """
    bars = get_bars(symbol, OB_LOOKBACK + RSI_LEN + 10)
    if bars is None or len(bars) < OB_LOOKBACK + RSI_LEN + 5:
        return None

    bars["rsi"] = calc_rsi(bars["close"])

    # Last closed bar
    bar     = bars.iloc[-2]
    rsi_val = bars["rsi"].iloc[-2]

    # RSI filter
    if np.isnan(rsi_val) or rsi_val > RSI_MAX:
        return None

    # OB detection — search back from bar[-2]
    ob_low = None
    ob_age = 0
    for j in range(1, OB_LOOKBACK + 1):
        prev = bars.iloc[-2 - j]
        if prev["close"] < prev["open"]:   # bearish candle
            impulse = (bar["close"] - prev["low"]) / PIP
            if impulse >= IMPULSE_MIN_PIPS:
                ob_low = prev["low"]
                ob_age = j
                break

    if ob_low is None or ob_age > OB_MAX_AGE:
        return None

    # Sweep: wick below OB low, close back above
    if not (bar["low"] < ob_low and bar["close"] > ob_low):
        return None

    wick_size    = ob_low - bar["low"]
    candle_range = bar["high"] - bar["low"]
    wick_ratio   = wick_size / candle_range if candle_range > 0 else 0

    if wick_ratio < WICK_RATIO_MIN:
        return None

    # Entry levels
    entry = bar["close"]
    sl    = bar["low"] - 2 * MINTICK
    risk  = entry - sl
    if risk <= 0:
        return None

    tp1 = entry + risk
    tp2 = entry + risk * TP_RR

    return {
        "symbol":      symbol,
        "bar_time":    bar["time"],
        "entry":       round(entry, 5),
        "sl":          round(sl, 5),
        "tp1":         round(tp1, 5),
        "tp2":         round(tp2, 5),
        "rsi":         round(rsi_val, 1),
        "wick_ratio":  round(wick_ratio, 3),
        "ob_age":      ob_age,
    }

# ── ORDER PLACEMENT ────────────────────────────────────────────────────────────

def place_order(signal, state):
    symbol = signal["symbol"]
    info   = mt5.symbol_info(symbol)
    tick   = mt5.symbol_info_tick(symbol)
    acct   = mt5.account_info()

    digits   = info.digits
    contract = info.trade_contract_size

    entry    = tick.ask
    sl_price = round(signal["sl"],  digits)
    tp_price = round(signal["tp2"], digits)   # set full TP on order

    sl_dist = abs(entry - sl_price)
    if sl_dist < info.point:
        log.warning(f"[{symbol}] SL too tight ({sl_dist:.5f}) — skip")
        return

    # Size to 1% risk
    risk_amount = acct.balance * RISK_PCT
    lot = risk_amount / (sl_dist * contract)
    lot = max(info.volume_min, round(lot / info.volume_step) * info.volume_step)
    lot = min(lot, info.volume_max)

    # Recalculate tp1 from actual entry (ask price may differ slightly)
    actual_risk = entry - sl_price
    tp1_price   = round(entry + actual_risk,          digits)
    tp2_price   = round(entry + actual_risk * TP_RR,  digits)

    log.info(f"[{symbol}] SIGNAL | entry~{entry:.5f}  SL={sl_price:.5f}  "
             f"TP1={tp1_price:.5f}  TP2={tp2_price:.5f}  lot={lot:.2f}  "
             f"RSI={signal['rsi']}  wick={signal['wick_ratio']}")

    if DRY_RUN:
        log.info(f"[{symbol}] DRY RUN — order not sent")
        ticket = "DRY"
    else:
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       lot,
            "type":         mt5.ORDER_TYPE_BUY,
            "price":        entry,
            "sl":           sl_price,
            "tp":           tp2_price,
            "deviation":    10,
            "magic":        MAGIC,
            "comment":      "OBSweep",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"[{symbol}] Order FAILED — retcode={result.retcode} | {result.comment}")
            return
        ticket = result.order
        log.info(f"[{symbol}] Order filled — ticket #{ticket}")

    # Persist state for partial TP management
    state[symbol] = {
        "ticket":       ticket,
        "entry":        round(entry, 5),
        "lot":          lot,
        "tp1":          tp1_price,
        "tp2":          tp2_price,
        "sl_be":        round(entry, digits),   # breakeven = entry
        "partial_done": False,
    }
    save_state(state)
    _log_trade(signal, entry, sl_price, tp2_price, lot, ticket)

# ── PARTIAL TP MANAGEMENT ──────────────────────────────────────────────────────

def manage_position(symbol, state):
    """
    Called every bar. If price has reached TP1 and partial not yet taken:
      - Close PARTIAL_PCT% of the position
      - Modify SL to breakeven on remaining
    """
    st = state[symbol]
    if st["ticket"] is None or st["partial_done"]:
        return

    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        # Position closed by MT5 (SL or TP2 hit) — clear state
        log.info(f"[{symbol}] Position closed by MT5 — clearing state")
        state[symbol] = _blank_state()
        save_state(state)
        return

    pos = positions[0]

    # Check if current high (via tick) reached TP1
    tick = mt5.symbol_info_tick(symbol)
    if tick.bid < st["tp1"]:
        return   # TP1 not reached yet

    log.info(f"[{symbol}] TP1 reached ({st['tp1']:.5f}) — taking partial close & moving SL to BE")

    # Close PARTIAL_PCT% of the lot
    info       = mt5.symbol_info(symbol)
    close_lot  = round(pos.volume * (PARTIAL_PCT / 100) / info.volume_step) * info.volume_step
    close_lot  = max(info.volume_min, min(close_lot, pos.volume))

    if not DRY_RUN:
        # Partial close
        close_req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       close_lot,
            "type":         mt5.ORDER_TYPE_SELL,
            "position":     pos.ticket,
            "price":        tick.bid,
            "deviation":    10,
            "magic":        MAGIC,
            "comment":      "OBSweep-Partial",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(close_req)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"[{symbol}] Partial close FAILED — retcode={res.retcode} | {res.comment}")
            return
        log.info(f"[{symbol}] Partial close done — {close_lot} lots at {tick.bid:.5f}")

        # Modify SL to breakeven on remaining position
        modify_req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   symbol,
            "position": pos.ticket,
            "sl":       st["sl_be"],
            "tp":       st["tp2"],
        }
        res2 = mt5.order_send(modify_req)
        if res2.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"[{symbol}] SL→BE modify FAILED — retcode={res2.retcode}")
        else:
            log.info(f"[{symbol}] SL moved to BE ({st['sl_be']:.5f})")
    else:
        log.info(f"[{symbol}] DRY RUN — partial close + BE move not sent")

    state[symbol]["partial_done"] = True
    save_state(state)

# ── SYNC STATE WITH MT5 ────────────────────────────────────────────────────────

def sync_state(state):
    """Clear state for any symbol whose position is no longer open in MT5."""
    for sym in SYMBOLS:
        if state[sym]["ticket"] is None:
            continue
        positions = mt5.positions_get(symbol=sym)
        if not positions:
            log.info(f"[{sym}] No open position found — state cleared")
            state[sym] = _blank_state()
    save_state(state)

# ── POSITION CHECK ─────────────────────────────────────────────────────────────

def has_open_position(symbol):
    positions = mt5.positions_get(symbol=symbol)
    return positions is not None and len(positions) > 0

# ── TRADE LOG ──────────────────────────────────────────────────────────────────

def _log_trade(signal, entry, sl, tp, lot, ticket):
    row = {
        "time":       datetime.now(timezone.utc).isoformat(),
        "symbol":     signal["symbol"],
        "entry":      round(entry, 5),
        "sl":         round(sl, 5),
        "tp2":        round(tp, 5),
        "lot":        lot,
        "rsi":        signal.get("rsi"),
        "wick_ratio": signal.get("wick_ratio"),
        "ticket":     ticket,
    }
    df = pd.DataFrame([row])
    df.to_csv(TRADE_LOG, mode="a", index=False, header=not TRADE_LOG.exists())

# ── BAR TIMING ─────────────────────────────────────────────────────────────────

def seconds_to_next_bar():
    now = datetime.now(timezone.utc).timestamp()
    return BAR_SECONDS - (now % BAR_SECONDS)

# ── MAIN LOOP ──────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("  OB + Liquidity Sweep — Live Trader")
    log.info(f"  Pairs    : {SYMBOLS}")
    log.info(f"  Sessions : London 07-11 UTC | NY 13-17 UTC")
    log.info(f"  Params   : wick>={WICK_RATIO_MIN}  RSI<={RSI_MAX}  "
             f"impulse>={IMPULSE_MIN_PIPS}pip  TP={TP_RR}R  partial={PARTIAL_PCT}%")
    log.info(f"  DRY RUN  : {DRY_RUN}")
    log.info("=" * 60)

    connect()
    state = load_state()
    sync_state(state)
    log.info(f"State loaded: { {s: state[s]['ticket'] for s in SYMBOLS} }")

    while True:
        wait = seconds_to_next_bar()
        log.info(f"Next bar in {wait:.0f}s — sleeping...")
        time.sleep(wait + 2)   # +2s buffer for bar to fully close

        now_utc = datetime.now(timezone.utc)
        log.info(f"--- Bar {now_utc.strftime('%Y-%m-%d %H:%M UTC')} ---")

        # Re-verify MT5 connection
        if mt5.account_info() is None:
            log.warning("MT5 disconnected — reconnecting...")
            connect()

        for symbol in SYMBOLS:
            try:
                # 1) Manage any open position (partial TP check)
                if has_open_position(symbol):
                    manage_position(symbol, state)
                    log.info(f"[{symbol}] Position open — "
                             f"partial={'done' if state[symbol]['partial_done'] else 'pending'}")
                    continue

                # Clear stale state if no position open
                if state[symbol]["ticket"] is not None:
                    state[symbol] = _blank_state()
                    save_state(state)

                # 2) Session filter
                if not in_session():
                    log.info(f"[{symbol}] Out of session — skip")
                    continue

                # 3) Check for new signal
                signal = check_signal(symbol)
                if signal:
                    log.info(f"[{symbol}] OB SWEEP signal | bar={signal['bar_time']} "
                             f"RSI={signal['rsi']} wick={signal['wick_ratio']} "
                             f"ob_age={signal['ob_age']} bars")
                    place_order(signal, state)
                else:
                    log.info(f"[{symbol}] No signal")

            except Exception as e:
                log.error(f"[{symbol}] Error: {e}", exc_info=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Trader stopped by user.")
        mt5.shutdown()
    except Exception as e:
        log.critical(f"Fatal error: {e}", exc_info=True)
        mt5.shutdown()
