from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena_research.commodity_data_audit.consolidated_gap_review import (
    GapGate,
    REVIEW_SCHEMA_VERSION,
    ResidualGapClass,
    ResidualGapEvidence,
    build_consolidated_payload,
    classify_residual_event,
    evaluate_gap_gate,
    family_policy_for_symbol,
    write_versioned_review_artifact,
)
from athena_research.commodity_data_audit.freeze_store import FreezeStoreError
from athena_research.commodity_data_audit.gap_embargo import (
    ProviderGapInterval,
    StrategyEmbargoContract,
    build_gap_exclusion_mask,
)


UTC = timezone.utc


def _ts(day: int, hour: int = 0) -> datetime:
    return datetime(2025, 1, day, hour, tzinfo=UTC)


def _evidence(**overrides) -> ResidualGapEvidence:
    base = {
        "event_id": "evt",
        "canonical_symbol": "WTI Oil",
        "prev_bar_timestamp": _ts(1),
        "next_bar_timestamp": _ts(2),
        "missing_timestamps": (_ts(1, 4),),
        "in_reliable_era": True,
        "recognized_holiday": True,
        "session_closure_pattern": True,
        "subject_refetch_empty": True,
        "d1_absent": True,
        "chunk_integrity": True,
        "primary_peer_shared_absence": True,
    }
    base.update(overrides)
    return ResidualGapEvidence(**base)


def test_nat_gas_holiday_can_close_without_same_family_peer():
    evidence = _evidence(
        canonical_symbol="Nat Gas",
        primary_peer_shared_absence=False,
    )
    assert family_policy_for_symbol("Nat Gas").allow_independent_closure is True
    assert classify_residual_event(evidence) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


def test_wti_brent_peer_confirms_closure():
    assert family_policy_for_symbol("WTI Oil").primary_peers == ("Brent Oil",)
    assert classify_residual_event(_evidence()) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


def test_gasoline_requires_aggregated_wti_and_brent_absence_for_closure():
    policy = family_policy_for_symbol("Gasoline")
    assert policy.primary_peers == ("WTI Oil", "Brent Oil")
    evidence = _evidence(canonical_symbol="Gasoline", primary_peer_shared_absence=True)
    assert classify_residual_event(evidence) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


def test_peer_evidence_cannot_override_corruption_or_acquisition_defect():
    corrupt = _evidence(corruption_reasons=("scale_or_decimal_shift",))
    defect = _evidence(acquisition_defect=True)
    assert classify_residual_event(corrupt) == ResidualGapClass.DATA_CORRUPTION
    assert classify_residual_event(defect) == ResidualGapClass.ACQUISITION_DEFECT


def test_conflicting_closure_and_provider_evidence_is_unresolved():
    evidence = _evidence(primary_peer_traded=True)
    assert classify_residual_event(evidence) == ResidualGapClass.UNRESOLVED


def _expected(count: int, start: datetime | None = None) -> list[datetime]:
    origin = start or datetime(2024, 1, 1, tzinfo=UTC)
    return [origin + timedelta(hours=4 * index) for index in range(count)]


def test_isolated_provider_gap_strictly_below_half_percent_clears_with_embargo():
    expected = _expected(1000)
    event = _evidence(
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=tuple(expected[100:102]),
        prev_bar_timestamp=expected[99],
        next_bar_timestamp=expected[102],
    )
    result = evaluate_gap_gate([event], expected)
    assert result.gate == GapGate.CLEAR_WITH_GAP_EMBARGO
    assert result.metrics.missing_numerator == 2
    assert result.metrics.expected_denominator == 1000
    assert result.metrics.missing_percentage_decimal == "0.200"


def test_half_percent_is_not_admissible():
    expected = _expected(1000)
    event = _evidence(
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=tuple(expected[100:105]),
    )
    assert evaluate_gap_gate([event], expected).gate == GapGate.BLOCKED_PROVIDER_DEGRADATION


def test_clustered_or_long_provider_gaps_remain_blocked():
    expected = _expected(3000)
    long_event = _evidence(
        event_id="long",
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=tuple(expected[100:107]),
    )
    first = _evidence(
        event_id="first",
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=(expected[500],),
        prev_bar_timestamp=expected[499],
        next_bar_timestamp=expected[501],
    )
    second = _evidence(
        event_id="second",
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=(expected[530],),
        prev_bar_timestamp=expected[529],
        next_bar_timestamp=expected[531],
    )
    assert evaluate_gap_gate([long_event], expected).gate == GapGate.BLOCKED_PROVIDER_DEGRADATION
    assert evaluate_gap_gate([first, second], expected).gate == GapGate.BLOCKED_PROVIDER_DEGRADATION


