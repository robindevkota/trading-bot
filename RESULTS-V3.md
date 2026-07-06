# v3 Photon LQ Models — A/B/C Backtest Results

Run date: 2026-07-02 (MetaQuotes-Demo acct 5052539183, $100k, 1% risk)

Configs:
- **base** — locked v2 config (all v3 flags OFF)
- **v3** — `use_htf_sweep` + `use_eql_bonus` ON (new entry triggers)
- **v3f** — v3 + `require_inducement` ON (PBID filter)

| Pair | Window | Cfg | Signals | Trades | WR% | PnL | MaxDD |
|------|--------|-----|---------|--------|-----|------|-------|
| EURUSD | 2023 | base | 7 | 3 | 66.7 | +$6,073 | 0.0% |
| EURUSD | 2023 | v3 | 7 | 3 | 66.7 | +$6,073 | 0.0% |
| EURUSD | 2023 | v3f | 4 | 3 | 66.7 | +$6,073 | 0.0% |
| EURUSD | 2024 | base | 8 | 4 | 50.0 | +$4,315 | 1.3% |
| EURUSD | 2024 | v3 | 8 | 4 | 50.0 | +$4,315 | 1.3% |
| EURUSD | 2024 | v3f | 5 | 4 | 50.0 | +$4,315 | 1.3% |
| AUDUSD | 2023–24 | base | 6 | 2 | 50.0 | +$2,894 | 0.0% |
| AUDUSD | 2023–24 | v3 | 6 | 2 | 50.0 | +$2,894 | 0.0% |
| AUDUSD | 2023–24 | **v3f** | 2 | 1 | **100.0** | **+$2,930** | 0.0% |
| NZDUSD | 2024 | base | 6 | 3 | 66.7 | +$4,881 | 1.2% |
| NZDUSD | 2024 | v3 | 6 | 3 | 66.7 | +$4,881 | 1.2% |
| NZDUSD | 2024 | v3f | 4 | 3 | 66.7 | +$4,881 | 1.2% |
| USDCHF | 2024 | base | 7 | 2 | 50.0 | +$1,710 | 1.1% |
| USDCHF | 2024 | **v3** | 7 | 3 | 33.3 | +$1,678 | 1.1% |
| USDCHF | 2024 | **v3f** | 3 | 1 | **0.0** | **−$1,145** | 1.1% |
| EURUSD | 2026 | all | 0 | 0 | — | $0 | — |
| AUDUSD | 2026 | all | 3 | 1 | 0.0 | −$1,104 | 1.1% |
| NZDUSD | 2026 | base/v3 | 2 | 1 | 0.0 | −$1,075 | 1.1% |
| NZDUSD | 2026 | v3f | 2 | 1 | 0.0 | −$1,111 | 1.1% |
| USDCHF | 2026 | base/v3 | 1 | 0 | — | $0 | — |
| USDCHF | 2026 | v3f | 0 | 0 | — | $0 | — |

## Totals (all 9 windows)

| Config | Total PnL | Notes |
|--------|-----------|-------|
| base | **+$17,696** | reproduces locked baselines exactly on new server data |
| v3 | +$17,664 | 1 extra trade in 2 years (USDCHF, a loser) — triggers almost never fire |
| v3f | +$14,839 | saved AUDUSD's loser (+$36) but killed USDCHF's winner (−$2,855) |

## Verdict — keep all v3 flags OFF

1. **v3 triggers (HTF sweep, equal-lows) are nearly inert.** With the RSI gate,
   ADX filter, zone requirements and score threshold already in place, the new
   models fired one additional trade in 9 windows — a loss. No edge added.
2. **The inducement filter (v3f) is too blunt.** It correctly removed AUDUSD's
   losing trade but removed USDCHF's winning trade, net −$2,857 vs baseline.
   With ~2–4 trades/yr/pair, one filter decision swings the whole result —
   sample size cannot justify it.
3. **The 2026 losses pass every Photon test.** Both losing trades (AUDUSD,
   NZDUSD) fired identically under all three configs — they were swept-and-
   confirmed entries that simply failed. The 2026 problem is regime, not
   entry mechanics.

The code stays in the repo behind flags (default OFF) for future re-testing
with more data. Live config remains the locked v2 baseline.
