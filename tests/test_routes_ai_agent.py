import sys
from types import SimpleNamespace

import pytest
from flask import Flask

from athena_app.api.routes_ai_agent import register_ai_agent_routes


@pytest.fixture(autouse=True)
def _unit_config(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "config",
        SimpleNamespace(
            CONFIG={
                "AI_MARKET_INTELLIGENCE_ENABLED": True,
                "AI_MARKET_INTELLIGENCE_TTL_SECONDS": 1800,
                "AI_SIMILAR_SETUPS_ENABLED": False,
            }
        ),
    )


class _Log:
    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass


def _client(tmp_path, last_scan=None):
    app = Flask(__name__)
    runtime = SimpleNamespace(
        CONFIG={
            "AI_AGENT_PACKET_ENABLED": True,
            "AI_SIMILAR_SETUPS_ENABLED": True,
            "AI_CONTRADICTION_DETECTOR_ENABLED": True,
        },
        AUDIT_DB=str(tmp_path / "audit.db"),
        last_scan_results=lambda: last_scan or {},
        live_dashboard_scalp_cache={},
        live_dashboard_scalp_cache_lock=None,
        json_safe=lambda value: value,
        log=_Log(),
    )
    register_ai_agent_routes(app, runtime)
    return app.test_client()


def test_trade_chat_returns_valid_shape_with_trace(tmp_path):
    client = _client(
        tmp_path,
        {
            "signals": [
                {
                    "trace_id": "trace-1",
                    "pair": "EUR/USD",
                    "type": "forex",
                    "direction": "LONG",
                    "rr1": 2.0,
                    "dataFreshness": {"allowed": True, "reason": "fresh"},
                    "market_intelligence": {
                        "schema_version": "market_intelligence.v1",
                        "freshness_status": "partial",
                        "warnings": ["calendar partial"],
                        "macro_regime": {
                            "risk_regime": "risk_off",
                            "calendar_within_72h": [{"name": "CPI"}],
                        },
                        "source_status": {"macro": {"status": "partial"}},
                    },
                    "vision": {
                        "structured_trade_read": {
                            "right_edge_status": "CONFIRMS",
                            "tf_alignment": "ALIGNED",
                            "freshness_status": "fresh",
                            "allowed_for_execution_context": True,
                            "style_ratings": {"intraday": "MODERATE"},
                            "visible_structure": {"obstacles_before_tp": ["supply above"]},
                            "memo": "visible confirmation",
                        }
                    },
                }
            ]
        },
    )

    resp = client.post(
        "/api/ai/trade-chat",
        json={"trace_id": "trace-1", "symbol": "EUR/USD", "message": "Review this trade"},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session_id"]
    assert data["trace_id"] == "trace-1"
    assert data["symbol"] == "EUR/USD"
    assert isinstance(data["facts_used"], list)
    assert isinstance(data["contradiction_flags"], list)
    assert isinstance(data["tool_calls"], list)
    assert data["safety"]["read_only"] is True
    assert data["safety"]["can_execute"] is False
    assert data["market_intelligence"]["freshness_status"] == "partial"
    assert data["market_intelligence"]["risk_regime"] == "risk_off"
    assert data["market_intelligence"]["calendar_within_72h"] == [{"name": "CPI"}]
    assert data["vision_summary"]["right_edge_status"] == "CONFIRMS"
    assert data["vision_summary"]["allowed_for_execution_context"] is True


def test_trade_chat_missing_trace_id_does_not_crash(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/ai/trade-chat",
        json={"symbol": "BTCUSDT", "message": "What do you know?", "trace_id": None},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["symbol"] == "BTCUSDT"
    assert "market_intelligence" in data
    assert "vision_summary" in data
    assert data["decision"] in {"DATA_INSUFFICIENT", "WATCHLIST", "WAIT_FOR_CONFIRMATION", "NO_TRADE", "BLOCKED_BY_RISK"}


def test_trade_chat_is_read_only_and_never_executes(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/ai/trade-chat",
        json={"message": "Execute this now", "symbol": "ETHUSDT"},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["decision"] != "EXECUTE"
    assert "execute" not in data["final_action"].lower()
    assert data["safety"]["can_execute"] is False


def test_trade_chat_rejects_empty_message(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/api/ai/trade-chat", json={"message": ""})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "message_required"


def _selected_signal_payload(symbol: str = "QQQ.US") -> dict:
    return {
        "trace_id": "selected-trace-1",
        "symbol": symbol,
        "pair": symbol,
        "direction": "LONG",
        "engine": "engine_a",
        "engine_source": "engine_a",
        "style": "intraday",
        "rr1": 2.1,
        "min_rr": 1.5,
        "entry": 450.12,
        "sl": 446.0,
        "tp1": 458.0,
        "dataFreshness": {"allowed": True, "reason": "fresh"},
        "market_intelligence": {
            "schema_version": "market_intelligence.v1",
            "freshness_status": "fresh",
            "macro_regime": {"risk_regime": "risk_on", "calendar_within_72h": []},
        },
        "vision": {
            "structured_trade_read": {
                "right_edge_status": "CONFIRMS",
                "tf_alignment": "ALIGNED",
                "freshness_status": "fresh",
                "allowed_for_execution_context": True,
            }
        },
    }


def test_trade_chat_resolves_from_trace_id_lookup(tmp_path):
    client = _client(tmp_path, {"signals": [_selected_signal_payload()]})

    resp = client.post(
        "/api/ai/trade-chat",
        json={
            "trace_id": "selected-trace-1",
            "symbol": "QQQ.US",
            "message": "Review this trade.",
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    resolution = data.get("context_resolution") or {}
    assert resolution.get("mode") == "trace_id"
    assert resolution.get("trace_id_received") is True
    assert resolution.get("resolved_symbol") == "QQQ.US"
    assert data["trace_id"] == "selected-trace-1"
    # Must not emit the "no linked signal packet" placeholder copy.
    assert "no linked signal packet is available" not in (data.get("answer") or "").lower()
    assert "no linked signal packet is available" not in (data.get("market_read") or "").lower()


def test_trade_chat_uses_signal_payload_when_runtime_lookup_misses(tmp_path):
    # Runtime cache empty so trace_id lookup misses; signal payload should win.
    client = _client(tmp_path)

    resp = client.post(
        "/api/ai/trade-chat",
        json={
            "symbol": "QQQ.US",
            "message": "Review this trade.",
            "signal": _selected_signal_payload(),
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    resolution = data.get("context_resolution") or {}
    assert resolution.get("mode") == "request_signal_payload"
    assert resolution.get("signal_payload_received") is True
    assert resolution.get("resolved_symbol") == "QQQ.US"
    assert "no linked signal packet is available" not in (data.get("answer") or "").lower()
    assert "no linked signal packet is available" not in (data.get("market_read") or "").lower()


def test_trade_chat_symbol_only_fallback_when_no_signal(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/ai/trade-chat",
        json={"symbol": "QQQ.US", "message": "What do you know?"},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    resolution = data.get("context_resolution") or {}
    assert resolution.get("mode") == "symbol_only"
    assert resolution.get("signal_payload_received") is False
    assert resolution.get("trace_id_received") is False
    assert data["safety"]["read_only"] is True


def test_trade_chat_request_signal_payload_remains_read_only(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/ai/trade-chat",
        json={
            "symbol": "QQQ.US",
            "message": "Place this trade now.",
            "signal": _selected_signal_payload(),
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["safety"]["can_execute"] is False
    assert data["safety"]["can_modify_thresholds"] is False
    assert "execute" not in (data.get("final_action") or "").lower()


def test_strategist_brief_route_returns_stable_shape(tmp_path):
    client = _client(tmp_path)

    resp = client.get("/api/ai/strategist/brief")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schema_version"] == "strategist_brief.v1"
    assert "full_brief" in data


def test_strategist_pre_trade_route_is_read_only(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/ai/strategist/pre-trade-check",
        json={"direction": "LONG", "risk": {"rr": 2.0}},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schema_version"] == "strategist_pre_trade.v1"
    assert data["advisory_only"] is True
    assert data["execution_allowed"] is False


def test_strategist_weekly_route_never_auto_applies(tmp_path):
    client = _client(tmp_path)

    resp = client.get("/api/ai/strategist/weekly-retrospective")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schema_version"] == "strategist_weekly.v1"
    assert data["do_not_auto_apply"] is True
