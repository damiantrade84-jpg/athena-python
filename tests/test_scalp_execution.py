from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import execution
import scalp_engine


def _load_athena_module():
    path = Path(__file__).resolve().parents[1] / "athena.py"
    spec = spec_from_file_location("athena_route_module_for_tests", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_scalp_execute_rejects_direction_flip(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        scalp_engine,
        "run_scalp_scan",
        lambda pairs: {
            "signals": [
                {
                    "pair": "EUR/USD",
                    "direction": "LONG",
                    "price": 1.1,
                    "sl": 1.095,
                    "tp1": 1.11,
                }
            ]
        },
    )

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={"symbol": "EUR/USD", "signal": {"symbol": "EUR/USD", "direction": "SHORT"}},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert "SIGNAL_FLIPPED" in data["error"]
    assert data["newDirection"] == "LONG"


def test_quick_execute_rejects_direction_flip(monkeypatch):
    athena_module = _load_athena_module()

    class _FakeLog:
        def warning(self, *args, **kwargs):
            return None

    class _FakeRt:
        CONFIG = {"SIGNAL_MAX_AGE_SEC": 300}
        ALL_PAIRS = [{"display": "EUR/USD"}]
        log = _FakeLog()

        @staticmethod
        def analyze_pair(pair_obj, btc_bias, style="swing"):
            return {
                "pair": pair_obj["display"],
                "direction": "LONG",
                "price": 1.101,
                "timestamp": "2026-03-26T00:00:00+00:00",
            }

    monkeypatch.setattr(execution, "rt", lambda: _FakeRt())

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/quick-execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "direction": "SHORT",
                "timestamp": "2000-01-01T00:00:00+00:00",
                "type": "forex",
            },
            "pip_mode": "intraday",
        },
    )

    assert resp.status_code == 409
    data = resp.get_json()
    assert "SIGNAL_FLIPPED" in data["error"]
    assert data["newDirection"] == "LONG"
