from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from athena_research.commodity_data_audit.freeze_registry import (
    QA_ALGORITHM_VERSION_V3_1,
    default_reliable_peer_symbol,
    resolve_phase1_mt5_symbol,
    slug_symbol,
)
from athena_research.commodity_data_audit.freeze_store import (
    hash_file_bytes,
    load_raw_bars_from_store,
    read_json,
    repo_root,
    resolve_path,
)
from athena_research.commodity_data_audit.gap_forensic import (
    _holiday_proximity,
    _parse_ts,
    _refetch_window,
    build_abnormal_gap_events,
    load_chunk_boundaries,
    refetch_gap_samples,
)

REVIEW_TOOL_VERSION = "commodity_reliable_era_gap_review_v3"
DEFAULT_RELIABLE_START = "2016-07-29"


class ReliableEraGapClass(str, Enum):
    LEGITIMATE_MARKET_CLOSURE = "LEGITIMATE_MARKET_CLOSURE"
    ACQUISITION_DEFECT = "ACQUISITION_DEFECT"
    PROVIDER_HISTORY_GAP = "PROVIDER_HISTORY_GAP"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ReliableEraGapReview:
    event_id: str
    prev_bar_timestamp: str
    next_bar_timestamp: str
    calendar_date: str
    weekday: str
    duration_seconds: float
    missing_h4_slots: list[int]
    missing_bar_count: int
    chunk_location: dict[str, Any]
    mt5_subject_refetch: dict[str, Any]
    peer_comparison: dict[str, Any]
    d1_availability: dict[str, Any]
    holiday_proximity: dict[str, Any]
    price_discontinuity: dict[str, Any]
    classification: ReliableEraGapClass
    classification_evidence: list[str]
    reopen_gap_risk: dict[str, Any] = field(default_factory=dict)
    mt5_xau_refetch: dict[str, Any] | None = None
    xag_comparison: dict[str, Any] | None = None


def _subject_refetch(refetch_row: dict[str, Any] | None) -> dict[str, Any]:
    row = refetch_row or {}
    return dict(row.get("subject") or row.get("xauusd") or {})


def _peer_refetch(refetch_row: dict[str, Any] | None) -> dict[str, Any]:
    row = refetch_row or {}
    return dict(row.get("peer") or row.get("xagusd") or {})


def _bar_lookup(bars: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(bar["time"]): bar for bar in bars}


def _calendar_date_for_gap(prev: pd.Timestamp, curr: pd.Timestamp) -> str:
    midpoint = prev + (curr - prev) / 2
    return midpoint.date().isoformat()


def _weekday_for_gap(prev: pd.Timestamp, curr: pd.Timestamp) -> str:
    midpoint = prev + (curr - prev) / 2
    return midpoint.strftime("%A")


