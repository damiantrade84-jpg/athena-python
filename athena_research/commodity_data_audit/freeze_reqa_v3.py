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
    QA_ALGORITHM_VERSION_V3,
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
from athena_research.commodity_data_audit.spread_audit import (
    classify_spread_field,
    spread_audit_to_dict,
)


@dataclass(frozen=True)
class ReqaV3Result:
    ok: bool
    canonical_symbol: str
    terminal_symbol: str
    timeframe: str
    bar_count: int
    reliable_era_bar_count: int
    manifest_path: str
    issues: tuple[str, ...]
    empirical_gate: str


def _load_forensic_shared_absence() -> dict[str, Any]:
    path = resolve_path("logs/commodity_data_audit/forensics/xau_h4/xau_h4_refetch_results.json")
    if not path.exists():
        return {}
    payload = read_json(path)
    results = payload.get("results") or []
    shared = sum(1 for row in results if row.get("xagusd", {}).get("shared_metals_session_closure_candidate"))
    return {
        "source": str(path.relative_to(repo_root())).replace("\\", "/"),
        "sample_size": len(results),
        "shared_metals_session_closure_candidates": shared,
    }


def reqa_v3_one_series(
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
) -> ReqaV3Result:
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

    shared_absence = _load_forensic_shared_absence() if canonical_symbol == "XAU/USD" else {}
    coverage = audit_h4_coverage(
        confirmed,
        min_reliable_bars=MIN_RELIABLE_H4_BARS,
        d1_bars=d1_bars,
        shared_absence_evidence=shared_absence,
    )
    research_eligible = filter_research_eligible_bars(confirmed, coverage)
    reliable_bars = [
        bar
        for bar in confirmed
        if coverage.reliable_research_start
        and _bar_date(bar) >= coverage.reliable_research_start
    ]

    symbol_metadata = original_manifest.get("symbol_metadata") or {}
    full_quality = audit_freeze_bars(
        confirmed,
        timeframe=timeframe,
        requested_start=requested_range.start_inclusive,
        requested_end=requested_range.end_exclusive,
        min_bars=min_bars,
        as_of=acquisition_as_of,
        qa_algorithm_version=QA_ALGORITHM_VERSION_V3,
    )
    reliable_quality = audit_freeze_bars(
        reliable_bars,
        timeframe=timeframe,
        requested_start=datetime.fromisoformat(coverage.reliable_research_start).replace(tzinfo=timezone.utc)
        if coverage.reliable_research_start
        else requested_range.start_inclusive,
        requested_end=requested_range.end_exclusive,
        min_bars=MIN_RELIABLE_H4_BARS,
        as_of=acquisition_as_of,
        qa_algorithm_version=QA_ALGORITHM_VERSION_V3,
    ) if reliable_bars else full_quality

    spread = classify_spread_field(research_eligible, point_size=symbol_metadata.get("point"))
    reliable_spread = classify_spread_field(reliable_bars, point_size=symbol_metadata.get("point")) if reliable_bars else spread

    empirical_gate = coverage.empirical_gate
    reliable_depth_ok = coverage.reliable_era_bar_count >= MIN_RELIABLE_H4_BARS
    reliable_quality_ok = reliable_quality.ok if reliable_bars else False
    if empirical_gate == "CLEAR_ON_FREEZE" and reliable_depth_ok and reliable_quality_ok:
        gate_ok = True
        empirical_gate = "CLEAR_ON_FREEZE"
    else:
        gate_ok = False
        if not reliable_depth_ok or coverage.reliable_research_start is None:
            empirical_gate = "BLOCKED_INSUFFICIENT_RELIABLE_HISTORY"
        elif not reliable_quality_ok:
            empirical_gate = "BLOCKED_RELIABLE_ERA_QUALITY"

    manifest = {
        "schema": MANIFEST_SCHEMA_V3,
        "qa_algorithm_version": QA_ALGORITHM_VERSION_V3,
        "tool_version": f"commodity_reqa_{QA_ALGORITHM_VERSION_V3}",
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
        "reqa_at": datetime.now(timezone.utc).isoformat(),
        "empirical_gate": empirical_gate,
        "gate_notes": [
            "Pre-cutoff historical intraday degradation does not block freeze permanently.",
            "Research eligibility excludes DEGRADED_UNUSABLE era bars; raw remains immutable.",
            "EXPECTED_DAILY_MAINTENANCE reclassification rejected.",
        ],
    }
    manifest["content_hash"] = hash_stable_json(
        {k: v for k, v in manifest.items() if k != "content_hash"}
    )

    manifest_file = qa_manifest_path(
        normalized_root,
        slug,
        timeframe,
        qa_algorithm_version=QA_ALGORITHM_VERSION_V3,
    )
    write_manifest_if_absent(manifest_file, manifest)

    issues = tuple(reliable_quality.issues if reliable_bars else full_quality.issues)
    return ReqaV3Result(
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


def _bar_date(bar: dict[str, Any]) -> str:
    ts = bar["time"]
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def reqa_v3_from_original_manifest_inputs(
    *,
    canonical_symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str = "",
    end_time: str = "",
    as_of: datetime | None = None,
    copy_rates: Callable[[str, str, datetime, datetime], list[dict[str, Any]]] | None = None,
) -> ReqaV3Result:
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
    return reqa_v3_one_series(
        canonical_symbol=canonical_symbol,
        timeframe=timeframe.upper(),
        requested_range=requested_range,
        original_manifest_path=original_manifest_path,
        as_of=as_of or parsed_as_of,
        copy_rates=copy_rates,
    )
