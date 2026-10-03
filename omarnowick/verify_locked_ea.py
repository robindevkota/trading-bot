"""verify_locked_ea.py -- bar-by-bar Python replica of OmarNoWick_LOCKED_EA.mq5.

WHY THIS EXISTS
  The EA is a port of the LOCKED v2 no-wick strategy
  (m1_scalper/strategy/omarnowick/LOCKED.md), whose numbers come from
  nowick_deep.py running on the m1_scalper engine (sim.run) at commit
  24b1696 (2026-09-08; checked out as ae8b28c, 2026-09-11 -- the state that
  printed n=1655 / 73.0%).  This file re-implements, line for line, what
  the EA does on every closed M15 bar -- the swing/BOS machine, the
  signal, the filters, the pending retest and the trade -- so its
  decisions can be diffed against nowick_deep's trade dump.

  It runs in two modes:
    --mode deep    reproduce nowick_deep EXACTLY, look-ahead included
                   (chop + SL anchor read swings by PIVOT bar, every signal
                   independent, fill-bar exits judged on the whole bar).
                   Must give 0 mismatches -- proves the port is the same
                   strategy.
    --mode ea      what the EA does: chop + SL anchor read only swings
                   already CONFIRMED by the signal bar, one position per
                   pair.  Diffed against the deep dump, every difference is
                   attributed to one of those honesty changes.

USAGE
  python verify_locked_ea.py --csv <eurusd_m15.csv> --deep <v2_trades.csv>
         [--pair eurusd] [--mode deep|ea|both]

  <eurusd_m15.csv>  the M15 file nowick_deep read (m1_scalper/data at ae8b28c)
  <v2_trades.csv>   nowick_deep.py --dump output from that same checkout
"""
import argparse
import math
import sys

import numpy as np
import pandas as pd

# ---- parameters: the EA's input defaults ---------------------------------
PULLBACK_PIPS = 15.0      # sim.TF_LADDER[900][0]   (M15)
BOOTSTRAP_PIPS = 60.0     # sim.TF_LADDER[900][1]
MIN_PULLBACK_BARS = 3
BOOTSTRAP_MIN_BARS = 5
BODY_MIN_PIPS_FX = 3.0    # nowick_bars(): 3 pips on majors ...
BODY_MIN_PIPS_JPY = 0.3   # ... 0.3 pip on JPY pairs (0.3 * 0.01)
SL_BUFFER_PIPS = 5.0
BIG_SL_PIPS = 10.0
EARLY_ENTRY_PIPS = 2.0
RETEST_BARS = 10
MAX_HOLD = 120
ATR_LEN = 14
BREAK_LOOKBACK = 10
MAX_SL_ATR = 8.0
DEAD_FROM, DEAD_TO = 13, 15
FIRST_SIGNAL_BAR = 20


