"""Market State Abstraction for Candle Monitoring.

Distinguishes between 'confirmed' (closed) bars and the 'forming' (open) bar
to provide diagnostic transparency for engines without delaying detection.
"""

from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict, Optional

from config import CONFIG


class MarketState(TypedDict):
    confirmed: list[dict[str, Any]]  # Series of closed bars
    forming: Optional[dict[str, Any]]  # The current open bar, if active
    is_live: bool  # True if the series includes a forming bar
    pair_display: str
    timeframe: str


def candle_timestamp_epoch(candle: dict[str, Any] | None) -> int:
    """Return normalized epoch seconds for a candle timestamp field."""
    if not candle:
        return 0
    t = candle.get("time", candle.get("datetime"))
    if t is None:
        return 0
    if isinstance(t, (int, float)):
        return int(t / 1000) if t > 1e12 else int(t)
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def get_bucket_start_epoch(tf: str, ts_s: float, offset_hours: float = 0.0) -> int:
    """Return the UTC epoch of the bar start for the given timeframe.
    
    Standard offsets:
    M5: 300s
    M15: 900s
    H1: 3600s
    H4: 14400s
    D1: 86400s
    """
    sec = {
        "M1": 60,
        "M5": 300,
        "M15": 900,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400
    }.get(tf.upper(), 3600)
    try:
        offset_s = float(offset_hours or 0.0) * 3600.0
    except (TypeError, ValueError):
        offset_s = 0.0
    return int(((float(ts_s) - offset_s) // sec) * sec + offset_s)


def _timeframe_seconds(tf: str) -> int:
    return {
        "M1": 60,
        "M5": 300,
        "M15": 900,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400,
    }.get(str(tf or "").upper(), 3600)


def _epoch_iso(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat().replace("+00:00", "Z")


def market_state_offset_hours(pair: dict[str, Any] | None, tf: str) -> float:
    """Return the bucket offset for this pair/timeframe market-state split.
    
    Offset rules:
    - MT5 forex/metals/commodities/indices: 2h offset (02/06/10/14/18/22 UTC grid)
    - MT5 stocks: 3h offset (15/19 UTC grid for US exchange session)
    - Crypto (Binance): 0h offset (24/7 UTC grid)
    """
    if str(tf or "").upper() != "H4":
        return 0.0
    if not isinstance(pair, dict):
        return 0.0
    
    source = pair.get("source", "").lower()
    asset_type = pair.get("type", "").lower()
    
    if source != "mt5":
        return 0.0
    
    # Stocks use US exchange session H4 grid (15/19 UTC = 3h offset)
    if asset_type == "stock":
        return 3.0
    
    # Forex, metals, commodities, indices use broker grid (2h offset)
    try:
        return float(CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 2.0) or 2.0)
    except (TypeError, ValueError):
        return 2.0


def split_market_state(
    candles: list[dict[str, Any]], 
    tf: str, 
    pair_display: str,
    time_now: Optional[float] = None,
    offset_hours: float = 0.0,
) -> MarketState:
    """Split a candle series into confirmed and forming components.
    
    A bar is 'forming' if its timestamp matches the current timeframe bucket.
    """
    if not candles:
        return {
            "confirmed": [],
            "forming": None,
            "is_live": False,
            "pair_display": pair_display,
            "timeframe": tf
        }

    now = time_now if time_now is not None else time.time()
    current_bucket = get_bucket_start_epoch(tf, now, offset_hours=offset_hours)
    
    last_bar = candles[-1]
    last_ts = candle_timestamp_epoch(last_bar)
    
    is_forming = (last_ts == current_bucket)
    
    if is_forming:
        return {
            "confirmed": candles[:-1],
            "forming": last_bar,
            "is_live": True,
            "pair_display": pair_display,
            "timeframe": tf
        }
    else:
        return {
            "confirmed": candles,
            "forming": None,
            "is_live": False,
            "pair_display": pair_display,
            "timeframe": tf
        }


def candle_freshness_diagnostic(
    pair: dict[str, Any],
    timeframe: str,
    candles: list[dict[str, Any]] | None,
    *,
    time_now: Optional[float] = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Return log/API-safe freshness diagnostics for one symbol/timeframe."""
    tf = str(timeframe or "").upper()
    series = list(candles or [])
    now = time_now if time_now is not None else time.time()
    offset_hours = market_state_offset_hours(pair, tf)
    expected_bucket = get_bucket_start_epoch(tf, now, offset_hours=offset_hours)
    state = split_market_state(
        series,
        tf,
        pair.get("display") or pair.get("symbol") or "",
        time_now=now,
        offset_hours=offset_hours,
    )

    last_epoch = None
    last_bucket = None
    if series and isinstance(series[-1], dict):
        last_raw_epoch = candle_timestamp_epoch(series[-1])
        if last_raw_epoch:
            last_epoch = int(last_raw_epoch)
            last_bucket = get_bucket_start_epoch(tf, last_epoch, offset_hours=offset_hours)

    bucket_lag = None
    has_current_bucket = False
    if last_bucket is not None:
        tf_seconds = _timeframe_seconds(tf)
        bucket_lag = max(0, int((int(expected_bucket) - int(last_bucket)) // tf_seconds))
        has_current_bucket = int(last_bucket) == int(expected_bucket)

    if last_bucket is None:
        severity = "missing_current_bucket"
    elif has_current_bucket:
        severity = "fresh"
    elif bucket_lag == 1:
        severity = "stale_1_bucket"
    elif bucket_lag and bucket_lag > 1:
        severity = "stale_multi_bucket"
    else:
        severity = "missing_current_bucket"

    return {
        "symbol": pair.get("symbol") or pair.get("display") or "",
        "timeframe": tf,
        "source": source or pair.get("source"),
        "lastBarEpoch": last_epoch,
        "lastBarIso": _epoch_iso(last_epoch),
        "expectedCurrentBucketEpoch": int(expected_bucket),
        "expectedCurrentBucketIso": _epoch_iso(int(expected_bucket)),
        "bucketLag": bucket_lag,
        "hasCurrentBucket": bool(has_current_bucket),
        "stalenessSeverity": severity,
        "confirmedCount": len(state.get("confirmed") or []),
        "formingCount": 1 if state.get("forming") else 0,
        "usesOffset": abs(float(offset_hours or 0.0)) > 1e-9,
        "offsetHours": float(offset_hours or 0.0),
    }


def get_tf_market_state(
    pair: dict[str, Any],
    timeframe: str,
    *,
    candles: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    fetch_candles: Callable[[dict[str, Any], str, int], Any] | None = None,
    time_now: Optional[float] = None,
) -> MarketState:
    """Normalize a timeframe candle series into raw/confirmed/forming state.

    If ``candles`` are provided, they are used directly. Otherwise this helper fetches
    candles via ``fetch_candles`` and ``limit``.
    """
    tf = str(timeframe or "").upper()
    display = pair.get("display") or pair.get("symbol") or ""
    series: list[dict[str, Any]] = list(candles or [])
    if not series and fetch_candles is not None and limit is not None:
        raw = fetch_candles(pair, tf, int(limit))
        if isinstance(raw, dict):
            raw = raw.get("candles")
        if isinstance(raw, list):
            series = raw
    offset_hours = market_state_offset_hours(pair, tf)
    return split_market_state(
        series,
        tf,
        display,
        time_now=time_now,
        offset_hours=offset_hours,
    )
