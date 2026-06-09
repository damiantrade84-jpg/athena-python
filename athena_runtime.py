"""Runtime bindings populated once `athena.py` has finished defining helpers.

Split modules (e.g. execution routes) read from here at request time so we avoid
import cycles with the monolith.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

# Shared with /api/webhook duplicate guard and /api/execute dedup.
executed_signals: set[str] = set()

_DEDUP_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
_DEDUP_TTL_SEC = 24 * 3600
_dedup_lock = threading.Lock()
_pending_signals: set[str] = set()
_db_ready = False

_rt: Any | None = None


def set_runtime(deps: Any) -> None:
    global _rt
    _rt = deps


def rt() -> Any:
    if _rt is None:
        raise RuntimeError("athena runtime not initialized (set_runtime not called)")
    return _rt


def _ensure_dedup_db() -> None:
    global _db_ready
    if _db_ready:
        return
    with sqlite3.connect(_DEDUP_DB, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS executed_signal_keys (
                sig_id      TEXT PRIMARY KEY,
                recorded_at REAL NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_executed_signal_keys_recorded "
            "ON executed_signal_keys (recorded_at)"
        )
        con.commit()
    _db_ready = True


def _purge_expired_locked(now: float | None = None) -> None:
    _ensure_dedup_db()
    ts = now if now is not None else time.time()
    cutoff = ts - _DEDUP_TTL_SEC
    with sqlite3.connect(_DEDUP_DB, timeout=15.0) as con:
        con.execute("DELETE FROM executed_signal_keys WHERE recorded_at < ?", (cutoff,))
        con.commit()


def hydrate_executed_signals_from_db() -> int:
    """Load recent dedup keys from SQLite into the in-memory set (startup)."""
    with _dedup_lock:
        _purge_expired_locked()
        _ensure_dedup_db()
        cutoff = time.time() - _DEDUP_TTL_SEC
        with sqlite3.connect(_DEDUP_DB, timeout=15.0) as con:
            rows = con.execute(
                "SELECT sig_id FROM executed_signal_keys WHERE recorded_at >= ?",
                (cutoff,),
            ).fetchall()
        for (sig_id,) in rows:
            executed_signals.add(sig_id)
        return len(rows)


def _db_has_signal_locked(sig_id: str) -> bool:
    _ensure_dedup_db()
    with sqlite3.connect(_DEDUP_DB, timeout=15.0) as con:
        row = con.execute(
            "SELECT 1 FROM executed_signal_keys WHERE sig_id=? LIMIT 1", (sig_id,)
        ).fetchone()
    return row is not None


def _persist_signal_locked(sig_id: str) -> None:
    _ensure_dedup_db()
    with sqlite3.connect(_DEDUP_DB, timeout=15.0) as con:
        con.execute(
            "INSERT OR REPLACE INTO executed_signal_keys (sig_id, recorded_at) VALUES (?,?)",
            (sig_id, time.time()),
        )
        con.commit()


def is_signal_duplicate(sig_id: str) -> bool:
    with _dedup_lock:
        _purge_expired_locked()
        return (
            sig_id in executed_signals
            or sig_id in _pending_signals
            or _db_has_signal_locked(sig_id)
        )


def claim_webhook_signal(sig_id: str) -> bool:
    """Atomically claim a webhook minute-bucket key before execution work."""
    with _dedup_lock:
        _purge_expired_locked()
        if (
            sig_id in executed_signals
            or sig_id in _pending_signals
            or _db_has_signal_locked(sig_id)
        ):
            return False
        executed_signals.add(sig_id)
        _persist_signal_locked(sig_id)
        return True


def begin_signal_execution(sig_id: str) -> bool:
    """Reserve an execution slot; abort on failure via abort_signal_execution."""
    with _dedup_lock:
        _purge_expired_locked()
        if sig_id in executed_signals or _db_has_signal_locked(sig_id):
            return False
        if sig_id in _pending_signals:
            return False
        _pending_signals.add(sig_id)
        return True


def complete_signal_execution(sig_id: str) -> None:
    with _dedup_lock:
        _pending_signals.discard(sig_id)
        executed_signals.add(sig_id)
        _persist_signal_locked(sig_id)


def abort_signal_execution(sig_id: str) -> None:
    with _dedup_lock:
        _pending_signals.discard(sig_id)


def mark_signal_executed(sig_id: str) -> None:
    """Backward-compatible mark after successful fill."""
    complete_signal_execution(sig_id)
