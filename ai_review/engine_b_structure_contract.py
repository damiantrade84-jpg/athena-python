"""Single source of truth for "does this payload carry Engine B structure?".

P1-5 root cause. Three consumers each had their own copy of this predicate and
they had drifted onto different Engine B contracts:

* ``routes_market_data._normalize_engine_b_overlay_payload`` (the renderer that
  draws the bands, ``overlay_version: engine_b_legacy_v1``) treats the *geometry*
  keys as evidence: ``bos_data``, ``choch_data``, ``order_blocks``,
  ``active_fvgs``, ``breaker_block``, ``nearest_*_zone``.
* ``engine_a_context._engine_b_structure_evidence`` recognised only the *scored*
  keys: ``structural_verdict``, ``bos_confirmed``, ``choch_confirmed``,
  ``ob_at_zone``, ``fvg_overlap``, ``score``, ``passed``, ``nearest_*``.
* ``routes_ai_chart_review`` used a third list again (``bos_data``/``choch_data``
  but not ``order_blocks``/``active_fvgs``).

A payload carrying only legacy geometry therefore passed the renderer and failed
the review-context predicate: bands drawn client-side, ``structure_context``
empty server-side, and no error raised anywhere. That is exactly the reported
``engine_b_overlays_enabled_but_server_structure_context_empty``.

Both contracts are accepted here so the join cannot silently drop structure.
"""

from __future__ import annotations

from typing import Any


# Scored Engine B contract — a verdict/quality was produced.
_SCORED_TRUTHY_KEYS = ("structural_verdict",)
_SCORED_BOOL_KEYS = (
    "bos_confirmed",
    "choch_confirmed",
    "liquidity_sweep",
    "ob_at_zone",
    "fvg_overlap",
)
_SCORED_PRESENT_KEYS = ("score", "passed", "nearest_support", "nearest_resistance")

# Legacy overlay geometry contract (engine_b_legacy_v1) — levels exist even when
# no verdict was scored. These are what the chart actually draws.
_GEOMETRY_KEYS = (
    "nearest_support_zone",
    "nearest_resistance_zone",
    "bos_data",
    "choch_data",
    "order_blocks",
    "active_fvgs",
    "breaker_block",
)


def has_engine_b_structure_evidence(payload: Any) -> bool:
    """True when the payload carries structure under either Engine B contract."""
    if not isinstance(payload, dict):
        return False
    if any(payload.get(key) for key in _SCORED_TRUTHY_KEYS):
        return True
    if any(payload.get(key) is True for key in _SCORED_BOOL_KEYS):
        return True
    if any(payload.get(key) is not None for key in _SCORED_PRESENT_KEYS):
        return True
    return any(payload.get(key) for key in _GEOMETRY_KEYS)


def describe_engine_b_structure_evidence(payload: Any) -> dict[str, Any]:
    """Which contract supplied the evidence — for review diagnostics."""
    if not isinstance(payload, dict):
        return {"present": False, "contract": None, "keys": []}
    scored = [key for key in _SCORED_TRUTHY_KEYS if payload.get(key)]
    scored += [key for key in _SCORED_BOOL_KEYS if payload.get(key) is True]
    scored += [key for key in _SCORED_PRESENT_KEYS if payload.get(key) is not None]
    geometry = [key for key in _GEOMETRY_KEYS if payload.get(key)]
    if scored and geometry:
        contract = "scored+geometry"
    elif scored:
        contract = "scored"
    elif geometry:
        contract = "engine_b_legacy_v1_geometry"
    else:
        contract = None
    return {
        "present": bool(scored or geometry),
        "contract": contract,
        "keys": sorted(set(scored + geometry)),
    }


def _zone_bounds(zone: Any) -> tuple[float, float] | None:
    if isinstance(zone, dict):
        low = zone.get("lower", zone.get("low"))
        high = zone.get("upper", zone.get("high"))
    elif isinstance(zone, (list, tuple)) and len(zone) >= 2:
        low, high = zone[0], zone[1]
    else:
        return None
    if low is None or high is None:
        return None
    try:
        low_f, high_f = float(low), float(high)
    except (TypeError, ValueError):
        return None
    if low_f != low_f or high_f != high_f:  # NaN
        return None
    return (low_f, high_f) if low_f <= high_f else (high_f, low_f)


def _zone_level(zone: Any) -> float | None:
    """Representative level for a zone — the edge price first meets."""
    bounds = _zone_bounds(zone)
    if bounds is None:
        return None
    return bounds[0]


def resolve_nearest_levels(structure: Any) -> dict[str, Any]:
    """Populate nearest_support / nearest_resistance from Engine B structure.

    P1-5: these were rendering ``n/a`` because only the zone dicts were carried
    and no consumer flattened them to a level.
    """
    if not isinstance(structure, dict):
        return {"nearest_support": None, "nearest_resistance": None}

    support = structure.get("nearest_support")
    resistance = structure.get("nearest_resistance")
    support_zone = structure.get("nearest_support_zone")
    resistance_zone = structure.get("nearest_resistance_zone")

    if support is None:
        support = _zone_level(support_zone)
    if resistance is None:
        # Price rises into a resistance band's lower edge first.
        resistance = _zone_level(resistance_zone)

    def _as_float(value: Any) -> float | None:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if out == out else None

    return {
        "nearest_support": _as_float(support),
        "nearest_resistance": _as_float(resistance),
        "nearest_support_zone": _zone_bounds(support_zone),
        "nearest_resistance_zone": _zone_bounds(resistance_zone),
    }


def evaluate_tp_clears_resistance(
    *,
    direction: str | None,
    entry: float | None,
    targets: dict[str, Any],
    structure: Any,
) -> dict[str, Any]:
    """P1-6: does each TP's path clear the mapped structure band?

    A TP that sits inside, or whose path crosses without clearing, the opposing
    band fails. ``None`` verdicts mean the structural input was absent — the
    caller must not narrate a structural claim in that case.
    """
    levels = resolve_nearest_levels(structure)
    d = str(direction or "").upper()
    band = (
        levels.get("nearest_resistance_zone")
        if d == "LONG"
        else levels.get("nearest_support_zone")
        if d == "SHORT"
        else None
    )

    def _f(value: Any) -> float | None:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if out == out else None

    results: dict[str, Any] = {}
    any_fail = False
    for label, raw_tp in targets.items():
        tp = _f(raw_tp)
        if tp is None or band is None or d not in {"LONG", "SHORT"}:
            results[label] = {
                "clears": None,
                "reason": "no mapped structure band"
                if band is None
                else "target unavailable",
            }
            continue
        low, high = band
        if d == "LONG":
            crosses = tp > low
            clears = tp > high
        else:
            crosses = tp < high
            clears = tp < low
        if not crosses:
            results[label] = {"clears": True, "reason": "target stops short of the band"}
            continue
        results[label] = {
            "clears": clears,
            "reason": (
                "target clears the far edge of the band"
                if clears
                else "target path runs through mapped structure without clearing it"
            ),
            "band": [low, high],
        }
        if not clears:
            any_fail = True

    verdicts = [r["clears"] for r in results.values() if r["clears"] is not None]
    return {
        "targets": results,
        "band": list(band) if band else None,
        "allClear": all(verdicts) if verdicts else None,
        "downgradeRiskGeometry": any_fail,
        "evaluated": bool(verdicts),
        "entry": _f(entry),
    }
