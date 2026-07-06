"""Download Binance monthly 15m klines (public archive, no API key)."""
import io
import os
import zipfile
import urllib.request
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "cache")
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_vol", "n_trades", "taker_buy_vol", "taker_buy_quote", "ignore"]


def month_range(start="2020-01", end="2026-06"):
    return [d.strftime("%Y-%m") for d in
            pd.period_range(start, end, freq="M").to_timestamp()]


def load(symbol="BTCUSDT", tf="15m", start="2020-01", end="2026-06"):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{symbol}_{tf}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)

    frames = []
    for m in month_range(start, end):
        url = (f"https://data.binance.vision/data/spot/monthly/klines/"
               f"{symbol}/{tf}/{symbol}-{tf}-{m}.zip")
        try:
            raw = urllib.request.urlopen(url, timeout=30).read()
        except Exception as e:
            print(f"  skip {m}: {e}")
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            csv = z.read(z.namelist()[0])
        df = pd.read_csv(io.BytesIO(csv), header=None, names=COLS)
        # newer files sometimes carry a header row
        df = df[pd.to_numeric(df.open_time, errors="coerce").notna()]
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)
    for c in ["open", "high", "low", "close", "volume", "quote_vol",
              "n_trades", "taker_buy_vol"]:
        df[c] = pd.to_numeric(df[c])
    ot = pd.to_numeric(df.open_time)
    ot = ot.where(ot < 1e14, ot // 1000)  # 2025+ files switched to microseconds
    df.index = pd.to_datetime(ot, unit="ms")
    df = df[["open", "high", "low", "close", "volume", "n_trades",
             "taker_buy_vol"]].sort_index()
    df = df[~df.index.duplicated()]
    df.to_parquet(path)
    print(f"{symbol} {tf}: {len(df)} bars {df.index[0]} -> {df.index[-1]}")
    return df


if __name__ == "__main__":
    for sym in ["BTCUSDT", "ETHUSDT"]:
        load(sym)
