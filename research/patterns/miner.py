"""Pattern miner: what conditions precede big intraday runs?

Event definition (trade-relevant, not cosmetic):
  UP event at bar t  = over the next 16 bars (4h), price hits +2.0 x ATR%
                       BEFORE it hits -1.0 x ATR%  (a 2:1 RR long wins)
  DOWN event         = mirror image (a 2:1 RR short wins)

Every candidate condition uses ONLY data available at bar t's close.
Validation: lift must persist IS (2020-2023) -> OOS (2024-2026H1) and
ideally across both BTC and ETH.
"""
import numpy as np
import pandas as pd
from binance_data import load

H = 16          # forward horizon: 16 x 15m = 4h
TP_R, SL_R = 2.0, 1.0

pd.set_option("display.width", 250)


def forward_outcome(df):
    """First-touch: +TP_R*ATR% before -SL_R*ATR% (long) and mirror (short)."""
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
        tp_l, sl_l = c[t] + TP_R * a, c[t] - SL_R * a
        tp_s, sl_s = c[t] - TP_R * a, c[t] + SL_R * a
        lw = ls = sw = ss = False
        for k in range(1, H + 1):
            hi, lo = h[t + k], l[t + k]
            if not (lw or ls):
                # conservative: if both hit same bar, count as loss
                if lo <= sl_l: ls = True
                elif hi >= tp_l: lw = True
            if not (sw or ss):
                if hi >= sl_s: ss = True
                elif lo <= tp_s: sw = True
            if (lw or ls) and (sw or ss):
                break
        up[t] = lw
        dn[t] = sw
    return up, dn, atr


def features(df, atr):
    c, h, l, o = df.close, df.high, df.low, df.open
    v, tb, nt = df.volume, df.taker_buy_vol, df.n_trades
    atr_pct = pd.Series(atr, index=df.index) / c

    f = {}
    ret1 = c.pct_change()
    vol_sma = v.rolling(20).mean()
    taker4 = (tb.rolling(4).sum() / v.rolling(4).sum())
    lo20 = l.shift(1).rolling(20).min()
    hi20 = h.shift(1).rolling(20).max()
    lo96 = l.shift(1).rolling(96).min()
    hi96 = h.shift(1).rolling(96).max()
    rng96 = (c - lo96) / (hi96 - lo96)
    bbw = (c.rolling(20).std() / c.rolling(20).mean())
    bbw_pct = bbw.rolling(500).rank(pct=True)
    downs = (ret1 < 0).astype(int)
    consec_dn = downs.groupby((downs == 0).cumsum()).cumsum()
    ups = (ret1 > 0).astype(int)
    consec_up = ups.groupby((ups == 0).cumsum()).cumsum()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    body = (c - o).abs()
    bar_rng = (h - l).replace(0, np.nan)
    lower_wick = (np.minimum(c, o) - l) / bar_rng
    upper_wick = (h - np.maximum(c, o)) / bar_rng

    # ── SMC / liquidity ──────────────────────────────────────────────
    f["sweep_low_20"] = (l < lo20) & (c > lo20)              # sweep & reclaim
    f["sweep_high_20"] = (h > hi20) & (c < hi20)
    f["sweep_low_96"] = (l < lo96) & (c > lo96)              # daily-range sweep
    f["sweep_high_96"] = (h > hi96) & (c < hi96)
    f["sweep_low_vol"] = f["sweep_low_20"] & (v > 2 * vol_sma)
    f["sweep_low_absorb"] = f["sweep_low_20"] & (taker4 > 0.52)

    # ── order flow ───────────────────────────────────────────────────
    f["taker_buy_extreme"] = taker4 > 0.62
    f["taker_sell_extreme"] = taker4 < 0.38
    f["absorption_bottom"] = (c <= lo96 * 1.001) & (taker4 > 0.52)
    f["distribution_top"] = (c >= hi96 * 0.999) & (taker4 < 0.48)

    # ── volume / activity ────────────────────────────────────────────
    f["vol_spike_3x"] = v > 3 * vol_sma
    f["vol_spike_5x"] = v > 5 * vol_sma
    f["trade_burst"] = nt > 3 * nt.rolling(20).mean()
    f["quiet_before"] = v.rolling(8).mean() < 0.5 * vol_sma  # calm before storm

    # ── volatility structure ────────────────────────────────────────
    f["squeeze_10pct"] = bbw_pct < 0.10
    f["squeeze_break_up"] = (bbw_pct.shift(1) < 0.15) & (c > hi20)
    f["squeeze_break_dn"] = (bbw_pct.shift(1) < 0.15) & (c < lo20)
    f["big_bar_up"] = ret1 > 2.5 * atr_pct
    f["big_bar_dn"] = ret1 < -2.5 * atr_pct

    # ── capitulation / exhaustion ────────────────────────────────────
    f["consec_dn_5"] = consec_dn >= 5
    f["consec_up_5"] = consec_up >= 5
    f["capitulation"] = (consec_dn >= 4) & (v > 2.5 * vol_sma)
    f["hammer_at_low"] = (lower_wick > 0.6) & (rng96 < 0.15)
    f["shooting_star_hi"] = (upper_wick > 0.6) & (rng96 > 0.85)

    # ── range position / trend ───────────────────────────────────────
    f["at_range_low"] = rng96 < 0.05
    f["at_range_high"] = rng96 > 0.95
    f["uptrend"] = ema50 > ema200
    f["breakout_24h_hi"] = c > hi96
    f["breakdown_24h_lo"] = c < lo96
    f["breakout_hi_flow"] = (c > hi96) & (taker4 > 0.55)
    f["breakdown_lo_flow"] = (c < lo96) & (taker4 < 0.45)

    # ── time of day (UTC) ────────────────────────────────────────────
    hr = df.index.hour
    f["asia_session"] = pd.Series((hr >= 0) & (hr < 7), index=df.index)
    f["london_open"] = pd.Series((hr >= 7) & (hr < 10), index=df.index)
    f["ny_open"] = pd.Series((hr >= 13) & (hr < 16), index=df.index)
    f["us_close"] = pd.Series((hr >= 20) & (hr < 23), index=df.index)
    f["weekend"] = pd.Series(df.index.dayofweek >= 5, index=df.index)

    return pd.DataFrame(f).fillna(False)


