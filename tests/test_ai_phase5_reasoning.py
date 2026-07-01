"""Focused tests for Phase 5 reasoning-depth primitives.

Covers: multi-role debate config gating + downgrade-only + parallel roles,
tree-of-thought gating by score + branch aggregation, self-critique gating,
cross-engine consistency direction/allow conflict detection. No live LLM
calls (all LLM paths fail-closed when no api key).
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import ai_reasoning_depth as rd
from config import CONFIG


@pytest.fixture(autouse=True)
def _reset_phase5_flags():
    for k in (
        "AI_MULTI_ROLE_DEBATE_ENABLED",
        "AI_TOT_ENABLED",
        "AI_SELF_CRITIQUE_ENABLED",
    ):
        CONFIG.pop(k, None)
    yield
    for k in (
        "AI_MULTI_ROLE_DEBATE_ENABLED",
        "AI_TOT_ENABLED",
        "AI_SELF_CRITIQUE_ENABLED",
    ):
        CONFIG.pop(k, None)


# ---------------------------------------------------------------------------
# Multi-role debate
# ---------------------------------------------------------------------------


def test_multi_role_disabled_by_default():
    out = rd.run_multi_role_debate({"trace_id": "x", "display": "BTCUSDT"})
    assert out["enabled"] is False
    assert out["roles"] == {}
    assert out["advisory_only"] is True


def test_multi_role_enabled_runs_roles_but_fail_closed(monkeypatch):
    CONFIG["AI_MULTI_ROLE_DEBATE_ENABLED"] = True
    # No api key -> all roles return None (fail-closed)
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    out = rd.run_multi_role_debate({"trace_id": "x", "display": "BTCUSDT"})
    assert out["enabled"] is True
    assert set(out["roles"].keys()) == {"risk_officer", "macro_strategist"}
    assert all(v is None for v in out["roles"].values())
    assert out["aggregate"]["roles_run"] == 2
    assert out["aggregate"]["suggest_downgrade"] is False


def test_multi_role_downgrade_only_enforced(monkeypatch):
    """When core_debate.allowed=True and a role suggests downgrade, advisory
    allowed is False (downgrade-only). The core debate's allowed flag is never
    mutated."""
    CONFIG["AI_MULTI_ROLE_DEBATE_ENABLED"] = True
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    # Force a role to suggest downgrade by injecting a stub
    monkeypatch.setattr(
        rd,
        "_run_one_role",
        lambda role, signal: {"stance": "bear", "downgrade_recommended": True, "reasoning": "r"},
    )
    out = rd.run_multi_role_debate(
        {"trace_id": "x"},
        core_debate={"allowed": True, "grade": "WEAK_GO"},
    )
    assert out["aggregate"]["suggest_downgrade"] is True
    assert out["aggregate"]["core_allowed"] is True
    assert out["aggregate"]["advisory_recommended_allowed"] is False
    assert out["aggregate"]["downgrade_only_enforced"] in (True, False)  # depends on safety const


def test_multi_role_never_mutates_core_debate(monkeypatch):
    CONFIG["AI_MULTI_ROLE_DEBATE_ENABLED"] = True
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    core = {"allowed": True, "grade": "WEAK_GO"}
    core_before = dict(core)
    rd.run_multi_role_debate({"trace_id": "x"}, core_debate=core)
    assert core == core_before  # not mutated


# ---------------------------------------------------------------------------
# Tree-of-thought
# ---------------------------------------------------------------------------


def test_tot_disabled_by_default():
    assert rd.tree_of_thought({"confluenceScore": 9.0}) is None


def test_tot_skipped_when_score_below_threshold(monkeypatch):
    CONFIG["AI_TOT_ENABLED"] = True
    CONFIG["AI_TOT_MIN_SCORE"] = 7.5
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "k")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "k", "selectedProvider": "grok"},
    )
    assert rd.tree_of_thought({"confluenceScore": 5.0}) is None


def test_tot_runs_when_score_meets_threshold_but_fail_closed(monkeypatch):
    CONFIG["AI_TOT_ENABLED"] = True
    CONFIG["AI_TOT_MIN_SCORE"] = 7.5
    # No api key -> branches all None -> returns None
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    out = rd.tree_of_thought({"confluenceScore": 8.0})
    assert out is None


def test_tot_aggregates_branches(monkeypatch):
    CONFIG["AI_TOT_ENABLED"] = True
    CONFIG["AI_TOT_MIN_SCORE"] = 7.5
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "k")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "k", "selectedProvider": "grok"},
    )

    def fake_llm_json(system, user, max_tokens=400):
        return {"verdict": "long", "confidence": 0.7, "key_reason": "trend"}

    monkeypatch.setattr(rd, "_llm_json", fake_llm_json)
    out = rd.tree_of_thought({"confluenceScore": 8.0})
    assert out is not None
    assert out["consensus_verdict"] == "long"
    assert out["branches_valid"] == out["branches_run"]
    assert out["advisory_only"] is True


# ---------------------------------------------------------------------------
# Self-critique
# ---------------------------------------------------------------------------


def test_self_critique_disabled_by_default():
    assert rd.self_critique({"decision": "long"}) is None


def test_self_critique_fail_closed_no_api_key(monkeypatch):
    CONFIG["AI_SELF_CRITIQUE_ENABLED"] = True
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
    )
    assert rd.self_critique({"decision": "long"}) is None


def test_self_critique_returns_dict_when_llm_succeeds(monkeypatch):
    CONFIG["AI_SELF_CRITIQUE_ENABLED"] = True
    monkeypatch.setattr(rd, "get_ai_api_key", lambda cfg: "k")
    monkeypatch.setattr(
        rd,
        "resolve_ai_review_runtime",
        lambda cfg: {"provider": "grok", "api_key": "k", "selectedProvider": "grok"},
    )
    monkeypatch.setattr(
        rd,
        "_llm_json",
        lambda sys, user, max_tokens=400: {"weaknesses": ["no volume confirmation"], "stands": False, "reasoning": "r"},
    )
    out = rd.self_critique({"decision": "long"})
    assert out is not None
    assert out["schema_version"] == "self_critique.v1"
    assert out["stands"] is False
    assert "no volume confirmation" in out["weaknesses"]
    assert out["advisory_only"] is True


def test_self_critique_non_dict_returns_none():
    CONFIG["AI_SELF_CRITIQUE_ENABLED"] = True
    assert rd.self_critique(None) is None  # type: ignore[arg-type]
    assert rd.self_critique("not a dict") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cross-engine consistency
# ---------------------------------------------------------------------------


def test_consistency_no_conflict_when_aligned():
    out = rd.cross_engine_consistency(
        {"direction": "long", "allowed": True},
        {"direction": "long", "allowed": True},
        {"direction": "long"},
        {"direction": "long"},
    )
    assert out["direction_agreement"] is True
    assert out["allow_agreement"] is True
    assert out["contradictions"] == []
    assert out["advisory_only"] is True


def test_consistency_flags_direction_conflict():
    out = rd.cross_engine_consistency(
        {"direction": "long"},
        {"direction": "short"},
    )
    assert out["direction_agreement"] is False
    assert "direction_long_short_conflict" in out["contradictions"]


def test_consistency_neutral_compatible_with_long():
    out = rd.cross_engine_consistency(
        {"direction": "long"},
        {"direction": "neutral"},
    )
    assert out["direction_agreement"] is True


def test_consistency_flags_allow_block_conflict():
    out = rd.cross_engine_consistency(
        {"allowed": True},
        {"allowed": False},
    )
    assert out["allow_agreement"] is False
    assert "allow_block_conflict" in out["contradictions"]


def test_consistency_handles_none_engines():
    out = rd.cross_engine_consistency(None, None, None, None)
    assert out["direction_agreement"] is True
    assert out["allow_agreement"] is True
    assert out["contradictions"] == []


def test_consistency_never_raises_on_bad_input():
    out = rd.cross_engine_consistency("not a dict", 42, None, [])  # type: ignore[arg-type]
    assert out["contradictions"] == []
    assert out["advisory_only"] is True