class Engine:
    """sim.run (24b1696) lines 201-309: the market swing / BOS machine.
    The CHoCH half of sim.run never feeds back into swings or BOS, so it is
    not ported.  Mirrors EngineStep() in the EA."""

    def __init__(self, pip):
        self.min_pb = PULLBACK_PIPS * pip
        self.boot_rng = BOOTSTRAP_PIPS * pip
        self.mode = 0            # 0 boot, 1 huntSL, 2 huntSH
        self.lastSH = self.lastSL = None
        self.lastSHIdx = self.lastSLIdx = None
        self.slSpent = self.shSpent = False
        self.candPrice = None
        self.candIdx = None
        self.hiHS = self.hiHSIdx = None     # hiHighSinceSL
        self.loLS = self.loLSIdx = None     # loLowSinceSH
        self.bHigh = self.bHighIdx = None
        self.bLSH = self.bLSHIdx = None     # bLowSinceHigh
        self.bLow = self.bLowIdx = None
        self.bHSL = self.bHSLIdx = None     # bHighSinceLow
        self.swings = []                    # (pivot idx, price, kind, recorded bar)

    def step(self, i, h, l, c):
        """returns (bos: +1/-1/0, confirm: 'SH'/'SL'/None)"""
        if self.mode == 0:
            if self.bHigh is None or h >= self.bHigh:
                self.bHigh, self.bHighIdx = h, i
                self.bLSH, self.bLSHIdx = l, i
            elif l < self.bLSH:
                self.bLSH, self.bLSHIdx = l, i
            if self.bLow is None or l <= self.bLow:
                self.bLow, self.bLowIdx = l, i
                self.bHSL, self.bHSLIdx = h, i
            elif h > self.bHSL:
                self.bHSL, self.bHSLIdx = h, i
            fall = (self.bHigh - self.bLSH) >= self.boot_rng and (self.bLSHIdx - self.bHighIdx) >= BOOTSTRAP_MIN_BARS
            rise = (self.bHSL - self.bLow) >= self.boot_rng and (self.bHSLIdx - self.bLowIdx) >= BOOTSTRAP_MIN_BARS
            if fall or rise:
                if fall:
                    self.lastSH, self.lastSHIdx = self.bHigh, self.bHighIdx
                    self.lastSL, self.lastSLIdx = self.bLSH, self.bLSHIdx
                else:
                    self.lastSH, self.lastSHIdx = self.bHSL, self.bHSLIdx
                    self.lastSL, self.lastSLIdx = self.bLow, self.bLowIdx
                self.swings.append((self.lastSHIdx, self.lastSH, "SH", i))
                self.swings.append((self.lastSLIdx, self.lastSL, "SL", i))
                self.mode = 1 if fall else 2
                self.candPrice = l if fall else h
                self.candIdx = i
                self.hiHS, self.hiHSIdx = h, i
                self.loLS, self.loLSIdx = l, i

        if self.mode == 0:
            return 0, None
        if self.hiHS is None or h > self.hiHS:
            self.hiHS, self.hiHSIdx = h, i
        if self.loLS is None or l < self.loLS:
            self.loLS, self.loLSIdx = l, i
        if self.mode == 1 and l < self.candPrice:
            self.candPrice, self.candIdx = l, i
        if self.mode == 2 and h > self.candPrice:
            self.candPrice, self.candIdx = h, i
        barsSince = 0 if self.candIdx is None else i - self.candIdx
        pullbackOK = barsSince >= MIN_PULLBACK_BARS
        slVac = self.lastSL is not None and self.hiHS is not None and (self.hiHS - self.lastSL) >= self.min_pb
        shVac = self.lastSH is not None and self.loLS is not None and (self.lastSH - self.loLS) >= self.min_pb
        bosDown = slVac and not self.slSpent and c < self.lastSL
        bosUp = shVac and not self.shSpent and c > self.lastSH
        newSL = self.mode == 1 and self.candIdx != self.lastSLIdx and (self.lastSL is None or self.candPrice < self.lastSL)
        newSH = self.mode == 2 and self.candIdx != self.lastSHIdx and (self.lastSH is None or self.candPrice > self.lastSH)
        confSL = newSL and pullbackOK and (c - self.candPrice) >= self.min_pb and not bosDown and not bosUp
        confSH = newSH and pullbackOK and (self.candPrice - c) >= self.min_pb and not bosUp and not bosDown
        if bosDown:
            self.lastSH, self.lastSHIdx = self.hiHS, self.hiHSIdx
            self.swings.append((self.lastSHIdx, self.lastSH, "SH", i))
            self.mode = 1
            self.slSpent, self.shSpent = True, False
            self.candPrice, self.candIdx = l, i
            self.loLS, self.loLSIdx = l, i
            return -1, None
        if bosUp:
            self.lastSL, self.lastSLIdx = self.loLS, self.loLSIdx
            self.swings.append((self.lastSLIdx, self.lastSL, "SL", i))
            self.mode = 2
            self.shSpent, self.slSpent = True, False
            self.candPrice, self.candIdx = h, i
            self.hiHS, self.hiHSIdx = h, i
            return +1, None
        if confSL:
            self.lastSL, self.lastSLIdx = self.candPrice, self.candIdx
            self.swings.append((self.lastSLIdx, self.lastSL, "SL", i))
            self.slSpent = False
            self.hiHS, self.hiHSIdx = h, i
            return 0, "SL"
        if confSH:
            self.lastSH, self.lastSHIdx = self.candPrice, self.candIdx
            self.swings.append((self.lastSHIdx, self.lastSH, "SH", i))
            self.shSpent = False
            self.loLS, self.loLSIdx = l, i
            return 0, "SH"
        return 0, None


