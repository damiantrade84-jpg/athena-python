from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from athena_research.commodity_data_audit.freeze_store import hash_stable_json, write_json_immutable

REVIEW_SCHEMA_VERSION = "commodity_h4_residual_gap_review_v4"
GATE_POLICY_VERSION = "commodity_h4_gap_admissibility_v1"
MAX_PROVIDER_GAP_BARS = 6
MIN_PROVIDER_GAP_SEPARATION_DAYS = 20
ROLLING_WINDOW_DAYS = 90
MAX_ROLLING_MISSING_PERCENT = Decimal("2.0")
MAX_OVERALL_MISSING_PERCENT = Decimal("0.5")


class ResidualGapClass(str, Enum):
    LEGITIMATE_MARKET_CLOSURE = "LEGITIMATE_MARKET_CLOSURE"
    ACQUISITION_DEFECT = "ACQUISITION_DEFECT"
    PROVIDER_HISTORY_GAP = "PROVIDER_HISTORY_GAP"
    HISTORICAL_INTRADAY_DEGRADATION = "HISTORICAL_INTRADAY_DEGRADATION"
    DATA_CORRUPTION = "DATA_CORRUPTION"
    UNRESOLVED = "UNRESOLVED"


class GapGate(str, Enum):
    CLEAR_ON_FREEZE = "CLEAR_ON_FREEZE"
    CLEAR_WITH_GAP_EMBARGO = "CLEAR_WITH_GAP_EMBARGO"
    BLOCKED_PROVIDER_DEGRADATION = "BLOCKED_PROVIDER_DEGRADATION"
    BLOCKED_ACQUISITION_DEFECT = "BLOCKED_ACQUISITION_DEFECT"
    BLOCKED_CORRUPTION = "BLOCKED_CORRUPTION"
    BLOCKED_UNRESOLVED = "BLOCKED_UNRESOLVED"


@dataclass(frozen=True)
class FamilyClosurePolicy:
    family: str
    primary_peers: tuple[str, ...]
    secondary_peers: tuple[str, ...] = ()
    allow_independent_closure: bool = False
    contextual_peers_only: bool = False
    independent_closure_requires_full_chain: bool = False


_FAMILY_POLICIES = {
    "XAU/USD": FamilyClosurePolicy("precious_gold", ("XAG/USD",), ("XPT/USD", "XPD/USD")),
    "XAG/USD": FamilyClosurePolicy("precious_silver", ("XAU/USD",), ("XPT/USD", "XPD/USD")),
    "XPT/USD": FamilyClosurePolicy("pgm_metals", ("XPD/USD",), ("XAU/USD", "XAG/USD")),
    "XPD/USD": FamilyClosurePolicy("pgm_metals", ("XPT/USD",), ("XAU/USD", "XAG/USD")),
    "WTI Oil": FamilyClosurePolicy("energy_oil", ("Brent Oil",)),
    "Brent Oil": FamilyClosurePolicy("energy_oil", ("WTI Oil",)),
    "Gasoline": FamilyClosurePolicy(
        "energy_gasoline", ("WTI Oil", "Brent Oil"), contextual_peers_only=True
    ),
    "Nat Gas": FamilyClosurePolicy("energy_nat_gas", (), allow_independent_closure=True),
    "Copper": FamilyClosurePolicy(
        "industrial_metals",
        (),
        ("XAU/USD", "XAG/USD"),
        independent_closure_requires_full_chain=True,
    ),
}


def family_policy_for_symbol(canonical_symbol: str) -> FamilyClosurePolicy:
    try:
        return _FAMILY_POLICIES[canonical_symbol]
    except KeyError as exc:
        raise ValueError(f"no residual-gap family policy for {canonical_symbol}") from exc


