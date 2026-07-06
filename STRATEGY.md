# MTF PRO Strategy — Trading Guide
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2026-01-01 --end 2026-03-20 --fresh


cd "C:\Users\user\Desktop\trading bot h"
python -m src.trading_bot --mode live
And for the scalper in a second CMD window:


cd "C:\Users\user\Desktop\trading bot h\scalping"
python live_trader.py
**Version:** 2.0 — Python / MT5 (branch: `v2-1h-intermediate`)
**Pairs:** EURUSD · AUDUSD · NZDUSD · USDCHF (all LOCKED)
**Implementation:** `src/strategies/mtf_pro_entry.py`
**Config:** `config/config.yaml` → `strategy.mtf_pro`

---

## Quick Start

```bash
# Live demo (MT5 terminal must be open and logged in)
python -m src.trading_bot --mode live

# Backtests — use these exact windows to reproduce locked results
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2023-01-01 --end 2023-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2024-01-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol AUDUSD --start 2023-01-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol NZDUSD --start 2024-01-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol USDCHF --start 2024-01-01 --end 2024-12-31 --fresh
```

> **MT5 demo data note:** Demo servers periodically update historical bars. If a
> 2022–2024 combined run gives different results than expected, always test each
> year separately to find where the trades land.

---

## Table of Contents

