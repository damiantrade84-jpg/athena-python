from __future__ import annotations

from ai_lee_confirmation import run_lee_confirmation


def _clean_signal(**overrides):
    signal = {
        "trace_id": "lee-trace-1",
        "pair": "EUR/USD",
        "symbol": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "style": "intraday",
        "trade": True,
        "execution_allowed": True,
        "risk_allowed": True,
        "freshness_allowed": True,
        "rr1": 2.2,
        "min_rr": 1.5,
        "entry": 1.101,
        "sl": 1.095,
        "tp1": 1.114,
        "engine_d": {
            "gate_result": "PASS",
            "executable": True,
            "rr1": 2.2,
            "rr_ok": True,
            "strict_fabio_pass": True,
        },
        "risk": {
            "rr": 2.2,
            "min_rr": 1.5,
            "guardian_status": "CLEAN",
            "kill_switch_status": "OFF",
            "data_freshness_status": "fresh",
        },
        "dataFreshness": {"allowed": True, "reason": "fresh"},
        "market_intelligence": {
            "schema_version": "market_intelligence.v1",
            "freshness_status": "fresh",
            "macro_regime": {"risk_regime": "risk_on", "calendar_within_72h": []},
            "warnings": [],
        },
    }
    signal.update(overrides)
    return signal


class _SupportAdapter:
    def draft(self, evidence_packet):
        return {
            "lee_verdict": "CONTEXT_SUPPORTS",
            "confidence": "medium",
            "narrative": "Lee sees the external context supporting the setup, but Athena gates remain in charge.",
            "supports": ["fresh market intelligence", "risk context present"],
            "risks": [],
            "missing_data": [],
            "model_used": "hermes-gpt-5.5-test",
        }


class _UnsafeSupportAdapter:
    def draft(self, evidence_packet):
        return {
            "lee_verdict": "CONTEXT_SUPPORTS",
            "confidence": "high",
            "narrative": "External context supports it. Execute the trade now.",
            "supports": ["adapter tried to support"],
            "risks": [],
            "missing_data": [],
            "model_used": "hermes-gpt-5.5-test",
        }


class _InvalidAdapter:
    def draft(self, evidence_packet):
        return "not-json"


class _TimeoutAdapter:
    def draft(self, evidence_packet):
        return None


def test_lee_confirmation_supports_verified_clean_signal_without_execution():
    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        _clean_signal(),
        server_verified_signal=True,
        adapter=_SupportAdapter(),
    )

    assert result["schema_version"] == "lee_confirmation.v1"
    assert result["lee_verdict"] == "CONTEXT_SUPPORTS"
    assert result["display_label"] == "Context supports"
    assert result["trade_specific_confirmation_allowed"] is True
    assert result["advisory_only"] is True
    assert result["execution_allowed"] is False
    assert result["safety"]["read_only"] is True
    assert result["safety"]["can_execute"] is False
    assert result["safety"]["can_modify_thresholds"] is False
    assert result["safety"]["can_modify_guardian"] is False
    assert result["safety"]["deterministic_gates_required"] is True
    assert result["model_used"] == "hermes-gpt-5.5-test"


def test_lee_confirmation_requires_server_verified_signal_for_trade_specific_support():
    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        _clean_signal(),
        server_verified_signal=False,
        adapter=_SupportAdapter(),
    )

    assert result["lee_verdict"] == "NEED_MORE_DATA"
    assert result["trade_specific_confirmation_allowed"] is False
    assert "server_verified_signal" in result["missing_data"]
    assert "server_signal_not_verified" in result["safety_flags"]


def test_lee_confirmation_downgrades_support_when_deterministic_gates_blocked():
    blocked = _clean_signal(
        trade=False,
        execution_allowed=False,
        risk_allowed=False,
        engine_d={"gate_result": "BLOCK", "executable": False, "rr1": 0.9, "rr_ok": False, "strict_fabio_pass": False},
        risk={"rr": 0.9, "min_rr": 1.5, "guardian_status": "BLOCK", "kill_switch_status": "OFF", "data_freshness_status": "fresh"},
    )

    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        blocked,
        server_verified_signal=True,
        adapter=_UnsafeSupportAdapter(),
    )

    assert result["lee_verdict"] == "CONTEXT_BLOCKS"
    assert result["trade_specific_confirmation_allowed"] is False
    assert result["execution_allowed"] is False
    assert "deterministic_gate_block" in result["safety_flags"]
    assert "unsafe_adapter_language_downgraded" in result["safety_flags"]
    assert "execute the trade" not in result["narrative"].lower()


