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
    assert "session context" in pb["reviewOrder"].lower() or "Session Context" in pb["reviewOrder"]
    assert "sessioncontext" in principles


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


def test_engine_a_playbook_uses_v3_factor_score_keys() -> None:
    pb = get_engine_a_playbook()
    usage = " ".join(pb["indicatorUsage"].keys())
    assert "factorScores.trend" in usage
    assert "factorScores.momentum" in usage
    assert "factorScores.ortho.location" in usage
    assert "factorDiagnostics.components" in usage
    assert "factorDiagnostics.minDirectionalFailed" in usage
    assert "diagnostics.trendScore" not in pb["indicatorUsage"]


def test_engine_playbooks_surface_resolved_timeframe_contract() -> None:
    a_pb = get_engine_a_playbook()
    b_pb = get_engine_b_playbook()

    assert "setupTf" in a_pb["timeframeContract"]["authoritativeFields"]
    assert "server" in b_pb["timeframeAuthority"].lower()
    assert "biasTf" in b_pb["timeframeContract"]["authority"]


def test_engine_b_playbook_documents_ob_fvg_and_gates() -> None:
    pb = get_engine_b_playbook()
    usage = " ".join(pb["structureUsage"].keys())
    assert "obAtZone" in usage
    assert "fvgOverlap" in usage
    assert "structureOk" in usage
    assert "ORDER_BLOCK_REJECTION" in pb["entryModels"]
    assert "FVG_FILL_CONTINUATION" in pb["entryModels"]
    assert "BAG_CONTINUATION" in pb["entryModels"]
    assert "bagState" in usage


def test_engine_b_playbook_uses_resolved_policy_timeframes() -> None:
    pb = get_engine_b_playbook()
    assert "timeframeMatrix" not in pb
    macro_contract = pb["timeframeContract"]["macroSwing"]
    assert "biasTf" in macro_contract
    assert "not hardcoded to H4" in macro_contract


def test_engine_b_playbook_zone_retest_principles() -> None:
    pb = get_engine_b_playbook()
    principles = " ".join(pb["principles"]).lower()
    assert "zone-retest" in principles
    assert "reflex" in principles
    assert "locationok" in principles


def test_engine_b_playbook_must_reject_if_qualified_not_blunt() -> None:
    pb = get_engine_b_playbook()
    rejects = pb["mustRejectIf"]
    assert "Longing directly into supply or resistance zone." not in rejects
    assert "Shorting directly into demand or support zone." not in rejects
    assert any("locationOk=false" in r for r in rejects)


def test_engine_b_playbook_strategy_mapping() -> None:
    pb = get_engine_b_playbook()
    mapping = pb["strategyMapping"]
    assert "ORDER_BLOCK_REJECTION" in mapping
    assert "obAtZone" in mapping["ORDER_BLOCK_REJECTION"]


def test_engine_b_rendered_prompt_uses_resolved_policy_roles() -> None:
    text = render_playbook_prompt_block([get_engine_b_playbook()])
    assert "server-supplied biasTf" in text
    assert "hardcoded H4" in text
    assert "zone-retest" in text.lower() or "zone-retest engine" in text.lower()


def test_compact_render_includes_timeframe_contract() -> None:
    """Compact injection must deliver the playbook's role contract, not drop it."""
    a_text = render_playbook_prompt_block([get_engine_a_playbook()], compact=True)
    b_text = render_playbook_prompt_block([get_engine_b_playbook()], compact=True)
    assert '"timeframeContract"' in a_text
    assert "setupTf" in a_text
    assert '"timeframeContract"' in b_text
    assert '"timeframeAuthority"' in b_text
    assert "not hardcoded to H4" in b_text


def test_compact_render_does_not_slice_engine_b_rules() -> None:
    pb = get_engine_b_playbook()
    text = render_playbook_prompt_block([pb], compact=True)
    assert "never invent BOS/OB/FVG/BAG" in text
    assert "must never mutate or override" in text
    assert "Structure signal after invalidation must be rejected" in text
    assert "doNotRejectIf" in text
    assert "Do not reject solely because RR1" in text
    assert "Do not reject solely because RR1" not in "".join(pb["mustRejectIf"])


