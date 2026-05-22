"""Tests for athena_ai.ai_review_schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from athena_ai.ai_review_schema import (
    NO_MATCH_SENTINEL,
    ClassifierAgreement,
    Direction,
    FinalVerdict,
    StrategyPlaybookVerdict,
    schema_metadata,
    validate_verdict,
)
from athena_ai.strategy_playbook_loader import loaded_playbook_ids


def _valid_payload(**overrides):
    base = {
        "final_verdict": "NO_TRADE",
        "matched_strategy_model": NO_MATCH_SENTINEL,
        "direction": "NONE",
        "location_quality": "UNKNOWN",
        "trigger_quality": "MISSING",
        "rr_quality": "UNKNOWN",
        "classifier_agreement": "AGREE",
        "plain_english_reason": "no setup",
    }
    base.update(overrides)
    return base


def test_minimal_valid_payload_round_trips() -> None:
    verdict = validate_verdict(_valid_payload())
    assert verdict.final_verdict == FinalVerdict.NO_TRADE.value
    assert verdict.matched_strategy_model == NO_MATCH_SENTINEL


def test_rejects_unknown_final_verdict() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_verdict(_valid_payload(final_verdict="MAYBE_TRADE"))
    assert "final_verdict" in str(exc_info.value)


def test_rejects_matched_strategy_model_not_in_loader() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_verdict(_valid_payload(matched_strategy_model="MADE_UP_MODEL"))
    msg = str(exc_info.value)
    assert "matched_strategy_model" in msg
    assert "MADE_UP_MODEL" in msg


def test_accepts_matched_strategy_model_from_loaded_ids() -> None:
    ids = loaded_playbook_ids()
    assert ids, "loader must have at least one playbook for this test"
    verdict = validate_verdict(
        _valid_payload(
            matched_strategy_model=ids[0],
            final_verdict="TRADE_VALID",
            direction="LONG",
            location_quality="HIGH",
            trigger_quality="CONFIRMED",
            rr_quality="GOOD",
        )
    )
    assert verdict.matched_strategy_model == ids[0]


def test_classifier_agreement_accepts_all_enum_values() -> None:
    for value in [e.value for e in ClassifierAgreement]:
        verdict = validate_verdict(_valid_payload(classifier_agreement=value))
        assert verdict.classifier_agreement == value


def test_classifier_agreement_rejects_free_text() -> None:
    with pytest.raises(ValidationError):
        validate_verdict(_valid_payload(classifier_agreement="kind_of_agree"))


def test_direction_enum_enforced() -> None:
    with pytest.raises(ValidationError):
        validate_verdict(_valid_payload(direction="up"))


def test_extras_allowed_so_ai_can_add_optional_fields() -> None:
    payload = _valid_payload()
    payload["model_specific_explainer"] = "ai-only annotation"
    payload["confidence_pct"] = 73
    verdict = validate_verdict(payload)
    # extras are preserved, not silently dropped
    dumped = verdict.model_dump()
    assert dumped.get("model_specific_explainer") == "ai-only annotation"


def test_schema_metadata_lists_loaded_playbook_ids_plus_sentinel() -> None:
    meta = schema_metadata()
    expected = set(loaded_playbook_ids()) | {NO_MATCH_SENTINEL}
    assert set(meta["matched_strategy_model_choices"]) == expected
    assert meta["enums"]["classifier_agreement"] == [e.value for e in ClassifierAgreement]


def test_roundtrip_classifier_agreement_override_added() -> None:
    payload = _valid_payload(
        classifier_agreement="OVERRIDE_ADDED",
        matched_strategy_model=loaded_playbook_ids()[0],
        final_verdict="WAIT_FOR_TRIGGER",
        direction="LONG",
        location_quality="MEDIUM",
        trigger_quality="PARTIAL",
        rr_quality="ACCEPTABLE",
    )
    verdict = validate_verdict(payload)
    assert verdict.classifier_agreement == "OVERRIDE_ADDED"
    # FIX 5 — survives a full json round trip
    serialized = verdict.model_dump()
    revived = StrategyPlaybookVerdict.model_validate(serialized)
    assert revived.classifier_agreement == "OVERRIDE_ADDED"


def test_invalidation_level_optional() -> None:
    verdict = validate_verdict(_valid_payload(invalidation_level=None))
    assert verdict.invalidation_level is None
    verdict2 = validate_verdict(_valid_payload(invalidation_level=1.2345))
    assert verdict2.invalidation_level == pytest.approx(1.2345)


def test_direction_enum_export_matches() -> None:
    # safety: enum values used by the API string contract are the literal upper-case names
    assert Direction.LONG.value == "LONG"
    assert Direction.NONE.value == "NONE"
