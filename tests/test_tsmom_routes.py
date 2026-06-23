"""TSMOM demo routes stay server-authoritative — manual run delegates to the gated
runtime; status is read-only. Runtime is monkeypatched (no feed/broker)."""
from __future__ import annotations

from flask import Flask

from athena_app.api import routes_tsmom


def test_tsmom_run_invokes_runtime_and_returns_result(monkeypatch):
    calls: list[str] = []

    def fake_run_once(cfg):
        calls.append(cfg.instrument)
        return {"status": "opened", "instrument": "gold",
                "result": {"executed": True, "result": {"orderId": "demo-9"}},
                "decision": {"action": "OPEN_LONG", "reason": "regime_flip_long"}}

    monkeypatch.setattr(routes_tsmom, "run_once", fake_run_once)
    app = Flask(__name__)
    routes_tsmom.register_tsmom_routes(app)

    resp = app.test_client().post("/api/tsmom-run", json={"instrument": "gold"})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["executed"] is True
    assert payload["result"]["status"] == "opened"
    assert calls == ["gold"]


def test_tsmom_run_rejects_unknown_instrument():
    app = Flask(__name__)
    routes_tsmom.register_tsmom_routes(app)
    resp = app.test_client().post("/api/tsmom-run", json={"instrument": "dogecoin"})
    assert resp.status_code == 400
    assert "unknown_instrument" in resp.get_json()["error"]


def test_tsmom_status_returns_snapshot_and_scorecard(monkeypatch):
    monkeypatch.setattr(routes_tsmom, "status",
                        lambda cfg: {"instrument": "gold", "hasData": True,
                                     "decision": {"action": "NONE", "reason": "no_flip"},
                                     "freshness": {"ok": False, "reason": "no_candles"}})
    app = Flask(__name__)
    routes_tsmom.register_tsmom_routes(app)

    resp = app.test_client().get("/api/tsmom-status")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["deployment"] == "DEMO_ONLY"
    assert payload["state"]["decision"]["action"] == "NONE"
    assert payload["scorecard"]["book"]["sqn100Oos"] == 3.47
