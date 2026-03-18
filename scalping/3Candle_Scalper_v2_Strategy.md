# 3-Candle Scalper — Complete Strategy Rules (v2.1)

**Type:** Momentum scalping with trend filter
**Execution timeframe:** 5M
**Bias timeframe:** 1H
**Active pairs:** EURUSD, NZDUSD
**Sessions:** London open and New York open only

> v2.1 reflects parameters tuned and verified via Python backtest on MT5 data (March 2025 – March 2026).
> Config lives in `config/strategy.yaml`. Backtest script: `backtest.py`.

---

## Why 5M Over 1M

The strategy is built around 3-candle momentum patterns. On 1M the majority of these patterns are random noise — the candle bodies are so small that spread alone consumes most of the theoretical profit. On 5M:

- Each candle represents 5 minutes of real price activity — genuine momentum
- Candle bodies are 5–10x larger than 1M, making spread negligible
- Patterns have real institutional backing — 5M moves are meaningful
- False signals drop significantly because weak moves cannot sustain 3 full 5M candles
- Backtests are more reliable because slippage and spread are proportionally smaller

**Think of it this way:** a 3-candle run on 1M can happen by accident in 3 minutes during a slow market. A 3-candle run on 5M requires 15 minutes of sustained buying or selling pressure — that is a real move.

---

## Why EURUSD and NZDUSD Only

Four pairs were backtested: EURUSD, AUDUSD, NZDUSD, USDCHF.

| Pair | Trades/yr | PF | Net PnL | Verdict |
|------|-----------|----|---------|---------|
| EURUSD | 57 | 2.76 | +$5,162 | Active |
| NZDUSD | 47 | 1.34 | +$672 | Active |
| AUDUSD | 6 | 0.04 | -$749 | Rejected — almost no signals |
| USDCHF | 11 | 0.88 | -$91 | Rejected — too few trades, losing |

AUDUSD moves too slowly to form 3 strong consecutive candles during London/NY sessions. USDCHF grinds rather than producing momentum bursts. Only EURUSD and NZDUSD produce consistent, profitable signals.

---

## The Four Filters (All Must Pass)

Every trade requires all four filters to be green simultaneously. Missing any one of them = no trade.

```
Filter 1: Trend Filter      — are we trading WITH the 1H trend?
Filter 2: Session Filter    — are we in a high-liquidity kill zone?
Filter 3: Pattern Filter    — do the 3 candles meet all quality rules?
Filter 4: Pullback Filter   — has price reset enough since the last trade?
```

---

## Filter 1 — Trend Filter (1H Bias)

Only trade in the direction of the 1H trend. The 15M bias option was tested but 1H gives cleaner, higher-quality signals.

**Bullish bias (longs only):**
- 1H EMA 50 is above 1H EMA 200
- Current 1H close is above 1H EMA 50
- Only take 5M bullish patterns when bias is bullish

**Bearish bias (shorts only):**
- 1H EMA 50 is below 1H EMA 200
- Current 1H close is below 1H EMA 50
- Only take 5M bearish patterns when bias is bearish

**Neutral — no trades:**
- EMA 50 and 200 are close together or crossing
- Price is between the two EMAs
- Sit out entirely until bias is clear

### Why this filter matters

Without a trend filter, the strategy takes short signals during bull trends and long signals during bear trends. These counter-trend scalps have a win rate close to 35–40%. With the 1H EMA 50/200 filter, win rate on EURUSD reached 61.4% in backtesting.

---

## Filter 2 — Session Filter (Kill Zones)

Only trade during high-liquidity sessions. Outside these windows the 3-candle pattern produces random noise.

### Valid trading windows (UTC times)

| Session | UTC Time | Why it works |
|---------|----------|--------------|
| London open | 07:00 – 10:00 | Highest forex volume, strong directional moves |
| New York open | 12:00 – 15:00 | Second highest volume |

### Times to avoid completely

| Period | UTC Time | Why |
|--------|----------|-----|
| Asian session | 00:00 – 07:00 | Low volume, choppy, many false patterns |
| NY afternoon | 15:00 – 20:00 | Volume drops, momentum dies |
| Overnight | 20:00 – 00:00 | Dead market, wide spreads |

