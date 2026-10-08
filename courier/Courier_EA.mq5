//+------------------------------------------------------------------+
//| Courier_EA.mq5                                                   |
//|                                                                  |
//| The courier EXPERT: places on a DEMO account exactly what the    |
//| Backtester's courier daemon wrote, and reports what happened.    |
//| Spec: backtest/walkthrough/COURIER-PLAN.md.                      |
//|                                                                  |
//| IT HAS NO OPINION. It never reads a candle, never computes a     |
//| level, never moves a stop. Every rule lives once, in Python,     |
//| under that project's tests.                                      |
//|                                                                  |
//|   reads   MQL5\Files\courier\orders.jsonl  (daemon -> expert)    |
//|   writes  MQL5\Files\courier\fills.jsonl   (expert -> daemon)    |
//|                                                                  |
//| place   -> pending BUY_LIMIT / SELL_LIMIT with sl / tp /         |
//|            expiration, magic = CRC-32(id) & 0x7FFFFFFF,          |
//|            comment = id; lots so one R = InpRiskPct % of the     |
//|            balance taken on the first order of the week,         |
//|            rounded DOWN to the lot step, capped by free margin   |
//| cancel  -> delete the pending order with that magic              |
//| fills   -> placed / filled / cancelled / closed / rejected, with |
//|            the deal price and SYMBOL_SPREAD at that moment       |
//|                                                                  |
//| A command whose expiry has already passed is ignored (the PC was |
//| off) and reported as "rejected: stale".                          |
//| A cursor (byte offset) in courier\ea_state.txt means a restart   |
//| replays nothing twice; a place whose magic already exists on the |
//| account is skipped as well (belt and braces).                    |
//|                                                                  |
//| InpDemoOnly (default true): on anything but a DEMO account the   |
//| expert prints why and removes itself -- at init, and again       |
//| before every order.                                              |
//+------------------------------------------------------------------+
#property copyright "Backtester courier -- dispatches what the app decided"
#property version   "1.00"

#include <Trade\Trade.mqh>
CTrade trade;

input bool   InpDemoOnly     = true;      // refuse (and remove itself on) any non-demo account
input double InpRiskPct      = 0.5;       // % of the week's opening balance risked per order (one R)
input string InpFolder       = "courier"; // MQL5\Files\<folder>\ -- where the daemon writes
input string InpSymbolSuffix = "";        // broker suffix, e.g. ".m" (MetaQuotes-Demo: none)
input double InpMarginUse    = 0.9;       // use at most this share of free margin for one order
input int    InpTimerSec     = 1;         // poll period

string g_orders, g_fills, g_state, g_ids;
long   g_cursor      = 0;
long   g_week        = -1;
double g_weekBalance = 0.0;

string g_mapId[];
ulong  g_mapMagic[];
ulong  g_deleting[];

uint   g_crc[256];

//+------------------------------------------------------------------+
//| CRC-32 (IEEE, as Python's zlib.crc32) -> the magic of an id      |
//+------------------------------------------------------------------+
void CrcInit()
  {
   for(uint i = 0; i < 256; i++)
     {
      uint c = i;
      for(int k = 0; k < 8; k++)
         c = ((c & 1) != 0) ? (0xEDB88320 ^ (c >> 1)) : (c >> 1);
      g_crc[i] = c;
     }
  }

ulong MagicOf(const string id)
  {
   uchar b[];
   int n = StringToCharArray(id, b, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0 && b[n - 1] == 0) n--;              // drop the terminating zero
   uint c = 0xFFFFFFFF;
   for(int i = 0; i < n; i++)
      c = g_crc[(c ^ b[i]) & 0xFF] ^ (c >> 8);
   c = c ^ 0xFFFFFFFF;
   return (ulong)(c & 0x7FFFFFFF);
  }

//+------------------------------------------------------------------+
//| the protocol's flat JSON: one value per key, no nesting          |
//+------------------------------------------------------------------+
string JStr(const string line, const string key)
  {
   string pat = "\"" + key + "\":";
   int p = StringFind(line, pat);
   if(p < 0) return "";
   p += StringLen(pat);
   if(StringGetCharacter(line, p) == '"')
     {
      int e = StringFind(line, "\"", p + 1);
      if(e < 0) return "";
      return StringSubstr(line, p + 1, e - p - 1);
     }
   int e = p, L = StringLen(line);
   while(e < L)
     {
      ushort ch = StringGetCharacter(line, e);
      if(ch == ',' || ch == '}') break;
      e++;
     }
   return StringSubstr(line, p, e - p);
  }

