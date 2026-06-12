"""Isotonic calibration helpers (ASE v2.1 §6)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression


@dataclass(frozen=True)
class CalibrateResult:
    calibrator: IsotonicRegression | None
    brier: float
    brier_skill: float


def chronological_calibration_split(
    n_rows: int,
    *,
    calibration_fraction: float = 0.15,
) -> tuple[np.ndarray, np.ndarray]:
    if n_rows < 2:
        raise ValueError("calibration split requires at least two rows")
    if not 0.0 < calibration_fraction < 1.0:
        raise ValueError("calibration_fraction must be between 0 and 1")
    cut = max(1, min(n_rows - 1, int(n_rows * (1.0 - calibration_fraction))))
    return np.arange(0, cut), np.arange(cut, n_rows)


def calibrate_fold(
    model,
    X: list[dict],
    y: np.ndarray,
    *,
    feature_names: list[str] | None = None,
    calib_fraction: float = 0.15,
) -> CalibrateResult:
    """Fit isotonic on chronologically-last calib_fraction of training fold."""
    from athena_ase.features.build import FEATURE_SCHEMA_CORE
    from athena_ase.models.meta import _prepare_matrix

    n = len(y)
    if n < 20:
        return CalibrateResult(None, float("nan"), float("nan"))
    _, cal_idx = chronological_calibration_split(
        n,
        calibration_fraction=calib_fraction,
    )
    names = tuple(feature_names) if feature_names else FEATURE_SCHEMA_CORE
    X_mat = _prepare_matrix(X, names)
    raw_p = model.predict_proba(X_mat)[:, 1]
    calibrator = fit_isotonic(raw_p[cal_idx], y[cal_idx])
    cal_p = calibrator.predict(raw_p)
    return CalibrateResult(
        calibrator=calibrator,
        brier=brier_score(cal_p, y),
        brier_skill=brier_skill(cal_p, y),
    )


def fit_isotonic(
    y_prob: np.ndarray,
    y_true: np.ndarray,
) -> IsotonicRegression:
    model = IsotonicRegression(out_of_bounds="clip")
    model.fit(y_prob, y_true)
    return model


def brier_score(y_prob: np.ndarray, y_true: np.ndarray) -> float:
    return float(np.mean((y_prob - y_true) ** 2))


def brier_skill(y_prob: np.ndarray, y_true: np.ndarray) -> float:
    base = float(np.mean(y_true))
    base_brier = float(np.mean((base - y_true) ** 2))
    if base_brier <= 0:
        return 0.0
    return 1.0 - brier_score(y_prob, y_true) / base_brier


def reliability_table(
    y_prob: np.ndarray,
    y_true: np.ndarray,
    *,
    n_bins: int = 10,
) -> list[dict[str, float]]:
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    rows: list[dict[str, float]] = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (y_prob >= lo) & (y_prob < hi if i < n_bins - 1 else y_prob <= hi)
        if not np.any(mask):
            continue
        rows.append(
            {
                "bin_lo": float(lo),
                "bin_hi": float(hi),
                "pred_mean": float(np.mean(y_prob[mask])),
                "obs_rate": float(np.mean(y_true[mask])),
                "count": float(np.sum(mask)),
            }
        )
    return rows


def reliability_monotone(rows: list[dict[str, float]], *, tol: float = 0.05) -> bool:
    obs = [r["obs_rate"] for r in rows]
    for i in range(1, len(obs)):
        if obs[i] + tol < obs[i - 1]:
            return False
    return True
