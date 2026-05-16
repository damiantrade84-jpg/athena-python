from types import SimpleNamespace

import ai_trade_chat
import ai_tools


def _signal() -> dict:
    return {
        "trace_id": "trace-1",
        "pair": "BTCUSDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "direction": "LONG",
        "rr1": 2.0,
        "min_rr": 1.5,
        "spread_pips": 0.2,
        "dataFreshness": {"allowed": True, "reason": "fresh"},
        "fee_guard": {"engine_d_reject_reason": None},
        "market_intelligence": {
            "schema_version": "market_intelligence.v1",
            "freshness_status": "partial",
            "warnings": [],
            "macro_regime": {"risk_regime": "unknown", "calendar_within_72h": []},
        },
        "vision": {
            "structured_trade_read": {
                "right_edge_status": "CONFIRMS",
                "tf_alignment": "ALIGNED",
                "freshness_status": "fresh",
                "allowed_for_execution_context": True,
            }
        },
        "similar_outcomes": {
            "examples": [],
            "aggregate_sample_size": 3,
            "win_rate": 0.8,
            "avg_r": 1.2,
            "profit_factor": 1.8,
            "oos_decay": None,
            "reliability": "insufficient",
            "warning": "sample too small",
            "missing_fields": [],
        },
    }


def test_plan_change_your_mind_triggers_relevant_tools():
    calls = ai_trade_chat.plan_tool_calls(
        "What would change your mind?",
        {"trace_id": "trace-1", "symbol": "BTCUSDT"},
    )
    names = {call["name"] for call in calls}

    assert {"get_signal_detail", "get_engine_context", "get_chart_vision", "get_historical_analogues"}.issubset(names)


def test_plan_vision_question_triggers_vision_tool():
    calls = ai_trade_chat.plan_tool_calls("Does Vision agree?", {"symbol": "BTCUSDT"})

    assert any(call["name"] == "get_chart_vision" for call in calls)


def test_plan_compare_triggers_compare_tool():
    calls = ai_trade_chat.plan_tool_calls(
        "Compare this BTC setup to ETH.",
        {"symbol": "BTCUSDT"},
    )

    assert calls[0]["name"] == "compare_symbols"
    assert calls[0]["args"]["right_symbol"] == "ETH/USDT"


def test_run_trade_chat_turn_missing_model_uses_deterministic_fallback(tmp_path):
    runtime = SimpleNamespace(last_scan_results=lambda: {"signals": [_signal()]})
    ai_tools.set_ai_tools_runtime(runtime)

    out = ai_trade_chat.run_trade_chat_turn(
        {
            "trace_id": "trace-1",
            "symbol": "BTCUSDT",
            "message": "What would change your mind?",
            "_audit_db": str(tmp_path / "audit.db"),
        }
    )

    assert out["session_id"]
    assert out["decision"] in {"VALID_SETUP", "WATCHLIST", "WAIT_FOR_CONFIRMATION", "DATA_INSUFFICIENT", "BLOCKED_BY_RISK"}
    assert out["safety"]["can_execute"] is False
    assert any(call["name"] == "get_chart_vision" for call in out["tool_calls"])
    assert out["facts_used"]


def test_failed_deterministic_gate_cannot_return_valid_setup(tmp_path):
    signal = _signal()
    signal["gate_result"] = "BLOCKED"
    signal["executable"] = False
    runtime = SimpleNamespace(last_scan_results=lambda: {"signals": [signal]})
    ai_tools.set_ai_tools_runtime(runtime)

    out = ai_trade_chat.run_trade_chat_turn(
        {"trace_id": "trace-1", "symbol": "BTCUSDT", "message": "Why blocked?", "_audit_db": str(tmp_path / "audit.db")}
    )

    assert out["decision"] != "VALID_SETUP"
    assert out["safety"]["read_only"] is True


