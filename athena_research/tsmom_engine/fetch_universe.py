"""Expand the deep-history universe beyond the 8 commodities to a broad CTA-style
basket: agriculturals, equity indices, rates, and FX futures. Trend-following's
robustness historically comes from BREADTH across uncorrelated sectors, so this is
the raw material for a diversified long-only book that raises N without diluting.

Continuous front-month futures via yfinance -> data_futures/*.parquet (same schema
as fetch_data.py so engine.load_fut works unchanged). Re-fetches the commodities too
so there is a single, consistently-built source of truth."""
from __future__ import annotations

import os
import yfinance as yf
import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_futures")
os.makedirs(OUT, exist_ok=True)

# Broad managed-futures universe. Sectors kept explicit so screening can respect them.
TICKERS = {
    # metals / energy (already had these 8)
    "gold": "GC=F", "silver": "SI=F", "copper": "HG=F", "platinum": "PL=F",
    "palladium": "PA=F", "crude": "CL=F", "brent": "BZ=F", "natgas": "NG=F",
    # agriculturals
    "corn": "ZC=F", "soybean": "ZS=F", "wheat": "ZW=F", "coffee": "KC=F",
    "sugar": "SB=F", "cotton": "CT=F", "cocoa": "CC=F", "cattle": "LE=F",
    # equity indices
    "sp500": "ES=F", "nasdaq": "NQ=F", "dow": "YM=F", "russell": "RTY=F",
    # rates
    "tnote10": "ZN=F", "tbond30": "ZB=F", "tnote5": "ZF=F", "tnote2": "ZT=F",
    # fx (vs USD) + dollar index
    "eur": "6E=F", "jpy": "6J=F", "gbp": "6B=F", "aud": "6A=F",
    "cad": "6C=F", "chf": "6S=F", "dxy": "DX=F",
}

SECTOR = {
    "gold": "metal", "silver": "metal", "copper": "metal", "platinum": "metal", "palladium": "metal",
    "crude": "energy", "brent": "energy", "natgas": "energy",
    "corn": "ag", "soybean": "ag", "wheat": "ag", "coffee": "ag", "sugar": "ag", "cotton": "ag", "cocoa": "ag", "cattle": "ag",
    "sp500": "equity", "nasdaq": "equity", "dow": "equity", "russell": "equity",
    "tnote10": "rate", "tbond30": "rate", "tnote5": "rate", "tnote2": "rate",
    "eur": "fx", "jpy": "fx", "gbp": "fx", "aud": "fx", "cad": "fx", "chf": "fx", "dxy": "fx",
}

rows = []
for name, tk in TICKERS.items():
    try:
        raw = yf.download(tk, period="max", interval="1d", progress=False, auto_adjust=False)
    except Exception as e:
        print(f"{name:10s} {tk:6s} FETCH FAILED: {e}"); continue
    if raw is None or len(raw) == 0:
        print(f"{name:10s} {tk:6s} EMPTY"); continue
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
        print(f"{name:10s} {tk:6s} TOO SHORT n={len(df)} (skipped)"); continue
    df.to_parquet(os.path.join(OUT, f"{name}.parquet"))
    yrs = (df["time"].max() - df["time"].min()).days / 365.25
    rows.append((SECTOR[name], name, tk, len(df), yrs, df["time"].min().date(), df["time"].max().date()))

print(f"\n{'sector':8s} {'name':10s} {'tk':6s} {'n':>6s} {'yrs':>5s}  span")
for sec, name, tk, n, yrs, lo, hi in sorted(rows):
    print(f"{sec:8s} {name:10s} {tk:6s} {n:6d} {yrs:5.1f}  {lo} -> {hi}")
print(f"\n{len(rows)} markets saved to data_futures/")
