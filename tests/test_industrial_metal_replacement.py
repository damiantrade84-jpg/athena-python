from __future__ import annotations

from datetime import datetime, timezone

from athena_research.commodity_data_audit.consolidated_gap_review import (
    ResidualGapClass,
    ResidualGapEvidence,
    classify_residual_event,
    family_policy_for_symbol,
)
from athena_research.commodity_data_audit.freeze_registry import (
    INDUSTRIAL_METAL_CANDIDATE_ORDER,
    PHASE_1_CANONICAL_SYMBOLS,
    PHASE_1_MT5_TERMINAL_ALIASES,
    resolve_phase1_mt5_symbol,
)
from athena_research.commodity_data_audit.registry import INDEPENDENT_CLUSTERS


def _copper_evidence(**overrides) -> ResidualGapEvidence:
    base = {
        "event_id": "copper-gap",
        "canonical_symbol": "Copper",
        "prev_bar_timestamp": datetime(2025, 12, 24, 20, tzinfo=timezone.utc),
        "next_bar_timestamp": datetime(2025, 12, 26, 0, tzinfo=timezone.utc),
        "missing_timestamps": (),
        "in_reliable_era": True,
        "recognized_holiday": True,
        "session_closure_pattern": True,
        "subject_refetch_empty": True,
        "d1_absent": True,
        "chunk_integrity": True,
    }
    base.update(overrides)
    return ResidualGapEvidence(**base)


def test_frozen_candidate_order_and_copper_registry_contract():
    assert INDUSTRIAL_METAL_CANDIDATE_ORDER == ("Copper", "Aluminium", "Nickel", "Zinc")
    assert "Copper" in PHASE_1_CANONICAL_SYMBOLS
    assert PHASE_1_MT5_TERMINAL_ALIASES["Copper"] == "Copper"
    assert resolve_phase1_mt5_symbol("Copper") == "Copper"
    assert INDEPENDENT_CLUSTERS["industrial_copper"] == "Copper"


def test_industrial_policy_is_not_automatically_independent():
    policy = family_policy_for_symbol("Copper")
    assert policy.family == "industrial_metals"
    assert policy.allow_independent_closure is False
    assert policy.independent_closure_requires_full_chain is True


def test_industrial_independent_closure_requires_complete_chain():
    assert classify_residual_event(_copper_evidence()) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE
    assert classify_residual_event(
        _copper_evidence(d1_absent=False)
    ) == ResidualGapClass.UNRESOLVED
    assert classify_residual_event(
        _copper_evidence(primary_peer_traded=True)
    ) == ResidualGapClass.UNRESOLVED
