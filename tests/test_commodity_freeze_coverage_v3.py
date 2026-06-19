from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from athena_research.commodity_data_audit.coverage_audit import (
    CoverageEra,
    DailyCoverage,
    HistoricalDegradationClass,
    audit_h4_coverage,
    compare_midnight_h4_to_d1,
    detect_reliable_research_start,
    filter_research_eligible_bars,
)
from athena_research.commodity_data_audit.freeze_registry import MIN_RELIABLE_H4_BARS


def _ts(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp())


def _bar(ts: int, close: float = 100.0) -> dict:
    return {
        "time": ts,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "tick_volume": 100,
        "spread": 5,
        "real_volume": 0,
    }


def _weekday_dates(start: datetime, count: int) -> list[datetime]:
    out: list[datetime] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _full_h4_day(day: datetime, close: float = 100.0) -> list[dict]:
    return [_bar(_ts(day.year, day.month, day.day, hour), close=close + hour * 0.01) for hour in (0, 4, 8, 12, 16, 20)]


def _midnight_only_day(day: datetime, close: float = 100.0) -> list[dict]:
    return [_bar(_ts(day.year, day.month, day.day, 0), close=close)]


def test_weekday_single_bar_historical_era_classified_degraded():
    days = _weekday_dates(datetime(2014, 1, 2, tzinfo=timezone.utc), 40)
    bars: list[dict] = []
    for day in days:
        bars.extend(_midnight_only_day(day))

    report = audit_h4_coverage(bars, min_reliable_bars=MIN_RELIABLE_H4_BARS)
    degraded_days = [day for day in report.daily if day.era == CoverageEra.DEGRADED_UNUSABLE]
    assert len(degraded_days) == len(days)
    assert all(day.degradation_class == HistoricalDegradationClass.HISTORICAL_INTRADAY_DEGRADATION.value for day in degraded_days)
    assert report.research_eligibility["rejected_reclassification"] == "EXPECTED_DAILY_MAINTENANCE"
    assert report.empirical_gate == "BLOCKED_INSUFFICIENT_RELIABLE_HISTORY"


def test_transition_into_full_six_slot_coverage():
    degraded_days = _weekday_dates(datetime(2014, 1, 2, tzinfo=timezone.utc), 30)
    reliable_days = _weekday_dates(datetime(2014, 3, 1, tzinfo=timezone.utc), 250)
    bars: list[dict] = []
    for day in degraded_days:
        bars.extend(_midnight_only_day(day))
    for day in reliable_days:
        bars.extend(_full_h4_day(day))

    report = audit_h4_coverage(bars, min_reliable_bars=500)
    assert report.reliable_research_start is not None
    assert report.reliable_research_start > degraded_days[-1].date().isoformat()
    assert report.era_summary[CoverageEra.DEGRADED_UNUSABLE.value] == len(degraded_days)
    assert report.era_summary[CoverageEra.RELIABLE_H4.value] > 0
    assert report.empirical_gate == "CLEAR_ON_FREEZE"


def test_isolated_holiday_after_reliable_cutoff_not_sustained_degradation():
    reliable_days = _weekday_dates(datetime(2016, 1, 4, tzinfo=timezone.utc), 200)
    bars: list[dict] = []
    for day in reliable_days:
        if day.date().isoformat() == "2016-02-15":
            bars.extend(_full_h4_day(day)[:5])
            continue
        bars.extend(_full_h4_day(day))

    report = audit_h4_coverage(bars, min_reliable_bars=500)
    assert report.reliable_research_start is not None
    assert report.reliable_era_gap_count >= 1
    assert report.empirical_gate == "CLEAR_ON_FREEZE"


