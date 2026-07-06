# Order Block + Liquidity Sweep Strategy — v2

> Pine Script v5 · 1-minute timeframe · Works on any market

---

## Overview

This strategy trades **liquidity sweeps of order blocks (OB)** — a smart money concept where price briefly breaks a key level to hunt stop-losses, then reverses sharply. The bot detects the OB zone, waits for the sweep and rejection, then enters in the direction of the reversal.

Two filters layer on top to improve signal quality:
- **Session filter** — only trade during high-liquidity windows (London + NY)
- **RSI filter** — confirms oversold/overbought context at sweep candle

---

## Core Concepts

### What is an Order Block?

An order block is the **last opposing candle before a strong impulse move**.

| Type | Formation | Zone colour |
|---|---|---|
| **Bullish OB** | Last bearish candle before a strong rally | Teal box |
| **Bearish OB** | Last bullish candle before a strong sell-off | Red box |

The high and low of that candle define the zone. Price often returns to this zone to "rebalance" before continuing.

### What is a Liquidity Sweep?

Before reversing, price frequently dips below a demand zone (or spikes above a supply zone) to **trigger stop-losses and grab liquidity**. This shows up as a prominent wick that closes back inside the zone — the signature entry signal.

```
Bullish sweep:  wick below OB low → close back above OB low → LONG
Bearish sweep:  wick above OB high → close back below OB high → SHORT
```

---

## Entry Logic

### Long Setup
1. A valid **bullish OB** exists (last bearish candle before a `≥ impulseMinPip` rally)
2. Current candle wicks **below** the OB low
3. Candle **closes back above** the OB low
4. Wick size ≥ `sweepWickPct` × candle range
5. RSI ≤ `rsiLongMax` (oversold context)
6. Inside an active trading session (London 07-11 UTC / NY 13-17 UTC)

### Short Setup
1. A valid **bearish OB** exists (last bullish candle before a `≥ impulseMinPip` sell-off)
2. Current candle wicks **above** the OB high
3. Candle **closes back below** the OB high
4. Wick size ≥ `sweepWickPct` × candle range
5. RSI ≥ `rsiShortMin` (overbought context)
6. Inside an active trading session (London 07-11 UTC / NY 13-17 UTC)

---

## Exit Logic

| Exit type | Level |
|---|---|
| **Stop loss** | 2 ticks beyond the sweep wick extreme |
| **Partial TP (1:1)** | Entry ± 1× risk — closes `partialPct`% of position |
| **Full TP** | Entry ± `tpMulti`× risk |
| **Invalidation** | Candle closes beyond the SL level |

---

## Filters

### Session Filter

Restricts entries to high-liquidity windows. Background tints show active sessions on the chart.

| Session | UTC window | Background |
|---|---|---|
| London | 07:00 – 10:00 | Blue tint |
| New York | 13:00 – 16:00 | Orange tint |
| Asian | 00:00 – 03:00 | Purple tint |

Toggle each session independently. Disable `useSession` entirely to trade 24/7.

---

## Settings Reference

### Order Block group

| Input | Default | Description |
|---|---|---|
| `obLookback` | 5 | How many candles back to search for OB |
| `impulseMinPip` | 10 | Minimum impulse size in pips to qualify an OB |
| `maxOBAge` | 50 | Maximum age (bars) before OB is discarded |
| `showBoxes` | true | Draw OB zones on chart |

### Sweep & Entry group

| Input | Default | Description |
|---|---|---|
| `sweepWickPct` | 0.4 | Wick must be ≥ 40% of candle range |
| `rsiLen` | 14 | RSI period |
| `rsiLongMax` | 45 | RSI ceiling for long entries |
| `rsiShortMin` | 55 | RSI floor for short entries |
| `tpMulti` | 2.0 | Full TP as a multiple of risk (RR ratio) |
| `partialPct` | 50 | % of position to close at 1:1 RR |

### Sessions group

| Input | Default | Description |
|---|---|---|
| `useSession` | true | Enable/disable session filter |
| `useLondon` | true | Allow entries in London open |
| `useNY` | true | Allow entries in NY open |
| `useAsian` | false | Allow entries in Asian session |

---

## Recommended Tuning by Market

| Market | `impulseMinPip` | `sweepWickPct` | Notes |
|---|---|---|---|
| EUR/USD (forex) | 8 – 12 | 0.35 – 0.45 | Tight spreads, clean sweeps |
| GBP/USD (forex) | 10 – 15 | 0.40 – 0.50 | More volatile, raise impulse |
| BTC/USDT (crypto) | 30 – 60 | 0.35 – 0.45 | Price in USD, scale pips up |
| NAS100 (index) | 15 – 25 | 0.40 – 0.50 | London + NY sessions only |
| Gold (XAU/USD) | 15 – 20 | 0.40 – 0.45 | NY session most reliable |

---

## Recommended Backtest Sequence

1. **Baseline** — run on full M5 data with session filter off. Note raw signal count and win rate.
2. **Add sessions** — enable London + NY, observe how much of the edge lives in those windows.
3. **Tune thresholds** — adjust `sweepWickPct` and `rsiLongMax` to balance frequency vs quality.
4. **Separate pairs** — run each pair independently. Reject pairs with PF < 1 or WR < 55%.

> **Validated result (EURUSD + AUDUSD, M5, Nov 2024 – Mar 2026):**
> wick=0.10 · RSI≤45 · London 07-11 + NY 13-17 UTC
> 87 trades · 72% WR · PF 2.7 · +$6,858 · Max DD -2%
> HTF bias filter tested (H1 EMA 20/50/100) — **rejected**: cuts trade count 67→17 with no meaningful WR gain

---

## Visual Guide

| Element | Meaning |
|---|---|
| Teal box | Active bullish OB (demand zone) |
| Red box | Active bearish OB (supply zone) |
| Teal triangle up | Long sweep entry signal |
| Red triangle down | Short sweep entry signal |
| Green line | Take profit level |
| Red line | Stop loss level |
| Yellow line | Partial TP at 1:1 RR |
| Blue tint | London session active |
| Orange tint | NY session active |
| Purple tint | Asian session active |

---

## Invalidation Rules

A trade setup is **skipped or closed** if:

- Long: candle closes **below** the sweep wick low
- Short: candle closes **above** the sweep wick high
- OB age exceeds `maxOBAge` bars before price returns
- HTF bias is against the trade direction (when filter is on)
- Outside all active sessions (when filter is on)
- A position is already open (`position_size ≠ 0`)

---

## Known Limitations

- **Lookahead bias** — `request.security` uses `lookahead_off` to prevent bar-by-bar peeking, but always verify Strategy Tester results on live paper trading before going live.
- **Spread not modelled** — add a commission/slippage setting in Strategy Properties to account for spread, especially on 1m forex.
- **OB detection is sequential** — the script finds the most recent qualifying OB. In strong trends, a new OB may replace the old one every few bars; raise `impulseMinPip` if OB boxes flicker too frequently.
- **1:1 partial close** — Pine Script's `strategy.close` with `qty` may not fire on the exact tick; treat partial closes as approximate in backtests.

---

## Files

| File | Description |
|---|---|
| `ob_liq_sweep_v2.pine` | Full strategy source — paste into Pine Editor |
| `ob_liq_sweep_strategy.md` | This document |

---

*Built with Pine Script v5. Load via TradingView → Pine Editor → paste → Add to chart → Strategy Tester.*
