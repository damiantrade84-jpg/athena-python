"""Execution route helper functions."""

from __future__ import annotations


def normalize_pip_mode(raw_mode: str | None) -> str:
    mode = (raw_mode or "swing")
    if isinstance(mode, str):
        mode = mode.strip().lower()
    if mode not in ("scalp", "intraday", "swing"):
        mode = "swing"
    return mode


def normalize_volume_mode(raw_mode: str | None) -> str | None:
    if raw_mode is None:
        return None
    mode = str(raw_mode).strip().lower()
    if mode in ("min_lot", "min", "minimum"):
        return "min_lot"
    if mode in ("calculated", "increase", "risk"):
        return "calculated"
    return None


def parse_execution_volume_args(payload: dict | None) -> tuple[str | None, str, float]:
    """Return (volume_mode, execution_context, sizing_override) from request payload."""
    payload = payload or {}
    volume_mode = normalize_volume_mode(
        payload.get("volume_mode") or payload.get("volumeMode")
    )
    execution_context = str(
        payload.get("execution_context") or payload.get("executionContext") or "manual"
    ).strip().lower()
    if execution_context not in ("auto", "manual"):
        execution_context = "manual"
    try:
        sizing_override = max(0.25, min(1.0, float(payload.get("sizing_override", 1.0))))
    except (TypeError, ValueError):
        sizing_override = 1.0
    return volume_mode, execution_context, sizing_override


def merge_scalp_sizing_override(
    volume_mode: str | None,
    payload: dict | None,
    signal: dict | None,
) -> float:
    """Scalp calculated mode may combine grade multiplier with user override."""
    _, _, override = parse_execution_volume_args(payload)
    mode = volume_mode or normalize_volume_mode(
        (payload or {}).get("volume_mode") or (payload or {}).get("volumeMode")
    )
    if mode != "calculated":
        return 1.0
    grade_mult = float((signal or {}).get("size_multiplier", 1.0) or 1.0)
    return max(0.25, min(1.0, grade_mult * override))

