"""Local ASE data paths (demo/paper only)."""

from __future__ import annotations

import os
from pathlib import Path


def _local_app_data() -> Path:
    override = os.environ.get("ATHENA_LOCALAPPDATA", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "Athena"


def default_models_root() -> Path:
    override = os.environ.get("ATHENA_ASE_MODELS_ROOT", "").strip()
    if override:
        return Path(override)
    return _local_app_data() / "models" / "ase"


def default_ase_state_root() -> Path:
    override = os.environ.get("ATHENA_ASE_STATE_ROOT", "").strip()
    if override:
        return Path(override)
    return _local_app_data() / "ase"


def shadow_journal_path() -> Path:
    return default_ase_state_root() / "ase_shadow_journal.parquet"


def trade_journal_path() -> Path:
    return default_ase_state_root() / "ase_trade_journal.parquet"


def promotion_state_path() -> Path:
    return default_ase_state_root() / "promotion_state.json"


def ase_audit_log_path() -> Path:
    return default_ase_state_root() / "ase_audit.jsonl"