def _price_discontinuity(
    prev_bar: dict[str, Any] | None,
    next_bar: dict[str, Any] | None,
) -> dict[str, Any]:
    if prev_bar is None or next_bar is None:
        return {
            "status": "missing_adjacent_bars",
            "corruption_reasons": [],
            "reopen_gap_risk": {
                "absolute_gap": None,
                "relative_gap_pct": None,
                "atr_normalised_gap": None,
                "severity": "UNAVAILABLE",
            },
        }

    corruption_reasons: list[str] = []
    for label, bar in (("prev", prev_bar), ("next", next_bar)):
        try:
            ohlc = {name: float(bar[name]) for name in ("open", "high", "low", "close")}
        except (KeyError, TypeError, ValueError):
            corruption_reasons.append(f"{label}_bar_invalid_ohlc_value")
            continue
        if not all(math.isfinite(value) for value in ohlc.values()):
            corruption_reasons.append(f"{label}_bar_non_finite_price")
            continue
        if any(value < 0 for value in ohlc.values()):
            corruption_reasons.append(f"{label}_bar_negative_price")
        if (
            ohlc["high"] < ohlc["low"]
            or ohlc["high"] < max(ohlc["open"], ohlc["close"])
            or ohlc["low"] > min(ohlc["open"], ohlc["close"])
        ):
            corruption_reasons.append(f"{label}_bar_ohlc_contract_violation")

    try:
        prev_close = float(prev_bar["close"])
        next_open = float(next_bar["open"])
    except (KeyError, TypeError, ValueError):
        prev_close = math.nan
        next_open = math.nan

    if math.isfinite(prev_close) and math.isfinite(next_open) and prev_close != 0 and next_open != 0:
        scale_ratio = max(abs(prev_close), abs(next_open)) / min(abs(prev_close), abs(next_open))
        if scale_ratio >= 10:
            corruption_reasons.append("scale_or_decimal_shift")

    gap_abs = abs(next_open - prev_close)
    gap_ratio = gap_abs / max(abs(prev_close), 1e-9)
    gap_pct = gap_ratio * 100
    atr = None
    for value in (prev_bar.get("atr"), next_bar.get("atr")):
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(candidate) and candidate > 0:
            atr = candidate
            break
    atr_normalised = gap_abs / atr if atr is not None else None
    if not math.isfinite(gap_abs) or not math.isfinite(gap_pct):
        severity = "INVALID"
    elif gap_pct >= 2:
        severity = "HIGH"
    elif gap_pct >= 1:
        severity = "MEDIUM"
    elif gap_pct >= 0.25:
        severity = "LOW"
    else:
        severity = "MINIMAL"
    reopen_gap_risk = {
        "absolute_gap": round(gap_abs, 6) if math.isfinite(gap_abs) else None,
        "relative_gap_pct": round(gap_pct, 6) if math.isfinite(gap_pct) else None,
        "atr_normalised_gap": round(atr_normalised, 6) if atr_normalised is not None else None,
        "severity": severity,
    }
    return {
        "status": "ok" if not corruption_reasons else "corrupt",
        "prev_close": prev_close,
        "next_open": next_open,
        "absolute_gap": round(gap_abs, 6) if math.isfinite(gap_abs) else None,
        "relative_gap_pct": round(gap_pct, 6) if math.isfinite(gap_pct) else None,
        "large_discontinuity": gap_ratio > 0.01,
        "corruption_reasons": list(dict.fromkeys(corruption_reasons)),
        "reopen_gap_risk": reopen_gap_risk,
    }


def _d1_availability(
    *,
    calendar_date: str,
    d1_bars: list[dict[str, Any]] | None,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None,
    subject_terminal: str,
) -> dict[str, Any]:
    day = datetime.fromisoformat(calendar_date).replace(tzinfo=timezone.utc)
    d1_by_date: dict[str, dict[str, Any]] = {}
    if d1_bars:
        for bar in d1_bars:
            ts = _parse_ts(bar["time"])
            d1_by_date[ts.date().isoformat()] = bar
    frozen = d1_by_date.get(calendar_date)
    live = None
    if copy_rates is not None:
        try:
            fetched = copy_rates(subject_terminal, "D1", day - timedelta(days=1), day + timedelta(days=2))
            for bar in fetched:
                ts = _parse_ts(bar["time"])
                if ts.date().isoformat() == calendar_date:
                    live = bar
                    break
        except Exception as exc:
            return {"status": "error", "error": str(exc), "frozen_d1_present": frozen is not None}
    return {
        "status": "ok",
        "calendar_date": calendar_date,
        "frozen_d1_present": frozen is not None,
        "live_d1_present": live is not None,
        "frozen_d1": {
            "open": float(frozen["open"]),
            "high": float(frozen["high"]),
            "low": float(frozen["low"]),
            "close": float(frozen["close"]),
        }
        if frozen
        else None,
        "live_d1": {
            "open": float(live["open"]),
            "high": float(live["high"]),
            "low": float(live["low"]),
            "close": float(live["close"]),
        }
        if live
        else None,
    }


