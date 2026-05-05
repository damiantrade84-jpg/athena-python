"""Lightweight SQLite contention instrumentation.

This is observability only. It is not a database abstraction layer and does not
serialize writes. A future single-writer queue can be inserted at the write
helper functions in this module.
"""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
from typing import Any

log = logging.getLogger("sentinel.sqlite")

SINGLE_WRITER_QUEUE_INSERTION_POINT = (
    "Insert a single-writer queue inside timed_sqlite_execute_write(), "
    "timed_sqlite_executemany_write(), and timed_sqlite_commit(), keeping "
    "call sites unchanged while one worker owns the audit.db write connection."
)

_CONNECTION_DB_PATHS: dict[int, str] = {}
_WRITERS: dict[str, "_SQLiteSingleWriter"] = {}
_WRITERS_LOCK = threading.Lock()


class _QueuedCursor:
    def __init__(self, rowcount: int | None = None, lastrowid: int | None = None):
        self.rowcount = rowcount if rowcount is not None else -1
        self.lastrowid = lastrowid


class _WriteRequest:
    def __init__(self, kind: str, sql: str | None = None, params: Any = None, label: str = ""):
        self.kind = kind
        self.sql = sql
        self.params = params
        self.label = label
        self.done = threading.Event()
        self.error: BaseException | None = None
        self.result: _QueuedCursor | None = None


class _SQLiteSingleWriter:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.requests: "queue.Queue[_WriteRequest | None]" = queue.Queue()
        self.thread = threading.Thread(
            target=self._run,
            name=f"sqlite-writer-{_db_label(db_path)}",
            daemon=True,
        )
        self.thread.start()

    def submit(self, request: _WriteRequest) -> _QueuedCursor:
        self.requests.put(request)
        request.done.wait()
        if request.error is not None:
            raise request.error
        return request.result or _QueuedCursor()

    def _run(self) -> None:
        con = sqlite3.connect(self.db_path, timeout=15.0)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            while True:
                request = self.requests.get()
                if request is None:
                    break
                start = time.perf_counter()
                try:
                    if request.kind == "execute":
                        cur = con.execute(request.sql or "", request.params or ())
                    elif request.kind == "executemany":
                        cur = con.executemany(request.sql or "", request.params or [])
                    elif request.kind == "commit":
                        con.commit()
                        cur = None
                    else:
                        raise ValueError(f"unknown sqlite writer request: {request.kind}")
                    if request.kind in {"execute", "executemany"}:
                        con.commit()
                    elapsed = _elapsed_ms(start)
                    threshold = _threshold_ms("ATHENA_SQLITE_SLOW_WRITE_MS", 500.0)
                    if elapsed >= threshold:
                        log.warning(
                            "[SQLITE_SLOW] label=%s phase=single_writer_%s db=%s elapsed_ms=%.1f threshold_ms=%.1f",
                            request.label,
                            request.kind,
                            _db_label(self.db_path),
                            elapsed,
                            threshold,
                        )
                    request.result = _QueuedCursor(
                        rowcount=getattr(cur, "rowcount", -1) if cur is not None else -1,
                        lastrowid=getattr(cur, "lastrowid", None) if cur is not None else None,
                    )
                except sqlite3.OperationalError as exc:
                    elapsed = _elapsed_ms(start)
                    if _is_locked_error(exc):
                        log.error(
                            "[SQLITE_LOCKED] label=%s phase=single_writer_%s db=%s elapsed_ms=%.1f error=%s",
                            request.label,
                            request.kind,
                            _db_label(self.db_path),
                            elapsed,
                            exc,
                        )
                    request.error = exc
                except BaseException as exc:
                    request.error = exc
                finally:
                    request.done.set()
                    self.requests.task_done()
        finally:
            con.close()


