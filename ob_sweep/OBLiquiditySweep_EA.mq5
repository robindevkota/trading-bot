//+------------------------------------------------------------------+
//| OBLiquiditySweep_EA.mq5                                          |
//| Cross-validation port of ob_sweep/backtest.py (Python engine)    |
//| Logic source: ob_sweep/config/strategy.yaml (m5 block, LOCKED)   |
//|                                                                    |
//| Concept   : Detect bearish Order Block -> price sweeps its low   |
//|             with a wick but closes back above -> enter LONG,     |
//|             SL below wick, partial at 1:1 (move SL to BE),       |
//|             full TP at tp_rr (2:1).                              |
//| Exec TF   : M5   (locked params; long-only, no short-side logic  |
//|             exists in the m5 config or in run_backtest())        |
//| Sessions (broker time must be UTC, or adjust InpBrokerUtcOffset):|
//|   London 07-11 UTC | New York 13-17 UTC                          |
//|                                                                    |
//| Load in Strategy Tester on EURUSD (or AUDUSD), M5, same date     |
//| range as the Python run (Nov 2024 - Mar 2026), to cross-check    |
//| win rate / PF / net P&L independently against the locked result: |
//| EURUSD 67 trades, 71.6% WR, PF 2.64.                              |
//+------------------------------------------------------------------+
#property copyright "cross-validation build"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//----------------------------------------------------------------------
// Inputs mirror ob_sweep/config/strategy.yaml exactly (m5 block)
//----------------------------------------------------------------------
input int    InpObLookback      = 10;      // ob_lookback
input double InpImpulseMinPips  = 5.0;     // impulse_min_pips
input int    InpObMaxAge        = 50;      // ob_max_age (~4h on M5)
input double InpWickRatioMin    = 0.10;    // wick_ratio_min
input int    InpRsiLength       = 14;      // rsi_length
input double InpRsiMaxLong      = 45.0;    // rsi_max_long
input double InpTpRR            = 2.0;     // tp_rr
input double InpPartialPct      = 50.0;    // partial_pct
input double InpRiskPct         = 0.01;    // risk_pct
input double InpAccountBalance  = 10000;   // account_balance (lot sizing reference only; live sizing uses current balance)

// Sessions (UTC hours) — London 07-11, New York 13-17 (strategy.yaml "sessions")
input int InpSession1Start = 7;
input int InpSession1End   = 11;
input int InpSession2Start = 13;
input int InpSession2End   = 17;

// If your broker server time is NOT UTC, set the offset here (broker_hour - UTC_hour)
input int InpBrokerUtcOffsetHours = 0;

input ulong InpMagic = 20260920;

//----------------------------------------------------------------------
// Handles / state
//----------------------------------------------------------------------
int rsiHandleM5;

datetime g_lastBarTime = 0;

// Partial-management state for the position currently open (this EA/magic only).
// Needed because MT5 has no native "partial close + move SL to BE" primitive —
// backtest.py tracks this in the `pos` dict; here it is tracked per-ticket.
ulong  g_posTicket      = 0;
bool   g_partialDone    = false;
double g_entryPrice     = 0.0;
double g_tp1Price       = 0.0;
double g_tp2Price       = 0.0;
double g_origSl         = 0.0;

//+------------------------------------------------------------------+
int OnInit()
  {
   rsiHandleM5 = iRSI(_Symbol, PERIOD_M5, InpRsiLength, PRICE_CLOSE);

   if(rsiHandleM5 == INVALID_HANDLE)
     {
      Print("Indicator handle creation failed");
      return(INIT_FAILED);
     }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);

   ResetPartialState();

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(rsiHandleM5);
  }

//+------------------------------------------------------------------+
void ResetPartialState()
  {
   g_posTicket   = 0;
   g_partialDone = false;
   g_entryPrice  = 0.0;
   g_tp1Price    = 0.0;
   g_tp2Price    = 0.0;
   g_origSl      = 0.0;
  }

//+------------------------------------------------------------------+
bool InSession(datetime t)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   int h = (dt.hour - InpBrokerUtcOffsetHours + 24) % 24;
   if(h >= InpSession1Start && h < InpSession1End) return true;
   if(h >= InpSession2Start && h < InpSession2End) return true;
   return false;
  }

//+------------------------------------------------------------------+
// Have we managed to find and hold this position already (this run)?
//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   if(!PositionSelect(_Symbol)) return false;
   if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagic) return false;
   return true;
  }

