//+------------------------------------------------------------------+
//| OmarNoWick_LOCKED_EA.mq5                                         |
//|                                                                  |
//| The LOCKED v2 no-wick strategy (m1_scalper/strategy/omarnowick/  |
//| LOCKED.md, re-locked 2026-09-14) as an Expert Advisor for the    |
//| MT5 Strategy Tester.  SIMULATION ONLY -- see InpTesterOnly.      |
//|                                                                  |
//| Setup A only (B and C were rejected):                            |
//|   no-wick candle   long  open == low,  short open == high,       |
//|                    body >= 3 pips (0.3 pip on JPY pairs)         |
//|   chop filter      last 4 market swings alternate SH/SL/SH/SL    |
//|   trend            market trend = trade side, >= 2 consecutive   |
//|                    same-direction market BOS                     |
//|   pullback         the candle does NOT close above the prior     |
//|                    10-bar HIGH (long) / below the 10-bar LOW     |
//|                    (short)                                       |
//|   dead hours       no signal candle 13:00-15:59 (see offset)     |
//|   stop             latest market swing +/- 5 pips, skip if the   |
//|                    stop is >= 8 x ATR(14)                        |
//|   entry            the candle's open (2 pips earlier when the    |
//|                    stop is > 10 pips), filled on a retest within |
//|                    10 bars, void if price reaches TP first       |
//|   target           fixed 1:1; time stop after 120 bars           |
//|                                                                  |
//| "Market swing" / "market BOS" are the m1_scalper engine's         |
//| (sim.run) swing machine, ported here from commit 24b1696          |
//| (2026-09-08) -- the engine that produced the v2 numbers           |
//| (n=1655 / 73.0%).  Only the swing/BOS half of sim.run is ported:  |
//| its CHoCH half never feeds back into swings or BOS.               |
//|                                                                  |
//| LOOK-AHEAD: every decision is made on CLOSED bars only (bars with |
//| time < the forming bar's time, asserted in ProcessClosedBar).     |
//| The forming bar is read for exactly one thing: whether price has |
//| touched a resting entry level -- what a resting limit order sees. |
//|                                                                  |
//| !! The published v2 numbers are NOT reproducible honestly. The   |
//| Python backtest's chop filter and stop anchor read swings by     |
//| their PIVOT bar, i.e. before the engine had confirmed them. This |
//| EA reads only swings already confirmed -- what a live chart has. |
//| Python replica (verify_locked_ea.py), same bars, 2000-2026:      |
//| published 1655 trades / 73.0%; honest + one position 1992 / 51%. |
//+------------------------------------------------------------------+
#property copyright "omarnowick LOCKED v2 -- tester port"
#property version   "1.00"

#include <Trade\Trade.mqh>
CTrade trade;