def _frozen_peer_shared_absence(
    *,
    event: dict[str, Any],
    peer_lookup: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    missing_times = {
        int(_parse_ts(ts).timestamp())
        for ts in event["expected_missing_timestamps"]
    }
    peer_has_bar_in_missing_slot = sorted(ts for ts in missing_times if ts in peer_lookup)
    return {
        "status": "ok",
        "missing_slot_count": len(missing_times),
        "peer_has_bar_in_missing_slot_timestamps": [
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in peer_has_bar_in_missing_slot
        ],
        "shared_frozen_absence_candidate": len(peer_has_bar_in_missing_slot) == 0,
    }


def classify_reliable_era_gap(
    *,
    event: dict[str, Any],
    refetch_row: dict[str, Any] | None,
    peer_frozen: dict[str, Any] | None,
    d1_info: dict[str, Any],
    holiday: dict[str, Any],
    price: dict[str, Any],
) -> tuple[ReliableEraGapClass, list[str]]:
    evidence: list[str] = []
    subject = _subject_refetch(refetch_row)
    peer = _peer_refetch(refetch_row)

    if subject.get("returned_missing_count", 0) > 0:
        evidence.append("MT5 H4 refetch returns bars absent from frozen raw")
        return ReliableEraGapClass.ACQUISITION_DEFECT, evidence

    peer_trades = peer.get("peer_trades_while_subject_missing_samples") or peer.get(
        "xag_trades_while_xau_missing_samples"
    ) or []
    if peer_trades:
        evidence.append("Peer H4 traded during subject missing window")
        evidence.append(f"peer_samples={peer_trades[:3]}")
        return ReliableEraGapClass.PROVIDER_HISTORY_GAP, evidence

    corruption_reasons = list(price.get("corruption_reasons") or [])
    if corruption_reasons:
        evidence.append(f"price_corruption_blocks_legitimate_closure={corruption_reasons}")
        return ReliableEraGapClass.UNRESOLVED, evidence

    chunk_context = event.get("chunk_context") or {}
    if chunk_context.get("chunk_defect") or chunk_context.get("acquisition_defect"):
        evidence.append("chunk/acquisition defect blocks legitimate closure")
        return ReliableEraGapClass.ACQUISITION_DEFECT, evidence
    chunk_clear = bool(chunk_context.get("within_single_chunk")) or chunk_context.get("at_chunk_boundary") is False
    if not chunk_clear:
        evidence.append("chunk/acquisition integrity not independently cleared")
        return ReliableEraGapClass.UNRESOLVED, evidence

    holiday_hits = holiday.get("holiday_hits") or []
    if holiday_hits:
        evidence.append(f"recognized_holiday_proximity={holiday_hits}")

    subject_refetch_absent = (
        subject.get("status") == "ok"
        and int(subject.get("returned_missing_count", 0)) == 0
        and str(subject.get("classification", "")).startswith("B_")
    )
    if subject_refetch_absent:
        evidence.append("MT5 targeted refetch absent for missing H4 slots")

    peer_shared_mt5 = bool(
        peer.get("shared_session_closure_candidate")
        or peer.get("shared_metals_session_closure_candidate")
    )
    peer_shared_frozen = bool((peer_frozen or {}).get("shared_frozen_absence_candidate"))
    if peer_shared_mt5:
        evidence.append("Peer MT5 refetch also lacks the missing H4 slots over the same window")
    if peer_shared_frozen:
        evidence.append("Peer frozen raw also lacks bars at the missing H4 slot timestamps")

    d1_checked = d1_info.get("status") == "ok"
    d1_present = bool(d1_info.get("frozen_d1_present") or d1_info.get("live_d1_present"))
    if d1_present:
        evidence.append("D1 bar present on gap calendar date")
    else:
        evidence.append("D1 absent on gap calendar date where checked")

    if (
        holiday_hits
        and subject_refetch_absent
        and (peer_shared_mt5 or peer_shared_frozen)
        and not peer_trades
        and d1_checked
        and not d1_present
    ):
        evidence.append(
            "Combined closure evidence: full-session holiday, MT5 refetch absence, peer shared absence, D1 absent, no acquisition or corruption defect"
        )
        return ReliableEraGapClass.LEGITIMATE_MARKET_CLOSURE, evidence

    if subject.get("status") == "error":
        evidence.append(f"mt5_refetch_error={subject.get('error')}")
        return ReliableEraGapClass.UNRESOLVED, evidence

    evidence.append("insufficient corroboration for legitimate closure")
    return ReliableEraGapClass.UNRESOLVED, evidence


def review_reliable_era_abnormal_gaps(
    *,
    bars: list[dict[str, Any]],
    chunks: list,
    reliable_start: str = DEFAULT_RELIABLE_START,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None = None,
    d1_bars: list[dict[str, Any]] | None = None,
    subject_terminal: str = "XAUUSD",
    peer_terminal: str | None = "XAGUSD",
    peer_bars: list[dict[str, Any]] | None = None,
) -> list[ReliableEraGapReview]:
    reliable = [
        bar
        for bar in bars
        if datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc).date().isoformat() >= reliable_start
    ]
    events = build_abnormal_gap_events(reliable, chunks=chunks)
    lookup = _bar_lookup(reliable)
    peer_lookup = _bar_lookup(peer_bars or [])
    refetch_rows = (
        refetch_gap_samples(
            events,
            copy_rates=copy_rates,
            subject_terminal=subject_terminal,
            peer_terminal=peer_terminal or subject_terminal,
        )
        if copy_rates
        else []
    )
    refetch_by_id = {row["event_id"]: row for row in refetch_rows}

    reviews: list[ReliableEraGapReview] = []
    for event in events:
        prev = _parse_ts(event["prev_bar_timestamp"])
        curr = _parse_ts(event["next_bar_timestamp"])
        calendar_date = _calendar_date_for_gap(prev, curr)
        holiday = _holiday_proximity(prev + (curr - prev) / 2)
        refetch_row = refetch_by_id.get(event["event_id"])
        peer_frozen = _frozen_peer_shared_absence(event=event, peer_lookup=peer_lookup) if peer_bars else None
        d1_info = _d1_availability(
            calendar_date=calendar_date,
            d1_bars=d1_bars,
            copy_rates=copy_rates,
            subject_terminal=subject_terminal,
        )
        price = _price_discontinuity(
            lookup.get(int(prev.timestamp())),
            lookup.get(int(curr.timestamp())),
        )
        classification, evidence = classify_reliable_era_gap(
            event=event,
            refetch_row=refetch_row,
            peer_frozen=peer_frozen,
            d1_info=d1_info,
            holiday=holiday,
            price=price,
        )
        subject_payload = _subject_refetch(refetch_row) or {"status": "skipped"}
        peer_payload = dict(_peer_refetch(refetch_row) or {"status": "skipped"})
        if peer_frozen:
            peer_payload["frozen_peer_evidence"] = peer_frozen
        reviews.append(
            ReliableEraGapReview(
                event_id=event["event_id"],
                prev_bar_timestamp=event["prev_bar_timestamp"],
                next_bar_timestamp=event["next_bar_timestamp"],
                calendar_date=calendar_date,
                weekday=_weekday_for_gap(prev, curr),
                duration_seconds=float(event["duration_seconds"]),
                missing_h4_slots=list(event["missing_hour_slots_utc"]),
                missing_bar_count=int(event["missing_bar_count"]),
                chunk_location=dict(event["chunk_context"]),
                mt5_subject_refetch=subject_payload,
                peer_comparison=peer_payload,
                d1_availability=d1_info,
                holiday_proximity=holiday,
                price_discontinuity=price,
                reopen_gap_risk=dict(price.get("reopen_gap_risk") or {}),
                classification=classification,
                classification_evidence=evidence,
                mt5_xau_refetch=subject_payload,
                xag_comparison=peer_payload,
            )
        )
    return reviews


