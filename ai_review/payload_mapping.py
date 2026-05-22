"""Shared path resolution helpers for report-only payload projections."""

from __future__ import annotations

from typing import Any, Callable


def to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_path(data: dict[str, Any] | None, path: str) -> tuple[Any, bool]:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def first_source(
    roots: dict[str, dict[str, Any] | None],
    sources: tuple[tuple[str, str], ...],
) -> tuple[Any, str | None, bool]:
    for root, path in sources:
        value, found = get_path(roots.get(root), path)
        if found:
            return value, f"{root}.{path}", True
    return None, None, False


def assign_path(payload: dict[str, Any], output_path: str, value: Any) -> None:
    cur = payload
    parts = output_path.split(".")
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def append_source_field(source_fields: list[str], source: str | None) -> None:
    if source and source not in source_fields:
        source_fields.append(source)


def parse_feed_multiplier(
    feed_status: dict[str, Any],
    key: str,
    source_prefix: str,
) -> tuple[float | None, str | None]:
    if not isinstance(feed_status, dict) or key not in feed_status:
        return None, None
    value = to_float(feed_status.get(key))
    if value is None:
        return None, None
    return value, f"{source_prefix}.{key}"


def apply_field(
    payload: dict[str, Any],
    output_path: str,
    roots: dict[str, dict[str, Any] | None],
    sources: tuple[tuple[str, str], ...],
    *,
    unavailable_fields: list[str],
    source_field_map: dict[str, str],
    block_source_fields: list[str] | None = None,
    transform: Callable[[Any], Any] | None = None,
) -> Any:
    value, source, found = first_source(roots, sources)
    if not found:
        unavailable_fields.append(output_path)
        return None
    if transform is not None:
        value = transform(value)
        if value is None:
            unavailable_fields.append(output_path)
            return None
    assign_path(payload, output_path, value)
    source_field_map[output_path] = source or ""
    if block_source_fields is not None:
        append_source_field(block_source_fields, source)
    return value
