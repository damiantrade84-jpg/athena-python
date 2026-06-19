from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SpreadFieldStatus(str, Enum):
    OBSERVED_USABLE = "OBSERVED_USABLE"
    PRESENT_UNRELIABLE = "PRESENT_UNRELIABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class SpreadAudit:
    status: SpreadFieldStatus
    present: bool
    zero_count: int
    non_zero_count: int
    missing_spread_count: int
    observed_usable_count: int
    extreme_outlier_count: int
    total_count: int
    median_points: float | None
    p95_points: float | None
    max_points: float | None
    outlier_threshold_points: float | None
    varies: bool
    point_size: float | None
    coverage_by_year: dict[str, dict[str, int | float | None]]
    cost_model: dict[str, Any]
    notes: tuple[str, ...]


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    idx = max(0, min(len(ordered) - 1, idx))
    return float(ordered[idx])


def _bar_year(bar: dict[str, Any]) -> int | None:
    raw = bar.get("time")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).year
        except ValueError:
            return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).year
    except (TypeError, ValueError, OSError):
        return None


def _extract_spread_points(bar: dict[str, Any]) -> float | None:
    if "spread" not in bar:
        return None
    value = bar.get("spread")
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def classify_spread_field(
    bars: list[dict[str, Any]],
    *,
    point_size: float | None,
) -> SpreadAudit:
    notes: list[str] = []
    all_values: list[float] = []
    missing_count = 0
    zero_count = 0
    coverage_by_year: dict[str, dict[str, int | float | None]] = {}

    for bar in bars:
        year = _bar_year(bar)
        year_key = str(year) if year is not None else "unknown"
        bucket = coverage_by_year.setdefault(
            year_key,
            {
                "total": 0,
                "observed_usable": 0,
                "missing_spread": 0,
                "extreme_outliers": 0,
                "median_points": None,
            },
        )
        bucket["total"] = int(bucket["total"]) + 1

        value = _extract_spread_points(bar)
        if value is None:
            missing_count += 1
            bucket["missing_spread"] = int(bucket["missing_spread"]) + 1
            continue
        if value == 0.0:
            zero_count += 1
            missing_count += 1
            bucket["missing_spread"] = int(bucket["missing_spread"]) + 1
            continue
        all_values.append(value)

    if not any("spread" in bar for bar in bars):
        return SpreadAudit(
            status=SpreadFieldStatus.UNAVAILABLE,
            present=False,
            zero_count=0,
            non_zero_count=0,
            missing_spread_count=len(bars),
            observed_usable_count=0,
            extreme_outlier_count=0,
            total_count=len(bars),
            median_points=None,
            p95_points=None,
            max_points=None,
            outlier_threshold_points=None,
            varies=False,
            point_size=point_size,
            coverage_by_year={},
            cost_model=_default_cost_model(None, None),
            notes=("spread field missing from raw bars",),
        )

    non_zero = list(all_values)
    if zero_count:
        notes.append("zero spread treated as missing spread, not zero transaction cost")
    median = _percentile(non_zero, 0.5)
    p95 = _percentile(non_zero, 0.95)
    max_value = max(non_zero) if non_zero else None
    outlier_threshold = None
    extreme_outliers: list[float] = []
    if non_zero and p95 is not None and median is not None:
        outlier_threshold = max(p95 * 5.0, median * 20.0, 50.0)
        extreme_outliers = [value for value in non_zero if value > outlier_threshold]

    observed_usable = [value for value in non_zero if outlier_threshold is None or value <= outlier_threshold]
    varies = len(set(non_zero)) > 1 if non_zero else False

    for bar in bars:
        year = _bar_year(bar)
        year_key = str(year) if year is not None else "unknown"
        value = _extract_spread_points(bar)
        if value is None or value == 0.0:
            continue
        bucket = coverage_by_year[year_key]
        if outlier_threshold is not None and value > outlier_threshold:
            bucket["extreme_outliers"] = int(bucket["extreme_outliers"]) + 1
        else:
            bucket["observed_usable"] = int(bucket["observed_usable"]) + 1

    for year_key, bucket in coverage_by_year.items():
        year_values = [
            value
            for bar in bars
            if str(_bar_year(bar) if _bar_year(bar) is not None else "unknown") == year_key
            for value in [_extract_spread_points(bar)]
            if value is not None and value > 0.0
        ]
        bucket["median_points"] = _percentile(year_values, 0.5)

    if not non_zero:
        status = SpreadFieldStatus.PRESENT_UNRELIABLE
        notes.append("no non-zero spread observations")
    elif len(observed_usable) / max(len(bars), 1) >= 0.5:
        status = SpreadFieldStatus.OBSERVED_USABLE
        if missing_count:
            notes.append("missing spread bars require conservative imputation fallback")
    else:
        status = SpreadFieldStatus.PRESENT_UNRELIABLE

    if extreme_outliers:
        notes.append("extreme spread outliers preserved for diagnostics; not auto-discarded")
    if point_size in (None, 0):
        notes.append("symbol point size unavailable for spread conversion")
    elif status == SpreadFieldStatus.OBSERVED_USABLE:
        notes.append("spread reported in MT5 points; convert with symbol_info.point")

    return SpreadAudit(
        status=status,
        present=True,
        zero_count=zero_count,
        non_zero_count=len(non_zero),
        missing_spread_count=missing_count,
        observed_usable_count=len(observed_usable),
        extreme_outlier_count=len(extreme_outliers),
        total_count=len(bars),
        median_points=median,
        p95_points=p95,
        max_points=max_value,
        outlier_threshold_points=outlier_threshold,
        varies=varies,
        point_size=point_size,
        coverage_by_year=coverage_by_year,
        cost_model=_default_cost_model(median, p95),
        notes=tuple(dict.fromkeys(notes)),
    )


def _default_cost_model(median: float | None, p95: float | None) -> dict[str, Any]:
    fallback = p95 or median
    return {
        "observed_non_zero_spread_preferred": True,
        "missing_spread_policy": "conservative_time_local_or_instrument_fallback",
        "fallback_reference_points": fallback,
        "stress_multipliers": [1.5, 2.0],
        "zero_spread_is_missing": True,
    }


def spread_audit_to_dict(audit: SpreadAudit) -> dict[str, Any]:
    return {
        "status": audit.status.value,
        "present": audit.present,
        "zero_count": audit.zero_count,
        "non_zero_count": audit.non_zero_count,
        "missing_spread_count": audit.missing_spread_count,
        "observed_usable_count": audit.observed_usable_count,
        "extreme_outlier_count": audit.extreme_outlier_count,
        "total_count": audit.total_count,
        "median_points": audit.median_points,
        "p95_points": audit.p95_points,
        "max_points": audit.max_points,
        "outlier_threshold_points": audit.outlier_threshold_points,
        "varies": audit.varies,
        "point_size": audit.point_size,
        "coverage_by_year": audit.coverage_by_year,
        "cost_model": audit.cost_model,
        "notes": list(audit.notes),
    }
