"""Engine B direction resolution and alignment helpers."""

from __future__ import annotations

from typing import Any, Callable

NO_CLEAR_DIRECTION_ERROR = (
    "Engine B: no clear structural direction (explicit direction required)"
)
INVALID_EXECUTION_DIRECTION_ERROR = "Invalid Engine B execution direction"


def normalize_caller_direction(
    raw_direction: Any,
    *,
    execution_mode: bool = False,
) -> tuple[str | None, str | None]:
    """Parse caller-supplied direction. Returns (direction, error)."""
    direction = str(raw_direction or "").upper()
    if direction in ("LONG", "SHORT"):
        return direction, None
    if execution_mode:
        return None, INVALID_EXECUTION_DIRECTION_ERROR
    return None, None


def resolve_analysis_direction(
    *,
    direction: str | None,
    probe_fn: Callable[..., tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]],
    probe_kwargs: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, str, str | None]:
    """Resolve direction for the analysis path before analyze_structure.

    Returns (direction, probe_res, probe_conf, direction_source, error).
    """
    if direction in ("LONG", "SHORT"):
        return direction, None, None, "caller", None

    dir_probe, res_probe, conf_probe = probe_fn(**probe_kwargs)
    if dir_probe in ("LONG", "SHORT"):
        return dir_probe, res_probe, conf_probe, "independent_probe", None
    return None, None, None, "", NO_CLEAR_DIRECTION_ERROR


def _independent_direction_value(independent_direction: Any) -> str | None:
    if isinstance(independent_direction, dict):
        raw = independent_direction.get("direction")
    else:
        raw = independent_direction
    direction = str(raw or "").upper()
    return direction if direction in ("LONG", "SHORT") else None


def engine_b_direction_alignment(
    chosen_direction: str,
    independent_direction: Any,
) -> str:
    """Return ``aligned``, ``opposed``, or ``none``."""
    chosen = str(chosen_direction or "").upper()
    independent = _independent_direction_value(independent_direction)
    if independent is None or chosen not in ("LONG", "SHORT"):
        return "none"
    if independent == chosen:
        return "aligned"
    return "opposed"


def independent_conflict_blocks_emit(
    chosen_direction: str,
    structure_result: dict[str, Any] | None,
    *,
    enabled: bool | None = None,
    min_confidence: str | None = None,
) -> bool:
    """True when a strong independent opinion opposes the chosen emit direction."""
    try:
        from config import CONFIG
    except Exception:
        CONFIG = {}

    if enabled is None:
        enabled = bool(
            CONFIG.get("ENGINE_B_DIRECTION_INDEPENDENT_CONFLICT_GUARD_ENABLED", False)
        )
    if not enabled or not isinstance(structure_result, dict):
        return False

    independent = structure_result.get("engine_b_independent_direction") or {}
    if not isinstance(independent, dict):
        return False

    if min_confidence is None:
        min_confidence = str(
            CONFIG.get(
                "ENGINE_B_DIRECTION_INDEPENDENT_CONFLICT_MIN_CONFIDENCE", "HIGH"
            )
            or "HIGH"
        ).upper()

    confidence = str(independent.get("confidence") or "").upper()
    if confidence != min_confidence:
        return False

    independent_dir = _independent_direction_value(independent)
    chosen = str(chosen_direction or "").upper()
    return (
        independent_dir in ("LONG", "SHORT")
        and chosen in ("LONG", "SHORT")
        and independent_dir != chosen
    )


def annotate_signal_direction_metadata(
    signal: dict[str, Any],
    structure_result: dict[str, Any] | None,
    chosen_direction: str,
) -> None:
    """Attach independent-direction metadata to an emitted signal."""
    if not isinstance(signal, dict):
        return
    structure = structure_result if isinstance(structure_result, dict) else {}
    independent = structure.get("engine_b_independent_direction")
    if independent is not None:
        signal["engine_b_independent_direction"] = independent
    signal["engine_b_direction_alignment"] = engine_b_direction_alignment(
        chosen_direction,
        independent,
    )
