"""KDI - Kinetic Drift Index.

Momentum measured as physics rather than as an oscillator.

Two markets can post an identical net change over forty bars: one walked there
in a straight line, the other travelled four times the distance wandering and
happened to end up in the same place. Every rate-of-change indicator scores
them the same. They are not the same - the first is a trend you can join, the
second is chop that will stop you out.

KDI multiplies normalised displacement by *fractal efficiency* (net move over
path length) raised to a punishing exponent, so a move only counts to the
extent it went somewhere directly. Jump ratio is exported alongside: a leg made
of discrete repricings is institutional, a continuous grind of equal size is
not, and the continuation setups care about the difference.
"""

from __future__ import annotations

import numpy as np

import opus.config as config
from opus.core.stats import fractal_efficiency, jump_ratio, squash
from opus.indicators.base import MarketContext
from opus.types import IndicatorReading


def kinetics(ctx: MarketContext) -> dict:
    """Raw kinetic measurements, shared with the regime classifier."""

    def _build() -> dict:
        cfg = config.indicator("KDI")
        candles = ctx.structure
        close = candles.close
        n = close.shape[0]
        fast = int(cfg["fast"])
        slow = int(cfg["slow"])
        out = {
            "driftFast": 0.0, "driftSlow": 0.0, "accel": 0.0,
            "efficiency": 0.0, "jumpRatio": 0.0, "bars": int(n),
        }
        if n < slow + 2 or not ctx.has_sigma:
            return out

        sigma = ctx.sigma
        last = float(close[-1])
        drift_fast = (last - float(close[-1 - fast])) / sigma
        drift_slow = (last - float(close[-1 - slow])) / sigma

        # Compare like with like: scale the slow leg to the fast horizon before
        # differencing, otherwise "acceleration" is just the horizon mismatch.
        drift_slow_at_fast = drift_slow * (fast / float(slow))
        accel = drift_fast - drift_slow_at_fast

        out.update(
            driftFast=float(drift_fast),
            driftSlow=float(drift_slow),
            accel=float(accel),
            efficiency=float(fractal_efficiency(close, int(cfg["efficiency_window"]))),
            jumpRatio=float(jump_ratio(close, int(cfg["jump_window"]))),
        )
        return out

    return ctx.cached("kinetics", _build)


def compute(ctx: MarketContext) -> IndicatorReading:
    cfg = config.indicator("KDI")
    k = kinetics(ctx)
    if k["bars"] < int(cfg["slow"]) + 2 or not ctx.has_sigma:
        return IndicatorReading("KDI", 0.0, 0.0, {"reason": "insufficient_bars"})

    gamma = float(cfg["efficiency_gamma"])
    efficiency = max(k["efficiency"], 0.0)
    quality = float(np.power(efficiency, gamma))

    # Displacement that actually went somewhere, plus a smaller acceleration
    # term so a decelerating trend scores below an accelerating one of equal
    # size.
    raw = (k["driftFast"] * quality) + 0.5 * (k["accel"] * quality)
    value = squash(raw, float(cfg["drift_scale"]))

    # Confidence tracks efficiency directly: in chop, the drift number is real
    # but it is not information, and the conviction stack should know that.
    confidence = float(np.clip(efficiency * 1.6, 0.0, 1.0))

    return IndicatorReading(
        "KDI",
        value,
        confidence,
        {
            "driftFastSigma": round(k["driftFast"], 4),
            "driftSlowSigma": round(k["driftSlow"], 4),
            "accel": round(k["accel"], 4),
            "efficiency": round(efficiency, 4),
            "efficiencyQuality": round(quality, 4),
            "jumpRatio": round(k["jumpRatio"], 4),
            "rawScore": round(raw, 4),
        },
    )


__all__ = ["compute", "kinetics"]
