from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from athena_research.commodity_data_audit.freeze_store import hash_stable_json

EMBARGO_CONTRACT_VERSION = "commodity_gap_embargo_contract_v1"
H4_STEP = timedelta(hours=4)


@dataclass(frozen=True)
class StrategyEmbargoContract:
    max_feature_lookback_bars: int
    post_gap_rewarm_bars: int
    max_label_holding_bars: int
    max_entry_delay_bars: int
    version: str = field(default=EMBARGO_CONTRACT_VERSION, init=False)

    def __post_init__(self) -> None:
        values = (
            self.max_feature_lookback_bars,
            self.post_gap_rewarm_bars,
            self.max_label_holding_bars,
            self.max_entry_delay_bars,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("embargo contract values must be integers")
        if self.max_feature_lookback_bars <= 0:
            raise ValueError("max_feature_lookback_bars must be greater than zero")
        if any(value < 0 for value in values[1:]):
            raise ValueError("embargo contract window values cannot be negative")


@dataclass(frozen=True)
class ProviderGapInterval:
    event_id: str
    prev_bar_timestamp: datetime
    next_bar_timestamp: datetime
    missing_timestamps: tuple[datetime, ...]


@dataclass(frozen=True)
class GapExclusionMask:
    allowed: tuple[bool, ...]
    reasons: tuple[tuple[str, ...], ...]
    exclusion_intervals: tuple[dict[str, str], ...]
    run_hash: str


def _intersects(start: datetime, end: datetime, missing: tuple[datetime, ...]) -> bool:
    return any(start <= value <= end for value in missing)


def build_gap_exclusion_mask(
    candidate_timestamps: Iterable[datetime],
    provider_gaps: Iterable[ProviderGapInterval],
    contract: StrategyEmbargoContract | None,
    run_context: dict[str, Any],
) -> GapExclusionMask:
    if contract is None:
        raise ValueError("explicit StrategyEmbargoContract is required")
    candidates = tuple(candidate_timestamps)
    gaps = tuple(provider_gaps)
    allowed: list[bool] = []
    all_reasons: list[tuple[str, ...]] = []
    for candidate in candidates:
        reasons: list[str] = []
        feature_start = candidate - H4_STEP * contract.max_feature_lookback_bars
        future_end = candidate + H4_STEP * (
            contract.max_entry_delay_bars + contract.max_label_holding_bars
        )
        for gap in gaps:
            if _intersects(feature_start, candidate, gap.missing_timestamps):
                reasons.append("feature_lookback_crosses_gap")
            if _intersects(candidate, future_end, gap.missing_timestamps):
                reasons.append("label_or_holding_crosses_gap")
            rewarm_end = gap.next_bar_timestamp + H4_STEP * contract.post_gap_rewarm_bars
            if gap.next_bar_timestamp <= candidate < rewarm_end:
                reasons.append("post_gap_rewarm")
        unique = tuple(dict.fromkeys(reasons))
        allowed.append(not unique)
        all_reasons.append(unique)
    intervals = tuple(
        {
            "event_id": gap.event_id,
            "feature_embargo_start": (
                min(gap.missing_timestamps) - H4_STEP * contract.max_feature_lookback_bars
            ).isoformat(),
            "post_gap_rewarm_end": (
                gap.next_bar_timestamp + H4_STEP * contract.post_gap_rewarm_bars
            ).isoformat(),
            "label_embargo_end": (
                max(gap.missing_timestamps)
                + H4_STEP * (contract.max_entry_delay_bars + contract.max_label_holding_bars)
            ).isoformat(),
        }
        for gap in gaps
    )
    run_hash = hash_stable_json(
        {
            "contract": asdict(contract),
            "run_context": run_context,
            "provider_gaps": [
                {
                    "event_id": gap.event_id,
                    "prev": gap.prev_bar_timestamp.isoformat(),
                    "next": gap.next_bar_timestamp.isoformat(),
                    "missing": [value.isoformat() for value in gap.missing_timestamps],
                }
                for gap in gaps
            ],
        }
    )
    return GapExclusionMask(tuple(allowed), tuple(all_reasons), intervals, run_hash)
