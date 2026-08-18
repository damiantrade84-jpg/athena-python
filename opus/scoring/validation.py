"""Evidence tests for whether an archetype's edge is real.

OPUS searches a grid: four archetypes across several asset classes. Searching a
grid and then reporting the best cell's Sharpe ratio is how backtests lie. The
maximum of N noise draws is positive and large by construction, so "the best
setup made 1.8 Sharpe" is not evidence of anything until it is compared against
what the best of N pure-noise trials would have produced anyway.

The deflated Sharpe ratio does exactly that comparison, and additionally
corrects for the non-normality of trade returns - which is severe for a system
with a hard stop and an open-ended target, where skew and kurtosis are large by
design rather than by accident.

Reported as a probability: DSR is P(true Sharpe > 0) given the trial count and
the shape of the return distribution. The configured gate is 0.90.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import opus.config as config

# Euler-Mascheroni constant, used in the expected-maximum-Sharpe expansion.
_EULER = 0.5772156649015329


@dataclass
class ValidationResult:
    bucket: str
    trades: int
    sharpe: float
    deflated_sharpe: float
    expected_max_sharpe: float
    skew: float
    kurtosis: float
    mean_r: float
    win_rate: float
    passed: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "trades": int(self.trades),
            "sharpe": round(float(self.sharpe), 4),
            "deflatedSharpe": round(float(self.deflated_sharpe), 4),
            "expectedMaxSharpe": round(float(self.expected_max_sharpe), 4),
            "skew": round(float(self.skew), 4),
            "kurtosis": round(float(self.kurtosis), 4),
            "meanR": round(float(self.mean_r), 4),
            "winRate": round(float(self.win_rate), 4),
            "passed": bool(self.passed),
            "reason": self.reason,
        }


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def _normal_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation)."""
    p = float(min(max(p, 1e-12), 1.0 - 1e-12))
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)


def expected_max_sharpe(trials: int, variance: float = 1.0) -> float:
    """Expected maximum Sharpe from `trials` independent NOISE strategies.

    This is the benchmark a real edge has to beat. With 24 trials it is around
    two standard errors above zero - so a Sharpe of 1.5 from a 24-cell search
    is entirely unremarkable.
    """
    n = max(int(trials), 1)
    if n == 1:
        return 0.0
    sd = math.sqrt(max(variance, 1e-12))
    a = _normal_ppf(1.0 - 1.0 / n)
    b = _normal_ppf(1.0 - 1.0 / (n * math.e))
    return float(sd * ((1.0 - _EULER) * a + _EULER * b))


def deflated_sharpe(
    returns: np.ndarray,
    *,
    trials: int,
    benchmark_sharpe: float | None = None,
) -> tuple[float, float, float]:
    """Return (deflated_sharpe_probability, observed_sharpe, benchmark).

    The probability is P(true Sharpe > benchmark), adjusting the standard error
    of the Sharpe estimate for skew and excess kurtosis.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = r.shape[0]
    if n < 8:
        return 0.0, 0.0, 0.0

    mu = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    if sd <= 1e-12:
        return 0.0, 0.0, 0.0

    sharpe = mu / sd
    centred = (r - mu) / sd
    skew = float(np.mean(centred ** 3))
    kurt = float(np.mean(centred ** 4))          # raw (normal == 3)

    if benchmark_sharpe is None:
        benchmark_sharpe = expected_max_sharpe(trials, variance=1.0) / math.sqrt(n)

    # Standard error of the Sharpe estimator under non-normality
    # (Mertens / Bailey-Lopez de Prado):
    #   var(SR) = (1 - skew*SR + (kurt-1)/4 * SR^2) / (n - 1)
    var_sr = (1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * sharpe ** 2) / (n - 1.0)
    if not np.isfinite(var_sr) or var_sr <= 0:
        return 0.0, float(sharpe), float(benchmark_sharpe)

    z = (sharpe - benchmark_sharpe) / math.sqrt(var_sr)
    return float(_normal_cdf(z)), float(sharpe), float(benchmark_sharpe)


def validate(
    bucket: str,
    r_multiples: np.ndarray,
    *,
    trials: int | None = None,
) -> ValidationResult:
    """Assess one archetype x asset-class bucket against its outcome history."""
    cfg = config.load()["VALIDATION"]
    min_trades = int(cfg["min_trades"])
    n_trials = int(trials if trials is not None else cfg["trials_assumed"])

    r = np.asarray(r_multiples, dtype=float)
    r = r[np.isfinite(r)]
    n = r.shape[0]

    if n < min_trades:
        return ValidationResult(
            bucket=bucket, trades=n, sharpe=0.0, deflated_sharpe=0.0,
            expected_max_sharpe=0.0, skew=0.0, kurtosis=0.0,
            mean_r=float(np.mean(r)) if n else 0.0,
            win_rate=float(np.mean(r > 0)) if n else 0.0,
            passed=False, reason=f"only_{n}_of_{min_trades}_trades",
        )

    dsr, sharpe, benchmark = deflated_sharpe(r, trials=n_trials)
    mu = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    centred = (r - mu) / sd if sd > 0 else np.zeros_like(r)

    threshold = float(cfg["deflated_sharpe_min"])
    passed = bool(dsr >= threshold and mu > 0.0)
    reason = "" if passed else (
        f"dsr_{dsr:.3f}_below_{threshold:.2f}" if mu > 0 else "negative_mean_r"
    )

    return ValidationResult(
        bucket=bucket,
        trades=n,
        sharpe=float(sharpe),
        deflated_sharpe=float(dsr),
        expected_max_sharpe=float(benchmark),
        skew=float(np.mean(centred ** 3)),
        kurtosis=float(np.mean(centred ** 4)),
        mean_r=mu,
        win_rate=float(np.mean(r > 0)),
        passed=passed,
        reason=reason,
    )


def enforced() -> bool:
    """Whether an unvalidated archetype is blocked from promoting to TRADE."""
    return bool(config.load()["VALIDATION"].get("enforce", False))


__all__ = [
    "ValidationResult", "expected_max_sharpe", "deflated_sharpe",
    "validate", "enforced",
]