//--- safety -----------------------------------------------------------
input bool     InpTesterOnly           = true;        // refuse to run outside the Strategy Tester
input ulong    InpMagic                = 20261004;    // magic number
//--- clock ------------------------------------------------------------
input ENUM_TIMEFRAMES InpTF            = PERIOD_M15;  // working timeframe (LOCKED: M15)
input int      InpBrokerUtcOffsetHours = 0;           // broker_hour - UTC_hour. 0 = read server hours as-is (what the Python backtest did)
input int      InpDeadHourFrom         = 13;          // no signal candle from this hour ...
input int      InpDeadHourTo           = 15;          // ... through this hour (inclusive)
//--- engine (sim.run, M15 rung of TF_LADDER) ------------------------
input datetime InpEngineFrom           = D'2000.01.01'; // structure is built from here (Python: year >= 2000)
input double   InpPipSize              = 0.0;         // 0 = auto: 0.01 on JPY pairs, else 0.0001
input double   InpPullbackPips         = 15.0;        // MIN_PULLBACK_PIPS (M15 rung)
input int      InpMinPullbackBars      = 3;           // MIN_PULLBACK_BARS
input double   InpBootstrapPips        = 60.0;        // BOOTSTRAP_PIPS (M15 rung)
input int      InpBootstrapMinBars     = 5;           // BOOTSTRAP_MIN_BARS
//--- signal -------------------------------------------------------------
input double   InpBodyMinPips          = 0.0;         // 0 = auto: 3 pips, 0.3 pip on JPY pairs
input int      InpChopSwings           = 4;           // last N market swings must alternate
input int      InpMinBosRun            = 2;           // consecutive same-direction market BOS
input int      InpBreakLookback        = 10;          // pullback filter: prior N-bar extreme
input int      InpAtrLen               = 14;          // ATR length
input double   InpMaxSlAtr             = 8.0;         // skip if stop distance >= this x ATR
input int      InpFirstSignalBar       = 20;          // engine bars before the first signal
//--- trade --------------------------------------------------------------
input double   InpSlBufferPips         = 5.0;         // stop beyond the market swing
input double   InpBigSlPips            = 10.0;        // stop wider than this -> enter early
input double   InpEarlyEntryPips       = 2.0;         // early-entry shift
input int      InpRetestBars           = 10;          // retest window
input double   InpTpRR                 = 1.0;         // target in R (LOCKED: 1.0)
input int      InpMaxHoldBars          = 120;         // close at market after N bars (0 = off; Python: 120)
input bool     InpOnePosition          = true;        // LOCKED: one position per pair (false needs a hedging account)
input double   InpRiskPct              = 1.0;         // % of balance risked per trade
input double   InpFixedLots            = 0.0;         // > 0 overrides risk sizing
input bool     InpLogCsv               = true;        // write MQL5/Files/OmarNoWick_LOCKED_<symbol>.csv

//--- derived ------------------------------------------------------------
double g_pip, g_bodyMin, g_minPb, g_bootRng, g_buf, g_bigSl, g_early;

//====================================================================
// ENGINE -- sim.run (24b1696) lines 201-309, the market swing machine
//====================================================================
int    e_mode = 0;                     // 0 boot, 1 huntSL, 2 huntSH
bool   e_hasSH = false, e_hasSL = false;
double e_lastSH = 0, e_lastSL = 0;
long   e_lastSHIdx = -1, e_lastSLIdx = -1;
bool   e_slSpent = false, e_shSpent = false;
double e_candPrice = 0;  long e_candIdx = -1;
bool   e_hasHiHS = false; double e_hiHS = 0; long e_hiHSIdx = -1;   // hiHighSinceSL
bool   e_hasLoLS = false; double e_loLS = 0; long e_loLSIdx = -1;   // loLowSinceSH
bool   e_hasBHigh = false, e_hasBLow = false;
double e_bHigh = 0, e_bLSH = 0, e_bLow = 0, e_bHSL = 0;
long   e_bHighIdx = -1, e_bLSHIdx = -1, e_bLowIdx = -1, e_bHSLIdx = -1;

//--- what the strategy reads from the engine ----------------------------
int    s_trend = 0, s_run = 0;          // market trend (+1/-1) and same-direction BOS run
int    s_kinds[];                      // last InpChopSwings recorded swing kinds (+1 SH / -1 SL)
int    s_nKinds = 0;
bool   s_hasBestSH = false, s_hasBestSL = false;          // latest CONFIRMED swing of each kind
long   s_bestSHIdx = -1, s_bestSLIdx = -1;
double s_bestSHPx = 0, s_bestSLPx = 0;
bool   s_hasLo = false, s_hasHi = false;                  // stop anchors
double s_lo = 0, s_hi = 0;

//--- recent closed bars (ring) ------------------------------------------
#define RING 64
double r_o[RING], r_h[RING], r_l[RING], r_c[RING];

//--- bookkeeping -----------------------------------------------------------
long     g_k = -1;                     // index of the last processed CLOSED bar (0 = first engine bar)
datetime g_lastBarTime = 0;            // open time of the bar that was forming at the last tick
bool     g_ready = false;
bool     g_hadPos = false;
datetime g_exitBarTime = 0;            // bar on which our last position closed
long     g_fillK = -1;                 // forming-bar index at the fill (time stop)
int      g_csv = INVALID_HANDLE;

struct Setup
  {
   long     k;          // signal bar index
   int      dir;        // +1 long, -1 short
   double   entry, sl, tp, sd;
   datetime sigTime;
  };
Setup g_pend[];

