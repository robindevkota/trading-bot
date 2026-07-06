"""Stack surviving patterns into combos; measure hit rate at multiple TP/SL
profiles including the high-win-rate profile (small TP, wider SL).

Profiles (TP_R : SL_R in ATR units)  breakeven WR (ex-cost):
  2:1  -> 33.3%     1:1 -> 50%      1:2 -> 66.7%     0.75:1.5 -> 66.7%
"""
import numpy as np
import pandas as pd
from binance_data import load
from miner import features

H = 16
PROFILES = [(2.0, 1.0), (1.0, 1.0), (1.0, 2.0)]

pd.set_option("display.width", 250)


def outcomes(df, tp_r, sl_r):
    c, h, l = df.close.values, df.high.values, df.low.values
    tr = np.maximum(df.high - df.low,
                    np.maximum((df.high - df.close.shift()).abs(),
                               (df.low - df.close.shift()).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().values
    n = len(df)
    up = np.zeros(n, dtype=bool)
    dn = np.zeros(n, dtype=bool)
    for t in range(20, n - H - 1):
        a = atr[t]
        tp_l, sl_l = c[t] + tp_r * a, c[t] - sl_r * a
        tp_s, sl_s = c[t] - tp_r * a, c[t] + sl_r * a
        lw = ls = sw = ss = False
        for k in range(1, H + 1):
            hi, lo = h[t + k], l[t + k]
            if not (lw or ls):
                if lo <= sl_l: ls = True
                elif hi >= tp_l: lw = True
            if not (sw or ss):
                if hi >= sl_s: ss = True
                elif lo <= tp_s: sw = True
            if (lw or ls) and (sw or ss):
                break
        up[t], dn[t] = lw, sw
    return up, dn, atr


COMBOS = {
    # momentum ignition long
    "big_bar_up": ("UP", ["big_bar_up"]),
    "big_bar_up + uptrend": ("UP", ["big_bar_up", "uptrend"]),
    "big_bar_up + squeeze_released": ("UP", ["big_bar_up", "squeeze_10pct"]),
    "breakout_hi_flow + uptrend": ("UP", ["breakout_hi_flow", "uptrend"]),
    # momentum continuation short — the anti-textbook stack
    "sweep_low_vol": ("DOWN", ["sweep_low_vol"]),
    "sweep_low_vol + downtrend": ("DOWN", ["sweep_low_vol", "downtrend"]),
    "capitulation + at_range_low": ("DOWN", ["capitulation", "at_range_low"]),
    "hammer_at_low + downtrend": ("DOWN", ["hammer_at_low", "downtrend"]),
    "breakdown_lo_flow + downtrend": ("DOWN", ["breakdown_lo_flow", "downtrend"]),
    "big_bar_dn + downtrend": ("DOWN", ["big_bar_dn", "downtrend"]),
}


def run(sym):
    df = load(sym)
    tr = np.maximum(df.high - df.low,
                    np.maximum((df.high - df.close.shift()).abs(),
                               (df.low - df.close.shift()).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().values
    F = features(df, atr)
    F["downtrend"] = ~F["uptrend"]

    valid = np.zeros(len(df), dtype=bool)
    valid[500:len(df) - H - 1] = True
    is_m = valid & np.asarray(df.index < "2024-01-01")
    oos_m = valid & np.asarray(df.index >= "2024-01-01")

    out = {}
    for tp_r, sl_r in PROFILES:
        out[(tp_r, sl_r)] = outcomes(df, tp_r, sl_r)

    rows = []
    for name, (side, parts) in COMBOS.items():
        cond = np.ones(len(df), dtype=bool)
        for p_ in parts:
            cond &= F[p_].values
        for (tp_r, sl_r), (up, dn, _) in out.items():
            ev = up if side == "UP" else dn
            be = tp_r / (tp_r + sl_r) * 100  # breakeven WR ignoring costs
            r = dict(combo=name, side=side, rr=f"{tp_r}:{sl_r}", be=f"{be:.0f}%")
            for tag, m in (("is", is_m), ("oos", oos_m)):
                sel = cond & m
                n = int(sel.sum())
                r[f"{tag}_n"] = n
                r[f"{tag}_wr"] = ev[sel].mean() * 100 if n > 30 else np.nan
                r[f"{tag}_edge"] = (ev[sel].mean() * 100 - be) if n > 30 else np.nan
            rows.append(r)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    for sym in ["BTCUSDT", "ETHUSDT"]:
        r = run(sym)
        fmt = {c: "{:.1f}".format for c in ["is_wr", "is_edge", "oos_wr", "oos_edge"]}
        print(f"\n==== {sym} ====   (edge = WR minus breakeven WR, pre-cost, pct-points)")
        print(r.to_string(index=False, formatters=fmt))
