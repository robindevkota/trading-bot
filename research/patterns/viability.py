"""Net expectancy of top surviving patterns at the high-WR profile (TP=1xATR,
SL=2xATR), with timeout exits at the 16-bar close and real fee scenarios.

Fee scenarios (round trip, on notional):
  spot_taker    0.20%   (Binance spot, market in/out)
  fut_taker     0.10%   (USDT-M futures, market in/out)
  fut_mixed     0.055%  (maker entry 2bps + taker exit 3.5bps)
Slippage: +0.02% added to every scenario.
ATR filter: only trade when ATR(14)/close > 0.25% so fees stay small vs target.
"""
import numpy as np
import pandas as pd
from binance_data import load
from miner import features

H = 16
TP_R, SL_R = 1.0, 2.0
FEES = {"spot_taker": 0.0020, "fut_taker": 0.0010, "fut_mixed": 0.00055}
SLIP = 0.0002

pd.set_option("display.width", 220)

PATTERNS = {
    "big_bar_up": ("UP", ["big_bar_up"]),
    "sweep_low_vol + downtrend": ("DOWN", ["sweep_low_vol", "downtrend"]),
    "capitulation + at_range_low": ("DOWN", ["capitulation", "at_range_low"]),
    "big_bar_dn + downtrend": ("DOWN", ["big_bar_dn", "downtrend"]),
}


def trade_returns(df, side_up):
    """Per-bar simulated trade return in PRICE % (before fees), high-WR profile.
    Entry at bar close; TP/SL first-touch; timeout -> exit at close[t+H]."""
    c, h, l = df.close.values, df.high.values, df.low.values
    tr = np.maximum(df.high - df.low,
                    np.maximum((df.high - df.close.shift()).abs(),
                               (df.low - df.close.shift()).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().values
    n = len(df)
    ret = np.full(n, np.nan)
    won = np.full(n, np.nan)
    for t in range(20, n - H - 1):
        a = atr[t]
        e = c[t]
        if side_up:
            tp, sl = e + TP_R * a, e - SL_R * a
        else:
            tp, sl = e - TP_R * a, e + SL_R * a
        out = None
        for k in range(1, H + 1):
            hi, lo = h[t + k], l[t + k]
            if side_up:
                if lo <= sl: out = (sl - e) / e; won[t] = 0; break
                if hi >= tp: out = (tp - e) / e; won[t] = 1; break
            else:
                if hi >= sl: out = (e - sl) / e; won[t] = 0; break
                if lo <= tp: out = (e - tp) / e; won[t] = 1; break
        if out is None:
            x = c[t + H]
            out = (x - e) / e if side_up else (e - x) / e
            won[t] = 1 if out > 0 else 0
        ret[t] = out
    return ret, won, atr


def main():
  for sym in ["BTCUSDT", "ETHUSDT"]:
    df = load(sym)
    ret_up, won_up, atr = trade_returns(df, True)
    ret_dn, won_dn, _ = trade_returns(df, False)
    F = features(df, atr)
    F["downtrend"] = ~F["uptrend"]
    atr_pct = atr / df.close.values
    liquid = atr_pct > 0.0025

    valid = np.zeros(len(df), dtype=bool)
    valid[500:len(df) - H - 1] = True
    oos = valid & np.asarray(df.index >= "2024-01-01") & liquid

    rows = []
    for name, (side, parts) in PATTERNS.items():
        cond = oos.copy()
        for p_ in parts:
            cond &= F[p_].values
        r, w = (ret_up, won_up) if side == "UP" else (ret_dn, won_dn)
        sel = ~np.isnan(r) & cond
        n = int(sel.sum())
        if n < 30:
            continue
        gross = r[sel]
        row = dict(pattern=name, side=side, n_oos=n,
                   wr=f"{np.nanmean(w[sel])*100:.1f}%",
                   gross_avg=f"{gross.mean()*100:+.3f}%")
        for fname, fee in FEES.items():
            net = gross - fee - SLIP
            row[fname] = f"{net.mean()*100:+.3f}%"
        row["net_total_futmixed"] = f"{((gross - FEES['fut_mixed'] - SLIP)).sum()*100:+.0f}%"
        rows.append(row)
    print(f"\n==== {sym} — OOS 2024-2026, ATR%>0.25 filter, avg per-trade return ====")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
