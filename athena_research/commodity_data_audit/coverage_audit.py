from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

import pandas as pd

from athena_research.commodity_data_audit.quality import _TF_MINUTES


class CoverageEra(str, Enum):
    DEGRADED_UNUSABLE = "DEGRADED_UNUSABLE"
    TRANSITIONAL = "TRANSITIONAL"
    RELIABLE_H4 = "RELIABLE_H4"


class HistoricalDegradationClass(str, Enum):
    HISTORICAL_INTRADAY_DEGRADATION = "HISTORICAL_INTRADAY_DEGRADATION"


RELIABLE_COMPLETENESS_THRESHOLD = 0.95
TRANSITIONAL_COMPLETENESS_THRESHOLD = 0.50
ROLLING_WINDOWS_TRADING_DAYS = (20, 60, 120)
SUSTAINED_DEGRADATION_THRESHOLD = 0.90
SUSTAINED_DEGRADATION_MIN_DAYS = 20
WARMUP_TRADING_DAYS_AFTER_RELIABLE = 120
MIDNIGHT_ONLY_MAX_BARS = 2
MIDNIGHT_HOUR = 0


def _parse_ts(value: object) -> pd.Timestamp:
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1e12:
            raw /= 1000.0
        return pd.Timestamp(raw, unit="s", tz="UTC")
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _is_weekday(ts: pd.Timestamp) -> bool:
    return int(ts.dayofweek) < 5


@dataclass
class DailyCoverage:
    date: str
    weekday: str
    era: CoverageEra
    observed_slots: list[int]
    expected_slots: list[int]
    observed_slot_count: int
    expected_slot_count: int
    completeness_ratio: float
    degradation_class: str | None = None


@dataclass
class CoverageAuditReport:
    expected_recurring_slots_utc: list[int]
    provider_actual_start: str | None
    reliable_research_start: str | None
    warmup_adjusted_usable_start: str | None
    excluded_bar_count: int
    excluded_day_count: int
    exclusion_reason: str
    reliable_era_bar_count: int
    reliable_era_gap_count: int
    empirical_gate: str
    daily: list[DailyCoverage] = field(default_factory=list)
    rolling_completeness: dict[str, dict[str, float | None]] = field(default_factory=dict)
    yearly_completeness: dict[str, float] = field(default_factory=dict)
    era_summary: dict[str, int] = field(default_factory=dict)
    midnight_d1_comparison: dict[str, Any] = field(default_factory=dict)
    research_eligibility: dict[str, Any] = field(default_factory=dict)


def infer_expected_slots_from_reliable_tail(
    bars: list[dict[str, Any]],
    *,
    reliable_start: pd.Timestamp | None = None,
    tail_fraction: float = 0.35,
) -> list[int]:
    timestamps = [_parse_ts(bar["time"]) for bar in bars]
    if reliable_start is not None:
        timestamps = [ts for ts in timestamps if ts >= reliable_start]
    elif len(timestamps) > 100:
        cut = int(len(timestamps) * (1.0 - tail_fraction))
        timestamps = timestamps[cut:]
    hour_counts: dict[int, int] = {}
    for ts in timestamps:
        if not _is_weekday(ts):
            continue
        hour_counts[int(ts.hour)] = hour_counts.get(int(ts.hour), 0) + 1
    if not hour_counts:
        return [0, 4, 8, 12, 16, 20]
    ranked = sorted(hour_counts.items(), key=lambda item: (-item[1], item[0]))
    top_hours = [hour for hour, count in ranked if count >= max(ranked[0][1] * 0.5, 20)]
    if len(top_hours) < 4:
        return [0, 4, 8, 12, 16, 20]
    return sorted(top_hours)