### News events

Do not take any trade in the 15 minutes before or after a high-impact news release (NFP, CPI, FOMC, interest rate decisions). The 3-candle pattern during news is driven by the release, not momentum, and the move reverses violently.

---

## Filter 3 — Pattern Rules

### The 3-candle pattern on 5M

```
C1 = bar[3] — oldest, sets the size reference
C2 = bar[2] — middle candle
C3 = bar[1] — most recent closed candle (signal bar)
C4 = current bar — entry and exit bar
```

### Bullish pattern — all conditions must be true

**C1 (reference candle)**
- Bullish — close > open
- Body must be at least **0.7 × ATR(14)** — filters out micro-candles
- This is the quality gate. Raised from 0.5 to 0.7 after tuning — removes weak patterns and was the single biggest improvement to profit factor.

**C2 (continuation candle)**
- Bullish — close > open
- Close must be **strictly above C1 close** — genuine progression
- Body must be **≥ 60% of C1 body** — no weak doji-like candles

**C3 (signal candle)**
- Bullish — close > open
- Close must be **strictly above C2 close** — pattern still moving
- Body must be **≥ 60% of C1 body**

### Bearish pattern — mirror rules

- C1 bearish, body ≥ 0.7 × ATR(14)
- C2 bearish, close **below** C1 close, body ≥ 60% of C1
- C3 bearish, close **below** C2 close, body ≥ 60% of C1

### Valid vs invalid — worked examples

```
VALID BULLISH (EURUSD 5M, ATR = 0.00080):
  C1: open 1.08000, close 1.08065  → body 0.00065 (≥ 0.7×0.00080 = 0.00056 ✓)
  C2: open 1.08065, close 1.08118  → body 0.00053 (82% of C1 ✓), close > C1 close ✓
  C3: open 1.08118, close 1.08165  → body 0.00047 (72% of C1 ✓), close > C2 close ✓
  → VALID — signal fires

INVALID — C1 too small:
  ATR = 0.00080, C1 body = 0.00040 (< 0.7×0.00080 = 0.00056)
  → REJECTED before checking anything else

INVALID — C2 body too small:
  C1 body = 0.00065, C2 body = 0.00035 (54% of C1, need 60%)
  → REJECTED

INVALID — progression fails:
  C1 closes at 1.08065
  C2 opens at 1.08000, closes at 1.08050 (green candle but below C1 close)
  → REJECTED — close must be above previous close, not just green
```

---

## Filter 4 — Pullback Filter

After every trade closes, the strategy locks and refuses all new signals until price has meaningfully retraced. This prevents entering multiple times in the same directional run.

### The lock mechanism

```
Trade closes at C4
        |
LOCKED immediately
        |
Monitor every bar for pullback confirmation
        |
Pullback confirmed → UNLOCKED
        |
Next valid pattern can now trigger a trade
```

### Pullback confirmation — ATR method

**After a long trade closes:**
Price must drop at least **1.0 × ATR** below the exit price before the next long signal is valid.

```
Long exits at 1.08200, ATR = 0.00080
Pullback threshold = 1.08200 - 0.00080 = 1.08120
→ Wait for any candle low to touch 1.08120
→ Once touched → UNLOCKED for next long
```

**After a short trade closes:**
Price must rise at least **1.0 × ATR** above the exit price before the next short signal is valid.

### ATR pullback multiplier (active pairs)

| Pair | ATR Multiplier | Reason |
|------|---------------|--------|
| EURUSD | 1.0 | Tested, optimal |
| NZDUSD | 1.0 | Tested, optimal |

---

## Entry Rule

Signal fires at the **close of C3** (all 4 filters confirmed).
Enter at the **open of C4** — the very next 5M candle.
Entry price = C4 open.

---

## Exit Rules

### Take profit — per symbol (tuned)

TP is set at **N × average body size** of C1+C2+C3.

```
Average body = (C1 body + C2 body + C3 body) / 3
Take profit distance = average body × multiplier
```

