from __future__ import annotations

import os
from pathlib import Path


def runtime_root() -> Path:
    """Return the isolated writable v2 runtime root.

    Tests and portable builds may override this with
    ``ATHENA_BACKTESTING_V2_ROOT``.  Production defaults to the selected local
    Windows location and never writes into the repository.
    """

    override = str(os.environ.get("ATHENA_BACKTESTING_V2_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return (Path(local_app_data) / "Athena" / "backtesting_v2").resolve()


def runtime_layout(root: Path | None = None) -> dict[str, Path]:
    """Return the runtime paths without touching the filesystem.

    Use this when you only need to know where something lives — creating the
    directories is a side effect that must not happen at import time.
    """
    base = (root or runtime_root()).resolve()
    return {
        "root": base,
        "catalog": base / "catalog.sqlite3",
        "datasets": base / "datasets",
        "runs": base / "runs",
        "exports": base / "exports",
        "logs": base / "logs",
    }


def ensure_runtime_layout(root: Path | None = None) -> dict[str, Path]:
    layout = runtime_layout(root)
    for key in ("root", "datasets", "runs", "exports", "logs"):
        layout[key].mkdir(parents=True, exist_ok=True)
    return layout


def assert_within_runtime(path: Path, root: Path | None = None) -> Path:
    base = (root or runtime_root()).resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path outside backtesting v2 runtime: {resolved}") from exc
    return resolved

