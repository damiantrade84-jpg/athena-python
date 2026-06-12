"""Stationary block bootstrap for ASE validation (§11)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_BLOCK = 10
DEFAULT_RESAMPLES = 5000


@dataclass(frozen=True)
class BootstrapResult:
    mean: float
    ci_low: float
    ci_high: float
    lb_expectancy: float
    n_resamples: int
    block_size: int


def block_bootstrap_expectancy(
    returns: np.ndarray,
    *,
    block: int = DEFAULT_BLOCK,
    n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = 42,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Stationary block bootstrap on trade-level net_R series."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n == 0:
        return BootstrapResult(
            mean=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            lb_expectancy=float("nan"),
            n_resamples=n_resamples,
            block_size=block,
        )

    rng = np.random.default_rng(seed)
    block = max(1, min(block, n))
    means = np.empty(n_resamples, dtype=float)

    for i in range(n_resamples):
        sample: list[float] = []
        while len(sample) < n:
            start = int(rng.integers(0, max(1, n - block + 1)))
            chunk = r[start : start + block]
            sample.extend(chunk.tolist())
        sample_arr = np.asarray(sample[:n], dtype=float)
        means[i] = float(np.mean(sample_arr))

    lo = float(np.quantile(means, alpha / 2.0))
    hi = float(np.quantile(means, 1.0 - alpha / 2.0))
    return BootstrapResult(
        mean=float(np.mean(r)),
        ci_low=lo,
        ci_high=hi,
        lb_expectancy=lo,
        n_resamples=n_resamples,
        block_size=block,
    )
