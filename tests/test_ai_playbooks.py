"""Tests for ai_playbooks trade skill layer."""

from __future__ import annotations

from ai_playbooks import (
    get_engine_a_playbook,
    get_engine_b_playbook,
    get_engine_d_scalp_playbook,
    render_playbook_prompt_block,
)
from ai_playbooks.contracts import PLAYBOOK_SCHEMA_VERSION, TRADE_SKILL_VERSION
from ai_playbooks.trade_skill_normalizer import normalize_trade_skill_output


def test_engine_d_playbook_exists_with_schema_version() -> None:
    pb = get_engine_d_scalp_playbook()
    assert pb["schemaVersion"] == PLAYBOOK_SCHEMA_VERSION
    assert pb["engine"] == "D"


def test_engine_d_playbook_contains_market_location_aggression() -> None:
    pb = get_engine_d_scalp_playbook()
    assert "marketStates" in pb
    assert "locationChecklist" in pb
    assert "aggressionChecklist" in pb
    assert "trending" in pb["marketStates"]


def test_engine_d_playbook_entry_models_include_no_trade() -> None:
    pb = get_engine_d_scalp_playbook()
    assert "NO_TRADE" in pb["entryModels"]


def test_engine_d_playbook_contains_invalidation_requirements() -> None:
    pb = get_engine_d_scalp_playbook()
    assert pb["invalidations"]
    assert any("invalidation" in str(x).lower() for x in pb["invalidations"])


def test_engine_d_playbook_states_m5_context_m1_execution() -> None:
    pb = get_engine_d_scalp_playbook()
    principles = " ".join(pb["principles"]).lower()
    assert "m5" in principles
    assert "m1" in principles
    assert pb["reviewOrder"] == (
        "Market State -> Location -> Aggression -> Entry Model -> Invalidation -> Decision"
    )


def test_engine_d_playbook_separates_watch_only_from_no_trade() -> None:
    pb = get_engine_d_scalp_playbook()
    principles = " ".join(pb["principles"])
    assert "Use WATCH_ONLY when state is unclear" in principles
    assert "Use NO_TRADE only for hard invalidation" in principles


def test_engine_a_and_b_playbooks_have_required_shape() -> None:
    for getter in (get_engine_a_playbook, get_engine_b_playbook):
        pb = getter()
        assert pb["schemaVersion"] == PLAYBOOK_SCHEMA_VERSION
        assert pb["principles"]
        assert pb["mustRejectIf"]
        assert pb["requiredOutputFields"]


def test_render_playbook_prompt_block_includes_review_order() -> None:
    text = render_playbook_prompt_block([get_engine_d_scalp_playbook()])
    assert "ATHENA TRADE PLAYBOOKS" in text
    assert "Market State -> Location -> Aggression" in text


def test_normalize_trade_skill_downgrades_entry_now_without_invalidation() -> None:
    out, warnings = normalize_trade_skill_output(
        {
            "tradeSkillVersion": TRADE_SKILL_VERSION,
            "reviewType": "engine_d_scalp",
            "decision": "ENTRY_NOW",
            "direction": "LONG",
            "confidence": 80,
            "marketState": "trending",
            "locationAssessment": "good location at VAL",
            "aggressionAssessment": "buying aggression confirms",
            "entryModel": "PULLBACK_TO_VALUE_REJECTION",
            "entryAllowedNow": True,
        },
        review_type="engine_d_scalp",
    )
    assert out["decision"] == "WATCH_ONLY"
    assert out["entryAllowedNow"] is False
    assert "invalidation_missing" in warnings


def test_normalize_trade_skill_engine_d_missing_fields() -> None:
    out, warnings = normalize_trade_skill_output(
        {
            "decision": "ENTRY_NOW",
            "direction": "LONG",
            "confidence": 70,
            "marketState": "trending",
        },
        review_type="engine_d_scalp",
    )
    assert out["entryAllowedNow"] is False
    assert "engine_d_required_fields_missing" in warnings


def test_normalize_trade_skill_engine_d_entry_now_requires_all_fields_even_with_invalidation() -> None:
    out, warnings = normalize_trade_skill_output(
        {
            "decision": "ENTRY_NOW",
            "direction": "LONG",
            "confidence": 70,
            "invalidationLevel": 64920.0,
            "invalidationReason": "Break below structure invalidates",
            "entryAllowedNow": True,
        },
        review_type="engine_d_scalp",
    )
    assert out["decision"] == "WATCH_ONLY"
    assert out["entryAllowedNow"] is False
    assert "engine_d_required_fields_missing" in warnings


def test_normalize_trade_skill_downgrades_no_trade_without_hard_reason() -> None:
    out, warnings = normalize_trade_skill_output(
        {
            "decision": "NO_TRADE",
            "direction": "LONG",
            "confidence": 55,
            "entryAllowedNow": False,
        },
        review_type="engine_a_chart",
    )
    assert out["decision"] == "WATCH_ONLY"
    assert out["entryAllowedNow"] is False
    assert "no_trade_without_hard_reason_downgraded" in warnings


def test_normalize_trade_skill_preserves_no_trade_with_hard_reason() -> None:
    out, warnings = normalize_trade_skill_output(
        {
            "decision": "NO_TRADE",
            "direction": "LONG",
            "confidence": 55,
            "noTradeReason": "Invalidated below daily demand",
            "entryAllowedNow": False,
        },
        review_type="engine_a_chart",
    )
    assert out["decision"] == "NO_TRADE"
    assert out["entryAllowedNow"] is False
    assert "no_trade_without_hard_reason_downgraded" not in warnings
