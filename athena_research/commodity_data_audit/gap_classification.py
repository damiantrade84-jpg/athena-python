from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import pandas as pd

from athena_research.commodity_data_audit.quality import _TF_MINUTES


class GapClass(str, Enum):
    EXPECTED_WEEKEND_CLOSURE = "EXPECTED_WEEKEND_CLOSURE"
    EXPECTED_DAILY_MAINTENANCE = "EXPECTED_DAILY_MAINTENANCE"
    HOLIDAY_OR_SESSION_CLOSURE_CANDIDATE = "HOLIDAY_OR_SESSION_CLOSURE_CANDIDATE"
    ABNORMAL_ACTIVE_SESSION_GAP = "ABNORMAL_ACTIVE_SESSION_GAP"
    UNKNOWN_GAP = "UNKNOWN_GAP"


@dataclass(frozen=True)
class ClassifiedGap:
    classification: GapClass
    prev_timestamp: str
    curr_timestamp: str
    duration_seconds: float
    missing_bar_estimate: int
    notes: tuple[str, ...] = ()


@dataclass
class SessionScheduleInference:
    active_slots: dict[tuple[int, int], bool]
    slot_confidence: dict[tuple[int, int], float]
    overall_confidence: float
    sample_bar_count: int
    sample_week_count: int
    method: str = "hour_of_week_frequency_v1"


@dataclass
class GapClassificationReport:
    gaps: list[ClassifiedGap] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    missing_bar_estimate_abnormal: int = 0
    missing_bar_estimate_unknown: int = 0
    missing_bar_estimate_total_classified: int = 0
    large_gap_count: int = 0
    session_schedule: SessionScheduleInference | None = None
    unknown_gaps: list[dict[str, Any]] = field(default_factory=list)


def _slot(ts: pd.Timestamp) -> tuple[int, int]:
    return int(ts.dayofweek), int(ts.hour)


def infer_session_schedule(timestamps: list[pd.Timestamp]) -> SessionScheduleInference:
    if not timestamps:
        return SessionScheduleInference({}, {}, 0.0, 0, 0)

    slot_counts: dict[tuple[int, int], int] = {}
    slot_weeks: dict[tuple[int, int], set[tuple[int, int]]] = {}
    all_weeks: set[tuple[int, int]] = set()
    for ts in timestamps:
        key = _slot(ts)
        slot_counts[key] = slot_counts.get(key, 0) + 1
        week_key = (int(ts.isocalendar().year), int(ts.isocalendar().week))
        all_weeks.add(week_key)
        slot_weeks.setdefault(key, set()).add(week_key)

    total_weeks = max(len(all_weeks), 1)
    active: dict[tuple[int, int], bool] = {}
    confidence: dict[tuple[int, int], float] = {}
    for key, count in slot_counts.items():
        week_ratio = len(slot_weeks.get(key, set())) / total_weeks
        confidence[key] = round(week_ratio, 4)
        active[key] = week_ratio >= 0.35 and count >= 20

    overall = round(sum(confidence.values()) / max(len(confidence), 1), 4)
    return SessionScheduleInference(
        active_slots=active,
        slot_confidence=confidence,
        overall_confidence=overall,
        sample_bar_count=len(timestamps),
        sample_week_count=total_weeks,
    )


def _gap_crosses_weekend(prev: pd.Timestamp, curr: pd.Timestamp) -> bool:
    if curr <= prev:
        return False
    if prev.dayofweek == 4 and curr.dayofweek in (0, 1) and (curr - prev) >= timedelta(hours=24):
        return True
    if prev.dayofweek >= 4 and curr.dayofweek <= 1 and (curr - prev) >= timedelta(hours=36):
        return True
    day = prev.normalize()
    end_day = curr.normalize()
    while day <= end_day:
        if day.dayofweek in (5, 6) and (curr - prev) >= timedelta(hours=12):
            return True
        day += timedelta(days=1)
    return False


def _slot_active(schedule: SessionScheduleInference, ts: pd.Timestamp) -> bool:
    return schedule.active_slots.get(_slot(ts), False)