def _threshold_ms(env_key: str, default: float) -> float:
    try:
        return float(os.environ.get(env_key, default))
    except (TypeError, ValueError):
        return float(default)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _is_locked_error(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower() or "database table is locked" in str(exc).lower()


def _db_label(db_path: str | os.PathLike[str]) -> str:
    try:
        return os.path.basename(os.fspath(db_path)) or os.fspath(db_path)
    except TypeError:
        return str(db_path)


def _single_writer_enabled(db_path: str | os.PathLike[str]) -> bool:
    enabled = str(os.environ.get("ATHENA_SQLITE_SINGLE_WRITER_ENABLED", "")).lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    allow_any = str(os.environ.get("ATHENA_SQLITE_SINGLE_WRITER_ANY_DB", "")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return allow_any or _db_label(db_path) == "audit.db"


def _get_writer(db_path: str) -> _SQLiteSingleWriter:
    resolved = os.path.abspath(os.fspath(db_path))
    with _WRITERS_LOCK:
        writer = _WRITERS.get(resolved)
        if writer is None:
            writer = _SQLiteSingleWriter(resolved)
            _WRITERS[resolved] = writer
        return writer


def _writer_for_connection(con: sqlite3.Connection) -> _SQLiteSingleWriter | None:
    db_path = _CONNECTION_DB_PATHS.get(id(con))
    if not db_path or not _single_writer_enabled(db_path):
        return None
    return _get_writer(db_path)


def timed_sqlite_connect(
    db_path: str | os.PathLike[str],
    *,
    timeout: float = 15.0,
    label: str = "sqlite.connect",
    **kwargs: Any,
) -> sqlite3.Connection:
    """Open sqlite connection and log slow acquisition or lock failures."""
    start = time.perf_counter()
    try:
        con = sqlite3.connect(db_path, timeout=timeout, **kwargs)
    except sqlite3.OperationalError as exc:
        elapsed = _elapsed_ms(start)
        if _is_locked_error(exc):
            log.error(
                "[SQLITE_LOCKED] label=%s phase=connect db=%s elapsed_ms=%.1f error=%s",
                label,
                _db_label(db_path),
                elapsed,
                exc,
            )
        raise
    elapsed = _elapsed_ms(start)
    _CONNECTION_DB_PATHS[id(con)] = os.fspath(db_path)
    threshold = _threshold_ms("ATHENA_SQLITE_SLOW_CONNECT_MS", 250.0)
    if elapsed >= threshold:
        log.warning(
            "[SQLITE_SLOW] label=%s phase=connect db=%s elapsed_ms=%.1f threshold_ms=%.1f",
            label,
            _db_label(db_path),
            elapsed,
            threshold,
        )
    return con


def timed_sqlite_execute_write(
    con: sqlite3.Connection,
    sql: str,
    params: tuple | list = (),
    *,
    label: str = "sqlite.execute_write",
):
    """Run one write statement and log slow writes or lock failures."""
    writer = _writer_for_connection(con)
    if writer is not None:
        return writer.submit(_WriteRequest("execute", sql, params, label))

    start = time.perf_counter()
    try:
        cur = con.execute(sql, params)
    except sqlite3.OperationalError as exc:
        elapsed = _elapsed_ms(start)
        if _is_locked_error(exc):
            log.error(
                "[SQLITE_LOCKED] label=%s phase=execute elapsed_ms=%.1f error=%s",
                label,
                elapsed,
                exc,
            )
        raise
    elapsed = _elapsed_ms(start)
    threshold = _threshold_ms("ATHENA_SQLITE_SLOW_WRITE_MS", 500.0)
    if elapsed >= threshold:
        log.warning(
            "[SQLITE_SLOW] label=%s phase=execute elapsed_ms=%.1f threshold_ms=%.1f",
            label,
            elapsed,
            threshold,
        )
    return cur


def timed_sqlite_executemany_write(
    con: sqlite3.Connection,
    sql: str,
    rows,
    *,
    label: str = "sqlite.executemany_write",
):
    """Run a batched write and log slow writes or lock failures."""
    writer = _writer_for_connection(con)
    if writer is not None:
        return writer.submit(_WriteRequest("executemany", sql, rows, label))

    start = time.perf_counter()
    try:
        cur = con.executemany(sql, rows)
    except sqlite3.OperationalError as exc:
        elapsed = _elapsed_ms(start)
        if _is_locked_error(exc):
            log.error(
                "[SQLITE_LOCKED] label=%s phase=executemany elapsed_ms=%.1f error=%s",
                label,
                elapsed,
                exc,
            )
        raise
    elapsed = _elapsed_ms(start)
    threshold = _threshold_ms("ATHENA_SQLITE_SLOW_WRITE_MS", 500.0)
    if elapsed >= threshold:
        log.warning(
            "[SQLITE_SLOW] label=%s phase=executemany elapsed_ms=%.1f threshold_ms=%.1f",
            label,
            elapsed,
            threshold,
        )
    return cur


def timed_sqlite_commit(
    con: sqlite3.Connection,
    *,
    label: str = "sqlite.commit",
) -> None:
    """Commit and log slow commit or lock failures."""
    writer = _writer_for_connection(con)
    if writer is not None:
        writer.submit(_WriteRequest("commit", label=label))
        return None

    start = time.perf_counter()
    try:
        con.commit()
    except sqlite3.OperationalError as exc:
        elapsed = _elapsed_ms(start)
        if _is_locked_error(exc):
            log.error(
                "[SQLITE_LOCKED] label=%s phase=commit elapsed_ms=%.1f error=%s",
                label,
                elapsed,
                exc,
            )
        raise
    elapsed = _elapsed_ms(start)
    threshold = _threshold_ms("ATHENA_SQLITE_SLOW_WRITE_MS", 500.0)
    if elapsed >= threshold:
        log.warning(
            "[SQLITE_SLOW] label=%s phase=commit elapsed_ms=%.1f threshold_ms=%.1f",
            label,
            elapsed,
            threshold,
        )