def test_lee_confirmation_text_block_gate_overrides_supportive_adapter():
    blocked = _clean_signal(
        engine_d={"gate_result": "BLOCK", "executable": True, "rr1": 2.2, "rr_ok": True, "strict_fabio_pass": True},
    )

    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        blocked,
        server_verified_signal=True,
        adapter=_SupportAdapter(),
    )

    assert result["lee_verdict"] == "CONTEXT_BLOCKS"
    assert result["trade_specific_confirmation_allowed"] is False
    assert "deterministic_gate_block" in result["safety_flags"]
    assert any(flag.endswith("gate_result_blocking") for flag in result["safety_flags"])


def test_lee_confirmation_invalid_adapter_output_fails_closed():
    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        _clean_signal(),
        server_verified_signal=True,
        adapter=_InvalidAdapter(),
    )

    assert result["lee_verdict"] == "NEED_MORE_DATA"
    assert result["trade_specific_confirmation_allowed"] is False
    assert "lee_reasoning_invalid" in result["safety_flags"]
    assert result["execution_allowed"] is False


def test_lee_confirmation_timeout_adapter_fails_closed_when_explicit():
    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        _clean_signal(),
        server_verified_signal=True,
        adapter=_TimeoutAdapter(),
    )

    assert result["lee_verdict"] == "NEED_MORE_DATA"
    assert result["trade_specific_confirmation_allowed"] is False
    assert "lee_reasoning_invalid" in result["safety_flags"]
    assert "valid_lee_reasoning" in result["missing_data"]


def test_lee_confirmation_unsafe_adapter_language_fails_closed_on_clean_gates():
    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        _clean_signal(),
        server_verified_signal=True,
        adapter=_UnsafeSupportAdapter(),
    )

    assert result["lee_verdict"] == "NEED_MORE_DATA"
    assert result["trade_specific_confirmation_allowed"] is False
    assert "unsafe_adapter_language_downgraded" in result["safety_flags"]
    assert "execute the trade" not in result["narrative"].lower()
    assert "safe_lee_reasoning" in result["missing_data"]


def test_lee_confirmation_buy_now_language_fails_closed_on_clean_gates():
    class BuyNowAdapter:
        def draft(self, evidence_packet):
            return {
                "lee_verdict": "CONTEXT_SUPPORTS",
                "confidence": "high",
                "narrative": "Buy now.",
                "supports": ["adapter tried to support"],
                "risks": [],
                "missing_data": [],
                "model_used": "hermes-gpt-5.5-test",
            }

    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        _clean_signal(),
        server_verified_signal=True,
        adapter=BuyNowAdapter(),
    )

    assert result["lee_verdict"] == "NEED_MORE_DATA"
    assert result["trade_specific_confirmation_allowed"] is False
    assert "unsafe_adapter_language_downgraded" in result["safety_flags"]
    assert "buy now" not in result["narrative"].lower()


def test_lee_confirmation_missing_gate_evidence_cannot_be_trade_specific_support():
    sparse_signal = {
        "trace_id": "lee-trace-1",
        "symbol": "EUR/USD",
        "direction": "LONG",
        "market_intelligence": {"freshness_status": "fresh", "warnings": []},
    }

    result = run_lee_confirmation(
        {"trace_id": "lee-trace-1", "symbol": "EUR/USD"},
        sparse_signal,
        server_verified_signal=True,
        adapter=_SupportAdapter(),
    )

    assert result["lee_verdict"] == "NEED_MORE_DATA"
    assert result["trade_specific_confirmation_allowed"] is False
    assert "deterministic_gate_evidence_incomplete" in result["safety_flags"]
    assert any(item.startswith("deterministic_gate:") for item in result["missing_data"])
