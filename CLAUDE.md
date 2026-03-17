# MTF PRO Trading Bot — CLAUDE.md

---

## Quick Start

```bash
# Install dependencies (first time only)
pip install -r requirements.txt

# ── Live demo trading ──────────────────────────────────────────────────────
# MT5 terminal must be open and logged in before running
python -m src.trading_bot --mode live

# ── Backtesting (validated date windows) ──────────────────────────────────
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2023-01-01 --end 2023-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2024-01-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol AUDUSD --start 2023-01-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol NZDUSD --start 2024-01-01 --end 2024-12-31 --fresh
python -m src.trading_bot --mode backtest --symbol USDCHF --start 2024-01-01 --end 2024-12-31 --fresh
```

---

## Project Overview

Python algorithmic trading bot running the **MTF PRO** strategy — a 3-timeframe
system that identifies Order Block zones and waits for scored 15M entry triggers.

**Git branches**
- `v1.0` tag — 4 locked pairs, baseline results
- `v2-1h-intermediate` (current) — 1H OB architecture + live demo readiness

---

## Project Structure

```
trading bot h/
├── src/
│   ├── trading_bot.py              # Main orchestrator — live loop + backtest engine
│   ├── strategies/
│   │   ├── mtf_pro_entry.py        # ACTIVE — MTFProEntryGenerator (3-TF strategy)
│   │   └── counter_trend_entry.py  # LEGACY — EntrySignal/SignalDirection types only
│   ├── data/
│   │   └── mt5_connector.py        # MT5 connection, data fetch, order execution
│   ├── risk/
│   │   └── position_manager.py     # RiskManager, PositionSizer, TradeExecutor
│   └── analysis/                   # LEGACY — not used by active strategy
├── config/
│   ├── config.yaml                 # MT5 creds, symbols, strategy + risk params
│   └── config.example.yaml         # Safe copy (no credentials) for version control
├── logs/                           # Runtime logs (auto-created)
├── results/                        # Trade history JSON (auto-created)
└── requirements.txt
```

---

## Strategy: MTF PRO v5

**3-timeframe flow:**

```
1D bias  ──►  4H Order Block zone  ──►  15M entry trigger
(EMA21/50       (unmitigated OB,          (scored models:
 + RSI > 50)     price entering zone)      CHoCH +2, Sweep +2,
                                           BOS +1, EMA20 +1,
                                           Flip zone +1)
```

**Direction:** LONG-only mode (`use_short_direction: false`).
Both pro-trend LONG and counter-trend LONG are active.

**Minimum score to trade:**
- Pro-trend: `min_entry_score: 2` + confidence ≥ 0.40 (effectively score ≥ 3)
- Counter-trend: `ct_min_score: 4`

**SL/TP:**
- SL = 4H zone_low − ATR_buffer × 4H_ATR
- TP1 = 1.5R (SL moves to breakeven)
- TP2 = 3.0R (full exit)

---

## Validated Pairs (all LOCKED — do not change params)

| Pair | Window | Trades | WR | PnL |
|------|--------|--------|----|-----|
| EURUSD | 2023 | 3 | 67% | +$6,073 |
| EURUSD | 2024 | 4 | 50% | +$4,315 |
| AUDUSD | 2023–2024 | 2 | 50% | +$2,894 |
| NZDUSD | 2024 | 3 | 67% | +$4,881 |
| USDCHF | 2024 | 2 | 50% | +$1,710 |
| **Total** | | **14** | **~58%** | **+$19,873** |

**~5 trades/year** across all 4 pairs — frequency is a known ceiling with strict filters.

> **Note:** MT5 demo server periodically updates historical data. If a combined
> 2022-2024 run gives different results, always test each year separately to
> find where the trades land.

---

## Per-Symbol Locked Parameters

Each symbol has isolated params in `config.yaml → strategy.symbol_params`.
Changes to one symbol cannot affect others.

| Symbol | disp_pips | atr_buffer | use_bos | min_sl |
|--------|-----------|------------|---------|--------|
| EURUSD | 15 | 0.75 | true | 15 |
| AUDUSD | 12 | 1.0 | false | 12 |
| NZDUSD | 12 | 1.0 | false | 12 |
| USDCHF | 15 | 0.75 | true | 15 |

