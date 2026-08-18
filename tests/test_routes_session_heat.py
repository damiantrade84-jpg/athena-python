"""Live Dashboard session-heat route + snapshot integration.

Asserts the advisory contract: the snapshot never blocks on the aggregate, the
existing snapshot shape is untouched when the feature is off or the data is
missing, and the dedicated endpoint degrades instead of 500-ing.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest
from flask import Flask

from athena_app.api.routes_live_dashboard import register_live_dashboard_routes
from athena_app.services import session_heat as sh


class _Log:
    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


PAIRS = [
    {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "source": "mt5"},
    {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "bybit"},
]


def _audit_db(with_tables: bool = True, trades: int = 60) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="ld_session_heat_")) / "audit.db"
    con = sqlite3.connect(str(tmp))
    if with_tables:
        con.execute(
            "CREATE TABLE backtest_runs_v3 (id INTEGER PRIMARY KEY, created_at TEXT, symbol TEXT, "
            "engine TEXT, style TEXT, contract_version TEXT)"
        )
        con.execute(
            "CREATE TABLE backtest_trades_v3 (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, "
            "entry_time TEXT, result_r REAL)"
        )
        con.execute(
            "INSERT INTO backtest_runs_v3 VALUES (1,'2026-01-01T00:00:00+00:00','EUR/USD','A','intraday','3.1.0')"
        )
        con.execute(
            "INSERT INTO backtest_runs_v3 VALUES (2,'2026-01-01T00:00:00+00:00','EUR/USD','B','intraday','3.1.0')"
        )
        for run_id in (1, 2):
            for hour in range(24):
                for i in range(trades):
                    con.execute(
                        "INSERT INTO backtest_trades_v3 (run_id, entry_time, result_r) VALUES (?,?,?)",
                        (run_id, f"2026-06-15T{hour:02d}:30:00+00:00", 1.0 if i % 2 else -0.6),
                    )
    con.commit()
    con.close()
    return str(tmp)


def _runtime(audit_db: str, heat_cfg: dict | None = None, **overrides):
    from types import SimpleNamespace

    live_dashboard = {
        "ENABLED": True,
        "DEFAULT_SYMBOLS": ["EUR/USD"],
        "MAX_SYMBOLS": 8,
    }
    if heat_cfg is not None:
        live_dashboard["SESSION_HEAT"] = heat_cfg

    runtime = SimpleNamespace(
        CONFIG={
            "LIVE_DASHBOARD": live_dashboard,
            "PAPER_SOAK": {"ENABLED": True},
            "REAL_ORDERS_ALLOWED": False,
        },
        AUDIT_DB=audit_db,
        ALL_PAIRS=PAIRS,
        ACTIVE_PAIRS=PAIRS,
        live_prices={},
        live_prices_lock=threading.Lock(),
        live_dashboard_scalp_cache={},
        live_dashboard_scalp_cache_lock=threading.Lock(),
        fetch_candles=lambda *_a, **_k: [],
        scan_candle_limits=lambda: {"H1": 100, "H4": 100, "D1": 100},
        get_candle_fetch_meta=lambda *_a, **_k: {},
        get_candle_builder=lambda: None,
        json_safe=lambda value: value,
        mt5_connection_health=lambda: {"available": True, "connected": False},
        engine_b_cache_get=lambda _key: None,
        resolve_pair_from_signal=lambda _sig: None,
        get_min_confluence_threshold=lambda _pair: 0.0,
        last_scan_results=lambda: {},
        binance_ws=lambda: None,
        kill_switch=lambda: False,
        log=_Log(),
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


def _client(runtime):
    app = Flask(__name__)
    register_live_dashboard_routes(app, runtime)
    return app.test_client()


def _warm(client, path="/api/live-dashboard/snapshot?symbols=EUR/USD"):
    """Poll the snapshot until the background aggregate has landed."""
    deadline = time.time() + 15.0
    while time.time() < deadline:
        payload = client.get(path).get_json()
        if (payload.get("sessionHeat") or {}).get("available"):
            return payload
        time.sleep(0.1)
    return client.get(path).get_json()


@pytest.fixture(autouse=True)
def _clear_cache():
    sh.reset_session_heat_cache()
    yield
    sh.reset_session_heat_cache()


# --- Snapshot integration ----------------------------------------------------


def test_snapshot_does_not_block_on_a_cold_aggregate():
    client = _client(_runtime(_audit_db()))
    started = time.perf_counter()
    payload = client.get("/api/live-dashboard/snapshot?symbols=EUR/USD").get_json()
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, "cold session-heat cache must not stall the snapshot"
    heat = payload["sessionHeat"]
    assert heat["enabled"] is True
    assert heat["available"] is False
    assert heat["cache"]["state"] == "WARMING"
    assert heat["currentSession"]["key"] in sh.SESSION_KEYS
    # The card cue degrades to unavailable rather than inventing a reading.
    assert payload["symbols"][0]["sessionHeat"] == {
        "available": False,
        "reason": "warming",
        "session": heat["currentSession"]["key"],
    }


def test_snapshot_carries_card_cues_once_warm():
    client = _client(_runtime(_audit_db()))
    payload = _warm(client)

    heat = payload["sessionHeat"]
    assert heat["available"] is True
    assert heat["advisoryOnly"] is True
    assert heat["source"] == "backtest_trades_v3"

    cue = payload["symbols"][0]["sessionHeat"]
    assert cue["available"] is True
    assert cue["scoreGroup"] == "forex_majors"
    assert cue["primaryEngine"] in ("A", "B")
    assert cue["primary"]["label"].startswith(heat["currentSession"]["label"])
    assert cue["primary"]["tone"] in ("strong", "neutral", "weak", "unknown")
    for engine_key in ("engineA", "engineB"):
        assert cue[engine_key]["engine"] == engine_key[-1]


def test_engine_b_leads_the_card_cue_when_it_cleared_structure():
    client = _client(
        _runtime(_audit_db(), engine_b_cache_get=lambda key: (
            {"structural_verdict": "CLEAR", "direction": "LONG", "confidence_passed": True}
            if str(key).upper().startswith("EUR") else None
        ))
    )
    payload = _warm(client)
    cue = payload["symbols"][0]["sessionHeat"]
    assert cue["available"] is True
    # Engine B has its own recorded history here, so it stays the primary read.
    assert cue["primaryEngine"] == "B"


def test_disabled_feature_leaves_the_rest_of_the_snapshot_intact():
    client = _client(_runtime(_audit_db(), heat_cfg={"ENABLED": False}))
    payload = client.get("/api/live-dashboard/snapshot?symbols=EUR/USD").get_json()

    assert payload["sessionHeat"]["enabled"] is False
    assert payload["sessionHeat"]["available"] is False
    assert payload["symbols"][0]["sessionHeat"]["reason"] == "disabled"

    # Every pre-existing field is still present and unchanged in shape.
    row = payload["symbols"][0]
    for key in (
        "symbol", "asset_type", "source", "timeframe", "freshness", "engineA",
        "engineB", "engineC", "engineD", "aiReview", "paperPosition", "levels",
        "finalState", "mainReason", "blockReason", "executableState",
    ):
        assert key in row
    assert payload["payloadVersion"] == "live-dashboard-v3"
    assert "events" in payload and "connections" in payload


def test_snapshot_survives_a_missing_backtest_table():
    client = _client(_runtime(_audit_db(with_tables=False)))
    payload = client.get("/api/live-dashboard/snapshot?symbols=EUR/USD").get_json()
    assert payload["symbols"][0]["symbol"] == "EUR/USD"
    assert payload["sessionHeat"]["available"] is False
    assert payload["symbols"][0]["sessionHeat"]["available"] is False


def test_snapshot_survives_a_nonexistent_audit_db():
    client = _client(_runtime("Z:/nope/missing-audit.db"))
    payload = client.get("/api/live-dashboard/snapshot?symbols=EUR/USD").get_json()
    assert payload["symbols"][0]["symbol"] == "EUR/USD"
    assert payload["sessionHeat"]["available"] is False


# --- Dedicated endpoint ------------------------------------------------------


def test_endpoint_returns_the_matrix_with_the_documented_session_contract():
    client = _client(_runtime(_audit_db()))
    res = client.get("/api/live-dashboard/session-heat")
    assert res.status_code == 200
    data = res.get_json()

    assert data["enabled"] is True
    assert data["status"] in ("OK", "INSUFFICIENT")
    assert [s["key"] for s in data["sessions"]] == list(sh.SESSION_KEYS)
    assert data["currentSession"]["key"] in sh.SESSION_KEYS
    for bucket in data["sessions"]:
        assert bucket["windowUtc"] and bucket["killzone"] and bucket["description"]

    engine_rows = [m for m in data["matrix"] if m["scope"] == "engine"]
    assert {m["engine"] for m in engine_rows} == {"A", "B"}
    for row in engine_rows:
        assert set(row["cells"]) == set(sh.SESSION_KEYS)


def test_endpoint_engine_filter():
    client = _client(_runtime(_audit_db()))
    data = client.get("/api/live-dashboard/session-heat?engine=A").get_json()
    assert {m["engine"] for m in data["matrix"]} == {"A"}
    assert data["requested"]["engine"] == "A"


def test_endpoint_score_group_filter_keeps_the_engine_row_for_comparison():
    client = _client(_runtime(_audit_db()))
    data = client.get("/api/live-dashboard/session-heat?engine=A&scoreGroup=forex_majors").get_json()
    scopes = {(m["scope"], m["scoreGroup"]) for m in data["matrix"]}
    assert ("engine", None) in scopes
    assert ("scoreGroup", "forex_majors") in scopes
    assert all(m["scoreGroup"] in (None, "forex_majors") for m in data["matrix"])


def test_endpoint_lookback_and_min_sample_overrides_are_applied():
    client = _client(_runtime(_audit_db()))
    data = client.get("/api/live-dashboard/session-heat?lookbackDays=0&minSample=999").get_json()
    assert data["settings"]["lookbackDays"] == 0
    assert data["settings"]["minSample"] == 999
    # Nothing can clear a 999-trade floor here, so no cell may be graded.
    assert data["status"] == "INSUFFICIENT"
    for row in data["matrix"]:
        assert all(c["heat"] == "INSUFFICIENT" for c in row["cells"].values())


def test_endpoint_rejects_junk_parameters():
    client = _client(_runtime(_audit_db()))
    assert client.get("/api/live-dashboard/session-heat?lookbackDays=abc").status_code == 400
    assert client.get("/api/live-dashboard/session-heat?engine=Z").status_code == 400


def test_endpoint_reports_disabled_instead_of_failing():
    client = _client(_runtime(_audit_db(), heat_cfg={"ENABLED": False}))
    res = client.get("/api/live-dashboard/session-heat")
    assert res.status_code == 200
    data = res.get_json()
    assert data["enabled"] is False
    assert data["status"] == "DISABLED"
    assert data["matrix"] == []
    assert data["currentSession"]["key"] in sh.SESSION_KEYS


def test_endpoint_reports_no_data_instead_of_failing():
    client = _client(_runtime(_audit_db(with_tables=False)))
    res = client.get("/api/live-dashboard/session-heat")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ERROR"
    assert data["matrix"] == []


def test_endpoint_is_blocked_when_the_whole_dashboard_is_disabled():
    runtime = _runtime(_audit_db())
    runtime.CONFIG["LIVE_DASHBOARD"]["ENABLED"] = False
    res = _client(runtime).get("/api/live-dashboard/session-heat")
    assert res.status_code == 503
