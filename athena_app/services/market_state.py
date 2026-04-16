"""Market State Abstraction for Candle Monitoring.

Distinguishes between 'confirmed' (closed) bars and the 'forming' (open) bar
to provide diagnostic transparency for engines without delaying detection.
"""

from __future__ import annotations
import time
from typing import Any, Callable, TypedDict, Optional


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


def get_bucket_start_epoch(tf: str, ts_s: float) -> int:
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
    return int(ts_s // sec) * sec


def split_market_state(
    candles: list[dict[str, Any]], 
    tf: str, 
    pair_display: str,
    time_now: Optional[float] = None
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
    current_bucket = get_bucket_start_epoch(tf, now)
    
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


def get_tf_market_state(
    pair: dict[str, Any],
    timeframe: str,
    *,
    candles: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    fetch_candles: Callable[[dict[str, Any], str, int], Any] | None = None,
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
    return split_market_state(series, tf, display)
