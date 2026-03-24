"""Explicit loader for legacy monolith module `athena.py`.

The repository also contains an `athena/` package, so `import athena` may
resolve to the package instead of the monolith file. This helper always loads
the file module from disk and caches it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_MONOLITH = None


def load() -> ModuleType:
    """Load and return the legacy monolith module from `athena.py`."""
    global _MONOLITH
    if _MONOLITH is not None:
        return _MONOLITH

    root = Path(__file__).resolve().parent
    module_path = root / "athena.py"
    spec = importlib.util.spec_from_file_location("athena_monolith", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load monolith module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MONOLITH = module
    return _MONOLITH

