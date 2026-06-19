"""Metrics for the Engine A factor-ablation harness (research-only).

All metrics operate on a list of per-trade R multiples (already net of costs).
Nothing here connects to execution.
"""

from __future__ import annotations

import random
from math import erf, sqrt
from statistics import fmean, pstdev, stdev
from typing import Mapping, Sequence


def summarize(results: Sequence[float]) -> dict:
    r = [float(x) for x in results]
    n = len(r)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "expectancy": 0.0, "sqn": 0.0,
                "profit_factor": None, "total_r": 0.0, "max_dd_r": 0.0, "sharpe": 0.0}
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    exp = fmean(r)
    sd = stdev(r) if n > 1 else 0.0
    sqn = (exp / sd * sqrt(n)) if (n > 1 and sd > 0) else 0.0
    sharpe = (exp / sd) if sd > 0 else 0.0
    equity = peak = max_dd = 0.0
    for x in r:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "n": n,
        "win_rate": round(len(wins) / n * 100, 2),
        "expectancy": round(exp, 4),
        "sqn": round(sqn, 4),
        "profit_factor": round(gp / gl, 4) if gl > 0 else None,
        "total_r": round(sum(r), 4),
        "max_dd_r": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
    }


def bootstrap_ci(results: Sequence[float], *, n_boot: int = 2000, alpha: float = 0.05,
                 seed: int = 13) -> tuple[float, float]:
    r = [float(x) for x in results]
    if len(r) < 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = []
    n = len(r)
    for _ in range(n_boot):
        means.append(fmean(rng.choices(r, k=n)))
    means.sort()
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot) - 1]
    return (round(lo, 4), round(hi, 4))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def deflated_sharpe(results: Sequence[float], *, n_trials: int = 1) -> dict:
    """Probabilistic / deflated Sharpe (Bailey & Lopez de Prado, per-trade basis).

    Returns the probability the true Sharpe > 0 after a multiple-testing haircut
    for `n_trials` independent variants. Skew/kurtosis adjusted.
    """
    r = [float(x) for x in results]
    n = len(r)
    if n < 8:
        return {"sharpe": 0.0, "psr_gt0": None, "dsr": None, "n": n, "note": "n<8"}
    mu = fmean(r)
    sd = pstdev(r)
    if sd <= 0:
        return {"sharpe": 0.0, "psr_gt0": None, "dsr": None, "n": n, "note": "zero_var"}
    sr = mu / sd
    # sample skew / kurtosis
    m3 = fmean([((x - mu) / sd) ** 3 for x in r])
    m4 = fmean([((x - mu) / sd) ** 4 for x in r])
    # Expected max Sharpe under n_trials (the multiple-testing benchmark).
    gamma = 0.5772156649
    e = 2.718281828459045
    if n_trials > 1:
        z1 = _inv_norm(1.0 - 1.0 / n_trials)
        z2 = _inv_norm(1.0 - 1.0 / (n_trials * e))
        sr0 = (z1 * (1 - gamma) + z2 * gamma) / sqrt(n)  # per-trade benchmark
    else:
        sr0 = 0.0
    denom = sqrt(max(1e-12, 1.0 - m3 * sr + (m4 - 1.0) / 4.0 * sr * sr))
    psr0 = _norm_cdf((sr - 0.0) * sqrt(n - 1) / denom)
    dsr = _norm_cdf((sr - sr0) * sqrt(n - 1) / denom)
    return {"sharpe": round(sr, 4), "psr_gt0": round(psr0, 4),
            "dsr": round(dsr, 4), "sr_benchmark": round(sr0, 5), "n": n,
            "n_trials": n_trials}


