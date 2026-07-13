"""Shared Engine A V3 entry-timeframe contract."""

from __future__ import annotations

from typing import Any


VALID_V3_ENTRY_TIMEFRAMES = frozenset({"H1", "H4", "D1"})
# Diagnostic-only allowlist for entry_tf_override experiments (not production).
DIAGNOSTIC_V3_ENTRY_TIMEFRAMES = frozenset({"H1", "M15", "M30"})


def resolve_live_v3_entry_timeframe(
    asset_type: str,
    horizon: str,
    *,
    source: str | None = None,
) -> str | None:
    """Resolve a configured live lower-TF entry without changing structure TFs.

    Missing, malformed, or unsupported values return ``None`` so callers keep
    the canonical confirmed-timeframe path. A configured lower TF must be
    supplied explicitly to the evaluator and is never silently substituted.
    """
    try:
        from config import CONFIG

        by_style = (CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}).get(
            "LIVE_ENTRY_TF_BY_STYLE"
        ) or {}
        by_type = by_style.get(str(horizon or "").strip().lower()) or {}
        raw = by_type.get(str(asset_type or "").strip().lower())
    except Exception:
        raw = None
    resolved = resolve_diagnostic_v3_entry_timeframe(raw)
    if resolved and str(asset_type or "").lower() == "forex" and str(source or "").lower() != "mt5":
        return None
    return resolved


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


def resolve_diagnostic_v3_entry_timeframe(override: str | None) -> str | None:
    """Return override TF if it is in the diagnostic allowlist, else ``None``."""
    tf = str(override or "").strip().upper()
    return tf if tf in DIAGNOSTIC_V3_ENTRY_TIMEFRAMES else None


def engine_a_diagnostic_entry_kwargs(
    entry_tf: str | None,
    *,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Build evaluate/score kwargs for diagnostic entry-TF override.

    Default-off via ``ENGINE_A_DIAGNOSTIC_ENTRY_TF_ENABLED``. When enabled and
    ``entry_tf`` is H1/M15/M30, returns ``entry_tf_override`` so primary/entry
    scoring runs on that series. Experiment harnesses call with ``enabled=True``
    (or pass ``entry_tf_override`` directly) and do not require the config flag.
    """
    if enabled is None:
        try:
            from config import CONFIG

            enabled = bool(CONFIG.get("ENGINE_A_DIAGNOSTIC_ENTRY_TF_ENABLED", False))
        except Exception:
            enabled = False
    if not enabled:
        return {}
    resolved = resolve_diagnostic_v3_entry_timeframe(entry_tf)
    if resolved is None:
        return {}
    return {"entry_tf_override": resolved}