double JNum(const string line, const string key) { return StringToDouble(JStr(line, key)); }

// "2026-10-09T09:12:00" (server time) -> datetime
datetime ParseT(string s)
  {
   if(StringLen(s) == 0) return 0;
   StringReplace(s, "-", ".");
   StringReplace(s, "T", " ");
   return StringToTime(s);
  }

// datetime -> "2026-10-09T09:12:00"
string Stamp(const datetime t)
  {
   string s = TimeToString(t, TIME_DATE | TIME_SECONDS);
   StringReplace(s, ".", "-");
   StringReplace(s, " ", "T");
   return s;
  }

string Clean(string s)
  {
   StringReplace(s, "\"", "'");
   StringReplace(s, "\\", "/");
   StringReplace(s, "\n", " ");
   StringReplace(s, "\r", " ");
   return s;
  }

//+------------------------------------------------------------------+
//| fills.jsonl -- one whole line per write, appended                |
//+------------------------------------------------------------------+
void WriteFill(const string id, const string ev, const datetime t, const double price,
               const long spread, const ulong ticket, const string reason)
  {
   string line = "{\"id\":\"" + id + "\",\"t\":\"" + Stamp(t) + "\",\"event\":\"" + ev +
                 "\",\"price\":" + DoubleToString(price, 6) +
                 ",\"spread_points\":" + IntegerToString(spread) +
                 ",\"ticket\":" + IntegerToString((long)ticket) +
                 ",\"reason\":\"" + Clean(reason) + "\"}\n";
   uchar b[];
   int n = StringToCharArray(line, b, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0 && b[n - 1] == 0) n--;
   for(int attempt = 0; attempt < 20; attempt++)
     {
      int h = FileOpen(g_fills, FILE_READ | FILE_WRITE | FILE_BIN | FILE_SHARE_READ);
      if(h != INVALID_HANDLE)
        {
         FileSeek(h, 0, SEEK_END);
         FileWriteArray(h, b, 0, n);
         FileClose(h);
         PrintFormat("courier %s %s %s", ev, id, reason);
         return;
        }
      Sleep(50);
     }
   PrintFormat("courier: could NOT write fills.jsonl (%d) -- lost event %s %s", GetLastError(), ev, id);
  }

//+------------------------------------------------------------------+
//| state: cursor + the week's balance; the id <-> magic map         |
//+------------------------------------------------------------------+
void SaveState()
  {
   int h = FileOpen(g_state, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("courier: cannot write ", g_state, " ", GetLastError()); return; }
   FileWriteString(h, IntegerToString(g_cursor) + "\n");
   FileWriteString(h, IntegerToString(g_week) + "\n");
   FileWriteString(h, DoubleToString(g_weekBalance, 2) + "\n");
   FileClose(h);
  }

