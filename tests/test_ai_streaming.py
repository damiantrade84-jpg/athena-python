"""Focused tests for ai_streaming SSE helpers (Phase 0).

Covers: SSE formatting, disabled-by-default, fail-closed when no API key,
start/token/done event sequence. Does NOT make live API calls.
"""

from __future__ import annotations

import json

import pytest

import ai_streaming


def test_is_streaming_enabled_default_false():
    ai_streaming.CONFIG["AI_STREAMING_ENABLED"] = False
    try:
        assert ai_streaming.is_streaming_enabled() is False
    finally:
        ai_streaming.CONFIG.pop("AI_STREAMING_ENABLED", None)


def test_is_streaming_enabled_true_when_configured():
    ai_streaming.CONFIG["AI_STREAMING_ENABLED"] = True
    try:
        assert ai_streaming.is_streaming_enabled() is True
    finally:
        ai_streaming.CONFIG.pop("AI_STREAMING_ENABLED", None)


def test_sse_format_string_data():
    out = ai_streaming.sse_format("token", "hello")
    assert out == "event: token\ndata: hello\n\n"


def test_sse_format_json_data():
    out = ai_streaming.sse_format("token", {"text": "hi"})
    # data line is JSON-serialized
    assert out.startswith("event: token\ndata: ")
    assert out.endswith("\n\n")
    payload = out.split("data: ", 1)[1].rstrip("\n\n")
    parsed = json.loads(payload)
    assert parsed == {"text": "hi"}


def test_sse_format_handles_non_serializable():
    out = ai_streaming.sse_format("meta", {"obj": object()})
    assert out.startswith("event: meta\ndata: ")
    assert out.endswith("\n\n")


def test_sse_done_emits_done_event():
    out = ai_streaming.sse_done()
    assert "event: done" in out
    assert '"ok": true' in out


def test_stream_chat_completion_no_api_key_yields_nothing(monkeypatch):
    """When no API key is configured, the generator yields nothing (fail-closed)."""
    # resolve_ai_review_runtime returns empty api_key
    from config import CONFIG
    monkeypatch.setattr(
        ai_streaming,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    monkeypatch.setattr(ai_streaming, "get_ai_model", lambda *a, **kw: "grok-4.3")
    monkeypatch.setattr(ai_streaming, "create_ai_client", lambda *a, **kw: None)
    tokens = list(
        ai_streaming.stream_chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
        )
    )
    assert tokens == []


def test_stream_sse_tokens_emits_start_and_done_when_no_key(monkeypatch):
    """When streaming is unavailable, SSE stream emits start + error + done."""
    monkeypatch.setattr(
        ai_streaming,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    monkeypatch.setattr(ai_streaming, "get_ai_model", lambda *a, **kw: "grok-4.3")
    monkeypatch.setattr(ai_streaming, "create_ai_client", lambda *a, **kw: None)
    events = list(
        ai_streaming.stream_sse_tokens(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
            meta={"symbol": "BTCUSDT"},
        )
    )
    # Should emit start, error, done (no token events)
    assert any("event: start" in e for e in events)
    assert any("event: error" in e for e in events)
    assert any("event: done" in e for e in events)
    assert not any("event: token" in e for e in events)
    # start event contains the meta
    start_event = next(e for e in events if "event: start" in e)
    assert "BTCUSDT" in start_event


def test_stream_sse_tokens_emits_tokens_when_generator_yields(monkeypatch):
    """When the underlying generator yields tokens, SSE stream emits token events."""

    def fake_stream(**kwargs):
        yield "Hello"
        yield " world"

    monkeypatch.setattr(ai_streaming, "stream_chat_completion", fake_stream)
    events = list(
        ai_streaming.stream_sse_tokens(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
        )
    )
    assert any("event: start" in e for e in events)
    token_events = [e for e in events if "event: token" in e]
    assert len(token_events) == 2
    assert "Hello" in token_events[0]
    assert "world" in token_events[1]
    assert any("event: done" in e for e in events)
    # No error event when tokens were emitted
    assert not any("event: error" in e for e in events)


def test_stream_chat_completion_swallows_exceptions(monkeypatch):
    """Provider errors must not raise — the generator simply ends."""

    def boom(**kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(ai_streaming, "resolve_ai_review_runtime", lambda cfg: {"provider": "grok", "api_key": "k", "selectedProvider": "grok"})
    monkeypatch.setattr(ai_streaming, "get_ai_model", lambda *a, **kw: "grok-4.3")
    monkeypatch.setattr(ai_streaming, "create_ai_client", lambda *a, **kw: object())
    # Patch the client.chat.completions.create to raise
    class _BadClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("boom")
    monkeypatch.setattr(ai_streaming, "create_ai_client", lambda *a, **kw: _BadClient())
    tokens = list(
        ai_streaming.stream_chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
        )
    )
    assert tokens == []
