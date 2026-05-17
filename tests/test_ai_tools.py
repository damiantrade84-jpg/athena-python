from __future__ import annotations

from types import SimpleNamespace

import ai_tools


def _signal(symbol: str = "BTCUSDT") -> dict:
    return {
        "trace_id": f"trace-{symbol}",
        "pair": symbol,
        "symbol": symbol,
        "type": "crypto",
        "direction": "LONG",
        "rr1": 2.0,
        "spread_pips": 0.2,
        "dataFreshness": {"allowed": True, "reason": "fresh"},
        "market_intelligence": {
            "schema_version": "market_intelligence.v1",
            "freshness_status": "partial",
            "warnings": [],
            "macro_regime": {"risk_regime": "unknown", "calendar_within_72h": []},
        },
        "similar_outcomes": {
            "examples": [],
            "aggregate_sample_size": 0,
            "win_rate": None,
            "avg_r": None,
            "profit_factor": None,
            "oos_decay": None,
            "reliability": "unavailable",
            "warning": None,
            "missing_fields": [],
        },
    }


def test_signal_detail_stable_shape_from_provided_signal():
    out = ai_tools.get_signal_detail(signal=_signal())

    assert out["schema_version"] == "ai_tool.signal_detail.v1"
    assert out["status"] == "ok"
    assert out["found"] is True
    assert out["advisory_only"] is True
    assert out["execution_allowed"] is False
    assert set(out) >= {
        "generated_at",
        "signal_id",
        "symbol",
        "signal",
        "packet",
        "summary",
        "missing_fields",
        "warnings",
    }
    assert out["packet"]["symbol"] == "BTCUSDT"
    assert isinstance(out["summary"]["facts_used"], list)


def test_missing_signal_returns_safe_non_fabricated_fallback():
    out = ai_tools.get_signal_detail(signal_id="missing-trace", symbol="EURUSD", runtime=None)

    assert out["schema_version"] == "ai_tool.signal_detail.v1"
    assert out["status"] == "not_found"
    assert out["found"] is False
    assert out["signal"] is None
    assert out["packet"] is None
    assert out["summary"] is None
    assert out["symbol"] == "EURUSD"
    assert any("no signal data was fabricated" in warning for warning in out["warnings"])
    assert out["execution_allowed"] is False


def test_historical_analogues_insufficient_sample_has_no_probability_claim(monkeypatch):
    def fake_similar(_payload, limit=5):
        return {
            "examples": [{"pair": "BTCUSDT"}],
            "aggregate_sample_size": 3,
            "win_rate": 0.67,
            "avg_r": 1.2,
            "profit_factor": 2.0,
            "oos_decay": None,
            "reliability": "insufficient",
            "warning": "Fewer than 20 historical examples; do not calibrate probability from outcomes.",
            "missing_fields": [],
        }

    monkeypatch.setattr(ai_tools, "find_similar_setups", fake_similar)

    out = ai_tools.get_historical_analogues_tool(signal=_signal())

    assert out["schema_version"] == "ai_tool.historical_analogues.v1"
    assert out["analogues"]["aggregate_sample_size"] == 3
    assert out["probability_claim_allowed"] is False
    assert out["probability_claim"] is None


def test_compare_symbols_handles_one_missing_side():
    runtime = SimpleNamespace(last_scan_results=lambda: {"signals": [_signal("BTCUSDT")]})

    out = ai_tools.compare_symbols_tool(left_symbol="BTCUSDT", right_symbol="ETHUSDT", runtime=runtime)

    assert out["schema_version"] == "ai_tool.compare_symbols.v1"
    assert out["status"] == "ok"
    assert out["comparison"]["left_available"] is True
    assert out["comparison"]["right_available"] is False
    assert out["comparison"]["both_available"] is False
    assert out["left"]["packet"]["symbol"] == "BTCUSDT"
    assert out["right"]["packet"] is None
    assert out["execution_allowed"] is False


def test_open_risk_state_reads_runtime_only():
    runtime = SimpleNamespace(open_risk_state={"positions": [{"symbol": "BTCUSDT"}]})

    out = ai_tools.get_open_risk_state_tool(runtime=runtime)

    assert out["schema_version"] == "ai_tool.open_risk_state.v1"
    assert out["source"] == "runtime_only"
    assert out["risk_state"]["open_risk_state"]["positions"][0]["symbol"] == "BTCUSDT"
    assert out["execution_allowed"] is False
