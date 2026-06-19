from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from athena_research.commodity_data_audit.coverage_audit import (
    audit_h4_coverage,
    coverage_report_to_dict,
    filter_research_eligible_bars,
)
from athena_research.commodity_data_audit.freeze_quality import (
    audit_freeze_bars,
    exclude_forming_bar,
    filter_bars_to_requested_range,
    quality_report_to_dict,
)
from athena_research.commodity_data_audit.freeze_range import (
    RequestedRangeBoundary,
    requested_range_to_manifest_dict,
    resolve_requested_range,
)
from athena_research.commodity_data_audit.freeze_registry import (
    MANIFEST_SCHEMA_V3,
    MIN_BARS_BY_TIMEFRAME,
    MIN_RELIABLE_H4_BARS,
    QA_ALGORITHM_VERSION_V3_1,
    default_reliable_peer_symbol,
    resolve_phase1_mt5_symbol,
    slug_symbol,
)
from athena_research.commodity_data_audit.freeze_store import (
    FreezeStoreError,
    git_commit,
    hash_file_bytes,
    hash_stable_json,
    load_raw_bars_from_store,
    manifest_path,
    qa_manifest_path,
    read_json,
    repo_root,
    resolve_path,
    resolve_raw_evidence_hashes,
    verify_manifest_raw_evidence_unchanged,
    write_manifest_if_absent,
)
from athena_research.commodity_data_audit.reliable_era_gap_review import (
    DEFAULT_RELIABLE_START,
    adjust_reliable_quality_for_legitimate_closures,
    review_to_dict,
    review_reliable_era_abnormal_gaps,
)
from athena_research.commodity_data_audit.spread_audit import (
    classify_spread_field,
    spread_audit_to_dict,
)


@dataclass(frozen=True)
class ReqaV31Result:
    ok: bool
    canonical_symbol: str
    terminal_symbol: str
    timeframe: str
    bar_count: int
    reliable_era_bar_count: int
    manifest_path: str
    issues: tuple[str, ...]
    empirical_gate: str