def review_to_dict(
    reviews: list[ReliableEraGapReview],
    *,
    canonical_symbol: str,
    peer_symbol: str | None,
    timeframe: str,
) -> dict[str, Any]:
    counts: dict[str, int] = {member.value: 0 for member in ReliableEraGapClass}
    for review in reviews:
        counts[review.classification.value] += 1
    return {
        "tool_version": REVIEW_TOOL_VERSION,
        "canonical_symbol": canonical_symbol,
        "peer_symbol": peer_symbol,
        "timeframe": timeframe.upper(),
        "gap_count": len(reviews),
        "classification_counts": counts,
        "all_legitimate_closures": all(
            review.classification == ReliableEraGapClass.LEGITIMATE_MARKET_CLOSURE for review in reviews
        ),
        "gaps": [
            {
                "event_id": review.event_id,
                "prev_bar_timestamp": review.prev_bar_timestamp,
                "next_bar_timestamp": review.next_bar_timestamp,
                "calendar_date": review.calendar_date,
                "weekday": review.weekday,
                "duration_seconds": review.duration_seconds,
                "missing_h4_slots_utc": review.missing_h4_slots,
                "missing_bar_count": review.missing_bar_count,
                "chunk_location": review.chunk_location,
                "mt5_subject_refetch": review.mt5_subject_refetch,
                "peer_comparison": review.peer_comparison,
                "mt5_xau_refetch": review.mt5_xau_refetch,
                "xag_comparison": review.xag_comparison,
                "d1_availability": review.d1_availability,
                "holiday_proximity": review.holiday_proximity,
                "price_discontinuity": review.price_discontinuity,
                "reopen_gap_risk": review.reopen_gap_risk,
                "classification": review.classification.value,
                "classification_evidence": review.classification_evidence,
            }
            for review in reviews
        ],
    }


