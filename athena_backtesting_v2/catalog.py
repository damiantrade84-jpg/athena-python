from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .constants import CATALOG_SCHEMA_VERSION
from .hashing import canonical_json
from .models import utc_now_iso
from .paths import ensure_runtime_layout, runtime_layout


class BacktestCatalog:
    """SQLite metadata catalog for immutable datasets and durable jobs."""

    def __init__(self, path: Path | None = None):
        self.path = (path or runtime_layout()["catalog"]).resolve()
        self._migration_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def _raw_connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection without running the schema migration.

        Only ``initialize`` may use this; every other caller goes through
        ``connect`` so the schema is guaranteed to exist.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        with self._raw_connect() as connection:
            yield connection

    def initialize(self) -> None:
        """Create the runtime layout and schema, once, on first use.

        Deliberately lazy.  athena.py builds a BacktestingV2Service at module
        scope, so an eager __init__ opened a SQLite database and created five
        directories merely by importing athena — which
        tests/test_import_safety.py exists to forbid.  Callers that want the
        old eager behaviour can still call this directly.
        """
        if self._initialized:
            return
        with self._migration_lock:
            if self._initialized:
                return
            ensure_runtime_layout()
            self._initialize_locked()
            self._initialized = True

    def _initialize_locked(self) -> None:
        with self._raw_connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    certification TEXT NOT NULL,
                    requested_mode TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    series_count INTEGER NOT NULL DEFAULT 0,
                    exclusion_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS dataset_series (
                    series_id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE RESTRICT,
                    symbol TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    first_timestamp TEXT,
                    last_timestamp TEXT,
                    gap_count INTEGER NOT NULL,
                    duplicate_count INTEGER NOT NULL,
                    quality_state TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    UNIQUE(dataset_id, symbol, timeframe)
                );
                CREATE INDEX IF NOT EXISTS idx_dataset_series_lookup
                    ON dataset_series(dataset_id, symbol, timeframe);

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    reusable_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress_completed INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    dataset_id TEXT,
                    config_snapshot_id TEXT,
                    certification TEXT NOT NULL DEFAULT 'EXPLORATORY',
                    request_json TEXT NOT NULL,
                    config_snapshot_json TEXT,
                    result_json TEXT,
                    error_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    recovered_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_reuse ON runs(reusable_hash, status);

                CREATE TABLE IF NOT EXISTS run_tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    engine TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    style TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_sec REAL,
                    peak_memory_bytes INTEGER,
                    result_json TEXT,
                    error_json TEXT,
                    UNIQUE(run_id, engine, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_run_tasks_run ON run_tasks(run_id, status);

                CREATE TABLE IF NOT EXISTS trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    engine TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    net_r REAL NOT NULL,
                    split TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trades_page
                    ON trades(run_id, trade_id);

                CREATE TABLE IF NOT EXISTS comparisons (
                    comparison_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    run_ids_json TEXT NOT NULL,
                    compatibility_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('schema_version', ?)",
                (str(CATALOG_SCHEMA_VERSION),),
            )
            connection.commit()

    @staticmethod
    def _decode_row(row: sqlite3.Row | None, *json_fields: str) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in json_fields:
            raw = result.get(field)
            if raw:
                try:
                    result[field[:-5] if field.endswith("_json") else field] = json.loads(raw)
                except json.JSONDecodeError:
                    result[field[:-5] if field.endswith("_json") else field] = None
        return result

    def insert_run(self, row: dict[str, Any], tasks: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO runs(
                    run_id, request_hash, reusable_hash, created_at, updated_at,
                    status, phase, progress_total, request_json, certification
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["run_id"], row["request_hash"], row["reusable_hash"],
                    row["created_at"], row["created_at"], row["status"], row["phase"],
                    len(tasks), canonical_json(row["request"]), row.get("certification", "EXPLORATORY"),
                ),
            )
            connection.executemany(
                """INSERT INTO run_tasks(task_id, run_id, engine, symbol, style, status)
                   VALUES(?,?,?,?,?,?)""",
                [
                    (task["task_id"], row["run_id"], task["engine"], task["symbol"], task["style"], "PENDING")
                    for task in tasks
                ],
            )
            connection.commit()

    def find_reusable_run(self, reusable_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE reusable_hash=? AND status='COMPLETED' ORDER BY completed_at DESC LIMIT 1",
                (reusable_hash,),
            ).fetchone()
        return self._decode_row(row, "request_json", "config_snapshot_json", "result_json", "error_json")

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            tasks = connection.execute(
                "SELECT * FROM run_tasks WHERE run_id=? ORDER BY engine, symbol", (run_id,)
            ).fetchall()
        result = self._decode_row(row, "request_json", "config_snapshot_json", "result_json", "error_json")
        if result is not None:
            result["tasks"] = [
                self._decode_row(task, "result_json", "error_json") for task in tasks
            ]
        return result

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._decode_row(row, "request_json", "result_json", "error_json") for row in rows]

    def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {
            "status", "phase", "started_at", "completed_at", "progress_completed",
            "progress_total", "dataset_id", "config_snapshot_id", "certification",
            "config_snapshot_json", "result_json", "error_json", "cancel_requested",
            "recovered_count",
            "reusable_hash", "request_hash",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported run fields: {sorted(unknown)}")
        encoded: dict[str, Any] = {}
        for key, value in fields.items():
            encoded[key] = canonical_json(value) if key.endswith("_json") and value is not None else value
        encoded["updated_at"] = utc_now_iso()
        assignments = ", ".join(f"{key}=?" for key in encoded)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE runs SET {assignments} WHERE run_id=?",
                (*encoded.values(), run_id),
            )
            connection.commit()

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {"status", "started_at", "completed_at", "duration_sec", "peak_memory_bytes", "result_json", "error_json"}
        if set(fields) - allowed:
            raise ValueError("unsupported run task fields")
        encoded = {
            key: canonical_json(value) if key.endswith("_json") and value is not None else value
            for key, value in fields.items()
        }
        assignments = ", ".join(f"{key}=?" for key in encoded)
        with self.connect() as connection:
            connection.execute(f"UPDATE run_tasks SET {assignments} WHERE task_id=?", (*encoded.values(), task_id))
            connection.commit()

    def pending_tasks(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_tasks WHERE run_id=? AND status IN ('PENDING','INTERRUPTED') ORDER BY engine,symbol",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def request_cancel(self, run_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET cancel_requested=1, updated_at=? WHERE run_id=? AND status NOT IN ('COMPLETED','FAILED','CANCELLED')",
                (utc_now_iso(), run_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def cancel_requested(self, run_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row[0])

    def recover_incomplete_runs(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM runs WHERE status IN ('PREPARING_DATA','RUNNING','VALIDATING','INTERRUPTED')"
            ).fetchall()
            run_ids = [str(row[0]) for row in rows]
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                connection.execute(
                    f"UPDATE runs SET status='QUEUED', phase='RECOVERED_AFTER_RESTART', recovered_count=recovered_count+1, updated_at=? WHERE run_id IN ({placeholders})",
                    (utc_now_iso(), *run_ids),
                )
                connection.execute(
                    f"UPDATE run_tasks SET status='INTERRUPTED' WHERE run_id IN ({placeholders}) AND status='RUNNING'",
                    tuple(run_ids),
                )
                connection.commit()
        return run_ids

    def queued_run_ids(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT run_id FROM runs WHERE status='QUEUED' ORDER BY created_at").fetchall()
        return [str(row[0]) for row in rows]

    def save_trades(self, run_id: str, trades: list[dict[str, Any]]) -> None:
        if not trades:
            return
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO trades(run_id,engine,symbol,entry_time,exit_time,net_r,split,payload_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, trade["engine"], trade["symbol"], trade["entry_time"],
                        trade["exit_time"], float(trade["net_r"]), trade.get("split", "UNASSIGNED"),
                        canonical_json(trade),
                    )
                    for trade in trades
                ],
            )
            connection.commit()

    def clone_trades(self, source_run_id: str, target_run_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO trades(run_id,engine,symbol,entry_time,exit_time,net_r,split,payload_json)
                   SELECT ?,engine,symbol,entry_time,exit_time,net_r,split,
                          replace(payload_json, ?, ?)
                   FROM trades WHERE run_id=?""",
                (target_run_id, source_run_id, target_run_id, source_run_id),
            )
            connection.commit()

    def list_trades(self, run_id: str, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        with self.connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM trades WHERE run_id=?", (run_id,)).fetchone()[0])
            rows = connection.execute(
                "SELECT trade_id,payload_json FROM trades WHERE run_id=? ORDER BY trade_id LIMIT ? OFFSET ?",
                (run_id, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["tradeId"] = row["trade_id"]
            items.append(payload)
        return {"items": items, "total": count, "limit": limit, "offset": offset}

    def upsert_dataset(self, manifest: dict[str, Any]) -> None:
        series = list(manifest.get("series") or [])
        exclusions = list(manifest.get("exclusions") or [])
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR REPLACE INTO datasets(
                    dataset_id,created_at,status,certification,requested_mode,start_date,end_date,
                    manifest_json,manifest_hash,row_count,series_count,exclusion_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    manifest["datasetId"], manifest["createdAt"], manifest["status"], manifest["certification"],
                    manifest["requestedMode"], manifest["startDate"], manifest["endDate"],
                    canonical_json(manifest), manifest["manifestHash"],
                    sum(int(item.get("row_count", item.get("rowCount", 0))) for item in series),
                    len(series), len(exclusions),
                ),
            )
            connection.execute("DELETE FROM dataset_series WHERE dataset_id=?", (manifest["datasetId"],))
            connection.executemany(
                """INSERT INTO dataset_series(
                    series_id,dataset_id,symbol,provider,provider_symbol,timeframe,asset_type,path,file_hash,
                    row_count,first_timestamp,last_timestamp,gap_count,duplicate_count,quality_state,manifest_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        item["series_id"], manifest["datasetId"], item["symbol"], item["provider"],
                        item["provider_symbol"], item["timeframe"], item["asset_type"], item["path"],
                        item["file_hash"], item["row_count"], item.get("first_timestamp"), item.get("last_timestamp"),
                        item["gap_count"], item["duplicate_count"], item["quality_state"], canonical_json(item),
                    )
                    for item in series
                ],
            )
            connection.commit()

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT manifest_json FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_datasets(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT manifest_json FROM datasets ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_comparison(self, comparison_id: str, run_ids: list[str], compatibility_hash: str, result: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO comparisons(comparison_id,created_at,run_ids_json,compatibility_hash,result_json) VALUES(?,?,?,?,?)",
                (comparison_id, utc_now_iso(), canonical_json(run_ids), compatibility_hash, canonical_json(result)),
            )
            connection.commit()

    def list_comparisons(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM comparisons ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "comparisonId": row["comparison_id"],
                "createdAt": row["created_at"],
                "runIds": json.loads(row["run_ids_json"]),
                "compatibilityHash": row["compatibility_hash"],
                "result": json.loads(row["result_json"]),
            }
            for row in rows
        ]
