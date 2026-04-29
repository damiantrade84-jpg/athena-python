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


def test_api_scan_settings_returns_runtime_snapshot():
    athena_module = _load_athena_module()
    client = athena_module.app.test_client()
    resp = client.get("/api/scan-settings")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "settings" in data
    assert data["settings"]["D1_CANDLES"] == int(
        athena_module.CONFIG.get("D1_CANDLES", 1001)
    )
    assert data["settings"]["SCAN_MAX_WORKERS"] == int(
        athena_module.CONFIG.get("SCAN_MAX_WORKERS", 3)
    )
    assert data["unverified_scan_keys"] == ["FOREX_H4_RESAMPLE_OFFSET_HOURS"]
    assert "TIMED_EXIT" in data["settings"]
    assert isinstance(data["settings"]["TIMED_EXIT"], dict)
    assert "scalp" in data["settings"]["TIMED_EXIT"]
    assert "SCALP_LIVE_MILESTONES" in data["settings"]
    assert "LIVE_MILESTONE_MANAGEMENT" in data["settings"]["SCALP_LIVE_MILESTONES"]


def test_api_scan_settings_is_read_only():
    athena_module = _load_athena_module()
    client = athena_module.app.test_client()

    resp = client.post("/api/scan-settings", json={"SCAN_MAX_WORKERS": 0})

    assert resp.status_code == 405


def test_api_last_scan_unavailable_until_successful_scan(monkeypatch):
    athena_module = _load_athena_module()
    client = athena_module.app.test_client()
    resp = client.get("/api/last-scan")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("available") is False

    monkeypatch.setattr(
        athena_module,
        "_last_scan_results",
        {
            "success": True,
            "scannedAt": "2026-04-29T12:00:00+00:00",
            "tradeSignals": [],
            "watchlist": [],
            "signals": [],
        },
    )
    resp2 = client.get("/api/last-scan")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2.get("available") is True
    assert data2.get("scannedAt") == "2026-04-29T12:00:00+00:00"


def test_api_auto_trade_toggle_persists_runtime_and_yaml(monkeypatch):
    athena_module = _load_athena_module()
    persisted = []

    monkeypatch.setattr(athena_module, "_update_yaml_toggle", lambda state: persisted.append(state))
    athena_module.CONFIG["AUTO_TRADE_ENABLED"] = True
    athena_module._auto_trader.disable()

    client = athena_module.app.test_client()

    resp = client.post("/api/auto-trade", json={"action": "on"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True
    assert athena_module.CONFIG["AUTO_TRADE_ENABLED"] is True
    assert persisted[-1] is True

    resp = client.post("/api/auto-trade", json={"action": "off"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is False
    assert athena_module.CONFIG["AUTO_TRADE_ENABLED"] is False
    assert persisted[-1] is False
