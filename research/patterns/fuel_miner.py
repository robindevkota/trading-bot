"""Fuel experiments: does positioning data (OI, funding, long/short ratios)
predict/improve intraday moves?

H1 trap confirmation : ignition works better when crowd was positioned against it
H2 fuel loading      : OI build-up inside a range -> stronger range break
H3 fuel exhaustion   : OI flushing during the move -> move stalls (worse entry)
H4 crowd fading      : L/S ratio extremes alone -> fade the crowd

IS: 2021-06 -> 2023-12-31.  OOS: 2024-01-01 -> 2026-06.  BTC + ETH.
Tradability metric: TP=2xATR SL=4xATR, hold<=16h, net futures fees.
"""
import numpy as np
import pandas as pd
import viability as V
from binance_data import load
from fuel_data import load_metrics, load_funding
from miner import features

V.H, V.TP_R, V.SL_R = 64, 2.0, 4.0
FEE = 0.00055 + 0.0002  # futures maker entry + taker exit + slippage

pd.set_option("display.width", 220)


def build(sym):
    px = load(sym)  # spot 15m klines
    met = load_metrics(sym)
    fund = load_funding(sym)

    # align metrics to bar close, backward (no lookahead)
    met15 = met.resample("15min").last().reindex(px.index).ffill()
    oi = met15.sum_open_interest
    f = pd.DataFrame(index=px.index)
    f["oi_chg_1h"] = oi.pct_change(4)
    f["oi_chg_4h"] = oi.pct_change(16)
    f["oi_chg_24h"] = oi.pct_change(96)
    f["ls_top"] = met15.sum_toptrader_long_short_ratio
    f["ls_glob"] = met15.count_long_short_ratio
    f["taker_ls"] = met15.sum_taker_long_short_vol_ratio
    fund15 = fund.resample("15min").last().reindex(px.index).ffill()
    f["funding"] = fund15.funding
    # rolling 30d percentiles (2880 bars)
    for c in ["ls_top", "ls_glob", "taker_ls", "funding"]:
        f[c + "_pct"] = f[c].rolling(2880, min_periods=500).rank(pct=True)

    ret_up, won_up, atr = V.trade_returns(px, True)
    ret_dn, won_dn, _ = V.trade_returns(px, False)
    F = features(px, atr)
    atr_ok = (atr / px.close.values) > 0.0025

    valid = np.zeros(len(px), dtype=bool)
    valid[500:len(px) - V.H - 1] = True
    valid &= ~f.oi_chg_4h.isna().values  # metrics coverage only
    return px, f, F, (ret_up, won_up), (ret_dn, won_dn), atr_ok, valid


def stat(r, w, sel):
    n = int(sel.sum())
    if n < 25:
        return dict(n=n, wr=np.nan, net=np.nan, tot=np.nan)
    g = r[sel]
    return dict(n=n, wr=np.nanmean(w[sel]) * 100,
                net=(g - FEE).mean() * 100, tot=(g - FEE).sum() * 100)


def run_experiments(sym):
    px, f, F, (ru, wu), (rd, wd), atr_ok, valid = build(sym)
    is_m = valid & np.asarray(px.index < "2024-01-01")
    oos_m = valid & np.asarray(px.index >= "2024-01-01")

    big_up = F.big_bar_up.values & atr_ok
    big_dn = F.big_bar_dn.values & atr_ok

    fund_lo = (f.funding_pct < 0.25).values   # shorts crowded / paying
    fund_hi = (f.funding_pct > 0.75).values   # longs crowded / paying
    glob_lo = (f.ls_glob_pct < 0.25).values   # crowd is short
    glob_hi = (f.ls_glob_pct > 0.75).values   # crowd is long
    oi_up24 = (f.oi_chg_24h > 0.03).values    # fuel loaded
    oi_dump1 = (f.oi_chg_1h < -0.02).values   # fuel burning NOW

    # H2 range coil: tight 24h range + OI build, then break
    rng_hi = px.high.shift(1).rolling(96).max()
    rng_lo = px.low.shift(1).rolling(96).min()
    width_pct = ((rng_hi - rng_lo) / px.close).rolling(2880, min_periods=500).rank(pct=True)
    tight = (width_pct < 0.25).shift(1, fill_value=False).values
    brk_up = (px.close > rng_hi).values & tight
    brk_dn = (px.close < rng_lo).values & tight

    tests = [
        # name, direction returns, condition
        ("F3 baseline: ignition long",            (ru, wu), big_up),
        ("H1: ignition + shorts crowded (fund)",  (ru, wu), big_up & fund_lo),
        ("H1: ignition + longs crowded (fund)",   (ru, wu), big_up & fund_hi),
        ("H1: ignition + crowd short (L/S)",      (ru, wu), big_up & glob_lo),
        ("H1: ignition + crowd long (L/S)",       (ru, wu), big_up & glob_hi),
        ("H1s: dn-ignition short + longs crowded",(rd, wd), big_dn & fund_hi),
        ("H2: coil-break up + OI loaded",         (ru, wu), brk_up & oi_up24 & atr_ok),
        ("H2: coil-break up (no OI cond)",        (ru, wu), brk_up & atr_ok),
        ("H2: coil-break dn + OI loaded",         (rd, wd), brk_dn & oi_up24 & atr_ok),
        ("H3: ignition + OI dumping (late?)",     (ru, wu), big_up & oi_dump1),
        ("H3: ignition + OI NOT dumping",         (ru, wu), big_up & ~oi_dump1),
        ("H4: crowd short extreme -> long",       (ru, wu), (f.ls_glob_pct < 0.05).values & atr_ok),
        ("H4: crowd long extreme -> short",       (rd, wd), (f.ls_glob_pct > 0.95).values & atr_ok),
        ("H4: funding extreme neg -> long",       (ru, wu), (f.funding_pct < 0.05).values & atr_ok),
        ("H4: funding extreme pos -> short",      (rd, wd), (f.funding_pct > 0.95).values & atr_ok),
    ]
    rows = []
    for name, (r, w), cond in tests:
        cond = cond & ~np.isnan(r)
        a = stat(r, w, cond & is_m)
        b = stat(r, w, cond & oos_m)
        rows.append(dict(test=name,
                         is_n=a["n"], is_wr=a["wr"], is_net=a["net"],
                         oos_n=b["n"], oos_wr=b["wr"], oos_net=b["net"],
                         oos_tot=b["tot"]))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    for sym in ["BTCUSDT", "ETHUSDT"]:
        r = run_experiments(sym)
        fmt = {c: "{:.1f}".format for c in ["is_wr", "oos_wr", "oos_tot"]}
        fmt.update({c: "{:+.3f}".format for c in ["is_net", "oos_net"]})
        print(f"\n==== {sym} (wr %, net %/trade after fees, tot %) ====")
        print(r.to_string(index=False, formatters=fmt))