//+------------------------------------------------------------------+
void RecordSwing(long pivot, double px, int kind)
  {
   // chop window: the last N swings in the order the engine CONFIRMED them
   if(s_nKinds < InpChopSwings)
      s_kinds[s_nKinds++] = kind;
   else
     {
      for(int j = 1; j < InpChopSwings; j++) s_kinds[j - 1] = s_kinds[j];
      s_kinds[InpChopSwings - 1] = kind;
     }
   // latest confirmed swing per kind, by pivot bar (ties: higher price)
   if(kind > 0)
     {
      if(!s_hasBestSH || pivot > s_bestSHIdx || (pivot == s_bestSHIdx && px >= s_bestSHPx))
        { s_hasBestSH = true; s_bestSHIdx = pivot; s_bestSHPx = px; }
     }
   else
     {
      if(!s_hasBestSL || pivot > s_bestSLIdx || (pivot == s_bestSLIdx && px >= s_bestSLPx))
        { s_hasBestSL = true; s_bestSLIdx = pivot; s_bestSLPx = px; }
     }
  }

//+------------------------------------------------------------------+
// one bar of sim.run's swing machine. bos = +1/-1/0, conf = +1 SH / -1 SL / 0
void EngineStep(long i, double h, double l, double c, int &bos, int &conf)
  {
   bos = 0; conf = 0;
   if(e_mode == 0)
     {
      if(!e_hasBHigh || h >= e_bHigh)
        { e_hasBHigh = true; e_bHigh = h; e_bHighIdx = i; e_bLSH = l; e_bLSHIdx = i; }
      else if(l < e_bLSH)
        { e_bLSH = l; e_bLSHIdx = i; }
      if(!e_hasBLow || l <= e_bLow)
        { e_hasBLow = true; e_bLow = l; e_bLowIdx = i; e_bHSL = h; e_bHSLIdx = i; }
      else if(h > e_bHSL)
        { e_bHSL = h; e_bHSLIdx = i; }
      bool fall = (e_bHigh - e_bLSH) >= g_bootRng && (e_bLSHIdx - e_bHighIdx) >= InpBootstrapMinBars;
      bool rise = (e_bHSL - e_bLow) >= g_bootRng && (e_bHSLIdx - e_bLowIdx) >= InpBootstrapMinBars;
      if(fall || rise)
        {
         if(fall) { e_lastSH = e_bHigh; e_lastSHIdx = e_bHighIdx; e_lastSL = e_bLSH; e_lastSLIdx = e_bLSHIdx; }
         else     { e_lastSH = e_bHSL;  e_lastSHIdx = e_bHSLIdx;  e_lastSL = e_bLow; e_lastSLIdx = e_bLowIdx; }
         e_hasSH = e_hasSL = true;
         RecordSwing(e_lastSHIdx, e_lastSH, +1);
         RecordSwing(e_lastSLIdx, e_lastSL, -1);
         e_mode = fall ? 1 : 2;
         e_candPrice = fall ? l : h;
         e_candIdx = i;
         e_hasHiHS = true; e_hiHS = h; e_hiHSIdx = i;
         e_hasLoLS = true; e_loLS = l; e_loLSIdx = i;
        }
     }
   if(e_mode == 0) return;

   if(!e_hasHiHS || h > e_hiHS) { e_hasHiHS = true; e_hiHS = h; e_hiHSIdx = i; }
   if(!e_hasLoLS || l < e_loLS) { e_hasLoLS = true; e_loLS = l; e_loLSIdx = i; }
   if(e_mode == 1 && l < e_candPrice) { e_candPrice = l; e_candIdx = i; }
   if(e_mode == 2 && h > e_candPrice) { e_candPrice = h; e_candIdx = i; }

   long barsSince   = (e_candIdx < 0) ? 0 : i - e_candIdx;
   bool pullbackOK  = barsSince >= InpMinPullbackBars;
   bool slVac  = e_hasSL && e_hasHiHS && (e_hiHS - e_lastSL) >= g_minPb;
   bool shVac  = e_hasSH && e_hasLoLS && (e_lastSH - e_loLS) >= g_minPb;
   bool bosDown = slVac && !e_slSpent && c < e_lastSL;
   bool bosUp   = shVac && !e_shSpent && c > e_lastSH;
   bool newSL = e_mode == 1 && e_candIdx != e_lastSLIdx && (!e_hasSL || e_candPrice < e_lastSL);
   bool newSH = e_mode == 2 && e_candIdx != e_lastSHIdx && (!e_hasSH || e_candPrice > e_lastSH);
   bool confSL = newSL && pullbackOK && (c - e_candPrice) >= g_minPb && !bosDown && !bosUp;
   bool confSH = newSH && pullbackOK && (e_candPrice - c) >= g_minPb && !bosUp && !bosDown;

   if(bosDown)
     {
      e_lastSH = e_hiHS; e_lastSHIdx = e_hiHSIdx; e_hasSH = true;
      RecordSwing(e_lastSHIdx, e_lastSH, +1);
      e_mode = 1;
      e_slSpent = true; e_shSpent = false;
      e_candPrice = l; e_candIdx = i;
      e_loLS = l; e_loLSIdx = i; e_hasLoLS = true;
      bos = -1;
     }
   else if(bosUp)
     {
      e_lastSL = e_loLS; e_lastSLIdx = e_loLSIdx; e_hasSL = true;
      RecordSwing(e_lastSLIdx, e_lastSL, -1);
      e_mode = 2;
      e_shSpent = true; e_slSpent = false;
      e_candPrice = h; e_candIdx = i;
      e_hiHS = h; e_hiHSIdx = i; e_hasHiHS = true;
      bos = +1;
     }
   else if(confSL)
     {
      e_lastSL = e_candPrice; e_lastSLIdx = e_candIdx; e_hasSL = true;
      RecordSwing(e_lastSLIdx, e_lastSL, -1);
      e_slSpent = false;
      e_hiHS = h; e_hiHSIdx = i; e_hasHiHS = true;
      conf = -1;
     }
   else if(confSH)
     {
      e_lastSH = e_candPrice; e_lastSHIdx = e_candIdx; e_hasSH = true;
      RecordSwing(e_lastSHIdx, e_lastSH, +1);
      e_shSpent = false;
      e_loLS = l; e_loLSIdx = i; e_hasLoLS = true;
      conf = +1;
     }
  }

