"""P2-6: full component decomposition with documented semantics.

The audit saw ``trend`` 0.278 and ``location`` 0.056 against a raw score of
1.222 — two components shown, 73% unattributed, and no statement of what
``contribution`` means. Under a multiplicative score an additive-looking share
is ambiguous, so the semantics are emitted alongside the numbers rather than
left to the reader.
"""

from __future__ import annotations

from typing import Any


# The seven Engine A quant components. Emitted even when absent so a missing
# component is visible as a gap rather than silently dropped from the list.
COMPONENT_NAMES: tuple[str, ...] = (
    "trend",
    "momentum",
    "location",
    "volume",
    "volatility",
    "structure",
    "confluence",
)

CONTRIBUTION_SEMANTICS: dict[str, str] = {
    "formula": "weight x quality x volatility_multiplier",
    "zeroedWhen": (
        "the component is directional and its signal opposes the resolved trade "
        "side, or the resolved direction is FLAT (inside the deadband), which "
        "zeroes every contribution at once"
    ),
    "combination": (
        "Contributions are additive shares of the directional sum (dir_sum). "
        "They are NOT an exact decomposition of the final Engine A score: the "
        "v12 formula applies multiplicative gates on top of dir_sum, so "
        "contributions explain relative ranking between components, not the "
        "arithmetic that produced finalEngineAScore."
    ),
    "units": "score points before multiplicative gating",
    "readingWarning": (
        "attributedTotal will not equal rawScore. A large unattributed residual "
        "is expected under the multiplicative formula and is reported explicitly "
        "rather than hidden."
    ),
}


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def build_component_decomposition(
    factor_diagnostics: Any,
    *,
    raw_score: Any = None,
    component_names: tuple[str, ...] = COMPONENT_NAMES,
) -> dict[str, Any]:
    """Emit every component, the attributed total, and the residual."""
    factors = factor_diagnostics if isinstance(factor_diagnostics, dict) else {}
    components = factors.get("components")
    if not isinstance(components, dict):
        components = {}

    rows: dict[str, Any] = {}
    attributed = 0.0
    any_present = False
    for name in component_names:
        entry = components.get(name)
        if not isinstance(entry, dict):
            rows[name] = {
                "present": False,
                "signal": None,
                "quality": None,
                "weight": None,
                "contribution": None,
                "available": None,
                "reason": "component missing from factorDiagnostics payload",
            }
            continue
        any_present = True
        contribution = _to_float(entry.get("contribution"))
        if contribution is not None:
            attributed += contribution
        rows[name] = {
            "present": True,
            "signal": _to_float(entry.get("signal")),
            "quality": _to_float(entry.get("quality")),
            "weight": _to_float(entry.get("weight")),
            "contribution": contribution,
            "available": entry.get("available"),
        }

    # Components the scorer emitted that are not in the documented seven.
    extra = sorted(set(components) - set(component_names))
    for name in extra:
        entry = components.get(name)
        if not isinstance(entry, dict):
            continue
        any_present = True
        contribution = _to_float(entry.get("contribution"))
        if contribution is not None:
            attributed += contribution
        rows[name] = {
            "present": True,
            "signal": _to_float(entry.get("signal")),
            "quality": _to_float(entry.get("quality")),
            "weight": _to_float(entry.get("weight")),
            "contribution": contribution,
            "available": entry.get("available"),
            "unexpectedComponent": True,
        }

    raw = _to_float(raw_score)
    attributed_total = round(attributed, 6) if any_present else None
    unattributed = None
    unattributed_pct = None
    if raw is not None and attributed_total is not None:
        unattributed = round(raw - attributed_total, 6)
        if raw != 0:
            unattributed_pct = round(100.0 * unattributed / raw, 2)

    missing = [name for name, row in rows.items() if not row["present"]]
    return {
        "components": rows,
        "componentCount": len(rows),
        "missingComponents": missing,
        "complete": not missing,
        "attributedTotal": attributed_total,
        "rawScore": raw,
        "unattributed": unattributed,
        "unattributedPct": unattributed_pct,
        "contributionSemantics": CONTRIBUTION_SEMANTICS,
    }
