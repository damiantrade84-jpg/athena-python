"""Stage 7 verification: v3 blueprint route shapes and 410 stubs."""

from __future__ import annotations

from flask import Flask

from athena_backtest.api import create_backtest_v3_blueprint


def _pairs() -> list[dict]:
    return [
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "source": "mt5", "enabled": True},
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance", "enabled": True},
    ]


def _client(monkeypatch=None):
    app = Flask(__name__)
    app.register_blueprint(create_backtest_v3_blueprint(all_pairs_provider=_pairs))
    return app.test_client()


def test_retired_endpoints_return_410():
    client = _client()
    for name in ("backtest-scalp", "backtest-consensus", "backtest-ase", "backtest-ase-all", "backtest-naked-all"):
        resp = client.post(f"/api/{name}", json={})
        assert resp.status_code == 410, name
        assert resp.get_json()["success"] is False


def test_single_pair_requires_symbol_and_known_pair():
    client = _client()
    assert client.post("/api/backtest", json={}).status_code == 400
    assert client.post("/api/backtest", json={"symbol": "NOPE"}).status_code == 404
    assert (
        client.post("/api/backtest", json={"symbol": "EURUSD", "style": "scalp"}).status_code
        == 422
    )


def test_single_pair_success_shape(monkeypatch):
    import athena_backtest.runner as runner

    def _fake_run_pair(pair, *, engine, horizon, store=None, n_trials_hint=1):
        return {
            "totalTrades": 1,
            "sqn": 1.0,
            "totalR": 0.5,
            "trades": [{"resultR": 0.5, "entryTime": "2025-01-01T00:00:00+00:00"}],
            "metricsV3": {"deflatedSqn": 1.0},
            "validation": {"verdict": "INSUFFICIENT_SAMPLE", "trialCount": 1},
        }

    monkeypatch.setattr(runner, "run_pair", _fake_run_pair)
    client = _client()
    resp = client.post(
        "/api/backtest", json={"symbol": "EURUSD", "style": "intraday", "persist": False}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["totalTrades"] == 1
    assert body["validation"]["verdict"] == "INSUFFICIENT_SAMPLE"


def test_backtest_all_starts_job_and_reports_status(monkeypatch):
    import athena_backtest.api as api_module

    started: dict = {}

    class _FakeJobs:
        def start(self, *, symbols, engine, style, persist=True):
            started.update({"symbols": symbols, "engine": engine, "style": style})
            return "job123"

        def status(self, job_id):
            if job_id != "job123":
                return None
            return {"jobId": job_id, "state": "done", "done": 2, "total": 2, "rows": []}

    monkeypatch.setattr(api_module, "BacktestJobManager", lambda **_: _FakeJobs())
    app = Flask(__name__)
    app.register_blueprint(create_backtest_v3_blueprint(all_pairs_provider=_pairs))
    client = app.test_client()

    resp = client.post("/api/backtest-all", json={"engine": "B", "style": "intraday"})
    assert resp.status_code == 202
    assert resp.get_json()["jobId"] == "job123"
    assert started["symbols"] == ["EURUSD", "BTCUSDT"]
    assert started["engine"] == "B"

    status = client.get("/api/backtest-jobs/job123")
    assert status.status_code == 200
    assert status.get_json()["state"] == "done"
    assert client.get("/api/backtest-jobs/nope").status_code == 404