//+------------------------------------------------------------------+
// trend/run (bos_lines), stop anchors (nowick_analyze.market_refs)
void UpdateContext(int bos, int conf)
  {
   if(bos != 0)
     {
      s_run = (bos == s_trend) ? s_run + 1 : 1;
      s_trend = bos;
     }
   // a "BULL BOS" or "SH confirmed" event refreshes the SH anchor, a
   // "BEAR BOS" or "SL confirmed" the SL anchor -- to the latest swing of
   // that kind the engine has CONFIRMED so far (Python: by pivot bar, which
   // also saw swings confirmed later -- the look-ahead removed here)
   int kind = (bos != 0) ? bos : conf;
   if(kind > 0 && s_hasBestSH) { s_hasHi = true; s_hi = s_bestSHPx; }
   if(kind < 0 && s_hasBestSL) { s_hasLo = true; s_lo = s_bestSLPx; }
  }

bool Choppy()
  {
   if(s_nKinds < InpChopSwings) return true;
   for(int j = 0; j < InpChopSwings - 1; j++)
      if(s_kinds[j] == s_kinds[j + 1]) return true;
   return false;
  }

int HourOf(datetime t)
  {
   MqlDateTime dt; TimeToStruct(t, dt);
   return (dt.hour - InpBrokerUtcOffsetHours + 48) % 24;
  }

//+------------------------------------------------------------------+
bool HavePosition()
  {
   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong tk = PositionGetTicket(p);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return true;
     }
   return false;
  }

void CloseOurPositions()
  {
   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong tk = PositionGetTicket(p);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
         trade.PositionClose(tk);
     }
  }

void Log(string ev, const Setup &s, string extra)
  {
   if(g_csv == INVALID_HANDLE) return;
   FileWrite(g_csv, ev, TimeToString(s.sigTime, TIME_DATE | TIME_MINUTES), s.dir,
             DoubleToString(s.entry, _Digits + 1), DoubleToString(s.sl, _Digits + 1),
             DoubleToString(s.tp, _Digits + 1), TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), extra);
  }