| Pair | TP Multiplier | Reason |
|------|--------------|--------|
| EURUSD | 3.0 | EURUSD produces strong sustained moves — letting winners run maximises R:R |
| NZDUSD | 1.0 | NZD momentum bursts are shorter — tight TP captures them before reversal |

If TP is not hit by end of C4, exit at C4 close anyway.

```
EURUSD long example:
  C1 body = 0.00065, C2 body = 0.00053, C3 body = 0.00047
  Average body = 0.00055
  TP distance = 0.00055 × 3.0 = 0.00165
  Entry = 1.08165, TP = 1.08330

NZDUSD long example:
  Average body = 0.00045
  TP distance = 0.00045 × 1.0 = 0.00045
  Entry = 0.61200, TP = 0.61245
```

### Stop loss

Place stop loss at **low of C3** (for longs) or **high of C3** (for shorts).

This is the natural invalidation point — if price breaks back below C3's low the pattern has failed.

```
Long entry:
  Entry = C4 open
  Stop  = C3 low
  TP    = entry + (avg body × multiplier)

Short entry:
  Entry = C4 open
  Stop  = C3 high
  TP    = entry - (avg body × multiplier)
```

---

## Position Sizing

Risk **2% of account** per trade on both active pairs.

```
Account = $10,000
Risk per trade = 2% = $200

EURUSD long: entry 1.08165, stop 1.08080
Risk distance = 0.00085 = 8.5 pips
Lot size = $200 / (8.5 × $10) = 0.24 lots → round to 0.24
```

Never risk more than 2% on any single trade.

---

## Complete Trade Flow

```
Every 5M bar close → run through all 4 filters:

1. TREND FILTER
   Is 1H EMA50 above EMA200?           (for longs)
   Is 1H close above EMA50?
   → NO to any → skip, wait

2. SESSION FILTER
   Is current UTC time in 07:00-10:00 or 12:00-15:00?
   → NO → skip, wait

3. PATTERN FILTER
   Are C1, C2, C3 all in the same direction?
   Does each close strictly beyond the previous close?
   Is C1 body ≥ 0.7 × ATR(14)?
   Are C2 and C3 bodies ≥ 60% of C1?
   → NO to any → skip, wait

4. PULLBACK FILTER
   Is strategy unlocked?
   (Has price retraced 1.0 × ATR since last exit?)
   → NO → skip, wait

ALL 4 PASS:
   Signal confirmed
   Enter at C4 open
   Set stop at C3 low (long) / C3 high (short)
   Set TP at entry +/- (avg body × per-symbol multiplier)
   Exit at C4 close if TP not hit

AFTER TRADE CLOSES:
   Lock strategy
   Wait for 1.0 ATR pullback from exit price
   Unlock
   Repeat
```

---

## Settings Summary

| Parameter | EURUSD | NZDUSD | What it controls |
|-----------|--------|--------|-----------------|
| Execution timeframe | 5M | 5M | Chart to run strategy on |
| Bias timeframe | 1H | 1H | Timeframe for trend filter |
| Bias EMA fast | 50 | 50 | Fast EMA for trend |
| Bias EMA slow | 200 | 200 | Slow EMA for trend |
| Min body % of C1 | 60% | 60% | Body size filter for C2 and C3 |
| Min C1 size | 0.7 × ATR | 0.7 × ATR | Minimum pattern quality gate |
| ATR period | 14 | 14 | For min size and pullback |
| ATR pullback multiplier | 1.0 | 1.0 | Pullback lock threshold |
| Stop loss | C3 low/high | C3 low/high | Natural invalidation point |
| Take profit multiplier | 3.0 × avg body | 1.0 × avg body | R:R target |
| Max exit | C4 close | C4 close | Hard time-based exit |
| Risk per trade | 2% | 2% | Position sizing |
| Sessions (UTC) | 07-10, 12-15 | 07-10, 12-15 | Time filter |

---

## Verified Backtest Results

**Data:** MT5 MetaQuotes-Demo | March 2025 – March 2026 | 5M execution, 1H bias

