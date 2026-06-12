"""Inference routing for missing feeds (ASE v2.1 §5, §8)."""

from __future__ import annotations

from typing import Any


def route_for_feeds(
    *,
    core_missing: list[str],
    enriched_missing: list[str],
    unverified_lag: list[str] | None = None,
) -> dict[str, Any]:
    unverified = set(unverified_lag or [])
    enriched_usable = [f for f in enriched_missing if f not in unverified]
    if core_missing:
        return {"coreOk": False, "route": "none", "missingFeeds": core_missing, "decisionStatus": "FLAT"}
    if enriched_usable:
        return {"coreOk": True, "route": "core", "missingFeeds": enriched_usable}
    if enriched_missing:
        return {"coreOk": True, "route": "core", "missingFeeds": enriched_missing}
    return {"coreOk": True, "route": "enriched", "missingFeeds": []}
