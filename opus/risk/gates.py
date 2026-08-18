"""Deterministic gates.

Two rules govern everything here.

  1. Missing or ambiguous data FAILS. A gate that cannot evaluate its input
     returns False, never True. "We could not measure the spread" and "the
     spread is fine" are different states and must not collapse into one.

  2. Decision and readiness are separate. A signal can be a genuinely good
     idea (decision=TRADE) and still be un-executable right now (readiness
     BLOCKED or ARMED). Collapsing them is how a system ends up placing orders
     on stale quotes: the score was good, so it traded.
"""

from __future__ import annotations

import numpy as np

import opus.config as config
from opus.indicators.base import TF_SECONDS, MarketContext
from opus.scoring.expectancy import Expectancy
from opus.types import Decision, GateResult, Levels, Quote, Readiness


def _gates_cfg() -> dict:
    return config.load()["GATES"]


def data_gates(ctx: MarketContext, timeframes: dict) -> list[GateResult]:
    """Freshness and sufficiency of the market data behind the signal."""
    cfg = _gates_cfg()
    bars = config.load()["BARS"]
    out: list[GateResult] = []

    for role, minimum in (
        ("context", int(bars["context_min"])),
        ("structure", int(bars["structure_min"])),
        ("trigger", int(bars["trigger_min"])),
    ):
        candles = getattr(ctx, role)
        count = len(candles)
        out.append(
            GateResult(
                name=f"bars_{role}", passed=count >= minimum, hard=True,
                detail=f"{role} rung has {count} bars",
                observed=count, limit=minimum,
            )
        )

    max_mult = float(cfg["max_bar_age_mult"])
    for role in ("structure", "trigger"):
        candles = getattr(ctx, role)
        tf = str(timeframes.get(role, candles.timeframe)).upper()
        span = TF_SECONDS.get(tf, 0)
        if span <= 0 or not len(candles):
            out.append(
                GateResult(
                    name=f"bar_age_{role}", passed=False, hard=True,
                    detail=f"cannot evaluate {role} bar age",
                    observed=None, limit=None,
                )
            )
            continue
        age = ctx.bar_age_sec(tf, candles)
        limit = span * max_mult
        out.append(
            GateResult(
                name=f"bar_age_{role}", passed=age <= limit, hard=True,
                detail=f"{role} last bar closed {age:.0f}s ago ({tf})",
                observed=round(age, 1), limit=round(limit, 1),
            )
        )

    out.append(
        GateResult(
            name="sigma_valid", passed=bool(ctx.has_sigma), hard=True,
            detail="Yang-Zhang sigma is positive and finite",
            observed=round(float(ctx.sigma), 8) if np.isfinite(ctx.sigma) else None,
            limit="> 0",
        )
    )
    return out


def quote_gates(quote: Quote | None, now: float) -> list[GateResult]:
    """Liveness and cost sanity of the executable quote."""
    cfg = _gates_cfg()
    require = bool(cfg["require_live_quote"])
    out: list[GateResult] = []

    if quote is None:
        out.append(
            GateResult(
                name="live_quote", passed=not require, hard=True,
                detail="no live quote available", observed=None, limit="required",
            )
        )
        # Without a quote, spread and age are unknowable - they must not pass.
        out.append(
            GateResult(
                name="quote_age", passed=False, hard=True,
                detail="no quote to age", observed=None, limit=cfg["max_quote_age_sec"],
            )
        )
        out.append(
            GateResult(
                name="spread", passed=False, hard=True,
                detail="no quote to measure spread",
                observed=None, limit=cfg["max_spread_pct"],
            )
        )
        return out

    out.append(
        GateResult(
            name="live_quote", passed=True, hard=True,
            detail=f"quote from {quote.source}", observed=quote.source, limit="required",
        )
    )

    # Signed skew, NOT the clamped age. `Quote.age_sec` floors at zero, so a
    # future-dated timestamp reads as "0 seconds old" and sails through the
    # freshness gate - a fail-OPEN. Future-dating is exactly what happens when
    # a broker reports server-local time and it is read as UTC (an MT5 server
    # on UTC+3 dates every tick three hours ahead), so an arbitrarily stale
    # quote would be accepted as fresh. Treat clock skew in either direction as
    # a failure.
    skew = float(now) - float(quote.ts)
    max_age = float(cfg["max_quote_age_sec"])
    future_tolerance = float(cfg.get("max_quote_future_sec", 2.0))
    fresh = (-future_tolerance) <= skew <= max_age
    if skew < -future_tolerance:
        detail = (
            f"quote is dated {abs(skew):.0f}s in the FUTURE - broker clock or "
            f"timezone mismatch"
        )
    else:
        detail = f"quote is {skew:.1f}s old"
    out.append(
        GateResult(
            name="quote_age", passed=bool(fresh), hard=True,
            detail=detail, observed=round(skew, 2), limit=max_age,
        )
    )

    spread_pct = quote.spread_pct
    max_spread = float(cfg["max_spread_pct"])
    valid = np.isfinite(spread_pct) and quote.bid > 0 and quote.ask >= quote.bid
    out.append(
        GateResult(
            name="spread", passed=bool(valid and spread_pct <= max_spread), hard=True,
            detail=f"spread {spread_pct:.4f}% of mid" if valid else "invalid quote",
            observed=round(float(spread_pct), 5) if valid else None,
            limit=max_spread,
        )
    )
    return out