//+------------------------------------------------------------------+
// Manage an open position: partial TP at 1:1 (move remaining SL to
// breakeven), full TP at tp_rr. Mirrors backtest.py run_backtest()'s
// "Manage open position" block (SL is handled natively by the broker
// via the position's stored SL, so only partial-TP/BE/TP2 logic is
// done here explicitly).
//+------------------------------------------------------------------+
void ManagePosition()
  {
   if(!PositionSelect(_Symbol)) { ResetPartialState(); return; }
   if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagic) return;

   ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   if(ticket != g_posTicket)
     {
      // Position changed under us (shouldn't normally happen) — resync minimal state.
      g_posTicket  = ticket;
      g_entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      g_origSl     = PositionGetDouble(POSITION_SL);
     }

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Partial TP at 1:1 (if not yet taken) — backtest.py: hi >= pos["tp1"]
   if(!g_partialDone && bid >= g_tp1Price && g_tp1Price > 0)
     {
      double volume    = PositionGetDouble(POSITION_VOLUME);
      double closeVol  = NormalizeVolume(volume * (InpPartialPct / 100.0));
      if(closeVol > 0 && closeVol < volume)
        {
         if(trade.PositionClosePartial(_Symbol, closeVol))
           {
            g_partialDone = true;
            // Move remaining stop to breakeven — backtest.py: pos["sl"] = pos["entry"]
            double tp = PositionGetDouble(POSITION_TP);
            trade.PositionModify(_Symbol, g_entryPrice, tp);
           }
        }
     }
   // Full TP at tp_rr for the remainder is left to the broker-side TP order
   // (set at entry to tp2) — no extra action needed here; it fills natively.
  }

