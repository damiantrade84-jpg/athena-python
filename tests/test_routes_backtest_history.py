from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import Flask

from athena_app.api.routes_backtest import register_backtest_history_routes


ARTIFACT_ROOT = Path(__file__).resolve().parent / "_artifacts" / "route_backtest_history"


def _db_path():
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_ROOT / f"{uuid4().hex}.db"


def _create_db(path):
    # History routes read the v3 rebuild's table; create it via the writer
    # module so the test schema can never drift from production.
    from athena_backtest.results import save_run

    for symbol, sqn in [
        ("EURUSD", 1.1),
        ("EURUSD", 2.2),
        ("BTCUSDT", 3.3),
    ]:
        save_run(
            {"sqn": sqn, "totalTrades": 1, "trades": []},
            pair={"display": symbol, "symbol": symbol},
            engine="A",
            style="intraday",
            db_path=str(path),
        )
    with sqlite3.connect(path) as con:
        # Pin the deterministic created_at ordering the assertions rely on.
        rows = con.execute("SELECT id FROM backtest_runs_v3 ORDER BY id").fetchall()
        stamps = ["2026-05-04T10:00:00Z", "2026-05-05T10:00:00Z", "2026-05-05T09:00:00Z"]
        for (row_id,), stamp in zip(rows, stamps):
            con.execute(
                "UPDATE backtest_runs_v3 SET created_at = ? WHERE id = ?", (stamp, row_id)
            )
        con.commit()


def _client(db_path):
    app = Flask(__name__)
    register_backtest_history_routes(app, SimpleNamespace(AUDIT_DB=str(db_path)))
    return app.test_client(), app


def test_backtest_history_routes_register_expected_methods():
    client, app = _client(_db_path())

    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert "GET" in methods_by_path["/api/backtest-history"]
    assert "GET" in methods_by_path["/api/backtest-history/<pair_name>"]
    assert "GET" in methods_by_path["/api/backtest-best"]
    assert client is not None


def test_backtest_history_returns_newest_rows_first():
    db_path = _db_path()
    _create_db(db_path)
    client, _app = _client(db_path)

    resp = client.get("/api/backtest-history")

    assert resp.status_code == 200
    rows = resp.get_json()
    assert [row["run_date"] for row in rows] == [
        "2026-05-05T10:00:00Z",
        "2026-05-05T09:00:00Z",
        "2026-05-04T10:00:00Z",
    ]


def test_backtest_history_pair_filters_exact_pair():
    db_path = _db_path()
    _create_db(db_path)
    client, _app = _client(db_path)

    resp = client.get("/api/backtest-history/BTCUSDT")

    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) == 1
    assert rows[0]["pair"] == "BTCUSDT"
    assert rows[0]["sqn"] == 3.3


def test_backtest_best_returns_latest_row_per_pair_ordered_by_sqn():
    db_path = _db_path()
    _create_db(db_path)
    client, _app = _client(db_path)

    resp = client.get("/api/backtest-best")

    assert resp.status_code == 200
    rows = resp.get_json()
    assert [row["pair"] for row in rows] == ["BTCUSDT", "EURUSD"]
    assert [row["sqn"] for row in rows] == [3.3, 2.2]
