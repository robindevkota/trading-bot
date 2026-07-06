"""SMC multi-timeframe miner — H4 context + M15 trigger, 8 FX pairs.

Concepts implemented (all strictly causal — pivots confirm K bars later,
H4 zones only usable after the H4 bar closes):

  H4 context : demand zone  = last bearish H4 candle before an impulsive
               up-move (>1.5 x ATR_H4 over 2 bars); unmitigated until price
               closes below zone low; "active" while price is inside zone.
               supply zone  = mirror.
               H4 trend     = EMA50 vs EMA200 (structure proxy).
  M15 trigger: swing pivots (K=5 bars each side, confirmed K bars later)
               CHoCH up  = close breaks last confirmed swing-high while the
                           structure state is bearish (character change)
               BOS up    = same break while structure already bullish
               SWEEP low = wick below last confirmed swing-low, close above.

Setups tested (LONG side; SHORT is the mirror):
  S1 zone+choch  : inside H4 demand zone AND M15 CHoCH up
  S2 zone+sweep  : inside H4 demand zone AND M15 sweep of lows
  S3 trend+bos   : H4 uptrend AND M15 BOS up
  S4 sweep+choch : M15 sweep low then CHoCH up within 12 bars (no zone)
  S5 zone_touch  : inside H4 demand zone (context alone, baseline)

Outcomes: first-touch, two profiles:
  hiWR profile  TP=2xATR15 SL=4xATR15 (breakeven ~67%)
  smc  profile  TP=2xATR15 SL=1xATR15 (tight stop, breakeven ~33%)
Horizon 64 M15 bars (16h). Costs 1.2-1.8 pips RT.
IS 2018->2023, OOS 2024->2026-07.
"""
import numpy as np
import pandas as pd
import viability as V
from fx_miner import load_fx
from fx_swing import load_h4

K = 5           # pivot confirmation bars
H = 64
COST = {"EURUSD": 0.00012, "GBPUSD": 0.00014, "USDJPY": 0.00012,
        "AUDUSD": 0.00016, "NZDUSD": 0.00018, "USDCHF": 0.00015,
        "USDCAD": 0.00015, "EURJPY": 0.00013}

pd.set_option("display.width", 240)


def pivots(df):
    """Confirmed swing highs/lows. pivot at i becomes known at i+K."""
    h, l = df.high.values, df.low.values
    n = len(df)
    ph = np.full(n, np.nan)   # value of last confirmed swing high, known at i
    pl = np.full(n, np.nan)
    last_h = last_l = np.nan
    for i in range(K, n - K):
        j = i  # candidate pivot at i, confirmed at i+K
        if h[j] == h[j - K:j + K + 1].max():
            if j + K < n:
                last_h_at = j + K
        if l[j] == l[j - K:j + K + 1].min():
            pass
    # vectorized rebuild (loop above kept simple): do it directly
    is_ph = np.zeros(n, dtype=bool)
    is_pl = np.zeros(n, dtype=bool)
    for i in range(K, n - K):
        w_h = h[i - K:i + K + 1]
        w_l = l[i - K:i + K + 1]
        if h[i] == w_h.max() and (w_h == h[i]).sum() == 1:
            is_ph[i] = True
        if l[i] == w_l.min() and (w_l == l[i]).sum() == 1:
            is_pl[i] = True
    for i in range(n):
        if i - K >= 0 and is_ph[i - K]:
            last_h = h[i - K]
        if i - K >= 0 and is_pl[i - K]:
            last_l = l[i - K]
        ph[i] = last_h
        pl[i] = last_l
    return ph, pl


def m15_events(df):
    """CHoCH / BOS / sweep events per bar (evaluated on the bar's close)."""
    c, l, h = df.close.values, df.low.values, df.high.values
    ph, pl = pivots(df)
    n = len(df)
    state = 0  # +1 bullish structure, -1 bearish
    choch_up = np.zeros(n, dtype=bool); choch_dn = np.zeros(n, dtype=bool)
    bos_up = np.zeros(n, dtype=bool);   bos_dn = np.zeros(n, dtype=bool)
    sweep_lo = np.zeros(n, dtype=bool); sweep_hi = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if not np.isnan(pl[i]):
            if l[i] < pl[i] and c[i] > pl[i]:
                sweep_lo[i] = True
        if not np.isnan(ph[i]):
            if h[i] > ph[i] and c[i] < ph[i]:
                sweep_hi[i] = True
        if not np.isnan(ph[i]) and c[i] > ph[i]:
            (bos_up if state == 1 else choch_up)[i] = True
            state = 1
        elif not np.isnan(pl[i]) and c[i] < pl[i]:
            (bos_dn if state == -1 else choch_dn)[i] = True
            state = -1
    return dict(choch_up=choch_up, choch_dn=choch_dn, bos_up=bos_up,
                bos_dn=bos_dn, sweep_lo=sweep_lo, sweep_hi=sweep_hi)


