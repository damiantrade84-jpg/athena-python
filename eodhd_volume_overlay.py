"""Helpers for overlaying EODHD volume onto price candles without changing OHLC."""

from __future__ import annotations

from candles_cache import candle_time_epoch_utc, resample_from_h1

_EODHD_VOLUME_TYPES = {"stock", "commodity", "index"}
_EODHD_VOLUME_TIMEFRAMES = {"D1", "H1", "H4"}

_COMMODITY_D1_ONLY = {
    "GC=F",
    "SI=F",
    "CL=F",
    "BZ=F",
    "NatGas",
    "Copper",
}

_INDEX_INTRADAY_ONLY = {
    "NAS100",
    "^GSPC",
    "^DJI",
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
}

_EODHD_VOLUME_WHITELIST = {
    **{symbol: frozenset({"D1"}) for symbol in _COMMODITY_D1_ONLY},
    **{symbol: frozenset({"H1", "H4"}) for symbol in _INDEX_INTRADAY_ONLY},
    **{symbol: frozenset(_EODHD_VOLUME_TIMEFRAMES) for symbol in _STOCK_ALL_TFS},
}


def supports_eodhd_volume_overlay(pair: dict | None) -> bool:
    """True when the pair should use EODHD volume as a best-effort overlay."""
    if not isinstance(pair, dict):
        return False
    return str(pair.get("type") or "").lower() in _EODHD_VOLUME_TYPES


def is_eodhd_volume_whitelisted(pair: dict | None, tf: str) -> bool:
    """True when the pair/timeframe combination passed live EODHD audit checks."""
    if not supports_eodhd_volume_overlay(pair):
        return False
    symbol = str((pair or {}).get("symbol") or "")
    tf_key = str(tf or "").upper()
    allowed = _EODHD_VOLUME_WHITELIST.get(symbol)
    if not allowed or tf_key not in _EODHD_VOLUME_TIMEFRAMES:
        return False
    return tf_key in allowed


def resample_eodhd_volume_bars(
    h1_candles: list[dict] | None,
    target_tf: str,
    limit: int,
) -> list[dict] | None:
    """Resample H1 EODHD bars for volume-only overlays."""
    tf = str(target_tf or "").upper()
    if tf == "H1":
        if not h1_candles:
            return None
        return h1_candles[-limit:] if len(h1_candles) > limit else list(h1_candles)
    if tf == "H4":
        return resample_from_h1(h1_candles, "H4", limit, alignment_offset_hours=0.0)
    return None


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
