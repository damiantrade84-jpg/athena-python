"""Tests for SQLite contention instrumentation."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite_instrumentation as si


class _FakeConnection:
    def execute(self, *_args, **_kwargs):
        return object()

    def executemany(self, *_args, **_kwargs):
        return object()

    def commit(self):
        return None


class _LockedConnection:
    def execute(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")


def test_slow_connect_is_logged(monkeypatch, caplog):
    times = iter([1.0, 1.4])
    monkeypatch.setenv("ATHENA_SQLITE_SLOW_CONNECT_MS", "100")
    monkeypatch.setattr(si.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(si.sqlite3, "connect", lambda *_a, **_kw: _FakeConnection())

    with caplog.at_level(logging.WARNING, logger="sentinel.sqlite"):
        con = si.timed_sqlite_connect("audit.db", label="test.connect")

    assert isinstance(con, _FakeConnection)
    assert any("[SQLITE_SLOW]" in rec.message and "test.connect" in rec.message for rec in caplog.records)


def test_slow_write_is_logged(monkeypatch, caplog):
    times = iter([1.0, 1.8])
    monkeypatch.setenv("ATHENA_SQLITE_SLOW_WRITE_MS", "100")
    monkeypatch.setattr(si.time, "perf_counter", lambda: next(times))

    with caplog.at_level(logging.WARNING, logger="sentinel.sqlite"):
        si.timed_sqlite_execute_write(_FakeConnection(), "INSERT INTO audit_log VALUES (?)", (1,), label="test.write")

    assert any("[SQLITE_SLOW]" in rec.message and "test.write" in rec.message for rec in caplog.records)


def test_database_locked_write_is_logged(caplog):
    with caplog.at_level(logging.ERROR, logger="sentinel.sqlite"):
        with pytest.raises(sqlite3.OperationalError):
            si.timed_sqlite_execute_write(
                _LockedConnection(),
                "INSERT INTO audit_log VALUES (?)",
                (1,),
                label="test.locked",
            )

    assert any("[SQLITE_LOCKED]" in rec.message and "test.locked" in rec.message for rec in caplog.records)


def test_single_writer_queue_insertion_point_is_reported():
    assert "single-writer queue" in si.SINGLE_WRITER_QUEUE_INSERTION_POINT
    assert "timed_sqlite_execute_write" in si.SINGLE_WRITER_QUEUE_INSERTION_POINT


def test_single_writer_queue_serializes_audit_db_writes(monkeypatch, tmp_path):
    db_path = tmp_path / "audit.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE audit_log(id INTEGER PRIMARY KEY, label TEXT)")
    con.commit()
    con.close()

    monkeypatch.setenv("ATHENA_SQLITE_SINGLE_WRITER_ENABLED", "true")
    queued_con = si.timed_sqlite_connect(db_path, label="test.queue.connect")
    try:
        si.timed_sqlite_execute_write(
            queued_con,
            "INSERT INTO audit_log(label) VALUES (?)",
            ("queued",),
            label="test.queue.insert",
        )
        si.timed_sqlite_commit(queued_con, label="test.queue.commit")
    finally:
        queued_con.close()

    verify = sqlite3.connect(db_path)
    try:
        rows = verify.execute("SELECT label FROM audit_log").fetchall()
    finally:
        verify.close()

    assert rows == [("queued",)]