def _missing_bars(delta: pd.Timedelta, expected_minutes: int) -> int:
    if expected_minutes <= 0:
        return 0
    ratio = delta.total_seconds() / 60.0 / expected_minutes
    if ratio <= 1.01:
        return 0
    return max(0, int(round(ratio)) - 1)


def classify_gap(
    *,
    prev: pd.Timestamp,
    curr: pd.Timestamp,
    timeframe: str,
    schedule: SessionScheduleInference,
) -> ClassifiedGap | None:
    expected_minutes = _TF_MINUTES.get(timeframe.upper(), 60)
    expected_delta = pd.Timedelta(minutes=expected_minutes)
    delta = curr - prev
    if delta <= expected_delta * 1.01:
        return None

    missing = _missing_bars(delta, expected_minutes)
    notes: list[str] = []

    if _gap_crosses_weekend(prev, curr):
        return ClassifiedGap(
            classification=GapClass.EXPECTED_WEEKEND_CLOSURE,
            prev_timestamp=prev.isoformat(),
            curr_timestamp=curr.isoformat(),
            duration_seconds=float(delta.total_seconds()),
            missing_bar_estimate=missing,
            notes=tuple(notes),
        )

    if missing == 1 and prev.dayofweek == curr.dayofweek and delta <= expected_delta * 2.5:
        notes.append("single missing bar on same weekday")
        return ClassifiedGap(
            classification=GapClass.EXPECTED_DAILY_MAINTENANCE,
            prev_timestamp=prev.isoformat(),
            curr_timestamp=curr.isoformat(),
            duration_seconds=float(delta.total_seconds()),
            missing_bar_estimate=missing,
            notes=tuple(notes),
        )

    if missing == 1 and delta <= expected_delta * 2.5:
        notes.append("single missing bar within maintenance-sized gap")
        return ClassifiedGap(
            classification=GapClass.EXPECTED_DAILY_MAINTENANCE,
            prev_timestamp=prev.isoformat(),
            curr_timestamp=curr.isoformat(),
            duration_seconds=float(delta.total_seconds()),
            missing_bar_estimate=missing,
            notes=tuple(notes),
        )

    prev_active = _slot_active(schedule, prev)
    curr_active = _slot_active(schedule, curr)
    if prev_active and curr_active and missing >= 1:
        return ClassifiedGap(
            classification=GapClass.ABNORMAL_ACTIVE_SESSION_GAP,
            prev_timestamp=prev.isoformat(),
            curr_timestamp=curr.isoformat(),
            duration_seconds=float(delta.total_seconds()),
            missing_bar_estimate=missing,
            notes=("both adjacent slots inferred active",),
        )

    if not prev_active or not curr_active:
        notes.append("adjacent to inferred inactive session slot")
        return ClassifiedGap(
            classification=GapClass.HOLIDAY_OR_SESSION_CLOSURE_CANDIDATE,
            prev_timestamp=prev.isoformat(),
            curr_timestamp=curr.isoformat(),
            duration_seconds=float(delta.total_seconds()),
            missing_bar_estimate=missing,
            notes=tuple(notes),
        )

    return ClassifiedGap(
        classification=GapClass.UNKNOWN_GAP,
        prev_timestamp=prev.isoformat(),
        curr_timestamp=curr.isoformat(),
        duration_seconds=float(delta.total_seconds()),
        missing_bar_estimate=missing,
        notes=tuple(notes),
    )