1. [Strategy Overview](#1-strategy-overview)
2. [The Three-Timeframe Framework](#2-the-three-timeframe-framework)
3. [Entry Models & Scoring](#3-entry-models--scoring)
4. [Pro-Trend vs Counter-Trend](#4-pro-trend-vs-counter-trend)
5. [Signal Filters & Confluence](#5-signal-filters--confluence)
6. [Risk Management](#6-risk-management)
7. [v2 Architecture — 1H Intermediate OB](#7-v2-architecture--1h-intermediate-ob)
8. [Live Trading](#8-live-trading)
9. [Settings Reference](#9-settings-reference)
10. [Pre-Trade Checklist](#10-pre-trade-checklist)
11. [Validated Backtest Results](#11-validated-backtest-results)
12. [Common Mistakes](#12-common-mistakes)

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

**LONG bias** (all must be true):
- EMA21 > EMA50 AND EMA21 has a positive 10-day slope (rising)
- Daily close > EMA21 × (1 + 0.001 margin)
- Daily RSI(14) > 50

**SHORT bias** (symmetric — currently disabled via `use_short_direction: false`):
- EMA21 < EMA50 AND EMA21 falling
- Daily close < EMA21
- Daily RSI(14) < 50

**Neutral → no trades** if conditions are mixed.

> EMA21/EMA50 was chosen over EMA50/EMA100 because it catches trend reversals
> faster, especially at yearly turning points.

---

### 4H — Order Block Zone

Once daily bias is confirmed, scan the last 80 4H bars for valid unmitigated Order Blocks.

**Bullish OB (demand zone for LONGs):**
- Candle `i` is bearish
- Candle `i+1` is strongly bullish: body ≥ 1.5× OB body AND closes above OB high by ≥ disp_pips
- No 4H close below `OB_low` since formation (unmitigated)
- Zone = `[OB_low, OB_high]`

**4H structure confirmation:**
- Pro-trend LONG: 4H must show HH + HL (40-bar lookback, 3-bar pivot)
- Counter-trend LONG: 4H must show HH + HL even while daily is SHORT

---

### 15M — Entry Scoring

When 15M price enters the 4H OB zone, each model is evaluated simultaneously.

| Model | Points | Description |
|-------|--------|-------------|
| CHoCH | +2 | Lower-high formed, 15M bar breaks above the lower-high |
| Sweep | +2 | SSL swept ≥ 5 pips, body closes back above SSL ≥ 50% of range |
| BOS | +1 | Any 15M swing high break (needs other confluence to reach threshold) |
| EMA bounce | +1 | Wick to EMA20 inside zone, close back above with body ≥ 50% |
| Flip zone | +1 | OB mid within 10 pips of a previous 4H structural swing high |

**Minimum scores:**
- Pro-trend: `min_entry_score = 2` + confidence ≥ 40% → effectively score ≥ 3
- Counter-trend: `ct_min_score = 4`

---

## 3. Entry Models & Scoring

### CHoCH — Change of Character (+2 pts)

The highest-conviction entry. Requires a **lower-high structure** before the break:

1. Find the 2 most recent 15M confirmed swing highs
2. Last swing high < previous swing high → lower-high formed
3. Current bar closes **above** the lower-high → buyers have taken control at the zone
4. Enter at bar close

---

### Sweep — Liquidity Sweep (+2 pts)

Catches the stop-hunt moment at the zone:

1. Price is inside the 4H OB zone
2. A 15M bar wicks ≥ 5 pips below a recent swing low (SSL)
3. Bar closes back above the SSL
4. Body ≥ 50% of bar range
5. Enter at bar close, SL below the wick extreme

---

### BOS — Break of Structure (+1 pt)

A weaker confirmation. Any 15M swing high break fires this model. Alone it is too weak; needs a 2-pt model to reach the entry threshold. BOS+EMA bounce (score=2) is still blocked by the 40% confidence gate.

> `use_bos: false` for AUDUSD and NZDUSD — BOS signals lose on commodity pairs
> due to frequent stop-hunts near OBs.

---

### EMA20 Bounce (+1 pt)

1. 15M EMA20 sits inside or near the 4H OB zone
2. 15M bar wicks down to EMA20 (bar_low ≤ EMA20 + 5 pips)
3. Bar closes above EMA20 with body ≥ 50% of range

Useful as supplementary confirmation alongside CHoCH or Sweep.

---

### Flip Zone Bonus (+1 pt)

Automatically added if the OB zone mid-price is within 10 pips of a previous 4H structural swing high (LONG). Indicates the zone is a "flip" — old resistance now acting as support.

---

## 4. Pro-Trend vs Counter-Trend

### Pro-Trend
- Daily bias = LONG, 4H structure = HH+HL
- Trade WITH the dominant trend at a pullback OB zone
- Minimum score: effectively 3 (CHoCH+BOS, CHoCH+EMA, Sweep+BOS, etc.)

### Counter-Trend
- Daily bias = SHORT, but 4H shows HH+HL (bouncing against the daily trend)
- Trade LONG while daily is bearish — higher risk, needs more confluence
- Minimum score: 4 (`ct_min_score`) — needs CHoCH+BOS+EMA or Sweep+CHoCH
- Best trades in the backtest are CT-LONG entries at 2023/2024 macro lows

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
| Entry score | ≥ 3 pts pro-trend, ≥ 4 pts counter-trend |
| Confidence | ≥ 40% (score / 6 ≥ 0.40) |
| RSI gate | LONG: 15M RSI 45–70 |
| SL distance | ≥ symbol min_sl_pips AND ≤ 2% of entry price |

---

## 6. Risk Management

### Stop Loss
```
LONG SL = sl_anchor − (4H_ATR × atr_buffer)

sl_anchor = zone_low     (CHoCH / BOS)
          = wick_extreme (Sweep — whichever is lower: wick low or zone_low)
          = bar_low      (EMA bounce — whichever is lower: bar low or zone_low)
```

SL is always anchored to the **4H OB zone_low** for CHoCH and BOS.
For Sweep and EMA bounce, the wick/bar extreme is used if it is lower than zone_low.

### Take Profit

```
TP1 = entry + risk × 1.5  →  SL moves to breakeven (live: modify_position on MT5)
TP2 = entry + risk × 3.0  →  full exit
```

In backtest: 50% closed at TP1, 50% runner to TP2.
In live mode: full position runs to TP2, SL moved to entry when TP1 price is reached.

At 3R runner and 50% WR: **average trade ≈ +1.25R**. Break-even WR = 25%.

### Position Sizing
- Risk per trade: 1% of account balance
- Max daily loss: 5%
- Max drawdown: 15%
- Max concurrent trades: 3

---

## 7. v2 Architecture — 1H Intermediate OB

### What v2 adds

A 1H OB layer sits between the 4H macro zone and the 15M entry trigger:

```
4H OB zone  →  1H OB nested inside  →  15M entry
(macro)        (presence filter)        (scored models)
```

When `use_h1_ob: true`, the bot checks for a fresh unmitigated 1H Order Block
nested inside the active 4H zone. If one exists, it is labeled `'1H-OB'` in
`criteria_met`. If none exists, the bot still proceeds with the 4H zone alone.

**Key design rule — SL always anchors to 4H zone:**
The 1H OB never changes the SL level. `sl_anchor = active_h4_zone['low']` in
all cases. This was a critical fix from the broken v1.5 implementation.

### Why `use_h1_ob: false` is the safe default

An earlier implementation switched `active_zone` to the 1H OB, making the 1H
OB low become the SL anchor. Since the 1H OB low can be below the 4H OB low,
TP targets became wider and previously winning trades stopped out short:

| | EURUSD Sep 2024 trade |
|--|--|
| v1 (`h1_ob: false`) | SL=1.10923, TP=1.11751 → price hit 1.119 → **WIN +$3,610** |
| broken v1.5 | SL=1.10815, TP=1.12075 → price only reached 1.119 → **LOSS** |

Full EURUSD 2024 comparison:
- v1: 4 trades, **50% WR, +$4,315**
- broken v1.5: 2 trades, **25% WR, +$513**

### To test the 1H OB filter

1. Set `use_h1_ob: true` in `config/config.yaml`
2. Run all 4 pairs against locked baselines above
3. If all results match or improve → safe to enable live

---

## 8. Live Trading

### Prerequisites
- MT5 terminal open and logged into demo account
- Credentials set in `config/config.yaml` (`mt5.account`, `mt5.password`, `mt5.server`)

### How the live loop works

Every 60 seconds:
1. **Scan** — fetch H4, H1, M15 data for all 4 pairs and run signal generator
2. **Execute** — if signal passes all filters, place order on MT5 with SL + TP2 set
3. **Sync** — call `get_open_positions()` to detect any positions MT5 closed via SL/TP;
   update internal state so risk limits reset and the next trade can open
4. **TP1 check** — if price has crossed TP1, call `modify_position()` to slide SL
   to entry price (breakeven) on MT5

### Frequency expectation

~5 trades/year across 4 pairs with current filters. Expect **days to weeks** between
live signals. This is normal — the strategy is selective by design.

---

## 9. Settings Reference

All under `config/config.yaml` → `strategy.mtf_pro`:

| Setting | Default | Description |
|---------|---------|-------------|
| `d_fast_ema` | 21 | Fast daily EMA (resampled from 4H) |
| `d_slow_ema` | 50 | Slow daily EMA |
| `h4_ob_lookback` | 80 | 4H bars to scan for OBs |
| `h4_ob_displacement_pips` | 15 | Min displacement after OB candle |
| `h4_ob_body_ratio` | 1.5 | Displacement body ≥ ratio × OB body |
| `h4_zone_tol_pips` | 10 | Tolerance for "inside zone" check |
| `use_h1_ob` | false | Enable 1H OB presence filter (v2 — off by default) |
| `h1_ob_lookback` | 80 | 1H bars to scan for nested OBs |
| `h1_ob_displacement_pips` | 8 | Min displacement for 1H OB |
| `h1_zone_tol_pips` | 5 | Tolerance for 1H zone check |
| `m15_ema` | 20 | 15M EMA period |
| `use_choch` | true | CHoCH model (+2 pts) |
| `use_sweep` | true | Sweep model (+2 pts) |
| `use_bos` | true | BOS model (+1 pt, per-symbol override available) |
| `use_ema_bounce` | true | EMA bounce model (+1 pt) |
| `choch_lookback` | 20 | 15M bars to search for swing highs |
| `choch_swing_pivot` | 3 | Bars each side to confirm swing |
| `min_wick_pips` | 5 | Min wick past SSL for sweep |
| `body_close_pct` | 0.50 | Min body ratio for sweep/EMA models |
| `rsi_period` | 14 | RSI period (15M) |
| `long_min_rsi` | 45 | LONG: min 15M RSI |
| `long_max_rsi` | 70 | LONG: max 15M RSI |
| `min_entry_score` | 2 | Raw score gate (conf gate makes it effectively 3) |
| `ct_min_score` | 4 | Counter-trend minimum score |
| `use_counter_trend` | true | Allow CT-LONG when daily=SHORT |
| `use_short_direction` | false | LONG-only mode |
| `atr_period` | 14 | ATR period for SL calculation |
| `atr_buffer` | 0.75 | SL buffer multiplier × 4H ATR |
| `min_sl_pips` | 15 | Min SL distance |
| `max_sl_pct` | 0.02 | Max SL as % of entry price |
| `tp1_rr` | 1.5 | TP1 risk:reward |
| `tp2_rr` | 3.0 | TP2 risk:reward |
| `tp1_close_pct` | 0.50 | % closed at TP1 (backtest only) |
| `h4_adx_min` | 20 | 4H ADX minimum (trend filter) |
| `pip_size` | 0.0001 | 0.0001 for forex |

---

## 10. Pre-Trade Checklist

### Step 1 — Daily Bias
- [ ] EMA21 > EMA50 AND EMA21 rising
- [ ] Daily RSI > 50
- [ ] Daily close > EMA21

### Step 2 — 4H Structure
- [ ] ADX(14) ≥ 20
- [ ] HH + HL confirmed in last 40 4H bars (pivot=3)

### Step 3 — 4H Order Block
- [ ] Unmitigated bullish OB within last 80 bars
- [ ] Displacement ≥ disp_pips (symbol-specific) and body ratio ≥ 1.5×

### Step 4 — Price in Zone
- [ ] 15M bar low ≤ OB_high + 10 pips
- [ ] 15M bar close ≥ OB_low − 10 pips

### Step 5 — Entry Signal (score ≥ 3 pro-trend, ≥ 4 CT)
- [ ] CHoCH: lower-high broke to the upside (+2)
- [ ] Sweep: SSL swept and closed back above (+2)
- [ ] BOS: swing high broken (+1, needs other confluence; disabled AUD/NZD)
- [ ] EMA bounce: wick to EMA20, close above (+1)
- [ ] Flip zone: OB at old structural high (+1 bonus)

### Step 6 — Risk
- [ ] SL ≥ symbol min_sl_pips and ≤ 2% of entry
- [ ] Counter-trend: score ≥ 4

---

## 11. Validated Backtest Results

Each symbol has an **isolated parameter block** in `config.yaml → strategy.symbol_params`.
Changing one symbol's params has zero effect on any other symbol.

---

### EURUSD — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | Bear market correctly avoided |
| 2023 | 3 | 67% | +$6,073 | PF 122 — 2 wins, 1 scratch loss |
| 2024 | 4 | 50% | +$4,315 | CT-LONG @ 1.073 key win |
| **Total** | **7** | **57%** | **+$10,388** | |

**Locked params:** `disp_pips: 15` · `atr_buffer: 0.75` · `use_bos: true` · `min_sl: 15`

---

### AUDUSD — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | Bear market correctly avoided |
| 2023–2024 | 2 | 50% | +$2,894 | PF 53.88 — 1 boundary trade (opens 2023, closes 2024) |
| **Total** | **2** | **50%** | **+$2,894** | |

**Locked params:** `disp_pips: 12` · `atr_buffer: 1.0` · `use_bos: false` · `min_sl: 12`

> `use_bos: false` — BOS signals lose on AUDUSD due to frequent stop-hunts near OBs.
> `atr_buffer: 1.0` — wider structural stop needed to survive AUD stop hunts.
> The 2-trade result spans the year boundary; use the 2023–2024 combined window to reproduce it.

---

### NZDUSD — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | Bear market correctly avoided |
| 2023 | 0 | — | $0 | NZD weak/ranging — no qualifying setups |
| 2024 | 3 | 67% | +$4,881 | PF 4.99 — 2 CHoCH wins |
| **Total** | **3** | **67%** | **+$4,881** | |

**Locked params:** `disp_pips: 12` · `atr_buffer: 1.0` · `use_bos: false` · `min_sl: 12`

> Same family as AUDUSD (commodity currencies) — same params validated directly.
> 0 trades in 2023 is correct; NZDUSD had no valid 4H structure that year.

---

### USDCHF — LOCKED

| Period | Trades | WR | Net PnL | Notes |
|--------|--------|----|---------|-------|
| 2022 H2 | 0 | — | $0 | USD peaked then declined — no valid OBs |
| 2023 | 0 | — | $0 | USDCHF downtrend (0.92→0.83) — correctly avoided |
| 2024 | 2 | 50% | +$1,710 | USD recovery rally, 1 win + 1 loss |
| **Total** | **2** | **50%** | **+$1,710** | |

**Locked params:** `disp_pips: 15` · `atr_buffer: 0.75` · `use_bos: true` · `min_sl: 15`

> Same params as EURUSD — USDCHF is the near-perfect inverse.
> USDCHF and EURUSD are anti-correlated — they generate LONG signals at different
> times, adding genuine diversification to the portfolio.

---

### GBPUSD — NOT READY

Generates 0 qualifying trades across 2022–2024 by design:
- 2022 crash (1.37 → 1.07) left many mitigated OBs in the 4H lookback
- SLs on GBP setups are typically 80–100 pips (filtered by `max_sl_pips: 80`)

Re-evaluate with 2025+ data when market structure is cleaner.

---

### Rejected Pairs

| Pair | Reason |
|------|--------|
| USDJPY | BOJ interventions destroy OB zones unpredictably — 0% WR |
| USDCAD | Choppy/ranging structure — only conf=33% signals that fail the gate |
| XAUUSD | EMA bias lags gold's sharp reversals; 4 consecutive losses in 2023 |

---

### Portfolio Combined — 4 Pairs

| Period | Trades | WR | Net PnL |
|--------|--------|----|---------|
| 2022 H2 | 0 | — | $0 |
| 2023 | 3 | 67% | +$6,073 |
| 2023–2024 combined | 2 | 50% | +$2,894 |
| 2024 | 9 | 56% | +$10,906 |
| **Total (all windows)** | **14** | **~58%** | **+$19,873** |

> $100,000 account · 1% risk per trade · ~5 trades/year average
> USDCHF anti-correlation with EURUSD provides genuine diversification.

---

## 12. Common Mistakes

### 1. Mixing symbol parameters
Each symbol has its own locked param block. Never copy EURUSD displacement pips to AUDUSD.

### 2. Expecting high frequency on a single pair
EURUSD generates ~3–4 quality setups per year. This is normal — use 4 pairs to get 12–15 trades/year.

### 3. Removing the OB zone filter
CHoCH and Sweep can fire anywhere — the OB zone requirement is what provides the edge.

### 4. Re-enabling SHORTs without recalibration
SHORT entries failed on every test. The OB detection and RSI gates for SHORTs need separate calibration.

### 5. Lowering score thresholds for more frequency
Score=2 BOS-only entries were tested and rejected. If you need more trades, add more symbols.

### 6. Enabling `use_h1_ob: true` without testing
Check all 4 pairs against their locked baselines first. The 1H OB architecture is sound, but any config change to the SL logic requires full revalidation.

### 7. Testing 2022–2024 combined range for all pairs
NZDUSD and USDCHF trades land in 2024, not spread across 2022–2024. Use the validated date windows in the Quick Start section at the top.

---

*Strategy implemented in `src/strategies/mtf_pro_entry.py`.*
*All settings in `config/config.yaml` under `strategy.mtf_pro`.*
*Always backtest a new symbol for at least 2 years before live trading.*