void RemovePending(int idx)
  {
   int n = ArraySize(g_pend);
   for(int j = idx; j < n - 1; j++) g_pend[j] = g_pend[j + 1];
   ArrayResize(g_pend, n - 1);
  }

//+------------------------------------------------------------------+
// one CLOSED bar.  trading=false during the warm-up over history.
void ProcessClosedBar(const MqlRates &b, bool trading)
  {
   // LOOK-AHEAD GUARD: only a bar that has fully closed may get here
   if(trading && b.time >= iTime(_Symbol, InpTF, 0))
     {
      Print("LOOK-AHEAD GUARD: refused unclosed bar ", TimeToString(b.time));
      return;
     }
   g_k++;
   long k = g_k;
   int ri = (int)(k % RING);
   r_o[ri] = b.open; r_h[ri] = b.high; r_l[ri] = b.low; r_c[ri] = b.close;

   int bos, conf;
   EngineStep(k, b.high, b.low, b.close, bos, conf);
   UpdateContext(bos, conf);

   if(!trading) return;

   //--- time stop: Python walks fill .. fill+MAX_HOLD-1, then exits at that close
   if(InpMaxHoldBars > 0 && g_fillK >= 0 && HavePosition() && k - g_fillK >= InpMaxHoldBars - 1)
     {
      CloseOurPositions();
      g_hadPos = HavePosition();
      g_exitBarTime = b.time;
      g_fillK = -1;
     }

   //--- pending setups: void if TP was reached without a retest; expire the window
   for(int p = ArraySize(g_pend) - 1; p >= 0; p--)
     {
      Setup s = g_pend[p];
      bool voidTp = (s.dir > 0) ? (b.high >= s.tp && b.low > s.entry)
                                : (b.low <= s.tp && b.high < s.entry);
      if(voidTp) { Log("VOID_TP", s, ""); RemovePending(p); continue; }
      if(k - s.k >= InpRetestBars) { Log("EXPIRED", s, ""); RemovePending(p); }
     }

   //--- signal on this bar
   if(k < InpFirstSignalBar) return;
   if(HourOf(b.time) >= InpDeadHourFrom && HourOf(b.time) <= InpDeadHourTo) return;
   if(Choppy()) return;

   double o = b.open, h = b.high, l = b.low, c = b.close;
   double body = MathAbs(c - o);

   // ATR(14): mean true range of bars k-13..k
   double sum = 0;
   for(int j = 0; j < InpAtrLen; j++)
     {
      int a = (int)((k - j) % RING), pv = (int)((k - j - 1) % RING);
      double tr = MathMax(r_h[a] - r_l[a], MathMax(MathAbs(r_h[a] - r_c[pv]), MathAbs(r_l[a] - r_c[pv])));
      sum += tr;
     }
   double atrPips = (sum / InpAtrLen) / g_pip;
   // prior N-bar extremes (bars k-N .. k-1)
   double rhi = -DBL_MAX, rlo = DBL_MAX;
   for(int j = 1; j <= InpBreakLookback; j++)
     {
      int a = (int)((k - j) % RING);
      rhi = MathMax(rhi, r_h[a]); rlo = MathMin(rlo, r_l[a]);
     }

   for(int d = 1; d >= -1; d -= 2)
     {
      bool nw = (d > 0) ? ((o - l) <= 0.0) : ((h - o) <= 0.0);
      if(!nw || body < g_bodyMin) continue;
      if(!(s_trend == d && s_run >= InpMinBosRun)) continue;
      if(d > 0 && !s_hasLo) continue;
      if(d < 0 && !s_hasHi) continue;
      double ref = (d > 0) ? s_lo : s_hi;
      double e0 = o;
      double sl = (d > 0) ? ref - g_buf : ref + g_buf;
      if((d > 0 && sl >= e0) || (d < 0 && sl <= e0)) continue;
      double sd = MathAbs(e0 - sl);
      double entry = e0;
      if(sd > g_bigSl)
        {
         entry = e0 + ((d > 0) ? g_early : -g_early);
         sd = MathAbs(entry - sl);
        }
      if(sd <= 0) continue;
      double tp = entry + d * sd * InpTpRR;
      bool broke = (d > 0) ? (c > rhi) : (c < rlo);
      if(broke) continue;
      if(atrPips <= 0) continue;
      if(!((sd / g_pip) / atrPips < InpMaxSlAtr)) continue;

      Setup s;
      s.k = k; s.dir = d; s.entry = entry; s.sl = sl; s.tp = tp; s.sd = sd; s.sigTime = b.time;
      int n = ArraySize(g_pend);
      ArrayResize(g_pend, n + 1);
      g_pend[n] = s;
      Log("SIGNAL", s, "");
     }
  }