def h4_zones(h4):
    """Per H4 bar: list of active (unmitigated) demand/supply zones known at
    that bar's close. Returns arrays of zone bounds via interval tracking."""
    o, h, l, c = h4.open.values, h4.high.values, h4.low.values, h4.close.values
    tr = np.maximum(h4.high - h4.low,
                    np.maximum((h4.high - h4.close.shift()).abs(),
                               (h4.low - h4.close.shift()).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().values
    n = len(h4)
    demand, supply = [], []          # active zones: (lo, hi, born)
    in_dem = np.zeros(n, dtype=bool)  # price inside an unmitigated demand zone
    in_sup = np.zeros(n, dtype=bool)
    for i in range(3, n):
        # impulse over the last 2 closed bars
        if not np.isnan(atr[i]) and atr[i] > 0:
            up_imp = (c[i] - c[i - 2]) > 1.5 * atr[i]
            dn_imp = (c[i - 2] - c[i]) > 1.5 * atr[i]
            base = i - 2
            if up_imp and c[base] < o[base]:      # last bearish candle = demand
                demand.append((l[base], max(o[base], c[base]), i))
            if dn_imp and c[base] > o[base]:      # last bullish candle = supply
                supply.append((min(o[base], c[base]), h[base], i))
        # mitigate zones
        demand = [(zl, zh, b) for (zl, zh, b) in demand if c[i] >= zl][-20:]
        supply = [(zl, zh, b) for (zl, zh, b) in supply if c[i] <= zh][-20:]
        # is close inside any zone born before this bar?
        in_dem[i] = any(zl <= c[i] <= zh for (zl, zh, b) in demand if b < i)
        in_sup[i] = any(zl <= c[i] <= zh for (zl, zh, b) in supply if b < i)
    ema_f = h4.close.ewm(span=50, adjust=False).mean().values
    ema_s = h4.close.ewm(span=200, adjust=False).mean().values
    return (pd.Series(in_dem, index=h4.index),
            pd.Series(in_sup, index=h4.index),
            pd.Series(ema_f > ema_s, index=h4.index))


def within_recent(a, b, lookback):
    """event b at i AND event a within previous `lookback` bars."""
    a_recent = pd.Series(a).rolling(lookback, min_periods=1).max().shift(1).fillna(0).astype(bool).values
    return b & a_recent


def run():
    results = []
    for sym in COST:
        m15 = load_fx(sym)
        h4 = load_h4(sym)
        ev = m15_events(m15)
        in_dem_h4, in_sup_h4, up_h4 = h4_zones(h4)
        # map H4 state to M15 bars: last H4 bar CLOSED before m15 bar opens
        # (H4 index = bar open time; bar closes 4h later)
        closed = in_dem_h4.copy()
        closed.index = closed.index + pd.Timedelta(hours=4)
        in_dem = closed.reindex(m15.index, method="ffill").fillna(False).values
        closed = in_sup_h4.copy(); closed.index = closed.index + pd.Timedelta(hours=4)
        in_sup = closed.reindex(m15.index, method="ffill").fillna(False).values
        closed = up_h4.copy(); closed.index = closed.index + pd.Timedelta(hours=4)
        h4up = closed.reindex(m15.index, method="ffill").fillna(False).values

        setups = {
            "S1 zone+choch": ("UP", in_dem & ev["choch_up"]),
            "S1s zone+choch dn": ("DOWN", in_sup & ev["choch_dn"]),
            "S2 zone+sweep": ("UP", in_dem & ev["sweep_lo"]),
            "S2s zone+sweep hi": ("DOWN", in_sup & ev["sweep_hi"]),
            "S3 trend+bos": ("UP", h4up & ev["bos_up"]),
            "S3s trend+bos dn": ("DOWN", ~h4up & ev["bos_dn"]),
            "S4 sweep->choch": ("UP", within_recent(ev["sweep_lo"], ev["choch_up"], 12)),
            "S4s sweep->choch dn": ("DOWN", within_recent(ev["sweep_hi"], ev["choch_dn"], 12)),
            "S5 zone touch": ("UP", in_dem),
        }
        for prof, (tp, sl) in {"hiWR(2:4)": (2.0, 4.0), "smc(2:1)": (2.0, 1.0)}.items():
            V.H, V.TP_R, V.SL_R = H, tp, sl
            ru, wu, atr = V.trade_returns(m15, True)
            rd, wd, _ = V.trade_returns(m15, False)
            valid = np.zeros(len(m15), dtype=bool)
            valid[500:len(m15) - H - 1] = True
            is_m = valid & np.asarray(m15.index < "2024-01-01")
            oos_m = valid & np.asarray(m15.index >= "2024-01-01")
            for name, (side, cond) in setups.items():
                r, w = (ru, wu) if side == "UP" else (rd, wd)
                cond2 = cond & ~np.isnan(r)
                for tag, m in (("IS", is_m), ("OOS", oos_m)):
                    sel = cond2 & m
                    n = int(sel.sum())
                    if n < 40:
                        continue
                    g = r[sel]
                    net = g - COST[sym]
                    results.append(dict(sym=sym, profile=prof, setup=name,
                                        period=tag, n=n,
                                        wr=np.nanmean(w[sel]) * 100,
                                        net=net.mean() * 100))
        print(f"{sym} done", flush=True)

    res = pd.DataFrame(results)
    res.to_csv("smc_results.csv", index=False)
    # aggregate: per setup/profile, average across pairs, IS vs OOS
    agg = res.groupby(["setup", "profile", "period"]).agg(
        pairs=("sym", "nunique"), trades=("n", "sum"),
        wr=("wr", "mean"), net=("net", "mean")).round(3)
    print("\n=== AGGREGATE (mean across pairs) ===")
    print(agg.to_string())
    # survivors: setups net-positive OOS on majority of pairs
    oos = res[res.period == "OOS"]
    surv = oos.groupby(["setup", "profile"]).apply(
        lambda x: pd.Series(dict(pairs=len(x), pos=(x.net > 0).sum(),
                                 wr=x.wr.mean(), net=x.net.mean())),
        include_groups=False)
    print("\n=== OOS: pairs net-positive per setup ===")
    print(surv.to_string())


if __name__ == "__main__":
    run()
