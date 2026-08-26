"""
Omarnowick -- No-Wick Candle Strategy -- Backtest
Reads config from config/mt5.yaml and config/strategy.yaml
Full rules: see STRATEGY-NOTES.md

Three setups, all built on the same "no-wick candle" trigger:
  A - Trend continuation: no-wick appears mid-trend after 2 same-direction BOS
  B - Confirmed reversal: CHoCH + confirming BOS, then opposite no-wick
  C - Early reversal: CHoCH only (no confirming BOS yet), then opposite no-wick

Shared mechanics:
  - Swing highs/lows via 2-bar fractal
  - Entry = no-wick candle open, filled on retest within 10 bars
    (adjusted 2 pips early if raw SL distance > 10 pips)
  - SL = reference swing point +/- fixed pip buffer; TP = fixed 1:1 R:R
  - Invalidation while pending: (1) TP level reached before entry touched,
    (2) opposing CHoCH prints before entry touched
  - No-trade session windows (early Asia, late NY)
  - Consolidation filter: no signals unless a clean directional swing
    sequence is active
  - Close-and-flip: a live trade is closed (any P/L) the moment a valid
    OPPOSING Setup B signal appears, and the new signal is taken instead

Usage:
  python backtest.py --start 2025-01-01 --end 2025-12-31 --setups A B C
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import yaml, argparse
from datetime import datetime
from pathlib import Path

# -- CONFIG ----------------------------------------------------------------

BASE = Path(__file__).parent
with open(BASE / "config" / "mt5.yaml")      as f: MT5_CFG = yaml.safe_load(f)["mt5"]
with open(BASE / "config" / "strategy.yaml") as f: S       = yaml.safe_load(f)["strategy"]

FRACTAL_BARS         = S["fractal_bars"]
RETEST_MAX_BARS       = S["retest_max_bars"]
SL_BUFFER_PIPS        = S["sl_buffer_pips"]
LARGE_SL_THRESH_PIPS  = S["large_sl_threshold_pips"]
EARLY_ENTRY_PIPS      = S["early_entry_pips"]
TP_RR                 = S["tp_rr"]
NO_TRADE_WINDOWS      = [tuple(x) for x in S["no_trade_windows"]]
RISK_PCT              = S["risk_pct"]
ACCOUNT_BALANCE       = S["account_balance"]
SYMBOLS               = S["symbols"]
M15_BAR_COUNT         = S["m15_bar_count"]
DEFAULT_SETUPS        = S.get("setups", ["A", "B", "C"])

MT5_TF = mt5.TIMEFRAME_M15


def pip_size(symbol):
    return 0.01 if "JPY" in symbol else 0.0001

def contract_size(symbol):
    return 100_000

# -- MT5 ---------------------------------------------------------------------

def connect():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    if not mt5.login(MT5_CFG["account"], password=MT5_CFG["password"],
                     server=MT5_CFG["server"]):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
    info = mt5.account_info()
    print(f"Connected: {info.login} | {info.server} | balance ${info.balance:,.0f}\n")


def fetch(symbol, date_from, date_to):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, MT5_TF, 0, M15_BAR_COUNT)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No M15 data for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    df = df[(df.index >= pd.Timestamp(date_from, tz="UTC")) &
            (df.index <= pd.Timestamp(date_to,   tz="UTC"))]
    if len(df) == 0:
        raise RuntimeError(f"No M15 data in range {date_from.date()} to {date_to.date()}")
    print(f"  {len(df):,} M15 bars  ({df.index[0].date()} to {df.index[-1].date()})")
    return df.reset_index()

# -- STRUCTURE -----------------------------------------------------------------

def find_fractals(bars, n=2):
    highs = bars["high"].values
    lows  = bars["low"].values
    N = len(bars)
    is_high = np.zeros(N, dtype=bool)
    is_low  = np.zeros(N, dtype=bool)
    for i in range(n, N - n):
        wh = highs[i - n:i + n + 1]
        wl = lows[i - n:i + n + 1]
        if highs[i] == wh.max() and np.argmax(wh) == n:
            is_high[i] = True
        if lows[i] == wl.min() and np.argmin(wl) == n:
            is_low[i] = True
    return is_high, is_low


def in_session_blackout(dt):
    h = dt.hour
    return any(s <= h < e for s, e in NO_TRADE_WINDOWS)


class Swing:
    __slots__ = ("idx", "price", "kind")   # kind: 'H' or 'L'
    def __init__(self, idx, price, kind):
        self.idx, self.price, self.kind = idx, price, kind


class StructureTracker:
    """
    Maintains rolling swing-point history and derives:
      - current trend state: 'up', 'down', or 'range' (consolidation)
      - CHoCH / BOS events per direction
      - reference points needed for each setup's SL
    """
    def __init__(self):
        self.highs = []   # list[Swing]
        self.lows  = []   # list[Swing]
        self.trend = "range"          # 'up' | 'down' | 'range'
        self.bos_streak = 0            # consecutive same-direction BOS count
        self.last_choch_dir = None     # 'down' means CHoCH broke uptrend (bearish choch)
        self.choch_confirmed_bos = False   # has a confirming BOS occurred since last CHoCH?
        self.choch_pivot_price = None      # the HH/LL the move originated from (for setup C)
        self.post_choch_new_swing = None   # the fresh LH/HL formed after CHoCH (for setup B)

    def update(self, idx, is_sh, is_sl, high, low):
        events = []   # list of event dicts this bar may have produced

        if is_sh:
            new_h = Swing(idx, high, "H")
            if self.highs:
                prev = self.highs[-1]
                if new_h.price > prev.price:
                    # higher high
                    if self.trend == "down" and self.last_choch_dir == "up":
                        # this HH is the confirming BOS of an up-reversal
                        self.choch_confirmed_bos = True
                        events.append(("BOS_CONFIRM", "up", new_h))
                    if self.trend != "down":
                        self.bos_streak = self.bos_streak + 1 if self.trend == "up" else 1
                        self.trend = "up"
                    elif self.trend == "down" and self.last_choch_dir == "up":
                        self.trend = "up"
                        self.bos_streak = 1
                else:
                    # lower high while we were in an uptrend -> CHoCH down
                    if self.trend == "up":
                        self.last_choch_dir = "down"
                        self.choch_confirmed_bos = False
                        self.choch_pivot_price = prev.price   # the HH the move came from
                        self.post_choch_new_swing = None
                        self.trend = "range"
                        events.append(("CHOCH", "down", new_h))
                    elif self.last_choch_dir == "down" and not self.choch_confirmed_bos:
                        # this is the fresh LH after a down-CHoCH
                        self.post_choch_new_swing = new_h
            self.highs.append(new_h)

        if is_sl:
            new_l = Swing(idx, low, "L")
            if self.lows:
                prev = self.lows[-1]
                if new_l.price < prev.price:
                    # lower low
                    if self.trend == "up" and self.last_choch_dir == "down":
                        self.choch_confirmed_bos = True
                        events.append(("BOS_CONFIRM", "down", new_l))
                    if self.trend != "up":
                        self.bos_streak = self.bos_streak + 1 if self.trend == "down" else 1
                        self.trend = "down"
                    elif self.trend == "up" and self.last_choch_dir == "down":
                        self.trend = "down"
                        self.bos_streak = 1
                else:
                    # higher low while we were in a downtrend -> CHoCH up
                    if self.trend == "down":
                        self.last_choch_dir = "up"
                        self.choch_confirmed_bos = False
                        self.choch_pivot_price = prev.price   # the LL the move came from
                        self.post_choch_new_swing = None
                        self.trend = "range"
                        events.append(("CHOCH", "up", new_l))
                    elif self.last_choch_dir == "up" and not self.choch_confirmed_bos:
                        self.post_choch_new_swing = new_l
            self.lows.append(new_l)

        return events

    def nearest_hl(self):
        return self.lows[-1].price if self.lows else None

    def nearest_lh(self):
        return self.highs[-1].price if self.highs else None

# -- SETUP DETECTION -------------------------------------------------------------

def check_setup_a(struct, row):
    """Trend continuation: 2 same-direction BOS, no-wick in trend direction."""
    if struct.trend == "up" and struct.bos_streak >= 2:
        if row["close"] > row["open"] and row["open"] == row["low"]:
            hl = struct.nearest_hl()
            if hl is not None and hl < row["open"]:
                return "long", hl, "HL"
    if struct.trend == "down" and struct.bos_streak >= 2:
        if row["close"] < row["open"] and row["open"] == row["high"]:
            lh = struct.nearest_lh()
            if lh is not None and lh > row["open"]:
                return "short", lh, "LH"
    return None


def check_setup_b(struct, row):
    """Confirmed reversal: CHoCH + confirming BOS, then opposite no-wick."""
    if struct.last_choch_dir == "down" and struct.choch_confirmed_bos:
        if row["close"] < row["open"] and row["open"] == row["high"]:
            lh = struct.nearest_lh()
            if lh is not None and lh > row["open"]:
                return "short", lh, "LH"
    if struct.last_choch_dir == "up" and struct.choch_confirmed_bos:
        if row["close"] > row["open"] and row["open"] == row["low"]:
            hl = struct.nearest_hl()
            if hl is not None and hl < row["open"]:
                return "long", hl, "HL"
    return None


def check_setup_c(struct, row):
    """Early reversal: CHoCH only, no confirming BOS yet."""
    if struct.last_choch_dir == "down" and not struct.choch_confirmed_bos:
        if row["close"] < row["open"] and row["open"] == row["high"]:
            if struct.choch_pivot_price is not None and struct.choch_pivot_price > row["open"]:
                return "short", struct.choch_pivot_price, "HH"
    if struct.last_choch_dir == "up" and not struct.choch_confirmed_bos:
        if row["close"] > row["open"] and row["open"] == row["low"]:
            if struct.choch_pivot_price is not None and struct.choch_pivot_price < row["open"]:
                return "long", struct.choch_pivot_price, "LL"
    return None


SETUP_CHECKS = {"A": check_setup_a, "B": check_setup_b, "C": check_setup_c}

# -- BACKTEST ENGINE -----------------------------------------------------------

def run_backtest(symbol, df, active_setups):
    bars = df.copy()
    n = len(bars)
    is_sh, is_sl = find_fractals(bars, FRACTAL_BARS)

    pip = pip_size(symbol)
    contract = contract_size(symbol)
    sl_buffer = SL_BUFFER_PIPS * pip
    large_sl_thresh = LARGE_SL_THRESH_PIPS * pip
    early_entry = EARLY_ENTRY_PIPS * pip

    balance = ACCOUNT_BALANCE
    peak = balance
    max_dd = 0.0
    trades = []

    struct = StructureTracker()
    pending = None    # dict describing a signal waiting for retest
    pos = None         # open position dict

    warmup = FRACTAL_BARS * 2 + 5

    def compute_trade(direction, ref_price, ref_kind, no_wick_open):
        raw_sl = (ref_price - sl_buffer) if direction == "long" else (ref_price + sl_buffer)
        raw_risk = abs(no_wick_open - raw_sl)
        if raw_risk <= 0:
            return None
        if raw_risk > large_sl_thresh:
            entry = no_wick_open + early_entry if direction == "long" else no_wick_open - early_entry
        else:
            entry = no_wick_open
        sl = raw_sl

        # Sanity guard: SL must be on the correct side of entry (below for
        # longs, above for shorts). If the early-entry shift or a stale
        # reference swing point ever puts it on the wrong side, reject the
        # trade rather than record a structurally backwards SL/TP.
        if direction == "long" and sl >= entry:
            return None
        if direction == "short" and sl <= entry:
            return None

        risk = abs(entry - sl)
        if risk <= 0:
            return None
        tp = entry + risk * TP_RR if direction == "long" else entry - risk * TP_RR
        return dict(direction=direction, entry=entry, sl=sl, tp=tp, ref_kind=ref_kind)

    blown_out = False

    for i in range(warmup, n):
        row = bars.iloc[i]

        confirm_idx = i - FRACTAL_BARS
        events = []
        if confirm_idx >= FRACTAL_BARS:
            events = struct.update(confirm_idx, is_sh[confirm_idx], is_sl[confirm_idx],
                                   bars.iloc[confirm_idx]["high"], bars.iloc[confirm_idx]["low"])

        # Account wiped out (margin-call equivalent): stop opening/flipping into
        # new trades, but still let any already-open position get managed to its exit.
        if balance <= 0:
            blown_out = True

        # ── Close-and-flip check: opposing valid Setup B signal while a trade is open ──
        if pos is not None and "B" in active_setups and not blown_out:
            sig = check_setup_b(struct, row)
            if sig is not None:
                sig_dir = sig[0]
                if sig_dir != pos["dir"]:
                    exit_p = row["close"]
                    pnl_pts = (exit_p - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - exit_p)
                    pnl_usd = pnl_pts * pos["lot"] * contract
                    balance += pnl_usd
                    peak = max(peak, balance)
                    dd = (balance - peak) / peak * 100
                    max_dd = min(max_dd, dd)
                    trades.append(dict(
                        symbol=symbol, setup=pos["setup"], time=pos["time"], direction=pos["dir"],
                        entry=round(pos["entry"], 5), sl=round(pos["sl"], 5), tp=round(pos["tp"], 5),
                        exit=round(exit_p, 5), result="FLIP",
                        pnl_pips=round(pnl_pts / pip, 1), pnl_usd=round(pnl_usd, 2),
                        lot=pos["lot"], balance=round(balance, 2),
                    ))
                    pos = None
                    trade = compute_trade(sig_dir, sig[1], sig[2], row["open"])
                    if trade is not None:
                        pending = dict(setup="B", created_idx=i, **trade)

        # ── Manage open position ──────────────────────────────────────
        if pos is not None:
            lo, hi = row["low"], row["high"]
            if pos["dir"] == "long":
                if lo <= pos["sl"]:
                    exit_p, result = pos["sl"], "SL"
                elif hi >= pos["tp"]:
                    exit_p, result = pos["tp"], "TP"
                else:
                    exit_p, result = None, None
            else:
                if hi >= pos["sl"]:
                    exit_p, result = pos["sl"], "SL"
                elif lo <= pos["tp"]:
                    exit_p, result = pos["tp"], "TP"
                else:
                    exit_p, result = None, None

            if exit_p is not None:
                pnl_pts = (exit_p - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - exit_p)
                pnl_usd = pnl_pts * pos["lot"] * contract
                balance += pnl_usd
                peak = max(peak, balance)
                dd = (balance - peak) / peak * 100
                max_dd = min(max_dd, dd)
                trades.append(dict(
                    symbol=symbol, setup=pos["setup"], time=pos["time"], direction=pos["dir"],
                    entry=round(pos["entry"], 5), sl=round(pos["sl"], 5), tp=round(pos["tp"], 5),
                    exit=round(exit_p, 5), result=result,
                    pnl_pips=round(pnl_pts / pip, 1), pnl_usd=round(pnl_usd, 2),
                    lot=pos["lot"], balance=round(balance, 2),
                ))
                pos = None
            continue

        # ── Pending retest tracking + invalidation checks ───────────────
        if pending is not None:
            bars_since = i - pending["created_idx"]
            invalidated = False

            if bars_since > RETEST_MAX_BARS:
                invalidated = True
            else:
                # Invalidation 1: TP reached before entry touched
                if pending["direction"] == "long":
                    if row["high"] >= pending["tp"] and row["low"] > pending["entry"]:
                        invalidated = True
                else:
                    if row["low"] <= pending["tp"] and row["high"] < pending["entry"]:
                        invalidated = True

                # Invalidation 2: opposing CHoCH prints before entry touched
                for ev_type, ev_dir, _ in events:
                    if ev_type == "CHOCH":
                        if pending["direction"] == "long" and ev_dir == "down":
                            invalidated = True
                        elif pending["direction"] == "short" and ev_dir == "up":
                            invalidated = True

            if not invalidated:
                touched = (row["low"] <= pending["entry"] <= row["high"])
                if touched and not in_session_blackout(row["time"]) and balance > 0:
                    risk = abs(pending["entry"] - pending["sl"])
                    lot = max(0.01, round(balance * RISK_PCT / (risk * contract), 2))
                    pos = dict(dir=pending["direction"], time=row["time"], entry=pending["entry"],
                              sl=pending["sl"], tp=pending["tp"], lot=lot, setup=pending["setup"])
                    pending = None
                elif touched:
                    pending = None   # in blackout window at moment of touch -- drop it
            else:
                pending = None

            if pending is not None or pos is not None:
                continue

        if pos is not None:
            continue

        if blown_out:
            continue

        # ── New signal detection (consolidation filter = trend must not be 'range') ──
        if in_session_blackout(row["time"]):
            continue

        for setup_id in active_setups:
            sig = SETUP_CHECKS[setup_id](struct, row)
            if sig is None:
                continue
            direction, ref_price, ref_kind = sig
            trade = compute_trade(direction, ref_price, ref_kind, row["open"])
            if trade is not None:
                pending = dict(setup=setup_id, created_idx=i, **trade)
            break   # only one setup can trigger per bar

    return trades, balance, max_dd

# -- STATS & OUTPUT --------------------------------------------------------------

def calc_stats(key, trades, final_bal, max_dd, start_bal):
    df = pd.DataFrame(trades)
    if len(df) == 0:
        return None, df
    wins = df[df["pnl_usd"] > 0]
    losses = df[df["pnl_usd"] <= 0]
    total = len(df)
    wr = len(wins) / total * 100
    gw = wins["pnl_usd"].sum()
    gl = abs(losses["pnl_usd"].sum())
    pf = gw / gl if gl else float("inf")
    stats = {
        "key": key, "trades": total, "win_rate": round(wr, 1),
        "profit_factor": round(pf, 3), "net_pnl": round(final_bal - start_bal, 2),
        "max_dd": round(max_dd, 2),
        "tp_hits": int((df["result"] == "TP").sum()),
        "sl_hits": int((df["result"] == "SL").sum()),
        "flip_hits": int((df["result"] == "FLIP").sum()),
    }
    return stats, df


def print_summary(all_stats, title):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)
    print(f"  {'KEY':<16} {'TRADES':>7} {'WR%':>6} {'PF':>6} {'NET P&L':>10} {'MAX DD':>8}")
    print("-" * 78)
    for s in all_stats:
        print(f"  {s['key']:<16} {s['trades']:>7} {s['win_rate']:>6.1f} "
              f"{s['profit_factor']:>6.3f} ${s['net_pnl']:>9,.0f} {s['max_dd']:>7.1f}%")
    print("=" * 78)

# -- MAIN ------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Omarnowick No-Wick Candle Backtest")
    parser.add_argument("--start", default="2025-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   default="2025-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--setups", nargs="+", default=DEFAULT_SETUPS, choices=["A", "B", "C"],
                        help="Which setups to test (each run independently, no interaction between setups unless combined)")
    parser.add_argument("--combined", action="store_true",
                        help="Run all chosen setups together in one pass per symbol (interacting: flip rule active), "
                             "instead of isolating each setup separately")
    args = parser.parse_args()

    date_from = datetime.strptime(args.start, "%Y-%m-%d")
    date_to   = datetime.strptime(args.end,   "%Y-%m-%d")
    print(f"Backtest window : {date_from.date()} -> {date_to.date()}")
    print(f"Setups          : {args.setups}  (combined={args.combined})")
    print(f"Params          : fractal={FRACTAL_BARS}  retest<={RETEST_MAX_BARS}bars  "
          f"sl_buf={SL_BUFFER_PIPS}pip  RR={TP_RR}  large_sl>{LARGE_SL_THRESH_PIPS}pip->early{EARLY_ENTRY_PIPS}pip\n")

    connect()

    dfs = {}
    for symbol in SYMBOLS:
        print(f"[{symbol}] Fetching M15 data...", end=" ", flush=True)
        try:
            dfs[symbol] = fetch(symbol, date_from, date_to)
        except RuntimeError as e:
            print(f"SKIP -- {e}")

    all_trades = []

    if args.combined:
        all_stats = []
        for symbol, df in dfs.items():
            print(f"[{symbol}] Running combined backtest {args.setups}...", end=" ", flush=True)
            trades, final_bal, max_dd = run_backtest(symbol, df, args.setups)
            print(f"{len(trades)} trades")
            if trades:
                stats, tdf = calc_stats(symbol, trades, final_bal, max_dd, ACCOUNT_BALANCE)
                if stats:
                    all_stats.append(stats)
                    all_trades.append(tdf)
        print_summary(all_stats, "OMARNOWICK COMBINED  |  M15  |  Fixed 1:1 R:R")
    else:
        # Run each setup in isolation per symbol (no cross-setup interaction, no flip)
        for setup_id in args.setups:
            all_stats = []
            for symbol, df in dfs.items():
                print(f"[{symbol}] Running Setup {setup_id}...", end=" ", flush=True)
                trades, final_bal, max_dd = run_backtest(symbol, df, [setup_id])
                print(f"{len(trades)} trades")
                if trades:
                    stats, tdf = calc_stats(f"{symbol}-{setup_id}", trades, final_bal, max_dd, ACCOUNT_BALANCE)
                    if stats:
                        all_stats.append(stats)
                        all_trades.append(tdf)
            print_summary(all_stats, f"OMARNOWICK SETUP {setup_id}  |  M15  |  Fixed 1:1 R:R")

    if all_trades:
        combined = pd.concat(all_trades, ignore_index=True)
        out = BASE / "trades.csv"
        combined.to_csv(out, index=False)
        print(f"\n  Trade log -> {out}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
