from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    MANIFEST_SCHEMA_V2,
    MIN_BARS_BY_TIMEFRAME,
    QA_ALGORITHM_VERSION,
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
class ReqaResult:
    ok: bool
    canonical_symbol: str
    terminal_symbol: str
    timeframe: str
    bar_count: int
    manifest_path: str
    issues: tuple[str, ...]
    empirical_gate: str


def reqa_one_series(
    *,
    canonical_symbol: str,
    timeframe: str,
    requested_range: RequestedRangeBoundary,
    raw_root: Path | None = None,
    normalized_root: Path | None = None,
    original_manifest_path: Path | None = None,
    as_of: datetime | None = None,
) -> ReqaResult:
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

    symbol_metadata = original_manifest.get("symbol_metadata") or {}
    quality = audit_freeze_bars(
        confirmed,
        timeframe=timeframe,
        requested_start=requested_range.start_inclusive,
        requested_end=requested_range.end_exclusive,
        min_bars=min_bars,
        as_of=acquisition_as_of,
        qa_algorithm_version=QA_ALGORITHM_VERSION,
    )
    spread = classify_spread_field(confirmed, point_size=symbol_metadata.get("point"))

    manifest = {
        "schema": MANIFEST_SCHEMA_V2,
        "qa_algorithm_version": QA_ALGORITHM_VERSION,
        "tool_version": f"commodity_reqa_{QA_ALGORITHM_VERSION}",
        "git_commit": git_commit(),
        "source": original_manifest.get("source", "mt5"),
        "broker": original_manifest.get("broker", {}),
        "canonical_symbol": canonical_symbol,
        "terminal_symbol": terminal_symbol,
        "timeframe": timeframe.upper(),
        "requested_range": requested_range_to_manifest_dict(requested_range),
        "actual_range": {
            "start": quality.first_timestamp,
            "end": quality.last_timestamp,
        },
        "row_count": len(confirmed),
        "normalized_schema": original_manifest.get("normalized_schema"),
        "symbol_metadata": symbol_metadata,
        "quality": quality_report_to_dict(quality),
        "spread_audit": spread_audit_to_dict(spread),
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
        "warnings": [],
    }
    manifest["content_hash"] = hash_stable_json(
        {k: v for k, v in manifest.items() if k != "content_hash"}
    )

    manifest_file = qa_manifest_path(
        normalized_root,
        slug,
        timeframe,
        qa_algorithm_version=QA_ALGORITHM_VERSION,
    )
    write_manifest_if_absent(manifest_file, manifest)

    empirical_gate = "CLEAR_ON_FREEZE" if quality.ok and len(confirmed) >= min_bars else "BLOCKED_INCOMPLETE_ACQUISITION"
    return ReqaResult(
        ok=quality.ok and len(confirmed) >= min_bars,
        canonical_symbol=canonical_symbol,
        terminal_symbol=terminal_symbol,
        timeframe=timeframe.upper(),
        bar_count=len(confirmed),
        manifest_path=str(manifest_file.relative_to(repo_root())).replace("\\", "/"),
        issues=tuple(quality.issues),
        empirical_gate=empirical_gate,
    )


def reqa_from_original_manifest_inputs(
    *,
    canonical_symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str = "",
    end_time: str = "",
    as_of: datetime | None = None,
) -> ReqaResult:
    original_manifest_path = manifest_path(
        resolve_path("logs/commodity_data_audit/normalized/commodity_ohlcv_v1"),
        slug_symbol(canonical_symbol),
        timeframe.upper(),
    )
    original_manifest = read_json(original_manifest_path)
    original_as_of = original_manifest.get("quality", {}).get("details", {}).get("as_of")
    parsed_as_of = None
    if original_as_of:
        parsed_as_of = datetime.fromisoformat(str(original_as_of))
    requested_range = resolve_requested_range(
        start_date=start_date,
        end_date=end_date,
        end_time=end_time,
        as_of=as_of or parsed_as_of,
    )
    return reqa_one_series(
        canonical_symbol=canonical_symbol,
        timeframe=timeframe.upper(),
        requested_range=requested_range,
        original_manifest_path=original_manifest_path,
        as_of=as_of or parsed_as_of,
    )
