"""Point-in-Time Store (PTIS) — sole read path for ASE features (§1.1)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np
import pandas as pd

from athena_ase.data.availability import AvailabilityRuleId, rule_for_source, series_metadata_for_audit
from athena_ase.data.parquet_io import read_parquet_safe, write_parquet_atomic

PTIS_ROW_COLUMNS = (
    "series_id",
    "value_time",
    "available_time",
    "value",
    "revision",
    "ingest_time",
)

PTIS_VALUE_DTYPE = np.dtype(
    [
        ("value_time", "i8"),
        ("available_time", "i8"),
        ("value", "f8"),
        ("revision", "i2"),
        ("ingest_time", "i8"),
    ]
)

_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    series_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 1,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_series_source ON series(source);
"""


def default_ptis_root() -> Path:
    override = os.environ.get("ATHENA_PTIS_ROOT", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "Athena" / "ptis"


def dataset_hash_from_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dataset_hash_from_parquet(path: Path) -> str:
    return dataset_hash_from_bytes(path.read_bytes())


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _series_path_component(series_id: str) -> str:
    return series_id.replace(":", "_").replace("/", "_")


def _year_from_ms(ms: int | float) -> int:
    ms_int = int(ms)
    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms_int)
    return dt.year


class PTISStore:
    """Parquet + SQLite catalog point-in-time store."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_ptis_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "ptis_catalog.db"
        self._lock = threading.Lock()
        self._frame_cache: dict[str, pd.DataFrame] = {}
        self._init_catalog()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # NB: a sqlite3.Connection used as a context manager commits/rolls back
        # the transaction but does NOT close the connection, which leaks the
        # handle until GC (surfaces as ResourceWarning: unclosed database).
        # This wrapper preserves the commit-on-success / rollback-on-error
        # semantics callers rely on and also closes the connection.
        con = sqlite3.connect(self.catalog_path, timeout=15.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_catalog(self) -> None:
        with self._connect() as con:
            con.executescript(_CATALOG_SCHEMA)

    def register_series(
        self,
        series_id: str,
        source: str,
        *,
        meta: dict[str, Any] | None = None,
        verified: bool | None = None,
    ) -> dict[str, Any]:
        rule_id = rule_for_source(source)
        audit = series_metadata_for_audit(series_id, source)
        is_verified = audit["verified"] if verified is None else verified
        now = datetime.now(timezone.utc).isoformat()
        payload = dict(meta or {})
        payload.update({"audit": audit})
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO series (series_id, source, rule_id, verified, meta_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id) DO UPDATE SET
                    source=excluded.source,
                    rule_id=excluded.rule_id,
                    verified=excluded.verified,
                    meta_json=excluded.meta_json,
                    updated_at=excluded.updated_at
                """,
                (
                    series_id,
                    source,
                    rule_id.value,
                    1 if is_verified else 0,
                    json.dumps(payload, sort_keys=True),
                    now,
                    now,
                ),
            )
        return audit

    def series_exists(self, series_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM series WHERE series_id = ?", (series_id,)
            ).fetchone()
        return row is not None

    def list_series(self) -> list[str]:
        with self._connect() as con:
            rows = con.execute("SELECT series_id FROM series ORDER BY series_id").fetchall()
        return [r[0] for r in rows]

    def catalog_series_count(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) FROM series").fetchone()
        return int(row[0]) if row else 0

    def partition_dir(self, source: str, series_id: str, year: int) -> Path:
        path = self.root / source / _series_path_component(series_id) / str(year)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def partition_file(self, source: str, series_id: str, year: int) -> Path:
        return self.partition_dir(source, series_id, year) / "data.parquet"

    def _series_source(self, series_id: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT source FROM series WHERE series_id = ?", (series_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown PTIS series: {series_id}")
        return str(row[0])

    def _iter_partition_paths(self, source: str, series_id: str) -> list[Path]:
        base = self.root / source / _series_path_component(series_id)
        if not base.exists():
            return []
        paths: list[Path] = []
        for year_dir in sorted(base.glob("*"), key=lambda path: path.name):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            part_path = year_dir / "data.parquet"
            if part_path.exists():
                paths.append(part_path)
        return paths

    def _read_series_partitions(self, source: str, series_id: str) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for part_path in self._iter_partition_paths(source, series_id):
            part = read_parquet_safe(part_path)
            if not part.empty:
                frames.append(part)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _load_series_frame(self, series_id: str, source: str) -> pd.DataFrame:
        cached = self._frame_cache.get(series_id)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._frame_cache.get(series_id)
            if cached is not None:
                return cached
            frame = self._read_series_partitions(source, series_id)
            self._frame_cache[series_id] = frame
            return frame

    def append_rows(
        self,
        series_id: str,
        rows: Sequence[dict[str, Any]],
        *,
        source: str | None = None,
        auto_register: bool = True,
    ) -> int:
        if not rows:
            return 0
        if self.series_exists(series_id):
            src = source or self._series_source(series_id)
        else:
            src = source
        if src is None:
            raise KeyError(f"series {series_id} not registered and source not provided")
        if not self.series_exists(series_id):
            if not auto_register:
                raise KeyError(f"series {series_id} not registered")
            self.register_series(series_id, src)

        df = pd.DataFrame(rows)
        for col in PTIS_ROW_COLUMNS:
            if col not in df.columns:
                raise ValueError(f"missing required column: {col}")
        df = df[list(PTIS_ROW_COLUMNS)].copy()
        df["series_id"] = series_id

        ingest_default = _utc_now_ms()
        # Fill every missing ingest_time, not just when row 0 lacks one — and
        # never clobber valid per-row values with the default.
        df["ingest_time"] = pd.to_numeric(df["ingest_time"], errors="coerce").fillna(
            ingest_default
        )

        written = 0
        df["year"] = df["value_time"].map(_year_from_ms)
        with self._lock:
            for year, chunk in df.groupby("year"):
                year = int(year)
                part_path = self.partition_file(src, series_id, year)
                if part_path.exists():
                    existing = read_parquet_safe(part_path)
                    if existing.empty:
                        merged = chunk.drop(columns=["year"])
                    else:
                        merged = pd.concat(
                            [existing, chunk.drop(columns=["year"])], ignore_index=True
                        )
                else:
                    merged = chunk.drop(columns=["year"])
                merged = merged.sort_values(
                    ["value_time", "revision", "ingest_time"]
                ).drop_duplicates(subset=["value_time", "revision"], keep="last")
                write_parquet_atomic(part_path, merged)
                written += len(chunk)
        self._frame_cache.pop(series_id, None)
        return written

    def asof(self, series_id: str, decision_time_ms: int, n: int = 1) -> np.ndarray:
        """Last n values with available_time <= decision_time_ms, ordered by value_time."""
        if n <= 0:
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        source = self._series_source(series_id)
        frame = self._load_series_frame(series_id, source)
        if frame.empty:
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        df = frame[frame["available_time"] <= decision_time_ms].copy()
        if df.empty:
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        df = df.sort_values(["value_time", "revision", "ingest_time"])
        df = df.drop_duplicates(subset=["value_time"], keep="last")
        df = df.sort_values("value_time")
        tail = df.tail(n)

        out = np.empty(len(tail), dtype=PTIS_VALUE_DTYPE)
        out["value_time"] = tail["value_time"].to_numpy(dtype="i8")
        out["available_time"] = tail["available_time"].to_numpy(dtype="i8")
        out["value"] = tail["value"].to_numpy(dtype="f8")
        out["revision"] = tail["revision"].to_numpy(dtype="i2")
        out["ingest_time"] = tail["ingest_time"].to_numpy(dtype="i8")
        return out

    def load_window(self, series_id: str, start_ms: int, end_ms: int) -> np.ndarray:
        """All rows with value_time in [start_ms, end_ms], latest revision per value_time.

        Revision resolution is window-global: the newest revision in the whole
        window wins even if it only became available after some decision time
        inside the window. Callers mask by ``available_time`` per decision, so
        a too-new revision degrades to a gap (fail-closed), never to lookahead.
        """
        if end_ms < start_ms:
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        source = self._series_source(series_id)
        frame = self._load_series_frame(series_id, source)
        if frame.empty:
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        df = frame[
            (frame["value_time"] >= start_ms) & (frame["value_time"] <= end_ms)
        ].copy()
        if df.empty:
            return np.empty(0, dtype=PTIS_VALUE_DTYPE)
        df = df.sort_values(["value_time", "revision", "ingest_time"])
        df = df.drop_duplicates(subset=["value_time"], keep="last")
        df = df.sort_values("value_time")
        out = np.empty(len(df), dtype=PTIS_VALUE_DTYPE)
        out["value_time"] = df["value_time"].to_numpy(dtype="i8")
        out["available_time"] = df["available_time"].to_numpy(dtype="i8")
        out["value"] = df["value"].to_numpy(dtype="f8")
        out["revision"] = df["revision"].to_numpy(dtype="i2")
        out["ingest_time"] = df["ingest_time"].to_numpy(dtype="i8")
        return out

    def audit_rows(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT series_id, source, rule_id, verified, meta_json FROM series ORDER BY series_id"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for series_id, source, rule_id, verified, meta_json in rows:
            meta = json.loads(meta_json or "{}")
            audit = meta.get("audit") or series_metadata_for_audit(series_id, source)
            audit = dict(audit)
            audit["rule_id"] = rule_id
            audit["verified"] = bool(verified)
            if not audit.get("flag") and not audit["verified"]:
                audit["flag"] = "UNVERIFIED_LAG"
            out.append(audit)
        return out

    def series_row_count(self, series_id: str) -> int:
        source = self._series_source(series_id)
        with self._lock:
            total = 0
            for part_path in self._iter_partition_paths(source, series_id):
                total += len(read_parquet_safe(part_path))
            return total


_default_store: PTISStore | None = None
_default_lock = threading.Lock()


def get_store(root: Path | str | None = None) -> PTISStore:
    global _default_store
    if root is not None:
        return PTISStore(root)
    with _default_lock:
        if _default_store is None:
            _default_store = PTISStore()
        return _default_store


def asof(series_id: str, decision_time_ms: int, n: int = 1) -> np.ndarray:
    """Module-level query API — the only PTIS read entry for features."""
    return get_store().asof(series_id, decision_time_ms, n)


def load_window(series_id: str, start_ms: int, end_ms: int) -> np.ndarray:
    """Module-level bulk window read for Layer 1 / backtest paths."""
    return get_store().load_window(series_id, start_ms, end_ms)


def build_row(
    series_id: str,
    value_time_ms: int,
    available_time_ms: int,
    value: float,
    *,
    revision: int = 0,
    ingest_time_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "value_time": int(value_time_ms),
        "available_time": int(available_time_ms),
        "value": float(value),
        "revision": int(revision),
        "ingest_time": int(ingest_time_ms if ingest_time_ms is not None else _utc_now_ms()),
    }


def ingest_with_rule(
    store: PTISStore,
    series_id: str,
    source: str,
    rule_id: AvailabilityRuleId,
    points: Iterable[tuple[int, float]],
    *,
    bar_close_ms: int | None = None,
    realtime_start_ms: int | None = None,
) -> int:
    """Helper for ingest pipelines: compute available_time from frozen rules."""
    from athena_ase.data.availability import compute_available_time_ms

    rows = []
    for value_time_ms, value in points:
        avail = compute_available_time_ms(
            rule_id,
            value_time_ms=value_time_ms,
            bar_close_ms=bar_close_ms,
            realtime_start_ms=realtime_start_ms,
        )
        rows.append(build_row(series_id, value_time_ms, avail, value))
    store.register_series(series_id, source)
    return store.append_rows(series_id, rows, source=source, auto_register=False)
