# Crypto 15m Pattern Mining — Findings

455,312 candles (BTC + ETH, 15m, 2020-01 → 2026-06, Binance archive incl.
taker-buy order flow). Method: define "big run" as *price hits +2×ATR before
−1×ATR within 4h* (i.e. a real 2:1 trade would win), then measure which
conditions at the bar close raise that probability. Rules: features use only
past data; patterns must be significant in-sample (2020–2023, z>2) AND keep
their lift out-of-sample (2024–2026) to count. Mined 2026-07-06.

Base rate: ~28% of all bars begin a 2:1-winning long. Anything above that is edge.

## Finding 1 — On crypto 15m, CONTINUATION beats REVERSAL. Everywhere.

The single biggest result. Every classic "reversal" signal, tested honestly,
predicts **continuation** instead:

| Textbook says | Data says (OOS lift, both symbols) |
|---|---|
| Hammer candle at range low → bounce | Price keeps FALLING (lift 1.10–1.15 for shorts) |
| Liquidity sweep of lows + reclaim (SMC long) | Price keeps FALLING (lift 1.21–1.22 for shorts) |
| Capitulation (4+ red candles, huge volume) → buy | More downside (lift 1.14–1.21 for shorts) |
| Shooting star at highs → short | Price keeps RISING (ETH lift 1.21 for longs) |
| Huge green bar → "overextended, wait for pullback" | Best long signal in the entire study |

Retail traders fade moves; the moves continue. The "hidden pattern" you asked
for is that **the crowd's reversal playbook is the fuel** — their stop-losses
feed the continuation.

## Finding 2 — Patterns that died out-of-sample (why validation matters)

`5 consecutive up-closes` and `US-close-hour longs` looked strongly significant
2020–2023 (z ≈ 4) and went **negative** 2024–2026. Without the OOS split these
would have gone straight into a losing strategy.

## Finding 3 — The survivor: Momentum Ignition

**Pattern:** a single 15m bar closing ≥ 2.5×ATR(14) above the prior close
(`big_bar_up`) → go LONG at that close.

Out-of-sample (2024-01 → 2026-06), ATR% > 0.25 filter, TP = 2×ATR,
SL = 4×ATR, max hold 16h, after Binance futures fees (maker entry + taker
exit + slippage):

| Symbol | Trades | Win rate | Net avg/trade | Net total (fixed size) |
|---|---|---|---|---|
| BTCUSDT | 175 | **73.1%** | **+0.135%** | +24% |
| ETHUSDT | 233 | **70.4%** | **+0.093%** | +22% |

Also survived on ETH: `capitulation + at_range_low` SHORT (70.3% WR,
+0.088%/trade net). Marginal (skip for now): `sweep_low_vol` short,
`big_bar_dn` short — real lift but fees eat it.

## Finding 4 — Fees are the boss fight, not the pattern

The same patterns at the original 15m scale (TP = 1×ATR ≈ 0.3%) had the same
win rates but **negative net expectancy on spot** (0.20% round-trip fees ≈
two-thirds of the target). Doubling the target size (TP = 2×ATR, hold up to
16h) kept the ~70% win rate and made fees small relative to the prize. Any
strategy built from these patterns must use **futures maker/taker fees, never
spot market orders**, and only trade when ATR% > 0.25.

## What did NOT matter (tested, no stable edge)

Round-number proximity, weekend effect, trade-count bursts, quiet-volume
"accumulation", squeeze-breakout direction (squeeze raises the odds a move
starts but doesn't say which way), London-open hour.

## Next step candidate strategy (not yet built)

LONG-only momentum ignition on a basket of liquid perps (BTC, ETH, +SOL/BNB
to verify): `big_bar_up` + ATR% > 0.25 → market/limit long, TP 2×ATR,
SL 4×ATR, timeout 16h. Expected: ~70% WR, ~4–6 trades/week on 2 symbols
(scales with basket), net positive after futures fees, validated OOS.
Risk note: the 1:2 RR profile means one loss erases ~2 wins — WR must stay
above ~67% + fees, so live monitoring vs backtest WR is mandatory.

## Forex (M15, MT5 data 2018–2026, 4 majors) — mined 2026-07-06

Same miner, same IS/OOS discipline, 200k bars per pair (EURUSD, GBPUSD,
USDJPY, AUDUSD). Order-flow features excluded (no taker data in FX).

**Finding 5 — Forex is the OPPOSITE regime of crypto.** The same patterns
invert:

| Pattern | Crypto 15m says | Forex M15 says |
|---|---|---|
| Big down bar | keep falling (short) | **bounce** (EURUSD OOS lift 1.44, GBPUSD 1.41 for longs) |
| Squeeze breakout | trade WITH the break | **fade the break** (lift 1.17–1.29, all pairs) |
| Capitulation | more downside | bounce (AUDUSD lift 1.36) |

Crypto trends intraday (liquidation cascades feed continuation); FX majors
mean-revert intraday (bank flow fades retail breakouts). One strategy cannot
serve both — they need opposite trade direction.

**Finding 6 — Sessions are volatility timers, not direction signals.**
London/NY-open hours show elevated hit rates for BOTH longs and shorts
(z up to 21): big moves cluster there, but the session doesn't say which way.

**Finding 7 — FX intraday edges are real but economically dead.** The fade
patterns win 65–70% at the 1:2 profile out-of-sample — but breakeven is 66.7%,
and M15 FX targets (2×ATR ≈ 6–10 pips) are so small that ~1.2 pips of
spread+slippage erases the residual. Net expectancy: −0.02% to +0.01% per
trade ≈ zero. On H1 the mean-reversion effect fades entirely (WR 50–62%).
Verdict: **no tradable intraday pattern edge on FX majors at retail costs** —
this is precisely why MTF PRO (4H zones, wide targets) and daily trend (VDT)
are the right FX vehicles, and why the intraday pattern strategy should be
built on crypto futures only.

## Reproduce

```bash
cd research/patterns
python binance_data.py   # download 455k candles (cached)
python miner.py          # full pattern catalog + survivors + OOS failures
python combos.py         # combos at 2:1 / 1:1 / 1:2 RR profiles
python viability.py      # net-of-fee expectancy, top patterns
```