---

## Implementation Status

### COMPLETE — ready for live demo

| Component | File | Status |
|-----------|------|--------|
| MT5 connection + login | `mt5_connector.py` | ✅ |
| Historical data fetch (H4, H1, M15) | `mt5_connector.py` | ✅ |
| Live data feed (all TFs) | `mt5_connector.py` | ✅ |
| Signal generation (MTF PRO) | `mtf_pro_entry.py` | ✅ |
| Order placement (buy/sell + SL/TP) | `mt5_connector.py` | ✅ |
| Position sizing (% risk) | `position_manager.py` | ✅ |
| Daily loss / max drawdown limits | `position_manager.py` | ✅ |
| MT5 position sync (SL/TP close detection) | `trading_bot.py` | ✅ |
| TP1 breakeven (modify SL on MT5) | `trading_bot.py` | ✅ |
| Walk-forward backtest engine | `trading_bot.py` | ✅ |
| Results saving (JSON) + logging | `trading_bot.py` | ✅ |

### NOT BUILT (future work)

| Feature | Notes |
|---------|-------|
| Chart visualization | Trade entries/exits on OHLC, equity curve |
| 1H OB as entry filter | Architecture in place (`use_h1_ob: true`), needs testing |
| Partial close at TP1 | Live mode uses SL-to-BE instead (simpler, no counter-order needed) |
| More pairs | GBPUSD, USDCAD, USDJPY, XAUUSD all tested and rejected — see below |

---

## Rejected Pairs

| Pair | Reason |
|------|--------|
| GBPUSD | 0 valid trades; 2022 crash left mitigated OBs; SLs 79–90 pips (filtered) |
| USDJPY | BOJ interventions destroy OB zones; 0% WR in testing |
| USDCAD | Choppy/ranging structure; only conf=33% signals that fail gate |
| XAUUSD | EMA bias lags gold's sharp reversals; 4 consecutive losses in 2023 |

---

## Key Config Decisions

- `use_short_direction: false` — SHORT entries tested and lost; LONG-only
- `ct_min_score: 4` — prevents weak counter-trend entries in bear markets
- `confidence ≥ 0.40` — in risk manager; effectively requires score ≥ 3
- `d_fast_ema: 21 / d_slow_ema: 50` — faster than 50/100, catches reversals sooner
- `use_h1_ob: false` — 1H OB architecture built but disabled; switching SL to
  1H OB low worsened EURUSD 2024 from 50% WR to 25% WR; safe default is off

---

## v2 Architecture Note (1H Intermediate OB)

The v2 branch adds a 1H OB layer between 4H zone and 15M entry.

**Correct design (current):** 1H OB is a presence filter only — SL always
anchors to 4H zone_low. When `use_h1_ob: true`, signals get an extra
`'1H-OB'` confluence label but SL/TP are unchanged.

**Broken design (fixed):** Earlier implementation switched `active_zone` to
the 1H OB, making the 1H OB low the SL anchor. This widened TP targets and
turned EURUSD 2024 winners into losses (25% WR vs 50% WR).

To test the 1H OB filter: set `use_h1_ob: true` in config, run all 4 pairs,
compare against the locked baselines above before enabling live.

---

## Key File Locations

| Task | File | Key Class/Method |
|------|------|-----------------|
| Generate signals | [src/strategies/mtf_pro_entry.py](src/strategies/mtf_pro_entry.py) | `MTFProEntryGenerator.generate_signals()` |
| Fetch MT5 data | [src/data/mt5_connector.py](src/data/mt5_connector.py) | `MT5Connector.fetch_historical_range()` |
| Risk / sizing | [src/risk/position_manager.py](src/risk/position_manager.py) | `RiskManager`, `PositionSizer` |
| Live loop + backtest | [src/trading_bot.py](src/trading_bot.py) | `TradingBot.run()`, `run_backtest()` |
| Position sync | [src/trading_bot.py](src/trading_bot.py) | `_sync_positions_from_mt5()` |
| TP1 breakeven | [src/trading_bot.py](src/trading_bot.py) | `_check_live_tp1()` |
| Config | [config/config.yaml](config/config.yaml) | MT5 creds, symbols, locked params |