| Metric | EURUSD | NZDUSD | Target |
|--------|--------|--------|--------|
| Total trades | 57 | 47 | 40+ |
| Win rate | 61.4% | 57.4% | 55–65% |
| Profit factor | 2.756 | 1.335 | 1.4+ |
| Avg R:R | 1.73 | 0.99 | 1.3+ |
| Net P&L ($10k) | +$5,162 | +$672 | positive |
| Max drawdown | -4.5% | -5.0% | < 8% |
| Combined P&L | — | — | **+$5,834** |

**TP hit / SL hit / C4-close exits:**
- EURUSD: 3 TP / 6 SL / 48 C4-close — most trades resolved within C4, TP multiplier set high to capture the rare large moves
- NZDUSD: 8 TP / 5 SL / 34 C4-close — tighter TP generates actual TP hits

---

## How to Run

### Requirements

```bash
pip install MetaTrader5 pandas numpy pyyaml
```

MT5 terminal must be open and logged in before running any script.

---

### 1. Backtest

Runs the full strategy on all available historical data for EURUSD and NZDUSD.

```bash
python backtest.py
```

Output: summary table in terminal + `multi_pair_trades.csv` trade log.

---

### 2. Live Trader (Demo)

Monitors EURUSD and NZDUSD in real time. Waits for each 5M bar close, checks all 4 filters, and places orders automatically via MT5.

**Before first run — choose mode in `live_trader.py` line 38:**

```python
DRY_RUN = True    # signals printed, NO orders placed — use this first
DRY_RUN = False   # real orders sent to MT5 demo account
```

**Start the bot:**

```bash
python live_trader.py
```

**What you will see (example output):**

```
2026-03-17 14:45:02  INFO     Next bar in 298s — sleeping...
2026-03-17 14:50:04  INFO     --- Bar check 2026-03-17 14:50 UTC ---
2026-03-17 14:50:04  INFO     [EURUSD] No signal (out of session)
2026-03-17 14:50:04  INFO     [NZDUSD] No signal (no pattern)
...
2026-03-17 08:05:02  INFO     [EURUSD] Signal confirmed: LONG
2026-03-17 08:05:02  INFO     [EURUSD] SIGNAL LONG | entry~1.08320 SL=1.08240 TP=1.08650 lot=0.23
2026-03-17 08:05:02  INFO     [EURUSD] Order filled — ticket #123456
```

**Logs and output files:**

| File | Contents |
|------|----------|
| `trader.log` | Full timestamped log of every bar check and order |
| `trades_live.csv` | One row per order placed (entry, SL, TP, lot, ticket) |
| `lock_state.json` | Pullback lock state — auto-managed, survives restarts |

**To stop:** `Ctrl+C` — the bot shuts down cleanly.

---

### 3. Changing Parameters

All parameters are in `config/strategy.yaml`. Edit there — no code changes needed.

Key values to adjust:

```yaml
tp_body_mult:
  EURUSD: 3.0    # increase for bigger winners, fewer hits
  NZDUSD: 1.0    # decrease for more TP hits

min_c1_atr_mult: 0.70   # raise to filter more aggressively (fewer trades)
min_body_pct: 0.60       # raise for stricter pattern quality
```

After changing params, re-run `backtest.py` to verify impact before going live.

---

## What to Do If Results Degrade

If profit factor drops below 1.1 over 50+ live trades:

1. Check session times — broker server time may differ from UTC
2. Verify the 1H bias is correct (EMA uses closing prices only)
3. Re-run `backtest.py` on fresh data — parameters may need adjusting as market regime changes
4. Avoid trading EURUSD in low-volatility ranging months

---

## Project Files

```
scalping/
  config/
    mt5.yaml          — MT5 connection credentials
    strategy.yaml     — all strategy parameters (edit here, not in code)
  backtest.py         — full multi-pair backtest
  live_trader.py      — live trading bot
  3Candle_Scalper_v2_Strategy.md   — this file
```

---

*All rules are fully mechanical. Every condition is objective — no subjectivity, no interpretation required. Parameters are defined in `config/strategy.yaml` and should be changed there.*
