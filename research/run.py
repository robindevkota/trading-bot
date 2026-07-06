"""Run the VDT strategy across the full universe, in-sample and out-of-sample."""
import sys
import pandas as pd
from data import UNIVERSE, load
import engine

pd.set_option("display.width", 200)

SPLITS = {
    "IN-SAMPLE  (start -> 2021-12-31)": (None, "2021-12-31"),
    "OUT-SAMPLE (2022-01-01 -> today) ": ("2022-01-01", None),
    "FULL PERIOD                      ": (None, None),
}


def fmt(m):
    return dict(
        ticker=m["ticker"], yrs=m["years"],
        ret=f"{m['total_ret']*100:+.0f}%", cagr=f"{m['cagr']*100:+.1f}%",
        sharpe=f"{m['sharpe']:.2f}", maxdd=f"{m['max_dd']*100:.1f}%",
        n=m["trades"], wr=f"{m['win_rate']*100:.0f}%",
        pf=f"{m['profit_factor']:.2f}" if m['profit_factor'] != float('inf') else "inf",
        bh=f"{m.get('bh_ret', 0)*100:+.0f}%", bh_dd=f"{m.get('bh_dd', 0)*100:.0f}%",
    )


def main(refresh=False):
    data = {}
    for tk in UNIVERSE:
        try:
            df = load(tk, refresh=refresh)
            data[tk] = df
            print(f"loaded {tk:10s} {len(df):6d} bars  {df.index[0].date()} -> {df.index[-1].date()}")
        except Exception as e:
            print(f"FAILED {tk}: {e}")

    for label, (start, end) in SPLITS.items():
        rows = []
        for tk, df in data.items():
            _, cost = UNIVERSE[tk]
            res = engine.run(df, cost_bps=cost, start=start, end=end)
            res.ticker = tk
            if len(res.equity) < 50 or len(res.trades) == 0:
                continue
            s = df.Close.loc[res.equity.index[0]:res.equity.index[-1]]
            rows.append(fmt(res.metrics(bh_close=s)))
        print(f"\n=== {label} ===")
        print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main(refresh="--refresh" in sys.argv)