@dataclass(frozen=True)
class ResidualGapEvidence:
    event_id: str
    canonical_symbol: str
    prev_bar_timestamp: datetime
    next_bar_timestamp: datetime
    missing_timestamps: tuple[datetime, ...]
    in_reliable_era: bool
    recognized_holiday: bool = False
    session_closure_pattern: bool = False
    subject_refetch_empty: bool = False
    subject_refetch_returned_bars: bool = False
    d1_absent: bool = False
    chunk_integrity: bool = False
    acquisition_defect: bool = False
    corruption_reasons: tuple[str, ...] = ()
    primary_peer_shared_absence: bool = False
    primary_peer_traded: bool = False
    secondary_peer_shared_absence: bool = False
    provider_history_absence: bool = False
    historical_degradation: bool = False
    d1_holiday_placeholder: bool = False
    subject_day_traded: bool = False
    frozen_closure_corroborated: bool = False
    reopen_gap_risk: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ("prev_bar_timestamp", "next_bar_timestamp"):
            row[key] = row[key].isoformat()
        row["missing_timestamps"] = [value.isoformat() for value in self.missing_timestamps]
        return row


def classify_residual_event(evidence: ResidualGapEvidence) -> ResidualGapClass:
    if evidence.corruption_reasons:
        return ResidualGapClass.DATA_CORRUPTION
    if evidence.acquisition_defect or evidence.subject_refetch_returned_bars:
        return ResidualGapClass.ACQUISITION_DEFECT
    if not evidence.in_reliable_era and evidence.historical_degradation:
        return ResidualGapClass.HISTORICAL_INTRADAY_DEGRADATION

    policy = family_policy_for_symbol(evidence.canonical_symbol)
    # Energy-specific: a recognized full/partial energy holiday can carry a
    # zero-range provider *placeholder* D1 (and flat placeholder H4 neighbours)
    # instead of a fully absent D1. A flat O==H==L==C day is not real trading,
    # so it satisfies the closure D1 condition exactly like an absent D1.
    d1_supports_closure = evidence.d1_absent or evidence.d1_holiday_placeholder
    common_closure_live = (
        evidence.recognized_holiday
        and evidence.session_closure_pattern
        and evidence.subject_refetch_empty
        and d1_supports_closure
        and evidence.chunk_integrity
    )
    # Onboarding path: when a live targeted refetch was unavailable at forensic
    # time, a recognized holiday with an absent D1, intact chunk, no returned
    # bars, and the family peer also frozen-absent over the identical slots is
    # the validated closure evidence. This corroboration is at least as strong
    # as an empty live refetch (it adds peer agreement), so it does not weaken
    # the gate; ``frozen_closure_corroborated`` already encodes the peer check.
    common_closure = common_closure_live or evidence.frozen_closure_corroborated
    peer_closure = evidence.primary_peer_shared_absence
    independent_closure = (
        policy.allow_independent_closure or policy.independent_closure_requires_full_chain
    )
    closure_supported = common_closure and (peer_closure or independent_closure)
    # Energy-specific: an isolated non-holiday intraday gap on a day the subject
    # demonstrably traded (real-range D1 plus real-range adjacent H4 bars) is a
    # provider history gap even when no peer trade sample is available.
    provider_supported = (
        evidence.primary_peer_traded
        or evidence.provider_history_absence
        or evidence.subject_day_traded
    )
    if closure_supported and provider_supported:
        return ResidualGapClass.UNRESOLVED
    if closure_supported:
        return ResidualGapClass.LEGITIMATE_MARKET_CLOSURE
    if provider_supported:
        return ResidualGapClass.PROVIDER_HISTORY_GAP
    return ResidualGapClass.UNRESOLVED


@dataclass(frozen=True)
class GapAdmissibilityMetrics:
    missing_numerator: int
    expected_denominator: int
    missing_ratio_decimal: str
    missing_percentage_decimal: str
    maximum_event_missing_bars: int
    minimum_event_separation_days: str | None
    maximum_rolling_90d_missing_percentage: str
    isolated: bool


@dataclass(frozen=True)
class GapGateResult:
    gate: GapGate
    metrics: GapAdmissibilityMetrics
    classification_counts: dict[str, int]
    classified_events: tuple[tuple[ResidualGapEvidence, ResidualGapClass], ...]
    reasons: tuple[str, ...]


def _decimal_ratio(numerator: int, denominator: int) -> Decimal:
    getcontext().prec = 28
    return Decimal(numerator) / Decimal(denominator) if denominator else Decimal(0)


