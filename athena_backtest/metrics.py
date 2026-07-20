"""Trade-list metrics for the rebuilt backtester.

Pure functions over a chronological list of per-trade R results. No I/O, no
config reads. Randomness is confined to the seeded stationary block bootstrap
so every metric here is deterministic for a given input + seed.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist, fmean, stdev


def sqn(results: list[float]) -> float | None:
    """Van Tharp SQN: sqrt(n) * mean(R) / std(R). None below 2 trades or flat std.

    Sample stdev, matching engine_a_v3.backtest._sqn so the headline and the
    deflated figure share one convention.
    """
    if len(results) < 2:
        return None
    std = stdev(results)
    if std <= 0:
        return None
    return math.sqrt(len(results)) * fmean(results) / std


def profit_factor(results: list[float]) -> float | None:
    gross_profit = sum(value for value in results if value > 0)
    gross_loss = abs(sum(value for value in results if value <= 0))
    if gross_loss <= 0:
        return None if gross_profit <= 0 else float("inf")
    return gross_profit / gross_loss


def win_rate(results: list[float]) -> float | None:
    if not results:
        return None
    return 100.0 * sum(1 for value in results if value > 0) / len(results)


def expectancy(results: list[float]) -> float | None:
    return fmean(results) if results else None


def equity_curve(results: list[float]) -> list[float]:
    curve = []
    total = 0.0
    for value in results:
        total += value
        curve.append(round(total, 6))
    return curve


def max_drawdown_r(results: list[float]) -> float:
    peak = 0.0
    total = 0.0
    max_dd = 0.0
    for value in results:
        total += value
        peak = max(peak, total)
        max_dd = max(max_dd, peak - total)
    return max_dd


def mfe_mae(trades: list[dict]) -> dict:
    """Aggregate MFE/MAE when per-trade excursions are present (else zeros)."""
    mfes = [float(t["mfeR"]) for t in trades if t.get("mfeR") is not None]
    maes = [float(t["maeR"]) for t in trades if t.get("maeR") is not None]
    return {
        "avgMfeR": round(fmean(mfes), 4) if mfes else None,
        "avgMaeR": round(fmean(maes), 4) if maes else None,
    }


def deflated_sqn(observed_sqn: float | None, n_trades: int, n_trials: int) -> float | None:
    """Deflate SQN for multiple testing (Bailey/Lopez de Prado style).

    Approximates the expected maximum of `n_trials` standard-normal draws and
    subtracts it from the observed t-like statistic before rescaling. With one
    trial this returns the input unchanged. None when SQN or n is unusable.
    """
    if observed_sqn is None or n_trades < 2 or n_trials < 1:
        return None
    if n_trials == 1:
        return observed_sqn
    if n_trials == 2:
        expected_max = 1.0 / math.sqrt(math.pi)  # exact E[max] of 2 iid N(0,1)
    else:
        # Asymptotic E[max] of n iid N(0,1):
        # sqrt(2 ln n) - (ln ln n + ln 4pi) / (2 sqrt(2 ln n))
        log_n = math.log(n_trials)
        expected_max = math.sqrt(2.0 * log_n) - (
            (math.log(log_n) + math.log(4.0 * math.pi)) / (2.0 * math.sqrt(2.0 * log_n))
        )
    return observed_sqn - expected_max


def stationary_block_bootstrap(
    results: list[float],
    *,
    n_resamples: int = 2000,
    mean_block: int = 5,
    seed: int = 1729,
) -> dict:
    """Seeded stationary block bootstrap over per-trade R results.

    Returns the 95% CI for expectancy and p(expectancy <= 0). Deterministic
    for a fixed (results, n_resamples, mean_block, seed).
    """
    n = len(results)
    if n < 5:
        return {
            "expectancyLower95": None,
            "expectancyUpper95": None,
            "pExpectancyLeqZero": None,
            "resamples": 0,
            "seed": seed,
        }
    rng = random.Random(seed)
    restart_p = 1.0 / max(1, mean_block)
    means = []
    for _ in range(n_resamples):
        idx = rng.randrange(n)
        total = 0.0
        for _step in range(n):
            total += results[idx]
            if rng.random() < restart_p:
                idx = rng.randrange(n)
            else:
                idx = (idx + 1) % n
        means.append(total / n)
    means.sort()

    def _quantile(q: float) -> float:
        pos = q * (len(means) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(means) - 1)
        frac = pos - lo
        return means[lo] * (1.0 - frac) + means[hi] * frac

    p_leq_zero = sum(1 for value in means if value <= 0.0) / len(means)
    return {
        "expectancyLower95": round(_quantile(0.025), 4),
        "expectancyUpper95": round(_quantile(0.975), 4),
        "pExpectancyLeqZero": round(p_leq_zero, 4),
        "resamples": n_resamples,
        "seed": seed,
    }


def summarize_trades(
    trades: list[dict],
    *,
    n_trials: int = 1,
    bootstrap_seed: int = 1729,
) -> dict:
    """Full metric block for a chronological trade list (uses `resultR`)."""
    results = [float(t["resultR"]) for t in trades]
    observed_sqn = sqn(results)
    pf = profit_factor(results)
    boot = stationary_block_bootstrap(results, seed=bootstrap_seed)
    wr = win_rate(results)
    exp_r = expectancy(results)
    dsqn = deflated_sqn(observed_sqn, len(results), n_trials)
    return {
        "totalTrades": len(results),
        "winRate": round(wr, 2) if wr is not None else None,
        "expectancyR": round(exp_r, 4) if exp_r is not None else None,
        "totalR": round(sum(results), 4),
        "sqn": round(observed_sqn, 4) if observed_sqn is not None else None,
        "deflatedSqn": round(dsqn, 4) if dsqn is not None else None,
        "trialCount": n_trials,
        "profitFactor": (
            round(pf, 4) if pf is not None and math.isfinite(pf) else pf
        ),
        "maxDrawdownR": round(max_drawdown_r(results), 4),
        "equityCurve": equity_curve(results),
        "bootstrap": boot,
        **mfe_mae(trades),
    }


def normal_p_value_positive_expectancy(results: list[float]) -> float | None:
    """One-sided normal-approx p-value that true expectancy <= 0 (small helper
    for validation reporting; the bootstrap p is the primary figure)."""
    if len(results) < 2:
        return None
    std = stdev(results)
    if std <= 0:
        return None
    t_stat = math.sqrt(len(results)) * fmean(results) / std
    return round(1.0 - NormalDist().cdf(t_stat), 6)
