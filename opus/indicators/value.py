"""SAV - Session Anchored Value.

Auction logic, made numeric. Each session builds its own volume-weighted value
area from its own open. Two facts matter intraday:

  1. Price near accepted value drifts WITH the direction value is migrating.
  2. Price stretched far from value tends to be returned to it.

Those are opposite behaviours, and which one applies is a function of distance,
not of opinion. SAV encodes that as a single continuous curve: the acceptance
term grows with distance then decays to nothing, while a fade term switches on
only beyond the outer band. No regime flag is needed to switch between them -
the geometry does it.

`rotation` (crossings of the anchor per bar) is exported because it is the
cleanest available measure of whether an auction is balancing or trending, and
the regime classifier consumes it directly.
"""

from __future__ import annotations

import numpy as np

import opus.config as config
from opus.core.stats import squash
from opus.indicators.base import MarketContext, session_for_ts, session_start_index
from opus.types import IndicatorReading


def _anchored_value(candles, start_idx: int) -> dict | None:
    """Volume-weighted anchored VWAP and dispersion from `start_idx`.

    Falls back to equal (time) weighting when volume is unusable, and says so,
    rather than dividing by a zero volume sum and emitting a silent NaN.
    """
    h = candles.high[start_idx:]
    lo = candles.low[start_idx:]
    c = candles.close[start_idx:]
    v = candles.volume[start_idx:]
    n = c.shape[0]
    if n < 2:
        return None

    typical = (h + lo + c) / 3.0
    ok = np.isfinite(typical)
    if int(ok.sum()) < 2:
        return None

    weights = np.where(np.isfinite(v) & (v > 0), v, 0.0)
    volume_backed = bool(weights.sum() > 0 and np.count_nonzero(weights) >= n * 0.5)
    if not volume_backed:
        weights = np.ones(n)
    weights = np.where(ok, weights, 0.0)
    total = float(weights.sum())
    if total <= 0:
        return None

    vwap = float(np.sum(weights * np.nan_to_num(typical)) / total)
    var = float(np.sum(weights * (np.nan_to_num(typical) - vwap) ** 2) / total)
    dispersion = float(np.sqrt(max(var, 0.0)))

    # Rotation: sign changes of (close - vwap) per bar. A balancing auction
    # crosses its own value repeatedly; a trending one leaves and stays gone.
    rel = np.nan_to_num(c - vwap)
    signs = np.sign(rel)
    signs = signs[signs != 0]
    crossings = int(np.count_nonzero(np.diff(signs))) if signs.shape[0] > 1 else 0
    rotation = crossings / max(n - 1, 1)

    return {
        "vwap": vwap,
        "dispersion": dispersion,
        "bars": n,
        "rotation": float(rotation),
        "crossings": crossings,
        "volumeBacked": volume_backed,
    }


def session_value(ctx: MarketContext) -> dict | None:
    """Current-session anchored value, computed on the trigger timeframe.

    The trigger rung is used because a session holds 3-5x more trigger bars
    than structure bars, and an anchored VWAP built from ~10 samples is not a
    value area, it is a rounding error.
    """
    def _build() -> dict | None:
        candles = ctx.trigger if len(ctx.trigger) >= 20 else ctx.structure
        if len(candles) < 10:
            return None
        cfg = config.indicator("SAV")
        min_bars = int(cfg["min_bars"])
        start = session_start_index(candles.ts, float(candles.ts[-1]))
        anchor = "session"

        if candles.ts.shape[0] - start < min_bars:
            # The session has only just opened. Returning None here would blind
            # SAV - and therefore the whole regime classifier, which reads
            # rotation from it - for the first ~40 minutes of every session,
            # including the London open. Instead fall back to a rolling
            # developing value over the trailing window and label it honestly;
            # confidence downstream is reduced for a rolling anchor.
            start = max(0, candles.ts.shape[0] - min_bars * 4)
            anchor = "rolling"
            if candles.ts.shape[0] - start < min_bars:
                return None

        value = _anchored_value(candles, start)
        if value is None:
            return None
        value["sessionName"] = session_for_ts(float(candles.ts[-1]))
        value["startIndex"] = int(start)
        value["timeframe"] = candles.timeframe
        value["anchor"] = anchor

        # Prior session's value, for the migration term.
        prev = None
        if start > 2:
            prev_name = session_for_ts(float(candles.ts[start - 1]))
            j = start - 1
            while j > 0 and session_for_ts(float(candles.ts[j - 1])) == prev_name:
                j -= 1
            if start - j >= int(cfg["min_bars"]):
                # Explicit [j, start) window: _anchored_value always runs to
                # the end of the series, which would blend the prior session's
                # value with the current one.
                prev = _anchored_value_range(candles, j, start)
        value["priorVwap"] = prev["vwap"] if prev else None
        return value

    return ctx.cached("session_value", _build)


