"""ASE manual demo execution route stays server-authoritative."""

from __future__ import annotations

from flask import Flask

from athena_app.api import routes_ase


def test_ase_execute_reruns_one_symbol_and_returns_execution_outcome(monkeypatch):
    calls: list[dict] = []

    def fake_run_ase_scan(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "signals": [
                {
                    "instrument": "COCOA",
                    "horizon": "swing",
                    "decisionStatus": "TRADE",
                    "direction": "LONG",
                }
            ],
            "executions": [
                {
                    "executed": False,
                    "reason": "risk:MAX_POSITIONS_REACHED",
                    "venue": "mt5",
                }
            ],
        }

    monkeypatch.setattr(routes_ase, "run_ase_scan", fake_run_ase_scan)

    app = Flask(__name__)
    routes_ase.register_ase_routes(app)

    response = app.test_client().post(
        "/api/ase-execute",
        json={
            "instrument": "COCOA",
            "horizon": "swing",
            "entryReference": 1,
            "sl": 0,
            "tp1": 999999,
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "symbols": ["COCOA"],
            "horizon": "swing",
            "write_journal": True,
            "execute_trades": True,
        }
    ]
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["executed"] is False
    assert payload["reason"] == "risk:MAX_POSITIONS_REACHED"
    assert payload["execution"]["venue"] == "mt5"
    assert payload["signal"]["decisionStatus"] == "TRADE"


def test_ase_execute_reports_when_refreshed_signal_is_no_longer_trade(monkeypatch):
    monkeypatch.setattr(
        routes_ase,
        "run_ase_scan",
        lambda **_kwargs: {
            "success": True,
            "signals": [
                {
                    "instrument": "WHEAT",
                    "horizon": "swing",
                    "decisionStatus": "WATCH",
                    "direction": "LONG",
                }
            ],
            "executions": [],
        },
    )

    app = Flask(__name__)
    routes_ase.register_ase_routes(app)

    response = app.test_client().post(
        "/api/ase-execute",
        json={"instrument": "WHEAT", "horizon": "swing"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": False,
        "executed": False,
        "reason": "signal_status:WATCH",
        "signal": {
            "instrument": "WHEAT",
            "horizon": "swing",
            "decisionStatus": "WATCH",
            "direction": "LONG",
        },
    }
