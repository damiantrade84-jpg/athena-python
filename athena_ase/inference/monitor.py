"""Feature/calibration drift monitor (ASE v2.1 §12)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from athena_ase.settings import monitor_min_samples

log = logging.getLogger("ase.monitor")

# Reserved per-row key carrying the scan decision time; never a PSI feature.
DECISION_TIME_KEY = "_decision_time_ms"


def population_stability_index(expected: np.ndarray, actual: np.ndarray, *, bins: int = 10) -> float:
    expected = np.asarray(expected, dtype=float)
    actual = np.asarray(actual, dtype=float)
    if len(expected) == 0 or len(actual) == 0:
        return 0.0
    edges = np.linspace(min(expected.min(), actual.min()), max(expected.max(), actual.max()), bins + 1)
    if edges[0] == edges[-1]:
        return 0.0
    e_hist, _ = np.histogram(expected, bins=edges)
    a_hist, _ = np.histogram(actual, bins=edges)
    e_pct = np.where(e_hist == 0, 1e-6, e_hist / max(e_hist.sum(), 1))
    a_pct = np.where(a_hist == 0, 1e-6, a_hist / max(a_hist.sum(), 1))
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


def drift_score(psi_values: dict[str, float]) -> float:
    if not psi_values:
        return 0.0
    return float(max(psi_values.values()))


def should_auto_demote(
    psi_values: dict[str, float],
    *,
    psi_warn: float = 0.25,
    psi_critical: float = 0.5,
    warn_count: int = 3,
) -> bool:
    high = [v for v in psi_values.values() if v > psi_warn]
    if any(v > psi_critical for v in psi_values.values()):
        return True
    return len(high) >= warn_count


def calibration_gap(realized_win_rate: float, mean_p_cal: float, *, tol: float = 0.12) -> bool:
    return abs(realized_win_rate - mean_p_cal) > tol


@dataclass
class MonitorResult:
    drift_score: float = 0.0
    watch_max: bool = False
    psi: dict[str, float] = field(default_factory=dict)
    calibration_drift: bool = False
    insufficient_samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "driftScore": self.drift_score,
            "watchMax": self.watch_max,
            "psi": self.psi,
            "calibrationDrift": self.calibration_drift,
            "insufficientSamples": self.insufficient_samples,
        }


def evaluate_monitor(
    *,
    reference_features: dict[str, np.ndarray] | None = None,
    live_feature_rows: list[dict[str, float]] | None = None,
    realized_win_rate: float | None = None,
    mean_p_cal: float | None = None,
    min_samples: int | None = None,
) -> MonitorResult:
    psi: dict[str, float] = {}
    insufficient: list[str] = []
    reference_features = reference_features or {}
    live_feature_rows = live_feature_rows or []
    min_n = monitor_min_samples() if min_samples is None else max(1, int(min_samples))
    if live_feature_rows and reference_features:
        live_df: dict[str, list[float]] = {k: [] for k in reference_features}
        live_times: dict[str, set[Any]] = {k: set() for k in reference_features}
        for i, row in enumerate(live_feature_rows):
            when = row.get(DECISION_TIME_KEY)
            # Rows from one scan share a decision time and are cross-sectional,
            # not independent time samples; untagged rows keep legacy counting.
            time_marker: Any = float(when) if isinstance(when, (int, float)) else ("row", i)
            for key in reference_features:
                val = row.get(key)
                if isinstance(val, (int, float)):
                    live_df[key].append(float(val))
                    live_times[key].add(time_marker)
        for key, ref in reference_features.items():
            live_vals = live_df.get(key) or []
            effective_n = len(live_times.get(key) or ())
            if effective_n < min_n or len(ref) < min_n:
                insufficient.append(key)
                log.debug(
                    "ASE monitor skip PSI %s: live=%d distinct_times=%d ref=%d need>=%d",
                    key,
                    len(live_vals),
                    effective_n,
                    len(ref),
                    min_n,
                )
                continue
            psi[key] = population_stability_index(ref, np.asarray(live_vals, dtype=float))

    score = drift_score(psi)
    watch = should_auto_demote(psi)
    cal_drift = False
    if realized_win_rate is not None and mean_p_cal is not None:
        cal_drift = calibration_gap(realized_win_rate, mean_p_cal)
        if cal_drift:
            watch = True
    return MonitorResult(
        drift_score=score,
        watch_max=watch,
        psi=psi,
        calibration_drift=cal_drift,
        insufficient_samples=insufficient,
    )


def apply_watch_max_cap(status: str, watch_max: bool) -> str:
    if watch_max and status == "TRADE":
        return "WATCH"
    return status