def edge_gates(
    *,
    conviction: float,
    coherence: float,
    expectancy: Expectancy,
    validated: bool | None = None,
) -> list[GateResult]:
    """The gates that decide whether this is worth paying the spread for."""
    cfg = _gates_cfg()
    out = [
        GateResult(
            name="conviction", passed=conviction >= float(cfg["min_conviction"]),
            hard=True, detail="weighted, coherence-adjusted evidence",
            observed=round(float(conviction), 4), limit=cfg["min_conviction"],
        ),
        GateResult(
            name="coherence", passed=coherence >= float(cfg["min_coherence"]),
            hard=True, detail="agreement across indicators",
            observed=round(float(coherence), 4), limit=cfg["min_coherence"],
        ),
        GateResult(
            name="probability", passed=expectancy.probability >= float(cfg["min_probability"]),
            hard=True, detail="calibrated P(target before stop)",
            observed=round(float(expectancy.probability), 4), limit=cfg["min_probability"],
        ),
        GateResult(
            name="cost_r", passed=expectancy.cost_r <= float(cfg["max_cost_r"]),
            hard=True, detail="round-trip cost as a fraction of risk",
            observed=round(float(expectancy.cost_r), 5), limit=cfg["max_cost_r"],
        ),
        GateResult(
            name="expectancy",
            passed=bool(
                np.isfinite(expectancy.expectancy_r)
                and expectancy.expectancy_r >= float(cfg["min_expectancy_r"])
            ),
            hard=True, detail="net expected R after costs - the decisive gate",
            observed=round(float(expectancy.expectancy_r), 5)
            if np.isfinite(expectancy.expectancy_r) else None,
            limit=cfg["min_expectancy_r"],
        ),
        GateResult(
            name="rr", passed=expectancy.rr >= float(config.load()["GEOMETRY"]["min_rr"]),
            hard=True, detail="reward multiple to first target",
            observed=round(float(expectancy.rr), 3),
            limit=config.load()["GEOMETRY"]["min_rr"],
        ),
    ]

    from opus.scoring.validation import enforced
    if enforced():
        out.append(
            GateResult(
                name="archetype_validated", passed=bool(validated), hard=True,
                detail="archetype has cleared the deflated-Sharpe evidence gate",
                observed=bool(validated), limit=True,
            )
        )
    else:
        out.append(
            GateResult(
                name="archetype_validated", passed=True, hard=False,
                detail="evidence gate reported but not enforced",
                observed=bool(validated) if validated is not None else None,
                limit="advisory",
            )
        )
    return out


def levels_gates(levels: Levels, direction) -> list[GateResult]:
    """Structural sanity of the order itself."""
    out: list[GateResult] = []
    risk = levels.risk_per_unit
    out.append(
        GateResult(
            name="risk_positive", passed=risk > 0, hard=True,
            detail="stop distance is non-zero", observed=round(float(risk), 8), limit="> 0",
        )
    )

    sign = direction.sign
    stop_side_ok = (
        (levels.stop < levels.entry) if sign > 0 else (levels.stop > levels.entry)
    )
    out.append(
        GateResult(
            name="stop_side", passed=bool(stop_side_ok), hard=True,
            detail="stop is on the losing side of entry",
            observed=round(float(levels.stop), 8), limit=round(float(levels.entry), 8),
        )
    )

    targets_ok = bool(levels.targets) and all(
        (t > levels.entry) if sign > 0 else (t < levels.entry) for t in levels.targets
    )
    out.append(
        GateResult(
            name="target_side", passed=targets_ok, hard=True,
            detail="every target is on the winning side of entry",
            observed=[round(float(t), 8) for t in levels.targets], limit="beyond entry",
        )
    )

    finite = all(
        np.isfinite(v) for v in [levels.entry, levels.stop, *levels.targets]
    )
    out.append(
        GateResult(
            name="levels_finite", passed=bool(finite), hard=True,
            detail="no NaN or infinite level", observed=finite, limit=True,
        )
    )
    return out


def decide(
    conviction: float, gates: list[GateResult]
) -> tuple[Decision, Readiness]:
    """Map gate state onto a decision and an independent readiness."""
    cfg = _gates_cfg()
    blocking = [g for g in gates if g.hard and not g.passed]

    # Gates that describe whether the IDEA is good, versus whether it can be
    # ACTED ON right now. A stale quote does not make the idea wrong.
    execution_gate_names = {"live_quote", "quote_age", "spread"}
    edge_blockers = [g for g in blocking if g.name not in execution_gate_names]
    execution_blockers = [g for g in blocking if g.name in execution_gate_names]

    if edge_blockers:
        decision = (
            Decision.WATCH
            if conviction >= float(cfg["watch_conviction"])
            else Decision.STAND_DOWN
        )
    else:
        decision = Decision.TRADE

    if blocking:
        readiness = Readiness.BLOCKED
    elif decision is Decision.TRADE:
        readiness = Readiness.READY
    else:
        readiness = Readiness.ARMED

    # A blocked-only-on-execution signal keeps its decision but can never be
    # READY; that combination is exactly what the two fields exist to express.
    if execution_blockers and not edge_blockers:
        readiness = Readiness.BLOCKED

    return decision, readiness


__all__ = [
    "data_gates", "quote_gates", "edge_gates", "levels_gates", "decide",
]
