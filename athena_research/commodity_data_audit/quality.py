from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import pandas as pd

from athena_research.commodity_data_audit.models import CandleQualityReport

_TF_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


def _parse_timestamp(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _normalize_as_of(as_of: datetime | None) -> datetime:
    cutoff = as_of or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        return cutoff.replace(tzinfo=timezone.utc)
    return cutoff.astimezone(timezone.utc)


def candles_to_frame(candles: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        ts = _parse_timestamp(candle.get("time"))
        if ts is None:
            continue
        rows.append(
            {
                "timestamp": ts,
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "volume": candle.get("vol", candle.get("volume")),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows).sort_values("timestamp", kind="stable")
    frame = frame.drop_duplicates(subset=["timestamp"], keep="last")
    return frame.set_index("timestamp")


def estimate_missing_bars(frame: pd.DataFrame, timeframe: str) -> int:
    if frame.empty or len(frame.index) < 2:
        return 0
    minutes = _TF_MINUTES.get(timeframe.upper())
    if not minutes:
        return 0
    total_minutes = (frame.index[-1] - frame.index[0]).total_seconds() / 60.0
    expected = int(total_minutes / minutes) + 1
    return max(0, expected - len(frame))


def detect_duplicate_timestamps(timestamps: list[pd.Timestamp]) -> list[str]:
    if not timestamps:
        return []
    series = pd.Series(timestamps)
    duplicated = series[series.duplicated(keep=False)]
    return sorted({ts.isoformat() for ts in duplicated})


def detect_future_bars(
    timestamps: list[pd.Timestamp],
    *,
    as_of: datetime | None = None,
) -> int:
    if not timestamps:
        return 0
    cutoff = _normalize_as_of(as_of)
    return sum(1 for ts in timestamps if ts > pd.Timestamp(cutoff))


def _confirmed_timestamps(
    timestamps: list[pd.Timestamp],
    *,
    timeframe: str,
    as_of: datetime,
    include_forming_bar: bool,
) -> list[pd.Timestamp]:
    if include_forming_bar:
        return timestamps
    minutes = _TF_MINUTES.get(timeframe.upper(), 60)
    bucket = timedelta(minutes=minutes)
    cutoff = pd.Timestamp(as_of)
    return [ts for ts in timestamps if ts + bucket <= cutoff]


def audit_candle_series(
    candles: list[dict[str, Any]],
    *,
    timeframe: str,
    as_of: datetime | None = None,
    include_forming_bar: bool = False,
) -> CandleQualityReport:
    parsed: list[tuple[pd.Timestamp, dict[str, Any]]] = []
    for candle in candles:
        ts = _parse_timestamp(candle.get("time"))
        if ts is not None:
            parsed.append((ts, candle))

    cutoff = _normalize_as_of(as_of)
    all_timestamps = [ts for ts, _ in parsed]
    confirmed_ts = _confirmed_timestamps(
        all_timestamps,
        timeframe=timeframe,
        as_of=cutoff,
        include_forming_bar=include_forming_bar,
    )
    confirmed_set = set(confirmed_ts)
    confirmed_candles = [candle for ts, candle in parsed if ts in confirmed_set]
    frame = candles_to_frame(confirmed_candles)

    duplicates = detect_duplicate_timestamps(all_timestamps)
    missing = estimate_missing_bars(frame, timeframe)
    future_count = detect_future_bars(all_timestamps, as_of=cutoff)
    first_ts = frame.index[0].isoformat() if not frame.empty else None
    last_ts = frame.index[-1].isoformat() if not frame.empty else None
    return CandleQualityReport(
        timeframe=timeframe.upper(),
        bar_count=len(parsed),
        first_timestamp=first_ts,
        last_timestamp=last_ts,
        duplicate_timestamps=duplicates,
        missing_bar_estimate=missing,
        future_bar_count=future_count,
        incomplete_bar_present=include_forming_bar,
        timezone="UTC",
        confirmed_bar_semantics="open_time_utc; confirmed when open+bucket<=as_of",
        details={
            "duplicate_count": len(duplicates),
            "confirmed_bar_count": len(frame),
            "has_ohlc_gaps": bool(missing),
        },
    )
