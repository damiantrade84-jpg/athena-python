"""OX Book API contract: explicit read-only scan, then manual demo execution.

All TSMOM runtime functions are monkeypatched. These tests never touch a feed,
account, risk engine, or broker.
"""
from __future__ import annotations

from flask import Flask

from athena_app.api import routes_ox_book


def _register(monkeypatch, members=("gold", "nasdaq"), *, enabled=True) -> Flask:
    monkeypatch.setattr(routes_ox_book.ox_settings, "enabled", lambda: enabled)
    monkeypatch.setattr(routes_ox_book.ox_settings, "live_members", lambda: tuple(members))
    monkeypatch.setattr(routes_ox_book, "_latest_scan", None, raising=False)
    app = Flask(__name__)
    routes_ox_book.register_ox_book_routes(app)
    return app


def _snapshot(cfg, *, action="OPEN_LONG", fresh=True, has_data=True):
    return {
        "instrument": cfg.instrument,
        "display": cfg.display,
        "brokerSymbol": cfg.broker_symbol,
        "hasData": has_data,
        "bars": 399 if has_data else 0,
        "lastBarTimeMs": 1_777_000_000_000 if has_data else None,
        "inPosition": action in {"HOLD", "CLOSE"},
        "dataSource": "mt5",
        "freshness": {
            "ok": fresh,
            "reason": "fresh" if fresh else "stale_multi_bucket",
        },
        "decision": {
            "action": action,
            "reason": "regime_flip_long" if action == "OPEN_LONG" else "no_flip",
            "direction": "LONG" if action != "NONE" else "NONE",
            "entryRef": 2000.0,
            "sl": 1940.0,
            "tp1": 2180.0,
            "trailStop": 1940.0,
            "atr": 20.0,
            "emaFast": 2001.0,
            "emaSlow": 1999.0,
        },
    }


def test_scan_is_read_only_and_surfaces_manual_execution_eligibility(monkeypatch):
    app = _register(monkeypatch)
    calls = []

    def fake_status(cfg):
        calls.append(("status", cfg.instrument))
        return _snapshot(cfg)

    monkeypatch.setattr("tsmom_live.runtime.status", fake_status)
    monkeypatch.setattr(
        "tsmom_live.runtime.execute_manual",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("scan executed a trade")),
        raising=False,
    )

    response = app.test_client().post("/api/ox-book-scan")
    body = response.get_json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["autoExecute"] is False
    assert body["schedulerEnabled"] is False
    assert body["executionMode"] == "MANUAL_DEMO_ONLY"
    assert body["scanId"]
    assert body["scannedCount"] == 2
    assert calls == [("status", "gold"), ("status", "nasdaq")]
    assert body["snapshots"]["gold"]["manualExecution"] == {
        "eligible": True,
        "action": "OPEN_LONG",
        "reason": "manual_demo_action_available",
    }


def test_execute_requires_current_scan_and_old_run_cannot_bypass_it(monkeypatch):
    app = _register(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "tsmom_live.runtime.execute_manual",
        lambda *_a, **_k: calls.append("execute"),
        raising=False,
    )

    for endpoint in ("/api/ox-book-execute", "/api/ox-book-run"):
        response = app.test_client().post(endpoint, json={"instrument": "gold"})
        assert response.status_code == 409
        assert response.get_json()["error"] == "current_scan_required"

    assert calls == []


def test_manual_execute_uses_current_scan_action_and_bar(monkeypatch):
    app = _register(monkeypatch)
    captured = []
    monkeypatch.setattr("tsmom_live.runtime.status", lambda cfg: _snapshot(cfg))

    def fake_execute(cfg, *, expected_action, expected_bar_time_ms):
        captured.append((cfg.instrument, expected_action, expected_bar_time_ms))
        return {"status": "opened", "executed": True, "orderId": "demo-1"}

    monkeypatch.setattr("tsmom_live.runtime.execute_manual", fake_execute, raising=False)
    client = app.test_client()
    scan = client.post("/api/ox-book-scan").get_json()
    response = client.post(
        "/api/ox-book-execute",
        json={"instrument": "gold", "scanId": scan["scanId"]},
    )

    assert response.status_code == 200
    assert response.get_json()["executed"] is True
    assert captured == [("gold", "OPEN_LONG", 1_777_000_000_000)]


