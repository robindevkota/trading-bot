"""Download Binance futures 'fuel' data: 5m metrics (OI, long/short ratios)
+ funding rates. Daily zip per metrics file, monthly for funding."""
import io
import os
import sys
import zipfile
import urllib.request
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "cache")
BASE = "https://data.binance.vision/data/futures/um"


def fetch(url):
    raw = urllib.request.urlopen(url, timeout=30).read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return z.read(z.namelist()[0])


def load_metrics(sym, start="2021-06-01", end="2026-06-30"):
    path = os.path.join(CACHE, f"metrics_{sym}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    frames, miss = [], 0
    for d in pd.date_range(start, end, freq="D"):
        ds = d.strftime("%Y-%m-%d")
        url = f"{BASE}/daily/metrics/{sym}/{sym}-metrics-{ds}.zip"
        try:
            csv = fetch(url)
        except Exception:
            miss += 1
            continue
        df = pd.read_csv(io.BytesIO(csv))
        frames.append(df)
        if len(frames) % 200 == 0:
            print(f"  {sym} {ds} ({len(frames)} days)", flush=True)
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip() for c in df.columns]
    df["ts"] = pd.to_datetime(df["create_time"])
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated()]
    keep = ["sum_open_interest", "sum_open_interest_value",
            "count_toptrader_long_short_ratio",
            "sum_toptrader_long_short_ratio",
            "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
    df = df[[c for c in keep if c in df.columns]].apply(pd.to_numeric, errors="coerce")
    df.to_parquet(path)
    print(f"{sym} metrics: {len(df)} rows, {miss} missing days, "
          f"{df.index[0]} -> {df.index[-1]}", flush=True)
    return df


def load_funding(sym, start="2021-01", end="2026-06"):
    path = os.path.join(CACHE, f"funding_{sym}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    frames = []
    for m in pd.period_range(start, end, freq="M").strftime("%Y-%m"):
        url = f"{BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip"
        try:
            csv = fetch(url)
        except Exception:
            continue
        df = pd.read_csv(io.BytesIO(csv))
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip() for c in df.columns]
    tcol = "calc_time" if "calc_time" in df.columns else df.columns[0]
    fcol = "last_funding_rate" if "last_funding_rate" in df.columns else df.columns[-1]
    ts = pd.to_numeric(df[tcol], errors="coerce")
    df["ts"] = pd.to_datetime(ts.where(ts < 1e14, ts // 1000), unit="ms")
    out = df.set_index("ts")[[fcol]].rename(columns={fcol: "funding"})
    out["funding"] = pd.to_numeric(out.funding, errors="coerce")
    out = out.sort_index()
    out.to_parquet(path)
    print(f"{sym} funding: {len(out)} rows {out.index[0]} -> {out.index[-1]}", flush=True)
    return out


if __name__ == "__main__":
    for sym in ["BTCUSDT", "ETHUSDT"]:
        load_funding(sym)
        load_metrics(sym)
    print("DONE", flush=True)