def _forensic_output_dir(canonical_symbol: str, timeframe: str) -> Path:
    slug = slug_symbol(canonical_symbol)
    return resolve_path(f"logs/commodity_data_audit/forensics/{slug}_{timeframe.upper()}_reliable_gaps")


def _forensic_output_filename(canonical_symbol: str, timeframe: str) -> str:
    slug = slug_symbol(canonical_symbol)
    return f"{slug}_{timeframe.upper()}_reliable_era_gap_review.json"


def run_commodity_reliable_era_gap_review(
    *,
    canonical_symbol: str,
    timeframe: str = "H4",
    peer_symbol: str | None = None,
    reliable_start: str = DEFAULT_RELIABLE_START,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None = None,
    raw_root: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    raw_root = raw_root or resolve_path("logs/commodity_data_audit/raw/v1")
    slug = slug_symbol(canonical_symbol)
    peer_symbol = peer_symbol if peer_symbol is not None else default_reliable_peer_symbol(canonical_symbol)
    subject_terminal = resolve_phase1_mt5_symbol(canonical_symbol)
    peer_terminal = resolve_phase1_mt5_symbol(peer_symbol) if peer_symbol else None
    output_dir = output_dir or _forensic_output_dir(canonical_symbol, timeframe)
    output_dir.mkdir(parents=True, exist_ok=True)

    bars = load_raw_bars_from_store(raw_root, slug, timeframe.upper())
    chunks = load_chunk_boundaries(raw_root, slug, timeframe.upper())
    peer_bars = None
    if peer_symbol:
        peer_slug = slug_symbol(peer_symbol)
        try:
            peer_bars = load_raw_bars_from_store(raw_root, peer_slug, timeframe.upper())
        except Exception:
            peer_bars = None

    d1_bars = None
    if copy_rates is not None:
        try:
            d1_bars = copy_rates(
                subject_terminal,
                "D1",
                datetime(2016, 1, 1, tzinfo=timezone.utc),
                datetime(2027, 1, 1, tzinfo=timezone.utc),
            )
        except Exception:
            d1_bars = None

    reviews = review_reliable_era_abnormal_gaps(
        bars=bars,
        chunks=chunks,
        reliable_start=reliable_start,
        copy_rates=copy_rates,
        d1_bars=d1_bars,
        subject_terminal=subject_terminal,
        peer_terminal=peer_terminal,
        peer_bars=peer_bars,
    )
    payload = review_to_dict(
        reviews,
        canonical_symbol=canonical_symbol,
        peer_symbol=peer_symbol,
        timeframe=timeframe,
    )
    payload["reliable_start"] = reliable_start
    payload["subject_terminal"] = subject_terminal
    payload["peer_terminal"] = peer_terminal
    payload["raw_content_hash"] = None
    merged = raw_root / slug / timeframe.upper() / "merged_raw.json"
    if merged.exists():
        payload["raw_content_hash"] = hash_file_bytes(merged)
    else:
        from athena_research.commodity_data_audit.freeze_store import resolve_raw_evidence_hashes

        payload["raw_content_hash"] = resolve_raw_evidence_hashes(raw_root, slug, timeframe.upper()).get(
            "merged_bar_content_hash"
        )

    out_path = output_dir / _forensic_output_filename(canonical_symbol, timeframe)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "review_path": str(out_path.relative_to(repo_root())).replace("\\", "/"),
        "gap_count": len(reviews),
        "classification_counts": payload["classification_counts"],
        "all_legitimate_closures": payload["all_legitimate_closures"],
        "canonical_symbol": canonical_symbol,
        "peer_symbol": peer_symbol,
        "timeframe": timeframe.upper(),
        "reviews": reviews,
        "review_payload": payload,
    }