void LoadState()
  {
   if(!FileIsExist(g_state)) return;
   int h = FileOpen(g_state, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   g_cursor      = StringToInteger(FileReadString(h));
   g_week        = StringToInteger(FileReadString(h));
   g_weekBalance = StringToDouble(FileReadString(h));
   FileClose(h);
  }

void MapAdd(const string id, const ulong magic, const bool persist)
  {
   int n = ArraySize(g_mapId);
   for(int i = 0; i < n; i++)
      if(g_mapMagic[i] == magic) return;
   ArrayResize(g_mapId, n + 1);
   ArrayResize(g_mapMagic, n + 1);
   g_mapId[n] = id;
   g_mapMagic[n] = magic;
   if(!persist) return;
   int h = FileOpen(g_ids, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, id + "\n");
   FileClose(h);
  }

void LoadIds()
  {
   if(!FileIsExist(g_ids)) return;
   int h = FileOpen(g_ids, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   while(!FileIsEnding(h))
     {
      string id = FileReadString(h);
      StringTrimLeft(id);
      StringTrimRight(id);
      if(StringLen(id) > 0) MapAdd(id, MagicOf(id), false);
     }
   FileClose(h);
  }

string IdOf(const ulong magic)
  {
   for(int i = ArraySize(g_mapMagic) - 1; i >= 0; i--)
      if(g_mapMagic[i] == magic) return g_mapId[i];
   return "";
  }

// a position's id through its opening deal (an SL/TP close may carry another magic)
string IdOfPosition(const ulong posId)
  {
   if(!HistorySelectByPosition(posId)) return "";
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
         return IdOf((ulong)HistoryDealGetInteger(d, DEAL_MAGIC));
     }
   return "";
  }

bool IsDeleting(const ulong ticket)
  {
   for(int i = 0; i < ArraySize(g_deleting); i++)
      if(g_deleting[i] == ticket) return true;
   return false;
  }

//+------------------------------------------------------------------+
//| the account guard                                                |
//+------------------------------------------------------------------+
bool AccountIsDemo()
  {
   return AccountInfoInteger(ACCOUNT_LOGIN) != 0 &&
          AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
  }

bool GuardOrRemove()
  {
   if(!InpDemoOnly || AccountIsDemo()) return true;
   PrintFormat("Courier: account %I64d is NOT a demo account (trade mode %I64d). "
               "The courier only runs on a DEMO -- removing myself.",
               AccountInfoInteger(ACCOUNT_LOGIN), AccountInfoInteger(ACCOUNT_TRADE_MODE));
   ExpertRemove();
   return false;
  }

//+------------------------------------------------------------------+
//| sizing: one R = InpRiskPct % of the week's opening balance       |
//+------------------------------------------------------------------+
double WeekBalance()
  {
   long wk = ((long)TimeCurrent() / 86400 + 3) / 7;       // Monday-based week index
   if(wk != g_week || g_weekBalance <= 0)
     {
      g_week = wk;
      g_weekBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      PrintFormat("courier: week %I64d, sizing off balance %.2f", g_week, g_weekBalance);
      SaveState();
     }
   return g_weekBalance;
  }

double Lots(const string sym, const bool buy, const double price, const double sl, string &why)
  {
   double tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tv <= 0) tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double perLot = (ts > 0 && tv > 0) ? MathAbs(price - sl) / ts * tv : 0;
   if(perLot <= 0) { why = "no tick value / stop distance"; return 0; }
   double lots = WeekBalance() * InpRiskPct / 100.0 / perLot;
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   lots = MathFloor(lots / step + 1e-9) * step;
   lots = MathMin(lots, vmax);
   double freeM = AccountInfoDouble(ACCOUNT_MARGIN_FREE) * InpMarginUse;
   double m = 0;
   ENUM_ORDER_TYPE mt = buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   while(lots >= vmin && OrderCalcMargin(mt, sym, lots, price, m) && m > freeM)
      lots = MathFloor((lots - step) / step + 1e-9) * step;
   if(lots < vmin)
     {
      why = "size " + DoubleToString(lots, 4) + " below the minimum " + DoubleToString(vmin, 2) +
            " (never rounded UP)";
      return 0;
     }
   return NormalizeDouble(lots, 2);
  }

//+------------------------------------------------------------------+
//| commands                                                         |
//+------------------------------------------------------------------+
bool HaveMagic(const ulong magic)
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong tk = OrderGetTicket(i);
      if(tk != 0 && (ulong)OrderGetInteger(ORDER_MAGIC) == magic) return true;
     }
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && (ulong)PositionGetInteger(POSITION_MAGIC) == magic) return true;
     }
   return false;
  }

