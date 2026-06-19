from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import pandas as pd

from athena_research.commodity_data_audit.quality import _TF_MINUTES

MT5_BAR_KEYS = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")


class CaptureState(str, Enum):
    CONFIRMED_AT_CAPTURE = "CONFIRMED_AT_CAPTURE"
    PROVISIONAL_AT_CAPTURE = "PROVISIONAL_AT_CAPTURE"


def _parse_ts(value: object) -> pd.Timestamp | None:
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1e12:
            raw /= 1000.0
        return pd.Timestamp(raw, unit="s", tz="UTC")
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def bar_bucket_end(bar: dict[str, Any], *, timeframe: str) -> pd.Timestamp | None:
    ts = _parse_ts(bar.get("time"))
    if ts is None:
        return None
    minutes = _TF_MINUTES.get(timeframe.upper(), 60)
    return ts + pd.Timedelta(minutes=minutes)


def is_bar_confirmed_at_as_of(bar: dict[str, Any], *, timeframe: str, as_of: datetime) -> bool:
    bucket_end = bar_bucket_end(bar, timeframe=timeframe)
    if bucket_end is None:
        return False
    cutoff = pd.Timestamp(as_of.astimezone(timezone.utc))
    return bucket_end <= cutoff


def capture_state_for_bar(bar: dict[str, Any], *, timeframe: str, as_of: datetime) -> CaptureState:
    if is_bar_confirmed_at_as_of(bar, timeframe=timeframe, as_of=as_of):
        return CaptureState.CONFIRMED_AT_CAPTURE
    return CaptureState.PROVISIONAL_AT_CAPTURE


def annotate_bars_at_capture(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    as_of: datetime,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for bar in bars:
        row = dict(bar)
        row["capture_state"] = capture_state_for_bar(row, timeframe=timeframe, as_of=as_of).value
        row["capture_as_of"] = as_of.astimezone(timezone.utc).isoformat()
        annotated.append(row)
    return annotated


def effective_capture_state(
    bar: dict[str, Any],
    *,
    timeframe: str,
    pinned_as_of: datetime,
) -> CaptureState:
    stored = bar.get("capture_state")
    if stored in {CaptureState.CONFIRMED_AT_CAPTURE.value, CaptureState.PROVISIONAL_AT_CAPTURE.value}:
        return CaptureState(stored)
    return capture_state_for_bar(bar, timeframe=timeframe, as_of=pinned_as_of)


def research_eligible_bars(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    pinned_as_of: datetime,
) -> list[dict[str, Any]]:
    return [
        bar
        for bar in bars
        if effective_capture_state(bar, timeframe=timeframe, pinned_as_of=pinned_as_of)
        == CaptureState.CONFIRMED_AT_CAPTURE
    ]


def provisional_bars(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    pinned_as_of: datetime,
) -> list[dict[str, Any]]:
    return [
        bar
        for bar in bars
        if effective_capture_state(bar, timeframe=timeframe, pinned_as_of=pinned_as_of)
        == CaptureState.PROVISIONAL_AT_CAPTURE
    ]


def strip_to_mt5_fields(bar: dict[str, Any]) -> dict[str, Any]:
    return {key: bar[key] for key in MT5_BAR_KEYS if key in bar}


def confirmed_research_bars_for_hash(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    pinned_as_of: datetime,
) -> list[dict[str, Any]]:
    confirmed = research_eligible_bars(bars, timeframe=timeframe, pinned_as_of=pinned_as_of)
    return [strip_to_mt5_fields(bar) for bar in confirmed]
