"""Deflated Sharpe and CSCV PBO from trials registry (ASE v2.1 §10)."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

DEFAULT_PARTITIONS = 16


@dataclass(frozen=True)
class DSRResult:
    observed_sharpe: float
    deflated_sharpe: float
    n_trials: int
    n_obs: int
    skew: float
    kurtosis: float


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    n_partitions: int
    n_configs: int
    logit_distribution: tuple[float, ...]


def _sharpe(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        return 0.0
    return float(np.mean(r) / sd * math.sqrt(len(r)))


def _moment_skew_kurtosis(r: np.ndarray) -> tuple[float, float]:
    r = np.asarray(r, dtype=float)
    if len(r) < 4:
        return 0.0, 3.0
    m = float(np.mean(r))
    s = float(np.std(r, ddof=1))
    if s <= 0:
        return 0.0, 3.0
    z = (r - m) / s
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4))
    return skew, kurt


def deflated_sharpe_ratio(
    returns: np.ndarray,
    *,
    n_trials: int,
    sr_benchmark: float = 0.0,
) -> DSRResult:
    """Bailey–López de Prado deflated Sharpe (approximate)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    sr = _sharpe(r)
    skew, kurt = _moment_skew_kurtosis(r)
    if n < 2 or not math.isfinite(sr):
        return DSRResult(sr, float("nan"), n_trials, n, skew, kurt)

    sr_var = (1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr**2) / max(n - 1, 1)
    sr_std = math.sqrt(max(sr_var, 1e-12))
    euler = 0.5772156649
    max_sr = sr_benchmark + sr_std * (
        (1.0 - euler) * _norm_ppf(1.0 - 1.0 / max(n_trials, 1))
        + euler * _norm_ppf(1.0 - 1.0 / (max(n_trials, 1) * math.e))
    )
    dsr = (sr - max_sr) / sr_std if sr_std > 0 else float("nan")
    return DSRResult(sr, dsr, n_trials, n, skew, kurt)


def dsr_probability(deflated_sharpe: float) -> float:
    """Confidence form of the deflated Sharpe z-score.

    ``deflated_sharpe_ratio`` returns a standardised statistic; the §11
    ``dsr_min`` threshold (0.95) is a confidence level, so callers gating on
    it must compare against this CDF, not the raw z.
    """
    if not math.isfinite(deflated_sharpe):
        return float("nan")
    return 0.5 * (1.0 + math.erf(deflated_sharpe / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    return float(math.sqrt(2.0) * _erfinv(2.0 * p - 1.0))


def _erfinv(x: float) -> float:
    # Approximation for inverse error function (Numerical Recipes style).
    a = 0.147
    sgn = 1.0 if x >= 0 else -1.0
    ln = math.log(1.0 - x * x)
    first = 2.0 / (math.pi * a) + ln / 2.0
    return sgn * math.sqrt(math.sqrt(first * first - ln / a) - first)


def cscv_pbo(
    performance_matrix: np.ndarray,
    *,
    n_partitions: int = DEFAULT_PARTITIONS,
) -> PBOResult:
    """Combinatorial symmetric cross-validation PBO with fixed partition count."""
    mat = np.asarray(performance_matrix, dtype=float)
    if mat.ndim != 2:
        raise ValueError("performance_matrix must be 2D: n_obs x n_configs")
    n_obs, n_cfg = mat.shape
    if n_obs < n_partitions or n_cfg < 2:
        return PBOResult(float("nan"), n_partitions, n_cfg, ())

    splits = np.array_split(np.arange(n_obs), n_partitions)
    half = n_partitions // 2
    combos = list(itertools.combinations(range(n_partitions), half))
    logits: list[float] = []

    for combo in combos:
        is_idx = np.concatenate([splits[i] for i in combo])
        oos_idx = np.concatenate([splits[i] for i in range(n_partitions) if i not in combo])
        if len(is_idx) == 0 or len(oos_idx) == 0:
            continue
        is_perf = np.nanmean(mat[is_idx], axis=0)
        oos_perf = np.nanmean(mat[oos_idx], axis=0)
        best_is = int(np.nanargmax(is_perf))
        rank_oos = int(np.argsort(oos_perf)[::-1].tolist().index(best_is))
        logits.append(math.log((rank_oos + 1) / max(n_cfg - rank_oos, 1)))

    if not logits:
        return PBOResult(float("nan"), n_partitions, n_cfg, ())
    pbo = float(np.mean(np.asarray(logits) > 0))
    return PBOResult(pbo, n_partitions, n_cfg, tuple(logits))
