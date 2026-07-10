"""Safe atomic Parquet read/write helpers for ASE local stores."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

log = logging.getLogger("ase.parquet_io")

_PARQUET_MAGIC = b"PAR1"
_MIN_PARQUET_BYTES = 8


def is_valid_parquet_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < _MIN_PARQUET_BYTES:
        return False
    try:
        with path.open("rb") as handle:
            handle.seek(-4, os.SEEK_END)
            footer = handle.read(4)
        return footer == _PARQUET_MAGIC
    except OSError:
        return False


def quarantine_corrupt_parquet(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = path.with_name(f"{path.name}.corrupt.{stamp}")
    try:
        path.replace(dest)
        log.warning("quarantined corrupt parquet: %s -> %s", path, dest)
        return dest
    except OSError as exc:
        log.warning("failed to quarantine corrupt parquet %s: %s", path, exc)
        return None


def read_parquet_safe(path: Path) -> pd.DataFrame:
    """Read a Parquet file; return empty DataFrame on missing/invalid/corrupt input."""
    if not path.exists():
        return pd.DataFrame()
    if not is_valid_parquet_file(path):
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        log.warning("invalid parquet file (%s bytes): %s", size, path)
        quarantine_corrupt_parquet(path)
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except ImportError:
        # Missing parquet engine (pyarrow/fastparquet) is an environment
        # failure, not file corruption — quarantining here would destroy a
        # healthy store. Fail loudly instead of returning fictitious "no data".
        raise
    except Exception as exc:
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        log.warning("parquet read failed (%s bytes) %s: %s", size, path, exc)
        quarantine_corrupt_parquet(path)
        return pd.DataFrame()


def write_parquet_atomic(path: Path, frame: pd.DataFrame) -> None:
    """Write Parquet atomically via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_parquet(temp, index=False)
        if not is_valid_parquet_file(temp):
            raise RuntimeError(f"atomic parquet write produced invalid file: {temp}")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
