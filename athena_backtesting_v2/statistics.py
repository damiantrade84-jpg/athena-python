from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

import numpy as np


def max_drawdown(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    equity = np.cumsum(values)
    peaks = np.maximum.accumulate(np.concatenate(([0.0], equity)))[:-1]
    return float(np.min(equity - peaks))


def summarize_r(values: list[float], costs: list[float] | None = None) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return {
            "tradeCount": 0, "winRate": 0.0, "meanR": 0.0, "medianR": 0.0,
            "totalR": 0.0, "profitFactor": 0.0, "sqn": 0.0, "sharpe": 0.0,
            "maxDrawdownR": 0.0, "totalCostR": 0.0,
        }
    wins = array[array > 0]
    losses = array[array < 0]
    std = float(np.std(array, ddof=1)) if array.size > 1 else 0.0
    profit_factor = float(np.sum(wins) / abs(np.sum(losses))) if losses.size and np.sum(losses) != 0 else (float("inf") if wins.size else 0.0)
    return {
        "tradeCount": int(array.size),
        "winRate": float(np.mean(array > 0) * 100.0),
        "meanR": float(np.mean(array)),
        "medianR": float(np.median(array)),
        "totalR": float(np.sum(array)),
        "profitFactor": profit_factor,
        "sqn": float(math.sqrt(array.size) * np.mean(array) / std) if std > 0 else 0.0,
        "sharpe": float(np.mean(array) / std * math.sqrt(array.size)) if std > 0 else 0.0,
        "maxDrawdownR": max_drawdown(array),
        "totalCostR": float(np.sum(np.asarray(costs or [], dtype=float))) if costs else 0.0,
        "skew": float(((array - array.mean()) ** 3).mean() / (array.std() ** 3)) if array.size > 2 and array.std() > 0 else 0.0,
        "excessKurtosis": float(((array - array.mean()) ** 4).mean() / (array.var() ** 2) - 3.0) if array.size > 3 and array.var() > 0 else 0.0,
    }


def block_bootstrap_ci(
    values: list[float],
    *,
    samples: int,
    seed: int,
    confidence: float = 0.95,
) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    if array.size < 2:
        value = float(array.mean()) if array.size else 0.0
        return {"meanR": {"lower": value, "estimate": value, "upper": value}, "samples": 0, "blockLength": 0, "seed": seed}
    rng = np.random.default_rng(seed)
    block = max(2, int(round(math.sqrt(array.size))))
    means = np.empty(samples, dtype=float)
    for sample in range(samples):
        indices: list[int] = []
        while len(indices) < array.size:
            start = int(rng.integers(0, array.size))
            indices.extend((start + offset) % array.size for offset in range(block))
        means[sample] = float(np.mean(array[np.asarray(indices[: array.size])]))
    alpha = (1.0 - confidence) / 2.0
    return {
        "meanR": {
            "lower": float(np.quantile(means, alpha)),
            "estimate": float(np.mean(array)),
            "upper": float(np.quantile(means, 1.0 - alpha)),
        },
        "samples": samples,
        "blockLength": block,
        "seed": seed,
    }


def probabilistic_sharpe_ratio(values: list[float], benchmark: float = 0.0) -> float | None:
    array = np.asarray(values, dtype=float)
    if array.size < 3:
        return None
    std = float(np.std(array, ddof=1))
    if std <= 0:
        return None
    sharpe = float(np.mean(array) / std)
    skew = float(((array - array.mean()) ** 3).mean() / (array.std() ** 3)) if array.std() > 0 else 0.0
    kurtosis = float(((array - array.mean()) ** 4).mean() / (array.var() ** 2)) if array.var() > 0 else 3.0
    denominator = math.sqrt(max(1e-12, 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe))
    statistic = (sharpe - benchmark) * math.sqrt(array.size - 1) / denominator
    return float(NormalDist().cdf(statistic))


def deflated_sharpe_ratio(values: list[float], trial_count: int) -> float | None:
    array = np.asarray(values, dtype=float)
    if array.size < 3:
        return None
    trials = max(1, int(trial_count))
    # Expected maximum of independent standard-normal trials.  The estimate is
    # conservative and is recorded with the exact trial family count.
    if trials == 1:
        benchmark = 0.0
    else:
        euler_gamma = 0.5772156649015329
        z_one = NormalDist().inv_cdf(1.0 - 1.0 / trials)
        z_e = NormalDist().inv_cdf(1.0 - 1.0 / (trials * math.e))
        benchmark = (1.0 - euler_gamma) * z_one + euler_gamma * z_e
        benchmark /= math.sqrt(array.size)
    return probabilistic_sharpe_ratio(values, benchmark)

