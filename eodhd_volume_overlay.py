"""Helpers for overlaying EODHD volume onto price candles without changing OHLC."""

from __future__ import annotations

from candles_cache import candle_time_epoch_utc
from config import CONFIG

_EODHD_VOLUME_TYPES = {"stock", "commodity", "index", "forex", "etf", "etf_bond"}
_EODHD_VOLUME_TIMEFRAMES = {"M1", "M5", "M15", "H1", "H4", "D1"}
_INTRADAY_TFS = {"M1", "M5", "M15", "H1", "H4"}

_COMMODITY_D1_ONLY = {
    "GC=F",  # retained for legacy mapping; intraday now routed via _FOREX_METALS_1M_RESAMPLE
    # Industrial metals - D1 overlay only.
    "Copper",
    "Aluminium",
    "Lead",
    "Nickel",
    "Zinc",
    # Agricultural commodities - D1 overlay only.
    "Cattle",
    "Cocoa",
    "Coffee",
    "Corn",
    "Cotton",
    "Soybeans",
    "Sugar",
    "Wheat",
}

_COMMODITY_INTRADAY_AND_D1 = {
    "SI=F",
    "CL=F",
    "BZ=F",
    "NatGas",
    "WTI Oil",
    "Brent Oil",
    "Nat Gas",
}

_FOREX_1M_RESAMPLE = {
    # Majors — originally audited
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CHF",
    "USD/MXN",
    # Remaining active forex pairs — EODHD fetches as {PAIR}.FOREX via _eodhd_intraday_ticker_for_pair
    # Falls back to MT5 tick volume silently if EODHD returns no data for a pair
    "NZD/USD",
    "EUR/GBP",
    "USD/CAD",
    "EUR/JPY",
    "GBP/JPY",
    "AUD/JPY",
    "EUR/AUD",
    "GBP/AUD",
    "USD/ZAR",
    "EUR/CHF",
    "USD/SGD",
    "AUD/CHF",
    "AUD/NZD",
    "USD/BRL",
    "USD/INR",
}

_FOREX_METALS_1M_RESAMPLE = {
    "GC=F",
    "SI=F",
    "XAU/USD",
    "XAG/USD",
    # Platinum and palladium — EODHD intraday confirmed (1m, XPTUSD.FOREX / XPDUSD.FOREX)
    "XPT/USD",
    "XPD/USD",
}

_INDEX_INTRADAY_AND_D1 = {
    # US indices (originally whitelisted)
    "NAS100",
    "^GSPC",
    "^DJI",
    "S&P 500",
    "Nasdaq",
    "NASDAQ-100",
    "Dow Jones",
    "GSPC",
    "IXIC",
    "DJI",
    "GSPC.INDX",
    "IXIC.INDX",
    "DJI.INDX",
    # UK / European / Asia-Pacific indices — display names as used in _overlay_eodhd_volume_for_scalp
    # Ticker resolution via _vendor_overrides: UK100→FTSE.INDX, DAX 40→GDAXI.INDX, etc.
    "UK100",
    "DAX 40",
    "ASX 200",
    "Nikkei 225",
    "Hang Seng",
    # Currency basket indices — ticker via _eodhd_ticker_for_pair: EURX→EURX.INDX, etc.
    # Falls back to MT5 tick volume if EODHD has no data
    "EURX",
    "JPYX",
    "USDX",
}

_STOCK_ALL_TFS = {
    "AAPL.US",
    "TSLA.US",
    "NVDA.US",
    "MSFT.US",
    "AMZN.US",
    "META.US",
    "GOOG.US",
    "JPM.US",
    "V.US",
    "XOM.US",
    "NFLX.US",
    "AMD.US",
    "CRM.US",
    "DIS.US",
    "BA.US",
    "COIN.US",
    "PYPL.US",
    "INTC.US",
    "UBER.US",
    "PLTR.US",
    "SPY.US",
    "QQQ.US",
    "GLD.US",
    "TLT.US",
    "IWM.US",
    "EEM.US",
    "XLE.US",
    "SLV.US",
    "USO.US",
    # New ETFs added to ETF_PAIRS 2026-04-03
    "DIA.US",
    "GDX.US",
    "SOXX.US",
}

_EODHD_VOLUME_WHITELIST = {
    **{symbol: frozenset({"D1"}) for symbol in _COMMODITY_D1_ONLY},
    **{symbol: frozenset(_EODHD_VOLUME_TIMEFRAMES) for symbol in _COMMODITY_INTRADAY_AND_D1},
    **{symbol: frozenset(_INTRADAY_TFS) for symbol in _FOREX_1M_RESAMPLE},
    **{symbol: frozenset(_EODHD_VOLUME_TIMEFRAMES) for symbol in _FOREX_METALS_1M_RESAMPLE},
    **{symbol: frozenset(_EODHD_VOLUME_TIMEFRAMES) for symbol in _INDEX_INTRADAY_AND_D1},
    **{symbol: frozenset(_EODHD_VOLUME_TIMEFRAMES) for symbol in _STOCK_ALL_TFS},
}


def supports_eodhd_volume_overlay(pair: dict | None) -> bool:
    """True when the pair should use EODHD volume as a best-effort overlay."""
    if not isinstance(pair, dict):
        return False
    return str(pair.get("type") or "").lower() in _EODHD_VOLUME_TYPES


def eodhd_live_overlay_asset_types() -> set[str]:
    """Asset types that keep the EODHD volume overlay in live scans."""
    cfg = CONFIG.get("SCALP_ENGINE") or {}
    types = cfg.get("EODHD_VOLUME_OVERLAY_LIVE_ASSET_TYPES", ["stock"])
    return {str(t).lower() for t in (types or [])}


