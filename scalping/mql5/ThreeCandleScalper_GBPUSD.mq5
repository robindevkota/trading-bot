//+------------------------------------------------------------------+
//| ThreeCandleScalper_GBPUSD.mq5                                    |
//| Cross-validation port of scalping/backtest.py (Python engine)    |
//| Logic source: scalping/config/strategy.yaml (GBPUSD block)       |
//|                                                                    |
//| Exec TF: M5   Bias TF: H1                                        |
//| Sessions (broker time must be UTC, or adjust InpUseServerGMT*):  |
//|   London 07-10 UTC | New York 12-15 UTC                          |
//|                                                                    |
//| Load in Strategy Tester on GBPUSD, M5, same date range as the    |
//| Python run, to cross-check win rate / PF / net P&L independently.|
//+------------------------------------------------------------------+
#property copyright "cross-validation build"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//----------------------------------------------------------------------
// Inputs mirror scalping/config/strategy.yaml exactly (GBPUSD block)
//----------------------------------------------------------------------
input int    InpAtrPeriod       = 14;      // atr_period
input double InpMinC1AtrMult    = 0.70;    // min_c1_atr_mult
input double InpMinBodyPct      = 0.60;    // min_body_pct
input double InpTpBodyMult      = 1.5;     // tp_body_mult[GBPUSD]
input double InpPullbackAtrMult = 1.2;     // pullback_atr_mult[GBPUSD]
input int    InpEmaFast         = 50;      // ema_fast (H1)
input int    InpEmaSlow         = 200;     // ema_slow (H1)
input double InpRiskPct         = 0.02;    // risk_pct.forex
input double InpAccountBalance  = 10000;   // account_balance (for lot sizing reference only; live sizing uses current equity)

// Sessions (UTC hours) — London 07-10, New York 12-15
input int InpSession1Start = 7;
input int InpSession1End   = 10;
input int InpSession2Start = 12;
input int InpSession2End   = 15;

// If your broker server time is NOT UTC, set the offset here (broker_hour - UTC_hour)
input int InpBrokerUtcOffsetHours = 0;

input ulong InpMagic = 20260825;

//----------------------------------------------------------------------
// Handles / state
//----------------------------------------------------------------------
int atrHandleM5;
int emaFastHandleH1;
int emaSlowHandleH1;

bool   g_locked         = false;
int    g_lockDir        = 0;      // 1 = long, -1 = short
double g_lockThreshold  = 0.0;

