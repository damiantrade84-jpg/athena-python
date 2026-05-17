"""Explicit loader for legacy monolith module `athena.py`.

The repository also contains an `athena/` package, so `import athena` may
resolve to the package instead of the monolith file. This helper always loads
the file module from disk and caches it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_MONOLITH = None


def load() -> ModuleType:
    """Load and return the legacy monolith module from `athena.py`.

    When ``athena.py`` is the entry script (``python athena.py``), it is
    already loaded as ``__main__``. Re-executing the same file under a
    separate module name would re-run all top-level statements — duplicating
    logging handlers, monitor threads, and other module-level side effects.
    Reuse the running ``__main__`` in that case.
    """
    global _MONOLITH
    if _MONOLITH is not None:
        return _MONOLITH

    module_path = (Path(__file__).resolve().parent / "athena.py").resolve()

    main_mod = sys.modules.get("__main__")
    if main_mod is not None:
        main_file = getattr(main_mod, "__file__", None)
        if main_file:
            try:
                if Path(main_file).resolve() == module_path:
                    _MONOLITH = main_mod
                    return main_mod
            except (OSError, ValueError):
                pass

    spec = importlib.util.spec_from_file_location("athena_monolith", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load monolith module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MONOLITH = module
    return _MONOLITH

