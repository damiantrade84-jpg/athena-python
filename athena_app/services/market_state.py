"""Market State Abstraction for Candle Monitoring.

Distinguishes between 'confirmed' (closed) bars and the 'forming' (open) bar
to provide diagnostic transparency for engines without delaying detection.
"""

from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict, Optional

from config import CONFIG, get_d1_resample_offset_hours


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


def _mt5_intraday_calendar_gap_grace_buckets(tf: str) -> int:
    """Max H4/H1 buckets of lag tolerated as session calendar gaps."""
    cfg = CONFIG.get("MT5_INTRADAY_CALENDAR_GAP_GRACE_BUCKETS") or {}
    if isinstance(cfg, dict):
        raw = cfg.get(str(tf or "").upper()) or cfg.get("default")
    else:
        raw = cfg
    try:
        cap = int(raw or 0)
    except (TypeError, ValueError):
        cap = 0
    return max(0, cap)


def _mt5_intraday_calendar_gap_types() -> set[str]:
    raw = CONFIG.get(
        "MT5_INTRADAY_CALENDAR_GAP_TYPES",
        ["stock", "index", "commodity", "etf", "etf_bond"],
    )
    if isinstance(raw, str):
        raw = [raw]
    return {str(x).lower() for x in (raw or []) if str(x).strip()}


def _mt5_d1_calendar_gap_grace_buckets() -> int:
    """Max D1 buckets of lag tolerated as session calendar gaps (Sat/Sun, etc.)."""
    raw = CONFIG.get("MT5_D1_CALENDAR_GAP_GRACE_BUCKETS")
    if raw is None:
        raw = CONFIG.get("FOREX_D1_MULTI_BUCKET_CALENDAR_GAP_GRACE_BUCKETS", 4)
    try:
        cap = int(raw or 0)
    except (TypeError, ValueError):
        cap = 0
    return max(0, cap)


def _mt5_d1_calendar_gap_excluded_types() -> set[str]:
    """Asset types excluded from D1 gap grace (e.g. 24h crypto)."""
    raw = CONFIG.get("MT5_D1_CALENDAR_GAP_EXCLUDE_TYPES", ["crypto"])
    if isinstance(raw, str):
        raw = [raw]
    return {str(x).lower() for x in (raw or []) if str(x).strip()}


def market_state_offset_hours(pair: dict[str, Any] | None, tf: str) -> float:
    """Return the bucket offset for this pair/timeframe market-state split.
    
    Offset rules:
    - MT5 D1 grid: configured by D1_RESAMPLE_OFFSET_HOURS (default 0.0 = UTC 00:00)
    - MT5 non-stock H4 grid: configured by FOREX_H4_RESAMPLE_OFFSET_HOURS
    - MT5 stocks: 3h offset (15/19 UTC grid for US exchange session)
    - Crypto (Binance): 0h offset (24/7 UTC grid)
    """
    tf_upper = str(tf or "").upper()
    
    # D1 offset for MT5 brokers (configurable for session roll differences)
    if tf_upper == "D1":
        if isinstance(pair, dict) and str(pair.get("source") or "").lower() == "mt5":
            return get_d1_resample_offset_hours()
        return 0.0
    
    if tf_upper != "H4":
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
    
    # Forex, metals, commodities, indices use the broker H4 grid.
    # Prefer explicit config; otherwise derive from MT5_BROKER_UTC_OFFSET:
    # formula: (24 - broker_offset) % 4  (GMT+3 → 1h, GMT+2 → 2h).
    _explicit = CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS")
    if _explicit is not None:
        try:
            return float(_explicit)
        except (TypeError, ValueError):
            pass
    # Auto-derive from broker UTC offset
    try:
        _broker_off = int(CONFIG.get("MT5_BROKER_UTC_OFFSET", 3) or 3)
        return float((24 - _broker_off) % 4)
    except (TypeError, ValueError):
        return 2.0


