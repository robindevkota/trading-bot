# Strategy Notes — MTF Structure Trading (cleaned & consolidated)

> Sources: personal notes from ~26 course videos ("Counter Trend Trading System.pdf")
> + Photon Trading "LQ Entry Models" (5 entry model diagrams).
> This file standardizes terminology, fixes garbled rules, and maps each concept
> to what is (and isn't) mechanized in this repo.

---

## 1. Terminology (standardized)

| Term | Meaning | Notes |
|------|---------|-------|
| **BoS** | Break of Structure — break **in the direction** of the current trend (continuation) | |
| **CHoCH / CoC** | Change of Character — **first** break **against** the current trend (reversal warning) | Your old notes used "CoC" for both; that was the main error |
| **POI** | Point of Interest — HTF supply/demand zone (Order Block) you plan to trade from | |
| **SLQ** | Structural liquidity — stops resting beyond a swing high/low | |
| **Inducement (IDM / PBID)** | A minor pullback swing near the POI that traps early entrants; their stops become the fuel for the move into the POI | |
| **PBL** | Pullback level/liquidity — entry happens where the PBL *fails* | |
| **Premium / Discount** | Upper / lower half of the active swing range; EQ = midpoint | Buy discount, sell premium |
| **RE / CE** | Risk entry (at the touch, no confirmation) vs Confirmation entry (wait for hold/CHoCH on LTF) | |
| **Displacement** | The break must be impulsive (large body), not a grind | Mechanized as `h4_ob_displacement_pips` + `body_ratio` |

**Fixed Rule 11 (was garbled):** When price is at the extreme of a **HTF** demand/supply
zone, you may drop straight to 15M confirmation without waiting for a 4H CHoCH
(the aggressive variant). Default remains: wait for the 4H shift.

**Fixed Rule 12 (was garbled):** A break only counts as CHoCH if the swing being
broken **achieved something** (made a new high/low of the leg). Breaks of minor
internal wiggles are not CHoCH — distinguish **swing structure** from **internal
structure**.

---

## 2. Top-down workflow (both systems)

```
Weekly  — macro bias + macro POI
Daily   — refine POI (flip zone? structure? sweep? inducement?)  [only AFTER price reacts]
4H      — structure shift (CHoCH for counter-trend, BoS for pro-trend) + 4H POI
15M     — DOUBLE swing BoS confirmation → mark 15M POI in discount/premium
1M      — micro CHoCH + sharp V-reaction at the 15M POI → entry
```

Key principles kept from the course:
- Refine zones only after price reacts to the higher-TF zone.
- One BoS can be a liquidity grab; **two** is a trend shift.
- Counter-trend: target the old HH, exit at target. Pro-trend: scale out, trail, extend.
- If weekly structure breaks against the position, exit immediately.

---

## 3. Photon LQ Entry Models (liquidity taxonomy)

The "what counts as inducement" catalog that the original notes were missing:

| Model | Name | Setup |
|-------|------|-------|
| **EM 1** | HTF Leg Inducement | No inducement built near price → expect sweep of the SLQ on the HTF leg itself, into the POI |
| **EM 2a** | Fake Break | A break that exists only to trap early buyers/sellers (the break **is** the inducement); SLQ builds beyond a "strong" low/high, gets swept → enter |
| **EM 2b** | Equal Lows/Highs | Equal lows/highs are the trap; engineered SLQ swept → enter |
| **EM 3a/3b** | Flip Inducement (RE/CE) | A flip zone that didn't sweep liquidity acts as inducement for the true extreme; RE = enter before PBL fails (riskier), CE = wait for hold |
| **EM 4** | HTF Leg/POI Sweep | The only liquidity left **is** the HTF structural low/high; its sweep is the trigger |
| **EM 5** | Sweep Flip | Sweep, then trade the flip zone that forms after |

Common thread: **know where the trapped traders are; enter when their stops are
consumed into your HTF POI and the pullback level fails.**

---

## 4. What is mechanized in this repo (v3)

| Concept | Code | Config flag | Status |
|---------|------|-------------|--------|
| Daily bias (EMA21/50 + RSI) | `_daily_bias` | — | LIVE (locked) |
| 4H OB zones w/ displacement + mitigation | `_find_4h_ob_zones` | — | LIVE (locked) |
| 15M CHoCH (+2) / BOS (+1) / Sweep (+2) / EMA20 (+1) / Flip (+1) | `_try_*` models | `use_choch` etc. | LIVE (locked) |
| **EM 4 — HTF POI sweep (+2)** | `_try_htf_sweep` | `use_htf_sweep` | v3, default OFF |
| **EM 2b — Equal lows/highs bonus (+1)** | `_is_equal_level` | `use_eql_bonus` | v3, default OFF |
| **EM 2a — Inducement (PBID) filter** | `_inducement_swept` | `require_inducement` | v3, default OFF |
| 1H intermediate OB (presence label) | `_find_1h_ob_zones` | `use_h1_ob` | built, OFF (worsened RR) |

Not mechanized (still discretionary / future work): weekly-TF layer, fake-break
(EM 2a as a *trigger*), flip-inducement (EM 3), sweep-flip (EM 5), 1M micro
confirmation, FVG/imbalance, session killzones for this bot (ob_sweep bot has them).

---

## 5. Evidence so far (small samples — treat as indicative)

- Pro-trend LONG mechanization: 2023–24 = 14 trades, ~58% WR, +$19.9k. 2026 YTD = 0W/2L.
- Counter-trend needed `ct_min_score: 4` to stop bleeding; SHORT side lost outright → disabled.
- ob_sweep bot (= EM 1/EM 4 mechanized on M5, London/NY sessions) is the best 2026 performer: +$1,167, DD < 2%.
- v3 A/B results: see `RESULTS-V3.md` (generated from the 27-run suite).

## 6. Re-watch checklist (gaps in original notes)

- [ ] Order block drawing rules — which candle, body vs wick, mitigation definition
- [ ] Fair Value Gaps / imbalance — completely absent from notes
- [ ] Liquidity taxonomy — now covered by Photon EM 1–5 (section 3)
- [ ] Displacement definition ("achievement")
- [ ] Internal vs swing structure (source of the Rule 12 confusion)
- [ ] Session timing / killzones
- [ ] Risk model: %-risk per trade, minimum R:R for counter-trend