datetime g_lastBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   atrHandleM5 = iATR(_Symbol, PERIOD_M5, InpAtrPeriod);
   emaFastHandleH1 = iMA(_Symbol, PERIOD_H1, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   emaSlowHandleH1 = iMA(_Symbol, PERIOD_H1, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);

   if(atrHandleM5 == INVALID_HANDLE || emaFastHandleH1 == INVALID_HANDLE || emaSlowHandleH1 == INVALID_HANDLE)
     {
      Print("Indicator handle creation failed");
      return(INIT_FAILED);
     }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(atrHandleM5);
   IndicatorRelease(emaFastHandleH1);
   IndicatorRelease(emaSlowHandleH1);
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
// H1 bias: 1 = bull, -1 = bear, 0 = neutral (mirrors build_bias() in Python)
//+------------------------------------------------------------------+
int GetH1Bias(datetime m5Time)
  {
   // Find the most recently closed H1 bar at or before m5Time
   int shift = iBarShift(_Symbol, PERIOD_H1, m5Time, false);
   if(shift < 0) return 0;
   // use the bar that has fully closed relative to m5Time
   datetime h1Time = iTime(_Symbol, PERIOD_H1, shift);
   if(h1Time > m5Time) shift++;

   double emaFast[], emaSlow[];
   double close[];
   ArraySetAsSeries(emaFast, true);
   ArraySetAsSeries(emaSlow, true);
   ArraySetAsSeries(close, true);

   if(CopyBuffer(emaFastHandleH1, 0, shift, 1, emaFast) <= 0) return 0;
   if(CopyBuffer(emaSlowHandleH1, 0, shift, 1, emaSlow) <= 0) return 0;
   if(CopyClose(_Symbol, PERIOD_H1, shift, 1, close) <= 0) return 0;

   bool bull = (emaFast[0] > emaSlow[0]) && (close[0] > emaFast[0]);
   bool bear = (emaFast[0] < emaSlow[0]) && (close[0] < emaFast[0]);

   if(bull) return 1;
   if(bear) return -1;
   return 0;
  }

//+------------------------------------------------------------------+
double Body(const MqlRates &r) { return MathAbs(r.close - r.open); }

//+------------------------------------------------------------------+
void OnTick()
  {
   // Process once per new M5 bar close, evaluating the just-closed bar as C3
   datetime curBarTime = iTime(_Symbol, PERIOD_M5, 0);
   if(curBarTime == g_lastBarTime) return;
   g_lastBarTime = curBarTime;

   // Need at least 4 closed bars: C1, C2, C3 closed + we are now forming C4
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, PERIOD_M5, 0, 5, rates) < 5) return;

   // rates[0] = current forming bar (C4, in progress)
   // rates[1] = C3 (just closed)
   // rates[2] = C2
   // rates[3] = C1
   MqlRates c3 = rates[1];
   MqlRates c2 = rates[2];
   MqlRates c1 = rates[3];

   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(atrHandleM5, 0, 1, 1, atrBuf) <= 0) return; // ATR at C3
   double atr = atrBuf[0];
   if(atr <= 0) return;

   // ── Manage pullback lock ──────────────────────────────────────
   if(g_locked)
     {
      if(g_lockDir == 1 && c3.low <= g_lockThreshold) g_locked = false;
      else if(g_lockDir == -1 && c3.high >= g_lockThreshold) g_locked = false;
     }
   if(g_locked) return;

   // Don't evaluate a new signal while a position from this EA is open
   if(PositionSelect(_Symbol) && PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
      return;

   if(!InSession(c3.time)) return;

   int bias = GetH1Bias(c3.time);
   if(bias == 0) return;

   double c1b = Body(c1);
   double c2b = Body(c2);
   double c3b = Body(c3);

   int direction = 0; // 1 long, -1 short

   if(bias == 1)
     {
      if(!(c1.close > c1.open && c2.close > c2.open && c3.close > c3.open)) return;
      if(c1b < InpMinC1AtrMult * atr) return;
      if(c2.close <= c1.close) return;
      if(c3.close <= c2.close) return;
      if(c2b < InpMinBodyPct * c1b) return;
      if(c3b < InpMinBodyPct * c1b) return;
      direction = 1;
     }
   else
     {
      if(!(c1.close < c1.open && c2.close < c2.open && c3.close < c3.open)) return;
      if(c1b < InpMinC1AtrMult * atr) return;
      if(c2.close >= c1.close) return;
      if(c3.close >= c2.close) return;
      if(c2b < InpMinBodyPct * c1b) return;
      if(c3b < InpMinBodyPct * c1b) return;
      direction = -1;
     }

   // Entry at C4 open == current tick's open price of the forming bar
   double entry = rates[0].open;
   double avgBody = (c1b + c2b + c3b) / 3.0;
   double tpDist = avgBody * InpTpBodyMult;

   double sl = (direction == 1) ? c3.low : c3.high;
   double tp = (direction == 1) ? entry + tpDist : entry - tpDist;
   double slDist = MathAbs(entry - sl);
   if(slDist <= 0) return;

   // Position size: risk% of current equity / (SL distance in price * contract size)
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lotStep   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   double riskAmount = equity * InpRiskPct;
   double slValuePerLot = (slDist / tickSize) * tickValue;
   if(slValuePerLot <= 0) return;

   double lot = riskAmount / slValuePerLot;
   lot = MathFloor(lot / lotStep) * lotStep;
   lot = MathMax(minLot, MathMin(maxLot, lot));

   // Set pullback lock threshold for AFTER this trade closes
   g_lockDir = direction;
   g_lockThreshold = (direction == 1) ? entry - InpPullbackAtrMult * atr
                                       : entry + InpPullbackAtrMult * atr;
   g_locked = true;

   if(direction == 1)
      trade.Buy(lot, _Symbol, 0.0, sl, tp, "3CS-GBPUSD");
   else
      trade.Sell(lot, _Symbol, 0.0, sl, tp, "3CS-GBPUSD");

   // Note: entry price above is intended to equal the market order fill price
   // at C4 open; in Strategy Tester with "Every tick based on real ticks" or
   // OHLC, the market order fills at/near current price, matching entry=C4 open.
  }
//+------------------------------------------------------------------+