def _bar_date(bar: dict[str, Any]) -> str:
    ts = bar["time"]
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def reqa_v3_1_one_series(
    *,
    canonical_symbol: str,
    timeframe: str,
    requested_range: RequestedRangeBoundary,
    raw_root: Path | None = None,
    normalized_root: Path | None = None,
    original_manifest_path: Path | None = None,
    as_of: datetime | None = None,
    d1_bars: list[dict[str, Any]] | None = None,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None = None,
    gap_review_payload: dict[str, Any] | None = None,
    gap_reviews: list | None = None,
    peer_symbol: str | None = None,
) -> ReqaV31Result:
    terminal_symbol = resolve_phase1_mt5_symbol(canonical_symbol)
    slug = slug_symbol(canonical_symbol)
    raw_root = raw_root or resolve_path("logs/commodity_data_audit/raw/v1")
    normalized_root = normalized_root or resolve_path("logs/commodity_data_audit/normalized/commodity_ohlcv_v1")
    min_bars = MIN_BARS_BY_TIMEFRAME.get(timeframe.upper(), 1)

    original_manifest_file = original_manifest_path or manifest_path(normalized_root, slug, timeframe)
    if not original_manifest_file.exists():
        raise FreezeStoreError(f"original manifest not found: {original_manifest_file}")

    original_manifest = read_json(original_manifest_file)
    original_manifest_hash = hash_file_bytes(original_manifest_file)
    raw_hashes = resolve_raw_evidence_hashes(raw_root, slug, timeframe)
    verify_manifest_raw_evidence_unchanged(original_manifest, raw_hashes)

    bars = load_raw_bars_from_store(raw_root, slug, timeframe)
    ranged = filter_bars_to_requested_range(
        bars,
        start_inclusive=requested_range.start_inclusive,
        end_exclusive=requested_range.end_exclusive,
    )
    acquisition_as_of = as_of or requested_range.as_of or datetime.now(timezone.utc)
    confirmed = exclude_forming_bar(
        ranged,
        timeframe=timeframe,
        as_of=acquisition_as_of,
    )

    if d1_bars is None and copy_rates is not None and timeframe.upper() == "H4":
        try:
            d1_bars = copy_rates(
                terminal_symbol,
                "D1",
                requested_range.start_inclusive,
                min(requested_range.end_exclusive, acquisition_as_of),
            )
        except Exception:
            d1_bars = None

    coverage = audit_h4_coverage(
        confirmed,
        min_reliable_bars=MIN_RELIABLE_H4_BARS,
        d1_bars=d1_bars,
        shared_absence_evidence={},
    )
    research_eligible = filter_research_eligible_bars(confirmed, coverage)
    reliable_bars = [
        bar
        for bar in confirmed
        if coverage.reliable_research_start
        and _bar_date(bar) >= coverage.reliable_research_start
    ]
    reliable_start = coverage.reliable_research_start or DEFAULT_RELIABLE_START

    if gap_reviews is None and timeframe.upper() == "H4":
        from athena_research.commodity_data_audit.gap_forensic import load_chunk_boundaries

        resolved_peer = peer_symbol if peer_symbol is not None else default_reliable_peer_symbol(canonical_symbol)
        peer_bars = None
        peer_terminal = None
        if resolved_peer:
            peer_slug = slug_symbol(resolved_peer)
            try:
                peer_bars = load_raw_bars_from_store(raw_root, peer_slug, timeframe.upper())
                peer_terminal = resolve_phase1_mt5_symbol(resolved_peer)
            except Exception:
                peer_bars = None
                peer_terminal = None
        gap_reviews = review_reliable_era_abnormal_gaps(
            bars=bars,
            chunks=load_chunk_boundaries(raw_root, slug, timeframe),
            reliable_start=reliable_start,
            copy_rates=copy_rates,
            d1_bars=d1_bars,
            subject_terminal=terminal_symbol,
            peer_terminal=peer_terminal,
            peer_bars=peer_bars,
        )
        gap_review_payload = review_to_dict(
            gap_reviews,
            canonical_symbol=canonical_symbol,
            peer_symbol=resolved_peer,
            timeframe=timeframe,
        )
    elif gap_review_payload is None and gap_reviews is not None:
        gap_review_payload = review_to_dict(
            gap_reviews,
            canonical_symbol=canonical_symbol,
            peer_symbol=peer_symbol if peer_symbol is not None else default_reliable_peer_symbol(canonical_symbol),
            timeframe=timeframe,
        )

    symbol_metadata = original_manifest.get("symbol_metadata") or {}
    full_quality = audit_freeze_bars(
        confirmed,
        timeframe=timeframe,
        requested_start=requested_range.start_inclusive,
        requested_end=requested_range.end_exclusive,
        min_bars=min_bars,
        as_of=acquisition_as_of,
        qa_algorithm_version=QA_ALGORITHM_VERSION_V3_1,
    )
    reliable_quality = audit_freeze_bars(
        reliable_bars,
        timeframe=timeframe,
        requested_start=datetime.fromisoformat(reliable_start).replace(tzinfo=timezone.utc),
        requested_end=requested_range.end_exclusive,
        min_bars=MIN_RELIABLE_H4_BARS,
        as_of=acquisition_as_of,
        qa_algorithm_version=QA_ALGORITHM_VERSION_V3_1,
    ) if reliable_bars else full_quality

    closure_adjustment: dict[str, Any] = {"adjusted": False}
    if gap_reviews:
        reliable_quality, closure_adjustment = adjust_reliable_quality_for_legitimate_closures(
            reliable_quality,
            gap_reviews,
        )

    spread = classify_spread_field(research_eligible, point_size=symbol_metadata.get("point"))
    reliable_spread = classify_spread_field(reliable_bars, point_size=symbol_metadata.get("point")) if reliable_bars else spread

    reliable_depth_ok = coverage.reliable_era_bar_count >= MIN_RELIABLE_H4_BARS
    reliable_quality_ok = reliable_quality.ok if reliable_bars else False
    all_legitimate = bool(gap_review_payload and gap_review_payload.get("all_legitimate_closures"))
    if (
        coverage.reliable_research_start is not None
        and reliable_depth_ok
        and reliable_quality_ok
        and all_legitimate
    ):
        gate_ok = True
        empirical_gate = "CLEAR_ON_FREEZE"
    else:
        gate_ok = False
        if not reliable_depth_ok or coverage.reliable_research_start is None:
            empirical_gate = "BLOCKED_INSUFFICIENT_RELIABLE_HISTORY"
        elif not all_legitimate:
            empirical_gate = "BLOCKED_RELIABLE_ERA_GAP_REVIEW"
        else:
            empirical_gate = "BLOCKED_RELIABLE_ERA_QUALITY"

    v3_manifest_path = qa_manifest_path(
        normalized_root,
        slug,
        timeframe,
        qa_algorithm_version="commodity_freeze_qa_v3",
    )
    prior_v3_hash = hash_file_bytes(v3_manifest_path) if v3_manifest_path.exists() else None

    manifest = {
        "schema": MANIFEST_SCHEMA_V3,
        "qa_algorithm_version": QA_ALGORITHM_VERSION_V3_1,
        "tool_version": f"commodity_reqa_{QA_ALGORITHM_VERSION_V3_1}",
        "git_commit": git_commit(),
        "source": original_manifest.get("source", "mt5"),
        "broker": original_manifest.get("broker", {}),
        "canonical_symbol": canonical_symbol,
        "terminal_symbol": terminal_symbol,
        "timeframe": timeframe.upper(),
        "requested_range": requested_range_to_manifest_dict(requested_range),
        "actual_range": {
            "start": full_quality.first_timestamp,
            "end": full_quality.last_timestamp,
        },
        "provider_actual_start": coverage.provider_actual_start,
        "reliable_research_start": coverage.reliable_research_start,
        "warmup_adjusted_usable_start": coverage.warmup_adjusted_usable_start,
        "row_count": len(confirmed),
        "research_eligible_row_count": len(research_eligible),
        "reliable_era_row_count": len(reliable_bars),
        "excluded_bar_count": coverage.excluded_bar_count,
        "excluded_day_count": coverage.excluded_day_count,
        "exclusion_reason": coverage.exclusion_reason,
        "coverage_audit": coverage_report_to_dict(coverage),
        "reliable_era_gap_review": gap_review_payload or {},
        "reliable_era_gap_review_peer_symbol": (
            (gap_review_payload or {}).get("peer_symbol")
            or peer_symbol
            or default_reliable_peer_symbol(canonical_symbol)
        ),
        "reliable_era_closure_adjustment": closure_adjustment,
        "quality_full_series": quality_report_to_dict(full_quality),
        "quality_reliable_era": quality_report_to_dict(reliable_quality),
        "spread_audit_research_eligible": spread_audit_to_dict(spread),
        "spread_audit_reliable_era": spread_audit_to_dict(reliable_spread),
        "normalized_schema": original_manifest.get("normalized_schema"),
        "symbol_metadata": symbol_metadata,
        "raw_path": original_manifest.get("raw_path"),
        "normalized_path": original_manifest.get("normalized_path"),
        "raw_content_hash": raw_hashes["merged_bar_content_hash"],
        "chunk_set_hash": raw_hashes.get("chunk_set_hash") or original_manifest.get("chunk_set_hash"),
        "merged_bar_content_hash": raw_hashes["merged_bar_content_hash"],
        "legacy_merged_raw_file_hash": raw_hashes.get("legacy_merged_raw_file_hash") or None,
        "normalized_content_hash": original_manifest.get("normalized_content_hash"),
        "original_manifest_path": str(original_manifest_file.relative_to(repo_root())).replace("\\", "/"),
        "original_manifest_content_hash": original_manifest_hash,
        "original_manifest_schema": original_manifest.get("schema"),
        "prior_v3_manifest_path": str(v3_manifest_path.relative_to(repo_root())).replace("\\", "/") if prior_v3_hash else None,
        "prior_v3_manifest_content_hash": prior_v3_hash,
        "reqa_at": datetime.now(timezone.utc).isoformat(),
        "empirical_gate": empirical_gate,
        "gate_notes": [
            "Pre-cutoff historical intraday degradation does not block freeze permanently.",
            "Research eligibility excludes DEGRADED_UNUSABLE era bars; raw remains immutable.",
            "Reliable-era Christmas/New Year gaps reclassified as LEGITIMATE_MARKET_CLOSURE when corroborated.",
            "MT5 refetch alone is insufficient for legitimate closure classification.",
        ],
    }
    manifest["content_hash"] = hash_stable_json(
        {k: v for k, v in manifest.items() if k != "content_hash"}
    )

    manifest_file = qa_manifest_path(
        normalized_root,
        slug,
        timeframe,
        qa_algorithm_version=QA_ALGORITHM_VERSION_V3_1,
    )
    write_manifest_if_absent(manifest_file, manifest)

    issues = tuple(reliable_quality.issues if reliable_bars else full_quality.issues)
    return ReqaV31Result(
        ok=gate_ok,
        canonical_symbol=canonical_symbol,
        terminal_symbol=terminal_symbol,
        timeframe=timeframe.upper(),
        bar_count=len(confirmed),
        reliable_era_bar_count=len(reliable_bars),
        manifest_path=str(manifest_file.relative_to(repo_root())).replace("\\", "/"),
        issues=issues,
        empirical_gate=empirical_gate,
    )


