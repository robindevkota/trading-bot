"""FX SWING pattern mining — H4 bars, where spread is ~2% of target.

Trade shape: TP = 2xATR(14,H4), SL = 4xATR, max hold 16 H4 bars (~2.7 days).
Cost: 1.2-1.8 pips RT -> tiny vs 40-80 pip targets.
IS: history -> 2021-12-31.  OOS: 2022-01-01 -> 2026-07 (4.5y untouched).
Features: same universal set (miner.features), flow features neutralized.
"""
import os
import numpy as np
import pandas as pd
import viability as V
from miner import features

CACHE = os.path.join(os.path.dirname(__file__), "cache")
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
         "NZDUSD", "USDCHF", "USDCAD", "EURJPY"]
COST = {"EURUSD": 0.00012, "GBPUSD": 0.00014, "USDJPY": 0.00012,
        "AUDUSD": 0.00016, "NZDUSD": 0.00018, "USDCHF": 0.00015,
        "USDCAD": 0.00015, "EURJPY": 0.00013}

V.H, V.TP_R, V.SL_R = 16, 2.0, 4.0
SPLIT = "2022-01-01"

pd.set_option("display.width", 240)


def load_h4(sym):
    path = os.path.join(CACHE, f"FX_{sym}_4h.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    import MetaTrader5 as mt5
    assert mt5.initialize()
    mt5.symbol_select(sym, True)
    r = None
    for n in (90000, 50000, 30000, 12000):
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, n)
        if r is not None and len(r):
            break
    mt5.shutdown()
    assert r is not None and len(r), sym
    df = pd.DataFrame(r)
    df.index = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={"tick_volume": "volume"})[
        ["open", "high", "low", "close", "volume"]]
    df["n_trades"] = df.volume
    df["taker_buy_vol"] = df.volume * 0.5
    df.to_parquet(path)
    return df


def main():
    # outcome + feature prep per pair
    data = {}
    for sym in PAIRS:
        df = load_h4(sym)
        ret_up, won_up, atr = V.trade_returns(df, True)
        ret_dn, won_dn, _ = V.trade_returns(df, False)
        F = features(df, atr)
        F["downtrend"] = ~F["uptrend"]
        valid = np.zeros(len(df), dtype=bool)
        valid[500:len(df) - V.H - 1] = True
        data[sym] = (df, F, ret_up, won_up, ret_dn, won_dn, valid)
        print(f"{sym} H4: {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()}", flush=True)

    # mine: every feature x side, hit-rate lift IS vs OOS (event = TP-first)
    rows = []
    for sym, (df, F, ru, wu, rd, wd, valid) in data.items():
        is_m = valid & np.asarray(df.index < SPLIT)
        oos_m = valid & np.asarray(df.index >= SPLIT)
        for name in F.columns:
            cond = F[name].values
            for side, (r, w) in (("UP", (ru, wu)), ("DOWN", (rd, wd))):
                ok = ~np.isnan(r)
                rec = {"sym": sym, "pattern": name, "side": side}
                good = True
                for tag, m in (("is", is_m & ok), ("oos", oos_m & ok)):
                    base = np.nanmean(w[m])
                    sel = cond & m
                    n = int(sel.sum())
                    hit = np.nanmean(w[sel]) if n > 50 else np.nan
                    rec[f"{tag}_n"] = n
                    rec[f"{tag}_wr"] = hit
                    rec[f"{tag}_lift"] = hit / base if n > 50 else np.nan
                    rec[f"{tag}_z"] = ((hit - base) / np.sqrt(base * (1 - base) / n)
                                       if n > 50 else np.nan)
                    if n <= 50:
                        good = False
                if good:
                    rows.append(rec)
    res = pd.DataFrame(rows)
    keep = res[(res.is_z > 2) & (res.oos_lift > 1.05)].copy()
    keep = keep.sort_values("oos_lift", ascending=False)
    cols = ["sym", "pattern", "side", "is_n", "is_wr", "is_lift", "is_z",
            "oos_n", "oos_wr", "oos_lift", "oos_z"]
    fmt = {c: "{:.3f}".format for c in cols if c not in ("sym", "pattern", "side", "is_n", "oos_n")}
    print("\n--- H4 SURVIVORS (IS z>2, OOS lift>1.05) ---")
    print(keep[cols].head(40).to_string(index=False, formatters=fmt))
    keep.to_csv("fx_swing_survivors.csv", index=False)

    # net-of-cost viability for patterns surviving on >=3 pairs, same side
    counts = keep.groupby(["pattern", "side"]).sym.nunique()
    multi = counts[counts >= 3]
    print("\n--- patterns surviving on >=3 pairs ---")
    print(multi.to_string())

    print("\n--- NET-OF-SPREAD viability, OOS 2022-2026 ---")
    vrows = []
    for (pat, side) in multi.index:
        for sym, (df, F, ru, wu, rd, wd, valid) in data.items():
            oos_m = valid & np.asarray(df.index >= SPLIT)
            cond = F[pat].values & oos_m
            r, w = (ru, wu) if side == "UP" else (rd, wd)
            sel = cond & ~np.isnan(r)
            n = int(sel.sum())
            if n < 40:
                continue
            g = r[sel]
            net = g - COST[sym]
            vrows.append(dict(pattern=pat, side=side, sym=sym, n_oos=n,
                              wr=f"{np.nanmean(w[sel])*100:.1f}%",
                              net_avg=f"{net.mean()*100:+.4f}%",
                              net_total=f"{net.sum()*100:+.1f}%"))
    v = pd.DataFrame(vrows)
    print(v.to_string(index=False))
    v.to_csv("fx_swing_viability.csv", index=False)


if __name__ == "__main__":
    main()
