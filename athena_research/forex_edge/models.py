from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class ForexEdgeError(RuntimeError):
    """Base error for the standalone research package."""


class BlockedDataError(ForexEdgeError):
    """Required evidence is missing or fails a pre-registered quality gate."""


class InvalidResearchInputError(ForexEdgeError, ValueError):
    """Configuration or source schema is invalid."""


class StudyStatus(str, Enum):
    BLOCKED_DATA = "BLOCKED_DATA"
    COMPLETED_NO_EDGE = "COMPLETED_NO_EDGE"
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    BLOCKED_DATA = "BLOCKED_DATA"


class ReasonCode(str, Enum):
    MISSING_SERIES = "MISSING_SERIES"
    MISSING_PAIR = "MISSING_PAIR"
    MISSING_CURRENCY = "MISSING_CURRENCY"
    MISSING_COT_MAPPING = "MISSING_COT_MAPPING"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INSUFFICIENT_UNIVERSE_BREADTH = "INSUFFICIENT_UNIVERSE_BREADTH"
    STALE_DATA = "STALE_DATA"
    UNVERIFIED_AVAILABILITY = "UNVERIFIED_AVAILABILITY"
    AMBIGUOUS_UNIT = "AMBIGUOUS_UNIT"
    AMBIGUOUS_TIMEZONE = "AMBIGUOUS_TIMEZONE"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    NONPOSITIVE_PRICE = "NONPOSITIVE_PRICE"
    CROSSED_QUOTE = "CROSSED_QUOTE"
    MIDPOINT_ONLY = "MIDPOINT_ONLY"
    EXCESSIVE_GAPS = "EXCESSIVE_GAPS"
    NO_EXECUTABLE_QUOTE = "NO_EXECUTABLE_QUOTE"
    PROXY_CARRY_ONLY = "PROXY_CARRY_ONLY"
    UNVERIFIED_REVISION_HISTORY = "UNVERIFIED_REVISION_HISTORY"
    PROXY_TRANSACTION_COSTS = "PROXY_TRANSACTION_COSTS"
    PBO_UNAVAILABLE = "PBO_UNAVAILABLE"
    PINNED_MANIFEST_REQUIRED = "PINNED_MANIFEST_REQUIRED"
    UNREGISTERED_QUALITY_LIMIT = "UNREGISTERED_QUALITY_LIMIT"


class EvidenceFlag(str, Enum):
    NON_PROMOTABLE_PROXY_CARRY = "NON_PROMOTABLE_PROXY_CARRY"
    NON_PROMOTABLE_REVISION_RISK = "NON_PROMOTABLE_REVISION_RISK"
    PROXY_TRANSACTION_COSTS = "PROXY_TRANSACTION_COSTS"
    PBO_UNAVAILABLE = "PBO_UNAVAILABLE"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _dataclass_dict(instance: Any) -> dict[str, Any]:
    return {
        item.name: _json_safe(getattr(instance, item.name))
        for item in fields(instance)
    }


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    status: EligibilityStatus
    reason_codes: tuple[ReasonCode, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class DatasetManifest:
    schema_version: int
    dataset: str
    key: str
    version: str
    source: str
    source_url: str
    retrieved_at: str
    actual_start: str | None
    actual_end: str | None
    row_count: int
    raw_hashes: tuple[str, ...]
    partition_hashes: Mapping[str, str]
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    study: str
    configuration: str
    cost_multiplier: float
    returns_hash: str
    n_observations: int

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class StudyResult:
    study_status: StudyStatus
    production_eligible: bool
    evidence_flags: tuple[EvidenceFlag, ...]
    metrics: Mapping[str, Any]
    eligibility: EligibilityResult
    trials: tuple[TrialRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_status": self.study_status.value,
            "production_eligible": self.production_eligible,
            "evidence_flags": [flag.value for flag in self.evidence_flags],
            "metrics": _json_safe(self.metrics),
            "eligibility": self.eligibility.to_dict(),
            "trials": [trial.to_dict() for trial in self.trials],
        }
