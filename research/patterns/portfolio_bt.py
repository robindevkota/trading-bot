"""Portfolio backtest — Momentum Ignition basket, 6 symbols, compounding.

Rules (locked from F3/F10):
  signal : 15m close >= 2.5xATR(14) above prev close, ATR%>0.25
  trade  : long at close, TP +2xATR, SL -4xATR, timeout 64 bars at close
  fees   : 0.075% round trip (futures maker entry/taker exit + slippage)
  sizing : risk 0.75% of CURRENT equity per trade (compounding)
  limits : one position per symbol; total notional capped at 3x equity
  mania guard: skip signal if prior-24h return > threshold
               (threshold chosen on IS 2020-2023 only, verified OOS)
"""
import numpy as np
import pandas as pd
from binance_data import load

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
H, TP_R, SL_R = 64, 2.0, 4.0
FEE = 0.00075
RISK = 0.0075
SPLIT = pd.Timestamp("2024-01-01")

pd.set_option("display.width", 220)


def signals(sym):
    """All ignition trades for one symbol: entry/exit times, gross ret, r24h."""
    df = load(sym)
    c, h, l = df.close.values, df.high.values, df.low.values
    tr = np.maximum(df.high - df.low,
                    np.maximum((df.high - df.close.shift()).abs(),
                               (df.low - df.close.shift()).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().values
    ret1 = np.diff(c, prepend=np.nan)
    r24 = pd.Series(c).pct_change(96).values
    idx = df.index
    n = len(df)
    out = []
    for t in range(500, n - H - 1):
        a = atr[t]
        if a / c[t] < 0.0025 or ret1[t] < 2.5 * a:
            continue
        e = c[t]
        tp, sl = e + TP_R * a, e - SL_R * a
        px, reason, k_exit = None, "TIMEOUT", H
        for k in range(1, H + 1):
            if l[t + k] <= sl:
                px, reason, k_exit = sl, "SL", k
                break
            if h[t + k] >= tp:
                px, reason, k_exit = tp, "TP", k
                break
        if px is None:
            px = c[t + H]
        out.append(dict(sym=sym, entry_t=idx[t], exit_t=idx[t + k_exit],
                        gross=(px - e) / e, reason=reason,
                        sl_dist=SL_R * a / e, r24=r24[t]))
    return pd.DataFrame(out)


def portfolio(trades, guard=None, start_eq=10_000.0):
    """Chronological sim: one pos/symbol, notional cap 3x equity, compounding."""
    t = trades.sort_values("entry_t").reset_index(drop=True)
    if guard is not None:
        t = t[t.r24 <= guard].reset_index(drop=True)
    eq = start_eq
    open_pos = {}   # sym -> (exit_t, pnl_frac_of_notional, notional)
    curve, log = [], []
    for _, tr_ in t.iterrows():
        # close positions that exited before this entry
        for s in list(open_pos):
            if open_pos[s][0] <= tr_.entry_t:
                _, net, notion = open_pos.pop(s)
                eq += notion * net
                curve.append((_, eq))
        if tr_.sym in open_pos:
            continue
        used = sum(v[2] for v in open_pos.values())
        notional = eq * RISK / tr_.sl_dist
        if used + notional > 3 * eq:
            continue  # exposure cap
        open_pos[tr_.sym] = (tr_.exit_t, tr_.gross - FEE, notional)
        log.append(dict(entry=tr_.entry_t, exit=tr_.exit_t, sym=tr_.sym,
                        reason=tr_.reason, net=tr_.gross - FEE,
                        notional=notional, eq_at_entry=eq))
    for s in list(open_pos):
        _, net, notion = open_pos.pop(s)
        eq += notion * net
    lg = pd.DataFrame(log)
    lg["pnl"] = lg.net * lg.notional
    return eq, lg


def report(lg, start_eq, label):
    lg = lg.copy()
    lg["equity"] = start_eq + lg.pnl.cumsum()
    lg["year"] = lg.exit.dt.year
    eq_end = lg.equity.iloc[-1]
    yrs = (lg.exit.iloc[-1] - lg.entry.iloc[0]).days / 365.25
    cagr = (eq_end / start_eq) ** (1 / yrs) - 1
    peak = lg.equity.cummax()
    dd = ((lg.equity - peak) / peak).min()
    wr = (lg.pnl > 0).mean() * 100
    print(f"\n── {label} ──")
    print(f"trades {len(lg)}  |  WR {wr:.1f}%  |  final ${eq_end:,.0f} "
          f"(x{eq_end/start_eq:.2f})  |  CAGR {cagr*100:.1f}%  |  maxDD {dd*100:.1f}%"
          f"  |  {len(lg)/yrs/12:.1f} trades/mo")
    yr = lg.groupby("year").agg(trades=("pnl", "size"),
                                wr=("pnl", lambda x: (x > 0).mean() * 100),
                                pnl=("pnl", "sum"))
    yr["ret_on_start_eq"] = yr.pnl / start_eq * 100
    print(yr.round(1).to_string())
    return lg


if __name__ == "__main__":
    allt = pd.concat([signals(s) for s in SYMS], ignore_index=True)
    print(f"signals: {len(allt)} total")

    # ── mania guard: choose threshold on IS only ─────────────────────
    is_t = allt[allt.entry_t < SPLIT]
    print("\nmania-guard grid (IS 2020-2023 only): skip if 24h ret > X")
    for g in [None, 0.10, 0.15, 0.20, 0.25]:
        sel = is_t if g is None else is_t[is_t.r24 <= g]
        net = sel.gross - FEE
        print(f"  X={g}: n={len(sel):4d}  avg net {net.mean()*100:+.3f}%  "
              f"sum {net.sum()*100:+.0f}%")

    # run full portfolio with chosen guard (picked from IS grid above)
    GUARD = 0.15
    for label, guard in [("NO GUARD — full 2020-2026", None),
                         (f"GUARD 24h<{GUARD:.0%} — full 2020-2026", GUARD)]:
        eq, lg = portfolio(allt, guard)
        lg = report(lg, 10_000.0, label)
        if guard == GUARD:
            lg.to_csv("portfolio_trades.csv", index=False)
            oos = lg[lg.entry >= SPLIT]
            print(f"\nOOS-only (2024+): {len(oos)} trades, "
                  f"WR {(oos.pnl>0).mean()*100:.1f}%, pnl ${oos.pnl.sum():,.0f}")
