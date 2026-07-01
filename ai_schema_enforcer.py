"""ai_schema_enforcer.py — strict Pydantic schema enforcement for AI responses.

Phase 0 of the Athena AI Edge Program. Config-gated by
`AI_STRICT_SCHEMA_ENFORCEMENT` (default False). When disabled, validation is
skipped and raw dicts pass through unchanged (zero behavior change).

When enabled, every AI response dict is validated against its Pydantic schema.
On validation failure, the configured safe default is returned instead —
fail-closed. Never raises.

Safety:
- Advisory-only. Schema validation does not grant execution permission.
- Safe defaults are caller-supplied so each surface controls its own
  fail-closed shape.
- Validation errors are logged via the standard ai_review_logger trail.
"""

from __future__ import annotations

import logging
from typing import Any, Type

try:
    from pydantic import BaseModel, ValidationError
except Exception:  # pragma: no cover - pydantic is a hard dep
    BaseModel = None  # type: ignore
    ValidationError = Exception  # type: ignore

try:
    from config import CONFIG
except Exception:  # pragma: no cover
    CONFIG = {}

log = logging.getLogger("athena.schema_enforcer")


def is_strict_enforcement_enabled() -> bool:
    try:
        return bool(CONFIG.get("AI_STRICT_SCHEMA_ENFORCEMENT", False))
    except Exception:
        return False


def validate_or_safe_default(
    schema_model: Type[BaseModel] | None,
    raw: dict[str, Any] | None,
    *,
    surface: str,
    safe_default: dict[str, Any],
    strict: bool | None = None,
) -> tuple[dict[str, Any], bool]:
    """Validate `raw` against `schema_model`.

    Returns ``(validated_dict, ok)``.
    - If strict enforcement is disabled (or `strict=False` is passed), returns
      ``(raw, True)`` without validation. This preserves backward-compatible
      behavior when the feature is off.
    - If validation succeeds, returns ``(parsed.model_dump(mode="json"), True)``.
    - If validation fails (or raw is not a dict), returns
      ``(safe_default, False)`` and logs a warning. Never raises.

    `surface` is used only for logging.
    """
    if strict is None:
        strict = is_strict_enforcement_enabled()
    if not strict:
        return (raw if isinstance(raw, dict) else dict(safe_default)), True

    if schema_model is None or BaseModel is None:
        log.debug("[SCHEMA_ENFORCER] no schema for surface=%s; passing through", surface)
        return (raw if isinstance(raw, dict) else dict(safe_default)), True

    if not isinstance(raw, dict):
        log.warning(
            "[SCHEMA_ENFORCER] surface=%s rejected: raw is not a dict (type=%s); using safe default",
            surface,
            type(raw).__name__,
        )
        return dict(safe_default), False

    try:
        parsed = schema_model.model_validate(raw)
        return parsed.model_dump(mode="json"), True
    except (ValidationError, Exception) as exc:
        log.warning(
            "[SCHEMA_ENFORCER] surface=%s schema validation failed: %s; using safe default",
            surface,
            exc,
        )
        # Merge safe default with any non-validated fields the caller might want
        out = dict(safe_default)
        out.setdefault("_schema_validation_failed", True)
        return out, False
