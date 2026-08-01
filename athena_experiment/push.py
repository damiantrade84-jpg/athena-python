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

import os
from pathlib import Path
from typing import Any

import yaml

from athena_experiment.overlay import apply_overlay


def local_config_path() -> Path:
    import config as _config_module

    return Path(os.path.dirname(os.path.abspath(_config_module.__file__))) / "config.local.yaml"


def push_overlay_to_local_config(overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` onto ``config.local.yaml`` and write it back.

    Returns the full merged document that was written (not just the diff) so
    the caller can show the operator exactly what the file now contains.
    """
    path = local_config_path()
    existing: dict[str, Any] = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            existing = loaded
    merged = apply_overlay(existing, overlay)
    header = (
        "# config.local.yaml — machine-local overrides, deep-merged over config.yaml at\n"
        "# startup (config.py). Gitignored. Entries below tagged \"Tuning Lab\" were\n"
        "# written by athena_experiment's push-to-default action.\n"
    )
    path.write_text(header + yaml.safe_dump(merged, sort_keys=True, default_flow_style=False), encoding="utf-8")
    return merged
