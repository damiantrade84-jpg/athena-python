"""SQLite persistence and idempotency ledger for MUSE."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any, Iterator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class MuseRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_lock = threading.Lock()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=15.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def migrate(self) -> None:
        with self._migration_lock, self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS muse_scans (
                    scan_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS muse_signals (
                    signal_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    score REAL NOT NULL,
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(scan_id) REFERENCES muse_scans(scan_id)
                );
                CREATE INDEX IF NOT EXISTS idx_muse_signals_scan_score
                    ON muse_signals(scan_id, decision, score DESC);

                CREATE TABLE IF NOT EXISTS muse_executions (
                    execution_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(signal_id) REFERENCES muse_signals(signal_id)
                );
                CREATE INDEX IF NOT EXISTS idx_muse_executions_signal
                    ON muse_executions(signal_id, requested_at DESC);
                """
            )

    def create_scan(self, request_payload: dict[str, Any]) -> str:
        scan_id = "musescan_" + uuid.uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO muse_scans(scan_id,started_at,status,request_json,summary_json) VALUES(?,?,?,?,?)",
                (scan_id, _now(), "RUNNING", _json(request_payload), "{}"),
            )
        return scan_id

    def complete_scan(self, scan_id: str, status: str, summary: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE muse_scans SET completed_at=?, status=?, summary_json=? WHERE scan_id=?",
                (_now(), status, _json(summary), scan_id),
            )

    def upsert_signal(self, scan_id: str, signal: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT INTO muse_signals(signal_id,scan_id,pair,asset_type,decision,score,generated_at,payload_json)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(signal_id) DO UPDATE SET scan_id=excluded.scan_id, decision=excluded.decision,
                   score=excluded.score, payload_json=excluded.payload_json""",
                (signal["signalId"], scan_id, signal.get("pair"), signal.get("assetType"),
                 signal.get("decision"), float(signal.get("score") or 0.0),
                 signal.get("generatedAt"), _json(signal)),
            )

    def latest_scan(self) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM muse_scans ORDER BY started_at DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def list_signals(self, *, decisions: set[str] | None = None,
                     asset_types: set[str] | None = None, limit: int = 250) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if decisions:
            marks = ",".join("?" for _ in decisions)
            clauses.append(f"decision IN ({marks})")
            params.extend(sorted(decisions))
        if asset_types:
            marks = ",".join("?" for _ in asset_types)
            clauses.append(f"asset_type IN ({marks})")
            params.extend(sorted(asset_types))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as con:
            rows = con.execute(
                f"SELECT payload_json FROM muse_signals {where} ORDER BY score DESC LIMIT ?",
                (*params, max(1, min(1000, int(limit)))),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json FROM muse_signals WHERE signal_id=?",
                              (signal_id,)).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def claim_execution(self, *, signal_id: str, idempotency_key: str, mode: str,
                        venue: str, request: dict[str, Any]) -> tuple[str, bool]:
        execution_id = "museexec_" + uuid.uuid4().hex
        with self._connect() as con:
            existing = con.execute("SELECT execution_id, result_json FROM muse_executions WHERE idempotency_key=?",
                                   (idempotency_key,)).fetchone()
            if existing:
                return str(existing["execution_id"]), True
            con.execute(
                """INSERT INTO muse_executions(execution_id,signal_id,idempotency_key,mode,venue,
                       status,requested_at,request_json,result_json)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (execution_id, signal_id, idempotency_key, mode, venue, "PENDING",
                 _now(), _json(request), "{}"),
            )
            return execution_id, False

    def finish_execution(self, execution_id: str, status: str, result: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute("UPDATE muse_executions SET status=?, completed_at=?, result_json=? WHERE execution_id=?",
                        (status, _now(), _json(result), execution_id))

    def execution_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT execution_id, signal_id, mode, venue, status, requested_at, completed_at, result_json"
                " FROM muse_executions ORDER BY requested_at DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
            out = []
            for row in rows:
                payload = dict(row)
                try:
                    payload["result"] = json.loads(row["result_json"])
                except (TypeError, ValueError):
                    payload["result"] = {}
                payload.pop("result_json", None)
                out.append(payload)
            return out