def _provider_metrics(
    provider_events: list[ResidualGapEvidence], expected_timestamps: list[datetime]
) -> GapAdmissibilityMetrics:
    expected = sorted(set(expected_timestamps))
    expected_set = set(expected)
    missing = sorted(
        {
            timestamp
            for event in provider_events
            for timestamp in event.missing_timestamps
            if timestamp in expected_set
        }
    )
    ratio = _decimal_ratio(len(missing), len(expected))
    event_lengths = [len(set(event.missing_timestamps) & expected_set) for event in provider_events]
    separations = [
        Decimal(str((right.prev_bar_timestamp - left.next_bar_timestamp).total_seconds() / 86400))
        for left, right in zip(
            sorted(provider_events, key=lambda item: item.prev_bar_timestamp),
            sorted(provider_events, key=lambda item: item.prev_bar_timestamp)[1:],
        )
    ]
    maximum_rolling = Decimal(0)
    for window_start in expected:
        window_end = window_start + timedelta(days=ROLLING_WINDOW_DAYS)
        denominator = sum(window_start <= value < window_end for value in expected)
        numerator = sum(window_start <= value < window_end for value in missing)
        maximum_rolling = max(maximum_rolling, _decimal_ratio(numerator, denominator) * 100)
    minimum_separation = min(separations) if separations else None
    maximum_event = max(event_lengths, default=0)
    isolated = (
        maximum_event <= MAX_PROVIDER_GAP_BARS
        and (minimum_separation is None or minimum_separation >= MIN_PROVIDER_GAP_SEPARATION_DAYS)
        and maximum_rolling < MAX_ROLLING_MISSING_PERCENT
    )
    return GapAdmissibilityMetrics(
        missing_numerator=len(missing),
        expected_denominator=len(expected),
        missing_ratio_decimal=format(ratio, "f"),
        missing_percentage_decimal=format(ratio * 100, ".3f"),
        maximum_event_missing_bars=maximum_event,
        minimum_event_separation_days=format(minimum_separation, "f") if minimum_separation is not None else None,
        maximum_rolling_90d_missing_percentage=format(maximum_rolling, "f"),
        isolated=isolated,
    )


def evaluate_gap_gate(
    events: Iterable[ResidualGapEvidence], expected_timestamps: Iterable[datetime]
) -> GapGateResult:
    classified = tuple((event, classify_residual_event(event)) for event in events)
    counts = {member.value: 0 for member in ResidualGapClass}
    for _event, classification in classified:
        counts[classification.value] += 1
    provider_events = [event for event, value in classified if value == ResidualGapClass.PROVIDER_HISTORY_GAP]
    metrics = _provider_metrics(provider_events, list(expected_timestamps))
    reasons: list[str] = []
    if counts[ResidualGapClass.DATA_CORRUPTION.value]:
        gate = GapGate.BLOCKED_CORRUPTION
    elif counts[ResidualGapClass.ACQUISITION_DEFECT.value]:
        gate = GapGate.BLOCKED_ACQUISITION_DEFECT
    elif counts[ResidualGapClass.UNRESOLVED.value]:
        gate = GapGate.BLOCKED_UNRESOLVED
    elif provider_events and (
        Decimal(metrics.missing_percentage_decimal) >= MAX_OVERALL_MISSING_PERCENT
        or not metrics.isolated
    ):
        gate = GapGate.BLOCKED_PROVIDER_DEGRADATION
        reasons.append("provider gaps fail pre-registered admissibility")
    elif provider_events:
        gate = GapGate.CLEAR_WITH_GAP_EMBARGO
    else:
        gate = GapGate.CLEAR_ON_FREEZE
    return GapGateResult(gate, metrics, counts, classified, tuple(reasons))


def write_versioned_review_artifact(path: Path, payload: dict[str, Any]) -> str:
    if payload.get("schema") != REVIEW_SCHEMA_VERSION:
        raise ValueError(f"artifact schema must be {REVIEW_SCHEMA_VERSION}")
    return write_json_immutable(path, payload)


