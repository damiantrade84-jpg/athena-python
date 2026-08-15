"""Inference routing for missing feeds (ASE v2.1 §5, §8)."""

from __future__ import annotations

from typing import Any


def route_for_feeds(
    *,
    core_missing: list[str],
    enriched_missing: list[str],
    unverified_lag: list[str] | None = None,
) -> dict[str, Any]:
    # unverified_lag is accepted for caller compatibility but does not change
    # the route: enriched-missing feeds demote enriched -> core either way.
    if core_missing:
        return {"coreOk": False, "route": "none", "missingFeeds": core_missing, "decisionStatus": "FLAT"}
    if enriched_missing:
        return {"coreOk": True, "route": "core", "missingFeeds": list(enriched_missing)}
    return {"coreOk": True, "route": "enriched", "missingFeeds": []}
