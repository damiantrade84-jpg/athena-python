"""Pydantic schema for the AI strategy-playbook verdict.

Lives alongside — never replaces — the existing legacy verdict contract
(aiReviewSummary, engineAVerdictComparison). The new layer emits a single
additional top-level field on the AI response, validated by this model.

FIX 4: matched_strategy_model is validated against the live playbook id list
loaded from the YAML, with "NONE" added as the explicit no-match sentinel.
Free text is rejected at validation time, not at parse time.

FIX 5: classifier_agreement records whether the AI agreed with the
deterministic classifier, overrode it by adding a model, overrode it by
rejecting all suggested models, or had nothing to compare against.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from athena_ai.strategy_playbook_loader import loaded_playbook_ids


class FinalVerdict(str, Enum):
    TRADE_VALID = "TRADE_VALID"
    WAIT_FOR_TRIGGER = "WAIT_FOR_TRIGGER"
    NO_TRADE = "NO_TRADE"
    CONFLICT_REJECT = "CONFLICT_REJECT"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class LocationQuality(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class TriggerQuality(str, Enum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    INVALID = "INVALID"


class RRQuality(str, Enum):
    GOOD = "GOOD"
    ACCEPTABLE = "ACCEPTABLE"
    POOR = "POOR"
    UNKNOWN = "UNKNOWN"


class ClassifierAgreement(str, Enum):
    AGREE = "AGREE"
    OVERRIDE_ADDED = "OVERRIDE_ADDED"        # AI picked a model the classifier did not surface
    OVERRIDE_REJECTED = "OVERRIDE_REJECTED"  # AI rejected all classifier suggestions
    NA = "N/A"


NO_MATCH_SENTINEL = "NONE"


class StrategyPlaybookVerdict(BaseModel):
    """The AI's structured response when reviewing under the playbook layer.

    Extra fields are tolerated — the AI prompt may evolve and we should not
    reject a response because the model included an extra explanation field
    we didn't anticipate. Required fields are validated strictly.
    """

    model_config = ConfigDict(extra="allow", use_enum_values=True)

    final_verdict: FinalVerdict
    matched_strategy_model: str = Field(
        ...,
        description=(
            "ID of the matched playbook from the loaded YAML, or 'NONE' if no "
            "playbook applies. Free text is rejected."
        ),
    )
    direction: Direction
    market_state: str | None = None
    location_quality: LocationQuality
    trigger_quality: TriggerQuality
    invalidation_level: float | None = None
    target_logic: str | None = None
    rr_quality: RRQuality
    conflict_notes: list[str] = Field(default_factory=list)
    required_next_confirmation: list[str] = Field(default_factory=list)
    classifier_agreement: ClassifierAgreement
    plain_english_reason: str

    @field_validator("matched_strategy_model")
    @classmethod
    def _validate_against_loaded_playbooks(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("matched_strategy_model must be a non-empty string")
        normalized = value.strip()
        if normalized == NO_MATCH_SENTINEL:
            return normalized
        # FIX 4: refresh loader-driven id list at validation time so the schema
        # stays in sync if playbooks are edited without restarting the process.
        valid_ids = set(loaded_playbook_ids())
        if normalized not in valid_ids:
            raise ValueError(
                f"matched_strategy_model {normalized!r} is not a loaded playbook id; "
                f"expected one of {sorted(valid_ids)} or {NO_MATCH_SENTINEL!r}"
            )
        return normalized


def schema_metadata() -> dict[str, Any]:
    """Compact, AI-prompt-friendly description of the schema.

    Used by the payload builder to attach a schema hint to the prompt so the
    AI knows the exact verdict shape it must return.
    """
    return {
        "name": "StrategyPlaybookVerdict",
        "version": 1,
        "required_fields": [
            "final_verdict",
            "matched_strategy_model",
            "direction",
            "location_quality",
            "trigger_quality",
            "rr_quality",
            "classifier_agreement",
            "plain_english_reason",
        ],
        "enums": {
            "final_verdict": [e.value for e in FinalVerdict],
            "direction": [e.value for e in Direction],
            "location_quality": [e.value for e in LocationQuality],
            "trigger_quality": [e.value for e in TriggerQuality],
            "rr_quality": [e.value for e in RRQuality],
            "classifier_agreement": [e.value for e in ClassifierAgreement],
        },
        "matched_strategy_model_choices": list(loaded_playbook_ids()) + [NO_MATCH_SENTINEL],
        "no_match_sentinel": NO_MATCH_SENTINEL,
    }


def validate_verdict(payload: dict[str, Any]) -> StrategyPlaybookVerdict:
    """Helper to validate a raw dict (typically parsed from the AI JSON)."""
    return StrategyPlaybookVerdict.model_validate(payload)