def test_rolling_ninety_day_missing_rate_at_two_percent_remains_blocked():
    expected = _expected(5000)
    first = _evidence(
        event_id="first_rolling",
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=tuple(expected[100:106]),
        prev_bar_timestamp=expected[99],
        next_bar_timestamp=expected[106],
    )
    second = _evidence(
        event_id="second_rolling",
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=tuple(expected[280:286]),
        prev_bar_timestamp=expected[279],
        next_bar_timestamp=expected[286],
    )
    result = evaluate_gap_gate([first, second], expected)
    assert float(result.metrics.minimum_event_separation_days) >= 20
    assert float(result.metrics.maximum_rolling_90d_missing_percentage) >= 2.0
    assert result.gate == GapGate.BLOCKED_PROVIDER_DEGRADATION


def test_corruption_and_unresolved_always_block():
    expected = _expected(1000)
    corrupt = _evidence(corruption_reasons=("non_finite_price",))
    unresolved = _evidence(
        recognized_holiday=False,
        session_closure_pattern=False,
        subject_refetch_empty=False,
        d1_absent=False,
        primary_peer_shared_absence=False,
    )
    assert evaluate_gap_gate([corrupt], expected).gate == GapGate.BLOCKED_CORRUPTION
    assert evaluate_gap_gate([unresolved], expected).gate == GapGate.BLOCKED_UNRESOLVED


def test_embargo_contract_has_no_defaults_and_validates_values():
    with pytest.raises(TypeError):
        StrategyEmbargoContract()
    with pytest.raises(ValueError):
        StrategyEmbargoContract(0, 1, 1, 1)
    contract = StrategyEmbargoContract(10, 3, 4, 2)
    assert contract.version == "commodity_gap_embargo_contract_v1"


def test_feature_label_and_rewarm_windows_crossing_gap_are_excluded():
    gap = ProviderGapInterval(
        event_id="gap",
        prev_bar_timestamp=_ts(10, 0),
        next_bar_timestamp=_ts(10, 12),
        missing_timestamps=(_ts(10, 4), _ts(10, 8)),
    )
    candidates = [_ts(10, 0), _ts(10, 12), _ts(11, 0), _ts(15, 0)]
    result = build_gap_exclusion_mask(
        candidates,
        [gap],
        StrategyEmbargoContract(2, 4, 2, 1),
        {"symbol": "WTI Oil", "review_hash": "abc"},
    )
    assert result.allowed == (False, False, False, True)
    assert "label_or_holding_crosses_gap" in result.reasons[0]
    assert "feature_lookback_crosses_gap" in result.reasons[1]
    assert "post_gap_rewarm" in result.reasons[2]
    assert len(result.run_hash) == 64


def test_mask_generation_requires_contract_and_does_not_interpolate():
    candidates = [_ts(1), _ts(2)]
    before = tuple(candidates)
    with pytest.raises(ValueError, match="explicit StrategyEmbargoContract"):
        build_gap_exclusion_mask(candidates, [], None, {})
    assert tuple(candidates) == before


def test_versioned_artifact_is_immutable_and_exports_each_event_once():
    scratch = Path(__file__).resolve().parent / f"_tmp_consolidated_gap_{uuid.uuid4().hex}"
    scratch.mkdir()
    try:
        events = [_evidence(event_id="a"), _evidence(event_id="b")]
        payload = {
            "schema": REVIEW_SCHEMA_VERSION,
            "events": [event.to_dict() for event in events],
        }
        path = scratch / "review.json"
        first_hash = write_versioned_review_artifact(path, payload)
        assert len(first_hash) == 64
        assert [row["event_id"] for row in json.loads(path.read_text())["events"]] == ["a", "b"]
        write_versioned_review_artifact(path, payload)
        with pytest.raises(FreezeStoreError):
            write_versioned_review_artifact(path, {**payload, "events": []})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_consolidated_payload_has_one_classification_per_event_and_no_strategy_mask():
    expected = _expected(1000)
    closure = _evidence(event_id="closure")
    provider = _evidence(
        event_id="provider",
        recognized_holiday=False,
        primary_peer_shared_absence=False,
        provider_history_absence=True,
        missing_timestamps=(expected[100],),
        prev_bar_timestamp=expected[99],
        next_bar_timestamp=expected[101],
    )
    payload = build_consolidated_payload(
        canonical_symbol="WTI Oil",
        terminal_symbol="SpotCrude",
        events=[closure, provider],
        expected_timestamps=expected,
        reliable_start="2024-01-01",
        usable_start="2024-06-01",
        source_hashes={"raw_content_hash": "raw", "chunk_set_hash": "chunks"},
    )
    assert payload["event_count"] == 2
    assert [row["event_id"] for row in payload["events"]] == ["closure", "provider"]
    assert all(row["classification"] for row in payload["events"])
    assert payload["gate"] == GapGate.CLEAR_WITH_GAP_EMBARGO.value
    assert payload["candidate_mask_status"] == "BLOCKED_MISSING_STRATEGY_EMBARGO_CONTRACT"