def _anchored_value_range(candles, start_idx: int, end_idx: int) -> dict | None:
    """Anchored value over an explicit [start, end) window."""
    h = candles.high[start_idx:end_idx]
    lo = candles.low[start_idx:end_idx]
    c = candles.close[start_idx:end_idx]
    v = candles.volume[start_idx:end_idx]
    n = c.shape[0]
    if n < 2:
        return None
    typical = (h + lo + c) / 3.0
    weights = np.where(np.isfinite(v) & (v > 0), v, 0.0)
    if weights.sum() <= 0:
        weights = np.ones(n)
    weights = np.where(np.isfinite(typical), weights, 0.0)
    total = float(weights.sum())
    if total <= 0:
        return None
    vwap = float(np.sum(weights * np.nan_to_num(typical)) / total)
    var = float(np.sum(weights * (np.nan_to_num(typical) - vwap) ** 2) / total)
    return {"vwap": vwap, "dispersion": float(np.sqrt(max(var, 0.0))), "bars": n}


def compute(ctx: MarketContext) -> IndicatorReading:
    cfg = config.indicator("SAV")
    value = session_value(ctx)
    if value is None:
        return IndicatorReading("SAV", 0.0, 0.0, {"reason": "no_session_value"})

    price = ctx.last_price
    dispersion = float(value["dispersion"])
    if price <= 0 or dispersion <= 0:
        return IndicatorReading(
            "SAV", 0.0, 0.1,
            {"reason": "degenerate_value", "vwap": value["vwap"]},
        )

    stretch = (price - float(value["vwap"])) / dispersion

    # Sanity bound. Real intraday price does not trade 18 sigma from its own
    # session VWAP; if the arithmetic says it does, the inputs disagree - a
    # stale trigger feed, a wrong symbol mapping, or a quote from a different
    # venue than the candles. Left unguarded this produces a saturated
    # maximum-conviction fade off corrupt data, which is the worst possible
    # failure mode. Fail closed instead.
    max_stretch = float(cfg.get("max_stretch_sigma", 6.0))
    if abs(stretch) > max_stretch:
        return IndicatorReading(
            "SAV", 0.0, 0.0,
            {
                "reason": "stretch_out_of_range",
                "stretchSigma": round(float(stretch), 3),
                "maxStretchSigma": max_stretch,
                "vwap": round(float(value["vwap"]), 8),
                "price": round(float(price), 8),
                "anchor": value.get("anchor", "session"),
                "detail": "price vs session value implies inconsistent feeds",
            },
        )

    # Acceptance: drift with value while price is near it, decaying to zero as
    # the excursion becomes statistically unusual.
    acceptance = stretch * float(np.exp(-((stretch / 2.2) ** 2)))

    # Fade: switches on only beyond the outer band, and is bounded so a single
    # extreme excursion cannot saturate the whole reading on its own.
    outer = float(cfg["band_sigma_mult"][1])
    excess = min(max(abs(stretch) - outer, 0.0), float(cfg.get("max_fade_sigma", 2.5)))
    fade = -np.sign(stretch) * excess

    # Migration: where value itself is moving, in sigma of the instrument.
    migration = 0.0
    prior = value.get("priorVwap")
    if prior and ctx.has_sigma:
        migration = (float(value["vwap"]) - float(prior)) / ctx.sigma
        migration = float(np.clip(migration / float(cfg["migration_scale"]), -1.5, 1.5))

    # Term budget matters as much as the terms. Fade must dominate when price
    # is genuinely extended - that is the whole reason to fade - while
    # migration is a tilt, not a thesis. Weighting migration heavily with a
    # wide clip lets any trending session saturate the reading and drown out
    # both the acceptance and fade geometry.
    raw = 0.6 * acceptance + 0.9 * fade + 0.5 * migration
    reading = squash(raw, float(cfg["squash_scale"]))

    # Confidence grows with how much of the session has actually printed.
    bars = int(value["bars"])
    confidence = float(np.tanh(bars / 24.0))
    if not value.get("volumeBacked", False):
        confidence *= 0.7
    if value.get("anchor") == "rolling":
        # A trailing-window anchor is a stand-in for a session anchor, not an
        # equal to it: the reference point is arbitrary rather than the open.
        confidence *= 0.6

    return IndicatorReading(
        "SAV",
        reading,
        confidence,
        {
            "vwap": round(float(value["vwap"]), 8),
            "dispersion": round(dispersion, 8),
            "stretchSigma": round(float(stretch), 3),
            "rotation": round(float(value["rotation"]), 4),
            "crossings": int(value["crossings"]),
            "sessionBars": bars,
            "session": value.get("sessionName"),
            "anchor": value.get("anchor", "session"),
            "timeframe": value.get("timeframe"),
            "volumeBacked": bool(value.get("volumeBacked", False)),
            "priorVwap": round(float(prior), 8) if prior else None,
            "migration": round(float(migration), 4),
            "acceptanceTerm": round(float(acceptance), 4),
            "fadeTerm": round(float(fade), 4),
            "upperBand": round(float(value["vwap"]) + outer * dispersion, 8),
            "lowerBand": round(float(value["vwap"]) - outer * dispersion, 8),
        },
    )


__all__ = ["compute", "session_value"]