def per_bar_context(O, H, L, C, pip, lookahead):
    """Per-bar trend/run, chop, SL anchors.  lookahead=True reproduces
    nowick_analyze (swings keyed by PIVOT bar); False = the EA (swings known
    by their RECORDED bar)."""
    n = len(O)
    eng = Engine(pip)
    trend = np.zeros(n, int); run = np.zeros(n, int)
    ref_ev = []                        # (bar, kind) market_refs confirm events
    cur = r = 0
    for i in range(n):
        bos, conf = eng.step(i, H[i], L[i], C[i])
        if bos:
            r = r + 1 if bos == cur else 1
            cur = bos
            # "BULL BOS  broke SH" registers an SH ref; "BEAR BOS  broke SL" an SL ref
            ref_ev.append((i, "SH" if bos > 0 else "SL"))
        if conf:
            ref_ev.append((i, conf))
        trend[i], run[i] = cur, r
    sw = eng.swings

    # ---- chop: last 4 swings strictly alternate ----
    chop = np.ones(n, bool)
    if lookahead:
        seq = sorted((int(p), k) for p, _, k, _ in sw)            # by pivot bar
    else:
        seq = [(rb, k) for _, _, k, rb in sw]                     # by recorded bar
    recent = []; si = 0
    for b in range(n):
        while si < len(seq) and seq[si][0] <= b:
            recent.append(seq[si][1])
            if len(recent) > 4:
                recent.pop(0)
            si += 1
        if len(recent) == 4:
            chop[b] = not all(recent[j] != recent[j + 1] for j in range(3))

    # ---- SL anchors: at each confirm event, the latest swing of that kind ----
    # nowick_analyze.market_refs: the latest swing of that kind by PIVOT bar
    # (ties: higher price) among swings with pivot <= event bar -- which
    # includes swings RECORDED later (look-ahead).  Honest: only swings
    # already recorded by the event bar.  Events arrive in bar order, so a
    # single forward pointer per mode is enough.
    lo = np.full(n, np.nan); hi = np.full(n, np.nan)
    if lookahead:
        pool = sorted((p, px, k) for p, px, k, _ in sw)          # by pivot
        gate = [p for p, _, _ in pool]
    else:
        pool = [(p, px, k) for p, px, k, _ in sw]                # by recorded bar
        gate = [rb for _, _, _, rb in sw]
    best = {"SL": None, "SH": None}                               # (p, px)
    li = hv = np.nan; ei = 0; pi = 0
    for k in range(n):
        while ei < len(ref_ev) and ref_ev[ei][0] <= k:
            b, kind = ref_ev[ei]
            while pi < len(pool) and gate[pi] <= b:
                p_, px_, k_ = pool[pi]
                if best[k_] is None or (p_, px_) >= best[k_]:
                    best[k_] = (p_, px_)
                pi += 1
            if best[kind] is not None:
                if kind == "SL":
                    li = best[kind][1]
                else:
                    hv = best[kind][1]
            ei += 1
        lo[k], hi[k] = li, hv
    return trend, run, chop, lo, hi, sw


