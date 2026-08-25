"""OX Book routes stay server-authoritative: membership gate refuses anything not
in OX_BOOK.LIVE_MEMBERS before the runtime is touched. Runtime is monkeypatched
(no feed/broker)."""
from __future__ import annotations

from flask import Flask

from athena_app.api import routes_ox_book


def _register(monkeypatch, members=("gold", "nasdaq")) -> Flask:
    monkeypatch.setattr(routes_ox_book.ox_settings, "live_members", lambda: tuple(members))
    app = Flask(__name__)
    routes_ox_book.register_ox_book_routes(app)
    return app


def test_run_invokes_runtime_for_certified_member(monkeypatch):
    calls: list[str] = []

    def fake_run_once(cfg):
        calls.append(cfg.instrument)
        return {"status": "hold", "instrument": cfg.instrument,
                "decision": {"action": "HOLD", "reason": "trail"}}

    import tsmom_live.runtime as rt

    monkeypatch.setattr(rt, "run_once", fake_run_once)
    app = _register(monkeypatch)

    resp = app.test_client().post("/api/ox-book-run", json={"instrument": "gold"})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["executed"] is True  # hold ratchets the trail stop at the broker
    assert calls == ["gold"]


def test_run_refuses_non_certified_instrument(monkeypatch):
    app = _register(monkeypatch, members=("gold",))
    resp = app.test_client().post("/api/ox-book-run", json={"instrument": "nasdaq"})
    assert resp.status_code == 400
    payload = resp.get_json()
    assert "not_certified_member" in payload["error"]
    assert "nasdaq" in payload["error"]
    assert payload["certifiedMembers"] == ["gold"]


def test_run_refuses_unknown_and_missing_instrument(monkeypatch):
    app = _register(monkeypatch)
    r1 = app.test_client().post("/api/ox-book-run", json={"instrument": "dogecoin"})
    assert r1.status_code == 400
    assert "not_certified_member" in r1.get_json()["error"]

    r2 = app.test_client().post("/api/ox-book-run", json={})
    assert r2.status_code == 400
    assert r2.get_json()["error"] == "missing_instrument"


def test_status_lists_members_and_certification(monkeypatch):
    def fake_status(cfg):
        return {"instrument": cfg.instrument, "display": cfg.display, "hasData": False,
                "status": "no_bars"}

    import tsmom_live.runtime as rt

    monkeypatch.setattr(rt, "status", fake_status)
    app = _register(monkeypatch)

    resp = app.test_client().get("/api/ox-book-status")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["deployment"] == "DEMO_ONLY"
    assert set(payload["members"]) == {"gold", "nasdaq"}
    assert payload["certification"]["bookPooled"]["tStat"] == 3.56
    assert payload["snapshots"]["gold"]["instrument"] == "gold"