def _build_daily_frame(bars: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for bar in bars:
        ts = _parse_ts(bar["time"])
        rows.append(
            {
                "timestamp": ts,
                "date": ts.normalize(),
                "weekday": ts.day_name(),
                "hour": int(ts.hour),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "tick_volume": int(bar.get("tick_volume", 0) or 0),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("timestamp")


def _classify_day_era(
    day: DailyCoverage,
    *,
    reliable_idx: int | None,
    idx: int,
    trading_days: list[DailyCoverage],
) -> CoverageEra:
    if day.degradation_class == HistoricalDegradationClass.HISTORICAL_INTRADAY_DEGRADATION.value:
        if reliable_idx is not None and idx >= reliable_idx and day.completeness_ratio >= RELIABLE_COMPLETENESS_THRESHOLD:
            if not _detect_sustained_degradation(trading_days, start_idx=idx):
                return CoverageEra.RELIABLE_H4
        if reliable_idx is None or idx < reliable_idx:
            return CoverageEra.DEGRADED_UNUSABLE
    if reliable_idx is None or idx < reliable_idx:
        if day.completeness_ratio < TRANSITIONAL_COMPLETENESS_THRESHOLD:
            return CoverageEra.DEGRADED_UNUSABLE
        return CoverageEra.TRANSITIONAL
    if _detect_sustained_degradation(trading_days, start_idx=idx):
        return CoverageEra.DEGRADED_UNUSABLE
    if day.completeness_ratio >= RELIABLE_COMPLETENESS_THRESHOLD:
        return CoverageEra.RELIABLE_H4
    if day.completeness_ratio >= TRANSITIONAL_COMPLETENESS_THRESHOLD:
        return CoverageEra.TRANSITIONAL
    return CoverageEra.DEGRADED_UNUSABLE


def _rolling_means(trading_days: list[DailyCoverage], window: int) -> dict[str, float | None]:
    values = [day.completeness_ratio for day in trading_days]
    out: dict[str, float | None] = {}
    for idx, day in enumerate(trading_days):
        start = max(0, idx - window + 1)
        chunk = values[start : idx + 1]
        out[day.date] = round(sum(chunk) / len(chunk), 4) if chunk else None
    return out


def _detect_sustained_degradation(
    trading_days: list[DailyCoverage],
    *,
    start_idx: int,
    window: int = SUSTAINED_DEGRADATION_MIN_DAYS,
    threshold: float = SUSTAINED_DEGRADATION_THRESHOLD,
) -> bool:
    if start_idx + window > len(trading_days):
        return False
    chunk = trading_days[start_idx : start_idx + window]
    return all(day.completeness_ratio < threshold for day in chunk)


def _era_day_counts(days: list[DailyCoverage]) -> dict[str, int]:
    out: dict[str, int] = {member.value: 0 for member in CoverageEra}
    for day in days:
        out[day.era.value] += 1
    return out


def detect_reliable_research_start(
    trading_days: list[DailyCoverage],
    rolling_120: dict[str, float | None],
) -> pd.Timestamp | None:
    for idx, day in enumerate(trading_days):
        if day.completeness_ratio < RELIABLE_COMPLETENESS_THRESHOLD:
            continue
        rolling = rolling_120.get(day.date)
        if rolling is None or rolling < RELIABLE_COMPLETENESS_THRESHOLD:
            continue
        degraded_later = False
        for later_idx in range(idx, len(trading_days) - SUSTAINED_DEGRADATION_MIN_DAYS + 1):
            if _detect_sustained_degradation(trading_days, start_idx=later_idx):
                degraded_later = True
                break
        if not degraded_later:
            return pd.Timestamp(day.date, tz="UTC")
    return None


def compare_midnight_h4_to_d1(
    bars: list[dict[str, Any]],
    *,
    d1_bars: list[dict[str, Any]] | None,
    era_end: pd.Timestamp,
) -> dict[str, Any]:
    frame = _build_daily_frame(bars)
    if frame.empty:
        return {"status": "no_h4_bars"}

    era_end_ts = era_end if isinstance(era_end, pd.Timestamp) else _parse_ts(era_end)
    degraded = frame[frame["date"] <= era_end_ts.normalize()]
    d1_by_date: dict[pd.Timestamp, dict[str, Any]] = {}
    if d1_bars:
        for bar in d1_bars:
            ts = _parse_ts(bar["time"])
            d1_by_date[ts.normalize()] = bar

    comparisons: list[dict[str, Any]] = []
    midnight_only_days = 0
    for date, group in degraded.groupby("date"):
        if not _is_weekday(pd.Timestamp(date)):
            continue
        hours = sorted(int(v) for v in group["hour"].tolist())
        if len(hours) > MIDNIGHT_ONLY_MAX_BARS:
            continue
        if MIDNIGHT_HOUR not in hours:
            continue
        midnight_only_days += 1
        h4 = group[group["hour"] == MIDNIGHT_HOUR].iloc[0]
        d1 = d1_by_date.get(pd.Timestamp(date))
        if d1 is None:
            continue
        h4_range = float(h4["high"]) - float(h4["low"])
        d1_range = float(d1["high"]) - float(d1["low"])
        comparisons.append(
            {
                "date": pd.Timestamp(date).date().isoformat(),
                "h4_open": float(h4["open"]),
                "h4_high": float(h4["high"]),
                "h4_low": float(h4["low"]),
                "h4_close": float(h4["close"]),
                "d1_open": float(d1["open"]),
                "d1_high": float(d1["high"]),
                "d1_low": float(d1["low"]),
                "d1_close": float(d1["close"]),
                "ohlc_near_equal": all(
                    abs(float(h4[field]) - float(d1[field])) <= max(abs(float(d1[field])) * 0.002, 0.05)
                    for field in ("open", "high", "low", "close")
                ),
                "range_ratio_h4_over_d1": round(h4_range / d1_range, 4) if d1_range else None,
                "volume_ratio_h4_over_d1": round(float(h4["tick_volume"]) / float(d1["tick_volume"]), 4)
                if float(d1.get("tick_volume", 0) or 0) > 0
                else None,
            }
        )

    near_equal = sum(1 for row in comparisons if row["ohlc_near_equal"])
    range_ratios = [row["range_ratio_h4_over_d1"] for row in comparisons if row["range_ratio_h4_over_d1"] is not None]
    return {
        "status": "ok" if comparisons else "no_d1_overlap",
        "midnight_only_weekday_days": midnight_only_days,
        "compared_days": len(comparisons),
        "ohlc_near_equal_count": near_equal,
        "ohlc_near_equal_share": round(near_equal / len(comparisons), 4) if comparisons else None,
        "median_range_ratio_h4_over_d1": float(pd.Series(range_ratios).median()) if range_ratios else None,
        "interpretation": _interpret_midnight_d1(comparisons, midnight_only_days),
        "sample": comparisons[:20],
    }


def _interpret_midnight_d1(comparisons: list[dict[str, Any]], midnight_only_days: int) -> str:
    if not comparisons:
        return "Insufficient D1 overlap to classify midnight-only H4 bars."
    near_equal_share = sum(1 for row in comparisons if row["ohlc_near_equal"]) / len(comparisons)
    median_range = pd.Series([row["range_ratio_h4_over_d1"] for row in comparisons if row["range_ratio_h4_over_d1"]]).median()
    if near_equal_share >= 0.8 and median_range is not None and 0.8 <= float(median_range) <= 1.2:
        return (
            "Midnight-only H4 bars in the degraded era closely match same-day D1 OHLC/range — "
            "likely daily aggregate placeholders or isolated archival bars, not full intraday H4 coverage."
        )
    if near_equal_share >= 0.5:
        return "Partial OHLC alignment with D1; mixed archival artefact and incomplete intraday history."
    return "Midnight-only H4 bars do not consistently match D1; treat as provider-history incompleteness rather than daily aggregates."


def audit_h4_coverage(
    bars: list[dict[str, Any]],
    *,
    min_reliable_bars: int,
    d1_bars: list[dict[str, Any]] | None = None,
    shared_absence_evidence: dict[str, Any] | None = None,
) -> CoverageAuditReport:
    frame = _build_daily_frame(bars)
    if frame.empty:
        return CoverageAuditReport(
            expected_recurring_slots_utc=[0, 4, 8, 12, 16, 20],
            provider_actual_start=None,
            reliable_research_start=None,
            warmup_adjusted_usable_start=None,
            excluded_bar_count=0,
            excluded_day_count=0,
            exclusion_reason=HistoricalDegradationClass.HISTORICAL_INTRADAY_DEGRADATION.value,
            reliable_era_bar_count=0,
            reliable_era_gap_count=0,
            empirical_gate="BLOCKED_INSUFFICIENT_RELIABLE_HISTORY",
        )

    provisional_expected = infer_expected_slots_from_reliable_tail(bars)
    trading_day_groups: list[DailyCoverage] = []
    for date, group in frame.groupby("date"):
        ts = pd.Timestamp(date)
        if not _is_weekday(ts):
            continue
        observed_hours = sorted(int(v) for v in group["hour"].tolist())
        expected_hours = list(provisional_expected)
        completeness = min(1.0, len(set(observed_hours) & set(expected_hours)) / max(len(expected_hours), 1))
        degradation = None
        if len(observed_hours) <= MIDNIGHT_ONLY_MAX_BARS and MIDNIGHT_HOUR in observed_hours:
            degradation = HistoricalDegradationClass.HISTORICAL_INTRADAY_DEGRADATION.value
        trading_day_groups.append(
            DailyCoverage(
                date=ts.date().isoformat(),
                weekday=ts.day_name(),
                era=CoverageEra.TRANSITIONAL,
                observed_slots=observed_hours,
                expected_slots=expected_hours,
                observed_slot_count=len(observed_hours),
                expected_slot_count=len(expected_hours),
                completeness_ratio=round(completeness, 4),
                degradation_class=degradation,
            )
        )

    rolling = {
        str(window): _rolling_means(trading_day_groups, window)
        for window in ROLLING_WINDOWS_TRADING_DAYS
    }
    reliable_start = detect_reliable_research_start(trading_day_groups, rolling["120"])
    expected_slots = infer_expected_slots_from_reliable_tail(
        bars,
        reliable_start=reliable_start,
    )

    # Recompute daily completeness with final expected slots
    trading_day_groups = []
    for date, group in frame.groupby("date"):
        ts = pd.Timestamp(date)
        if not _is_weekday(ts):
            continue
        observed_hours = sorted(int(v) for v in group["hour"].tolist())
        completeness = min(1.0, len(set(observed_hours) & set(expected_slots)) / max(len(expected_slots), 1))
        degradation = None
        if len(observed_hours) <= MIDNIGHT_ONLY_MAX_BARS and completeness < TRANSITIONAL_COMPLETENESS_THRESHOLD:
            degradation = HistoricalDegradationClass.HISTORICAL_INTRADAY_DEGRADATION.value
        trading_day_groups.append(
            DailyCoverage(
                date=ts.date().isoformat(),
                weekday=ts.day_name(),
                era=CoverageEra.TRANSITIONAL,
                observed_slots=observed_hours,
                expected_slots=expected_slots,
                observed_slot_count=len(observed_hours),
                expected_slot_count=len(expected_slots),
                completeness_ratio=round(completeness, 4),
                degradation_class=degradation,
            )
        )

    rolling = {
        str(window): _rolling_means(trading_day_groups, window)
        for window in ROLLING_WINDOWS_TRADING_DAYS
    }
    reliable_start = detect_reliable_research_start(trading_day_groups, rolling["120"])

    reliable_idx = 0
    if reliable_start is not None:
        reliable_dates = [day.date for day in trading_day_groups]
        reliable_key = reliable_start.date().isoformat()
        if reliable_key in reliable_dates:
            reliable_idx = reliable_dates.index(reliable_key)

    for idx, day in enumerate(trading_day_groups):
        day.era = _classify_day_era(
            day,
            reliable_idx=reliable_idx if reliable_start is not None else None,
            idx=idx,
            trading_days=trading_day_groups,
        )

    provider_start = frame["timestamp"].iloc[0].isoformat()
    reliable_start_iso = reliable_start.date().isoformat() if reliable_start is not None else None
    warmup_start_iso = None
    if reliable_start is not None and reliable_idx < len(trading_day_groups):
        warmup_idx = min(len(trading_day_groups) - 1, reliable_idx + WARMUP_TRADING_DAYS_AFTER_RELIABLE)
        warmup_start_iso = trading_day_groups[warmup_idx].date

    excluded_dates = {day.date for day in trading_day_groups if day.era == CoverageEra.DEGRADED_UNUSABLE}
    excluded_bars = frame[
        frame["date"].map(lambda d: pd.Timestamp(d).date().isoformat()).isin(excluded_dates)
    ]
    reliable_mask = (
        frame["date"].map(lambda d: pd.Timestamp(d).date().isoformat()) >= reliable_start_iso
        if reliable_start_iso
        else pd.Series([False] * len(frame))
    )
    reliable_bars = frame[reliable_mask]

    yearly: dict[str, list[float]] = {}
    for day in trading_day_groups:
        yearly.setdefault(day.date[:4], []).append(day.completeness_ratio)

    era_summary = _era_day_counts(trading_day_groups)
    midnight_end = pd.Timestamp(reliable_start_iso, tz="UTC") - pd.Timedelta(days=1) if reliable_start_iso else frame["date"].max()
    midnight_compare = compare_midnight_h4_to_d1(bars, d1_bars=d1_bars, era_end=midnight_end)

    reliable_era_bar_count = int(len(reliable_bars))
    reliable_era_gap_count = count_reliable_era_gaps(
        bars,
        reliable_start=reliable_start_iso,
        expected_slots=expected_slots,
    )
    reliable_gate_ok = (
        reliable_era_bar_count >= min_reliable_bars
        and reliable_start_iso is not None
    )
    empirical_gate = "CLEAR_ON_FREEZE" if reliable_gate_ok else "BLOCKED_INSUFFICIENT_RELIABLE_HISTORY"

    return CoverageAuditReport(
        expected_recurring_slots_utc=expected_slots,
        provider_actual_start=provider_start,
        reliable_research_start=reliable_start_iso,
        warmup_adjusted_usable_start=warmup_start_iso,
        excluded_bar_count=int(len(excluded_bars)),
        excluded_day_count=len(excluded_dates),
        exclusion_reason=HistoricalDegradationClass.HISTORICAL_INTRADAY_DEGRADATION.value,
        reliable_era_bar_count=reliable_era_bar_count,
        reliable_era_gap_count=reliable_era_gap_count,
        empirical_gate=empirical_gate,
        daily=trading_day_groups,
        rolling_completeness=rolling,
        yearly_completeness={year: round(sum(vals) / len(vals), 4) for year, vals in sorted(yearly.items())},
        era_summary=era_summary,
        midnight_d1_comparison=midnight_compare,
        research_eligibility={
            "raw_preserved": True,
            "normalized_research_excludes_eras": [CoverageEra.DEGRADED_UNUSABLE.value],
            "eligible_eras": [CoverageEra.TRANSITIONAL.value, CoverageEra.RELIABLE_H4.value],
            "reliable_era_only_for_depth_gate": True,
            "shared_absence_evidence": shared_absence_evidence or {},
            "rejected_reclassification": "EXPECTED_DAILY_MAINTENANCE",
        },
    )


def count_reliable_era_gaps(
    bars: list[dict[str, Any]],
    *,
    reliable_start: str | None,
    expected_slots: list[int],
) -> int:
    if not reliable_start:
        return 0
    frame = _build_daily_frame(bars)
    frame = frame[frame["date"] >= pd.Timestamp(reliable_start, tz="UTC")]
    gaps = 0
    for date, group in frame.groupby("date"):
        ts = pd.Timestamp(date)
        if not _is_weekday(ts):
            continue
        observed = set(int(v) for v in group["hour"].tolist())
        missing = [hour for hour in expected_slots if hour not in observed]
        if missing:
            gaps += 1
    return gaps


def coverage_report_to_dict(report: CoverageAuditReport) -> dict[str, Any]:
    return {
        "expected_recurring_slots_utc": report.expected_recurring_slots_utc,
        "provider_actual_start": report.provider_actual_start,
        "reliable_research_start": report.reliable_research_start,
        "warmup_adjusted_usable_start": report.warmup_adjusted_usable_start,
        "excluded_bar_count": report.excluded_bar_count,
        "excluded_day_count": report.excluded_day_count,
        "exclusion_reason": report.exclusion_reason,
        "reliable_era_bar_count": report.reliable_era_bar_count,
        "reliable_era_gap_count": report.reliable_era_gap_count,
        "empirical_gate": report.empirical_gate,
        "era_summary": report.era_summary,
        "yearly_completeness": report.yearly_completeness,
        "rolling_completeness_latest": {
            window: (values[list(values)[-1]] if values else None)
            for window, values in report.rolling_completeness.items()
        },
        "midnight_d1_comparison": report.midnight_d1_comparison,
        "research_eligibility": report.research_eligibility,
        "daily_sample": [
            {
                "date": day.date,
                "weekday": day.weekday,
                "era": day.era.value,
                "observed_slots": day.observed_slots,
                "expected_slots": day.expected_slots,
                "completeness_ratio": day.completeness_ratio,
                "degradation_class": day.degradation_class,
            }
            for day in report.daily[-30:]
        ],
    }


def filter_research_eligible_bars(
    bars: list[dict[str, Any]],
    report: CoverageAuditReport,
) -> list[dict[str, Any]]:
    excluded_dates = {day.date for day in report.daily if day.era == CoverageEra.DEGRADED_UNUSABLE}
    if not excluded_dates:
        return list(bars)
    kept: list[dict[str, Any]] = []
    for bar in bars:
        ts = _parse_ts(bar["time"])
        if ts.date().isoformat() in excluded_dates:
            continue
        kept.append(bar)
    return kept