def signals(df, pip, jpy, lookahead):
    O = df["open"].to_numpy(); H = df["high"].to_numpy()
    L = df["low"].to_numpy(); C = df["close"].to_numpy()
    n = len(df)
    trend, run, chop, lo, hi, sw = per_bar_context(O, H, L, C, pip, lookahead)
    tr = np.maximum(H - L, np.maximum(np.abs(H - np.roll(C, 1)), np.abs(L - np.roll(C, 1))))
    atr = pd.Series(tr).rolling(ATR_LEN).mean().to_numpy() / pip
    rhi = pd.Series(H).rolling(BREAK_LOOKBACK).max().shift(1).to_numpy()
    rlo = pd.Series(L).rolling(BREAK_LOOKBACK).min().shift(1).to_numpy()
    body_min = (BODY_MIN_PIPS_JPY if jpy else BODY_MIN_PIPS_FX) * pip
    hours = df.index.hour.to_numpy()
    out = []
    for i in range(FIRST_SIGNAL_BAR, n):
        body = abs(C[i] - O[i])
        for d in (1, -1):
            nw = ((O[i] - L[i]) <= 0.0) if d > 0 else ((H[i] - O[i]) <= 0.0)
            if not (nw and body >= body_min):
                continue
            if chop[i] or not (trend[i] == d and run[i] >= 2):
                continue
            ref = lo[i] if d > 0 else hi[i]
            if np.isnan(ref):
                continue
            e0 = O[i]
            sl = ref - SL_BUFFER_PIPS * pip if d > 0 else ref + SL_BUFFER_PIPS * pip
            if (d > 0 and sl >= e0) or (d < 0 and sl <= e0):
                continue
            sd = abs(e0 - sl); entry = e0
            if sd > BIG_SL_PIPS * pip:
                entry = e0 + (EARLY_ENTRY_PIPS * pip if d > 0 else -EARLY_ENTRY_PIPS * pip)
                sd = abs(entry - sl)
            if sd <= 0:
                continue
            tp = entry + d * sd
            # LOCKED filters 5-7
            if d > 0:
                broke = C[i] > rhi[i] if not np.isnan(rhi[i]) else False
            else:
                broke = C[i] < rlo[i] if not np.isnan(rlo[i]) else False
            a = atr[i]
            sva = (sd / pip) / a if a and not np.isnan(a) else np.nan
            if broke or not (sva < MAX_SL_ATR) or (DEAD_FROM <= hours[i] <= DEAD_TO):
                continue
            out.append(dict(i=i, d=d, entry=entry, sl=sl, tp=tp, sd=sd))
    return out, (O, H, L, C)


def simulate(df, sigs, bars, one_position):
    O, H, L, C = bars
    n = len(df); idx = df.index
    # pass 1: when (if ever) does each setup fill -- independent of the others
    cands = []
    for s in sigs:
        i, d, entry, tp = s["i"], s["d"], s["entry"], s["tp"]
        if i >= n - RETEST_BARS - MAX_HOLD:
            continue         # nowick_deep's loop stops 130 bars before the end
        fill = None
        for j in range(i + 1, min(i + 1 + RETEST_BARS, n)):
            if d > 0:
                if H[j] >= tp and L[j] > entry:
                    break
                if L[j] <= entry:
                    fill = j; break
            else:
                if L[j] <= tp and H[j] < entry:
                    break
                if H[j] >= entry:
                    fill = j; break
        if fill is not None:
            cands.append((fill, i, s))
    # pass 2: in FILL order (the EA sees touches in time order); with
    # one_position a setup touching while a position is open -- or on the
    # bar the previous one exited -- is dropped, as the EA drops it.
    cands.sort(key=lambda x: (x[0], x[1]))
    trades = []
    busy_until = -1
    for fill, i, s in cands:
        d, entry, sl, tp, sd = s["d"], s["entry"], s["sl"], s["tp"], s["sd"]
        if one_position and fill <= busy_until:
            continue
        hit = None; ex = None
        for k in range(fill, min(fill + MAX_HOLD, n)):
            fav = ((H[k] - entry) if d > 0 else (entry - L[k])) / sd
            adv = ((entry - L[k]) if d > 0 else (H[k] - entry)) / sd
            if adv >= 1.0:
                hit, ex = "SL", k; break
            if fav >= 1.0:
                hit, ex = "TP", k; break
        if hit is None:
            ex = min(fill + MAX_HOLD, n) - 1
            hit = "TP" if d * (C[ex] - entry) / sd >= 0 else "SL"
            hit += "_timeout"
        busy_until = ex
        trades.append(dict(ts=idx[i], dir=d, entry=entry, sl=sl, tp=tp,
                           fill_t=idx[fill], exit_t=idx[ex],
                           win=int(hit.startswith("TP")), hit=hit,
                           fillbar_both=int((L[fill] <= sl if d > 0 else H[fill] >= sl)
                                            or (H[fill] >= tp if d > 0 else L[fill] <= tp))))
    return pd.DataFrame(trades).sort_values("ts").reset_index(drop=True) if trades else pd.DataFrame(trades)


