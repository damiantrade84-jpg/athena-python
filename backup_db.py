"""
backup_db.py — Automated database backup for Athena.
Run manually or called at startup to protect audit.db and candle_cache.db.
Keeps last 7 daily backups. Never overwrites — always creates new timestamped copy.
"""

import os
import shutil
import sqlite3
from datetime import datetime

from runtime_paths import ensure_candle_cache_db_ready, resolve_backup_dir

_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKUP_DIR = resolve_backup_dir()

DATABASES = [
    "audit.db",
    "candle_cache.db",
]


def _database_sources() -> dict[str, str]:
    return {
        "audit.db": os.path.join(_DIR, "audit.db"),
        "candle_cache.db": str(ensure_candle_cache_db_ready()),
    }


def backup_now(reason: str = "manual") -> list:
    """Create timestamped backup of all databases. Returns list of backup paths."""
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backed_up = []

    for db_name, src in _database_sources().items():
        if not os.path.exists(src):
            continue
        dst = os.path.join(_BACKUP_DIR, f"{db_name}.{ts}.{reason}.bak")
        # Use SQLite backup API — safe even if DB is open
        try:
            src_con = sqlite3.connect(src)
            dst_con = sqlite3.connect(dst)
            src_con.backup(dst_con)
            dst_con.close()
            src_con.close()
            backed_up.append(dst)
            print(f"[BACKUP] {db_name} -> {os.path.basename(dst)}")
        except Exception as e:
            print(f"[BACKUP] ERROR backing up {db_name}: {e}")

    _prune_old_backups()
    return backed_up


def _prune_old_backups(keep: int = 7):
    """Keep only the most recent `keep` backups per database."""
    if not os.path.exists(_BACKUP_DIR):
        return
    for db_name in DATABASES:
        backups = sorted(
            [
                f
                for f in os.listdir(_BACKUP_DIR)
                if f.startswith(db_name) and f.endswith(".bak")
            ]
        )
        for old in backups[:-keep]:
            try:
                os.remove(os.path.join(_BACKUP_DIR, old))
            except Exception:
                pass


def restore_latest(db_name: str) -> bool:
    """Restore the most recent backup of a database."""
    sources = _database_sources()
    dst = sources.get(db_name)
    if not dst:
        print(f"[RESTORE] Unknown database: {db_name}")
        return False
    if not os.path.exists(_BACKUP_DIR):
        print("[RESTORE] No backup directory found")
        return False
    backups = sorted(
        [
            f
            for f in os.listdir(_BACKUP_DIR)
            if f.startswith(db_name) and f.endswith(".bak")
        ]
    )
    if not backups:
        print(f"[RESTORE] No backups found for {db_name}")
        return False
    latest = os.path.join(_BACKUP_DIR, backups[-1])
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(latest, dst)
        print(f"[RESTORE] {db_name} restored from {backups[-1]}")
        return True
    except Exception as e:
        print(f"[RESTORE] ERROR: {e}")
        return False


def list_backups():
    """Print all available backups."""
    if not os.path.exists(_BACKUP_DIR):
        print("No backups found")
        return
    files = sorted(os.listdir(_BACKUP_DIR))
    for f in files:
        path = os.path.join(_BACKUP_DIR, f)
        size = os.path.getsize(path) / 1024
        print(f"  {f}  ({size:.1f} KB)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        db = sys.argv[2] if len(sys.argv) > 2 else "audit.db"
        restore_latest(db)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        list_backups()
    else:
        backup_now("manual")
