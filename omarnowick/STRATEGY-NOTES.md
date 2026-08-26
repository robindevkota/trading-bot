# Omarnowick — No-Wick Candle Strategy

> Working notes from discussion. Three setups, same core no-wick-candle
> trigger, different structural context before entry is allowed — different
> expected win rates:
> - **A** — trend continuation (no-wick appears mid-trend, after 2 BOS in
>   the same direction — no reversal involved)
> - **B** — confirmed reversal (CHoCH + confirming BOS)
> - **C** — early reversal (CHoCH only, no confirming BOS yet)
>
> This file is revised as the discussion continues.

---

## Timeframe

**M15** — all logic (structure, no-wick candle, retest) runs on the 15-minute chart.

---

## Fixed R:R

**1:1** — TP distance = SL distance, always.

---

## Market Structure — Swing Detection

Swing points detected via **2-bar fractal**: a swing high = a candle whose
high is greater than the 2 candles immediately before AND after it (mirror
for swing low). Standard, deterministic pivot method.

- **Uptrend** = sequence of **HH** (Higher High) + **HL** (Higher Low)
- **Downtrend** = sequence of **LH** (Lower High) + **LL** (Lower Low)
- **CHoCH** (Change of Character) = price breaks the most recent HL in an
  uptrend (first sign of reversal), or breaks the most recent LH in a
  downtrend (mirrored)
- **BOS** (Break of Structure) = a confirming break in the new direction
  *after* CHoCH — e.g. after CHoCH breaks the HL, a new LH forms, then price
  breaks below that LH too → BOS confirms the new downtrend is established

Setup A trades **continuation** — the no-wick candle appears mid-trend,
with no CHoCH/reversal involved at all. Setups B and C trade the
**reversal itself** — the transition out of an established trend. The
difference between B and C is *how much* reversal confirmation exists
before the no-wick candle is trusted.

---

## Core Signal — "No-Wick Candle"

**Bullish no-wick (for longs):**
- `open == low` (candle opens exactly at its low — no bottom wick at all)
- Top wick is irrelevant / allowed

**Bearish no-wick (for shorts, mirrored):**
- `open == high` (candle opens exactly at its high — no top wick at all)
- Bottom wick is irrelevant / allowed

This is the core trigger all three setups are built around.

---

## Setup A — Trend Continuation (2 BOS, same direction, no reversal)

The no-wick candle appears **mid-trend**, once the trend has proven itself
with two consecutive same-direction structure breaks. No CHoCH, no
reversal — this is trading strength within an established trend, not a
turning point.

**Uptrend (for longs):**
1. BOS #1 = price breaks above the prior swing high → HH #1 (sequence so
   far: HL → HH)
2. A new higher low forms → HL #1
3. BOS #2 = price breaks above that HH again → HH #2 — full confirmed
   sequence: **HL → HH → HL → HH**
4. A bullish no-wick candle forms (`open == low`) — only counts once BOS #2
   has occurred
5. **Entry**: at the open price of the no-wick candle, filled if price
   retests that level within 10 candles (else invalidated)
6. **Stop Loss**: at the **nearest HL**, minus a fixed pip buffer
7. **Take Profit**: fixed 1:1 R:R

**Downtrend (for shorts, mirrored):**
1. BOS #1 = price breaks below the prior swing low → LL #1 (sequence so
   far: LH → LL)
2. A new lower high forms → LH #1
3. BOS #2 = price breaks below that LL again → LL #2 — full confirmed
   sequence: **LH → LL → LH → LL**
4. A bearish no-wick candle forms (`open == high`) — only counts once BOS #2
   has occurred
5. **Entry**: at the open price of the no-wick candle, filled if price
   retests that level within 10 candles (else invalidated)
6. **Stop Loss**: at the **nearest LH**, plus a fixed pip buffer
7. **Take Profit**: fixed 1:1 R:R

No-wick candles appearing **before** the 2nd same-direction BOS are ignored
— trend isn't proven strong enough yet.

---

## Setup B — Confirmed Reversal (post-BOS)

Example: uptrend (HL→HH) → price breaks the HL → **CHoCH** → a fresh **LH**
forms → price breaks below that LH too → **BOS confirms new downtrend** →
**then** a bearish no-wick candle appears → trade it.

1. Uptrend structure: HL → HH established
2. CHoCH: price breaks below the most recent HL
3. A new LH forms after the CHoCH
4. BOS: price breaks below that LH, confirming the downtrend
5. A bearish no-wick candle forms (`open == high`) — **only counts after step 4**
6. **Entry**: at the open price of the no-wick candle, filled if price
   retests that level within 10 candles (else invalidated)
