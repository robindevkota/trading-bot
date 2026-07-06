"""Run the crypto pattern miner on forex M15 (MT5 data, 2018-2026).

Forex has no taker order flow; taker_buy_vol is synthesized at 0.5*volume so
flow features stay neutral (never fire). Volume = tick volume.
IS: 2018 -> 2023-12-31.  OOS: 2024-01-01 -> now. Same event definition.
"""
import os
import numpy as np
import pandas as pd
import miner
from miner import features, forward_outcome

CACHE = os.path.join(os.path.dirname(__file__), "cache")
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]

pd.set_option("display.width", 250)


def load_fx(sym):
    path = os.path.join(CACHE, f"FX_{sym}_15m.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    import MetaTrader5 as mt5
    assert mt5.initialize()
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 200000)
    mt5.shutdown()
    df = pd.DataFrame(r)
    df.index = pd.to_datetime(df.time, unit="s")
    df = df.rename(columns={"tick_volume": "volume"})[
        ["open", "high", "low", "close", "volume"]]
    df["n_trades"] = df.volume
    df["taker_buy_vol"] = df.volume * 0.5  # neutralize flow features
    df.to_parquet(path)
    return df


def mine_fx(sym):
    df = load_fx(sym)
    up, dn, atr = forward_outcome(df)
    F = features(df, atr)
    valid = np.zeros(len(df), dtype=bool)
    valid[500:len(df) - miner.H - 1] = True
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
                r[f"{tag}_hit"] = hit
                r[f"{tag}_lift"] = hit / base if n > 50 else np.nan
                r[f"{tag}_z"] = ((hit - base) / np.sqrt(base * (1 - base) / n)
                                 if n > 50 else np.nan)
                if n <= 50:
                    ok = False
                r[f"{tag}_base"] = base
            if ok:
                rows.append(r)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    allr = []
    for sym in PAIRS:
        r = mine_fx(sym)
        r["sym"] = sym
        allr.append(r)
        print(f"{sym}: base up IS {r.is_base.iloc[0]:.3f} OOS {r.oos_base.iloc[0]:.3f}")
    both = pd.concat(allr)

    cols = ["sym", "pattern", "side", "is_n", "is_hit", "is_lift", "is_z",
            "oos_n", "oos_hit", "oos_lift", "oos_z"]
    fmtf = {c: "{:.3f}".format for c in cols if c not in ("sym", "pattern", "side", "is_n", "oos_n")}

    keep = both[(both.is_z > 2) & (both.oos_lift > 1.10)].sort_values("oos_lift", ascending=False)
    print("\n─── FX SURVIVORS (IS z>2 AND OOS lift>1.1) ───")
    print(keep[cols].to_string(index=False, formatters=fmtf))

    # how did the crypto stars do on FX?
    stars = both[both.pattern.isin(["big_bar_up", "big_bar_dn", "sweep_low_vol",
                                    "capitulation", "hammer_at_low",
                                    "sweep_low_20", "shooting_star_hi"])]
    print("\n─── CRYPTO-STAR PATTERNS ON FX (all, for comparison) ───")
    print(stars.sort_values(["pattern", "sym"])[cols].to_string(index=False, formatters=fmtf))
    keep.to_csv("fx_survivors.csv", index=False)