//+------------------------------------------------------------------+
double NormalizeVolume(double vol)
  {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(step <= 0) step = 0.01;
   vol = MathFloor(vol / step) * step;
   if(vol < minLot) vol = 0.0; // cannot partial-close below broker minimum
   return vol;
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   // Process once per new M5 bar close, evaluating the just-closed bar
   // as the signal bar (mirrors backtest.py's bar-by-bar loop, where
   // `row` is always a fully-formed historical bar). In live/tester
   // terms this means: on the first tick of a NEW bar, look back at the
   // bar that just closed (shift 1) as "row", and bars further back
   // (shift 2..11) as the OB-search window. Any entry decided from that
   // closed bar is submitted immediately (this tick == the open of the
   // new bar), which is the standard "act on bar close" live-EA
   // translation of backtest.py's post-hoc closed-bar analysis.
   datetime curBarTime = iTime(_Symbol, PERIOD_M5, 0);
   bool isNewBar = (curBarTime != g_lastBarTime);
   if(isNewBar)
      g_lastBarTime = curBarTime;

   // Manage any open position on every tick (SL/TP1/TP2), not just on bar close —
   // backtest.py checks lo/hi against sl/tp1/tp2 every bar, and intrabar touches
   // matter for partial/BE behaviour; per-tick management is at least as accurate.
   if(HasOpenPosition())
     {
      ManagePosition();
      return; // never look for a new signal while in a trade (matches backtest.py's `continue`)
     }
   else
      ResetPartialState();

   // Only evaluate a NEW signal once per closed bar.
   if(!isNewBar) return;

   // Need enough closed bars for OB lookback + RSI warmup, same spirit as
   // backtest.py's `warmup = OB_LOOKBACK + RSI_LEN + 5` (Strategy Tester
   // simply won't have bars before its start date, so this is a soft guard).
   int bars_needed = InpObLookback + InpRsiLength + 5 + 2;
   if(Bars(_Symbol, PERIOD_M5) < bars_needed) return;

   // shift 1 = the just-closed bar ("row" in backtest.py)
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int need = InpObLookback + 2; // row (shift1) + OB window (shift2..shift(lookback+1))
   if(CopyRates(_Symbol, PERIOD_M5, 1, need, rates) < need) return;

   MqlRates row = rates[0]; // just-closed bar

   // ── Session gate first ─────────────────────────────────────────
   if(!InSession(row.time)) return;

   // ── RSI gate next: RSI(14) on the CURRENT closed bar must be <= 45 ──
   double rsiBuf[];
   ArraySetAsSeries(rsiBuf, true);
   if(CopyBuffer(rsiHandleM5, 0, 1, 1, rsiBuf) <= 0) return; // RSI at shift 1 = row
   double rsiVal = rsiBuf[0];
   if(rsiVal > InpRsiMaxLong) return; // NaN comparisons are false in MQL5 too; treat as skip like backtest.py's np.isnan check
   if(rsiVal != rsiVal) return;       // explicit NaN guard (self-inequality), belt-and-braces

   double pip = 0.0001; // PIP constant in backtest.py — 1 pip = 0.0001 for 4/5-digit pairs
   double point = _Point;

   // ── Order block search: walk backward j = 1..ob_lookback, FIRST match wins ──
   // backtest.py: for j in range(1, OB_LOOKBACK+1): prev = bars.iloc[i-j]
   // rates[] here is series-ordered with rates[0]=row(shift1), so prev at
   // "j bars before row" is rates[j] (shift 1+j).
   double ob_high = 0.0, ob_low = 0.0;
   int    ob_age  = 0;
   bool   ob_found = false;

   for(int j = 1; j <= InpObLookback; j++)
     {
      if(j >= need) break; // safety, should not happen given `need` sizing
      MqlRates prevc = rates[j];
      if(prevc.close < prevc.open) // bearish candle
        {
         double impulse_pips = (row.close - prevc.low) / pip;
         if(impulse_pips >= InpImpulseMinPips)
           {
            ob_high  = prevc.high;
            ob_low   = prevc.low;
            ob_age   = j;
            ob_found = true;
            break; // stop at first (nearest) match — do NOT scan the whole window
           }
        }
     }

   if(!ob_found) return;
   if(ob_age > InpObMaxAge) return; // ~always false given ob_lookback caps ob_age; ported for fidelity

   // ── Sweep check: wick below OB low, closes back above it ──
   if(!(row.low < ob_low && row.close > ob_low)) return;

   // ── Wick ratio check ──
   double wick_size    = ob_low - row.low;
   double candle_range = row.high - row.low;
   double wick_ratio   = (candle_range > 0.0) ? (wick_size / candle_range) : 0.0;
   if(wick_ratio < InpWickRatioMin) return;

   // ── Entry ──
   // backtest.py enters at `row["close"]` (the signal bar's close), evaluated
   // post-hoc on a fully-formed historical bar. Live/tester translation: this
   // code runs on the FIRST TICK of the bar immediately after `row`, i.e. at
   // the moment `row` has just closed — so the current market price is, by
   // construction, at/essentially-at row.close (no time has passed for price
   // to move away from the close print). We therefore enter at the current
   // market ASK, which is the live-EA equivalent of "enter at the signal
   // bar's close" rather than waiting for or requiring a specific price.
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double entry = ask;

   double sl   = row.low - 2 * point; // signal bar's low minus 2 ticks
   double risk = entry - sl;
   if(risk <= 0) return;

   double tp1 = entry + risk;              // 1:1
   double tp2 = entry + risk * InpTpRR;    // tp_rr (2:1)

   // Position sizing — mirrors backtest.py:
   //   lot = max(0.01, round(balance * RISK_PCT / (risk * CONTRACT), 2))
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(contract <= 0) contract = 100000.0;
   double lot = (balance * InpRiskPct) / (risk * contract);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(lotStep > 0) lot = MathRound(lot / lotStep) * lotStep;
   lot = MathMax(minLot, lot);
   lot = MathMin(maxLot, lot);
   if(lot < 0.01) lot = 0.01; // backtest.py's max(0.01, ...) floor

   // Submit market buy with SL at signal-low-minus-2-ticks and TP at tp2
   // (the full 2R target; the 1:1 partial is managed in ManagePosition()).
   if(trade.Buy(lot, _Symbol, 0.0, sl, tp2, "OBSweep"))
     {
      g_posTicket   = trade.ResultOrder(); // deal/position ticket from the last trade op
      g_entryPrice  = entry;
      g_tp1Price    = tp1;
      g_tp2Price    = tp2;
      g_origSl      = sl;
      g_partialDone = false;

      // Resolve the actual position ticket now open (ResultOrder can be the
      // order ticket; on a market fill the position ticket usually matches,
      // but re-select to be certain).
      if(PositionSelect(_Symbol) && PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
         g_posTicket = (ulong)PositionGetInteger(POSITION_TICKET);
     }
  }
//+------------------------------------------------------------------+