def mine(sym):
    df = load(sym)
    up, dn, atr = forward_outcome(df)
    F = features(df, atr)
    valid = np.zeros(len(df), dtype=bool)
    valid[500:len(df) - H - 1] = True

    is_mask = valid & np.asarray(df.index < "2024-01-01")
    oos_mask = valid & np.asarray(df.index >= "2024-01-01")

    rows = []
    for name in F.columns:
        cond = F[name].values
        for side, ev in (("UP", up), ("DOWN", dn)):
            r = {"pattern": name, "side": side}
            ok = True
            for tag, m in (("is", is_mask), ("oos", oos_mask)):
                sel = cond & m
                n = sel.sum()
                base = ev[m].mean()
                hit = ev[sel].mean() if n > 50 else np.nan
                r[f"{tag}_n"] = int(n)
                r[f"{tag}_base"] = base
                r[f"{tag}_hit"] = hit
                r[f"{tag}_lift"] = hit / base if n > 50 else np.nan
                # binomial z
                if n > 50:
                    se = np.sqrt(base * (1 - base) / n)
                    r[f"{tag}_z"] = (hit - base) / se
                else:
                    ok = False
            if ok:
                rows.append(r)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    res = {}
    for sym in ["BTCUSDT", "ETHUSDT"]:
        r = mine(sym)
        r["sym"] = sym
        res[sym] = r
        print(f"\n════ {sym}  (base rates: IS up {r.is_base.iloc[0]:.3f}) ════")

    both = pd.concat(res.values())
    # a pattern is REAL if: z>2 in-sample AND lift>1.1 out-of-sample, same side
    keep = both[(both.is_z > 2) & (both.oos_lift > 1.10)]
    keep = keep.sort_values("oos_lift", ascending=False)
    cols = ["sym", "pattern", "side", "is_n", "is_hit", "is_lift", "is_z",
            "oos_n", "oos_hit", "oos_lift", "oos_z", "is_base", "oos_base"]
    fmtf = {c: "{:.3f}".format for c in cols if c not in
            ("sym", "pattern", "side", "is_n", "oos_n")}
    print("\n─── PATTERNS THAT SURVIVED (IS z>2 AND OOS lift>1.1) ───")
    print(keep[cols].to_string(index=False, formatters=fmtf))
    keep.to_csv("survivors.csv", index=False)

    print("\n─── FAILED IN OOS (looked great in-sample, died after 2024) ───")
    dead = both[(both.is_z > 3) & (both.oos_lift < 1.0)].sort_values("is_z", ascending=False)
    print(dead[cols].head(15).to_string(index=False, formatters=fmtf))
