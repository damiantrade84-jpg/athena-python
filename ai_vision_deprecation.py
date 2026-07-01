"""ai_vision_deprecation.py — legacy /api/chart-analysis deprecation stamp.

Phase 0 of the Athena AI Edge Program: unifying the legacy vision path into
/api/ai/chart-review. This module provides a config-gated, non-breaking
deprecation stamp that attaches migration metadata to legacy responses.

Safety:
- Config-gated by AI_VISION_LEGACY_ROUTE_DEPRECATED (default False).
- When disabled, payloads pass through unchanged.
- When enabled, only ADDITIVE fields are attached; the legacy response shape
  is preserved so existing clients keep working.
- Never raises.
- Advisory-only; does not touch execution, gates, or scoring.
"""

from __future__ import annotations

from typing import Any

try:
    from config import CONFIG
except Exception:  # pragma: no cover - config import should never fail in prod
    CONFIG = {}


def stamp_vision_legacy_deprecation(payload: dict[str, Any]) -> dict[str, Any]:
    """Stamp a legacy /api/chart-analysis response with deprecation metadata.

    Returns the same payload object (mutated in place when enabled). When the
    feature is disabled or the payload is not a dict, the payload is returned
    unchanged. Never raises.
    """
    try:
        if not CONFIG.get("AI_VISION_LEGACY_ROUTE_DEPRECATED", False):
            return payload
        if not isinstance(payload, dict):
            return payload
        payload.setdefault("deprecated", True)
        payload.setdefault("useInstead", "/api/ai/chart-review")
        payload.setdefault(
            "migrationNote",
            "Legacy /api/chart-analysis is being unified into /api/ai/chart-review. "
            "Migrate clients to the new endpoint; this legacy route will be removed "
            "in a future release. Response shape is unchanged in the meantime.",
        )
        return payload
    except Exception:
        return payload
