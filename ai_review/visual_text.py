"""Shared visual-confirmation / contradiction text helpers for AI chart review.

Used by postprocessors (summary, concordance) so non-directional caveats do not
get scored as hard directional chart-vs-engine contradictions.
"""

from __future__ import annotations

from typing import Any

_EMPTY_VISUAL = frozenset({"", "none", "no", "false", "n/a", "na", "null", "undefined"})

# Directional conflict markers only — chop/stale/RR caveats are not direction flips.
_DIRECTIONAL_MARKERS = (
    "direction",
    "bias",
    "opposite",
    "against engine",
    "opposes",
    "bullish vs short",
    "bearish vs long",
    "short vs long",
    "long vs short",
    "chart says short",
    "chart says long",
    "wrong side",
    "counter-trend to engine",
    "counter trend to engine",
)


def is_empty_visual_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in _EMPTY_VISUAL


def is_directional_visual_contradiction(value: Any) -> bool:
    """True only when the model claims chart direction conflicts with the engine."""
    if is_empty_visual_text(value):
        return False
    text = str(value).strip().lower()
    return any(marker in text for marker in _DIRECTIONAL_MARKERS)


def has_visual_contradiction_text(value: Any) -> bool:
    """Any non-empty visualContradiction string the model filled."""
    return not is_empty_visual_text(value)
