# Consolidated Backtest Report — 2026-07-06

**Data:** 1.6M+ 15m candles. Crypto: BTC, ETH, SOL, BNB, XRP, DOGE
(2020–2026, Binance) + positioning fuel data (OI, long/short ratios, funding,
5m, since 2021-12). Forex: 8 majors/crosses M15 (2018–2026, MT5).
Backup: `research/data_backup/market_data_2026-07-06.zip` (138 MB, 33 files).

**Method everywhere:** signal at bar close → outcomes by first-touch TP/SL →
in-sample design (→2023) / out-of-sample verdict (2024–2026) → net of real
fees (crypto futures 0.075% RT incl. slippage; FX 1.2–1.8 pips RT).
Trade shape: TP = 2×ATR, SL = 4×ATR, max hold 16h, ATR% > 0.25 filter.

---

## 1. The tradable result: Momentum Ignition (long crypto)

Rule: one 15m bar closes ≥ 2.5×ATR(14) up → enter long at that close.

**Out-of-sample 2024-01 → 2026-06, net of fees:**

| Symbol | Trades | Win rate | Net avg/trade | Net total |
|---|---|---|---|---|
| BTC | 175 | 73.1% | +0.135% | +24% |
| ETH | 233 | 70.4% | +0.093% | +22% |
| SOL | 149 | 65.1% | +0.052% | +8% |
| BNB | 132 | 69.7% | +0.108% | +14% |
| XRP | 166 | 70.5% | +0.084% | +14% |
| DOGE | 183 | 69.4% | +0.098% | +18% |
| **Basket** | **1,038** | **~70%** | **+0.095%** | **+100%** (fixed size, no compounding) |

≈ 35 trades/month across 6 symbols. SOL/BNB/XRP/DOGE never influenced any
design decision — this table is pure generalization.

**Regime warning (in-sample evidence):** XRP and DOGE *lost* during the
2020–21 meme mania (DOGE 58% WR, −0.39%/trade). In vertical manias the giant
green bar is often the top. Must be monitored; a mania guard is queued for
research.

## 2. The upgrade: fuel confirmation (F11)

Ignition **while the crowd is short** (Binance global long/short ratio in its
bottom 25% of the last 30 days) = short-squeeze fuel confirmed:

| | Trades (OOS) | Win rate | Net avg/trade |
|---|---|---|---|
| BTC ignition + crowd short | 62 | 72.6% | +0.107% |
| ETH ignition + crowd short | 98 | **74.5%** | **+0.155%** |

Positive on both symbols in BOTH periods. ~5 trades/month on 2 symbols.
Use as a high-conviction tier (larger size), not a replacement for #1.

## 3. Buried by out-of-sample testing (do not revisit)

| Idea | IS looked like | OOS verdict |
|---|---|---|
| Funding-conditioned ignition | promising | inconsistent across symbols — noise |
| OI-build + range-break (H2) | good on ETH | negative / too few samples |
| Standalone crowd-fade (L/S, funding extremes) | 62–73% WR, +EV | all dead — context, not signals |
| 5 consecutive green candles | z > 4 | negative |
| US-close-hour longs | z > 3 | negative |

## 4. Forex: case closed, 8/8 pairs

Fade patterns (big-red-bar bounce, squeeze-break fades) win 63–77% on every
pair — and pay nothing: net −0.045% to +0.021% per trade after spread. The 4
held-out pairs (NZDUSD, USDCHF, USDCAD, EURJPY) replicated the first 4
exactly. **FX intraday at retail costs is permanently closed.** FX belongs to
4H+ timeframes only.

## Bottom line

One validated, generalizing, fee-surviving system exists today:
**LONG crypto momentum ignition, 6-symbol basket, ~35 trades/month, ~70% WR,
+0.095%/trade net**, with a crowd-short high-conviction tier at ~74% WR.
Next build step when ready: full portfolio backtest with position sizing,
compounding, drawdown profile, and the mania guard.

*Scripts: `miner.py`, `combos.py`, `viability.py`, `fx_miner.py`,
`fuel_data.py`, `fuel_miner.py`. Ledger: `research/FINDINGS.md` (F1–F13).*
