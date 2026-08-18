"""Turning a conviction score into a probability that means something.

A conviction of 0.6 is not a 60% chance of anything. It is an ordinal position
on a scale the engine invented. Sizing, and the expectancy gate, need a real
probability, so OPUS learns the mapping from its own realised outcomes.

Labelling uses the triple-barrier construction: every signal is resolved by
whichever comes first - its target, its stop, or a time limit. That mirrors how
the position actually resolves, unlike a fixed-horizon return which counts a
trade as a winner even when it was stopped out two bars in.

Two details that decide whether this is honest:

  * When a single bar contains BOTH the stop and the target, intrabar sequence
    is unknown. OPUS resolves those to the STOP. Assuming the target would
    inflate every measured hit rate, and the inflation lands hardest on exactly
    the wide-bar, high-volatility cases where it is least safe to assume.

  * The fitted logistic is SHRUNK toward a conservative prior by sample count.
    Forty trades is not enough to learn a slope, and an unshrunk fit on forty
    samples will happily report 0.85 probabilities that size positions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import opus.config as config
from opus.core.stats import logistic
from opus.types import Direction


@dataclass
class Outcome:
    """Resolution of one signal under the triple-barrier rule."""

    label: int              # 1 = target reached first, 0 = stop or adverse time exit
    r_multiple: float
    resolution: str         # "target" | "stop" | "time" | "ambiguous_stop"
    bars_held: int
    exit_price: float

    def as_dict(self) -> dict:
        return {
            "label": int(self.label),
            "rMultiple": round(float(self.r_multiple), 4),
            "resolution": self.resolution,
            "barsHeld": int(self.bars_held),
            "exitPrice": round(float(self.exit_price), 8),
        }


def triple_barrier(
    *,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    entry: float,
    stop: float,
    target: float,
    direction: Direction,
    max_bars: int,
) -> Outcome | None:
    """Resolve a trade against future bars. `high/low/close` start AFTER entry."""
    n = int(min(len(close), max_bars))
    if n <= 0:
        return None
    risk = abs(float(entry) - float(stop))
    if risk <= 0:
        return None
    sign = float(direction.sign)

    for i in range(n):
        h, l = float(high[i]), float(low[i])
        if not (np.isfinite(h) and np.isfinite(l)):
            continue

        if direction is Direction.LONG:
            hit_stop = l <= stop
            hit_target = h >= target
        else:
            hit_stop = h >= stop
            hit_target = l <= target

        if hit_stop and hit_target:
            # Both barriers inside one bar. The true sequence is unobservable
            # at this resolution, so resolve adversely rather than guessing in
            # our own favour.
            return Outcome(
                label=0, r_multiple=-1.0, resolution="ambiguous_stop",
                bars_held=i + 1, exit_price=float(stop),
            )
        if hit_stop:
            return Outcome(
                label=0, r_multiple=-1.0, resolution="stop",
                bars_held=i + 1, exit_price=float(stop),
            )
        if hit_target:
            r = abs(float(target) - float(entry)) / risk
            return Outcome(
                label=1, r_multiple=float(r), resolution="target",
                bars_held=i + 1, exit_price=float(target),
            )

    # Time barrier: mark to the last close.
    last = float(close[n - 1])
    r = sign * (last - float(entry)) / risk
    return Outcome(
        label=1 if r > 0 else 0, r_multiple=float(r), resolution="time",
        bars_held=n, exit_price=last,
    )


def fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = 1.0,
    max_iter: int = 60,
    tol: float = 1e-8,
) -> tuple[float, float] | None:
    """Single-feature logistic regression by IRLS. Returns (slope, intercept).

    L2 regularisation is always on: with separable data - which happens
    routinely when a small sample has every high-conviction signal winning -
    an unregularised fit diverges toward infinite slope and reports certainty.
    """
    x = np.asarray(x, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = x.shape[0]
    if n < 8:
        return None
    if np.unique(y).shape[0] < 2:
        return None  # all wins or all losses: no slope is identifiable

    design = np.column_stack([x, np.ones(n)])
    beta = np.zeros(2)
    penalty = np.diag([float(l2), 0.0])  # never penalise the intercept

    for _ in range(max_iter):
        eta = design @ beta
        eta = np.clip(eta, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(p * (1.0 - p), 1e-9, None)

        gradient = design.T @ (y - p) - penalty @ beta
        hessian = (design.T * w) @ design + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return None
        beta = beta + step
        if not np.all(np.isfinite(beta)):
            return None
        if float(np.max(np.abs(step))) < tol:
            break

    slope, intercept = float(beta[0]), float(beta[1])
    if not (np.isfinite(slope) and np.isfinite(intercept)):
        return None
    return slope, intercept


@dataclass
class Calibrator:
    """Effective (shrunk) logistic parameters for one bucket."""

    slope: float
    intercept: float
    samples: int
    fitted: bool
    bucket: str

    def probability(self, conviction: float) -> float:
        cfg = config.load()["CALIBRATION"]
        p = logistic(self.slope * float(conviction) + self.intercept)
        floor = float(cfg["probability_floor"])
        ceiling = float(cfg["probability_ceiling"])
        return float(np.clip(p, floor, ceiling))

    def as_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "slope": round(float(self.slope), 5),
            "intercept": round(float(self.intercept), 5),
            "samples": int(self.samples),
            "fitted": bool(self.fitted),
        }


def bucket_key(archetype: str, asset_class: str) -> str:
    return f"{archetype}|{asset_class}"


def prior_calibrator(bucket: str) -> Calibrator:
    cfg = config.load()["CALIBRATION"]
    return Calibrator(
        slope=float(cfg["prior_slope"]),
        intercept=float(cfg["prior_intercept"]),
        samples=0,
        fitted=False,
        bucket=bucket,
    )


def build_calibrator(
    bucket: str,
    convictions: np.ndarray | None,
    labels: np.ndarray | None,
) -> Calibrator:
    """Fit, then shrink toward the prior in proportion to sample size."""
    cfg = config.load()["CALIBRATION"]
    prior = prior_calibrator(bucket)

    if not bool(cfg.get("enabled", True)):
        return prior
    if convictions is None or labels is None:
        return prior

    x = np.asarray(convictions, dtype=float)
    y = np.asarray(labels, dtype=float)
    n = int(min(x.shape[0], y.shape[0]))
    if n < int(cfg["min_samples_fit"]):
        return Calibrator(prior.slope, prior.intercept, n, False, bucket)

    cap = int(cfg["max_samples_fit"])
    if n > cap:                     # keep the most recent window
        x, y, n = x[-cap:], y[-cap:], cap

    fitted = fit_logistic(x, y)
    if fitted is None:
        return Calibrator(prior.slope, prior.intercept, n, False, bucket)

    slope_fit, intercept_fit = fitted
    n0 = float(cfg["shrinkage_n0"])
    weight = n / (n + n0)

    slope = weight * slope_fit + (1.0 - weight) * prior.slope
    intercept = weight * intercept_fit + (1.0 - weight) * prior.intercept

    # A negative slope means higher conviction predicted WORSE outcomes in
    # this bucket. That is a real and important finding, but it must not be
    # used to size trades - it would invert the model. Fall back to the prior
    # and let the validation layer surface the anomaly instead.
    if slope <= 0.0:
        return Calibrator(prior.slope, prior.intercept, n, False, bucket)

    return Calibrator(float(slope), float(intercept), n, True, bucket)


__all__ = [
    "Outcome", "triple_barrier", "fit_logistic", "Calibrator",
    "bucket_key", "prior_calibrator", "build_calibrator",
]
