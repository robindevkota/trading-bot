"""Vol-Targeted Donchian Trend (VDT) — single-instrument backtest engine.

Rules (identical for every asset, long and short symmetric):
  Regime   : EMA50 vs EMA200 on daily close
  Entry    : close breaks 55-day close-high (long) / 55-day close-low (short),
             only in direction of regime. Signal on close, fill at next open.
  Stop     : chandelier trail, extreme-close-since-entry -/+ 3.0 x ATR(20).
             Checked intraday vs high/low; gaps fill at the open.
  Exit 2   : close crosses the 20-day opposite Donchian channel -> next open.
  Sizing   : risk 1% of current equity per trade over the 3-ATR stop distance,
             notional capped at 10x equity (covers forex where ATR is tiny).
  Costs    : per-side bps on notional (asset-class specific), both entry+exit.
No per-asset parameters. No lookahead: every decision uses data available
strictly before the fill.
"""
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

# Locked from in-sample sweep (research/sweep.py): best mean Sharpe among
# configs with positive Sharpe on ALL 11 assets pre-2022.
PARAMS = dict(don_entry=80, don_exit=10, atr_len=20, atr_mult=2.5,
              ema_fast=50, ema_slow=200, risk_pct=0.01, lev_cap=10.0,
              ts_min=0.0)  # min |EMAf-EMAs|/ATR trend strength to enter


@dataclass
class Result:
    ticker: str
    equity: pd.Series = field(repr=False, default=None)
    trades: pd.DataFrame = field(repr=False, default=None)

    def metrics(self, bh_close: pd.Series = None) -> dict:
        eq = self.equity
        ret = eq.pct_change().dropna()
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
        # annualise on this instrument's own trading calendar
        ppy = len(ret) / years if years > 0 else 252
        sharpe = ret.mean() / ret.std() * np.sqrt(ppy) if ret.std() > 0 else 0
        dd = (eq / eq.cummax() - 1).min()
        t = self.trades
        wins = t[t.pnl > 0] if len(t) else t
        losses = t[t.pnl <= 0] if len(t) else t
        pf = (wins.pnl.sum() / abs(losses.pnl.sum())
              if len(losses) and losses.pnl.sum() != 0 else np.inf)
        out = dict(
            ticker=self.ticker, years=round(years, 1),
            total_ret=eq.iloc[-1] / eq.iloc[0] - 1, cagr=cagr,
            sharpe=sharpe, max_dd=dd, trades=len(t),
            win_rate=len(wins) / len(t) if len(t) else 0,
            profit_factor=pf,
        )
        if bh_close is not None and len(bh_close) > 1:
            out["bh_ret"] = bh_close.iloc[-1] / bh_close.iloc[0] - 1
            out["bh_dd"] = (bh_close / bh_close.cummax() - 1).min()
        return out


def run(df: pd.DataFrame, cost_bps: float, start=None, end=None,
        start_equity: float = 10_000.0, p: dict = PARAMS,
        long_only: bool = False) -> Result:
    o, h, l, c = df.Open.values, df.High.values, df.Low.values, df.Close.values
    close = df.Close

    ema_f = close.ewm(span=p["ema_fast"], adjust=False).mean().values
    ema_s = close.ewm(span=p["ema_slow"], adjust=False).mean().values
    tr = np.maximum(df.High - df.Low,
                    np.maximum((df.High - close.shift()).abs(),
                               (df.Low - close.shift()).abs()))
    atr = tr.ewm(alpha=1 / p["atr_len"], adjust=False).mean().values
    hi_e = close.rolling(p["don_entry"]).max().shift(1).values
    lo_e = close.rolling(p["don_entry"]).min().shift(1).values
    hi_x = close.rolling(p["don_exit"]).max().shift(1).values
    lo_x = close.rolling(p["don_exit"]).min().shift(1).values

    idx = df.index
    i0 = p["ema_slow"]  # warmup
    if start is not None:
        i0 = max(i0, idx.searchsorted(pd.Timestamp(start)))
    i1 = len(df) if end is None else idx.searchsorted(pd.Timestamp(end), side="right")

    equity = start_equity
    eq_curve, trades = {}, []
    pos = 0                  # +1 long, -1 short, 0 flat
    units = entry_px = stop = extreme = 0.0
    pending_entry = 0        # decided on yesterday's close, fills at today's open
    pending_exit = False     # channel exit decided on yesterday's close

    def costs(px, u):
        return abs(u) * px * cost_bps / 1e4

    def close_trade(px, i, reason):
        nonlocal equity, pos
        pnl = units * (px - entry_px) * pos - costs(px, units) - costs(entry_px, units)
        equity += pnl
        trades.append(dict(exit=idx[i], dir="L" if pos > 0 else "S",
                           entry_px=entry_px, exit_px=px, pnl=pnl, reason=reason))
        pos = 0

    for i in range(i0, i1):
        # 1) channel exit decided yesterday -> fill at today's open
        if pending_exit and pos != 0:
            close_trade(o[i], i, "chan_exit")
        pending_exit = False

        # 2) entry decided yesterday -> fill at today's open
        if pending_entry != 0 and pos == 0 and not np.isnan(atr[i - 1]):
            d = pending_entry
            stop_dist = p["atr_mult"] * atr[i - 1]
            risk_units = equity * p["risk_pct"] / stop_dist
            max_units = equity * p["lev_cap"] / o[i]
            units = min(risk_units, max_units)
            if units > 0:
                pos, entry_px = d, o[i]
                extreme = o[i]
                stop = entry_px - d * stop_dist
        pending_entry = 0

        # 3) intraday stop check (stop level from data through yesterday)
        if pos > 0:
            if o[i] <= stop:
                close_trade(o[i], i, "stop_gap")
            elif l[i] <= stop:
                close_trade(stop, i, "stop")
        elif pos < 0:
            if o[i] >= stop:
                close_trade(o[i], i, "stop_gap")
            elif h[i] >= stop:
                close_trade(stop, i, "stop")

        # 4) end-of-day decisions on today's close
        if pos != 0:
            extreme = max(extreme, c[i]) if pos > 0 else min(extreme, c[i])
            new_stop = extreme - pos * p["atr_mult"] * atr[i]
            stop = max(stop, new_stop) if pos > 0 else min(stop, new_stop)
            if (pos > 0 and not np.isnan(lo_x[i]) and c[i] < lo_x[i]) or \
               (pos < 0 and not np.isnan(hi_x[i]) and c[i] > hi_x[i]):
                pending_exit = True
        else:
            if not np.isnan(hi_e[i]) and not np.isnan(ema_s[i]) and \
               abs(ema_f[i] - ema_s[i]) >= p.get("ts_min", 0.0) * atr[i]:
                if c[i] > hi_e[i] and ema_f[i] > ema_s[i]:
                    pending_entry = 1
                elif (not long_only) and c[i] < lo_e[i] and ema_f[i] < ema_s[i]:
                    pending_entry = -1

        # mark-to-market equity
        mtm = equity + (units * (c[i] - entry_px) * pos if pos != 0 else 0)
        eq_curve[idx[i]] = mtm

    if pos != 0:
        close_trade(c[i1 - 1], i1 - 1, "eod")
        eq_curve[idx[i1 - 1]] = equity

    return Result(ticker="", equity=pd.Series(eq_curve),
                  trades=pd.DataFrame(trades))
