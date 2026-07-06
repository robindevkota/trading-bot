# OB + Liquidity Sweep — When to Run (Nepali Time / NPT)

> Nepal Standard Time = UTC + 5:45

---

## Trading Sessions (NPT)

| Session       | UTC           | Nepal Time (NPT)        |
|---------------|---------------|-------------------------|
| London Open   | 07:00 – 11:00 | **12:45 PM – 4:45 PM**  |
| New York Open | 13:00 – 17:00 | **6:45 PM – 10:45 PM**  |

The bot only takes signals during these two windows.
Outside these hours it does nothing — you can leave it running 24/7.

---

## Recommended Daily Schedule (NPT)

```
12:30 PM  — Start the bot (15 min before London open)
             MT5 terminal must be open and logged in

12:45 PM  — London session starts  ← bot active, watching for sweeps
 4:45 PM  — London session ends

 6:30 PM  — (bot already running, re-check MT5 is still connected)
 6:45 PM  — New York session starts  ← bot active again
10:45 PM  — New York session ends

10:45 PM  — Safe to stop if you want, or leave running overnight
             (bot will just skip bars outside session anyway)
```

---

## How to Start

```bash
# 1. Open MT5 terminal and log in first

# 2. Open terminal in the project folder
cd "c:\Users\user\Desktop\trading bot h"

# 3. Run live trader (real orders)
python ob_sweep/live_trader.py

# 4. Run in dry-run mode (signals only, no orders) — set DRY_RUN = True in live_trader.py first
python ob_sweep/live_trader.py
```

---

## What the Bot Does Each Bar (every 5 minutes)

```
Every 5 min:
  └─ Is there an open position?
       YES → Check if price hit TP1 (1:1 RR)
               → If yes: close 50%, move SL to breakeven
               → Remaining 50% runs to TP2 (2:1) or stops at BE
       NO  → Are we in a session? (12:45-4:45 PM or 6:45-10:45 PM NPT)
               → YES: scan for OB sweep signal
                        → Signal found: place LONG order
               → NO:  sleep until next bar
```

---

## Locked Results (M5, Nov 2024 – Mar 2026)

| Pair   | Trades | Win Rate | Profit Factor | Net PnL  | Max DD |
|--------|--------|----------|---------------|----------|--------|
| EURUSD | 67     | 71.6%    | 2.64          | +$5,140  | -2.0%  |
| AUDUSD | 20     | 75.0%    | 2.83          | +$1,429  | -2.0%  |
| **Combined** | **87** | **~72%** | **2.7** | **+$6,569** | **-2.0%** |

M15 validation (Mar 2022 – Mar 2026) confirms edge is real across 4 years.

---

## Validation Backtest Commands

```bash
# M5 — locked live params (16 months)
python ob_sweep/backtest.py --tf m5 --start 2024-11-13 --end 2026-03-20

# M15 — concept validation (4 years)
python ob_sweep/backtest.py --tf m15 --start 2022-03-11 --end 2026-03-20
```
