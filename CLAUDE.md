# Counter Trend Trading Bot — CLAUDE.md

## Project Overview

A Python-based algorithmic trading bot implementing the **Counter Trend Trading System** and **Pro Trend Trading System** from `Counter Trend Trading System.pdf`. Uses a multi-timeframe analysis framework (Weekly → Daily → 4H → 15M → 1M) to identify high-probability trade entries.

---

## Project Structure

```
trading bot h/
├── src/
│   ├── trading_bot.py              # Main bot orchestrator + Backtester
│   ├── analysis/
│   │   ├── swing_detection.py      # Swing High/Low detection (Phase 1)
│   │   ├── break_of_structure.py   # BoS & CoC detection (Phases 3-4)
│   │   ├── zone_detection.py       # POI/Zone detection (Phases 2, 5)
│   │   └── bias_analysis.py        # Multi-timeframe bias (Phases 1-2)
│   ├── strategies/
│   │   └── counter_trend_entry.py  # Signal generation (Phases 3-8)
│   └── risk/
│       └── position_manager.py     # Risk management & trade execution
├── config/
│   ├── config.yaml                 # Main config (MT5, trading, risk)
│   └── strategy_params.yaml        # Strategy parameters
├── logs/                           # Runtime logs
├── results/                        # Trade history, open positions, reports
└── requirements.txt                # Dependencies (MT5, pandas, numpy, ta-lib)
```

---

## Strategy Summary (from PDF)

### Counter Trend System (8 Phases)
| Phase | Timeframe | Action |
|-------|-----------|--------|
| 1 | Weekly | Mark SH/SL, BoS, POI — establish macro bias |
| 2 | Daily | Refine weekly POI (flip zone, structure, liq sweep, inducement) |
| 3 | 4H | Mark SH/SL, BoS, identify 4H POI, wait for CoC |
| 4 | 15M | Wait for Double Swing BoS — catches HLO, targets old HH |
| 5 | 15M | Identify POI BELOW equilibrium (discount zone) |
| 6 | 1M | Wait for CoC with V-shape sharp reaction at POI |
| 7 | Entry | Option A: entry at extreme / Option B: above extreme (needs flip+liq+vol) |
| 8 | Exit | Target old HH (15M); secondary: nearest 4H supply |

### Pro Trend System (same 8 phases, different logic)
- Entry WITH weekly bias, not against it
- Target: next structural resistance/support (extended moves)
- Scale-in 50%+50% approach
- Trail stop: close 33% at T1, move to BE, trail to T2/T3

### Key Terminology
- **BoS** = Break of Structure
- **CoC** = Change of Character (stronger BoS, V-shape)
- **POI** = Point of Interest (supply/demand zone)
- **HH/HL/LH/LL** = Higher High/Low, Lower High/Low
- **EQ** = Equilibrium (midpoint of swing range)
- **Premium** = Upper half of range (sell area)
- **Discount** = Lower half of range (buy area)
- **Flip Zone** = Zone that switched from supply to demand or vice versa
- **CoC Line** = Line formed by new high/low that caused BoS (not just any swing)

---

## Completion Status

### COMPLETED (~60% overall)

#### Analysis Layer (~80% done)
- [x] Swing Detection — `SwingDetector` + `AdaptiveSwingDetector` (ATR-based)
- [x] Break of Structure — `BreakOfStructureDetector.detect_bos()` + `detect_sequential_bos()`
- [x] Change of Character — `ChangeOfCharacterDetector` with V-shape + rejection candle detection
- [x] Zone Detection — `ZoneDetector` with demand/supply zones, premium/discount, POI
- [x] Zone Validation — flip zone, structure zone, liquidity sweep, inducement checks
- [x] Multi-Timeframe Bias — `BiasAnalyzer` across W/D/4H/15M
- [x] Counter-Trend Bias — `CounterTrendBiasAnalyzer.find_counter_trend_setup()`
- [x] Multi-TF Zone Manager — `MultiTimeframeZoneManager` with zone alignment

#### Strategy Layer (~70% done)
- [x] Counter Trend Entry — `CounterTrendEntryGenerator` (full 8-phase checklist)
- [x] Pro Trend Entry — `ProTrendEntryGenerator` (basic implementation)
- [x] Entry Types — Option A (extreme) + Option B (above extreme)
- [x] Signal Validation — `validate_signal()` method

#### Risk Management (~85% done)
- [x] Position Sizing — `PositionSizer` (% risk-based, pip value per symbol)
- [x] Risk Manager — daily loss limit, max drawdown, max open positions
- [x] Trailing Stop — activation at configurable RR ratio
- [x] Stop Out Checks — SL/TP hit detection
- [x] Trade Executor — market/limit orders with spread + slippage simulation

#### Bot Orchestration (~50% done)
- [x] `TradingBot` class — main loop, scheduler (08:00/12:00/16:00 sessions)
- [x] `Backtester` class — multi-symbol backtest runner
- [x] Results Saving — JSON trade history, open positions, risk summary
- [x] Logging — console + file logging
- [x] CLI — `--mode live/backtest --symbol --start --end --config`
- [x] Configuration — full `config.yaml` with MT5, trading, risk, strategy settings