7. **Stop Loss**: at the nearest confirmed **LH** (the one that caused the
   BOS in step 4), plus a fixed pip buffer
8. **Take Profit**: fixed 1:1 R:R

More confirmation → later entry, presumably higher win rate, smaller reward
per the same risk (since SL sits closer, at the LH that already broke).

*(Long/mirrored version: downtrend → CHoCH breaks LH → new HL forms → BOS
breaks that HL → bullish no-wick candle → entry, SL at nearest HL.)*

---

## Setup C — Early Reversal (post-CHoCH only)

Example: uptrend (HL→HH→HL→BOS→HH) → price reverses from the final HH,
breaks below the most recent HL → **CHoCH** → a bearish no-wick candle
appears **immediately**, before any LH has even formed → trade it.

1. Uptrend structure established (includes at least one prior BOS while
   still in the uptrend — see chart example: HL→HH→HL→BOS→HH)
2. CHoCH: price breaks below the most recent HL
3. A bearish no-wick candle forms (`open == high`) **right at/after the
   CHoCH — no LH exists yet, no downtrend BOS has happened yet**
4. **Entry**: at the open price of the no-wick candle, filled if price
   retests that level within 10 candles (else invalidated)
5. **Stop Loss**: at the **HH** that the move originated from (since no LH
   exists yet to anchor to), plus a fixed pip buffer
6. **Take Profit**: fixed 1:1 R:R

Less confirmation → earlier entry, presumably lower win rate, but the SL
(anchored to the HH) is farther away than Setup B's tighter LH-based SL —
different risk profile.

*(Long/mirrored version: downtrend → CHoCH breaks LH → bullish no-wick
candle appears immediately, before any HL has formed → entry, SL at the
**LL** the move originated from.)*

---

## Setup Validity — Retest Rule (A, B, and C)

1. A qualifying no-wick candle forms (per the structural conditions of
   Setup A, Setup B, or Setup C above).
2. Price must come back and **touch the entry level** (see Early Entry
   Adjustment below) within the next **10 candles**.
3. If price does not return to that level within 10 candles → the no-wick
   candle is **invalidated** (no trade taken).

---

## Invalidation Conditions (checked every bar while waiting for retest)

While a no-wick candle is pending retest (within its 10-candle window), the
setup is voided — no trade taken — if **any** of the following occur before
the entry level is actually touched:

**1. TP would already be hit without ever tapping entry.**
If price reaches what would have been the TP level (projected from the
no-wick candle's own entry/SL/TP) before ever touching the entry price
itself, the trade never fires — skip it. (Price ran straight to target
without the pullback that would have filled the order.)

**2. Internal structure forms a CHoCH against the trade direction before
the retest taps entry.**
If, while waiting for the pullback to entry, price prints a **new swing
point sequence that breaks the most recent opposing swing** — i.e. a CHoCH
against the trade's direction — the setup is invalidated immediately, even
if price *later* drops/rises back into the entry zone anyway.

Example (long setup): bullish no-wick candle forms → before price retests
back down to the no-wick open, price makes a swing high then reverses and
breaks below the most recent swing low → that break is a bearish CHoCH →
original long setup is void, do not take it even if price subsequently
wicks into the entry zone.

Mirrored for shorts: bearish no-wick candle forms → before retest, price
makes a swing low then breaks back above the most recent swing high →
bullish CHoCH → original short setup is void.

**Rationale**: an opposing CHoCH appearing *before* the retest completes
means the market character has already changed while waiting — the setup's
premise (continuing/reversing in the original direction) is no longer
supported, regardless of whether price geometrically wanders back through
the entry price afterward.

---

## No-Trade Filter — Consolidation / Sideways Market

No setups (A, B, or C) are valid while the market is **consolidating** —
defined structurally, using the same swing-point sequence already tracked
elsewhere in this strategy (no separate indicator):

- A trending market shows a **clean directional sequence** of swings:
  HH→HL→HH→HL (up) or LH→LL→LH→LL (down).
- **Consolidation** = the swing sequence is **choppy/mixed** — swings
  alternate without a clean directional read (e.g. a new high forms but the
  following low is *lower* than the prior low while the trend context calls
  for higher lows, or vice versa) — i.e. structure fails to confirm either
  a clean uptrend or downtrend.
- While in this state, no-wick candles are ignored entirely for all three
  setups, regardless of how they otherwise qualify.

*(Exact chop-detection rule to be finalized in code — likely: if the most
recent swing high/low breaks the directional pattern required by the
setup's own trend definition, treat structure as unconfirmed/sideways
until a fresh clean HH/HL or LH/LL sequence re-establishes itself.)*

---

## Early Entry Adjustment (large-SL rule)

Entry is not always exactly at the no-wick candle's open price. If the
resulting **SL distance would be greater than 10 pips**, the entry level is
moved **2 pips earlier** (i.e. price only needs to come within 2 pips of
the no-wick open to trigger the fill, rather than touching the open
exactly) — this front-runs the retest slightly rather than waiting for the
full touch.

- **Long**: entry = no-wick open **+ 2 pips** (fills sooner, on the way down)
- **Short**: entry = no-wick open **− 2 pips** (fills sooner, on the way up)
- SL and TP still computed from the *adjusted* entry price (TP stays 1:1 R:R
  off the new entry, not the original no-wick open)
- Only applies when SL distance > 10 pips; if SL distance ≤ 10 pips, entry
  stays exactly at the no-wick open as normal

---

## Stop Loss Buffer

SL sits beyond the reference swing point (HL/LH for Setup A, LH/HL for
Setup B, HH/LL for Setup C) by a **fixed 5 pip buffer** — gives room for a
liquidity sweep through the swing point without stopping the trade out
prematurely. Starting value for all symbols; will tune per-symbol after
first backtest results (same approach used in ob_sweep/scalping).

---

## Session Filter — No-Trade Windows

No trades taken during:
- **Early Asia** — first 2 hours of the Asian session: **00:00–02:00 UTC**
- **Late New York** — last 2 hours of the New York session: **20:00–22:00 UTC**

*(Standard session convention assumed: Asia opens 00:00 UTC, NY runs to
~22:00 UTC. Flag if your broker's session boundaries differ — hours would
need adjusting.)*

---

## No-Trade Filter — News Blackout (DEFERRED — not enforced in initial testing)

> Documented for later; not implemented as a hard rule in the first backtest
> pass. Requires an economic calendar data source not yet wired into any
> strategy in this project — adding it now would block testing on a
> dependency we haven't sourced. Revisit once core setups (A/B/C) are
> validated on price action alone.

No trades taken for **high-impact news events** (per symbol's relevant
currencies):

- **Blackout starts 1 hour before** the scheduled release
- **Trading resumes 15 minutes after** the release

Applies to any pending setup (A, B, or C) and to new signal detection alike
— if the blackout window opens while a no-wick candle is pending retest,
that pending setup is invalidated the same as any other no-trade-window
overlap.

*(Requires an economic calendar data source for scheduled release times —
to be sourced/wired in during coding. Not yet decided: which impact tier
counts as "high-impact" — e.g. NFP/CPI/FOMC/rate decisions per the
scalping strategy's convention — and which currencies are relevant per
symbol, e.g. CADJPY watches both CAD and JPY releases.)*

---

## Trade Management — Close-and-Flip on Opposing Reversal

While a trade is open, if a **valid opposing Setup B signal** forms (CHoCH
+ confirming BOS **against the current trade's direction**, followed by a
no-wick candle in the new direction), the open trade is **closed
immediately — regardless of current profit or loss** — overriding its own
SL/TP. The new opposing signal is then taken as a fresh trade in the new
direction, following its normal entry/retest/SL/TP rules like any other
Setup B signal.

**Scope — what does NOT trigger this:**
- A no-wick candle **in the same direction** as the current trend/trade
  (e.g. Setup A continuation) — no action, current trade runs normally.
- A no-wick candle **against** the trade's direction that is **not** backed
  by a genuine CHoCH+BOS reversal against you (i.e. not a valid Setup B) —
  ignored, does not trigger a flip. Structure has to have actually turned
  against the position, not just any opposing-colored no-wick candle
  appearing.

**Example (from chart):** long trade open from a bullish no-wick entry →
price reverses, breaks the last swing low (CHoCH) → breaks again (BOS,
downtrend confirmed) → a bearish no-wick candle forms → close the long
now, take the new short.

---

## Target Symbols

- CADJPY
- USDCHF
- USDJPY
- AUDUSD
- GBPUSD

---

## Status

All three setups (A, B, C) to be coded and backtested separately, then
compared. Setup A is the original "2-BOS confirmed trend" continuation
logic from the first draft of this document. Setups B and C were added
after reviewing hand-drawn chart examples ("Perfect Example of A setup" /
"Perfect Example of C setup" — note: those chart labels correspond to
Setup B and Setup C respectively in this doc's final naming) from reference
video material, which showed this is fundamentally a reversal-trading
concept as well, not purely continuation. A is kept as a distinct case
since it targets a structurally different market condition (trend
strength, not reversal timing).