def run_xau_reliable_era_gap_review(
    *,
    raw_root: Path | None = None,
    reliable_start: str = DEFAULT_RELIABLE_START,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    legacy_dir = output_dir or resolve_path("logs/commodity_data_audit/forensics/xau_h4_reliable_gaps")
    result = run_commodity_reliable_era_gap_review(
        canonical_symbol="XAU/USD",
        timeframe="H4",
        peer_symbol="XAG/USD",
        reliable_start=reliable_start,
        copy_rates=copy_rates,
        raw_root=raw_root,
        output_dir=legacy_dir,
    )
    legacy_path = legacy_dir / "xau_reliable_era_gap_review.json"
    legacy_path.write_text(
        json.dumps(result["review_payload"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result["review_path"] = str(legacy_path.relative_to(repo_root())).replace("\\", "/")
    return result


def run_commodity_reliable_gap_review_with_qa(
    *,
    canonical_symbol: str,
    timeframe: str,
    peer_symbol: str | None,
    reliable_start: str,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None,
    qa_version: str,
    start_date: str = "2014-01-01",
    end_date: str = "",
    end_time: str = "",
) -> dict[str, Any]:
    review_result = run_commodity_reliable_era_gap_review(
        canonical_symbol=canonical_symbol,
        timeframe=timeframe,
        peer_symbol=peer_symbol,
        reliable_start=reliable_start,
        copy_rates=copy_rates,
    )
    manifest_path = None
    empirical_gate = None
    if qa_version in ("v3_1", QA_ALGORITHM_VERSION_V3_1, "commodity_freeze_qa_v3_1"):
        from athena_research.commodity_data_audit.freeze_reqa_v3_1 import reqa_v3_1_from_original_manifest_inputs

        reqa = reqa_v3_1_from_original_manifest_inputs(
            canonical_symbol=canonical_symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            end_time=end_time,
            copy_rates=copy_rates,
            gap_reviews=review_result["reviews"],
            gap_review_payload=review_result["review_payload"],
            peer_symbol=peer_symbol,
        )
        manifest_path = reqa.manifest_path
        empirical_gate = reqa.empirical_gate
    return {
        **review_result,
        "qa_manifest_path": manifest_path,
        "empirical_gate": empirical_gate,
    }


def adjust_reliable_quality_for_legitimate_closures(
    quality,
    reviews: list[ReliableEraGapReview],
):
    from dataclasses import replace

    legitimate = [
        review
        for review in reviews
        if review.classification == ReliableEraGapClass.LEGITIMATE_MARKET_CLOSURE
    ]
    if not legitimate:
        return quality, {
            "adjusted": False,
            "excluded_gap_events": 0,
            "excluded_abnormal_missing_bars": 0,
        }

    excluded_events = len(legitimate)
    excluded_missing = sum(review.missing_bar_count for review in legitimate)
    gap_details = dict(quality.details.get("gap_classification", {}))
    counts = dict(gap_details.get("counts", {}))
    prior_abnormal = int(counts.get("ABNORMAL_ACTIVE_SESSION_GAP", 0))
    new_abnormal = max(0, prior_abnormal - excluded_events)
    counts["ABNORMAL_ACTIVE_SESSION_GAP"] = new_abnormal
    counts["LEGITIMATE_MARKET_CLOSURE"] = int(counts.get("LEGITIMATE_MARKET_CLOSURE", 0)) + excluded_events
    gap_details["counts"] = counts
    gap_details["legitimate_closure_exclusions"] = [
        {
            "event_id": review.event_id,
            "prev_bar_timestamp": review.prev_bar_timestamp,
            "next_bar_timestamp": review.next_bar_timestamp,
            "calendar_date": review.calendar_date,
            "missing_bar_count": review.missing_bar_count,
            "classification_evidence": review.classification_evidence,
        }
        for review in legitimate
    ]

    reconciliation = dict(quality.details.get("missing_bar_reconciliation", {}))
    prior_abnormal_missing = int(reconciliation.get("abnormal_missing", 0))
    new_abnormal_missing = max(0, prior_abnormal_missing - excluded_missing)
    new_blocking = max(0, int(reconciliation.get("blocking_missing_bar_estimate", 0)) - excluded_missing)
    reconciliation["abnormal_missing"] = new_abnormal_missing
    reconciliation["blocking_missing_bar_estimate"] = new_blocking
    reconciliation["legitimate_closure_excluded_missing"] = excluded_missing

    issues = list(quality.issues)
    if new_abnormal == 0 and "abnormal_active_session_gaps" in issues:
        issues.remove("abnormal_active_session_gaps")
    if new_abnormal_missing == 0 and "missing_bars" in issues:
        issues.remove("missing_bars")

    blocking_issues = {
        "duplicate_timestamps",
        "non_monotonic_timestamps",
        "ohlc_violations",
        "nan_or_inf",
        "future_bars",
        "empty_series",
        "insufficient_history",
        "terminal_truncation_suspected",
        "abnormal_active_session_gaps",
    }
    ok = not any(issue in blocking_issues for issue in issues)
    new_missing_estimate = max(0, int(quality.missing_bar_estimate) - excluded_missing)

    adjusted = replace(
        quality,
        ok=ok,
        missing_bar_estimate=new_missing_estimate,
        issues=issues,
        details={
            **quality.details,
            "gap_classification": gap_details,
            "missing_bar_reconciliation": reconciliation,
            "legitimate_closure_adjustment": {
                "excluded_gap_events": excluded_events,
                "excluded_abnormal_missing_bars": excluded_missing,
                "prior_abnormal_gap_events": prior_abnormal,
                "remaining_abnormal_gap_events": new_abnormal,
            },
        },
    )
    return adjusted, {
        "adjusted": True,
        "excluded_gap_events": excluded_events,
        "excluded_abnormal_missing_bars": excluded_missing,
        "remaining_abnormal_gap_events": new_abnormal,
        "remaining_abnormal_missing_bars": new_abnormal_missing,
        "all_legitimate_closures": excluded_events == len(reviews),
    }