//+------------------------------------------------------------------+
// the forming bar: has price come back to a resting entry?
void CheckFills()
  {
   if(ArraySize(g_pend) == 0) return;
   datetime t0 = iTime(_Symbol, InpTF, 0);
   double lo = iLow(_Symbol, InpTF, 0), hi = iHigh(_Symbol, InpTF, 0);
   long cur = g_k + 1;                               // index of the forming bar
   for(int p = 0; p < ArraySize(g_pend); p++)
     {
      Setup s = g_pend[p];
      if(cur - s.k > InpRetestBars) continue;       // removed at this bar's close
      bool touched = (s.dir > 0) ? (lo <= s.entry) : (hi >= s.entry);
      if(!touched) continue;

      if(InpOnePosition && (HavePosition() || g_exitBarTime == t0))
        {
         Log("SKIP_BUSY", s, "");
         RemovePending(p); p--;
         continue;
        }
      double px = (s.dir > 0) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double lots = InpFixedLots;
      if(lots <= 0)
        {
         double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
         if(tv <= 0) tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
         double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
         double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPct / 100.0;
         double perLot = (ts > 0 && tv > 0) ? MathAbs(px - s.sl) / ts * tv : 0;
         lots = (perLot > 0) ? riskMoney / perLot : 0;
        }
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
      if(step > 0) lots = MathFloor(lots / step + 1e-9) * step;
      lots = MathMin(lots, vmax);
      if(lots < vmin)
        {
         Log("SKIP_SIZE", s, DoubleToString(lots, 4));   // never rounded UP to the minimum
         RemovePending(p); p--;
         continue;
        }
      string cmt = StringFormat("ONW-A %s %s", (s.dir > 0 ? "L" : "S"),
                                TimeToString(s.sigTime, TIME_DATE | TIME_MINUTES));
      double sl = NormalizeDouble(s.sl, _Digits), tp = NormalizeDouble(s.tp, _Digits);
      bool ok = (s.dir > 0) ? trade.Buy(lots, _Symbol, 0.0, sl, tp, cmt)
                            : trade.Sell(lots, _Symbol, 0.0, sl, tp, cmt);
      if(ok && (trade.ResultRetcode() == TRADE_RETCODE_DONE || trade.ResultRetcode() == TRADE_RETCODE_PLACED))
        {
         g_fillK = cur;
         g_hadPos = true;
         Log("FILL", s, DoubleToString(trade.ResultPrice(), _Digits) + " lots=" + DoubleToString(lots, 2));
        }
      else
         Log("REJECTED", s, IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription());
      RemovePending(p); p--;
     }
  }

