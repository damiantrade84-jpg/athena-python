"""Research-only SHADOW scorer for Engine A factor ablation (2026-06-19).

This module DOES NOT touch the live Engine A scoring path. It exists solely to
measure what each subsystem factor contributes, by wiring the subsystem context
into direction/confluence the way the production scorer fails to (see
`project_engine_a_context_defect_2026_06_19`).

Confirmed production defect being studied:
  * `engine_a_v3/quant_scorer.py` builds `components` from only the 4 price
    factors (trend/momentum/location/volume). `_context_component()` is never
    called, and `engine_a_v3/profile.py` forbids subsystem weights. So
    intermarket/carry/sentiment/macro/microstructure never reach direction or
    confluence on ANY path.

Parity guarantee: the price core (trend/momentum/location/volume + volatility)
is imported VERBATIM from the production scorer, so any score difference vs
production is attributable only to (a) the subsystem wiring and (b) the
denominator policy — never to a re-implemented technical.

Denominator policy (user-specified 2026-06-19, "HYBRID"):
  1. Price-core factors renormalize across AVAILABLE price factors (an
     unavailable price factor is dropped from the denominator).
  2. Subsystem factors that are STRUCTURALLY APPLICABLE to the family stay in
     the denominator whether or not data is available; when unavailable/stale
     they contribute 0 to the numerator and thereby REDUCE achievable
     confluence. They can never inflate the technicals.
  3. A structurally-inapplicable factor has weight 0 for that family (state
     "na") and is excluded entirely — no penalty.
  4. Four availability states are tracked per factor:
        available  — applicable, data present, materially non-neutral
        neutral    — applicable, data present, ~0 directional content
        unavailable— applicable, but missing/stale/malformed/failed
        na         — structurally not applicable (weight 0)
  5. Diagnostics expose configured weight, effective weight, state,
     contribution and total coverage.

Thresholds and weights are NOT tuned here; the research weights are the
documented design-intent priors (the dead `_DEFAULT_WEIGHTS` table in the
production scorer) restricted to the subset of subsystems we can source with
leak-free point-in-time data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Price core imported VERBATIM from production for parity (no re-implementation).
from engine_a_v3.quant_scorer import (
    MAX_SCORE,
    Component,
    _clamp,
    _location_component,
    _momentum_component,
    _resolve_v3_momentum_tf,
    _resolve_v3_tf_weights,
    _snapshots,
    _FAMILY_ASSET,
    _trend_component,
    _volatility_mult,
    _volume_component,
)

PRICE_FACTORS = ("trend", "momentum", "location", "volume")
SUBSYSTEM_FACTORS = ("intermarket", "carry", "sentiment", "macro", "microstructure")
ALL_FACTORS = PRICE_FACTORS + SUBSYSTEM_FACTORS

# Availability states (user policy item 4).
ST_AVAILABLE = "available"
ST_NEUTRAL = "neutral"
ST_UNAVAILABLE = "unavailable"
ST_NA = "na"

# Design-intent priors (absolute; NOT renormalized — the denominator policy does
# the normalizing). Source: the production scorer's `_DEFAULT_WEIGHTS` docstring
# table, restricted to factors with leak-free point-in-time data this pass.
# `intermarket`/`microstructure`/separate-`macro` have no point-in-time builder
# yet, so they are listed weight 0 here and reported as insufficient-coverage.
_RESEARCH_WEIGHTS: dict[str, dict[str, float]] = {
    "forex": {
        "trend": 0.24, "momentum": 0.16, "location": 0.12, "volume": 0.05,
        "intermarket": 0.0, "carry": 0.14, "sentiment": 0.08, "macro": 0.0,
        "microstructure": 0.0,
    },
    "crypto": {
        "trend": 0.24, "momentum": 0.18, "location": 0.12, "volume": 0.12,
        "intermarket": 0.0, "carry": 0.0, "sentiment": 0.09, "macro": 0.0,
        "microstructure": 0.0,
    },
    "commodity": {
        "trend": 0.26, "momentum": 0.15, "location": 0.12, "volume": 0.07,
        "intermarket": 0.0, "carry": 0.16, "sentiment": 0.09, "macro": 0.0,
        "microstructure": 0.0,
    },
    "index": {
        "trend": 0.24, "momentum": 0.16, "location": 0.12, "volume": 0.12,
        "intermarket": 0.0, "carry": 0.12, "sentiment": 0.08, "macro": 0.0,
        "microstructure": 0.0,
    },
    "equity_etf": {
        "trend": 0.24, "momentum": 0.15, "location": 0.12, "volume": 0.15,
        "intermarket": 0.0, "carry": 0.12, "sentiment": 0.09, "macro": 0.0,
        "microstructure": 0.0,
    },
    "unknown": {
        "trend": 0.30, "momentum": 0.20, "location": 0.15, "volume": 0.10,
        "intermarket": 0.0, "carry": 0.0, "sentiment": 0.0, "macro": 0.0,
        "microstructure": 0.0,
    },
}


@dataclass
class ShadowScore:
    direction: str
    confluence_score: float
    max_score: float
    score_norm: float
    threshold: float
    decision: str
    level_style: str
    dir_sum: float
    coverage: float                      # available weight / applicable weight
    factor_table: dict[str, dict]        # per-factor full diagnostic row
    components: dict[str, Component] = field(default_factory=dict)


def _subsystem_component(entry: Any) -> tuple[Component, str]:
    """Map a context entry to (Component, state). The context builder is the
    single source of truth for the 4-state classification; this just reads it.

    Accepted forms for context[key]:
      {"signal":..,"quality":..,"state":"available"|"neutral"}  -> usable
      {"state":"unavailable"} / {"state":"na"}                   -> no contribution
      None / missing                                            -> unavailable
    """
    if not isinstance(entry, Mapping):
        return Component(0.0, 0.0, available=False), ST_UNAVAILABLE
    state = str(entry.get("state") or "").lower()
    if state == ST_NA:
        return Component(0.0, 0.0, available=False), ST_NA
    if state == ST_UNAVAILABLE:
        return Component(0.0, 0.0, available=False), ST_UNAVAILABLE
    signal = _clamp(float(entry.get("signal") or 0.0), -1.0, 1.0)
    quality = _clamp(float(entry.get("quality") or 0.0), 0.0, 1.0)
    if not state:
        state = ST_NEUTRAL if (abs(signal) < 1e-9 or quality < 1e-9) else ST_AVAILABLE
    return Component(signal, quality, available=True), state


@dataclass
class BarComponents:
    """Per-bar, variant-independent inputs. The expensive indicator work
    (`_snapshots`) happens once here; ablation variants then call `aggregate`
    cheaply over this object."""
    family: str
    comps: dict[str, Component]
    raw_states: dict[str, str]
    level_style: str
    vol_mult: float
    deadband: float
    profile: Any


def compute_components(
    route: Any,
    horizon: str,
    candles: dict[str, list[dict]],
    *,
    context: Mapping[str, Any] | None = None,
    profile: Any | None = None,
) -> BarComponents:
    """Compute the price core (verbatim production components) + subsystem
    states once for a bar. Variant-independent."""
    family = getattr(route, "family", "unknown")
    asset_type = _FAMILY_ASSET.get(family, "other")
    horizon = "intraday" if str(horizon).lower() == "intraday" else "swing"
    entry_tf = "H1" if horizon == "intraday" else "H4"

    if profile is None:
        from engine_a_v3.profile import baseline_profile
        profile = baseline_profile(getattr(route, "score_group", "unknown"), horizon)

    snaps = _snapshots(candles, asset_type, dict(profile.indicator_periods))
    entry_snap = snaps.get(entry_tf) or snaps.get("H4") or snaps.get("D1") or {}
    _shadow_group = getattr(route, "score_group", "unknown")
    momentum_tf = _resolve_v3_momentum_tf(_shadow_group, asset_type, horizon)
    momentum_snap = snaps.get(momentum_tf) or snaps.get("H4") or entry_snap

    trend, _ = _trend_component(
        snaps,
        _resolve_v3_tf_weights(_shadow_group, asset_type, horizon),
    )
    momentum, _ = _momentum_component(
        momentum_snap, asset_type, getattr(route, "score_group", "unknown")
    )
    location, level_style = _location_component(entry_snap)
    volume = _volume_component(entry_snap, candles.get(entry_tf) or [], context)

    comps: dict[str, Component] = {
        "trend": trend, "momentum": momentum, "location": location, "volume": volume,
    }
    raw_states: dict[str, str] = {}
    # Price-factor availability mirrors production's `active` rule for volume.
    for name in PRICE_FACTORS:
        c = comps[name]
        if name == "volume":
            avail = c.available and (c.quality > 0.0 or c.signal != 0.0)
        else:
            avail = c.available
        raw_states[name] = ST_AVAILABLE if avail else ST_UNAVAILABLE

    # Subsystems from context (single source of truth for state).
    for name in SUBSYSTEM_FACTORS:
        comp, state = _subsystem_component((context or {}).get(name))
        comps[name] = comp
        raw_states[name] = state

    return BarComponents(
        family=family, comps=comps, raw_states=raw_states, level_style=level_style,
        vol_mult=_volatility_mult(entry_snap),
        deadband=float(getattr(profile, "direction_deadband", 0.05)), profile=profile,
    )


def aggregate(
    bar: BarComponents,
    *,
    include: set[str] | None = None,
    weights: Mapping[str, float] | None = None,
    threshold: float | None = None,
) -> ShadowScore:
    """Aggregate one variant from precomputed components under the hybrid
    denominator policy.

    `include` — ablation mask: only these factor names may contribute to the
    numerator (others held out as if "unavailable", but applicable subsystems
    still keep their denominator weight so the comparison is honest)."""
    include = set(ALL_FACTORS) if include is None else set(include)
    wmap = dict(weights or _RESEARCH_WEIGHTS.get(bar.family, _RESEARCH_WEIGHTS["unknown"]))
    if threshold is None:
        threshold = float(getattr(bar.profile, "trade_threshold", 2.0))
    comps = bar.comps
    states = dict(bar.raw_states)  # local copy; weight-driven NA applied here only
    deadband = bar.deadband
    level_style = bar.level_style

    # ── Hybrid denominator ───────────────────────────────────────────────────
    # Price core: only AVAILABLE price factors are in the denominator.
    # Subsystems: every STRUCTURALLY-APPLICABLE subsystem (state != na and
    # configured weight > 0) stays in the denominator, available or not.
    denom = 0.0
    applicable_weight = 0.0
    available_weight = 0.0
    for name in PRICE_FACTORS:
        w = max(0.0, wmap.get(name, 0.0))
        if w <= 0:
            states[name] = ST_NA
            continue
        applicable_weight += w
        if states[name] == ST_AVAILABLE and name in include:
            denom += w
            available_weight += w
        # unavailable price factor: dropped from denom (renormalize among available)
    for name in SUBSYSTEM_FACTORS:
        w = max(0.0, wmap.get(name, 0.0))
        if w <= 0 or states[name] == ST_NA:
            states[name] = ST_NA
            continue
        # structurally applicable -> always in denominator
        denom += w
        applicable_weight += w
        usable = states[name] in (ST_AVAILABLE, ST_NEUTRAL) and name in include
        if usable:
            available_weight += w
    if denom <= 0:
        denom = 1.0

    def _contributes(name: str) -> bool:
        if name not in include:
            return False
        if name in PRICE_FACTORS:
            return states[name] == ST_AVAILABLE
        return states[name] in (ST_AVAILABLE, ST_NEUTRAL)

    # Direction = sign of weighted (signal*quality) over contributing factors.
    dir_sum = sum(
        max(0.0, wmap.get(n, 0.0)) * comps[n].signal * comps[n].quality
        for n in ALL_FACTORS if _contributes(n)
    ) / denom
    if dir_sum > deadband:
        direction, dsign = "LONG", 1.0
    elif dir_sum < -deadband:
        direction, dsign = "SHORT", -1.0
    else:
        direction, dsign = "FLAT", 0.0

    # Confluence = weighted aligned quality over the SAME hybrid denominator.
    conf_num = 0.0
    factor_table: dict[str, dict] = {}
    for name in ALL_FACTORS:
        c = comps[name]
        w = max(0.0, wmap.get(name, 0.0))
        aligned = bool(dsign) and (c.signal * dsign) > 0.0 and _contributes(name)
        contribution = w * c.quality if aligned else 0.0
        if aligned:
            conf_num += contribution
        eff_w = round(w / denom, 4) if denom > 0 else 0.0
        factor_table[name] = {
            "signal": round(c.signal, 4),
            "quality": round(c.quality, 4),
            "state": states[name],
            "configured_weight": round(w, 4),
            "effective_weight": eff_w,
            "in_denominator": (
                (name in PRICE_FACTORS and states[name] == ST_AVAILABLE and name in include and w > 0)
                or (name in SUBSYSTEM_FACTORS and states[name] != ST_NA and w > 0)
            ),
            "aligned": aligned,
            "contribution": round(contribution / denom, 4),
        }

    confluence = _clamp(MAX_SCORE * (conf_num / denom) * bar.vol_mult, 0.0, MAX_SCORE)
    score_norm = confluence / MAX_SCORE
    decision = "TRADE" if direction in ("LONG", "SHORT") and confluence >= threshold else "WATCH"
    coverage = round(available_weight / applicable_weight, 4) if applicable_weight > 0 else 0.0

    return ShadowScore(
        direction=direction,
        confluence_score=round(confluence, 4),
        max_score=MAX_SCORE,
        score_norm=round(score_norm, 4),
        threshold=float(threshold),
        decision=decision,
        level_style=level_style,
        dir_sum=round(dir_sum, 6),
        coverage=coverage,
        factor_table=factor_table,
        components=comps,
    )


def shadow_score_pair(
    route: Any,
    horizon: str,
    candles: dict[str, list[dict]],
    *,
    context: Mapping[str, Any] | None = None,
    profile: Any | None = None,
    include: set[str] | None = None,
    weights: Mapping[str, float] | None = None,
    threshold: float | None = None,
) -> ShadowScore:
    """Convenience: compute components + aggregate one variant in a single call
    (used by tests). The harness calls compute_components / aggregate directly
    so it pays the indicator cost once per bar across all variants."""
    bar = compute_components(route, horizon, candles, context=context, profile=profile)
    return aggregate(bar, include=include, weights=weights, threshold=threshold)
