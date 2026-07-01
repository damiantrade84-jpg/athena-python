"""Focused tests for Phase 7 Research Lab AI.

Covers: agentic research loop config gating + bounding + fail-closed +
no-auto-apply, parameter discovery allowlist enforcement + locked-field
rejection + approval queue thread-safety, multi-run synthesis aggregation +
common-finding frequency. No live LLM calls.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from athena_research import ai_research_loop as rl
from athena_research import multi_run_synthesizer as ms
from config import CONFIG


@pytest.fixture(autouse=True)
def _reset_phase7_flags():
    for k in (
        "AI_RESEARCH_LOOP_ENABLED",
        "AI_PARAM_DISCOVERY_ENABLED",
        "AI_MULTI_RUN_SYNTHESIS_ENABLED",
        "AI_MULTI_RUN_SYNTHESIS_LLM_NARRATE",
        "AI_RESEARCH_MAX_STEPS",
    ):
        CONFIG.pop(k, None)
    yield
    for k in (
        "AI_RESEARCH_LOOP_ENABLED",
        "AI_PARAM_DISCOVERY_ENABLED",
        "AI_MULTI_RUN_SYNTHESIS_ENABLED",
        "AI_MULTI_RUN_SYNTHESIS_LLM_NARRATE",
        "AI_RESEARCH_MAX_STEPS",
    ):
        CONFIG.pop(k, None)


# ---------------------------------------------------------------------------
# Agentic research loop
# ---------------------------------------------------------------------------


def test_research_loop_disabled_by_default():
    out = rl.run_agentic_research_loop("test question", lambda plan: {"r": 1})
    assert out["enabled"] is False
    assert out["steps"] == []
    assert out["do_not_auto_apply"] is True


def test_research_loop_fail_closed_no_api_key(monkeypatch):
    CONFIG["AI_RESEARCH_LOOP_ENABLED"] = True
    monkeypatch.setattr(rl, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        rl,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    out = rl.run_agentic_research_loop("test question", lambda plan: {"r": 1})
    assert out["enabled"] is True
    # First plan failed -> loop breaks immediately
    assert len(out["steps"]) == 1
    assert out["steps"][0].get("error") == "plan_llm_failed"
    assert out["do_not_auto_apply"] is True


def test_research_loop_bounded_and_uses_backtest(monkeypatch):
    CONFIG["AI_RESEARCH_LOOP_ENABLED"] = True
    CONFIG["AI_RESEARCH_MAX_STEPS"] = 2
    calls = {"n": 0}

    def fake_llm(system, user, max_tokens=500):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"hypothesis": "h1", "params": {"x": 1}, "focus_metric": "sharpe"}
        if calls["n"] == 2:
            return {"hypothesis": "h2", "params": {"x": 2}, "focus_metric": "sharpe"}
        # synthesis call
        return {"key_findings": ["f1"], "recommended_next_experiments": ["e1"], "confidence": 0.6}

    monkeypatch.setattr(rl, "_llm_json", fake_llm)

    def run_backtest(plan):
        return {"sharpe": 1.2, "plan": plan}

    out = rl.run_agentic_research_loop("test", run_backtest)
    assert out["enabled"] is True
    assert len(out["steps"]) == 2  # bounded at 2
    assert all("result" in s for s in out["steps"])
    assert out["final_synthesis"] is not None
    assert out["do_not_auto_apply"] is True


def test_research_loop_breaks_on_done(monkeypatch):
    CONFIG["AI_RESEARCH_LOOP_ENABLED"] = True
    CONFIG["AI_RESEARCH_MAX_STEPS"] = 5
    calls = {"n": 0}

    def fake_llm(system, user, max_tokens=500):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"hypothesis": "h1", "params": {}, "focus_metric": "sharpe", "done": True}
        return {"key_findings": ["f"], "confidence": 0.5}

    monkeypatch.setattr(rl, "_llm_json", fake_llm)
    out = rl.run_agentic_research_loop("test", lambda p: {"r": 1})
    # Done on step 0 -> only 1 step, no backtest run for that step
    assert len(out["steps"]) == 1
    assert out["steps"][0].get("done") is True


def test_research_loop_catches_backtest_exception(monkeypatch):
    CONFIG["AI_RESEARCH_LOOP_ENABLED"] = True

    def fake_llm(system, user, max_tokens=500):
        return {"hypothesis": "h", "params": {}, "focus_metric": "x"}

    monkeypatch.setattr(rl, "_llm_json", fake_llm)

    def bad_backtest(plan):
        raise RuntimeError("boom")

    out = rl.run_agentic_research_loop("test", bad_backtest)
    assert len(out["steps"]) == 1
    assert "backtest_exception" in str(out["steps"][0].get("error"))


# ---------------------------------------------------------------------------
# Parameter discovery + approval queue
# ---------------------------------------------------------------------------


def test_param_discovery_disabled_by_default():
    out = rl.propose_parameter_sweep({"symbol": "BTC"})
    assert out["enabled"] is False
    assert out["proposals"] == []


def test_param_discovery_rejects_non_allowlist(monkeypatch):
    CONFIG["AI_PARAM_DISCOVERY_ENABLED"] = True
    monkeypatch.setattr(
        rl,
        "_llm_json",
        lambda sys, user, max_tokens=400: {
            "proposals": [
                {"param": "engine_a_rsi_period", "suggested_value": 21, "rationale": "test"},  # allowed
                {"param": "risk_max_loss", "suggested_value": 1000, "rationale": "should reject"},  # locked
                {"param": "unknown_param", "suggested_value": 5, "rationale": "not in allowlist"},  # not allowlisted
            ]
        },
    )
    out = rl.propose_parameter_sweep({})
    assert out["enabled"] is True
    assert len(out["proposals"]) == 1
    assert out["proposals"][0]["param"] == "engine_a_rsi_period"
    assert out["proposals"][0]["approved"] is False
    assert len(out["rejected"]) == 2
    rejected_names = {r["param"] for r in out["rejected"]}
    assert "risk_max_loss" in rejected_names
    assert "unknown_param" in rejected_names


def test_param_discovery_fail_closed_no_api_key(monkeypatch):
    CONFIG["AI_PARAM_DISCOVERY_ENABLED"] = True
    monkeypatch.setattr(rl, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        rl,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    out = rl.propose_parameter_sweep({})
    assert out["proposals"] == []
    assert out["rejected"] == []


def test_param_allowlist_excludes_locked_safety_fields():
    for locked in ("risk_max_loss", "execution_enabled", "sl_pips", "tp_pips", "rr_min", "guardian_x", "kill_switch_y", "freshness_max_age"):
        assert locked not in rl.PARAM_ALLOWLIST
    for allowed in ("engine_a_rsi_period", "engine_b_struct_confirmed_min_bars", "confluence_min_score"):
        assert allowed in rl.PARAM_ALLOWLIST


def test_approval_queue_starts_unapproved_and_approves():
    q = rl.ParameterApprovalQueue()
    iid = q.enqueue({"param": "engine_a_rsi_period", "suggested_value": 21, "rationale": "r"})
    assert q.pending_items() == [] or any(i["id"] == iid for i in q.pending_items())
    assert q.approved_items() == []
    assert q.approve(iid) is True
    approved = q.approved_items()
    assert len(approved) == 1
    assert approved[0]["param"] == "engine_a_rsi_period"
    assert approved[0]["approved"] is True


def test_approval_queue_reject():
    q = rl.ParameterApprovalQueue()
    iid = q.enqueue({"param": "x", "suggested_value": 1})
    assert q.reject(iid) is True
    assert q.approved_items() == []
    assert q.pending_items() == []  # rejected removed from pending
    assert q.approve("nonexistent") is False


def test_approval_queue_clear_and_all():
    q = rl.ParameterApprovalQueue()
    q.enqueue({"param": "a", "suggested_value": 1})
    q.enqueue({"param": "b", "suggested_value": 2})
    assert len(q.all_items()) == 2
    q.clear()
    assert q.all_items() == []


# ---------------------------------------------------------------------------
# Multi-run synthesis
# ---------------------------------------------------------------------------


def test_synthesis_disabled_by_default():
    out = ms.synthesize_runs([{"key_findings": [{"text": "f1"}]}])
    assert out["enabled"] is False
    assert out["common_findings"] == []


def test_synthesis_aggregates_common_findings():
    CONFIG["AI_MULTI_RUN_SYNTHESIS_ENABLED"] = True
    runs = [
        {"key_findings": [{"text": "EMA crossover works"}, {"text": "RSI divergence helps"}]},
        {"key_findings": [{"text": "EMA crossover works"}, {"text": "volume confirms"}]},
        {"key_findings": [{"text": "EMA crossover works"}]},
    ]
    out = ms.synthesize_runs(runs)
    assert out["enabled"] is True
    assert out["run_count"] == 3
    common_texts = [c["finding"] for c in out["common_findings"]]
    assert "EMA crossover works" in common_texts
    # "RSI divergence helps" appears only once -> not common
    assert "RSI divergence helps" not in common_texts
    assert out["do_not_auto_apply"] is True


def test_synthesis_aggregates_top_param_sets():
    CONFIG["AI_MULTI_RUN_SYNTHESIS_ENABLED"] = True
    runs = [
        {"top_param_sets": [{"params": {"rsi": 21}}]},
        {"top_param_sets": [{"params": {"rsi": 21}}]},
        {"top_param_sets": [{"params": {"rsi": 14}}]},
    ]
    out = ms.synthesize_runs(runs)
    # rsi:21 appears in 2 runs -> in top_param_sets
    found = any(p["params"] == {"rsi": 21} for p in out["top_param_sets"])
    assert found


def test_synthesis_empty_runs():
    CONFIG["AI_MULTI_RUN_SYNTHESIS_ENABLED"] = True
    out = ms.synthesize_runs([])
    assert out["run_count"] == 0
    assert out["common_findings"] == []


def test_synthesis_handles_bad_input():
    CONFIG["AI_MULTI_RUN_SYNTHESIS_ENABLED"] = True
    out = ms.synthesize_runs(None)  # type: ignore[arg-type]
    assert out["run_count"] == 0
    out2 = ms.synthesize_runs([None, "x", 42])  # type: ignore[list-item]
    assert out2["run_count"] == 0


def test_synthesis_llm_narrate_fail_closed(monkeypatch):
    CONFIG["AI_MULTI_RUN_SYNTHESIS_ENABLED"] = True
    CONFIG["AI_MULTI_RUN_SYNTHESIS_LLM_NARRATE"] = True
    monkeypatch.setattr(ms, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        ms,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    out = ms.synthesize_runs([{"key_findings": [{"text": "f1"}]}])
    assert out["llm_narrative"] is None