def test_sustained_later_degradation_blocks_early_reliable_start():
    early = _weekday_dates(datetime(2016, 1, 4, tzinfo=timezone.utc), 160)
    degraded = _weekday_dates(datetime(2016, 9, 1, tzinfo=timezone.utc), 25)
    late = _weekday_dates(datetime(2016, 10, 10, tzinfo=timezone.utc), 160)
    bars: list[dict] = []
    for day in early:
        bars.extend(_full_h4_day(day))
    for day in degraded:
        bars.extend(_midnight_only_day(day))
    for day in late:
        bars.extend(_full_h4_day(day))

    report = audit_h4_coverage(bars, min_reliable_bars=500)
    assert report.reliable_research_start is not None
    assert report.reliable_research_start >= "2016-10-10"
    later_degraded = [day for day in report.daily if day.date >= "2016-09-01" and day.date < "2016-10-10"]
    assert any(day.era == CoverageEra.DEGRADED_UNUSABLE for day in later_degraded)


def test_automatic_reliable_start_detection_not_provider_start():
    degraded = _weekday_dates(datetime(2014, 1, 2, tzinfo=timezone.utc), 80)
    reliable = _weekday_dates(datetime(2015, 6, 1, tzinfo=timezone.utc), 200)
    bars: list[dict] = []
    for day in degraded:
        bars.extend(_midnight_only_day(day))
    for day in reliable:
        bars.extend(_full_h4_day(day))

    report = audit_h4_coverage(bars, min_reliable_bars=500)
    assert report.provider_actual_start is not None
    assert report.provider_actual_start.startswith("2014")
    assert report.reliable_research_start is not None
    assert report.reliable_research_start > "2014-12-31"
    assert report.reliable_research_start != "2014-01-02"
    assert report.warmup_adjusted_usable_start is not None
    assert report.warmup_adjusted_usable_start > report.reliable_research_start


def test_raw_preservation_with_research_exclusion():
    degraded = _weekday_dates(datetime(2014, 1, 2, tzinfo=timezone.utc), 10)
    reliable = _weekday_dates(datetime(2016, 1, 4, tzinfo=timezone.utc), 130)
    bars: list[dict] = []
    for day in degraded:
        bars.extend(_midnight_only_day(day))
    for day in reliable:
        bars.extend(_full_h4_day(day))

    report = audit_h4_coverage(bars, min_reliable_bars=500)
    eligible = filter_research_eligible_bars(bars, report)
    assert len(bars) == 10 + 130 * 6
    assert len(eligible) == len(bars) - report.excluded_bar_count
    assert report.excluded_bar_count == len(degraded)
    assert report.research_eligibility["raw_preserved"] is True
    assert CoverageEra.DEGRADED_UNUSABLE.value in report.research_eligibility["normalized_research_excludes_eras"]


def test_midnight_h4_matches_d1_interpretation():
    day = datetime(2014, 6, 2, tzinfo=timezone.utc)
    h4 = _midnight_only_day(day, close=1200.0)[0]
    d1 = {
        "time": _ts(day.year, day.month, day.day, 0),
        "open": h4["open"],
        "high": h4["high"],
        "low": h4["low"],
        "close": h4["close"],
        "tick_volume": 500,
    }
    result = compare_midnight_h4_to_d1(
        [h4],
        d1_bars=[d1],
        era_end=day + timedelta(days=1),
    )
    assert result["compared_days"] == 1
    assert result["ohlc_near_equal_count"] == 1
    assert "daily aggregate" in result["interpretation"].lower()


def test_detect_reliable_research_start_requires_rolling_120_threshold():
    days = _weekday_dates(datetime(2016, 1, 4, tzinfo=timezone.utc), 140)
    trading_days = []
    rolling_120 = {}
    for idx, day in enumerate(days):
        observed = [0, 4, 8, 12, 16, 20]
        ratio = 1.0 if idx >= 120 else 0.5
        date = day.date().isoformat()
        trading_days.append(
            DailyCoverage(
                date=date,
                weekday=day.strftime("%A"),
                era=CoverageEra.TRANSITIONAL,
                observed_slots=observed if ratio == 1.0 else [0],
                expected_slots=observed,
                observed_slot_count=len(observed if ratio == 1.0 else [0]),
                expected_slot_count=6,
                completeness_ratio=ratio,
            )
        )
        rolling_120[date] = ratio if idx >= 119 else 0.5

    start = detect_reliable_research_start(trading_days, rolling_120)
    assert start is not None
    assert start.date().isoformat() == days[120].date().isoformat()
