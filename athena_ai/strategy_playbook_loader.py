"""Load and validate AI strategy playbooks from YAML.

The classifier and schema both read from this loader. Validation is strict
and side-effect-free: a malformed YAML raises with the offending playbook id
and the missing field named, so a typo cannot silently disable a model.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "configs" / "ai_strategy_playbooks.yaml"

_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "enabled",
    "engines",
    "asset_groups",
    "description",
    "required_context",
    "confirmation",
    "invalid_if",
    "invalidation_logic",
    "target_logic",
    "ai_review_rule",
)

_cache_lock = threading.Lock()
_cache: dict[Path, list[dict[str, Any]]] = {}


class PlaybookValidationError(ValueError):
    """Raised when a playbook YAML file is malformed."""


def _resolve_path(path: str | Path | None) -> Path:
    if path is None:
        return _DEFAULT_PATH
    return Path(path).resolve()


def _validate_playbook(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlaybookValidationError(
            f"playbook at index {index} is not a mapping (got {type(raw).__name__})"
        )
    pid = raw.get("id")
    if not isinstance(pid, str) or not pid.strip():
        raise PlaybookValidationError(
            f"playbook at index {index} is missing required field 'id'"
        )
    for field in _REQUIRED_FIELDS:
        if field not in raw:
            raise PlaybookValidationError(
                f"playbook '{pid}' is missing required field '{field}'"
            )
    if not isinstance(raw["enabled"], bool):
        raise PlaybookValidationError(
            f"playbook '{pid}' field 'enabled' must be a boolean"
        )
    for list_field in ("engines", "asset_groups", "required_context", "confirmation", "invalid_if"):
        if not isinstance(raw[list_field], list):
            raise PlaybookValidationError(
                f"playbook '{pid}' field '{list_field}' must be a list"
            )
    return dict(raw)


def load_strategy_playbooks(
    path: str | Path | None = None,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Load and validate the playbook list. Returns a deep-copied list.

    Caching is keyed on the resolved path; pass use_cache=False in tests to
    force a reload after editing the file on disk.
    """
    resolved = _resolve_path(path)
    if use_cache:
        with _cache_lock:
            cached = _cache.get(resolved)
            if cached is not None:
                return [dict(p) for p in cached]

    if not resolved.exists():
        raise FileNotFoundError(f"playbook file not found: {resolved}")

    with resolved.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise PlaybookValidationError(
            f"playbook file root must be a mapping (got {type(data).__name__})"
        )
    raw_list = data.get("playbooks")
    if not isinstance(raw_list, list) or not raw_list:
        raise PlaybookValidationError("playbook file must contain a non-empty 'playbooks' list")

    seen_ids: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_list):
        playbook = _validate_playbook(raw, index)
        pid = playbook["id"]
        if pid in seen_ids:
            raise PlaybookValidationError(f"duplicate playbook id: {pid}")
        seen_ids.add(pid)
        validated.append(playbook)

    if use_cache:
        with _cache_lock:
            _cache[resolved] = [dict(p) for p in validated]

    return [dict(p) for p in validated]


def loaded_playbook_ids(path: str | Path | None = None) -> tuple[str, ...]:
    """Return the tuple of valid playbook ids (used by the schema validator)."""
    return tuple(p["id"] for p in load_strategy_playbooks(path))


def get_enabled_playbooks(
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    return [p for p in load_strategy_playbooks(path) if p.get("enabled")]


def _asset_group_matches(playbook_groups: list[str], asset_group: str | None) -> bool:
    if not asset_group:
        return True
    normalized = asset_group.strip().lower()
    for group in playbook_groups:
        if not isinstance(group, str):
            continue
        if group.strip() == "*":
            return True
        if group.strip().lower() == normalized:
            return True
    return False


def get_candidate_playbooks(
    asset_group: str | None,
    symbol: str | None,
    timeframe: str | None,
    engines: list[str] | None,
    chart_context: dict[str, Any] | None = None,
    *,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Filter enabled playbooks down to those applicable to the current scope.

    `engines` is the list of engine letters that have produced data for this
    review (e.g. ["A"] or ["A", "B", "D"]). A playbook is a candidate only if
    its declared engines are a subset of what is available — we never offer a
    playbook the AI cannot evaluate. `symbol`, `timeframe`, and `chart_context`
    are part of the spec signature so per-symbol or per-tf gating can be added
    without a breaking change; they are currently unused by the asset-group
    + engine filter.
    """
    del symbol, timeframe, chart_context  # reserved for future per-symbol/tf gating
    available = {str(e).strip().upper() for e in (engines or [])}
    candidates: list[dict[str, Any]] = []
    for playbook in get_enabled_playbooks(path):
        if not _asset_group_matches(playbook.get("asset_groups") or [], asset_group):
            continue
        declared = {str(e).strip().upper() for e in playbook.get("engines") or []}
        if declared and not declared.issubset(available):
            continue
        candidates.append(playbook)
    return candidates


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