def _inv_norm(p: float) -> float:
    """Acklam's inverse normal CDF approximation."""
    p = min(max(p, 1e-9), 1 - 1e-9)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = sqrt(-2 * _ln(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = sqrt(-2 * _ln(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    rr = q * q
    return (((((a[0]*rr+a[1])*rr+a[2])*rr+a[3])*rr+a[4])*rr+a[5])*q / \
           (((((b[0]*rr+b[1])*rr+b[2])*rr+b[3])*rr+b[4])*rr+1)


def _ln(x: float) -> float:
    from math import log
    return log(x)


def pbo(variant_results: Mapping[str, Sequence[float]], *, folds: int = 8) -> dict:
    """Lightweight Probability of Backtest Overfitting (CSCV-style proxy).

    Splits each variant's trade sequence into `folds` contiguous folds. For each
    fold used as OOS (rest as IS), pick the IS-best variant by expectancy and
    record whether it ranks below the median variant OOS. PBO = fraction of
    folds where the IS-winner is OOS-mediocre.
    """
    names = [k for k, v in variant_results.items() if len(v) >= folds]
    if len(names) < 2:
        return {"pbo": None, "note": "insufficient_variants_or_trades"}
    series = {k: list(map(float, variant_results[k])) for k in names}
    n_min = min(len(series[k]) for k in names)
    folds = min(folds, n_min)
    if folds < 2:
        return {"pbo": None, "note": "too_few_trades"}
    bounds = [round(i * n_min / folds) for i in range(folds + 1)]
    overfit = 0
    counted = 0
    for f in range(folds):
        oos_lo, oos_hi = bounds[f], bounds[f + 1]
        is_exp, oos_exp = {}, {}
        for k in names:
            s = series[k][:n_min]
            oos = s[oos_lo:oos_hi]
            is_ = s[:oos_lo] + s[oos_hi:]
            if not oos or not is_:
                continue
            is_exp[k] = fmean(is_)
            oos_exp[k] = fmean(oos)
        if len(is_exp) < 2:
            continue
        is_best = max(is_exp, key=lambda k: is_exp[k])
        ranked = sorted(oos_exp, key=lambda k: oos_exp[k])
        rank = ranked.index(is_best)
        overfit += 1 if rank < len(ranked) / 2.0 else 0
        counted += 1
    return {"pbo": round(overfit / counted, 4) if counted else None,
            "folds": folds, "variants": len(names)}


def score_return_monotonicity(bucketed: Mapping[str, Sequence[float]]) -> dict:
    """Spearman-ish monotonicity between ordered score buckets and mean R.

    `bucketed` keys are ordered bucket labels (sortable); values are R lists."""
    labels = sorted(bucketed.keys())
    means = [fmean(bucketed[l]) if bucketed[l] else 0.0 for l in labels]
    counts = [len(bucketed[l]) for l in labels]
    k = len(means)
    if k < 2:
        return {"buckets": dict(zip(labels, [round(m, 4) for m in means])),
                "spearman": None, "n_buckets": k}
    ranks_x = list(range(k))
    order = sorted(range(k), key=lambda i: means[i])
    ranks_y = [0] * k
    for r, i in enumerate(order):
        ranks_y[i] = r
    dx = fmean(ranks_x)
    dy = fmean(ranks_y)
    num = sum((ranks_x[i] - dx) * (ranks_y[i] - dy) for i in range(k))
    den = sqrt(sum((ranks_x[i] - dx) ** 2 for i in range(k)) *
               sum((ranks_y[i] - dy) ** 2 for i in range(k)))
    rho = num / den if den > 0 else 0.0
    return {"buckets": {l: {"mean_r": round(m, 4), "n": c}
                        for l, m, c in zip(labels, means, counts)},
            "spearman": round(rho, 4), "n_buckets": k}


def concentration(by_symbol_total_r: Mapping[str, float]) -> dict:
    """Top-symbol share of gross positive R + Herfindahl index."""
    vals = {k: float(v) for k, v in by_symbol_total_r.items()}
    pos = {k: v for k, v in vals.items() if v > 0}
    gp = sum(pos.values())
    if gp <= 0:
        return {"top_symbol": None, "top_share": None, "hhi": None}
    shares = {k: v / gp for k, v in pos.items()}
    top = max(shares, key=lambda k: shares[k])
    hhi = sum(s * s for s in shares.values())
    return {"top_symbol": top, "top_share": round(shares[top], 4),
            "hhi": round(hhi, 4), "n_profitable_symbols": len(pos)}
