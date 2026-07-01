"""Focused tests for Phase 4 dormant-surface activation.

Covers: strategist LLM narrative config gating + fail-closed + additive
field, ASE narrator config gating + fail-closed + non-mutation + advisory
flag. No live LLM calls.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import ai_strategist as strat
import athena_ase.ai_narrator as narrator


# ---------------------------------------------------------------------------
# Strategist LLM narrative
# ---------------------------------------------------------------------------


def test_strategist_llm_disabled_by_default():
    from config import CONFIG

    CONFIG.pop("AI_STRATEGIST_LLM_ENABLED", None)
    assert strat._strategist_llm_enabled() is False


def test_strategist_llm_enabled_when_configured():
    from config import CONFIG

    CONFIG["AI_STRATEGIST_LLM_ENABLED"] = True
    try:
        assert strat._strategist_llm_enabled() is True
    finally:
        CONFIG.pop("AI_STRATEGIST_LLM_ENABLED", None)


def test_strategist_llm_narrative_returns_none_when_disabled():
    from config import CONFIG

    CONFIG.pop("AI_STRATEGIST_LLM_ENABLED", None)
    assert strat._strategist_llm_narrative("morning_brief", {"a": 1}) is None


def test_strategist_llm_narrative_fail_closed_no_api_key(monkeypatch):
    from config import CONFIG

    CONFIG["AI_STRATEGIST_LLM_ENABLED"] = True
    try:
        import config as cfgmod

        monkeypatch.setattr(cfgmod, "get_ai_api_key", lambda cfg: "")
        monkeypatch.setattr(
            cfgmod,
            "resolve_ai_review_runtime",
            lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
        )
        assert strat._strategist_llm_narrative("morning_brief", {"a": 1}) is None
    finally:
        CONFIG.pop("AI_STRATEGIST_LLM_ENABLED", None)


def test_strategist_morning_brief_includes_llm_narrative_field():
    """Deterministic output shape preserved; llm_narrative field present (None when disabled)."""
    from config import CONFIG

    CONFIG.pop("AI_STRATEGIST_LLM_ENABLED", None)
    out = strat.strategist_morning_brief("all")
    assert "llm_narrative" in out
    assert out["llm_narrative"] is None
    # Deterministic fields unchanged
    assert out["schema_version"] == "strategist_brief.v1"
    assert "advisory" in out.get("full_brief", "").lower()


def test_strategist_pre_trade_check_includes_llm_narrative_field():
    from config import CONFIG

    CONFIG.pop("AI_STRATEGIST_LLM_ENABLED", None)
    out = strat.strategist_pre_trade_check({})
    assert "llm_narrative" in out
    assert out["llm_narrative"] is None
    assert out["execution_allowed"] is False
    assert out["advisory_only"] is True


def test_strategist_weekly_retrospective_includes_llm_narrative_field():
    from config import CONFIG

    CONFIG.pop("AI_STRATEGIST_LLM_ENABLED", None)
    out = strat.weekly_strategy_retrospective(7)
    assert "llm_narrative" in out
    assert out["llm_narrative"] is None
    assert out["do_not_auto_apply"] is True


# ---------------------------------------------------------------------------
# ASE narrator
# ---------------------------------------------------------------------------


def test_ase_narrator_disabled_by_default():
    from config import CONFIG

    CONFIG.pop("AI_ASE_NARRATOR_ENABLED", None)
    assert narrator.is_narrator_enabled() is False


def test_ase_narrator_enabled_when_configured():
    from config import CONFIG

    CONFIG["AI_ASE_NARRATOR_ENABLED"] = True
    try:
        assert narrator.is_narrator_enabled() is True
    finally:
        CONFIG.pop("AI_ASE_NARRATOR_ENABLED", None)


def test_ase_narrator_returns_none_when_disabled():
    from config import CONFIG

    CONFIG.pop("AI_ASE_NARRATOR_ENABLED", None)
    out = narrator.narrate_ase_signal({"signal_id": "abc", "instrument": "BTCUSD"})
    assert out is None


def test_ase_narrator_returns_none_for_non_dict_signal():
    from config import CONFIG

    CONFIG["AI_ASE_NARRATOR_ENABLED"] = True
    try:
        assert narrator.narrate_ase_signal(None) is None  # type: ignore[arg-type]
        assert narrator.narrate_ase_signal("not a dict") is None  # type: ignore[arg-type]
    finally:
        CONFIG.pop("AI_ASE_NARRATOR_ENABLED", None)


def test_ase_narrator_fail_closed_no_api_key(monkeypatch):
    from config import CONFIG

    CONFIG["AI_ASE_NARRATOR_ENABLED"] = True
    try:
        import config as cfgmod

        monkeypatch.setattr(cfgmod, "get_ai_api_key", lambda cfg: "")
        monkeypatch.setattr(
            cfgmod,
            "resolve_ai_review_runtime",
            lambda cfg: {"provider": "grok", "api_key": "", "selectedProvider": "grok"},
        )
        out = narrator.narrate_ase_signal({"signal_id": "abc"})
        assert out is None
    finally:
        CONFIG.pop("AI_ASE_NARRATOR_ENABLED", None)


def test_ase_narrator_never_raises_on_provider_error(monkeypatch):
    from config import CONFIG

    CONFIG["AI_ASE_NARRATOR_ENABLED"] = True
    try:
        import config as cfgmod

        monkeypatch.setattr(cfgmod, "get_ai_api_key", lambda cfg: "k")
        monkeypatch.setattr(
            cfgmod,
            "resolve_ai_review_runtime",
            lambda cfg: {"provider": "grok", "api_key": "k", "selectedProvider": "grok"},
        )
        monkeypatch.setattr(cfgmod, "get_ai_model", lambda *a, **kw: "grok-4.3")

        class _BadClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        raise RuntimeError("provider exploded")

        monkeypatch.setattr(cfgmod, "create_ai_client", lambda *a, **kw: _BadClient())
        out = narrator.narrate_ase_signal({"signal_id": "abc"})
        assert out is None
    finally:
        CONFIG.pop("AI_ASE_NARRATOR_ENABLED", None)
