# Courier expert -- places on a DEMO what the Backtester decided

`Courier_EA.mq5` is the MT5 half of the courier (spec:
`Desktop/backtest/walkthrough/COURIER-PLAN.md`). The Python daemon in the
backtest repo (`python -m trading.courier`) runs the J4 signal on closed M1
bars, keeps the paper book, and writes commands to a file. This expert reads
that file and places / cancels pending limit orders. **It has no opinion**:
no candles, no levels, no stop management. Every rule is in Python.

## Install (once)

1. Copy `Courier_EA.mq5` (or the compiled `Courier_EA.ex5`) into the
   terminal's `MQL5\Experts\` folder (File -> Open Data Folder in MT5) and
   compile it in MetaEditor if you copied the source.
2. Log the terminal into the **DEMO** account. With `InpDemoOnly=true` (the
   default) the expert refuses any other account: it prints why and removes
   itself -- at start, and again before every order.
3. Turn **Algo Trading** on (toolbar button).
4. Open ONE chart (any symbol, any timeframe) and attach `Courier_EA`. One
   instance trades every pair in the file -- do not attach it twice.
5. Leave the terminal running. The daemon is a Windows logon task
   (`tools/install_courier_task.ps1` in the backtest repo).

## Where the files are -- no junction needed

The expert reads and writes `MQL5\Files\courier\` of its own terminal (the
only folder an expert can open without `FILE_COMMON`):

| file | written by | read by |
|---|---|---|
| `orders.jsonl` | the daemon | the expert |
| `fills.jsonl` | the expert | the daemon |
| `ea_state.txt` | the expert | the expert (cursor, week balance) |
| `ea_ids.txt` | the expert | the expert (id list for magic -> id) |

The daemon finds that folder itself: it asks the attached terminal
`mt5.terminal_info().data_path` and writes `<data_path>\MQL5\Files\courier\`
directly, and remembers it in `%LOCALAPPDATA%\Backtester\courier\state.json`.
So nothing has to be copied or linked -- but the daemon and the expert must
talk to the **same terminal installation**. (Override for testing:
`BACKTESTER_COURIER_CHANNEL=<folder>`.) The daemon's own state, journals,
log and reports stay in `%LOCALAPPDATA%\Backtester\courier\`.

## The protocol

One flat JSON object per line, ASCII, server time everywhere:

```
orders.jsonl  {"id":"J4-EURUSD-20261009-0912","t":"...","cmd":"place","symbol":"EURUSD","side":"buy",
               "price":1.1,"sl":1.099,"tp":1.102,"expiry":"2026-10-10T09:12:00","comment":"J4-2R"}
               {"id":"...","cmd":"cancel","comment":"<why>", ...}
fills.jsonl   {"id":"...","t":"...","event":"placed|filled|cancelled|closed|rejected",
               "price":1.099,"spread_points":7,"ticket":123,"reason":"..."}
```

* **place** -> `BUY_LIMIT` / `SELL_LIMIT` with SL, TP and the expiry
  (`ORDER_TIME_SPECIFIED`; GTC if the symbol does not allow it -- the
  daemon's cancel then ends it). Magic = CRC-32 of the id masked to 31 bits
  (the daemon's `protocol.magic_of`, self-tested at start); comment = the id.
* **cancel** -> deletes the pending order with that magic. Nothing to cancel
  (already filled / expired) is reported as `rejected` with the reason.
* A command whose expiry has already passed is NOT placed: `rejected`,
  reason "stale" (the PC was off).
* **filled / closed** come from `OnTradeTransaction` (`DEAL_ADD`), with the
  deal price and `SYMBOL_SPREAD` at that moment; `closed` carries sl / tp /
  manual / stop_out. An order the server expires is reported `cancelled`,
  reason "expired".
* Restart-safe: the byte cursor in `ea_state.txt` means nothing is replayed;
  a place whose magic is already on the account is skipped anyway.

## Sizing

One R = `InpRiskPct` (0.5 %) of the balance taken on the first order of each
week (the month's rule: "recomputed weekly"). Lots = risk / (stop distance in
ticks x tick value), rounded DOWN to the lot step, capped so the margin stays
under `InpMarginUse` (90 %) of free margin. Below the minimum lot the order
is `rejected` -- never rounded up.

## Inputs

| input | default | |
|---|---|---|
| `InpDemoOnly` | true | refuse / remove on a non-demo account |
| `InpRiskPct` | 0.5 | % of the week's opening balance per R |
| `InpFolder` | courier | `MQL5\Files\<folder>` |
| `InpSymbolSuffix` | "" | broker suffix (MetaQuotes-Demo has none) |
| `InpMarginUse` | 0.9 | share of free margin one order may use |
| `InpTimerSec` | 1 | poll period |

## The month (fixed before it starts)

J4 at 1:2, every signal, four pairs (EURUSD, GBPUSD, AUDUSD, USDJPY), no hand
on the demo account for 30 days (a manual order carries no courier magic and
is excluded). The daily reconcile (`python -m trading.courier.reconcile`,
23:30) compares the demo with the paper book and the backtest's +0.23R.
