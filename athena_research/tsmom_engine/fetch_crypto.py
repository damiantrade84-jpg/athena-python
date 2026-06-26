"""Deep daily crypto history via yfinance -> data_futures/ (same schema as the futures
so engine.load_fut works unchanged). BTC/LTC go back to 2014, ETH/XRP/BNB/ADA to 2017,
SOL to 2020 -> spans the 2018 (-84%) and 2022 (-77%) bear markets = real regimes, unlike
the 2-yr frozen H4 crypto (single bull) tested earlier."""
from __future__ import annotations

import os
import yfinance as yf
import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
os.makedirs(OUT, exist_ok=True)

TICKERS = {
    "btc": "BTC-USD", "eth": "ETH-USD", "ltc": "LTC-USD", "xrp": "XRP-USD",
    "bnb": "BNB-USD", "ada": "ADA-USD", "sol": "SOL-USD", "doge": "DOGE-USD",
}

rows = []
for name, tk in TICKERS.items():
    try:
        raw = yf.download(tk, period="max", interval="1d", progress=False, auto_adjust=False)
    except Exception as e:
        print(f"{name:6s} {tk:10s} FETCH FAILED: {e}"); continue
    if raw is None or len(raw) == 0:
        print(f"{name:6s} {tk:10s} EMPTY"); continue
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = pd.DataFrame({
        "time": pd.to_datetime(raw.index),
        "open": raw["Open"].values, "high": raw["High"].values,
        "low": raw["Low"].values, "close": raw["Close"].values,
        "vol": raw["Volume"].values,
    }).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df = df[df["close"] > 0].reset_index(drop=True)
    if len(df) < 250:
        print(f"{name:6s} {tk:10s} TOO SHORT n={len(df)} (skipped)"); continue
    df.to_parquet(os.path.join(OUT, f"{name}.parquet"))
    yrs = (df["time"].max() - df["time"].min()).days / 365.25
    rows.append((name, tk, len(df), yrs, df["time"].min().date(), df["time"].max().date()))

print(f"\n{'name':6s} {'tk':10s} {'n':>5s} {'yrs':>5s}  span")
for name, tk, n, yrs, lo, hi in sorted(rows, key=lambda r: -r[3]):
    print(f"{name:6s} {tk:10s} {n:5d} {yrs:5.1f}  {lo} -> {hi}")
print(f"\n{len(rows)} crypto markets saved.")