def trim_mt5_d1_broker_session_ahead_tail(
    pair: dict[str, Any],
    tf: str,
    candles: list[dict[str, Any]],
    *,
    time_now: Optional[float] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Drop a single trailing D1 bar when MT5 stamps it one UTC bucket ahead of wall-clock.

    Some brokers expose the *forming* daily as open time = session roll (e.g. Sydney)
    while Athena's D1 grid stays UTC 00:00 per policy. Then the latest bar may read as
    ``expected_current_bucket + 86400`` even though the true UTC forming bucket exists
    earlier in the series. Removing the erroneous tail restores freshness checks and
    market-state split without changing scoring gates in config.

    Returns ``(series, trimmed)`` where ``trimmed`` is True iff the last bar was removed.
    """
    if str(tf or "").upper() != "D1":
        return list(candles or []), False
    if not isinstance(pair, dict) or str(pair.get("source") or "").lower() != "mt5":
        return list(candles or []), False

    series = list(candles or [])
    if len(series) < 2:
        return series, False

    now = float(time_now if time_now is not None else time.time())
    # Use configured D1 offset for broker session alignment (e.g., Pepperstone Sydney roll).
    offset_hours = float(get_d1_resample_offset_hours())
    expected_bucket = get_bucket_start_epoch("D1", now, offset_hours=offset_hours)

    last_epoch = candle_timestamp_epoch(series[-1])
    if not last_epoch:
        return series, False
    last_bucket = get_bucket_start_epoch("D1", float(last_epoch), offset_hours=offset_hours)
    if int(last_bucket) != int(expected_bucket) + 86400:
        return series, False

    for c in series[:-1]:
        e = candle_timestamp_epoch(c)
        if not e:
            continue
        b = get_bucket_start_epoch("D1", float(e), offset_hours=offset_hours)
        if int(b) == int(expected_bucket):
            return series[:-1], True

    return series, False


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
    series, d1_session_trimmed = trim_mt5_d1_broker_session_ahead_tail(
        pair, tf, series, time_now=now
    )
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

    if (
        severity == "stale_multi_bucket"
        and tf in ("H4", "H1")
        and str(pair.get("source") or "").lower() == "mt5"
        and str(pair.get("type") or "").lower() in _mt5_intraday_calendar_gap_types()
        and bucket_lag is not None
        and last_bucket is not None
    ):
        grace_cap = _mt5_intraday_calendar_gap_grace_buckets(tf)
        blag = int(bucket_lag)
        if grace_cap >= 2 and 2 <= blag <= grace_cap:
            severity = "intraday_calendar_gap_policy_ok"

    if (
        severity == "stale_multi_bucket"
        and tf == "D1"
        and str(pair.get("source") or "").lower() == "mt5"
        and str(pair.get("type") or "").lower() not in _mt5_d1_calendar_gap_excluded_types()
        and bucket_lag is not None
        and last_bucket is not None
    ):
        grace_cap = _mt5_d1_calendar_gap_grace_buckets()
        blag = int(bucket_lag)
        # bucket_lag is already (expected-last)/86400 on D1; cap bounds false "ok" when feed is hollow.
        if grace_cap >= 2 and 2 <= blag <= grace_cap:
            severity = "d1_calendar_gap_policy_ok"
            # #region agent log
            try:
                from athena_app.debug_ndjson_agent import append_agent_ndjson

                append_agent_ndjson(
                    {
                        "hypothesisId": "H_d1_calendar_grace",
                        "location": "market_state.candle_freshness_diagnostic",
                        "message": "d1_calendar_gap_policy_ok",
                        "runId": "post-fix",
                        "data": {
                            "pairDisplay": pair.get("display"),
                            "pairType": pair.get("type"),
                            "bucketLag": blag,
                            "graceCap": grace_cap,
                            "lastBarIso": _epoch_iso(last_epoch),
                            "expectedIso": _epoch_iso(int(expected_bucket)),
                        },
                    }
                )
            except Exception:
                pass
            # #endregion

    out = {
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
    if d1_session_trimmed:
        out["mt5D1SessionAheadTrimmed"] = True

    # #region agent log
    if severity == "stale_multi_bucket":
        try:
            from athena_app.debug_ndjson_agent import append_agent_ndjson

            append_agent_ndjson(
                {
                    "hypothesisId": "H_multi_bucket_diag",
                    "location": "market_state.candle_freshness_diagnostic",
                    "message": "stale_multi_bucket",
                    "runId": "post-fix",
                    "data": {
                        "pairDisplay": pair.get("display"),
                        "pairSource": pair.get("source"),
                        "tf": tf,
                        "severity": severity,
                        "bucketLag": bucket_lag,
                        "offsetHours": float(offset_hours or 0.0),
                        "lastBarEpoch": last_epoch,
                        "lastBucketEpoch": last_bucket,
                        "expectedBucketEpoch": int(expected_bucket),
                        "d1Trimmed": bool(d1_session_trimmed),
                        "candleCount": len(series),
                        "lastBarIso": out.get("lastBarIso"),
                        "expectedIso": out.get("expectedCurrentBucketIso"),
                        "wallNowEpoch": int(now),
                    },
                }
            )
        except Exception:
            pass
    # #endregion
    return out


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
    now_ts = time_now if time_now is not None else time.time()
    if not series and fetch_candles is not None and limit is not None:
        raw = fetch_candles(pair, tf, int(limit))
        if isinstance(raw, dict):
            raw = raw.get("candles")
        if isinstance(raw, list):
            series = raw
    series, _ = trim_mt5_d1_broker_session_ahead_tail(pair, tf, series, time_now=now_ts)
    offset_hours = market_state_offset_hours(pair, tf)
    return split_market_state(
        series,
        tf,
        display,
        time_now=time_now,
        offset_hours=offset_hours,
    )
