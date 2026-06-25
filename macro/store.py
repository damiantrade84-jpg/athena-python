"""Idempotent local macro-event store (SQLite).

Mirrors the carry_feed SQLite conventions (WAL, short timeouts, repo-root db).
Re-running any ingestion updates existing rows in place — it never duplicates.
Safe to import without a configured app; failures are surfaced to the caller,
never swallowed silently here (callers log degraded mode).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

# ── Controlled vocabularies ─────────────────────────────────────────────────────
EVENT_TYPES = (
    "FOMC_RATE_DECISION",
    "FOMC_STATEMENT",
    "FOMC_PRESS_CONFERENCE",
    "FOMC_MINUTES",
    "FED_SPEECH",
)
STATUSES = ("SCHEDULED", "ACTIVE", "RELEASED", "COMPLETED", "CANCELLED", "ERROR")

# Status precedence: a re-ingest must not regress a more-advanced lifecycle state.
_STATUS_ORDER = {"SCHEDULED": 1, "ACTIVE": 2, "RELEASED": 3, "COMPLETED": 4}
# Explicit terminal/override states always win when supplied by a writer.
_STATUS_OVERRIDE = {"CANCELLED", "ERROR"}

_COLUMNS = (
    "id",
    "source",
    "event_type",
    "country",
    "currency",
    "title",
    "scheduled_time_utc",
    "press_conference_time_utc",
    "importance",
    "status",
    "raw_url",
    "raw_text",
    "previous_lower",
    "previous_upper",
    "actual_lower",
    "actual_upper",
    "surprise_bps",
    "time_source",
    "created_at",
    "updated_at",
)
# Fields that re-ingest should fill but never overwrite to NULL once populated.
_COALESCE_FIELDS = (
    "press_conference_time_utc",
    "raw_text",
    "previous_lower",
    "previous_upper",
    "actual_lower",
    "actual_upper",
    "surprise_bps",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> str:
    env = os.environ.get("ATHENA_MACRO_DB_PATH", "").strip()
    if env:
        return os.path.expanduser(env)
    # repo root (parent of macro/), beside carry_cache.db / cot.db
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "macro_events.db")


def _status_merge(existing: Optional[str], incoming: Optional[str]) -> str:
    inc = (incoming or "").upper()
    cur = (existing or "").upper()
    if inc in _STATUS_OVERRIDE:
        return inc
    if cur in _STATUS_OVERRIDE:
        return cur
    if _STATUS_ORDER.get(inc, 0) >= _STATUS_ORDER.get(cur, 0):
        return inc or cur or "SCHEDULED"
    return cur or inc or "SCHEDULED"


class MacroEventStore:
    """Thread-safe idempotent store for macro events."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _default_db_path()
        self._lock = threading.Lock()
        self._init_db()

    # ── schema ──
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15.0)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS macro_events (
                        id                          TEXT PRIMARY KEY,
                        source                      TEXT,
                        event_type                  TEXT,
                        country                     TEXT,
                        currency                    TEXT,
                        title                       TEXT,
                        scheduled_time_utc          TEXT,
                        press_conference_time_utc   TEXT,
                        importance                  TEXT,
                        status                      TEXT,
                        raw_url                     TEXT,
                        raw_text                    TEXT,
                        previous_lower              REAL,
                        previous_upper              REAL,
                        actual_lower                REAL,
                        actual_upper                REAL,
                        surprise_bps                REAL,
                        time_source                 TEXT,
                        created_at                  TEXT,
                        updated_at                  TEXT
                    )
                    """
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_macro_sched "
                    "ON macro_events (scheduled_time_utc)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_macro_type "
                    "ON macro_events (event_type, scheduled_time_utc)"
                )
                con.commit()
            finally:
                con.close()

    # ── write ──
    def upsert_event(self, event: dict[str, Any]) -> str:
        """Insert or merge one event by ``id``. Returns 'inserted' | 'updated'.

        Idempotent: created_at is preserved; nullable enrichment fields are
        COALESCE-merged (incoming None never wipes an existing value); status
        never regresses to an earlier lifecycle stage.
        """
        eid = str(event.get("id") or "").strip()
        if not eid:
            raise ValueError("macro event requires a non-empty 'id'")
        now = _now_iso()
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT * FROM macro_events WHERE id=?", (eid,)
                ).fetchone()
                existing = dict(row) if row is not None else None
                merged: dict[str, Any] = {}
                for col in _COLUMNS:
                    incoming = event.get(col)
                    if existing is None:
                        merged[col] = incoming
                    elif col == "created_at":
                        merged[col] = existing.get("created_at") or now
                    elif col == "status":
                        merged[col] = _status_merge(
                            existing.get("status"), event.get("status")
                        )
                    elif col in _COALESCE_FIELDS:
                        merged[col] = incoming if incoming is not None else existing.get(col)
                    else:
                        merged[col] = incoming if incoming is not None else existing.get(col)
                merged["id"] = eid
                if existing is None and not merged.get("created_at"):
                    merged["created_at"] = now
                merged["updated_at"] = now
                placeholders = ",".join(["?"] * len(_COLUMNS))
                con.execute(
                    f"INSERT OR REPLACE INTO macro_events ({','.join(_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    tuple(merged.get(col) for col in _COLUMNS),
                )
                con.commit()
                return "updated" if existing is not None else "inserted"
            finally:
                con.close()

    def upsert_events(self, events: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"inserted": 0, "updated": 0}
        for ev in events:
            try:
                counts[self.upsert_event(ev)] += 1
            except Exception:
                # Per-event failure must not abort a batch ingest.
                counts.setdefault("errors", 0)
                counts["errors"] += 1
        return counts

    def mark_status(self, event_id: str, status: str) -> bool:
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    "UPDATE macro_events SET status=?, updated_at=? WHERE id=?",
                    (status, _now_iso(), event_id),
                )
                con.commit()
                return cur.rowcount > 0
            finally:
                con.close()

    def mark_scheduled_released(
        self,
        scheduled_date_iso: str,
        *,
        actual_lower: float | None = None,
        actual_upper: float | None = None,
        raw_url: str | None = None,
        raw_text: str | None = None,
    ) -> int:
        """Flip a same-day SCHEDULED FOMC_RATE_DECISION to RELEASED (RSS confirmation).

        ``scheduled_date_iso`` is a YYYY-MM-DD (UTC) day key. Best-effort; returns
        the number of rows updated.
        """
        day = (scheduled_date_iso or "")[:10]
        if not day:
            return 0
        updated = 0
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT id, status FROM macro_events "
                    "WHERE event_type='FOMC_RATE_DECISION' "
                    "AND substr(scheduled_time_utc,1,10)=?",
                    (day,),
                ).fetchall()
                for r in rows:
                    new_status = _status_merge(r["status"], "RELEASED")
                    con.execute(
                        "UPDATE macro_events SET status=?, "
                        "actual_lower=COALESCE(?, actual_lower), "
                        "actual_upper=COALESCE(?, actual_upper), "
                        "raw_url=COALESCE(?, raw_url), "
                        "raw_text=COALESCE(?, raw_text), "
                        "updated_at=? WHERE id=?",
                        (
                            new_status,
                            actual_lower,
                            actual_upper,
                            raw_url,
                            raw_text,
                            _now_iso(),
                            r["id"],
                        ),
                    )
                    updated += 1
                con.commit()
            finally:
                con.close()
        return updated

    # ── read ──
    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT * FROM macro_events WHERE id=?", (event_id,)
                ).fetchone()
                return dict(row) if row is not None else None
            finally:
                con.close()

    def events_between(self, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        """Events whose scheduled_time_utc falls in [start_iso, end_iso] (inclusive)."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT * FROM macro_events "
                    "WHERE scheduled_time_utc IS NOT NULL "
                    "AND scheduled_time_utc >= ? AND scheduled_time_utc <= ? "
                    "ORDER BY scheduled_time_utc ASC",
                    (start_iso, end_iso),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                con.close()

    def list_events(
        self,
        *,
        event_types: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if event_types:
            clauses.append(f"event_type IN ({','.join('?' * len(event_types))})")
            params.extend(event_types)
        if statuses:
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if start_iso:
            clauses.append("scheduled_time_utc >= ?")
            params.append(start_iso)
        if end_iso:
            clauses.append("scheduled_time_utc <= ?")
            params.append(end_iso)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    f"SELECT * FROM macro_events{where} "
                    "ORDER BY scheduled_time_utc ASC LIMIT ?",
                    (*params, int(limit)),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                con.close()

    def count(self) -> int:
        with self._lock:
            con = self._connect()
            try:
                return int(con.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0])
            finally:
                con.close()


# ── process-wide default store (app path) ──────────────────────────────────────
_default_store: MacroEventStore | None = None
_default_lock = threading.Lock()


def default_store() -> MacroEventStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = MacroEventStore()
    return _default_store