def classify_series_gaps(
    timestamps: list[pd.Timestamp],
    *,
    timeframe: str,
) -> GapClassificationReport:
    schedule = infer_session_schedule(timestamps)
    gaps: list[ClassifiedGap] = []
    counts: dict[str, int] = {member.value: 0 for member in GapClass}
    abnormal_missing = 0
    unknown_missing = 0
    unknown_gaps: list[dict[str, Any]] = []

    for prev, curr in zip(timestamps, timestamps[1:]):
        item = classify_gap(prev=prev, curr=curr, timeframe=timeframe, schedule=schedule)
        if item is None:
            continue
        gaps.append(item)
        counts[item.classification.value] += 1
        if item.classification == GapClass.ABNORMAL_ACTIVE_SESSION_GAP:
            abnormal_missing += item.missing_bar_estimate
        elif item.classification == GapClass.UNKNOWN_GAP:
            unknown_missing += item.missing_bar_estimate
            unknown_gaps.append(
                {
                    "prev_timestamp": item.prev_timestamp,
                    "curr_timestamp": item.curr_timestamp,
                    "duration_seconds": item.duration_seconds,
                    "missing_bar_estimate": item.missing_bar_estimate,
                }
            )

    total_classified_missing = sum(gap.missing_bar_estimate for gap in gaps)
    return GapClassificationReport(
        gaps=gaps,
        counts=counts,
        missing_bar_estimate_abnormal=abnormal_missing,
        missing_bar_estimate_unknown=unknown_missing,
        missing_bar_estimate_total_classified=total_classified_missing,
        large_gap_count=len(gaps),
        session_schedule=schedule,
        unknown_gaps=unknown_gaps,
    )


def assess_initial_truncation(
    *,
    first_timestamp: pd.Timestamp,
    requested_start: datetime,
    timeframe: str,
    schedule: SessionScheduleInference,
) -> tuple[bool, str | None]:
    start = pd.Timestamp(requested_start.astimezone(timezone.utc))
    if first_timestamp <= start:
        return False, None

    initial_gap = first_timestamp - start
    expected_minutes = _TF_MINUTES.get(timeframe.upper(), 60)
    expected_delta = pd.Timedelta(minutes=expected_minutes)
    if initial_gap <= expected_delta * 1.01:
        return False, None

    if initial_gap <= timedelta(days=3):
        return False, GapClass.HOLIDAY_OR_SESSION_CLOSURE_CANDIDATE.value

    if initial_gap > timedelta(days=7):
        return True, "material_history_missing_at_series_start"

    synthetic = classify_gap(prev=start, curr=first_timestamp, timeframe=timeframe, schedule=schedule)
    if synthetic is None:
        return False, None

    if synthetic.classification in {
        GapClass.EXPECTED_WEEKEND_CLOSURE,
        GapClass.EXPECTED_DAILY_MAINTENANCE,
        GapClass.HOLIDAY_OR_SESSION_CLOSURE_CANDIDATE,
    }:
        return False, synthetic.classification.value

    if synthetic.classification == GapClass.ABNORMAL_ACTIVE_SESSION_GAP:
        return True, synthetic.classification.value

    return False, synthetic.classification.value


def gap_report_to_dict(report: GapClassificationReport) -> dict[str, Any]:
    schedule = report.session_schedule
    return {
        "counts": dict(report.counts),
        "large_gap_count": report.large_gap_count,
        "missing_bar_estimate_abnormal": report.missing_bar_estimate_abnormal,
        "missing_bar_estimate_unknown": report.missing_bar_estimate_unknown,
        "missing_bar_estimate_total_classified": report.missing_bar_estimate_total_classified,
        "unknown_gaps": list(report.unknown_gaps),
        "session_schedule_inference": {
            "method": schedule.method if schedule else "hour_of_week_frequency_v1",
            "overall_confidence": schedule.overall_confidence if schedule else 0.0,
            "sample_bar_count": schedule.sample_bar_count if schedule else 0,
            "sample_week_count": schedule.sample_week_count if schedule else 0,
            "active_slot_count": sum(1 for v in (schedule.active_slots.values() if schedule else []) if v),
        },
        "gaps_sample": [
            {
                "classification": gap.classification.value,
                "prev_timestamp": gap.prev_timestamp,
                "curr_timestamp": gap.curr_timestamp,
                "duration_seconds": gap.duration_seconds,
                "missing_bar_estimate": gap.missing_bar_estimate,
                "notes": list(gap.notes),
            }
            for gap in report.gaps[:50]
        ],
    }
