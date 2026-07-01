"""Focused tests for ai_agent_planner (Phase 2 agent autonomy).

Covers: config gating, read-only tool registry enforcement, validation
rejects unknown/mutating tools, dedup, max-steps bound, fail-closed on
bad input. Does NOT make live LLM calls.
"""

from __future__ import annotations

import pytest

import ai_agent_planner as planner


def test_disabled_by_default():
    planner.CONFIG.pop("AI_AGENT_NATIVE_PLANNING_ENABLED", None)
    assert planner.is_native_planning_enabled() is False


def test_enabled_when_configured():
    planner.CONFIG["AI_AGENT_NATIVE_PLANNING_ENABLED"] = True
    try:
        assert planner.is_native_planning_enabled() is True
    finally:
        planner.CONFIG.pop("AI_AGENT_NATIVE_PLANNING_ENABLED", None)


def test_max_steps_default_and_bounds():
    planner.CONFIG.pop("AI_AGENT_MAX_STEPS", None)
    assert planner.get_max_steps() == 8
    planner.CONFIG["AI_AGENT_MAX_STEPS"] = 3
    try:
        assert planner.get_max_steps() == 3
    finally:
        planner.CONFIG.pop("AI_AGENT_MAX_STEPS", None)
    # Bounds: clamped to [1, 32]
    planner.CONFIG["AI_AGENT_MAX_STEPS"] = 0
    try:
        assert planner.get_max_steps() == 1
    finally:
        planner.CONFIG.pop("AI_AGENT_MAX_STEPS", None)
    planner.CONFIG["AI_AGENT_MAX_STEPS"] = 100
    try:
        assert planner.get_max_steps() == 32
    finally:
        planner.CONFIG.pop("AI_AGENT_MAX_STEPS", None)


def test_validate_rejects_unknown_tools():
    proposed = [
        {"name": "get_signal_detail", "args": {}},
        {"name": "execute_trade", "args": {"symbol": "BTCUSDT"}},  # not in registry
        {"name": "delete_position", "args": {}},  # not in registry
    ]
    out = planner.validate_tool_calls(proposed)
    assert len(out) == 1
    assert out[0]["name"] == "get_signal_detail"


def test_validate_dedupes_by_name():
    proposed = [
        {"name": "get_signal_detail", "args": {"symbol": "BTC"}},
        {"name": "get_signal_detail", "args": {"symbol": "ETH"}},  # dup → dropped
        {"name": "get_engine_context", "args": {}},
    ]
    out = planner.validate_tool_calls(proposed)
    assert len(out) == 2
    assert out[0]["name"] == "get_signal_detail"
    assert out[1]["name"] == "get_engine_context"


def test_validate_handles_string_args():
    """Some providers return args as a JSON string; validator should parse."""
    proposed = [
        {"name": "get_signal_detail", "args": '{"signal_id": "abc"}'},
    ]
    out = planner.validate_tool_calls(proposed)
    assert len(out) == 1
    assert out[0]["args"] == {"signal_id": "abc"}


def test_validate_handles_bad_args():
    proposed = [
        {"name": "get_signal_detail", "args": "not-json"},
        {"name": "get_engine_context", "args": None},
    ]
    out = planner.validate_tool_calls(proposed)
    assert len(out) == 2
    # Bad args fall back to {}
    assert out[0]["args"] == {}
    assert out[1]["args"] == {}


def test_validate_rejects_non_list_input():
    assert planner.validate_tool_calls(None) == []
    assert planner.validate_tool_calls("not a list") == []
    assert planner.validate_tool_calls({"name": "get_signal_detail"}) == []


def test_validate_rejects_non_dict_entries():
    proposed = ["not a dict", 42, None, {"name": "get_signal_detail", "args": {}}]
    out = planner.validate_tool_calls(proposed)
    assert len(out) == 1
    assert out[0]["name"] == "get_signal_detail"


def test_build_tool_definitions_only_contains_read_only_tools():
    defs = planner.build_tool_definitions()
    names = {d["function"]["name"] for d in defs}
    # Every defined tool must be in the read-only registry
    assert names.issubset(planner.READ_ONLY_TOOL_NAMES)
    # No mutating tools present
    assert "execute_trade" not in names
    assert "place_order" not in names
    assert "delete_position" not in names
    assert "approve_order" not in names


def test_plan_tool_calls_with_llm_disabled_returns_empty():
    planner.CONFIG.pop("AI_AGENT_NATIVE_PLANNING_ENABLED", None)
    out = planner.plan_tool_calls_with_llm("tell me about BTC", {"symbol": "BTCUSDT"})
    assert out == []


def test_plan_tool_calls_with_llm_empty_message_returns_empty():
    planner.CONFIG["AI_AGENT_NATIVE_PLANNING_ENABLED"] = True
    try:
        assert planner.plan_tool_calls_with_llm("", {"symbol": "BTCUSDT"}) == []
        assert planner.plan_tool_calls_with_llm("   ", {"symbol": "BTCUSDT"}) == []
    finally:
        planner.CONFIG.pop("AI_AGENT_NATIVE_PLANNING_ENABLED", None)


def test_plan_tool_calls_with_llm_no_api_key_returns_empty(monkeypatch):
    """Fail-closed: no api key → empty list (caller falls back to keyword planner)."""
    planner.CONFIG["AI_AGENT_NATIVE_PLANNING_ENABLED"] = True
    try:
        monkeypatch.setattr(
            planner,
            "resolve_ai_review_runtime",
            lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
        )
        monkeypatch.setattr(planner, "get_ai_api_key", lambda cfg: "")
        out = planner.plan_tool_calls_with_llm("tell me about BTC", {"symbol": "BTCUSDT"})
        assert out == []
    finally:
        planner.CONFIG.pop("AI_AGENT_NATIVE_PLANNING_ENABLED", None)


def test_plan_tool_calls_with_llm_never_raises_on_provider_error(monkeypatch):
    planner.CONFIG["AI_AGENT_NATIVE_PLANNING_ENABLED"] = True
    try:
        monkeypatch.setattr(
            planner,
            "resolve_ai_review_runtime",
            lambda cfg: {"provider": "grok", "api_key": "k", "selectedProvider": "grok"},
        )
        monkeypatch.setattr(planner, "get_ai_api_key", lambda cfg: "k")
        monkeypatch.setattr(planner, "get_ai_model", lambda *a, **kw: "grok-4.3")

        class _BadClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        raise RuntimeError("provider exploded")

        monkeypatch.setattr(planner, "create_ai_client", lambda *a, **kw: _BadClient())
        # Should not raise; returns empty list
        out = planner.plan_tool_calls_with_llm("tell me about BTC", {"symbol": "BTCUSDT"})
        assert out == []
    finally:
        planner.CONFIG.pop("AI_AGENT_NATIVE_PLANNING_ENABLED", None)


def test_read_only_registry_does_not_contain_mutating_tools():
    """Safety gate: the registry must never include mutating tool names."""
    mutating = {"execute_trade", "place_order", "delete_position", "approve_order", "mutate_config", "close_position"}
    assert not (mutating & planner.READ_ONLY_TOOL_NAMES)