def build_consolidated_payload(
    *,
    canonical_symbol: str,
    terminal_symbol: str,
    events: Iterable[ResidualGapEvidence],
    expected_timestamps: Iterable[datetime],
    reliable_start: str,
    usable_start: str | None,
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    event_rows = list(events)
    if len({event.event_id for event in event_rows}) != len(event_rows):
        raise ValueError("each residual event_id must appear exactly once")
    gate = evaluate_gap_gate(event_rows, expected_timestamps)
    serialized_events = []
    for evidence, classification in gate.classified_events:
        row = evidence.to_dict()
        row["classification"] = classification.value
        serialized_events.append(row)
    payload = {
        "schema": REVIEW_SCHEMA_VERSION,
        "gate_policy_version": GATE_POLICY_VERSION,
        "canonical_symbol": canonical_symbol,
        "terminal_symbol": terminal_symbol,
        "timeframe": "H4",
        "reliable_start": reliable_start,
        "usable_start": usable_start,
        "source_hashes": dict(sorted(source_hashes.items())),
        "event_count": len(serialized_events),
        "classification_counts": gate.classification_counts,
        "events": serialized_events,
        "admissibility": asdict(gate.metrics),
        "gate": gate.gate.value,
        "gate_reasons": list(gate.reasons),
        "candidate_mask_status": "BLOCKED_MISSING_STRATEGY_EMBARGO_CONTRACT",
    }
    payload["run_hash"] = hash_stable_json(payload)
    return payload


def _peer_shared(peer: dict[str, Any]) -> bool:
    frozen = peer.get("frozen_peer_evidence") or {}
    return bool(
        peer.get("shared_session_closure_candidate")
        or peer.get("shared_metals_session_closure_candidate")
        or frozen.get("shared_frozen_absence_candidate")
    )


def _peer_traded(peer: dict[str, Any]) -> bool:
    return bool(
        peer.get("peer_trades_while_subject_missing_samples")
        or peer.get("xag_trades_while_xau_missing_samples")
    )


def residual_evidence_from_gap_rows(
    *,
    canonical_symbol: str,
    peer_gap_rows: list[tuple[str | None, dict[str, Any]]],
) -> ResidualGapEvidence:
    if not peer_gap_rows:
        raise ValueError("at least one subject gap row is required")
    policy = family_policy_for_symbol(canonical_symbol)
    base = peer_gap_rows[0][1]
    by_peer = {peer: row for peer, row in peer_gap_rows if peer is not None}
    subject = base.get("mt5_subject_refetch") or base.get("mt5_xau_refetch") or {}
    primary_rows = [by_peer[peer] for peer in policy.primary_peers if peer in by_peer]
    secondary_rows = [by_peer[peer] for peer in policy.secondary_peers if peer in by_peer]
    primary_shared = bool(primary_rows) and all(
        _peer_shared(row.get("peer_comparison") or row.get("xag_comparison") or {})
        for row in primary_rows
    )
    primary_traded = any(
        _peer_traded(row.get("peer_comparison") or row.get("xag_comparison") or {})
        for row in primary_rows
    )
    secondary_shared = bool(secondary_rows) and all(
        _peer_shared(row.get("peer_comparison") or row.get("xag_comparison") or {})
        for row in secondary_rows
    )
    d1 = base.get("d1_availability") or {}
    holiday = base.get("holiday_proximity") or {}
    chunk = base.get("chunk_location") or {}
    price = base.get("price_discontinuity") or {}
    missing = tuple(
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        for value in base.get("expected_missing_timestamps", [])
    )
    if not missing:
        prev = datetime.fromisoformat(str(base["prev_bar_timestamp"]).replace("Z", "+00:00"))
        missing = tuple(prev + timedelta(hours=4 * index) for index in range(1, int(base["missing_bar_count"]) + 1))
    old_classification = str(base.get("classification") or "")
    returned_count = int(subject.get("returned_missing_count", 0) or 0)
    recognized_holiday = bool(holiday.get("holiday_hits"))
    subject_refetch_empty = (
        subject.get("status") == "ok"
        and returned_count == 0
        and str(subject.get("classification", "")).startswith("B_")
    )
    live_refetch_unavailable = subject.get("status") != "ok"
    d1_absent = (
        d1.get("status") == "ok"
        and not d1.get("frozen_d1_present")
        and not d1.get("live_d1_present")
    )
    chunk_integrity = (
        bool(chunk.get("within_single_chunk")) or chunk.get("at_chunk_boundary") is False
    ) and not chunk.get("chunk_defect") and not chunk.get("acquisition_defect")
    # Frozen corroboration path (used only when no live refetch exists): holiday,
    # absent D1, intact chunk, no returned bars, and family peer frozen-absent.
    frozen_closure_corroborated = bool(
        recognized_holiday
        and d1_absent
        and chunk_integrity
        and returned_count == 0
        and live_refetch_unavailable
        and primary_shared
    )
    return ResidualGapEvidence(
        event_id=str(base["event_id"]),
        canonical_symbol=canonical_symbol,
        prev_bar_timestamp=datetime.fromisoformat(str(base["prev_bar_timestamp"]).replace("Z", "+00:00")),
        next_bar_timestamp=datetime.fromisoformat(str(base["next_bar_timestamp"]).replace("Z", "+00:00")),
        missing_timestamps=missing,
        in_reliable_era=True,
        recognized_holiday=recognized_holiday,
        session_closure_pattern=recognized_holiday and int(base.get("missing_bar_count", 0)) <= 6,
        subject_refetch_empty=subject_refetch_empty,
        subject_refetch_returned_bars=returned_count > 0,
        d1_absent=d1_absent,
        chunk_integrity=chunk_integrity,
        acquisition_defect=old_classification == "ACQUISITION_DEFECT",
        corruption_reasons=tuple(price.get("corruption_reasons") or []),
        primary_peer_shared_absence=primary_shared,
        primary_peer_traded=primary_traded,
        secondary_peer_shared_absence=secondary_shared,
        provider_history_absence=old_classification == "PROVIDER_HISTORY_GAP" or primary_traded,
        frozen_closure_corroborated=frozen_closure_corroborated,
        reopen_gap_risk=dict(base.get("reopen_gap_risk") or price.get("reopen_gap_risk") or {}),
        evidence={
            "family_policy": asdict(policy),
            "subject": subject,
            "d1": d1,
            "holiday": holiday,
            "chunk": chunk,
            "peer_symbols_checked": [peer for peer, _row in peer_gap_rows if peer],
        },
    )


def _bar_is_flat(bar: dict[str, Any] | None) -> bool:
    """A zero-range placeholder bar (open==high==low==close)."""
    if not bar:
        return False
    try:
        values = [float(bar[name]) for name in ("open", "high", "low", "close")]
    except (KeyError, TypeError, ValueError):
        return False
    return len(set(values)) == 1


PLACEHOLDER_CLASS = "NON_TRADING_PLACEHOLDER"


def non_trading_placeholder_events(
    bars: Iterable[dict[str, Any]],
    *,
    reliable_start: str,
    usable_start: str | None = None,
) -> list[dict[str, Any]]:
    """Detect zero-range (open==high==low==close) carry-forward placeholder bars.

    A zero-volatility H4 bar is a non-trading provider carry-forward placeholder,
    never a real trade. Returns one immutable record per placeholder bar; raw
    bars are read only and never mutated. ``in_reliable_era`` / ``in_usable_era``
    flag which records belong to the active research candidate window.
    """
    from athena_research.commodity_data_audit.gap_forensic import _holiday_proximity

    import pandas as pd

    events: list[dict[str, Any]] = []
    for bar in bars:
        if not _bar_is_flat(bar):
            continue
        ts = datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc)
        date_iso = ts.date().isoformat()
        holiday = _holiday_proximity(pd.Timestamp(ts))
        events.append(
            {
                "classification": PLACEHOLDER_CLASS,
                "timestamp": ts.isoformat(),
                "calendar_date": date_iso,
                "weekday": ts.strftime("%A"),
                "price": float(bar["close"]),
                "holiday_hits": list(holiday.get("holiday_hits") or []),
                "in_reliable_era": date_iso >= reliable_start,
                "in_usable_era": bool(usable_start) and date_iso >= str(usable_start),
            }
        )
    return sorted(events, key=lambda row: row["timestamp"])