void Place(const string line)
  {
   string id   = JStr(line, "id");
   string sym  = JStr(line, "symbol") + InpSymbolSuffix;
   string side = JStr(line, "side");
   datetime exp = ParseT(JStr(line, "expiry"));
   ulong magic = MagicOf(id);
   long spread = SymbolInfoInteger(sym, SYMBOL_SPREAD);
   if(!GuardOrRemove()) return;
   if(exp <= TimeCurrent())
     {
      WriteFill(id, "rejected", TimeCurrent(), 0, spread, 0,
                "stale: expiry " + Stamp(exp) + " passed before the expert read it (the PC was off)");
      return;
     }
   if(HaveMagic(magic))
     {
      PrintFormat("courier: %s already on the account (magic %I64u) -- not placed twice", id, magic);
      MapAdd(id, magic, true);
      return;
     }
   if(!SymbolSelect(sym, true))
     {
      WriteFill(id, "rejected", TimeCurrent(), 0, 0, 0, "unknown symbol " + sym);
      return;
     }
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double price = NormalizeDouble(JNum(line, "price"), digits);
   double sl    = NormalizeDouble(JNum(line, "sl"), digits);
   double tp    = NormalizeDouble(JNum(line, "tp"), digits);
   bool buy = (side == "buy");
   if(!buy && side != "sell")
     {
      WriteFill(id, "rejected", TimeCurrent(), price, spread, 0, "bad side " + side);
      return;
     }
   string why = "";
   double lots = Lots(sym, buy, price, sl, why);
   if(lots <= 0)
     {
      WriteFill(id, "rejected", TimeCurrent(), price, spread, 0, why);
      return;
     }
   ENUM_ORDER_TYPE_TIME tt = ORDER_TIME_SPECIFIED;
   datetime e = exp;
   if((SymbolInfoInteger(sym, SYMBOL_EXPIRATION_MODE) & SYMBOL_EXPIRATION_SPECIFIED) == 0)
     {
      tt = ORDER_TIME_GTC;                        // the daemon's cancel ends it instead
      e = 0;
     }
   trade.SetExpertMagicNumber(magic);
   trade.SetTypeFillingBySymbol(sym);
   bool ok = buy ? trade.BuyLimit(lots, price, sym, sl, tp, tt, e, id)
                 : trade.SellLimit(lots, price, sym, sl, tp, tt, e, id);
   uint rc = trade.ResultRetcode();
   if(ok && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED))
     {
      MapAdd(id, magic, true);
      WriteFill(id, "placed", TimeCurrent(), price, SymbolInfoInteger(sym, SYMBOL_SPREAD),
                trade.ResultOrder(), "lots " + DoubleToString(lots, 2) + " magic " + IntegerToString((long)magic) +
                (tt == ORDER_TIME_GTC ? " GTC (symbol has no specified expiry)" : ""));
     }
   else
      WriteFill(id, "rejected", TimeCurrent(), price, SymbolInfoInteger(sym, SYMBOL_SPREAD), 0,
                IntegerToString((long)rc) + " " + trade.ResultRetcodeDescription());
  }

void Cancel(const string line)
  {
   string id = JStr(line, "id");
   string why = JStr(line, "comment");
   ulong magic = MagicOf(id);
   bool any = false;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong tk = OrderGetTicket(i);
      if(tk == 0 || (ulong)OrderGetInteger(ORDER_MAGIC) != magic) continue;
      any = true;
      string sym = OrderGetString(ORDER_SYMBOL);
      int n = ArraySize(g_deleting);
      ArrayResize(g_deleting, n + 1);
      g_deleting[n] = tk;
      if(trade.OrderDelete(tk))
         WriteFill(id, "cancelled", TimeCurrent(), 0, SymbolInfoInteger(sym, SYMBOL_SPREAD), tk,
                   "courier: " + why);
      else
         WriteFill(id, "rejected", TimeCurrent(), 0, 0, tk,
                   "cancel failed " + IntegerToString((long)trade.ResultRetcode()) + " " +
                   trade.ResultRetcodeDescription());
     }
   if(!any)
      WriteFill(id, "rejected", TimeCurrent(), 0, 0, 0,
                "cancel: no pending order (already filled, expired or never placed)");
  }

void Handle(const string line)
  {
   string cmd = JStr(line, "cmd");
   if(cmd == "place")       Place(line);
   else if(cmd == "cancel") Cancel(line);
   else                     Print("courier: unknown command line ignored: ", line);
  }

