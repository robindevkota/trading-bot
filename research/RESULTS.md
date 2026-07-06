# VDT — Vol-Targeted Donchian Trend (research branch)

One rule set. Zero per-asset tuning. Tested on 11 instruments across crypto,
stocks, forex, and gold over up to 25 years of daily data (Yahoo Finance,
auto-adjusted). Built and validated 2026-07-06.

---

## The strategy

| Component | Rule |
|---|---|
| Timeframe | Daily bars |
| Regime | EMA50 vs EMA200 on close |
| Entry | Close breaks the 80-day close-high (long) / low (short), in regime direction only. Signal on close, **fill at next open** |
| Trailing stop | Extreme close since entry ∓ 2.5 × ATR(20), checked intraday, gaps fill at the open |
| Fast exit | Close crosses the opposite 10-day Donchian channel → out at next open |
| Sizing | Risk 1% of account equity per trade over the 2.5-ATR stop distance; notional capped at 10× equity |
| Costs | Per-side on notional: crypto 10 bps, stocks 3–5 bps, forex 1.5–2 bps, gold 3 bps |

**Why this design:** trend following + volatility-scaled sizing is the only
strategy class with decades of published cross-asset evidence (time-series
momentum). ATR-based stops and sizing are what make one parameter set portable
across BTC (80% swings) and EURUSD (8% ranges).

## Methodology (no-cheat checklist)

- **No lookahead** — every decision uses data strictly before the fill.
- **In-sample / out-of-sample split** — all parameter selection used data
  **≤ 2021-12-31 only**. 2022-01-01 → 2026-07-06 (bear + bull + chop) was
  touched exactly once, with locked parameters.
- **Parameter robustness** — all 36 combos in the sweep grid were profitable
  in aggregate (mean Sharpe 0.39–0.48). The locked set (80/10/2.5) was chosen
  as best mean Sharpe among configs positive on **all 11 assets** in-sample —
  there is no fragile peak to fall off.
- A trend-strength entry filter was tested and **rejected** (didn't improve
  in-sample; kept the simpler rules).

---

## Portfolio result — single account, all 11 instruments

Every entry risks 1% of shared account equity (typical concurrent open risk
2–5%).

| Period | Risk/trade | Years | CAGR | Sharpe | Max DD | Total |
|---|---|---|---|---|---|---|
| In-sample (2004→2021) | 1% | 17.3 | **+9.2%** | 0.87 | −18.8% | +355% |
| **Out-of-sample (2022→2026)** | 1% | 4.5 | **+7.8%** | 0.69 | −13.3% | +40% |
| Full period | 1% | 21.8 | **+8.9%** | 0.83 | −18.8% | +539% |
| Full period | 2% | 21.8 | **+17.1%** | 0.83 | −35.7% | +3,025% |

- **20 of 22 calendar years positive** (losers: 2008 −12.4%, 2025 −5.0%)
- **Correlation to S&P 500: +0.04** — genuinely uncorrelated to buy & hold
- Out-of-sample Sharpe held at ~80% of in-sample — normal, honest decay

## Per-asset, out-of-sample only (2022 → 2026-07, locked params)

| Asset | Sharpe | Profit factor | Trades | Max DD |
|---|---|---|---|---|
| GC=F (gold) | 1.06 | 3.12 | 24 | −4.0% |
| NVDA | 0.87 | 2.99 | 21 | −3.6% |
| BTC-USD | 0.66 | 2.07 | 28 | −3.6% |
| QQQ | 0.58 | 2.24 | 22 | −3.4% |
| USDJPY | 0.42 | 1.64 | 24 | −5.1% |
| ETH-USD | 0.29 | 1.38 | 24 | −6.0% |
| SPY | 0.17 | 1.20 | 25 | −4.4% |
| AAPL | 0.17 | 1.19 | 19 | −4.5% |
| GBPUSD | −0.22 | 0.68 | 20 | −6.6% |
| EURUSD | −0.49 | 0.44 | 23 | −5.7% |
| AUDUSD | −0.87 | 0.21 | 27 | −12.3% |

8 of 11 positive out-of-sample. Note the strategy caught ETH long trends for a
+4% while ETH buy-and-hold lost **−54%** in the same window, with 1/12th of
the drawdown.

## Honest limitations

1. **Single forex pairs are the weak sleeve** — FX majors have mostly ranged
   since 2015; this is true of trend following industry-wide. FX pairs earn
   their place as diversifiers inside the portfolio (they were all positive
   pre-2022), not as standalone systems. Do not trade this on one FX pair alone.
2. **The edge is the portfolio.** Diversification across uncorrelated asset
   classes is what turns per-asset Sharpe ~0.3–0.5 into portfolio Sharpe ~0.8.
3. Yahoo daily data is indicative for FX (no spread ticks); costs are modeled
   conservatively but live spreads vary.
4. ~1,300 trades over 25 years across the book ≈ 6–10 signals/month — far more
   activity than MTF PRO's ~5/year, but still a patient system.

## Reproduce

```bash
cd research
pip install --user yfinance
python run.py          # per-asset IS / OOS / full tables
python sweep.py        # in-sample parameter grid (36 combos)
python portfolio.py    # single-account portfolio + yearly returns
```

Files: `data.py` (download + cache), `engine.py` (event-driven backtester,
locked PARAMS at top), `run.py`, `sweep.py`, `portfolio.py`.
