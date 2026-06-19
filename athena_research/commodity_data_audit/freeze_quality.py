from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from athena_research.commodity_data_audit.gap_classification import (
    GapClass,
    assess_initial_truncation,
    classify_series_gaps,
    gap_report_to_dict,
)
from athena_research.commodity_data_audit.quality import (
    _TF_MINUTES,
    detect_duplicate_timestamps,
    detect_future_bars,
)
from athena_research.commodity_data_audit.quality import candles_to_frame


@dataclass
class FreezeQualityReport:
    ok: bool
    bar_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    requested_start: str
    requested_end: str
    duplicate_timestamps: list[str] = field(default_factory=list)
    non_monotonic_count: int = 0
    ohlc_violation_count: int = 0
    nan_inf_count: int = 0
    future_bar_count: int = 0
    missing_bar_estimate: int = 0
    large_gap_count: int = 0
    suspicious_discontinuity_count: int = 0
    identical_series: bool = False
    empty_series: bool = False
    insufficient_history: bool = False
    terminal_truncation_suspected: bool = False
    issues: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


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


def _bar_open_times(bars: list[dict[str, Any]]) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []
    for bar in bars:
        ts = _parse_ts(bar.get("time"))
        if ts is not None:
            out.append(ts)
    return out


def exclude_forming_bar(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    as_of: datetime,
) -> list[dict[str, Any]]:
    minutes = _TF_MINUTES.get(timeframe.upper(), 60)
    bucket = pd.Timedelta(minutes=minutes)
    cutoff_ts = pd.Timestamp(as_of.astimezone(timezone.utc))
    kept: list[dict[str, Any]] = []
    for bar in bars:
        ts = _parse_ts(bar.get("time"))
        if ts is None:
            continue
        if ts + bucket <= cutoff_ts:
            kept.append(bar)
    return kept


def filter_bars_to_requested_range(
    bars: list[dict[str, Any]],
    *,
    start_inclusive: datetime,
    end_exclusive: datetime,
) -> list[dict[str, Any]]:
    start_ts = pd.Timestamp(start_inclusive.astimezone(timezone.utc))
    end_ts = pd.Timestamp(end_exclusive.astimezone(timezone.utc))
    kept: list[dict[str, Any]] = []
    for bar in bars:
        ts = _parse_ts(bar.get("time"))
        if ts is None:
            continue
        if ts >= start_ts and ts < end_ts:
            kept.append(bar)
    return kept