def _bar_is_real(bar: dict[str, Any] | None) -> bool:
    if not bar:
        return False
    try:
        values = [float(bar[name]) for name in ("open", "high", "low", "close")]
    except (KeyError, TypeError, ValueError):
        return False
    return len(set(values)) > 1


def _energy_session_flags(
    *,
    recognized_holiday: bool,
    d1: dict[str, Any],
    calendar_date: str,
    subject_bar_lookup: dict[int, dict[str, Any]],
) -> tuple[bool, bool, dict[str, Any]]:
    """Derive deterministic energy-session flags from frozen evidence.

    Returns ``(d1_holiday_placeholder, subject_day_traded, detail)`` where the
    detail block records the exact bars inspected. Never reads or mutates raw
    bars beyond a read-only OHLC inspection on the gap calendar date.
    """
    d1_bar = d1.get("live_d1") or d1.get("frozen_d1")
    d1_present = bool(d1.get("live_d1_present") or d1.get("frozen_d1_present"))
    d1_flat = d1_present and _bar_is_flat(d1_bar)
    d1_real = d1_present and _bar_is_real(d1_bar)
    present_same_date = [
        bar
        for ts, bar in sorted(subject_bar_lookup.items())
        if datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat() == calendar_date
    ]
    neighbours_all_flat = bool(present_same_date) and all(
        _bar_is_flat(bar) for bar in present_same_date
    )
    neighbour_real = any(_bar_is_real(bar) for bar in present_same_date)
    no_present_same_date = not present_same_date
    d1_holiday_placeholder = bool(recognized_holiday and d1_flat and neighbours_all_flat)
    # The calendar date demonstrably traded (real-range D1) on a non-holiday, and
    # the present same-date bars are themselves real trades (or none survive) --
    # never flat placeholders. This marks an isolated provider history gap.
    subject_day_traded = bool(
        (not recognized_holiday)
        and d1_real
        and (neighbour_real or no_present_same_date)
    )
    detail = {
        "calendar_date": calendar_date,
        "d1_present": d1_present,
        "d1_zero_range_placeholder": d1_flat,
        "d1_real_range": d1_real,
        "present_same_date_bar_count": len(present_same_date),
        "present_same_date_all_flat": neighbours_all_flat,
        "present_same_date_any_real": neighbour_real,
    }
    return d1_holiday_placeholder, subject_day_traded, detail