//+------------------------------------------------------------------+
bool WarmUp()
  {
   datetime t0 = iTime(_Symbol, InpTF, 0);
   if(t0 == 0) return false;
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int cnt = CopyRates(_Symbol, InpTF, InpEngineFrom, t0, r);
   if(cnt <= 0) { Print("warm-up: no history yet (", GetLastError(), "), retrying next tick"); return false; }
   int used = 0;
   for(int j = 0; j < cnt; j++)
     {
      if(r[j].time >= t0) break;                    // the forming bar is not history
      ProcessClosedBar(r[j], false);
      used++;
     }
   g_lastBarTime = t0;
   PrintFormat("warm-up: %d closed %s bars, %s -> %s, engine mode %d, trend %d run %d",
               used, EnumToString(InpTF),
               used > 0 ? TimeToString(r[0].time) : "-",
               used > 0 ? TimeToString(r[used - 1].time) : "-", e_mode, s_trend, s_run);
   if(used > 0 && r[0].time > InpEngineFrom + 40 * 86400)
      PrintFormat("NOTE: history starts %s, later than InpEngineFrom %s -- the swing map is built from there",
                  TimeToString(r[0].time), TimeToString(InpEngineFrom));
   return true;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpTesterOnly && !MQLInfoInteger(MQL_TESTER))
     {
      Print("OmarNoWick_LOCKED_EA is a SIMULATION tool: it refuses to run outside the Strategy Tester. ",
            "Set InpTesterOnly=false only if you really mean to trade this account.");
      return INIT_FAILED;
     }
   string sym = _Symbol;
   StringToUpper(sym);
   bool jpy = StringFind(sym, "JPY") >= 0;
   g_pip     = (InpPipSize > 0) ? InpPipSize : (jpy ? 0.01 : 0.0001);
   g_bodyMin = (InpBodyMinPips > 0) ? InpBodyMinPips * g_pip : (jpy ? 0.3 * g_pip : 3 * g_pip);
   g_minPb   = InpPullbackPips * g_pip;
   g_bootRng = InpBootstrapPips * g_pip;
   g_buf     = InpSlBufferPips * g_pip;
   g_bigSl   = InpBigSlPips * g_pip;
   g_early   = InpEarlyEntryPips * g_pip;
   if(InpChopSwings < 2 || InpAtrLen + 1 >= RING || InpBreakLookback >= RING)
     { Print("bad lookback inputs"); return INIT_PARAMETERS_INCORRECT; }
   ArrayResize(s_kinds, InpChopSwings);

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(50);

   if(InpLogCsv)
     {
      g_csv = FileOpen("OmarNoWick_LOCKED_" + _Symbol + ".csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(g_csv != INVALID_HANDLE)
         FileWrite(g_csv, "event", "signal_bar", "dir", "entry", "sl", "tp", "server_time", "info");
     }
   PrintFormat("OmarNoWick LOCKED: pip=%.5f body>=%.5f pullback=%.5f bootstrap=%.5f offset=%d",
               g_pip, g_bodyMin, g_minPb, g_bootRng, InpBrokerUtcOffsetHours);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_csv != INVALID_HANDLE)
     {
      // closing deals, so the log carries the outcome next to each fill
      if(HistorySelect(0, TimeCurrent()))
        {
         for(int j = 0; j < HistoryDealsTotal(); j++)
           {
            ulong d = HistoryDealGetTicket(j);
            if((ulong)HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic) continue;
            if(HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
            FileWrite(g_csv, "EXIT", "", "", "", "", "",
                      TimeToString((datetime)HistoryDealGetInteger(d, DEAL_TIME), TIME_DATE | TIME_SECONDS),
                      DoubleToString(HistoryDealGetDouble(d, DEAL_PRICE), _Digits) + " pos=" +
                      IntegerToString(HistoryDealGetInteger(d, DEAL_POSITION_ID)) + " profit=" +
                      DoubleToString(HistoryDealGetDouble(d, DEAL_PROFIT), 2) + " reason=" +
                      IntegerToString(HistoryDealGetInteger(d, DEAL_REASON)));
           }
        }
      FileClose(g_csv);
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(!g_ready)
     {
      if(!WarmUp()) return;
      g_ready = true;
     }

   bool haveNow = HavePosition();
   if(g_hadPos && !haveNow) { g_exitBarTime = iTime(_Symbol, InpTF, 0); g_fillK = -1; }
   g_hadPos = haveNow;

   datetime t0 = iTime(_Symbol, InpTF, 0);
   if(t0 != g_lastBarTime && t0 != 0)
     {
      // every bar that closed since the last tick, oldest first
      MqlRates r[];
      ArraySetAsSeries(r, false);
      int cnt = CopyRates(_Symbol, InpTF, g_lastBarTime, t0, r);
      for(int j = 0; j < cnt; j++)
         if(r[j].time >= g_lastBarTime && r[j].time < t0)
            ProcessClosedBar(r[j], true);
      g_lastBarTime = t0;
     }
   CheckFills();
  }
//+------------------------------------------------------------------+
