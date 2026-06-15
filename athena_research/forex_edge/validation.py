from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd


TOTAL_REGISTERED_TRIALS = 48
PORTFOLIO_REGISTERED_TRIALS = 12
FIXING_REGISTERED_TRIALS = 36

CANDIDATE_DSR_Z = 1.645
CANDIDATE_MAX_PBO = 0.50
CANDIDATE_MAX_CONTRIBUTION = 0.40
CANDIDATE_MIN_POSITIVE_YEARS = 3


def chronological_split(
    timestamps: Iterable[pd.Timestamp],
    *,
    development_end: str,
    holdout_start: str,
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    index = pd.DatetimeIndex(timestamps)
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    dev_end = (
        pd.Timestamp(development_end, tz="UTC")
        + pd.Timedelta(days=1)
        - pd.Timedelta(nanoseconds=1)
    )
    holdout = pd.Timestamp(holdout_start, tz="UTC")
    development = index[index <= dev_end]
    final = index[index >= holdout]
    if len(development) == 0 or len(final) == 0:
        raise ValueError("BLOCKED_DATA: fixed chronological split unavailable")
    return development, final


def block_bootstrap_lower_bound(
    returns: pd.Series,
    *,
    block_size: int,
    resamples: int,
    seed: int,
    alpha: float = 0.05,
) -> dict[str, object]:
    values = returns.to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {
            "mean": None,
            "ci_low": None,
            "ci_high": None,
            "lower_bound": None,
            "reason_code": "INSUFFICIENT_HISTORY",
        }
    rng = np.random.default_rng(seed)
    block = max(1, min(int(block_size), len(values)))
    means = np.empty(int(resamples), dtype=float)
    for idx in range(int(resamples)):
        sample: list[float] = []
        while len(sample) < len(values):
            start = int(rng.integers(0, max(1, len(values) - block + 1)))
            sample.extend(values[start : start + block].tolist())
        means[idx] = float(np.mean(np.asarray(sample[: len(values)], dtype=float)))
    lo = float(np.quantile(means, alpha / 2.0))
    hi = float(np.quantile(means, 1.0 - alpha / 2.0))
    return {
        "mean": float(np.mean(values)),
        "ci_low": lo,
        "ci_high": hi,
        "lower_bound": lo,
        "reason_code": None,
    }


def _sharpe(values: np.ndarray) -> float:
    r = values[np.isfinite(values)]
    if len(r) < 2:
        return float("nan")
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        return 0.0
    return float(np.mean(r) / sd * math.sqrt(len(r)))


def _moments(values: np.ndarray) -> tuple[float, float]:
    r = values[np.isfinite(values)]
    if len(r) < 4:
        return 0.0, 3.0
    mean = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        return 0.0, 3.0
    z = (r - mean) / sd
    return float(np.mean(z**3)), float(np.mean(z**4))


def _norm_ppf(p: float) -> float:
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    return float(math.sqrt(2.0) * _erfinv(2.0 * p - 1.0))


def _erfinv(x: float) -> float:
    a = 0.147
    sgn = 1.0 if x >= 0 else -1.0
    ln = math.log(1.0 - x * x)
    first = 2.0 / (math.pi * a) + ln / 2.0
    return sgn * math.sqrt(math.sqrt(first * first - ln / a) - first)


def deflated_sharpe(
    returns: pd.Series,
    *,
    n_trials: int,
    sr_benchmark: float = 0.0,
) -> dict[str, object]:
    values = returns.to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    n_obs = len(values)
    sr = _sharpe(values)
    skew, kurtosis = _moments(values)
    if n_obs < 2 or not math.isfinite(sr):
        return {
            "observed_sharpe": sr,
            "dsr_z": None,
            "n_trials": int(n_trials),
            "n_obs": int(n_obs),
            "skew": skew,
            "kurtosis": kurtosis,
        }
    sr_var = (1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr**2) / max(n_obs - 1, 1)
    sr_std = math.sqrt(max(sr_var, 1e-12))
    euler = 0.5772156649
    max_sr = sr_benchmark + sr_std * (
        (1.0 - euler) * _norm_ppf(1.0 - 1.0 / max(n_trials, 1))
        + euler * _norm_ppf(1.0 - 1.0 / (max(n_trials, 1) * math.e))
    )
    return {
        "observed_sharpe": sr,
        "dsr_z": (sr - max_sr) / sr_std,
        "n_trials": int(n_trials),
        "n_obs": int(n_obs),
        "skew": skew,
        "kurtosis": kurtosis,
    }


def cscv_pbo(
    performance_matrix: np.ndarray,
    *,
    n_partitions: int = 16,
) -> dict[str, object]:
    matrix = np.asarray(performance_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("performance_matrix must be 2D")
    n_obs, n_configs = matrix.shape
    if n_obs < n_partitions or n_configs < 2:
        return {
            "pbo": None,
            "reason_code": "PBO_UNAVAILABLE",
            "n_partitions": n_partitions,
            "n_configs": n_configs,
            "logit_distribution": [],
        }
    splits = np.array_split(np.arange(n_obs), n_partitions)
    half = n_partitions // 2
    logits: list[float] = []
    for combo in itertools.combinations(range(n_partitions), half):
        is_idx = np.concatenate([splits[index] for index in combo])
        oos_idx = np.concatenate(
            [splits[index] for index in range(n_partitions) if index not in combo]
        )
        is_perf = np.nanmean(matrix[is_idx], axis=0)
        oos_perf = np.nanmean(matrix[oos_idx], axis=0)
        best_is = int(np.nanargmax(is_perf))
        rank_oos = int(np.argsort(oos_perf)[::-1].tolist().index(best_is))
        logits.append(math.log((rank_oos + 1) / max(n_configs - rank_oos, 1)))
    pbo = float(np.mean(np.asarray(logits) > 0)) if logits else None
    return {
        "pbo": pbo,
        "reason_code": None if pbo is not None else "PBO_UNAVAILABLE",
        "n_partitions": n_partitions,
        "n_configs": n_configs,
        "logit_distribution": logits,
    }


def max_positive_contribution_share(
    returns: pd.Series,
    groups: pd.Series,
) -> float | None:
    positive_total = float(returns[returns > 0].sum())
    if positive_total <= 0:
        return None
    contribution = returns.clip(lower=0).groupby(groups).sum()
    return float(contribution.max() / positive_total)


def classify_result(
    metrics: dict[str, Any],
    *,
    quality_passed: bool,
    evidence_flags: Iterable[str],
) -> dict[str, object]:
    flags = list(dict.fromkeys(str(flag) for flag in evidence_flags))
    if metrics.get("pbo") is None and "PBO_UNAVAILABLE" not in flags:
        flags.append("PBO_UNAVAILABLE")
    criteria = {
        "holdout_positive": float(metrics.get("holdout_net_return", 0.0)) > 0,
        "bootstrap_lb_positive": float(metrics.get("bootstrap_lb", 0.0)) > 0,
        "cost_2x_positive": float(metrics.get("cost_2x_net_return", 0.0)) > 0,
        "dsr_z": float(metrics.get("dsr_z", float("-inf"))) > CANDIDATE_DSR_Z,
        "pbo": metrics.get("pbo") is not None
        and float(metrics["pbo"]) < CANDIDATE_MAX_PBO,
        "concentration": metrics.get("max_positive_contribution_share") is not None
        and float(metrics["max_positive_contribution_share"])
        <= CANDIDATE_MAX_CONTRIBUTION,
        "positive_years": int(metrics.get("positive_holdout_years", 0))
        >= CANDIDATE_MIN_POSITIVE_YEARS,
        "quality": bool(quality_passed),
    }
    if not quality_passed:
        status = "BLOCKED_DATA"
    elif all(criteria.values()):
        status = "RESEARCH_CANDIDATE"
    else:
        status = "COMPLETED_NO_EDGE"
    return {
        "study_status": status,
        "production_eligible": False,
        "evidence_flags": flags,
        "criteria": criteria,
    }