def eodhd_commodity_ticker_for_pair(pair: dict | None) -> str | None:
    """Resolve configured EODHD commodity ticker without changing OHLC routing."""
    if not isinstance(pair, dict):
        return None
    if str(pair.get("type") or "").lower() != "commodity":
        return None
    tickers = CONFIG.get("EODHD_COMMODITY_TICKERS", {}) or {}
    for key in (pair.get("display"), pair.get("symbol")):
        if key in tickers:
            value = str(tickers.get(key) or "").strip()
            return value or None
    return None


def is_eodhd_volume_whitelisted(pair: dict | None, tf: str) -> bool:
    """True when the pair/timeframe combination passed live EODHD audit checks."""
    if not supports_eodhd_volume_overlay(pair):
        return False
    symbol = str((pair or {}).get("symbol") or "")
    display = str((pair or {}).get("display") or "")
    tf_key = str(tf or "").upper()
    keys = [symbol, display]
    if str((pair or {}).get("type") or "").lower() in ("stock", "etf", "etf_bond"):
        keys.extend([f"{symbol}.US", f"{display}.US"])
    allowed = None
    for key in keys:
        allowed = _EODHD_VOLUME_WHITELIST.get(key)
        if allowed:
            break
    if not allowed or tf_key not in _EODHD_VOLUME_TIMEFRAMES:
        return False
    return tf_key in allowed


_GRID_BUCKET_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}


def infer_grid_offset_seconds(base_candles: list[dict] | None, tf: str) -> int:
    """Offset of the base candle grid vs the epoch-aligned grid for ``tf``.

    MT5 bars keep the broker's bar grid after the UTC shift, so H4 bars can sit
    at 01/05/09/... UTC while epoch-aligned resampling buckets at 00/04/08/...
    — exact-timestamp overlay matching then never succeeds. Returns the
    dominant remainder of ``ts % bucket_seconds`` over the recent base bars so
    the volume resample can be aligned to the same grid; 0 when the grid is
    already epoch-aligned, unknown, or the timeframe has no fixed bucket (D1
    matching floors to calendar dates instead).
    """
    bucket = _GRID_BUCKET_SECONDS.get(str(tf or "").upper())
    if not bucket or not base_candles:
        return 0
    counts: dict[int, int] = {}
    for candle in base_candles[-20:]:
        if not isinstance(candle, dict):
            continue
        ts = candle_time_epoch_utc(candle.get("time", candle.get("datetime")))
        if ts is None:
            continue
        rem = int(ts) % bucket
        counts[rem] = counts.get(rem, 0) + 1
    if not counts:
        return 0
    return max(counts.items(), key=lambda kv: kv[1])[0]


def resample_eodhd_volume_bars(
    source_candles: list[dict] | None,
    target_tf: str,
    limit: int,
    offset_seconds: int = 0,
) -> list[dict] | None:
    """Resample EODHD bars for volume-only overlays.

    ``offset_seconds`` shifts the resample grid off the epoch alignment so the
    buckets land on the same grid as broker-shifted MT5 bars (see
    infer_grid_offset_seconds). 0 preserves the historical epoch-aligned grid.
    """
    tf = str(target_tf or "").upper()
    if tf == "M1":
        if not source_candles:
            return None
        return source_candles[-limit:] if len(source_candles) > limit else list(source_candles)
    freq = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D"}.get(tf)
    if not freq or not source_candles:
        return None
    try:
        import pandas as pd
    except Exception:
        return None
    rows = []
    for c in source_candles:
        ts = c.get("time", c.get("datetime"))
        if ts is None:
            continue
        try:
            rows.append(
                {
                    "time": ts,
                    "open": float(c.get("open")),
                    "high": float(c.get("high")),
                    "low": float(c.get("low")),
                    "close": float(c.get("close")),
                    "vol": float(c.get("vol", c.get("volume", 0)) or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").drop_duplicates(subset=["time"])
    if df.empty:
        return None
    df = df.set_index("time")
    resample_kwargs: dict = {"origin": "epoch", "label": "left", "closed": "left"}
    off = int(offset_seconds or 0)
    if off and freq != "1D":
        resample_kwargs["offset"] = pd.Timedelta(seconds=off)
    agg = (
        df.resample(freq, **resample_kwargs)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "vol": "sum"})
        .dropna(subset=["open", "close"])
    )
    out = [
        {
            "time": idx.isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "vol": float(row["vol"]),
        }
        for idx, row in agg.iterrows()
    ]
    return out[-limit:] if len(out) > limit else out


def overlay_candle_volumes(
    base_candles: list[dict] | None,
    volume_candles: list[dict] | None,
    tf: str,
) -> tuple[list[dict], int]:
    """Replace candle `vol` with EODHD volume where timestamps align."""
    if not base_candles:
        return [], 0
    if not volume_candles:
        return list(base_candles), 0

    tf_key = str(tf or "").upper()
    overlay_map: dict[int, float] = {}
    for candle in volume_candles:
        ts = candle_time_epoch_utc(candle.get("time", candle.get("datetime")))
        if ts is None:
            continue
        try:
            vol = float(candle.get("vol", candle.get("volume", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0:
            continue
        if tf_key == "D1":
            ts -= ts % 86400
        overlay_map[ts] = vol

    if not overlay_map:
        return list(base_candles), 0

    matched = 0
    merged: list[dict] = []
    for candle in base_candles:
        row = dict(candle)
        ts = candle_time_epoch_utc(candle.get("time", candle.get("datetime")))
        if ts is None:
            merged.append(row)
            continue
        if tf_key == "D1":
            ts -= ts % 86400
        vol = overlay_map.get(ts)
        if vol is not None:
            row["vol"] = vol
            matched += 1
        merged.append(row)
    return merged, matched
