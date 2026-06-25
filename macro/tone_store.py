"""Idempotent local store for FOMC tone scores (SQLite, ``macro_tone_scores`` table).

Lives in the same ``macro_events.db`` as the V1 event store and mirrors its conventions
(WAL, repo-root db, in-place merge keyed by ``id``). Re-scoring the same statement updates
the existing row — it never duplicates. Tone is CONTEXT only; this store grants nothing.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from macro.store import _default_db_path  # reuse the V1 db-path resolution

_COLUMNS = (
    "id",
    "macro_event_id",
    "source",
    "event_type",
    "country",
    "currency",
    "title",
    "statement_url",
    "statement_published_utc",
    "previous_statement_event_id",
    "previous_statement_url",
    "tone_label",
    "tone_score",
    "confidence",
    "rate_decision_score",
    "statement_language_score",
    "inflation_language_score",
    "labor_market_score",
    "growth_score",
    "balance_sheet_score",
    "forward_guidance_score",
    "dot_plot_score",
    "press_conference_score",
    "market_expectation_surprise_bps",
    "target_range_change_bps",
    "evidence_json",
    "warnings_json",
    "raw_text_hash",
    "previous_text_hash",
    "created_at",
    "updated_at",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MacroToneStore:
    """Thread-safe idempotent store for FOMC tone scores."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _default_db_path()
        self._lock = threading.Lock()
        self._init_db()

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
                    CREATE TABLE IF NOT EXISTS macro_tone_scores (
                        id                              TEXT PRIMARY KEY,
                        macro_event_id                  TEXT,
                        source                          TEXT,
                        event_type                      TEXT,
                        country                         TEXT,
                        currency                        TEXT,
                        title                           TEXT,
                        statement_url                   TEXT,
                        statement_published_utc         TEXT,
                        previous_statement_event_id     TEXT,
                        previous_statement_url          TEXT,
                        tone_label                      TEXT,
                        tone_score                      INTEGER,
                        confidence                      REAL,
                        rate_decision_score             INTEGER,
                        statement_language_score        INTEGER,
                        inflation_language_score        INTEGER,
                        labor_market_score              INTEGER,
                        growth_score                    INTEGER,
                        balance_sheet_score             INTEGER,
                        forward_guidance_score          INTEGER,
                        dot_plot_score                  INTEGER,
                        press_conference_score          INTEGER,
                        market_expectation_surprise_bps REAL,
                        target_range_change_bps         REAL,
                        evidence_json                   TEXT,
                        warnings_json                   TEXT,
                        raw_text_hash                   TEXT,
                        previous_text_hash              TEXT,
                        created_at                      TEXT,
                        updated_at                      TEXT
                    )
                    """
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tone_event "
                    "ON macro_tone_scores (macro_event_id)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tone_published "
                    "ON macro_tone_scores (statement_published_utc)"
                )
                con.commit()
            finally:
                con.close()

    def upsert_tone(self, record: dict[str, Any]) -> str:
        """Insert or update one tone row by ``id``. Returns 'inserted' | 'updated'.

        Idempotent: ``created_at`` is preserved across re-scores.
        """
        eid = str(record.get("id") or "").strip()
        if not eid:
            raise ValueError("tone record requires a non-empty 'id'")
        now = _now_iso()
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT created_at FROM macro_tone_scores WHERE id=?", (eid,)
                ).fetchone()
                existing = row is not None
                merged = {col: record.get(col) for col in _COLUMNS}
                merged["id"] = eid
                merged["created_at"] = (row["created_at"] if existing else None) or now
                merged["updated_at"] = now
                placeholders = ",".join(["?"] * len(_COLUMNS))
                con.execute(
                    f"INSERT OR REPLACE INTO macro_tone_scores ({','.join(_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    tuple(merged.get(col) for col in _COLUMNS),
                )
                con.commit()
                return "updated" if existing else "inserted"
            finally:
                con.close()

    def get_by_event(self, macro_event_id: str) -> dict[str, Any] | None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT * FROM macro_tone_scores WHERE macro_event_id=? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (macro_event_id,),
                ).fetchone()
                return dict(row) if row is not None else None
            finally:
                con.close()

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT * FROM macro_tone_scores "
                    "WHERE statement_published_utc IS NOT NULL "
                    "ORDER BY statement_published_utc DESC LIMIT 1"
                ).fetchone()
                return dict(row) if row is not None else None
            finally:
                con.close()

    def list_tones(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT * FROM macro_tone_scores "
                    "ORDER BY statement_published_utc DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                con.close()

    def count(self) -> int:
        with self._lock:
            con = self._connect()
            try:
                return int(con.execute("SELECT COUNT(*) FROM macro_tone_scores").fetchone()[0])
            finally:
                con.close()


_default_tone_store: MacroToneStore | None = None
_default_lock = threading.Lock()


def default_tone_store() -> MacroToneStore:
    global _default_tone_store
    if _default_tone_store is None:
        with _default_lock:
            if _default_tone_store is None:
                _default_tone_store = MacroToneStore()
    return _default_tone_store
