"""Research-integrity metrics for Athena backtests.

The goal here is to keep the implementation:
- lightweight (stdlib only)
- auditable
- additive to existing backtest outputs

We compute:
- PSR: Probabilistic Sharpe Ratio
- DSR: Deflated Sharpe Ratio
- PBO: Probability of Backtest Overfitting

Notes:
- PSR/DSR are derived from the observed Sharpe ratio and the return distribution.
- Exact PBO usually needs a parameter sweep / CSCV-style evaluation matrix. Athena's
  current backtests do not always expose that data, so this module reports the
  missing-data assumption explicitly instead of inventing a false precision number.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649015329


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _sample_variance(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    avg = _mean(values)
    if avg is None:
        return None
    return sum((x - avg) ** 2 for x in values) / (n - 1)


def sample_moments(returns: list[float]) -> dict[str, float | int | None]:
    """Return lightweight sample moments used by PSR/DSR.

    Skewness and kurtosis are the standardized central moments.
    Kurtosis here is Pearson kurtosis (normal == 3), which matches the
    common PSR/DSR formulas.
    """
    clean = [float(x) for x in returns if _safe_float(x) is not None]
    n = len(clean)
    if n == 0:
        return {
            "count": 0,
            "mean": None,
            "variance": None,
            "std": None,
            "skewness": None,
            "kurtosis": None,
        }

    avg = _mean(clean)
    var = _sample_variance(clean)
    std = math.sqrt(var) if var and var > 0 else None
    skewness = None
    kurtosis = None

    if std and std > 0 and n >= 3:
        m3 = sum((x - avg) ** 3 for x in clean) / n
        skewness = m3 / (std**3)

    if std and std > 0 and n >= 4:
        m4 = sum((x - avg) ** 4 for x in clean) / n
        kurtosis = m4 / (std**4)

    return {
        "count": n,
        "mean": avg,
        "variance": var,
        "std": std,
        "skewness": skewness,
        "kurtosis": kurtosis,
    }


def probabilistic_sharpe_ratio(
    observed_sharpe: float | None,
    returns: list[float] | None = None,
    *,
    benchmark_sharpe: float = 0.0,
    sample_size: int | None = None,
    skewness: float | None = None,
    kurtosis: float | None = None,
) -> dict[str, Any]:
    """Compute the Probabilistic Sharpe Ratio.

    Formula (Bailey / Lopez de Prado style):
      PSR(SR*) = Phi( (SR_hat - SR*) * sqrt(n - 1) /
                      sqrt(1 - gamma3*SR_hat + ((gamma4 - 1)/4)*SR_hat^2) )
    """
    sr = _safe_float(observed_sharpe)
    if sr is None:
        return {
            "available": False,
            "value": None,
            "benchmarkSharpe": benchmark_sharpe,
            "sampleCount": 0,
            "note": "observed_sharpe_missing",
        }

    moments = sample_moments(returns or [])
    n = int(sample_size or moments["count"] or 0)
    gamma3 = _safe_float(skewness)
    gamma4 = _safe_float(kurtosis)
    if gamma3 is None:
        gamma3 = _safe_float(moments["skewness"])
    if gamma4 is None:
        gamma4 = _safe_float(moments["kurtosis"])

    if n < 2:
        return {
            "available": False,
            "value": None,
            "benchmarkSharpe": benchmark_sharpe,
            "sampleCount": n,
            "note": "insufficient_samples_for_psr",
        }

    gamma3 = gamma3 if gamma3 is not None else 0.0
    gamma4 = gamma4 if gamma4 is not None else 3.0
    denom_term = 1.0 - gamma3 * sr + (((gamma4 - 1.0) / 4.0) * (sr**2))
    if denom_term <= 0:
        return {
            "available": False,
            "value": None,
            "benchmarkSharpe": benchmark_sharpe,
            "sampleCount": n,
            "note": "non_positive_psr_denominator",
        }

    z_score = (sr - benchmark_sharpe) * math.sqrt(n - 1.0) / math.sqrt(denom_term)
    return {
        "available": True,
        "value": round(_NORMAL.cdf(z_score), 6),
        "benchmarkSharpe": round(float(benchmark_sharpe), 6),
        "sampleCount": n,
        "zScore": round(z_score, 6),
        "skewness": round(gamma3, 6),
        "kurtosis": round(gamma4, 6),
    }


def _expected_max_sharpe(num_trials: int, sr_std_error: float) -> float:
    """Approximate expected maximum Sharpe from multiple trials.

    This is the lightweight closed-form approximation commonly used when the
    exact multiple-testing distribution is unavailable.
    """
    if num_trials <= 1 or sr_std_error <= 0:
        return 0.0
    z1 = _NORMAL.inv_cdf(1.0 - (1.0 / num_trials))
    z2 = _NORMAL.inv_cdf(1.0 - (1.0 / (num_trials * math.e)))
    return sr_std_error * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def deflated_sharpe_ratio(
    observed_sharpe: float | None,
    returns: list[float] | None = None,
    *,
    sample_size: int | None = None,
    skewness: float | None = None,
    kurtosis: float | None = None,
    num_trials: int | None = None,
) -> dict[str, Any]:
    """Compute the Deflated Sharpe Ratio.

    If ``num_trials`` is unavailable, we assume a single tested strategy and make
    that assumption explicit in the output. In that case DSR collapses to a
    PSR-style test against a zero benchmark Sharpe.
    """
    sr = _safe_float(observed_sharpe)
    moments = sample_moments(returns or [])
    n = int(sample_size or moments["count"] or 0)
    assumptions = []
    trials = int(num_trials) if num_trials and int(num_trials) > 0 else 1
    if not num_trials:
        assumptions.append("num_trials_unavailable_assumed_1")

    if sr is None or n < 2:
        return {
            "available": False,
            "value": None,
            "benchmarkSharpe": None,
            "sampleCount": n,
            "numTrials": trials,
            "assumptions": assumptions + ["insufficient_inputs_for_dsr"],
        }

    gamma3 = _safe_float(skewness)
    gamma4 = _safe_float(kurtosis)
    if gamma3 is None:
        gamma3 = _safe_float(moments["skewness"])
    if gamma4 is None:
        gamma4 = _safe_float(moments["kurtosis"])
    gamma3 = gamma3 if gamma3 is not None else 0.0
    gamma4 = gamma4 if gamma4 is not None else 3.0

    sr_var_term = 1.0 - gamma3 * sr + (((gamma4 - 1.0) / 4.0) * (sr**2))
    if sr_var_term <= 0:
        return {
            "available": False,
            "value": None,
            "benchmarkSharpe": None,
            "sampleCount": n,
            "numTrials": trials,
            "assumptions": assumptions + ["non_positive_dsr_variance_term"],
        }

    sr_std_error = math.sqrt(sr_var_term / max(1.0, n - 1.0))
    benchmark_sharpe = _expected_max_sharpe(trials, sr_std_error)
    psr = probabilistic_sharpe_ratio(
        sr,
        returns or [],
        benchmark_sharpe=benchmark_sharpe,
        sample_size=n,
        skewness=gamma3,
        kurtosis=gamma4,
    )
    return {
        "available": bool(psr.get("available")),
        "value": psr.get("value"),
        "benchmarkSharpe": round(benchmark_sharpe, 6),
        "sampleCount": n,
        "numTrials": trials,
        "assumptions": assumptions,
    }


def probability_of_backtest_overfitting(
    *,
    in_sample_scores: list[float] | None = None,
    out_of_sample_scores: list[float] | None = None,
    chosen_index: int | None = None,
) -> dict[str, Any]:
    """Estimate PBO when trial/sweep scores are available.

    Exact CSCV can be runtime-heavy. This lightweight implementation only reports
    a rank-based estimate when we have multiple in-sample and out-of-sample trial
    scores. If those inputs are unavailable, we return an explicit unavailable
    payload instead of guessing.
    """
    is_scores = [float(x) for x in (in_sample_scores or []) if _safe_float(x) is not None]
    oos_scores = [float(x) for x in (out_of_sample_scores or []) if _safe_float(x) is not None]
    if len(is_scores) != len(oos_scores) or len(is_scores) < 2:
        return {
            "available": False,
            "value": None,
            "method": "not_computed",
            "note": "pbo_requires_multiple_trial_scores_exact_sweep_unavailable",
            "runtimeHeavy": True,
        }

    idx = int(chosen_index) if chosen_index is not None else max(range(len(is_scores)), key=is_scores.__getitem__)
    sorted_oos = sorted(oos_scores)
    oos_rank = sorted_oos.index(oos_scores[idx]) + 1
    percentile = oos_rank / len(sorted_oos)
    # With only a single fold of trial rankings, the most auditable estimate is whether
    # the IS winner landed in the bottom half OOS. We surface that as a 0/1 estimate.
    pbo_value = 1.0 if percentile <= 0.5 else 0.0
    return {
        "available": True,
        "value": round(pbo_value, 6),
        "method": "single_fold_rank_proxy",
        "selectedIndex": idx,
        "selectedOOSPercentile": round(percentile, 6),
        "note": "exact_cscv_not_run_lightweight_rank_proxy_used",
        "runtimeHeavy": False,
    }


def build_research_metrics(
    returns: list[float] | None,
    *,
    observed_sharpe: float | None,
    in_sample_scores: list[float] | None = None,
    out_of_sample_scores: list[float] | None = None,
    chosen_index: int | None = None,
    num_trials: int | None = None,
) -> dict[str, Any]:
    """Build a single additive research-metrics payload for backtest summaries."""
    clean_returns = [float(x) for x in (returns or []) if _safe_float(x) is not None]
    moments = sample_moments(clean_returns)
    assumptions = []
    runtime_heavy = []

    psr = probabilistic_sharpe_ratio(
        observed_sharpe,
        clean_returns,
        sample_size=int(moments["count"] or 0),
        skewness=_safe_float(moments["skewness"]),
        kurtosis=_safe_float(moments["kurtosis"]),
    )

    dsr = deflated_sharpe_ratio(
        observed_sharpe,
        clean_returns,
        sample_size=int(moments["count"] or 0),
        skewness=_safe_float(moments["skewness"]),
        kurtosis=_safe_float(moments["kurtosis"]),
        num_trials=num_trials,
    )
    assumptions.extend(dsr.get("assumptions", []))

    pbo = probability_of_backtest_overfitting(
        in_sample_scores=in_sample_scores,
        out_of_sample_scores=out_of_sample_scores,
        chosen_index=chosen_index,
    )
    if pbo.get("note"):
        assumptions.append(str(pbo["note"]))
    if pbo.get("runtimeHeavy"):
        runtime_heavy.append("exact_cscv_pbo_not_run")

    return {
        "tradeCount": int(moments["count"] or 0),
        "sampleMoments": {
            "mean": round(moments["mean"], 6) if moments["mean"] is not None else None,
            "variance": round(moments["variance"], 6) if moments["variance"] is not None else None,
            "skewness": round(moments["skewness"], 6) if moments["skewness"] is not None else None,
            "kurtosis": round(moments["kurtosis"], 6) if moments["kurtosis"] is not None else None,
        },
        "psr": psr,
        "dsr": dsr,
        "pbo": pbo,
        "assumptions": assumptions,
        "runtimeHeavy": runtime_heavy,
    }


def enrich_backtest_summary(
    summary: dict[str, Any],
    *,
    returns: list[float] | None = None,
    num_trials: int | None = None,
    in_sample_scores: list[float] | None = None,
    out_of_sample_scores: list[float] | None = None,
    chosen_index: int | None = None,
) -> dict[str, Any]:
    """Attach research metrics to an existing backtest summary.

    If ``returns`` is not passed, we derive them from ``summary['trades']`` when present.
    """
    out = dict(summary or {})
    if "error" in out:
        return out

    derived_returns = []
    if returns is None:
        for trade in out.get("trades", []) or []:
            value = _safe_float(trade.get("resultR", trade.get("r_multiple")))
            if value is not None:
                derived_returns.append(value)
    else:
        derived_returns = [float(x) for x in returns if _safe_float(x) is not None]

    out["researchMetrics"] = build_research_metrics(
        derived_returns,
        observed_sharpe=_safe_float(out.get("sharpe")),
        num_trials=num_trials,
        in_sample_scores=in_sample_scores,
        out_of_sample_scores=out_of_sample_scores,
        chosen_index=chosen_index,
    )
    return out