def test_compact_render_includes_engine_d_near_miss_blocks() -> None:
    text = render_playbook_prompt_block([get_engine_d_scalp_playbook()], compact=True)
    assert "sessionRegimeSwitch" in text
    assert "effortVsResult" in text
    assert "trappedTraderLogic" in text
    assert "pocMagnet" in text
    assert "casinoTimeDegradation" in text


def test_render_playbook_prompt_block_includes_review_order() -> None:
    text = render_playbook_prompt_block([get_engine_d_scalp_playbook()])
    assert "ATHENA TRADE PLAYBOOKS" in text
    assert "Session Context" in text
    assert "sessionModelSwitch" in text


def test_ai_playbook_contains_session_model_switch() -> None:
    pb = get_engine_d_scalp_playbook()
    assert "sessionModelSwitch" in pb
    switch = pb["sessionModelSwitch"]
    assert "modelA" in switch
    assert "modelB" in switch
    assert switch["modelA"]["name"] == "NY_TREND_SQUEEZE"
    assert switch["modelB"]["name"] == "LONDON_MEAN_REVERSION"


def test_ai_prompt_contains_effort_vs_result_decision_table() -> None:
    from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt

    ctx = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "scalpSetup": {},
        "marketLocation": {},
        "sourceContract": {},
        "signal": {"type": "crypto"},
        "scan_timestamp": "2026-01-15T18:00:00+00:00",
    }
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "HIGH_EFFORT_NO_RESULT" in prompt
    assert "effortVsResultClassification" in prompt


def test_ai_prompt_contains_trapped_trader_squeeze_logic() -> None:
    from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt

    ctx = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "scalpSetup": {},
        "marketLocation": {},
        "sourceContract": {},
        "signal": {"type": "crypto"},
        "scan_timestamp": "2026-01-15T18:00:00+00:00",
    }
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "squeezeFuelScore" in prompt
    assert "trappedTraderAssessment" in prompt


def test_ai_prompt_contains_poc_target_magnet_rules() -> None:
    from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt

    ctx = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "scalpSetup": {},
        "marketLocation": {},
        "sourceContract": {},
        "signal": {"type": "crypto"},
        "scan_timestamp": "2026-01-15T18:00:00+00:00",
    }
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "POC TARGET MAGNET" in prompt
    assert "POC is primary structural target" in prompt


def test_ai_prompt_contains_casino_time_degradation() -> None:
    from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt

    ctx = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "scalpSetup": {},
        "marketLocation": {},
        "sourceContract": {},
        "signal": {"type": "crypto"},
        "scan_timestamp": "2026-01-15T18:00:00+00:00",
    }
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "CASINO / TIME DEGRADATION" in prompt
    assert "NY_MIDDAY" in prompt
    assert "DOWNGRADE" in prompt


def test_ai_prompt_contains_tight_structural_stop_geometry() -> None:
    from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt

    ctx = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "scalpSetup": {},
        "marketLocation": {},
        "sourceContract": {},
        "signal": {"type": "crypto"},
        "scan_timestamp": "2026-01-15T18:00:00+00:00",
    }
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "stopPlacementValid" in prompt
    assert "STRUCTURAL STOP GEOMETRY" in prompt


def test_ai_output_schema_includes_effort_vs_result_trapped_target_invalidation_management() -> None:
    from ai_playbooks.trade_skill_normalizer import render_trade_skill_prompt_schema

    schema = render_trade_skill_prompt_schema("engine_d_scalp")
    for token in (
        "effortVsResultClassification",
        "trappedTraderAssessment",
        "targetLogic",
        "invalidationAssessment",
        "managementPlan",
        "aggressionClassification",
        "sessionQuality",
    ):
        assert token in schema


