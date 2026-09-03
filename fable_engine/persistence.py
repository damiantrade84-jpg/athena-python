"""SQLite persistence and idempotency ledger for FABLE.

``fable_executions`` intentionally shares the coordinator-store shape used by
``engine_attribution`` (venue, requested_at, completed_at, request_json,
result_json, status) so broker fills opened by FABLE are attributed on the
Trades panel without writing an ``audit_log`` row.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any, Generator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class FableRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_lock = threading.Lock()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
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
                CREATE TABLE IF NOT EXISTS fable_scans (
                    scan_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS fable_signals (
                    signal_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    coherence REAL NOT NULL,
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(scan_id) REFERENCES fable_scans(scan_id)
                );
                CREATE INDEX IF NOT EXISTS idx_fable_signals_scan
                    ON fable_signals(scan_id, decision, coherence DESC);

                CREATE TABLE IF NOT EXISTS fable_executions (
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
                    FOREIGN KEY(signal_id) REFERENCES fable_signals(signal_id)
                );
                CREATE INDEX IF NOT EXISTS idx_fable_executions_signal
                    ON fable_executions(signal_id, requested_at DESC);
                """
            )

    # ── scans ───────────────────────────────────────────────────────────

    def create_scan(self, request_payload: dict[str, Any]) -> str:
        scan_id = "fablescan_" + uuid.uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO fable_scans(scan_id,started_at,status,request_json,summary_json) VALUES(?,?,?,?,?)",
                (scan_id, _now(), "RUNNING", _json(request_payload), "{}"),
            )
        return scan_id

    def complete_scan(self, scan_id: str, status: str, summary: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE fable_scans SET completed_at=?, status=?, summary_json=? WHERE scan_id=?",
                (_now(), status, _json(summary), scan_id),
            )

    def latest_scan(self) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM fable_scans ORDER BY started_at DESC LIMIT 1").fetchone()
        return self._scan_dict(row) if row else None

    def latest_completed_scan_id(self) -> str | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT scan_id FROM fable_scans WHERE status='COMPLETED' ORDER BY completed_at DESC LIMIT 1"
            ).fetchone()
        return str(row["scan_id"]) if row else None

    @staticmethod
    def _scan_dict(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["request"] = json.loads(payload.pop("request_json"))
        payload["summary"] = json.loads(payload.pop("summary_json"))
        return payload

    # ── signals ─────────────────────────────────────────────────────────

    def save_signals(self, scan_id: str, signals: list[dict[str, Any]]) -> None:
        if not signals:
            return
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO fable_signals(
                    signal_id,scan_id,pair,asset_type,decision,tier,coherence,generated_at,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    scan_id=excluded.scan_id,
                    pair=excluded.pair,
                    asset_type=excluded.asset_type,
                    decision=excluded.decision,
                    tier=excluded.tier,
                    coherence=excluded.coherence,
                    generated_at=excluded.generated_at,
                    payload_json=excluded.payload_json
                """,
                [
                    (
                        row["signalId"],
                        scan_id,
                        row["pair"],
                        row["assetType"],
                        row["decision"],
                        row["tier"],
                        float(row["coherence"]),
                        row["generatedAt"],
                        _json(row),
                    )
                    for row in signals
                ],
            )

    def list_signals(
        self,
        *,
        scan_id: str | None = None,
        decisions: set[str] | None = None,
        asset_types: set[str] | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        selected_scan = scan_id or self.latest_completed_scan_id()
        if not selected_scan:
            return []
        clauses = ["scan_id=?"]
        params: list[Any] = [selected_scan]
        if decisions:
            marks = ",".join("?" for _ in decisions)
            clauses.append(f"decision IN ({marks})")
            params.extend(sorted(decisions))
        if asset_types:
            marks = ",".join("?" for _ in asset_types)
            clauses.append(f"asset_type IN ({marks})")
            params.extend(sorted(asset_types))
        params.append(max(1, min(int(limit), 1000)))
        query = (
            "SELECT payload_json FROM fable_signals WHERE "
            + " AND ".join(clauses)
            + " ORDER BY CASE decision WHEN 'EXECUTE' THEN 0 WHEN 'STAGE' THEN 1 WHEN 'OBSERVE' THEN 2 ELSE 3 END,"
            " coherence DESC, pair ASC LIMIT ?"
        )
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json FROM fable_signals WHERE signal_id=?", (signal_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    # ── executions ──────────────────────────────────────────────────────

    def reserve_execution(
        self,
        *,
        signal_id: str,
        idempotency_key: str,
        mode: str,
        venue: str,
        request_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        execution_id = "fableexec_" + uuid.uuid4().hex
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing_key = con.execute(
                "SELECT * FROM fable_executions WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing_key:
                return self._execution_dict(existing_key), False
            active = con.execute(
                """
                SELECT * FROM fable_executions
                WHERE signal_id=? AND status IN ('PENDING','SUCCESS','FAILED')
                ORDER BY requested_at DESC LIMIT 1
                """,
                (signal_id,),
            ).fetchone()
            if active:
                return self._execution_dict(active), False
            now = _now()
            con.execute(
                """
                INSERT INTO fable_executions(
                    execution_id,signal_id,idempotency_key,mode,venue,status,requested_at,request_json,result_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (execution_id, signal_id, idempotency_key, mode, venue, "PENDING", now, _json(request_payload), "{}"),
            )
            row = con.execute("SELECT * FROM fable_executions WHERE execution_id=?", (execution_id,)).fetchone()
        return self._execution_dict(row), True

    def complete_execution(self, execution_id: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                "UPDATE fable_executions SET status=?, completed_at=?, result_json=? WHERE execution_id=?",
                (status, _now(), _json(result), execution_id),
            )
            row = con.execute("SELECT * FROM fable_executions WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            raise LookupError("execution_not_found")
        return self._execution_dict(row)

    def list_executions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM fable_executions ORDER BY requested_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._execution_dict(row) for row in rows]

    @staticmethod
    def _execution_dict(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["request"] = json.loads(payload.pop("request_json"))
        payload["result"] = json.loads(payload.pop("result_json"))
        return payload
