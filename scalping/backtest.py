"""
3-Candle Scalper v2 -- Backtest
Reads config from config/mt5.yaml and config/strategy.yaml
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import yaml, time, argparse
from datetime import datetime
from pathlib import Path

# -- LOAD CONFIG ---------------------------------------------------------------

BASE = Path(__file__).parent
with open(BASE / "config" / "mt5.yaml")       as f: MT5_CFG = yaml.safe_load(f)["mt5"]
with open(BASE / "config" / "strategy.yaml")  as f: S       = yaml.safe_load(f)["strategy"]

SESSIONS          = [tuple(x) for x in S["sessions"]]
ATR_PERIOD        = S["atr_period"]
MIN_C1_ATR_MULT   = S["min_c1_atr_mult"]
MIN_BODY_PCT      = S["min_body_pct"]
TP_BODY_MULT      = S["tp_body_mult"]   # dict keyed by symbol
ACCOUNT_BALANCE   = S["account_balance"]
SYMBOLS           = S["symbols"]
M5_BARS           = S["m5_bar_count"]
H1_BARS           = S["h1_bar_count"]

TF_MAP = {"M5": mt5.TIMEFRAME_M5, "H1": mt5.TIMEFRAME_H1}

# -- HELPERS -------------------------------------------------------------------

def in_session(dt):
    h = dt.hour
    return any(s <= h < e for s, e in SESSIONS)

def calc_atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()

def body(row):
    return abs(row["close"] - row["open"])

# -- MT5 -----------------------------------------------------------------------

def connect():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    if not mt5.login(MT5_CFG["account"], password=MT5_CFG["password"], server=MT5_CFG["server"]):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
    info = mt5.account_info()
    print(f"Connected: {info.login} | {info.server} | balance ${info.balance:,.0f}\n")

def fetch(symbol, tf_key, date_from, date_to):
    mt5.symbol_select(symbol, True)
    time.sleep(0.5)
    # calculate bars needed to reach date_from from today
    days_back = (datetime.utcnow() - date_from).days + 10
    bars = min(days_back * 24 * (12 if tf_key == "M5" else 1), 99_999)
    rates = mt5.copy_rates_from_pos(symbol, TF_MAP[tf_key], 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    df = df[(df.index >= pd.Timestamp(date_from, tz="UTC")) &
            (df.index <= pd.Timestamp(date_to,   tz="UTC"))]
    if len(df) == 0:
        raise RuntimeError(f"No data for {symbol} in range {date_from.date()} to {date_to.date()} — demo server may not have M5 history that far back")
    return df

def build_bias(h1):
    h1 = h1.copy()
    h1["ema50"]  = h1["close"].ewm(span=S["ema_fast"], adjust=False).mean()
    h1["ema200"] = h1["close"].ewm(span=S["ema_slow"],  adjust=False).mean()
    bull = (h1["ema50"] > h1["ema200"]) & (h1["close"] > h1["ema50"])
    bear = (h1["ema50"] < h1["ema200"]) & (h1["close"] < h1["ema50"])
    h1["bias"] = 0
    h1.loc[bull, "bias"] =  1
    h1.loc[bear, "bias"] = -1
    return h1[["bias"]]

# -- BACKTEST ------------------------------------------------------------------

def run_backtest(symbol, m5, bias_df):
    pullback_mult  = S["pullback_atr_mult"].get(symbol, 1.0)
    tp_mult        = TP_BODY_MULT.get(symbol, 1.5) if isinstance(TP_BODY_MULT, dict) else TP_BODY_MULT
    is_gold        = symbol in ("XAUUSD", "XAGUSD")
    risk_pct       = S["risk_pct"]["gold"] if is_gold else S["risk_pct"]["forex"]
    contract_size  = 100 if is_gold else 100_000

    m5 = m5.copy()
    m5["atr"] = calc_atr(m5, ATR_PERIOD)

    balance  = ACCOUNT_BALANCE
    peak     = balance
    max_dd   = 0.0
    trades_log = []

    locked         = False
    lock_dir       = None
    lock_threshold = None

    bars = m5.reset_index()

    for i in range(3, len(bars) - 1):
        c1 = bars.iloc[i - 2]
        c2 = bars.iloc[i - 1]
        c3 = bars.iloc[i]
        c4 = bars.iloc[i + 1]

        atr = c3["atr"]
        if np.isnan(atr) or atr == 0:
            continue

        if locked:
            if lock_dir == "long"  and c3["low"]  <= lock_threshold: locked = False
            elif lock_dir == "short" and c3["high"] >= lock_threshold: locked = False
        if locked:
            continue

        if not in_session(c3["time"]):
            continue

        h1_at = bias_df.index[bias_df.index <= c3["time"]]
        if len(h1_at) == 0:
            continue
        bias = int(bias_df.loc[h1_at[-1], "bias"])
        if bias == 0:
            continue

        c1b = body(c1); c2b = body(c2); c3b = body(c3)

        if bias == 1:
            if not (c1["close"] > c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"]): continue
            if c1b < MIN_C1_ATR_MULT * atr:    continue
            if c2["close"] <= c1["close"]:      continue
            if c3["close"] <= c2["close"]:      continue
            if c2b < MIN_BODY_PCT * c1b:        continue
            if c3b < MIN_BODY_PCT * c1b:        continue
            direction = "long"
        else:
            if not (c1["close"] < c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"]): continue
            if c1b < MIN_C1_ATR_MULT * atr:    continue
            if c2["close"] >= c1["close"]:      continue
            if c3["close"] >= c2["close"]:      continue
            if c2b < MIN_BODY_PCT * c1b:        continue
            if c3b < MIN_BODY_PCT * c1b:        continue
            direction = "short"

        entry    = c4["open"]
        avg_body = (c1b + c2b + c3b) / 3
        tp_dist  = avg_body * tp_mult
        sl       = c3["low"]  if direction == "long" else c3["high"]
        tp       = entry + tp_dist if direction == "long" else entry - tp_dist
        sl_dist  = abs(entry - sl)
        if sl_dist == 0:
            continue

        lot = max(0.01, round(balance * risk_pct / (sl_dist * contract_size), 2))

        if direction == "long":
            if   c4["low"]  <= sl: exit_p, result = sl, "SL"
            elif c4["high"] >= tp: exit_p, result = tp, "TP"
            else:                  exit_p, result = c4["close"], "C4"
        else:
            if   c4["high"] >= sl: exit_p, result = sl, "SL"
            elif c4["low"]  <= tp: exit_p, result = tp, "TP"
            else:                  exit_p, result = c4["close"], "C4"

        pnl_pts = (exit_p - entry) if direction == "long" else (entry - exit_p)
        pnl_usd = pnl_pts * lot * contract_size
        balance += pnl_usd

        if balance > peak: peak = balance
        dd = (balance - peak) / peak * 100
        if dd < max_dd: max_dd = dd

        trades_log.append({
            "symbol": symbol, "time": c3["time"], "direction": direction,
            "entry": round(entry, 5), "sl": round(sl, 5), "tp": round(tp, 5),
            "exit": round(exit_p, 5), "result": result,
            "pnl_pips": round(pnl_pts * 10_000, 1),
            "pnl_usd":  round(pnl_usd, 2),
            "lot": lot, "balance": round(balance, 2),
        })

        locked         = True
        lock_dir       = direction
        lock_threshold = (exit_p - pullback_mult * atr if direction == "long"
                          else exit_p + pullback_mult * atr)

    return trades_log, balance, max_dd

# -- STATS & PRINT -------------------------------------------------------------

def calc_stats(symbol, logs, final_bal, max_dd):
    df     = pd.DataFrame(logs)
    wins   = df[df["pnl_usd"] > 0]
    losses = df[df["pnl_usd"] <= 0]
    total  = len(df)
    wr     = len(wins) / total * 100
    gw     = wins["pnl_usd"].sum()
    gl     = abs(losses["pnl_usd"].sum())
    pf     = gw / gl if gl else float("inf")
    avg_w  = wins["pnl_usd"].mean()  if len(wins)   else 0
    avg_l  = losses["pnl_usd"].mean() if len(losses) else 0
    rr     = abs(avg_w / avg_l) if avg_l else float("inf")
    by_r   = df["result"].value_counts()
    return {
        "symbol": symbol, "trades": total,
        "win_rate": round(wr, 1), "profit_factor": round(pf, 3),
        "avg_rr": round(rr, 2), "net_pnl": round(final_bal - ACCOUNT_BALANCE, 2),
        "final_bal": round(final_bal, 2), "max_dd": round(max_dd, 2),
        "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
        "tp_hits": int(by_r.get("TP", 0)), "sl_hits": int(by_r.get("SL", 0)),
        "c4_exits": int(by_r.get("C4", 0)),
    }, df

def print_summary(all_stats):
    print("\n" + "=" * 75)
    print("  3-CANDLE SCALPER v2 -- MULTI-PAIR SUMMARY (~1 year, 5M / 1H bias)")
    print("=" * 75)
    print(f"  {'PAIR':<8} {'TRADES':>7} {'WR%':>6} {'PF':>6} {'R:R':>5} "
          f"{'NET P&L':>10} {'MAX DD':>8} {'VERDICT':>8}")
    print("-" * 75)
    for s in all_stats:
        verdict = "PASS" if (s["profit_factor"] >= 1.1 and s["win_rate"] >= 48
                             and s["max_dd"] >= -15 and s["trades"] >= 50) else "REVIEW"
        print(f"  {s['symbol']:<8} {s['trades']:>7} {s['win_rate']:>6.1f} "
              f"{s['profit_factor']:>6.3f} {s['avg_rr']:>5.2f} "
              f"${s['net_pnl']:>9,.0f} {s['max_dd']:>7.1f}% {verdict:>8}")
    print("=" * 75)

def print_detail(s):
    print(f"\n  [{s['symbol']}]")
    print(f"    Trades : {s['trades']}  |  WR: {s['win_rate']}%  |  PF: {s['profit_factor']}  |  R:R: {s['avg_rr']}")
    print(f"    Net PnL: ${s['net_pnl']:,.2f}  |  Max DD: {s['max_dd']}%")
    print(f"    Exits  : TP={s['tp_hits']}  SL={s['sl_hits']}  C4={s['c4_exits']}")

# -- MAIN ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   default="2023-12-31", help="End date YYYY-MM-DD")
    args = parser.parse_args()

    date_from = datetime.strptime(args.start, "%Y-%m-%d")
    date_to   = datetime.strptime(args.end,   "%Y-%m-%d")
    print(f"Backtest window: {date_from.date()} → {date_to.date()}\n")

    connect()

    all_stats  = []
    all_trades = []

    for symbol in SYMBOLS:
        print(f"[{symbol}] Fetching...", end=" ", flush=True)
        try:
            m5      = fetch(symbol, S["exec_tf"], date_from, date_to)
            h1      = fetch(symbol, S["bias_tf"],  date_from, date_to)
            bias_df = build_bias(h1)
            print(f"{len(m5)} bars  ({m5.index[0].date()} to {m5.index[-1].date()})")
        except RuntimeError as e:
            print(f"SKIP -- {e}"); continue

        print(f"[{symbol}] Backtesting...", end=" ", flush=True)
        logs, final_bal, max_dd = run_backtest(symbol, m5, bias_df)
        print(f"{len(logs)} trades")

        if logs:
            stats, df = calc_stats(symbol, logs, final_bal, max_dd)
            all_stats.append(stats)
            all_trades.append(df)

    if not all_stats:
        print("No results."); return

    print_summary(all_stats)
    for s in all_stats:
        print_detail(s)

    # Monthly breakdown
    combined = pd.concat(all_trades, ignore_index=True)
    combined["month"] = combined["time"].dt.to_period("M")
    pivot = combined.groupby(["month", "symbol"])["pnl_usd"].sum().unstack(fill_value=0).round(0)
    print("\n  Monthly P&L per pair:")
    print(pivot.to_string())

    # Save trade log
    out = BASE / "multi_pair_trades.csv"
    combined.drop(columns="month").to_csv(out, index=False)
    print(f"\n  Trade log saved to {out}")

    mt5.shutdown()

if __name__ == "__main__":
    main()
