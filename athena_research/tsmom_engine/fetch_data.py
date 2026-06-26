"""Fetch deep daily futures history (commodities/metals) for multi-regime validation.
Continuous front-month futures via yfinance, cached to local parquet. ~25 yr history
spans the 2008 crash, 2011-2015 gold bear, 2020 COVID, 2022 inflation = real regimes."""
from __future__ import annotations

import os
import yfinance as yf
import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
os.makedirs(OUT, exist_ok=True)

TICKERS = {
    "gold": "GC=F", "silver": "SI=F", "copper": "HG=F",
    "crude": "CL=F", "platinum": "PL=F", "palladium": "PA=F",
    "natgas": "NG=F", "brent": "BZ=F",
}

for name, tk in TICKERS.items():
    try:
        raw = yf.download(tk, period="max", interval="1d", progress=False, auto_adjust=False)
    except Exception as e:
        print(f"{name:10s} FETCH FAILED: {e}"); continue
    if raw is None or len(raw) == 0:
        print(f"{name:10s} EMPTY"); continue
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = pd.DataFrame({
        "time": pd.to_datetime(raw.index),
        "open": raw["Open"].values, "high": raw["High"].values,
        "low": raw["Low"].values, "close": raw["Close"].values,
        "vol": raw["Volume"].values,
    }).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df = df[df["close"] > 0].reset_index(drop=True)
    df.to_parquet(os.path.join(OUT, f"{name}.parquet"))
    yrs = df["time"].dt.year
    print(f"{name:10s} {tk:6s} n={len(df):5d}  {df['time'].min().date()} -> {df['time'].max().date()}  "
          f"close[{df['close'].min():.2f}-{df['close'].max():.2f}]")
