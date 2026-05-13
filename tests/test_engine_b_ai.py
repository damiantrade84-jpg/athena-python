"""Focused Engine B AI regressions for message formatting and annotations."""

import os
import sys
from typing import Optional, get_type_hints

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_b_ai import (
    _call_ai_with_retry,
    _is_retryable_ai_error,
    _normalise_engine_b_grade,
    _validate_engine_b_ai_payload,
    build_engine_b_signal_message,
    get_engine_b_ai_verdict,
)

import engine_b_ai


def test_build_engine_b_signal_message_preserves_explicit_zero_engine_a_values():
    message = build_engine_b_signal_message(
        pair="EUR/USD",
        direction="LONG",
        current_price=1.1,
        structure_result={},
        confidence_result={"score": 4.0, "max_possible": 5.0, "passed": True},
        engine_a_ctx={
            "direction": "LONG",
            "confluenceScore": 0.0,
            "score": 2.5,
            "maxScore": 0.0,
            "max_score": 3.0,
        },
    )

    assert "Engine A Confluence: 0.00 / 0.0 (0%)" in message


def test_get_engine_b_ai_verdict_api_key_annotation_is_optional():
    hints = get_type_hints(get_engine_b_ai_verdict)

    assert hints["xai_api_key"] == Optional[str]


def test_engine_b_ai_grade_validation_rejects_out_of_rubric_grade():
    assert _normalise_engine_b_grade("B+") == "C"
    assert _normalise_engine_b_grade("a+") == "A+"


def test_validate_engine_b_ai_payload_sanitizes_edge_probability():
    out = _validate_engine_b_ai_payload(
        {"grade": "A", "edgeProbability": "75", "riskLevel": "Low", "verdict": "ok"},
        pair="TEST",
    )
    assert out["edgeProbability"] == 75.0


def test_validate_engine_b_ai_payload_clamps_out_of_range_probability():
    out = _validate_engine_b_ai_payload(
        {"grade": "A", "edgeProbability": 150, "riskLevel": "Low", "verdict": "ok"},
        pair="TEST",
    )
    assert out["edgeProbability"] == 100.0


def test_validate_engine_b_ai_payload_defaults_missing_probability():
    out = _validate_engine_b_ai_payload(
        {"grade": "A", "riskLevel": "Low", "verdict": "ok"},
        pair="TEST",
    )
    assert out["edgeProbability"] == 50.0


def test_validate_engine_b_ai_payload_normalizes_invalid_risk_level():
    out = _validate_engine_b_ai_payload(
        {"grade": "A", "edgeProbability": 50, "riskLevel": "unknown", "verdict": "ok"},
        pair="TEST",
    )
    assert out["riskLevel"] == "Medium"


def test_validate_engine_b_ai_payload_creates_missing_style_ratings():
    out = _validate_engine_b_ai_payload(
        {"grade": "A", "edgeProbability": 60, "riskLevel": "Low", "verdict": "ok"},
        pair="TEST",
    )
    assert set(out["style_ratings"].keys()) == {"scalp", "intraday", "swing"}
    assert out["style_ratings"]["swing"]["edgeProbability"] == 60.0


def test_validate_engine_b_ai_payload_normalizes_generated_style_grades():
    out = _validate_engine_b_ai_payload(
        {"grade": "B+", "edgeProbability": 60, "riskLevel": "Low", "verdict": "ok"},
        pair="TEST",
    )

    assert out["grade"] == "B+"
    assert out["style_ratings"]["scalp"]["grade"] == "C"
    assert out["style_ratings"]["intraday"]["grade"] == "C"
    assert out["style_ratings"]["swing"]["grade"] == "C"


def test_validate_engine_b_ai_payload_sanitizes_nested_style_fields():
    out = _validate_engine_b_ai_payload(
        {
            "grade": "A",
            "edgeProbability": 60,
            "riskLevel": "Low",
            "verdict": "ok",
            "style_ratings": {
                "scalp": {"grade": "invalid", "edgeProbability": "bad", "riskLevel": "???"},
            },
        },
        pair="TEST",
    )
    assert out["style_ratings"]["scalp"]["grade"] == "C"
    assert out["style_ratings"]["scalp"]["edgeProbability"] == 60.0
    assert out["style_ratings"]["scalp"]["riskLevel"] == "Low"


def test_get_engine_b_ai_verdict_returns_error_when_no_api_key():
    result = get_engine_b_ai_verdict(
        pair="EUR/USD",
        direction="LONG",
        current_price=1.1,
        structure_result={},
        confidence_result={"score": 4.0, "max_possible": 5.0, "passed": True},
        xai_api_key=None,
    )
    assert result.get("error") == "API key not configured"


def test_is_retryable_ai_error_detects_timeout_text():
    assert _is_retryable_ai_error(RuntimeError("Request timed out")) is True
    assert _is_retryable_ai_error(RuntimeError("Too many requests")) is True
    assert _is_retryable_ai_error(RuntimeError("schema invalid")) is False


def test_call_ai_with_retry_uses_configured_timeout_and_retries(monkeypatch):
    calls = []

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                raise RuntimeError("Request timed out")
            return type(
                "Resp",
                (),
                {"choices": [type("Choice", (), {"message": type("Msg", (), {"content": "{}"})()})()]},
            )()

    fake_client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _FakeCompletions()})()},
    )()

    monkeypatch.setitem(engine_b_ai.CONFIG, "ENGINE_B_AI_TIMEOUT_SEC", 42)
    monkeypatch.setitem(engine_b_ai.CONFIG, "ENGINE_B_AI_MAX_RETRIES", 2)
    monkeypatch.setitem(engine_b_ai.CONFIG, "ENGINE_B_AI_RETRY_BACKOFF_SEC", 0)
    monkeypatch.setattr(engine_b_ai.time, "sleep", lambda _s: None)

    _call_ai_with_retry(fake_client, "model-x", [{"role": "user", "content": "hi"}], 0.3)

    assert len(calls) == 3
    assert all(call["timeout"] == 42 for call in calls)


def test_get_engine_b_ai_verdict_normalizes_timeout_error(monkeypatch):
    monkeypatch.setattr(engine_b_ai, "create_ai_client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(engine_b_ai, "_call_ai_with_retry", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Request timed out")))

    result = get_engine_b_ai_verdict(
        pair="XAU/USD",
        direction="LONG",
        current_price=1.1,
        structure_result={},
        confidence_result={"score": 4.0, "max_possible": 5.0, "passed": True},
        xai_api_key="key",
    )

    assert result["error"] == "AI request timed out after retry attempts"
