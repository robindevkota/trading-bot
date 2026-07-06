# Research Findings Ledger

Running log of validated discoveries. Every entry must have survived
in-sample → out-of-sample validation before it gets a number. This file only
grows — when a finding is later disproven, it gets struck through with a note,
never deleted. Goal: accumulate until the pieces compose the master strategy.

**Direction (set 2026-07-06):** MTF PRO is retired from active development.
Focus = research-first. Requirements for the master strategy: good trade
frequency (≥ ~10/month), high win rate (≥ 65%), positive net expectancy after
real costs, validated OOS on multiple symbols.

---

## F1. Continuation beats reversal on crypto 15m — everywhere
Every classic reversal signal (hammer at lows, SMC sweep+reclaim, capitulation
volume, shooting star) predicts CONTINUATION on BTC/ETH 15m. The crowd's
reversal playbook is the fuel: their stops feed the move.
Evidence: 455k candles 2020–2026, IS z>2 + OOS lift>1.1 on both symbols.

## F2. Forex M15 is the OPPOSITE regime — mean reversion
Big red bars bounce (EURUSD OOS lift 1.44), squeeze breakouts fade on all 4
majors, capitulation bounces. Bank flow fades retail breakouts. Crypto and FX
need opposite trade directions — no single intraday rule serves both.

## F3. Momentum Ignition = best validated tradable pattern so far
One 15m bar ≥2.5×ATR up + ATR%>0.25 → long, TP 2×ATR, SL 4×ATR, ≤16h hold.
OOS 2024–2026 net of futures fees: BTC 73.1% WR +0.135%/trade (175 trades),
ETH 70.4% WR +0.093%/trade (233 trades). ~14 trades/month on 2 symbols.
Status: candidate core of master strategy. Next: verify SOL/BNB, full
equity-curve backtest with sizing.

## F4. Fees decide life or death at intraday scale
Same patterns, same win rates: spot taker fees (0.20% RT) = always negative;
futures maker/taker (0.055% RT) = positive. Target must be ≥2×ATR with
ATR%>0.25 so costs stay <15% of target. Spot market orders are banned.

## F5. FX intraday edges are real but economically dead
Fade patterns win 65–70% OOS but breakeven at 1:2 RR is 66.7% and M15 targets
(6–10 pips) vs 1.2 pips cost → net ≈ 0 on every pair. H1: effect fades
(50–62% WR). FX only works at 4H+ scale where targets dwarf spread.

## F6. Sessions are volatility timers, not direction signals
London/NY opens: hit rates elevated for BOTH directions (z up to 21). Use as
a "when to expect moves" filter, never as a direction signal.

## F7. Win rate is a design dial, not an edge
TP:SL ratio sets the win rate mechanically (2:1→~33% base, 1:2→~67% base).
Only WR minus profile-breakeven, minus costs, is real edge. Any pattern must
be judged on that margin (crypto ignition: +4–7pp above breakeven net; FX
fades: ~0pp).

## F8. OOS validation kills ~half of "great" patterns
consec_up_5 and US-close-hour longs: z>3 in 2020–2023, negative 2024–2026.
Cross-asset trend (VDT): all 36 param combos profitable IS, Sharpe decayed
0.83→0.60 OOS (survived). Rule stands: nothing enters this ledger without an
untouched OOS window.

## F9. Cross-asset daily trend works but is slow
VDT (research/): portfolio Sharpe 0.83, +8.9%/yr at 1% risk, 20/22 positive
years, corr to SPY +0.04 — but ~52 trades/yr and 42% WR. Shelved as
not matching the frequency/WR brief; keep as diversifier candidate.

## F10. Ignition generalizes: 6/6 crypto symbols profitable OOS — with a regime warning
Same locked rules on SOL/BNB/XRP/DOGE (never used in design): all positive
2024–2026 (WR 65–71%, net +0.05% to +0.11%/trade). BUT XRP and DOGE LOST in
the 2020–21 meme mania (DOGE 58% WR, −0.39%/trade) — in vertical manias a
giant green bar is often the top, not ignition. Basket OOS: ~1,038 trades /
2.5y ≈ 35/month on 6 symbols. Guard idea (untested): skip when 24h return
already > some extreme.

## F11. Fuel confirmation works: ignition + crowd-short = the best trade found so far
Binance positioning data (OI, L/S ratios, funding; 5m since 2021-12).
`big_bar_up + global long/short ratio in bottom quartile` (crowd is short →
squeeze fuel loaded) improved ignition on BOTH symbols BOTH periods:
BTC OOS 72.6% WR +0.107%/trade; ETH OOS 74.5% WR +0.155%/trade
(ETH IS: 74.6% WR +0.433%). ~5 trades/month on 2 symbols — a high-conviction
tier on top of F3, sized larger, not a replacement.

## F12. Fuel dead ends (tested, buried)
- Funding-conditioned ignition: inconsistent across symbols (both extremes
  "helped" on BTC, hurt on ETH) — noise, rejected.
- H2 coil-break + OI build: too few OOS samples / negative — rejected.
- H3 OI-dump timing: ignition almost never coincides with OI dumps (n<10) —
  unmeasurable at this scale.
- H4 standalone crowd fades (L/S or funding extremes alone): all profitable
  IS, all dead OOS. Positioning extremes persist for days — they are context,
  not signals.

## F13. FX intraday death confirmed on 8/8 pairs
The 4 held-out pairs (NZDUSD, USDCHF, USDCAD, EURJPY) replicated F5 exactly:
WR 63–77%, net −0.045% to +0.021% ≈ zero. Closed permanently.

---

*Next research queue:*
- Verify F3 on SOL, BNB, XRP, DOGE (does ignition generalize?)
- Ignition variants: 2-bar ignition, ignition + squeeze context, short side
  in downtrends (big_bar_dn + downtrend was +EV on ETH)
- Time-of-day interaction with F3 (does ignition during US hours do better?)
- 5m version of F3 with maker-only execution
- Capitulation+range-low short (worked ETH, failed BTC — need tiebreaker data)
