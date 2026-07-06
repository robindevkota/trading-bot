"""Single-account cross-asset portfolio of the VDT strategy.

Model: ONE account monitors all 11 instruments; every entry risks
`risk_pct` of account equity. Portfolio daily return = SUM of per-asset
strategy returns (each asset backtested standalone at 1% risk of its own
compounding equity — a close approximation of a shared account since
typical concurrent open risk is only 2-5%).

The 10x per-position leverage cap never binds at 1-2% risk, so scaled
variants multiply the return stream linearly.
"""
import numpy as np
import pandas as pd
from data import UNIVERSE, load
import engine

pd.set_option("display.width", 200)


def perf(ret, label, scale=1.0):
    r = ret * scale
    eq = (1 + r).cumprod()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1
    ppy = len(r) / years
    sharpe = r.mean() / r.std() * np.sqrt(ppy)
    dd = (eq / eq.cummax() - 1).min()
    return dict(period=label, risk_per_trade=f"{scale:.0f}%", years=round(years, 1),
                cagr=f"{cagr*100:+.1f}%", sharpe=f"{sharpe:.2f}",
                max_dd=f"{dd*100:.1f}%",
                total=f"{(eq.iloc[-1]-1)*100:+.0f}%")


rets = {}
for tk in UNIVERSE:
    df = load(tk)
    _, cost = UNIVERSE[tk]
    res = engine.run(df, cost_bps=cost)
    rets[tk] = res.equity.pct_change()

R = pd.DataFrame(rets)
# start once most of the universe is live so results reflect the full book
R = R[R.notna().sum(axis=1) >= 8]
port = R.sum(axis=1, skipna=True)

splits = dict(IS=port[:"2021-12-31"], OOS=port["2022-01-01":], FULL=port)
rows = [perf(r, label, s) for label, r in splits.items() for s in (1.0, 2.0)]
print(pd.DataFrame(rows).to_string(index=False))

spy = load("SPY").Close.pct_change()
both = pd.concat([port, spy], axis=1, keys=["vdt", "spy"]).dropna()
print(f"\nportfolio start: {port.index[0].date()}, corr(VDT, SPY) = {both.vdt.corr(both.spy):+.2f}")

eq = (1 + port).cumprod()
yearly = eq.resample("YE").last().pct_change().dropna()
print("\nYearly returns at 1% risk/trade:")
for y, v in yearly.items():
    bar = "#" * max(0, int(abs(v) * 100))
    print(f"  {y.year}: {v*100:+6.1f}%  {'+' if v>0 else '-'}{bar}")
print(f"\npositive years: {(yearly>0).sum()}/{len(yearly)}")

eq.to_csv("portfolio_equity.csv")