def test_insufficient_similar_setups_do_not_produce_probability_claim(tmp_path):
    runtime = SimpleNamespace(last_scan_results=lambda: {"signals": [_signal()]})
    ai_tools.set_ai_tools_runtime(runtime)

    out = ai_trade_chat.run_trade_chat_turn(
        {
            "trace_id": "trace-1",
            "symbol": "BTCUSDT",
            "message": "What do similar setups say?",
            "_audit_db": str(tmp_path / "audit.db"),
        }
    )

    assert "no calibrated probability claim" in (out["historical_analogue_summary"] or "").lower()


def test_marcus_prompt_contains_required_safety_language():
    prompt = ai_trade_chat.MARCUS_CHAT_SYSTEM_PROMPT

    assert "Athena's read is a hypothesis" in prompt
    assert "If data is missing, say what is missing" in prompt
    assert "Never override guardian, freshness, kill switch, RR, spread, fee, or risk gates" in prompt
    assert "execute trades" not in prompt.lower()


def test_llm_answer_replaces_answer_and_preserves_deterministic_decision(monkeypatch, tmp_path):
    runtime = SimpleNamespace(last_scan_results=lambda: {"signals": [_signal()]})
    ai_tools.set_ai_tools_runtime(runtime)

    llm_text = (
        "BTCUSDT LONG looks like a watchlist setup — the structure supports it but "
        "similar-setup history is too thin for a probability claim, so wait for a "
        "fresh H1 close before adding."
    )
    monkeypatch.setattr(
        ai_trade_chat,
        "_try_marcus_chat_llm",
        lambda message, response, tool_results, packet: (llm_text, "test-marcus-model"),
    )

    out = ai_trade_chat.run_trade_chat_turn(
        {
            "trace_id": "trace-1",
            "symbol": "BTCUSDT",
            "message": "Walk me through this one in your own words.",
            "_audit_db": str(tmp_path / "audit.db"),
        }
    )

    assert out["answer"] == llm_text
    assert out["llm_answer"] is True
    assert out["deterministic_answer"]
    assert out["deterministic_answer"] != llm_text
    assert out["safety"]["can_execute"] is False
    assert out["decision"] in {"VALID_SETUP", "WATCHLIST", "WAIT_FOR_CONFIRMATION", "DATA_INSUFFICIENT", "BLOCKED_BY_RISK"}


def test_llm_unsafe_execution_language_falls_back_to_deterministic(monkeypatch):
    class _FakeMessage:
        content = "You should place the trade right now at market."

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeCompletion:
        choices = [_FakeChoice()]

    class _FakeChatCompletions:
        def create(self, **kwargs):
            return _FakeCompletion()

    class _FakeChat:
        completions = _FakeChatCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(ai_trade_chat, "ai_key_configured", lambda cfg=None: True)
    monkeypatch.setattr(ai_trade_chat, "create_ai_client", lambda cfg=None, **kw: _FakeClient())
    monkeypatch.setattr(ai_trade_chat, "get_ai_model", lambda cfg=None, preferred_key="AI_MODEL": "test-model")
    monkeypatch.setattr(ai_trade_chat, "get_ai_timeout_sec", lambda cfg=None, preferred_key=None: 5.0)
    monkeypatch.setattr(ai_trade_chat, "get_ai_max_retries", lambda cfg=None, preferred_key=None: 0)

    text, model_used = ai_trade_chat._try_marcus_chat_llm(
        "Should I take it?",
        {"decision": "WATCHLIST", "supports": [], "contradictions": []},
        {},
        None,
    )

    assert text is None
    assert model_used == "deterministic_fallback"


def test_llm_disabled_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(ai_trade_chat, "ai_key_configured", lambda cfg=None: False)

    text, model_used = ai_trade_chat._try_marcus_chat_llm(
        "Anything to add?",
        {"decision": "WATCHLIST"},
        {},
        None,
    )

    assert text is None
    assert model_used == "deterministic_fallback"
