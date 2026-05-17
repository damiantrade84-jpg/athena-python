from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import Flask

from athena_app.api.routes_audit import register_audit_routes


ARTIFACT_ROOT = Path(__file__).resolve().parent / "_artifacts" / "route_audit"


def _db_path():
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_ROOT / f"{uuid4().hex}.db"


def _create_db(path):
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                pair TEXT,
                engine TEXT
            )
            """
        )
        con.executemany(
            "INSERT INTO audit_log (id, pair, engine) VALUES (?, ?, ?)",
            [
                (1, "EURUSD", "engine_a"),
                (2, "BTCUSDT", "engine_b"),
                (3, "XAUUSD", "scalp"),
            ],
        )


def _client(db_path):
    app = Flask(__name__)
    register_audit_routes(app, SimpleNamespace(AUDIT_DB=str(db_path)))
    return app.test_client(), app


def test_audit_route_registers_get_method():
    client, app = _client(_db_path())

    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert "GET" in methods_by_path["/api/audit"]
    assert client is not None


def test_audit_route_returns_latest_rows_with_limit():
    db_path = _db_path()
    _create_db(db_path)
    client, _app = _client(db_path)

    resp = client.get("/api/audit?limit=2")

    assert resp.status_code == 200
    rows = resp.get_json()
    assert [row["id"] for row in rows] == [3, 2]
    assert rows[0]["pair"] == "XAUUSD"