def test_sweep_reclaim_requires_structural_stop_behind_sweep() -> None:
    pb = get_engine_d_scalp_playbook()
    geometry = pb["structuralStopGeometry"]
    assert "SWEEP_AND_RECLAIM" in geometry["byEntryModel"]
    assert "swept wick" in geometry["byEntryModel"]["SWEEP_AND_RECLAIM"].lower()

    from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt

    ctx = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "scalpSetup": {},
        "marketLocation": {},
        "sourceContract": {},
        "signal": {"type": "crypto"},
        "scan_timestamp": "2026-01-15T18:00:00+00:00",
    }
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "SWEEP_AND_RECLAIM" in prompt
    assert "swept wick" in prompt.lower()


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


def test_normalize_trade_skill_downgrade_overrides_legacy_take_valid() -> None:
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
            "human_action": "take",
            "verdict": "VALID",
        },
        review_type="engine_d_scalp",
    )
    assert out["decision"] == "WATCH_ONLY"
    assert out["entryAllowedNow"] is False
    assert out["human_action"] != "take"
    assert out["verdict"] != "VALID"
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


def test_normalize_trade_skill_passthrough_extended_engine_d_fields() -> None:
    out, _ = normalize_trade_skill_output(
        {
            "decision": "WAIT_FOR_PULLBACK",
            "direction": "LONG",
            "confidence": 70,
            "marketState": "balancing",
            "locationAssessment": "at VAL",
            "aggressionAssessment": "absorption",
            "entryModel": "SWEEP_AND_RECLAIM",
            "aggressionClassification": "ABSORPTION",
            "effortVsResultClassification": "HIGH_EFFORT_NO_RESULT",
            "trappedTraderAssessment": {
                "trappedSide": "SHORTS",
                "trapTrigger": "SWEEP_AND_RECLAIM",
                "squeezeFuelScore": 75,
                "explanation": "reclaim after sweep",
            },
            "targetLogic": {
                "primaryTargetType": "POC",
                "primaryTargetPrice": 100.0,
                "targetJustification": "mean reversion magnet",
                "structuralRR": 1.5,
            },
            "invalidationAssessment": {
                "structuralInvalidationLevel": 99.0,
                "proposedStopLevel": 98.5,
                "stopPlacementValid": True,
                "stopProblem": "NONE",
                "expectedBehavior": "IMMEDIATE_GREEN_OR_BAIL",
            },
            "sessionQuality": "CHOP_RISK",
            "sessionConvictionAdjustment": "DOWNGRADE",
        },
        review_type="engine_d_scalp",
    )
    assert out["aggressionClassification"] == "ABSORPTION"
    assert out["effortVsResultClassification"] == "HIGH_EFFORT_NO_RESULT"
    assert out["trappedTraderAssessment"]["squeezeFuelScore"] == 75
    assert out["targetLogic"]["primaryTargetType"] == "POC"
    assert out["invalidationAssessment"]["stopPlacementValid"] is True
    assert out["sessionQuality"] == "CHOP_RISK"


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


def test_engine_d_playbook_adjudication_sections() -> None:
    pb = get_engine_d_scalp_playbook()
    for key in (
        "sessionRegimeSwitch",
        "effortVsResult",
        "trappedTraderLogic",
        "pocMagnet",
        "casinoTimeDegradation",
        "structuralStopPrinciples",
    ):
        assert key in pb
        assert len(pb[key]) >= 2


def test_engine_a_b_playbooks_document_policy_provenance_fields():
    expected = {
        "resolved_profile",
        "profile_source",
        "symbol_override_applied",
        "score_group",
        "engine_overlay",
        "execution_mode",
        "m5_policy",
    }
    for getter in (get_engine_a_playbook, get_engine_b_playbook):
        contract = getter()["timeframeContract"]
        assert expected <= set(contract)
        # Existing keys are retained.
        assert contract
    a_contract = get_engine_a_playbook()["timeframeContract"]
    b_contract = get_engine_b_playbook()["timeframeContract"]
    assert "roleMapping" in a_contract
    assert "authoritativeFields" in a_contract
    assert "macroSwing" in b_contract
    assert "authority" in b_contract
