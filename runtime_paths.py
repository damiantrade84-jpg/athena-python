from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("sentinel")

_ROOT = Path(__file__).resolve().parent
_LEGACY_CANDLE_CACHE_DB = _ROOT / "candle_cache.db"
_LEGACY_BACKTEST_CANDLE_CACHE_DB = _ROOT / "backtest_candle_cache.db"
_LEGACY_BACKUP_DIR = _ROOT / "db_backups"


def _athena_local_dir() -> Path | None:
    if os.name != "nt":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    target = Path(local_app_data) / "Athena"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning(f"[PATHS] Local Athena dir unavailable, using legacy repo path: {exc}")
        return None
    return target


def resolve_candle_cache_db_path() -> Path:
    """Resolve the candle cache path with a Windows default outside OneDrive."""
    env_path = os.environ.get("ATHENA_CANDLE_CACHE_DB_PATH", "").strip()
    if env_path:
        target = Path(env_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    local_dir = _athena_local_dir()
    if local_dir is not None:
        return local_dir / "candle_cache.db"
    return _LEGACY_CANDLE_CACHE_DB


def ensure_candle_cache_db_ready() -> Path:
    """Copy the legacy repo cache once when switching to the local Windows path."""
    target = resolve_candle_cache_db_path()
    if target == _LEGACY_CANDLE_CACHE_DB:
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning(f"[PATHS] Local candle cache path unavailable, using legacy repo path: {exc}")
        return _LEGACY_CANDLE_CACHE_DB
    if target.exists() or not _LEGACY_CANDLE_CACHE_DB.exists():
        return target
    try:
        shutil.copy2(_LEGACY_CANDLE_CACHE_DB, target)
        log.warning(f"[PATHS] Migrated candle cache to local path: {target}")
    except Exception as exc:
        log.warning(f"[PATHS] Candle cache migration warning: {exc}")
        return _LEGACY_CANDLE_CACHE_DB
    return target


def resolve_backtest_candle_cache_db_path() -> Path:
    """Resolve the separate backtest candle cache path."""
    env_path = os.environ.get("ATHENA_BACKTEST_CANDLE_CACHE_DB_PATH", "").strip()
    if env_path:
        target = Path(env_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    local_dir = _athena_local_dir()
    if local_dir is not None:
        return local_dir / "backtest_candle_cache.db"
    return _LEGACY_BACKTEST_CANDLE_CACHE_DB


def resolve_backup_dir() -> Path:
    """Resolve the DB backup directory with a Windows default outside OneDrive."""
    env_dir = os.environ.get("ATHENA_DB_BACKUP_DIR", "").strip()
    if env_dir:
        target = Path(env_dir).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        return target
    local_dir = _athena_local_dir()
    if local_dir is not None:
        target = local_dir / "db_backups"
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target
        except OSError as exc:
            log.warning(f"[PATHS] Local backup dir unavailable, using legacy repo path: {exc}")
    _LEGACY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return _LEGACY_BACKUP_DIR