---

### NOT IMPLEMENTED (Remaining ~40%)

#### 1. Data Layer — CRITICAL MISSING
- [ ] MT5 Connection — `mt5.initialize()`, login, `copy_rates_from_pos()`
- [ ] Historical Data Loading — `_load_historical_data()` returns `None` (placeholder only)
- [ ] Live Price Feed — `_analyze_symbol()` returns `[]` (placeholder only)
- [ ] Timeframe Mapping — MT5 timeframe constants (e.g. `mt5.TIMEFRAME_W1`)
- [ ] Data Normalization — MT5 rates dict → pandas OHLC DataFrame

#### 2. Backtesting Engine — NOT BUILT
- [ ] Historical Data Iteration — bar-by-bar walk-forward simulation
- [ ] Signal Generation on History — `_generate_backtest_signals()` is empty
- [ ] Performance Metrics — Sharpe ratio, profit factor, max consecutive losses

#### 3. Pro Trend Advanced Features — PARTIAL
- [ ] Scale-in Position Management — 50% at extreme + 50% on momentum confirmation
- [ ] Multiple Targets — Primary (15M/4H), Secondary (4H zone), Tertiary (Weekly)
- [ ] Trailing Exit Logic — close 33% at T1, move SL to BE, trail on 1M structure

#### 4. Visualization — NOT BUILT
- [ ] Chart Plotting — trade entries/exits on OHLC chart
- [ ] Zone Visualization — supply/demand zone overlay
- [ ] Equity Curve — account equity over time

#### 5. Known Bugs to Fix
- [ ] **CoC-at-POI check** — `counter_trend_entry.py:307` calls `poi.contains(coc['index'])`, comparing zone *price range* with candle *index integer*. Should compare zone price range with the candle's price at that index.
- [ ] **`_create_demand_zones()` bug** — `zone_detection.py:147` iterates `swing_lows` but checks `SwingType.HIGH` on elements that are already filtered lows.
- [ ] **Pro Trend confidence** — hardcoded to `0.7` in `counter_trend_entry.py:529`, should be dynamically calculated like counter-trend.

---

## Development Priorities (Next Steps)

1. **MT5 Data Integration** — implement `_load_historical_data()` and `_analyze_symbol()` with real MT5 API calls
2. **Fix CoC-at-POI bug** — critical for correct 1M confirmation logic
3. **Backtesting iteration** — bar-by-bar walk-forward engine on historical data
4. **Pro Trend multi-target exits** — trailing logic per strategy PDF
5. **Visualization** — plot zones and trades on OHLC charts

---

## Key File Locations

| Task | File | Key Class/Method |
|------|------|-----------------|
| Detect swings | [src/analysis/swing_detection.py](src/analysis/swing_detection.py) | `SwingDetector.detect_swings()` |
| Detect BoS | [src/analysis/break_of_structure.py](src/analysis/break_of_structure.py) | `BreakOfStructureDetector.detect_bos()` |
| Detect CoC | [src/analysis/break_of_structure.py](src/analysis/break_of_structure.py) | `ChangeOfCharacterDetector.detect_coc()` |
| Find zones | [src/analysis/zone_detection.py](src/analysis/zone_detection.py) | `ZoneDetector.detect_zones_from_swings()` |
| Timeframe bias | [src/analysis/bias_analysis.py](src/analysis/bias_analysis.py) | `BiasAnalyzer.analyze_all_timeframes()` |
| Generate signals | [src/strategies/counter_trend_entry.py](src/strategies/counter_trend_entry.py) | `CounterTrendEntryGenerator.generate_signals()` |
| Risk / sizing | [src/risk/position_manager.py](src/risk/position_manager.py) | `RiskManager`, `PositionSizer` |
| Main bot | [src/trading_bot.py](src/trading_bot.py) | `TradingBot`, `Backtester` |
| Config | [config/config.yaml](config/config.yaml) | MT5 creds, symbols, risk params |

---

## Running the Bot

```bash
# Install dependencies
pip install -r requirements.txt

# Backtest mode
python -m src.trading_bot --mode backtest --symbol EURUSD --start 2023-01-01 --end 2024-12-31

# Live mode (requires MT5 config filled in)
python -m src.trading_bot --mode live --config config/config.yaml
```

---

## Important Notes

- **MT5 credentials** must be set in `config/config.yaml` before live trading (`mt5.account`, `mt5.password`, `mt5.server`)
- Strategy requires **5 timeframes** simultaneously: Weekly, Daily, 4H, 15M, 1M
- Counter-trend entries trade **against** the weekly bias — lower probability, smaller targets
- Pro-trend entries trade **with** the weekly bias — higher probability, extended targets
- Minimum RR required: **2.0** (configurable in config)
- Max concurrent trades: **3** (configurable)
- Risk per trade: **1-2%** of account (configurable)
- Special rule from PDF: when price is in a higher timeframe demand/supply, a 15M CoC alone (without waiting for 4H CoC) is sufficient to enter