//+------------------------------------------------------------------+
//| tail orders.jsonl from the cursor; whole lines only              |
//+------------------------------------------------------------------+
void PollOrders()
  {
   if(!FileIsExist(g_orders)) return;
   int h = FileOpen(g_orders, FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return;
   long size = (long)FileSize(h);
   if(size < g_cursor)
     {
      PrintFormat("courier: orders.jsonl shrank (%I64d < cursor %I64d) -- reading from the start", size, g_cursor);
      g_cursor = 0;
     }
   if(size == g_cursor) { FileClose(h); return; }
   FileSeek(h, g_cursor, SEEK_SET);
   uchar buf[];
   int got = (int)FileReadArray(h, buf, 0, (int)(size - g_cursor));
   FileClose(h);
   int last = -1;
   for(int i = got - 1; i >= 0; i--)
      if(buf[i] == '\n') { last = i; break; }
   if(last < 0) return;                         // the daemon is mid-line: next tick
   int start = 0;
   for(int i = 0; i <= last; i++)
     {
      if(buf[i] != '\n') continue;
      string line = CharArrayToString(buf, start, i - start, CP_UTF8);
      start = i + 1;
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) > 0) Handle(line);
     }
   g_cursor += last + 1;
   SaveState();
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   CrcInit();
   // the daemon's magic for this id (Python: zlib.crc32(id) & 0x7FFFFFFF)
   if(MagicOf("J4-EURUSD-20261009-0912") != 1875958774)
     {
      Print("courier: CRC self-test FAILED -- magic numbers would not match the daemon's");
      return INIT_FAILED;
     }
   if(InpDemoOnly && !AccountIsDemo())
     {
      PrintFormat("Courier: account %I64d is NOT a demo account (trade mode %I64d). Removing myself.",
                  AccountInfoInteger(ACCOUNT_LOGIN), AccountInfoInteger(ACCOUNT_TRADE_MODE));
      ExpertRemove();
      return INIT_FAILED;
     }
   g_orders = InpFolder + "\\orders.jsonl";
   g_fills  = InpFolder + "\\fills.jsonl";
   g_state  = InpFolder + "\\ea_state.txt";
   g_ids    = InpFolder + "\\ea_ids.txt";
   FolderCreate(InpFolder);
   LoadState();
   LoadIds();
   trade.SetDeviationInPoints(10);
   trade.LogLevel(LOG_LEVEL_ERRORS);
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("courier: AlgoTrading is OFF in the terminal -- orders will be rejected until it is on");
   EventSetTimer(MathMax(1, InpTimerSec));
   PrintFormat("Courier ready: account %I64d (demo), MQL5\\Files\\%s, cursor %I64d, risk %.2f%% per R, %d ids known",
               AccountInfoInteger(ACCOUNT_LOGIN), InpFolder, g_cursor, InpRiskPct, ArraySize(g_mapId));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   SaveState();
  }

void OnTimer()
  {
   PollOrders();
  }

//+------------------------------------------------------------------+
//| what happened on the account -> fills.jsonl                      |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &tx, const MqlTradeRequest &rq, const MqlTradeResult &rs)
  {
   if(tx.type == TRADE_TRANSACTION_DEAL_ADD)
     {
      if(!HistoryDealSelect(tx.deal)) return;
      ulong pos = (ulong)HistoryDealGetInteger(tx.deal, DEAL_POSITION_ID);
      string id = IdOf((ulong)HistoryDealGetInteger(tx.deal, DEAL_MAGIC));
      long entry = HistoryDealGetInteger(tx.deal, DEAL_ENTRY);
      double px = HistoryDealGetDouble(tx.deal, DEAL_PRICE);
      datetime t = (datetime)HistoryDealGetInteger(tx.deal, DEAL_TIME);
      string sym = HistoryDealGetString(tx.deal, DEAL_SYMBOL);
      long reasonCode = HistoryDealGetInteger(tx.deal, DEAL_REASON);
      if(id == "" && pos != 0) id = IdOfPosition(pos);
      if(id == "") return;                              // not the courier's (a hand trade)
      long spread = SymbolInfoInteger(sym, SYMBOL_SPREAD);
      if(entry == DEAL_ENTRY_IN)
         WriteFill(id, "filled", t, px, spread, pos, "deal " + IntegerToString((long)tx.deal));
      else if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
        {
         string why = "other";
         if(reasonCode == DEAL_REASON_SL)          why = "sl";
         else if(reasonCode == DEAL_REASON_TP)     why = "tp";
         else if(reasonCode == DEAL_REASON_SO)     why = "stop_out";
         else if(reasonCode == DEAL_REASON_CLIENT) why = "manual";
         else if(reasonCode == DEAL_REASON_MOBILE) why = "manual";
         else if(reasonCode == DEAL_REASON_WEB)    why = "manual";
         else if(reasonCode == DEAL_REASON_EXPERT) why = "expert";
         WriteFill(id, "closed", t, px, spread, pos, why);
        }
      return;
     }
   if(tx.type == TRADE_TRANSACTION_HISTORY_ADD &&
      (tx.order_state == ORDER_STATE_EXPIRED || tx.order_state == ORDER_STATE_CANCELED))
     {
      if(IsDeleting(tx.order)) return;                  // our own cancel, already written
      if(!HistoryOrderSelect(tx.order)) return;
      string id = IdOf((ulong)HistoryOrderGetInteger(tx.order, ORDER_MAGIC));
      if(id == "") return;
      WriteFill(id, "cancelled", TimeCurrent(), 0, SymbolInfoInteger(tx.symbol, SYMBOL_SPREAD), tx.order,
                tx.order_state == ORDER_STATE_EXPIRED ? "expired" : "cancelled outside the courier");
     }
  }
//+------------------------------------------------------------------+