def audit_freeze_bars(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    min_bars: int,
    as_of: datetime | None = None,
    qa_algorithm_version: str = "commodity_freeze_qa_v2",
) -> FreezeQualityReport:
    as_of_utc = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    all_timestamps = _bar_open_times(bars)
    future_count = detect_future_bars(all_timestamps, as_of=as_of_utc)
    confirmed = exclude_forming_bar(bars, timeframe=timeframe, as_of=as_of_utc)
    timestamps = _bar_open_times(confirmed)

    duplicates = detect_duplicate_timestamps(timestamps)
    non_monotonic = 0
    for prev, curr in zip(timestamps, timestamps[1:]):
        if curr <= prev:
            non_monotonic += 1

    ohlc_violations = 0
    nan_inf = 0
    closes: list[float] = []
    for bar in confirmed:
        o = float(bar.get("open", 0))
        h = float(bar.get("high", 0))
        l = float(bar.get("low", 0))
        c = float(bar.get("close", 0))
        for value in (o, h, l, c):
            if not math.isfinite(value):
                nan_inf += 1
        if not (l <= min(o, c) and h >= max(o, c) and l <= h):
            ohlc_violations += 1
        closes.append(c)

    gap_report = classify_series_gaps(timestamps, timeframe=timeframe)
    missing_bar_estimate = (
        gap_report.missing_bar_estimate_abnormal + gap_report.missing_bar_estimate_unknown
    )

    discontinuities = 0
    for idx in range(len(confirmed) - 1):
        prev_close = float(confirmed[idx].get("close", 0))
        next_open = float(confirmed[idx + 1].get("open", 0))
        if prev_close > 0:
            jump = abs(next_open - prev_close) / prev_close
            if jump > 0.15:
                discontinuities += 1

    identical = len(set(closes)) <= 1 if closes else True
    empty = len(confirmed) == 0
    insufficient = len(confirmed) < min_bars

    actual_first = timestamps[0].isoformat() if timestamps else None
    actual_last = timestamps[-1].isoformat() if timestamps else None
    truncation = False
    truncation_reason: str | None = None
    if timestamps:
        truncation, truncation_reason = assess_initial_truncation(
            first_timestamp=timestamps[0],
            requested_start=requested_start,
            timeframe=timeframe,
            schedule=gap_report.session_schedule or infer_session_schedule_fallback(),
        )

    issues: list[str] = []
    if duplicates:
        issues.append("duplicate_timestamps")
    if non_monotonic:
        issues.append("non_monotonic_timestamps")
    if ohlc_violations:
        issues.append("ohlc_violations")
    if nan_inf:
        issues.append("nan_or_inf")
    if future_count:
        issues.append("future_bars")
    if gap_report.counts.get(GapClass.ABNORMAL_ACTIVE_SESSION_GAP.value, 0):
        issues.append("abnormal_active_session_gaps")
    if gap_report.missing_bar_estimate_abnormal:
        issues.append("missing_bars")
    if gap_report.counts.get(GapClass.UNKNOWN_GAP.value, 0):
        issues.append("unknown_gaps_present")
    if discontinuities:
        issues.append("suspicious_price_discontinuities")
    if identical and not empty:
        issues.append("identical_series")
    if empty:
        issues.append("empty_series")
    if insufficient:
        issues.append("insufficient_history")
    if truncation:
        issues.append("terminal_truncation_suspected")

    blocking_issues = {
        "duplicate_timestamps",
        "non_monotonic_timestamps",
        "ohlc_violations",
        "nan_or_inf",
        "future_bars",
        "empty_series",
        "insufficient_history",
        "terminal_truncation_suspected",
        "abnormal_active_session_gaps",
    }
    ok = not any(issue in blocking_issues for issue in issues)

    return FreezeQualityReport(
        ok=ok,
        bar_count=len(confirmed),
        first_timestamp=actual_first,
        last_timestamp=actual_last,
        requested_start=requested_start.astimezone(timezone.utc).isoformat(),
        requested_end=requested_end.astimezone(timezone.utc).isoformat(),
        duplicate_timestamps=duplicates,
        non_monotonic_count=non_monotonic,
        ohlc_violation_count=ohlc_violations,
        nan_inf_count=nan_inf,
        future_bar_count=future_count,
        missing_bar_estimate=missing_bar_estimate,
        large_gap_count=gap_report.large_gap_count,
        suspicious_discontinuity_count=discontinuities,
        identical_series=identical and not empty,
        empty_series=empty,
        insufficient_history=insufficient,
        terminal_truncation_suspected=truncation,
        issues=issues,
        details={
            "qa_algorithm_version": qa_algorithm_version,
            "confirmed_bar_semantics": "open_time_utc; exclude bar when open+bucket>as_of",
            "as_of": as_of_utc.isoformat(),
            "gap_classification": gap_report_to_dict(gap_report),
            "missing_bar_reconciliation": {
                "blocking_missing_bar_estimate": missing_bar_estimate,
                "classified_missing_total": gap_report.missing_bar_estimate_total_classified,
                "abnormal_missing": gap_report.missing_bar_estimate_abnormal,
                "unknown_missing": gap_report.missing_bar_estimate_unknown,
                "large_gap_count": gap_report.large_gap_count,
            },
            "initial_truncation_assessment": {
                "suspected": truncation,
                "reason": truncation_reason,
            },
        },
    )


def infer_session_schedule_fallback():
    from athena_research.commodity_data_audit.gap_classification import SessionScheduleInference

    return SessionScheduleInference({}, {}, 0.0, 0, 0)


def quality_report_to_dict(report: FreezeQualityReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "bar_count": report.bar_count,
        "first_timestamp": report.first_timestamp,
        "last_timestamp": report.last_timestamp,
        "requested_start": report.requested_start,
        "requested_end": report.requested_end,
        "duplicate_timestamps": report.duplicate_timestamps,
        "non_monotonic_count": report.non_monotonic_count,
        "ohlc_violation_count": report.ohlc_violation_count,
        "nan_inf_count": report.nan_inf_count,
        "future_bar_count": report.future_bar_count,
        "missing_bar_estimate": report.missing_bar_estimate,
        "large_gap_count": report.large_gap_count,
        "suspicious_discontinuity_count": report.suspicious_discontinuity_count,
        "identical_series": report.identical_series,
        "empty_series": report.empty_series,
        "insufficient_history": report.insufficient_history,
        "terminal_truncation_suspected": report.terminal_truncation_suspected,
        "issues": report.issues,
        "details": report.details,
    }
