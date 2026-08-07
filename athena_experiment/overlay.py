"""Expand a flat Tuning Lab knobs payload into a real nested config overlay.

``build_overlay`` is the only public entry point. It never touches the live
``CONFIG`` global — callers decide what to do with the returned dict (the
worker deep-merges it onto its own process-local ``CONFIG``; the push
endpoint deep-merges it onto ``config.local.yaml`` on disk).
"""

from __future__ import annotations

from typing import Any, Mapping

from athena_experiment.knobs import find_knob, validate_tf_role_selection


class OverlayValidationError(ValueError):
    """Raised for an unknown knob id, an out-of-range value, or a bad TF-role
    ladder selection. The caller (API layer) turns this into a 422."""


def _validate_value(knob, value: Any) -> Any:
    if knob.kind == "enum":
        if value not in (knob.choices or ()):
            raise OverlayValidationError(
                f"{knob.id}: {value!r} is not one of {list(knob.choices or ())}"
            )
        return value
    if knob.kind == "bool":
        if not isinstance(value, bool):
            raise OverlayValidationError(f"{knob.id}: expected a boolean, got {value!r}")
        return value
    if knob.kind == "float":
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise OverlayValidationError(f"{knob.id}: expected a number, got {value!r}")
        if knob.minimum is not None and parsed < knob.minimum:
            raise OverlayValidationError(f"{knob.id}: {parsed} is below minimum {knob.minimum}")
        if knob.maximum is not None and parsed > knob.maximum:
            raise OverlayValidationError(f"{knob.id}: {parsed} is above maximum {knob.maximum}")
        return parsed
    raise OverlayValidationError(f"{knob.id}: unknown knob kind {knob.kind!r}")


def _set_path(target: dict, path: tuple[str, ...], value: Any) -> None:
    node = target
    for key in path[:-1]:
        existing = node.get(key)
        if not isinstance(existing, dict):
            existing = {}
            node[key] = existing
        node = existing
    node[path[-1]] = value


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_tf_policy_group(
    score_group: str,
    pair: Mapping[str, Any] | None,
) -> str:
    """Policy-group key used by ENGINE_TF_ROLE_OVERRIDES.BY_GROUP.

    Live ``resolve_timeframe_policy`` prefers the *symbol-aware* policy group
    (e.g. GBPUSD → ``forex_majors_fast``, NAS100 → ``equity_index_fast``) over
    the score-group alias (``forex_majors`` → ``forex_majors_standard``). Tuning
    Lab TF-role writes must target that same key or the push is inert for the
    pair that was tested.
    """
    from engine_a_groups import (
        normalize_timeframe_policy_group,
        resolve_timeframe_policy_group,
    )

    if pair:
        try:
            symbol_group = resolve_timeframe_policy_group(dict(pair))
        except Exception:
            symbol_group = None
        text = str(symbol_group or "").strip().lower()
        if text and text != "unknown":
            return text
    return normalize_timeframe_policy_group(score_group) or score_group


def build_overlay(
    engine: str,
    group: str,
    style: str,
    knob_values: Mapping[str, Any],
    *,
    pair: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate ``{knob_id: value}`` and expand it into a nested config overlay.

    Raises ``OverlayValidationError`` on any unknown knob id, out-of-range
    value, or TF-role ladder violation — fail closed rather than silently
    dropping a bad entry.

    ``pair`` is required for correct TF-role writes whenever the selected
    instrument has a finer policy group than its score-group alias (fast
    majors, liquid crosses, fast indices). Other knobs still use the raw
    scoring group (``ENGINE_A_QUANT_WEIGHTS_BY_GROUP``, etc.).
    """
    engine_u = str(engine or "").strip().upper()
    group_key = str(group or "").strip().lower()
    style_key = str(style or "").strip().lower()
    if not group_key:
        raise OverlayValidationError("group is required")
    if not style_key:
        raise OverlayValidationError("style is required")

    # ENGINE_TF_ROLE_OVERRIDES.BY_GROUP is keyed by the v4 *timeframe-policy*
    # group taxonomy. Prefer the symbol-aware group when a pair is supplied so
    # the write matches live resolve_timeframe_policy role resolution.
    # Every other knob's {group} slot uses the raw scoring group.
    tf_policy_group = _resolve_tf_policy_group(group_key, pair)

    tf_role_selection: dict[str, str] = {}
    overlay: dict[str, Any] = {}

    for knob_id, raw_value in knob_values.items():
        knob = find_knob(knob_id)
        if knob is None:
            raise OverlayValidationError(f"unknown knob id: {knob_id!r}")
        if engine_u not in knob.applies_to:
            raise OverlayValidationError(f"{knob_id} does not apply to engine {engine_u}")
        value = _validate_value(knob, raw_value)
        is_tf_role = knob.id.startswith("tf_role.")
        if is_tf_role:
            tf_role_selection[knob.id.split(".", 1)[1]] = value

        group_for_path = tf_policy_group if is_tf_role else group_key
        path = tuple(
            group_for_path if part == "{group}" else style_key if part == "{style}" else part
            for part in knob.config_path
        )
        _set_path(overlay, path, value)

    if tf_role_selection:
        try:
            validate_tf_role_selection(tf_role_selection)
        except ValueError as exc:
            raise OverlayValidationError(str(exc)) from exc
        # Ensure the matrix stays active when a TF-role push is written —
        # a local ENABLED:false would otherwise leave the new row inert.
        _set_path(overlay, ("ENGINE_TF_ROLE_OVERRIDES", "ENABLED"), True)

    return overlay


def apply_overlay(base_config: dict, overlay: Mapping[str, Any]) -> dict:
    """Deep-merge ``overlay`` onto ``base_config`` and return a new dict.
    ``base_config`` is never mutated in place."""
    return _deep_merge(base_config, dict(overlay))
