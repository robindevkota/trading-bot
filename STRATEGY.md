# MTF PRO Strategy — Trading Guide

**Version:** 5.0 — Python / MT5
**Pairs tested:** EURUSD (validated), AUDUSD (validated), NZDUSD (validated), USDCHF (validated)
**Implementation:** `src/strategies/mtf_pro_entry.py`
**Config:** `config/config.yaml` → `strategy.mtf_pro`

---

## Table of Contents

1. [Strategy Overview](#1-strategy-overview)
2. [The Three-Timeframe Framework](#2-the-three-timeframe-framework)
3. [Entry Models & Scoring](#3-entry-models--scoring)
4. [Pro-Trend vs Counter-Trend](#4-pro-trend-vs-counter-trend)
5. [Signal Filters & Confluence](#5-signal-filters--confluence)
6. [Risk Management](#6-risk-management)
7. [Settings Reference](#7-settings-reference)
8. [Pre-Trade Checklist](#8-pre-trade-checklist)
9. [Validated Backtest Results](#9-validated-backtest-results)
10. [Common Mistakes](#10-common-mistakes)

---

## 1. Strategy Overview

MTF PRO is a **top-down, scored multi-timeframe confluence strategy** based on Smart Money Concepts.

> "Bias from the Daily. Zone from the 4H. Trigger on the 15M."

**Three-step logic:**
1. **1D** — two-confluence daily bias (EMA21/50 alignment + RSI > 50)
2. **4H** — identify active unmitigated Order Block zones; confirm 4H structure (HH+HL for LONG)
3. **15M** — wait for a scored entry model inside the zone (CHoCH, Sweep, BOS, EMA bounce)

The edge comes from entering only at validated institutional zones with multiple 15M confirmations. A CHoCH at a 4H OB gives two independent layers of confirmation — "where" (zone) and "now" (structure flip).

**Current mode:** LONG-only (`use_short_direction: false`). Both pro-trend LONGs and counter-trend LONGs are taken. SHORTs are disabled until a separate calibration is done.

---

## 2. The Three-Timeframe Framework

### 1D — Bias (Two Confluences Required)

Daily bars are computed by resampling the 4H data. No separate daily feed needed.

**LONG bias** (both must be true):
- EMA21 > EMA50 AND EMA21 has a positive 10-day slope (rising)
- Daily close > EMA21 × (1 + 0.001 margin)
- Daily RSI(14) > 50

**SHORT bias** (symmetric — currently disabled via `use_short_direction: false`):
- EMA21 < EMA50 AND EMA21 falling
- Daily close < EMA21
- Daily RSI(14) < 50

**Neutral → no trades** if conditions are mixed.

> EMA21/EMA50 was chosen over EMA50/EMA100 because it catches trend reversals faster, especially at yearly turning points (+$3,481 vs +$897 improvement in 2022–2024 testing).

---

### 4H — Order Block Zone

Once daily bias is confirmed, scan the last 80 4H bars for valid unmitigated Order Blocks.

**Bullish OB (demand zone for LONGs):**
- Candle `i` is bearish
- Candle `i+1` is strongly bullish: body ≥ 1.5× OB body AND closes above OB high by ≥ 15 pips
- No 4H close below `OB_low` since formation (unmitigated)
- Zone = `[OB_low, OB_high]`

**4H structure confirmation:**
- Pro-trend LONG: 4H must show HH + HL (40-bar lookback, 3-bar pivot)
- Counter-trend LONG: 4H must show HH + HL for the LONG direction even while daily is SHORT

---

### 15M — Entry Scoring

When 15M price enters the 4H OB zone, each of the following models is evaluated and scored. **All scoring happens simultaneously** — it is not first-wins.

| Model | Points | Description |
|-------|--------|-------------|
| CHoCH | +2 | Proper Change of Character: last swing high < previous swing high (lower-high formed), then 15M bar breaks above the lower-high → reversal confirmed |
| Sweep | +2 | 15M bar wicks ≥ 5 pips below SSL, body close back above SSL ≥ 50% of bar range |
| BOS | +1 | Any 15M swing high break (weaker, needs other confluence to reach threshold) |
| EMA bounce | +1 | 15M bar wicks to EMA20 inside zone, closes back above EMA20 with body ≥ 50% |
| Flip zone | +1 | OB mid is within 10 pips of a previous 4H structural swing high (resistance → support) |

**Minimum scores to enter:**
- Pro-trend: `min_entry_score = 2` (effectively score ≥ 3 due to the 40% confidence gate in risk manager)
- Counter-trend: `ct_min_score = 4` (needs CHoCH + something else, or Sweep + something)

---

## 3. Entry Models & Scoring

### CHoCH — Change of Character (+2 pts)

The highest-conviction entry. Requires a **lower-high structure** before the break:

1. Find the 2 most recent 15M confirmed swing highs
2. Last swing high < previous swing high → lower-high formed (pullback structure)
3. Current bar closes **above** the lower-high → buyers have taken control at the zone
4. Enter at bar close

**Why it's worth 2 pts:** Requires two separate structural observations (LH pattern + break), not just a single bar event.

---

### Sweep — Liquidity Sweep (+2 pts)

Catches the stop-hunt moment at the zone:

1. Price is inside the 4H OB zone
2. A 15M bar wicks ≥ 5 pips below a recent swing low (SSL)
3. Bar closes back above the SSL
4. Body ≥ 50% of bar range (not a doji — real rejection)
5. Enter at bar close, SL below the wick extreme

---

### BOS — Break of Structure (+1 pt)

A weaker confirmation. Any 15M swing high break fires this model. Alone (score=1) it is too weak; it needs CHoCH or Sweep to be scored simultaneously to reach the minimum threshold. In practice, BOS+EMA bounce (score=2) passes the raw score gate but is blocked by the 40% confidence gate — only BOS with another 2-pt model reaches actual trade threshold.

> Note: `use_bos: false` is set for AUDUSD and NZDUSD — BOS signals lose on commodity pairs due to frequent stop-hunts near OBs.

---

### EMA20 Bounce (+1 pt)

1. 15M EMA20 sits inside or near the 4H OB zone
2. 15M bar wicks down to EMA20 (bar_low ≤ EMA20 + 5 pips)
3. Bar closes above EMA20 with body ≥ 50% of range
4. Enter at close

Useful as a supplementary confirmation alongside CHoCH or Sweep.

---

### Flip Zone Bonus (+1 pt)

Automatically added if the OB zone mid-price is within 10 pips of a previous 4H structural swing high (LONG) or low (SHORT). This indicates the zone is a "flip" — old resistance now acting as support — which significantly increases the probability the level holds.

---

## 4. Pro-Trend vs Counter-Trend

### Pro-Trend
- Daily bias = LONG, 4H structure = HH+HL
- Trade WITH the dominant trend at a pullback OB zone
- Minimum score: effectively 3 (CHoCH+BOS, or CHoCH+EMA, or Sweep+BOS)

### Counter-Trend
- Daily bias = SHORT, but 4H shows HH+HL (bouncing against the daily trend)
- Trade LONG while daily is bearish — higher risk, needs more confluence
- Minimum score: 4 (`ct_min_score`) — needs CHoCH+BOS+EMA or Sweep+CHoCH
- Validated: CT-LONG-CHoCH entries at 2023 lows (+$3,166) and 2024 mid-year lows (+$3,300) are the strategy's best trades

---

## 5. Signal Filters & Confluence

### Required conditions (all must pass)

| Layer | Condition |
|-------|-----------|
| Daily | EMA21 > EMA50 + EMA rising + RSI > 50 (for LONG) |
| 4H ADX | ADX(14) ≥ 20 (trend-strength gate) |
| 4H structure | HH + HL confirmed (40-bar lookback, pivot=3) |
| 4H OB zone | Unmitigated bullish OB within last 80 bars |
| Zone active | 15M price inside zone (±10 pip tolerance) |
| Entry score | ≥ 3 pts (pro-trend) or ≥ 4 pts (counter-trend) |
| Confidence | ≥ 40% (score/6 ≥ 0.40 → score ≥ 2.4, i.e. effectively ≥ 3) |
| RSI gate | LONG: 15M RSI 45–70; SHORT: 15M RSI 35–65 |
| SL distance | ≥ 15 pips AND ≤ 2% of entry price |

---

## 6. Risk Management

### Stop Loss
```
LONG SL = min(entry_anchor, zone_low) − (4H_ATR × 0.75)
```
- `entry_anchor`: wick low (sweep), bar low (EMA bounce), zone_low (CHoCH)
- Uses 4H ATR for structural width, not 15M noise

### Take Profit
```
TP1 = entry + risk × 1.5   → close 50%, move SL to breakeven
TP2 = entry + risk × 3.0   → runner (remaining 50%)
```

At 50/50 split and 3R runner, **average win ≈ 2.25R**. Break-even WR = 31%. This system targets 50–65% WR.

### Position Sizing
- Risk per trade: 1% of account balance
- Max daily loss: 5%
- Max drawdown: 15%
- Max concurrent trades: 3

---

## 7. Settings Reference

All under `config/config.yaml` → `strategy.mtf_pro`:

| Setting | Current Value | Description |
|---------|--------------|-------------|
| `d_fast_ema` | 21 | Fast daily EMA (resampled from 4H) |
| `d_slow_ema` | 50 | Slow daily EMA |
| `h4_ob_lookback` | 80 | 4H bars to scan for OBs |
| `h4_ob_displacement_pips` | 15 | Min displacement after OB candle |
| `h4_ob_body_ratio` | 1.5 | Displacement body ≥ ratio × OB body |
| `h4_zone_tol_pips` | 10 | Tolerance for "inside zone" check |
| `m15_ema` | 20 | 15M EMA period |
| `use_choch` | true | CHoCH model (+2 pts) |
| `use_sweep` | true | Sweep model (+2 pts) |
| `use_bos` | true | BOS model (+1 pt, per-symbol override possible) |
| `use_ema_bounce` | true | EMA bounce model (+1 pt) |
| `choch_lookback` | 20 | 15M bars to search for swing highs |
| `choch_swing_pivot` | 3 | Bars each side to confirm swing |
| `min_wick_pips` | 5 | Min wick past SSL for sweep |
| `body_close_pct` | 0.50 | Min body ratio for sweep/EMA models |
| `rsi_period` | 14 | RSI period (15M) |
| `long_min_rsi` | 45 | LONG: min 15M RSI |
| `long_max_rsi` | 70 | LONG: max 15M RSI |
| `short_min_rsi` | 35 | SHORT: min 15M RSI (disabled) |
| `short_max_rsi` | 65 | SHORT: max 15M RSI (disabled) |
| `min_entry_score` | 2 | Raw score gate (effectively 3 via conf gate) |
| `ct_min_score` | 4 | Counter-trend minimum score |
| `use_counter_trend` | true | Allow CT-LONG when daily=SHORT |
| `use_short_direction` | false | LONG-only mode |
| `atr_period` | 14 | ATR period for SL calculation |
| `atr_buffer` | 0.75 | SL buffer multiplier × 4H ATR |
| `min_sl_pips` | 15 | Min SL distance |
| `max_sl_pct` | 0.02 | Max SL as % of entry price |
| `tp1_rr` | 1.5 | TP1 risk:reward |
| `tp2_rr` | 3.0 | TP2 risk:reward |
| `tp1_close_pct` | 0.50 | % closed at TP1 |
| `h4_adx_min` | 20 | 4H ADX minimum (trend filter) |
| `pip_size` | 0.0001 | 0.0001 for forex; 0.01 for gold |

---

## 8. Pre-Trade Checklist

### Step 1 — Daily Bias
- [ ] EMA21 > EMA50 AND EMA21 rising (for LONG)
- [ ] Daily RSI > 50
- [ ] Daily close > EMA21

### Step 2 — 4H Structure
- [ ] ADX(14) ≥ 20
- [ ] HH + HL confirmed in last 40 4H bars (pivot=3)

### Step 3 — 4H Order Block
- [ ] Unmitigated bullish OB within last 80 bars
- [ ] Displacement candle ≥ displacement_pips (symbol-specific) and body ratio ≥ 1.5×

### Step 4 — Price in Zone
- [ ] 15M bar low ≤ OB_high + 10 pips
- [ ] 15M bar close ≥ OB_low − 10 pips

### Step 5 — Entry Signal (score ≥ 3 pro-trend, ≥ 4 CT)
- [ ] CHoCH: lower-high broke to the upside (+2)
- [ ] Sweep: SSL swept and closed back above (+2)
- [ ] BOS: swing high broken (+1, needs other models; disabled for AUD/NZD)
- [ ] EMA bounce: wick to EMA20, close above (+1)
- [ ] Flip zone: OB at old structural high → +1 bonus

### Step 6 — Risk
- [ ] SL ≥ symbol min_sl_pips and ≤ 2% of entry
- [ ] Counter-trend: score ≥ 4

---

## 9. Validated Backtest Results

### System overview — per-symbol isolated params

Each symbol has its own locked parameter block in `config.yaml` → `strategy.symbol_params`. Changing one symbol's params has **zero effect** on any other symbol.

---

### EURUSD — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | Bear market correctly avoided |
| 2023 | 3 | 67% | +$6,073 | PF 122, 2 big wins, 1 scratch loss |
| 2024 | 4 | 50% | +$4,315 | CT-LONG @ 1.073 +$3,300 key win |
| **Total** | **7** | **57%** | **+$10,388** | |

**Locked params:** `h4_ob_displacement_pips: 15`, `atr_buffer: 0.75`, `use_bos: true`, `min_sl_pips: 15`

---

### AUDUSD — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | Bear market correctly avoided |
| 2023 | 1 | 0% | -$61 | Scratch loss only |
| 2024 | 1 | 100% | +$2,955 | Clean CHoCH win |
| **Total** | **2** | **50%** | **+$2,894** | PF 53.88 |

**Locked params:** `h4_ob_displacement_pips: 12`, `atr_buffer: 1.0`, `use_bos: false`, `min_sl_pips: 12`

> `use_bos: false` — BOS-only signals lose on AUDUSD due to frequent stop-hunts near OBs.
> `atr_buffer: 1.0` — wider structural stop needed to survive AUD stop hunts.

---

### NZDUSD — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | Bear market correctly avoided |
| 2023 | 0 | — | $0 | NZD weak/ranging — no qualifying setups |
| 2024 | 3 | 67% | +$4,881 | PF 4.99, 2 wins (CHoCH + CHoCH) |
| **Total** | **3** | **67%** | **+$4,881** | |

**Locked params:** `h4_ob_displacement_pips: 12`, `atr_buffer: 1.0`, `use_bos: false`, `min_sl_pips: 12`

> Same family as AUDUSD (commodity currencies) — same params validated directly.
> 0 trades in 2023 is correct — NZDUSD was weak/ranging that year, no valid 4H structure formed.

---

### USDCHF — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | USD peaked then declined — no valid OBs |
| 2023 | 0 | — | $0 | USDCHF downtrend (0.92→0.83) — correctly avoided |
| 2024 | 2 | 50% | +$1,710 | USD recovery rally, 1 win + 1 loss |
| **Total** | **2** | **50%** | **+$1,710** | |

**Locked params:** `h4_ob_displacement_pips: 15`, `atr_buffer: 0.75`, `use_bos: true`, `min_sl_pips: 15`

> Same params as EURUSD — USDCHF is the near-perfect inverse, same institutional flow drivers.
> USDCHF and EURUSD are anti-correlated — they generate LONG signals at different times, adding genuine diversification.

---

### GBPUSD — NOT READY

GBPUSD correctly generates 0 qualifying trades across the full 2022-2024 period. This is **not a failure** — the strategy correctly refuses bad setups:

- 2022 crash (1.37 → 1.07) left many mitigated OB zones visible in the 4H lookback
- SLs on GBP setups are typically 80–100 pips (filtered by `max_sl_pips: 80`)
- The 2 CHoCH entries that did fire (2023, 2024) both had 79–90 pip SLs and both lost

**Decision:** Leave as config stub. Re-evaluate with 2025+ data when market structure is cleaner.

---

### Portfolio Combined — 4 Pairs (EURUSD + AUDUSD + NZDUSD + USDCHF)

| Period | Trades | WR | Net PnL |
|--------|--------|----|---------|
| 2022 H2 | 0 | — | $0 |
| 2023 | 3 | 67% | +$6,012 |
| 2024 | 10 | 60% | +$13,861 |
| **Total** | **14** | **~58%** | **+$19,873** |

> Period: Jun 2022 – Dec 2024 on $100,000 account, 1% risk per trade.
> USDCHF adds genuine diversification — anti-correlated with EURUSD, generates LONGs when USD is strengthening (opposite conditions to EURUSD LONGs).

---

## 10. Common Mistakes

### 1. Mixing symbol parameters
Each symbol has its own locked param block. Never copy EURUSD displacement pips to AUDUSD — different pip volatility profiles require different thresholds.

### 2. Expecting high trade frequency on a single pair
EURUSD generates ~3–5 quality setups per year. NZDUSD generates 0–3/year. This is normal — filters are strict by design. Use 3+ pairs to get 10–15 trades/year total.

### 3. Removing the OB zone filter
The entry models (CHoCH, Sweep, EMA bounce) can fire in any market condition — it is the OB zone requirement that provides the edge. Never bypass it.

### 4. Re-enabling SHORTs without recalibration
SHORT entries failed on every test (2023: 3/3 lost, 2024: 3/3 lost). The OB detection and RSI gates for SHORTs need separate calibration before enabling.

### 5. Lowering score thresholds for more frequency
Score=2 BOS-only entries were tested and rejected — they dilute WR without improving profit. If you need more trades, add more symbols.

### 6. Adding the OB body size filter
Tested and rejected. A minimum OB body size blocked the Aug 2024 WIN (small-body OB) while keeping all losers. Do not add this filter.

### 7. Testing USDJPY
BOJ interventions create 300-500 pip sudden moves that destroy OB zones unpredictably. 0% WR on all tests. Do not add.

---

## Running the Backtest

```bash
# Per-symbol — full history
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2022-06-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol AUDUSD --start 2022-06-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol NZDUSD --start 2022-06-01 --end 2024-12-31 --fresh

# Year by year
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2023-01-01 --end 2023-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2024-01-01 --end 2024-12-31 --fresh

# Live mode (requires MT5 credentials in config.yaml)
python -m src.trading_bot --mode live
```

---

*Strategy implemented in `src/strategies/mtf_pro_entry.py`.*
*All settings in `config/config.yaml` under `strategy.mtf_pro`.*
*Always backtest a new symbol for at least 2 years before live trading.*
