"""Shared Engine A V3 entry-timeframe contract."""

from __future__ import annotations


VALID_V3_ENTRY_TIMEFRAMES = frozenset({"H1", "H4", "D1"})


def resolve_v3_entry_timeframe(
    score_group: str,
    asset_type: str,
    horizon: str,
) -> str | None:
    """Return the configured decision timeframe, or ``None`` if it is invalid."""
    del asset_type
    fallback = "H1" if str(horizon).lower() == "intraday" else "H4"
    try:
        from config import CONFIG

        by_group = (
            (CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}).get("BY_SCORE_GROUP")
            or {}
        )
        raw_override = (by_group.get(score_group) or {}).get("execution_tf")
    except Exception:
        raw_override = None
    resolved = str(raw_override or fallback).strip().upper()
    return resolved if resolved in VALID_V3_ENTRY_TIMEFRAMES else None