def key(df):
    return set(zip(pd.to_datetime(df.ts).astype("int64"), df.dir.astype(int)))


def compare(tag, rep, deep):
    kr, kd = key(rep), key(deep)
    both = kr & kd
    print(f"  [{tag}] replica n={len(rep)}  deep n={len(deep)}  same signal={len(both)}  "
          f"only-replica={len(kr - kd)}  only-deep={len(kd - kr)}")
    m = rep.merge(deep, on=["ts", "dir"], suffixes=("_r", "_d"))
    for col in ("entry", "sl", "tp"):
        bad = (np.abs(m[col + "_r"] - m[col + "_d"]) > 1e-9).sum()
        print(f"      {col:6s} mismatches on shared trades: {bad}")
    bad_fill = (pd.to_datetime(m.fill_t_r) != pd.to_datetime(m.fill_t_d)).sum()
    bad_win = (m.win_r != m.win_d).sum()
    print(f"      fill time mismatches: {bad_fill}   outcome mismatches: {bad_win}")
    wr = rep.win.mean() if len(rep) else float("nan")
    te = pd.to_datetime(rep.ts) >= "2018-01-01"
    print(f"      replica win {100*wr:.1f}%  E@1:1 {2*wr-1:+.3f}   "
          f"train<2018 n={(~te).sum()} {100*rep[~te].win.mean():.1f}%   "
          f"test>=2018 n={te.sum()} {100*rep[te].win.mean():.1f}%")
    return kr, kd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--deep", required=True)
    ap.add_argument("--pair", default="eurusd")
    ap.add_argument("--mode", default="both", choices=["deep", "ea", "both"])
    ap.add_argument("--from-year", type=int, default=2000)
    a = ap.parse_args()

    pair = a.pair.lower()
    jpy = pair.endswith("jpy")
    pip = 0.01 if jpy else 0.0001
    df = pd.read_csv(a.csv, parse_dates=["time"]).set_index("time")
    df = df[df.index.year >= a.from_year]

    deep = pd.read_csv(a.deep, parse_dates=["ts"])
    deep = deep[deep.pair == pair]
    deep = deep[(deep.broke == 0) & (deep.sl_vs_atr < 8) & ~deep.hour.between(13, 15)].copy()
    deep["dir"] = deep["dir"].astype(int)
    deep["win"] = deep["win"].astype(int)
    print(f"{pair.upper()}: {len(df)} M15 bars {df.index[0]} -> {df.index[-1]}; "
          f"nowick_deep LOCKED n={len(deep)} win {100*deep.win.mean():.1f}%")

    if a.mode in ("deep", "both"):
        sigs, bars = signals(df, pip, jpy, lookahead=True)
        rep = simulate(df, sigs, bars, one_position=False)
        compare("deep-mode (look-ahead kept, overlapping trades)", rep, deep)

    if a.mode in ("ea", "both"):
        sigs_h, bars = signals(df, pip, jpy, lookahead=False)
        # attribute differences one change at a time
        rep_h = simulate(df, sigs_h, bars, one_position=False)
        compare("honest swings only (chop + SL anchor from CONFIRMED swings)", rep_h, deep)
        rep_e = simulate(df, sigs_h, bars, one_position=True)
        compare("EA = honest swings + one position per pair", rep_e, deep)
        amb = rep_e.fillbar_both.sum()
        print(f"      EA trades whose FILL bar also touches SL or TP (order decided "
              f"by ticks in MT5, by the whole bar in Python): {amb}")
        rep_e.to_csv(f"ea_replica_{pair}.csv", index=False)
        print(f"      EA replica trade list -> ea_replica_{pair}.csv")


if __name__ == "__main__":
    sys.exit(main())
