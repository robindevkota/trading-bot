"""Paper trader — SQUEEZE IGNITION config (F16 surviving configuration).

Live 15m klines from Binance public data API (no keys, no account).
Signal  : bar closes >= 2.5 x ATR(14) above prior close
          AND ATR% > 0.5 (high-volatility only)
          AND close < EMA(672) (below 7-day trend = short-squeeze regime).
Entry   : at signal bar close (fee+slippage modelled: 0.075% round trip).
Exit    : TP +2xATR / SL -4xATR first-touch on candle high/low,
          timeout after 64 bars (16h) at close. Same as the backtest.
Sizing  : EQUAL NOTIONAL, 35% of paper equity per trade (F16: risk-based
          sizing overweights calm signals and inverts the edge).
State   : paper_state.json (restart-safe). Trades: paper_trades.csv.

Run continuously:   python paper_trader.py
Single check cycle: python paper_trader.py --once
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
API = "https://data-api.binance.vision/api/v3/klines"
DIR = os.path.dirname(os.path.abspath(__file__))
STATE_F = os.path.join(DIR, "paper_state.json")
TRADES_F = os.path.join(DIR, "paper_trades.csv")

BIG_BAR = 2.5        # ignition threshold, x ATR
ATR_MIN = 0.005      # ATR% filter — high-volatility signals only (F16)
EMA_SPAN = 672       # 7-day trend; trade only BELOW it (squeeze regime)
TP_R, SL_R = 2.0, 4.0
TIMEOUT_BARS = 64
FEE_RT = 0.00075     # 0.055% fees + 0.02% slippage round trip
NOTIONAL_PCT = 0.35  # equal-notional sizing (F16)
MAX_EXPOSURE = 2.0   # total open notional cap, x equity
START_EQ = 10_000.0


def klines(sym, limit=2000):
    """Fetch up to 2000 closed candles (two pages) for clean EMA(672)."""
    rows = []
    end = ""
    for _ in range(2):
        url = f"{API}?symbol={sym}&interval=15m&limit=1000{end}"
        raw = json.loads(urllib.request.urlopen(url, timeout=20).read())
        if not raw:
            break
        rows = raw + rows
        end = f"&endTime={raw[0][0] - 1}"
        if len(rows) >= limit:
            break
    now_ms = time.time() * 1000
    rows = [r for r in rows if r[6] < now_ms]
    return [dict(t=int(r[0]), o=float(r[1]), h=float(r[2]),
                 l=float(r[3]), c=float(r[4])) for r in rows]


def ema(bars, span):
    k = 2 / (span + 1)
    e = bars[0]["c"]
    for b in bars[1:]:
        e += k * (b["c"] - e)
    return e


def atr14(bars):
    trs, prev_c = [], None
    for b in bars:
        tr = b["h"] - b["l"] if prev_c is None else max(
            b["h"] - b["l"], abs(b["h"] - prev_c), abs(b["l"] - prev_c))
        trs.append(tr)
        prev_c = b["c"]
    a = trs[0]
    for tr in trs[1:]:
        a += (tr - a) / 14
    return a


def load_state():
    if os.path.exists(STATE_F):
        with open(STATE_F) as f:
            return json.load(f)
    return {"equity": START_EQ, "open": {}, "last_signal_bar": {}}


def save_state(s):
    with open(STATE_F, "w") as f:
        json.dump(s, f, indent=1)


def log_trade(row):
    new = not os.path.exists(TRADES_F)
    with open(TRADES_F, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def cycle(state):
    for sym in SYMBOLS:
        try:
            bars = klines(sym)
        except Exception as e:
            print(f"{now()} {sym}: data error {e}", flush=True)
            continue
        if len(bars) < 30:
            continue
        last = bars[-1]
        a = atr14(bars)

        # 1) manage open position with the newest closed bar
        pos = state["open"].get(sym)
        if pos:
            done, px, reason = False, None, None
            new_bars = [b for b in bars if b["t"] > pos["last_bar"]]
            for b in new_bars:
                pos["bars_held"] += 1
                if b["l"] <= pos["sl"]:
                    done, px, reason = True, pos["sl"], "SL"
                elif b["h"] >= pos["tp"]:
                    done, px, reason = True, pos["tp"], "TP"
                elif pos["bars_held"] >= TIMEOUT_BARS:
                    done, px, reason = True, b["c"], "TIMEOUT"
                pos["last_bar"] = b["t"]
                if done:
                    break
            if done:
                gross = (px - pos["entry"]) / pos["entry"]
                pnl = pos["notional"] * (gross - FEE_RT)
                state["equity"] += pnl
                state["open"].pop(sym)
                row = dict(time=now(), sym=sym, entry=pos["entry"], exit=px,
                           reason=reason, pnl=round(pnl, 2),
                           equity=round(state["equity"], 2))
                log_trade(row)
                print(f"{now()} {sym} CLOSED {reason} pnl {pnl:+.2f} "
                      f"eq {state['equity']:.2f}", flush=True)
            continue  # one position per symbol

        # 2) look for a fresh ignition signal on the newest closed bar
        if state["last_signal_bar"].get(sym) == last["t"]:
            continue  # already evaluated this bar
        state["last_signal_bar"][sym] = last["t"]
        prev_c = bars[-2]["c"]
        if a / last["c"] < ATR_MIN:
            continue
        if (last["c"] - prev_c) < BIG_BAR * a:
            continue
        if last["c"] >= ema(bars, EMA_SPAN):
            continue  # only squeeze regime: below 7-day trend
        exposure = sum(p["notional"] for p in state["open"].values())
        notional = state["equity"] * NOTIONAL_PCT
        if exposure + notional > MAX_EXPOSURE * state["equity"]:
            continue
        entry = last["c"]
        sl = entry - SL_R * a
        tp = entry + TP_R * a
        state["open"][sym] = dict(entry=entry, tp=tp, sl=sl,
                                  notional=notional, bars_held=0,
                                  last_bar=last["t"], opened=now())
        print(f"{now()} {sym} IGNITION LONG @ {entry} "
              f"TP {tp:.6g} SL {sl:.6g} notional {notional:.0f}", flush=True)
    save_state(state)


def main():
    state = load_state()
    print(f"{now()} paper trader started — equity {state['equity']:.2f}, "
          f"open: {list(state['open'])}", flush=True)
    if "--once" in sys.argv:
        cycle(state)
        return
    while True:
        cycle(state)
        # wake shortly after each 15m boundary
        wait = 900 - (time.time() % 900) + 10
        time.sleep(wait)


if __name__ == "__main__":
    main()