def test_consumed_scan_action_cannot_execute_twice(monkeypatch):
    app = _register(monkeypatch, members=("gold",))
    calls = []
    monkeypatch.setattr("tsmom_live.runtime.status", lambda cfg: _snapshot(cfg))

    def fake_execute(*_args, **_kwargs):
        calls.append("execute")
        return {"status": "opened", "executed": True}

    monkeypatch.setattr("tsmom_live.runtime.execute_manual", fake_execute, raising=False)
    client = app.test_client()
    scan = client.post("/api/ox-book-scan").get_json()
    payload = {"instrument": "gold", "scanId": scan["scanId"]}

    assert client.post("/api/ox-book-execute", json=payload).status_code == 200
    duplicate = client.post("/api/ox-book-execute", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "scan_action_consumed"
    assert calls == ["execute"]


def test_manual_execute_returns_actual_refusal_as_non_2xx(monkeypatch):
    app = _register(monkeypatch)
    monkeypatch.setattr("tsmom_live.runtime.status", lambda cfg: _snapshot(cfg))
    monkeypatch.setattr(
        "tsmom_live.runtime.execute_manual",
        lambda *_a, **_k: {
            "status": "rejected",
            "executed": False,
            "reason": "freshness:stale_multi_bucket",
        },
        raising=False,
    )
    client = app.test_client()
    scan = client.post("/api/ox-book-scan").get_json()
    response = client.post(
        "/api/ox-book-execute",
        json={"instrument": "gold", "scanId": scan["scanId"]},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "freshness:stale_multi_bucket"
    assert response.get_json()["executed"] is False


def test_non_actionable_or_stale_scan_never_reaches_execution(monkeypatch):
    app = _register(monkeypatch, members=("gold",))
    calls = []
    monkeypatch.setattr(
        "tsmom_live.runtime.status",
        lambda cfg: _snapshot(cfg, action="NONE", fresh=False),
    )
    monkeypatch.setattr(
        "tsmom_live.runtime.execute_manual",
        lambda *_a, **_k: calls.append("execute"),
        raising=False,
    )
    client = app.test_client()
    scan = client.post("/api/ox-book-scan").get_json()

    assert scan["snapshots"]["gold"]["manualExecution"] == {
        "eligible": False,
        "action": "NONE",
        "reason": "stale_data:stale_multi_bucket",
    }
    response = client.post(
        "/api/ox-book-execute",
        json={"instrument": "gold", "scanId": scan["scanId"]},
    )
    assert response.status_code == 409
    assert response.get_json()["error"] == "stale_data:stale_multi_bucket"
    assert calls == []


def test_status_is_metadata_only_and_returns_latest_scan(monkeypatch):
    app = _register(monkeypatch, members=("gold",))
    calls = []

    def fake_status(cfg):
        calls.append(cfg.instrument)
        return _snapshot(cfg)

    monkeypatch.setattr("tsmom_live.runtime.status", fake_status)
    client = app.test_client()

    empty = client.get("/api/ox-book-status").get_json()
    assert empty["scanId"] is None
    assert empty["snapshots"] == {}
    assert calls == []

    scan = client.post("/api/ox-book-scan").get_json()
    status = client.get("/api/ox-book-status").get_json()
    assert status["scanId"] == scan["scanId"]
    assert calls == ["gold"]


def test_disabled_engine_and_non_member_fail_closed(monkeypatch):
    disabled = _register(monkeypatch, enabled=False)
    response = disabled.test_client().post("/api/ox-book-scan")
    assert response.status_code == 503
    assert response.get_json()["error"] == "ox_book_disabled"

    app = _register(monkeypatch, members=("gold",))
    response = app.test_client().post(
        "/api/ox-book-execute",
        json={"instrument": "nasdaq", "scanId": "any"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "not_certified_member:nasdaq"