def residual_evidence_from_v3_event(
    event: dict[str, Any],
    *,
    subject_bar_lookup: dict[int, dict[str, Any]],
) -> ResidualGapEvidence:
    """Rebuild a residual event from a frozen prior-version artifact, adding the
    energy-session flags derived from frozen evidence and raw OHLC inspection.

    The frozen targeted-MT5-refetch result already captured in the prior event
    is reused verbatim (no live refetch). Raw bars are read only, never altered.
    """

    def _parse(value: str) -> datetime:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    inner = dict(event.get("evidence") or {})
    d1 = dict(inner.get("d1") or {})
    recognized_holiday = bool(event.get("recognized_holiday"))
    calendar_date = str(
        d1.get("calendar_date")
        or _parse(event["missing_timestamps"][0]).date().isoformat()
    )
    d1_placeholder, day_traded, session_detail = _energy_session_flags(
        recognized_holiday=recognized_holiday,
        d1=d1,
        calendar_date=calendar_date,
        subject_bar_lookup=subject_bar_lookup,
    )
    inner["energy_session"] = session_detail
    return ResidualGapEvidence(
        event_id=str(event["event_id"]),
        canonical_symbol=str(event["canonical_symbol"]),
        prev_bar_timestamp=_parse(event["prev_bar_timestamp"]),
        next_bar_timestamp=_parse(event["next_bar_timestamp"]),
        missing_timestamps=tuple(_parse(value) for value in event["missing_timestamps"]),
        in_reliable_era=bool(event.get("in_reliable_era", True)),
        recognized_holiday=recognized_holiday,
        session_closure_pattern=bool(event.get("session_closure_pattern")),
        subject_refetch_empty=bool(event.get("subject_refetch_empty")),
        subject_refetch_returned_bars=bool(event.get("subject_refetch_returned_bars")),
        d1_absent=bool(event.get("d1_absent")),
        chunk_integrity=bool(event.get("chunk_integrity")),
        acquisition_defect=bool(event.get("acquisition_defect")),
        corruption_reasons=tuple(event.get("corruption_reasons") or ()),
        primary_peer_shared_absence=bool(event.get("primary_peer_shared_absence")),
        primary_peer_traded=bool(event.get("primary_peer_traded")),
        secondary_peer_shared_absence=bool(event.get("secondary_peer_shared_absence")),
        provider_history_absence=bool(event.get("provider_history_absence")),
        historical_degradation=bool(event.get("historical_degradation")),
        d1_holiday_placeholder=d1_placeholder,
        subject_day_traded=day_traded,
        reopen_gap_risk=dict(event.get("reopen_gap_risk") or {}),
        evidence=inner,
    )
