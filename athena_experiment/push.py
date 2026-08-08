"""Push an accepted Tuning Lab overlay into ``config.local.yaml``.

``config.local.yaml`` is the existing gitignored, deep-merged-on-startup local
override file (see ``config.py``'s loader, ~line 810-828) — the established
mechanism for "operator tuning takes effect on next restart". This module
never touches the tracked ``config.yaml`` and never touches a running
process's in-memory ``CONFIG``; it only rewrites that one local YAML file.

Because ``config.local.yaml`` is a machine-local scratch file (not a
documented, hand-commented file like ``config.yaml``), a round-trip through
``yaml.safe_load``/``yaml.safe_dump`` here will not preserve any comments a
user may have hand-added to it. Every write is still a deep-merge over the
file's *existing* keys/values, so no prior override is lost — only comment
formatting.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from athena_experiment.overlay import apply_overlay


_PUSH_LOCK = threading.Lock()


def local_config_path() -> Path:
    import config as _config_module

    return Path(os.path.dirname(os.path.abspath(_config_module.__file__))) / "config.local.yaml"


def push_audit_path(config_path: Path | None = None) -> Path:
    path = config_path or local_config_path()
    return path.parent / "logs" / "tuning_lab_push_audit.jsonl"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass


def push_overlay_to_local_config(
    overlay: dict[str, Any],
    *,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deep-merge ``overlay`` onto ``config.local.yaml`` and write it back.

    Returns the full merged document that was written (not just the diff) so
    the caller can show the operator exactly what the file now contains.
    """
    if not isinstance(overlay, dict) or not overlay:
        raise ValueError("overlay must be a non-empty mapping")
    path = local_config_path()
    with _PUSH_LOCK:
        existing: dict[str, Any] = {}
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            loaded = yaml.safe_load(raw)
            if isinstance(loaded, dict):
                existing = loaded
        merged = apply_overlay(existing, overlay)
        header = (
            "# config.local.yaml — machine-local overrides, deep-merged over config.yaml at\n"
            "# startup (config.py). Gitignored. Tuning Lab writes are recorded in\n"
            "# logs/tuning_lab_push_audit.jsonl and require a restart to become live.\n"
        )
        serialized = yaml.safe_dump(merged, sort_keys=True, default_flow_style=False)
        _atomic_write_text(path, header + serialized)

        canonical_overlay = json.dumps(
            overlay, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "configPath": str(path.resolve()),
            "overlaySha256": hashlib.sha256(canonical_overlay.encode("utf-8")).hexdigest(),
            "overlay": overlay,
            "provenance": dict(provenance or {}),
            "restartRequired": True,
        }
        audit_path = push_audit_path(path)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return merged
