"""Daily OHLC data download + local parquet cache (Yahoo Finance)."""
import os
import pandas as pd
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

UNIVERSE = {
    # ticker: (asset_class, per-side cost in bps)
    "BTC-USD":  ("crypto", 10.0),
    "ETH-USD":  ("crypto", 10.0),
    "SPY":      ("stock", 3.0),
    "QQQ":      ("stock", 3.0),
    "AAPL":     ("stock", 5.0),
    "NVDA":     ("stock", 5.0),
    "EURUSD=X": ("forex", 1.5),
    "GBPUSD=X": ("forex", 2.0),
    "USDJPY=X": ("forex", 1.5),
    "AUDUSD=X": ("forex", 2.0),
    "GC=F":     ("futures", 3.0),
}


def load(ticker: str, refresh: bool = False) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, ticker.replace("=", "_") + ".parquet")
    if os.path.exists(path) and not refresh:
        return pd.read_parquet(path)

    df = yf.download(ticker, period="max", interval="1d",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df = df[(df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.to_parquet(path)
    return df
