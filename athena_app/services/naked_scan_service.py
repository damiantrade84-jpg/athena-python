"""Small helpers for Engine B naked scan response handling."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def iter_naked_scan_rows(result: Any) -> list[dict[str, Any]]:
    """Normalize a naked-scan worker result to row dictionaries.

    The worker usually returns a list, but debug-only exits can return a single
    ``{"debug": ...}`` dict. Iterating that dict directly yields string keys and
    can crash the endpoint.
    """
    if result is None:
        return []
    if isinstance(result, dict):
        return [result]
    if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
        return [row for row in result if isinstance(row, dict)]
    return []
