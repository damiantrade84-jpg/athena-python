from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_athena_module():
    path = Path(__file__).resolve().parents[1] / "athena.py"
    spec = spec_from_file_location("athena_health_module_for_tests", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_api_health_reports_actual_configured_sources(monkeypatch):
    athena_module = _load_athena_module()
    monkeypatch.setattr(
        athena_module,
        "ALL_PAIRS",
        [
            {"source": "mt5"},
            {"source": "binance"},
            {"source": "yfinance"},
            {"source": "mt5"},
        ],
    )
    monkeypatch.setattr(athena_module, "ACTIVE_PAIRS", [{"source": "mt5"}])
    monkeypatch.setattr(
        athena_module,
        "_mt5_connection_health",
        lambda: {"available": True, "connected": True, "detail": "ok"},
    )

    client = athena_module.app.test_client()
    resp = client.get("/api/health")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["dataSources"] == ["binance", "mt5", "yfinance"]
    assert data["dataSource"] == "binance+mt5+yfinance"
    assert data["mt5"]["connected"] is True


def test_api_feed_health_exposes_cached_timeframe_meta(monkeypatch):
    athena_module = _load_athena_module()
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD=X",
        "type": "forex",
        "source": "mt5",
    }
    monkeypatch.setattr(athena_module, "ACTIVE_PAIRS", [pair])
    monkeypatch.setattr(
        athena_module,
        "scan_candle_limits",
        lambda pair_obj: {"D1": 300, "H4": 500, "H1": 500},
    )
    monkeypatch.setattr(
        athena_module,
        "_mt5_connection_health",
        lambda: {"available": True, "connected": False, "detail": "not_initialized"},
    )

    def _fake_meta(pair_obj, tf, limit):
        if tf == "H4":
            return {
                "upstream": "mt5",
                "lastBarTime": "2026-04-21T08:00:00+00:00",
                "lastBarAgeSec": 1800.0,
                "lastBarStale": False,
            }
        return None

    monkeypatch.setattr(athena_module, "_get_candle_fetch_meta", _fake_meta)

    client = athena_module.app.test_client()
    resp = client.get("/api/feed-health")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["activePairCount"] == 1
    assert data["pairsWithCachedMeta"] == 1
    assert data["pairs"][0]["pair"] == "EUR/USD"
    assert data["pairs"][0]["timeframes"]["H4"]["upstream"] == "mt5"
    assert data["pairs"][0]["timeframes"]["H4"]["lastBarAgeSec"] == 1800.0


def test_api_scan_settings_updates_runtime_config_without_restart(monkeypatch):
    athena_module = _load_athena_module()

    def _fake_apply_scan_settings_updates(new_vals):
        current = athena_module._scan_settings_snapshot()
        current.update(new_vals)
        for key, value in current.items():
            athena_module.CONFIG[key] = value
        return athena_module._scan_settings_snapshot()

    monkeypatch.setattr(
        athena_module,
        "_apply_scan_settings_updates",
        _fake_apply_scan_settings_updates,
    )

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scan-settings",
        json={
            "D1_CANDLES": 1200,
            "H4_CANDLES": 900,
            "H1_CANDLES": 700,
            "SCAN_MAX_WORKERS": 5,
            "SCAN_DEBUG_CANDLE_META": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["saved"] is True
    assert data["settings"]["D1_CANDLES"] == 1200
    assert data["settings"]["SCAN_MAX_WORKERS"] == 5
    assert data["settings"]["SCAN_DEBUG_CANDLE_META"] is True
    assert athena_module.CONFIG["D1_CANDLES"] == 1200
    assert athena_module.CONFIG["SCAN_DEBUG_CANDLE_META"] is True


def test_api_scan_settings_rejects_invalid_worker_range():
    athena_module = _load_athena_module()
    client = athena_module.app.test_client()

    resp = client.post(
        "/api/scan-settings",
        json={"SCAN_MAX_WORKERS": 0},
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["saved"] is False
    assert "SCAN_MAX_WORKERS" in data["error"]