def reqa_v3_1_from_original_manifest_inputs(
    *,
    canonical_symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str = "",
    end_time: str = "",
    as_of: datetime | None = None,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None = None,
    gap_reviews: list | None = None,
    gap_review_payload: dict[str, Any] | None = None,
    peer_symbol: str | None = None,
) -> ReqaV31Result:
    original_manifest_path = manifest_path(
        resolve_path("logs/commodity_data_audit/normalized/commodity_ohlcv_v1"),
        slug_symbol(canonical_symbol),
        timeframe.upper(),
    )
    original_manifest = read_json(original_manifest_path)
    original_as_of = original_manifest.get("quality", {}).get("details", {}).get("as_of")
    parsed_as_of = datetime.fromisoformat(str(original_as_of)) if original_as_of else None
    requested_range = resolve_requested_range(
        start_date=start_date,
        end_date=end_date,
        end_time=end_time,
        as_of=as_of or parsed_as_of,
    )
    return reqa_v3_1_one_series(
        canonical_symbol=canonical_symbol,
        timeframe=timeframe.upper(),
        requested_range=requested_range,
        original_manifest_path=original_manifest_path,
        as_of=as_of or parsed_as_of,
        copy_rates=copy_rates,
        gap_reviews=gap_reviews,
        gap_review_payload=gap_review_payload,
        peer_symbol=peer_symbol,
    )
