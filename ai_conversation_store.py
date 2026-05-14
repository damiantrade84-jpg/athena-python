"""Small, best-effort SQLite store for AI Agent chat turns.

The store is advisory/audit metadata only. Failures are non-fatal because chat
must not block ATHENA scanning or review flows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> str:
    return os.environ.get("ATHENA_AUDIT_DB") or os.path.join(os.path.dirname(__file__), "audit.db")


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return "null"


@contextmanager
def _connect(db_path: str | None = None):
    path = db_path or _default_db_path()
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    con = sqlite3.connect(path, timeout=15.0)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        yield con
        con.commit()
    finally:
        con.close()


def init_store(db_path: str | None = None) -> bool:
    try:
        with _connect(db_path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS ai_conversations (
                    thread_id TEXT PRIMARY KEY,
                    pair TEXT,
                    trace_id TEXT,
                    created_ts TEXT,
                    last_ts TEXT,
                    title TEXT NULL,
                    metadata_json TEXT NULL
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS ai_conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT,
                    role TEXT,
                    content TEXT,
                    tool_calls TEXT,
                    facts_used TEXT,
                    decision TEXT,
                    ts TEXT
                )"""
            )
            _ensure_columns(con, "ai_conversations", {"title": "TEXT NULL", "metadata_json": "TEXT NULL"})
            _ensure_columns(
                con,
                "ai_conversation_turns",
                {"facts_used": "TEXT", "decision": "TEXT"},
            )
        return True
    except Exception:
        return False


def _ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, decl in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def create_or_get_thread(
    session_id: str | None,
    symbol: str | None,
    trace_id: str | None,
    db_path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    thread_id = str(session_id or "").strip() or f"ai-chat-{uuid.uuid4().hex}"
    try:
        init_store(db_path)
        ts = _now()
        with _connect(db_path) as con:
            con.execute(
                """INSERT INTO ai_conversations(thread_id, pair, trace_id, created_ts, last_ts, metadata_json)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                       pair=COALESCE(excluded.pair, ai_conversations.pair),
                       trace_id=COALESCE(excluded.trace_id, ai_conversations.trace_id),
                       last_ts=excluded.last_ts,
                       metadata_json=COALESCE(excluded.metadata_json, ai_conversations.metadata_json)""",
                (thread_id, symbol, trace_id, ts, ts, _safe_json(metadata) if metadata else None),
            )
    except Exception:
        pass
    return thread_id


def append_turn(
    thread_id: str,
    role: str,
    content: str,
    tool_calls: Any = None,
    facts_used: Any = None,
    decision: str | None = None,
    db_path: str | None = None,
) -> bool:
    try:
        init_store(db_path)
        ts = _now()
        with _connect(db_path) as con:
            con.execute(
                """INSERT INTO ai_conversation_turns(thread_id, role, content, tool_calls, facts_used, decision, ts)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    str(thread_id),
                    str(role),
                    str(content or ""),
                    _safe_json(tool_calls or []),
                    _safe_json(facts_used or []),
                    decision,
                    ts,
                ),
            )
            con.execute("UPDATE ai_conversations SET last_ts=? WHERE thread_id=?", (ts, str(thread_id)))
        return True
    except Exception:
        return False


def get_recent_turns(thread_id: str, limit: int = 12, db_path: str | None = None) -> list[dict[str, Any]]:
    try:
        init_store(db_path)
        with _connect(db_path) as con:
            rows = con.execute(
                """SELECT id, thread_id, role, content, tool_calls, facts_used, decision, ts
                   FROM ai_conversation_turns
                   WHERE thread_id=?
                   ORDER BY id DESC
                   LIMIT ?""",
                (str(thread_id), int(limit)),
            ).fetchall()
        out = [dict(row) for row in reversed(rows)]
        for row in out:
            for key in ("tool_calls", "facts_used"):
                try:
                    row[key] = json.loads(row.get(key) or "[]")
                except Exception:
                    row[key] = []
        return out
    except Exception:
        return []


def list_threads(symbol: str | None = None, limit: int = 50, db_path: str | None = None) -> list[dict[str, Any]]:
    try:
        init_store(db_path)
        with _connect(db_path) as con:
            if symbol:
                rows = con.execute(
                    """SELECT thread_id, pair, trace_id, created_ts, last_ts, title, metadata_json
                       FROM ai_conversations
                       WHERE pair=?
                       ORDER BY last_ts DESC
                       LIMIT ?""",
                    (symbol, int(limit)),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT thread_id, pair, trace_id, created_ts, last_ts, title, metadata_json
                       FROM ai_conversations
                       ORDER BY last_ts DESC
                       LIMIT ?""",
                    (int(limit),),
                ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def set_thread_title(thread_id: str, title: str, db_path: str | None = None) -> bool:
    try:
        init_store(db_path)
        with _connect(db_path) as con:
            con.execute(
                "UPDATE ai_conversations SET title=?, last_ts=? WHERE thread_id=?",
                (str(title or "")[:160], _now(), str(thread_id)),
            )
        return True
    except Exception:
        return False
