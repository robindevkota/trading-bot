"""Global parameter sweep — IN-SAMPLE ONLY (start -> 2021-12-31).
One param set for all assets; score = mean Sharpe across the universe
(equal weight per asset so BTC doesn't dominate).
"""
import itertools
import numpy as np
import pandas as pd
from data import UNIVERSE, load
import engine

data = {tk: load(tk) for tk in UNIVERSE}

grid = dict(
    don_entry=[40, 55, 80, 120],
    don_exit=[10, 20, 40],
    atr_mult=[2.5, 3.0, 4.0],
    ema_fast=[50], ema_slow=[200],
    atr_len=[20], risk_pct=[0.01], lev_cap=[10.0],
)

rows = []
keys = list(grid)
for combo in itertools.product(*grid.values()):
    p = dict(zip(keys, combo))
    sharpes, cagrs, dds, ns = [], [], [], []
    for tk, df in data.items():
        _, cost = UNIVERSE[tk]
        r = engine.run(df, cost_bps=cost, end="2021-12-31", p=p)
        r.ticker = tk
        if len(r.trades) == 0:
            continue
        m = r.metrics()
        sharpes.append(m["sharpe"]); cagrs.append(m["cagr"])
        dds.append(m["max_dd"]); ns.append(m["trades"])
    rows.append(dict(don_e=p["don_entry"], don_x=p["don_exit"], atr_m=p["atr_mult"],
                     mean_sharpe=np.mean(sharpes), min_sharpe=np.min(sharpes),
                     pos_assets=sum(s > 0 for s in sharpes),
                     mean_cagr=np.mean(cagrs), worst_dd=np.min(dds),
                     trades=sum(ns)))

df = pd.DataFrame(rows).sort_values("mean_sharpe", ascending=False)
pd.set_option("display.width", 200)
print(df.to_string(index=False,
                   formatters={c: "{:.3f}".format for c in
                               ["mean_sharpe", "min_sharpe", "mean_cagr", "worst_dd"]}))
