"""
OB + Liquidity Sweep Bot -- Backtest
Timeframe : M15 (validation, 4-year history) | M5 (live params, locked)
Sessions  : London 07-11 UTC | New York 13-17 UTC
Concept   : Detect bearish Order Block -> price sweeps its low with a wick
             but closes back above -> enter LONG, SL below wick, TP 2:1

Usage:
  python backtest.py --tf m15 --start 2022-01-01 --end 2026-03-20   # 4-year validation
  python backtest.py --tf m5  --start 2024-11-13 --end 2026-03-20   # M5 locked params
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import yaml, argparse
from datetime import datetime
from pathlib import Path

# -- CONFIG --------------------------------------------------------------------

BASE = Path(__file__).parent
with open(BASE / "config" / "mt5.yaml")     as f: MT5_CFG = yaml.safe_load(f)["mt5"]
with open(BASE / "config" / "strategy.yaml") as f: _S      = yaml.safe_load(f)["strategy"]

parser_pre = argparse.ArgumentParser(add_help=False)
parser_pre.add_argument("--tf", default="m15", choices=["m5", "m15"])
pre_args, _ = parser_pre.parse_known_args()

TF_KEY = pre_args.tf.lower()
S = _S[TF_KEY]   # load m5 or m15 param block

SESSIONS         = [tuple(x) for x in _S["sessions"]]
OB_LOOKBACK      = S["ob_lookback"]
IMPULSE_MIN_PIPS = S["impulse_min_pips"]
OB_MAX_AGE       = S["ob_max_age"]
WICK_RATIO_MIN   = S["wick_ratio_min"]
RSI_LEN          = S["rsi_length"]
RSI_MAX          = S["rsi_max_long"]
TP_RR            = S["tp_rr"]
PARTIAL_PCT      = S["partial_pct"]

MT5_TF   = mt5.TIMEFRAME_M15 if TF_KEY == "m15" else mt5.TIMEFRAME_M5
TF_LABEL = "M15" if TF_KEY == "m15" else "M5"
RISK_PCT         = _S["risk_pct"]
ACCOUNT_BALANCE  = _S["account_balance"]
SYMBOLS          = _S["symbols"]

MINTICK   = 0.00001   # EURUSD 5-digit broker
CONTRACT  = 100_000   # standard forex lot
PIP       = 0.0001    # 1 pip = 0.0001 for 4/5-digit pairs

# -- HELPERS -------------------------------------------------------------------

def in_session(dt):
    h = dt.hour
    return any(s <= h < e for s, e in SESSIONS)


def calc_rsi(close, period):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

# -- MT5 -----------------------------------------------------------------------

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
    # M1 demo servers don't support copy_rates_range — fetch via position and filter
    rates = mt5.copy_rates_from_pos(symbol, MT5_TF, 0, 99_999)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No M1 data for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    df = df[(df.index >= pd.Timestamp(date_from, tz="UTC")) &
            (df.index <= pd.Timestamp(date_to,   tz="UTC"))]
    if len(df) == 0:
        raise RuntimeError(f"No M1 data in range {date_from.date()} to {date_to.date()} — demo server may not have M1 history that far back")
    print(f"  {len(df):,} {TF_LABEL} bars  ({df.index[0].date()} to {df.index[-1].date()})")
    return df

# -- BACKTEST ENGINE -----------------------------------------------------------

def run_backtest(symbol, df):
    bars = df.copy()
    bars["rsi"] = calc_rsi(bars["close"], RSI_LEN)
    bars = bars.reset_index()
    n    = len(bars)

    balance = ACCOUNT_BALANCE
    peak    = balance
    max_dd  = 0.0
    trades  = []
    pos     = None                          # open position state dict
    warmup  = OB_LOOKBACK + RSI_LEN + 5

    for i in range(warmup, n):
        row = bars.iloc[i]

        # ── Manage open position ───────────────────────────────────────────
        if pos is not None:
            lo, hi = row["low"], row["high"]

            # 1) SL check first (conservative -- bad news before good)
            if lo <= pos["sl"]:
                exit_p  = pos["sl"]
                sl_pts  = exit_p - pos["entry"]           # <= 0 (loss) or 0 (BE)
                rem_pct = 1.0 - PARTIAL_PCT / 100 if pos["partial_done"] else 1.0
                pnl     = sl_pts * pos["lot"] * rem_pct * CONTRACT + pos["partial_pnl"]
                balance += pnl
                peak     = max(peak, balance)
                dd       = (balance - peak) / peak * 100
                max_dd   = min(max_dd, dd)
                pos.update(
                    exit     = round(exit_p, 5),
                    result   = "Partial+BE" if pos["partial_done"] else "SL",
                    pnl_pips = round((exit_p - pos["entry"]) * 10_000, 1),
                    pnl_usd  = round(pnl, 2),
                    balance  = round(balance, 2),
                )
                trades.append({k: v for k, v in pos.items()
                               if k not in ("partial_done", "partial_pnl")})
                pos = None
                continue

            # 2) Partial TP at 1:1 (if not yet taken)
            if not pos["partial_done"] and hi >= pos["tp1"]:
                p_lot               = pos["lot"] * (PARTIAL_PCT / 100)
                p_pnl               = (pos["tp1"] - pos["entry"]) * p_lot * CONTRACT
                pos["partial_pnl"]  = p_pnl
                pos["partial_done"] = True
                pos["sl"]           = pos["entry"]        # move SL to breakeven
                balance            += p_pnl
                peak                = max(peak, balance)

            # 3) TP2 check
            if hi >= pos["tp2"]:
                rem_pct = 1.0 - PARTIAL_PCT / 100 if pos["partial_done"] else 1.0
                tp2_pnl = (pos["tp2"] - pos["entry"]) * pos["lot"] * rem_pct * CONTRACT
                total   = pos["partial_pnl"] + tp2_pnl
                balance += tp2_pnl                        # partial already credited
                peak     = max(peak, balance)
                dd       = (balance - peak) / peak * 100
                max_dd   = min(max_dd, dd)
                pos.update(
                    exit     = round(pos["tp2"], 5),
                    result   = "Partial+TP2" if pos["partial_done"] else "TP2",
                    pnl_pips = round((pos["tp2"] - pos["entry"]) * 10_000, 1),
                    pnl_usd  = round(total, 2),
                    balance  = round(balance, 2),
                )
                trades.append({k: v for k, v in pos.items()
                               if k not in ("partial_done", "partial_pnl")})
                pos = None

            continue   # never look for a new signal while in a trade

        # ── Signal detection ───────────────────────────────────────────────
        if not in_session(row["time"]):
            continue

        rsi_val = row["rsi"]
        if np.isnan(rsi_val) or rsi_val > RSI_MAX:
            continue

        # Find most recent bearish OB with sufficient impulse
        ob_high = ob_low = None
        ob_age  = 0
        for j in range(1, OB_LOOKBACK + 1):
            prev = bars.iloc[i - j]
            if prev["close"] < prev["open"]:                  # bearish candle
                impulse_pips = (row["close"] - prev["low"]) / PIP
                if impulse_pips >= IMPULSE_MIN_PIPS:
                    ob_high = prev["high"]
                    ob_low  = prev["low"]
                    ob_age  = j
                    break

        if ob_low is None or ob_age > OB_MAX_AGE:
            continue

        # Sweep: wick pierces below OB low, candle closes back above
        if not (row["low"] < ob_low and row["close"] > ob_low):
            continue

        wick_size    = ob_low - row["low"]
        candle_range = row["high"] - row["low"]
        wick_ratio   = wick_size / candle_range if candle_range > 0 else 0

        if wick_ratio < WICK_RATIO_MIN:
            continue

        # Entry
        entry = row["close"]
        sl    = row["low"] - 2 * MINTICK
        risk  = entry - sl
        if risk <= 0:
            continue

        tp1 = entry + risk           # 1:1
        tp2 = entry + risk * TP_RR   # 2:1 (or configured multiple)
        lot = max(0.01, round(balance * RISK_PCT / (risk * CONTRACT), 2))

        pos = dict(
            symbol       = symbol,
            time         = row["time"],
            entry        = round(entry, 5),
            sl           = round(sl, 5),
            tp1          = round(tp1, 5),
            tp2          = round(tp2, 5),
            lot          = lot,
            rsi          = round(rsi_val, 1),
            wick_ratio   = round(wick_ratio, 3),
            ob_low       = round(ob_low, 5),
            ob_high      = round(ob_high, 5),
            ob_age_bars  = ob_age,
            partial_done = False,
            partial_pnl  = 0.0,
            exit         = None,
            result       = None,
            pnl_pips     = None,
            pnl_usd      = None,
            balance      = None,
        )

    # Close any trade still open at end of data (at last bar close)
    if pos is not None:
        last     = bars.iloc[-1]
        exit_p   = last["close"]
        sl_pts   = exit_p - pos["entry"]
        rem_pct  = 1.0 - PARTIAL_PCT / 100 if pos["partial_done"] else 1.0
        pnl      = sl_pts * pos["lot"] * rem_pct * CONTRACT + pos["partial_pnl"]
        balance += pnl
        pos.update(
            exit     = round(exit_p, 5),
            result   = "EOD",
            pnl_pips = round((exit_p - pos["entry"]) * 10_000, 1),
            pnl_usd  = round(pnl, 2),
            balance  = round(balance, 2),
        )
        trades.append({k: v for k, v in pos.items()
                       if k not in ("partial_done", "partial_pnl")})

    return trades, balance, max_dd

# -- STATS & OUTPUT ------------------------------------------------------------

def calc_stats(symbol, trades, final_bal, max_dd):
    df     = pd.DataFrame(trades)
    wins   = df[df["pnl_usd"] > 0]
    losses = df[df["pnl_usd"] <= 0]
    total  = len(df)
    if total == 0:
        return None, df

    wr   = len(wins) / total * 100
    gw   = wins["pnl_usd"].sum()
    gl   = abs(losses["pnl_usd"].sum())
    pf   = gw / gl if gl else float("inf")
    avg_w = wins["pnl_usd"].mean()   if len(wins)   else 0
    avg_l = losses["pnl_usd"].mean() if len(losses) else 0
    rr    = abs(avg_w / avg_l) if avg_l else float("inf")
    by_r  = df["result"].value_counts()

    stats = {
        "symbol":        symbol,
        "trades":        total,
        "win_rate":      round(wr, 1),
        "profit_factor": round(pf, 3),
        "avg_rr":        round(rr, 2),
        "net_pnl":       round(final_bal - ACCOUNT_BALANCE, 2),
        "final_bal":     round(final_bal, 2),
        "max_dd":        round(max_dd, 2),
        "avg_win":       round(avg_w, 2),
        "avg_loss":      round(avg_l, 2),
        "tp2_hits":      int(by_r.get("TP2",        0) + by_r.get("Partial+TP2", 0)),
        "sl_hits":       int(by_r.get("SL",         0)),
        "be_hits":       int(by_r.get("Partial+BE", 0)),
        "eod_exits":     int(by_r.get("EOD",        0)),
    }
    return stats, df


def print_summary(all_stats):
    print("\n" + "=" * 70)
    print(f"  OB + LIQUIDITY SWEEP  |  {TF_LABEL}  |  London + NY sessions")
    print("=" * 70)
    print(f"  {'PAIR':<8} {'TRADES':>7} {'WR%':>6} {'PF':>6} {'R:R':>5} "
          f"{'NET P&L':>10} {'MAX DD':>8}")
    print("-" * 70)
    for s in all_stats:
        print(f"  {s['symbol']:<8} {s['trades']:>7} {s['win_rate']:>6.1f} "
              f"{s['profit_factor']:>6.3f} {s['avg_rr']:>5.2f} "
              f"${s['net_pnl']:>9,.0f} {s['max_dd']:>7.1f}%")
    print("=" * 70)


def print_detail(s):
    print(f"\n  [{s['symbol']}]")
    print(f"    Trades : {s['trades']}  |  WR: {s['win_rate']}%  |  PF: {s['profit_factor']}  |  R:R: {s['avg_rr']}")
    print(f"    Net PnL: ${s['net_pnl']:,.2f}  |  Max DD: {s['max_dd']}%")
    print(f"    Exits  : TP2={s['tp2_hits']}  SL={s['sl_hits']}  BE={s['be_hits']}  EOD={s['eod_exits']}")
    print(f"    Avg Win: ${s['avg_win']:,.2f}  |  Avg Loss: ${s['avg_loss']:,.2f}")

# -- MAIN ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OB + Liquidity Sweep Backtest")
    parser.add_argument("--tf",    default="m15", choices=["m5", "m15"], help="Timeframe (m5=live params, m15=validation)")
    parser.add_argument("--start", default="2022-03-11", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   default="2026-03-20", help="End date YYYY-MM-DD")
    args = parser.parse_args()

    date_from = datetime.strptime(args.start, "%Y-%m-%d")
    date_to   = datetime.strptime(args.end,   "%Y-%m-%d")
    print(f"Backtest window : {date_from.date()} -> {date_to.date()}")
    print(f"Sessions        : London 07-11 UTC | New York 13-17 UTC")
    print(f"Params          : OB lookback={OB_LOOKBACK}  impulse>={IMPULSE_MIN_PIPS}pip  "
          f"wick>={WICK_RATIO_MIN}  RSI<={RSI_MAX}  TP={TP_RR}R  partial={PARTIAL_PCT}%\n")

    connect()

    all_stats  = []
    all_trades = []

    for symbol in SYMBOLS:
        print(f"[{symbol}] Fetching {TF_LABEL} data...", end=" ", flush=True)
        try:
            df = fetch(symbol, date_from, date_to)
        except RuntimeError as e:
            print(f"SKIP -- {e}")
            continue

        print(f"[{symbol}] Running backtest...", end=" ", flush=True)
        trades, final_bal, max_dd = run_backtest(symbol, df)
        print(f"{len(trades)} trades")

        if trades:
            stats, tdf = calc_stats(symbol, trades, final_bal, max_dd)
            if stats:
                all_stats.append(stats)
                all_trades.append(tdf)

    if not all_stats:
        print("No results.")
        mt5.shutdown()
        return

    print_summary(all_stats)
    for s in all_stats:
        print_detail(s)

    # Monthly breakdown
    combined = pd.concat(all_trades, ignore_index=True)
    combined["month"] = pd.to_datetime(combined["time"]).dt.to_period("M")
    pivot = (combined.groupby(["month", "symbol"])["pnl_usd"]
             .sum().unstack(fill_value=0).round(0))
    print("\n  Monthly P&L:")
    print(pivot.to_string())

    # Save trade log
    out = BASE / "trades.csv"
    combined.drop(columns="month").to_csv(out, index=False)
    print(f"\n  Trade log -> {out}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
