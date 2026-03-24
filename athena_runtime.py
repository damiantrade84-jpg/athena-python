"""Runtime bindings populated once `athena.py` has finished defining helpers.

Split modules (e.g. execution routes) read from here at request time so we avoid
import cycles with the monolith.
"""

from __future__ import annotations

from typing import Any

# Shared with /api/webhook duplicate guard (same process lifetime).
executed_signals: set[str] = set()

_rt: Any | None = None


def set_runtime(deps: Any) -> None:
    global _rt
    _rt = deps


def rt() -> Any:
    if _rt is None:
        raise RuntimeError("athena runtime not initialized (set_runtime not called)")
    return _rt
