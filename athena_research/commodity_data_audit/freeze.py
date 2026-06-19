from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from athena_research.commodity_data_audit.acquisition_run import (
    compute_confirmed_chunk_set_hash,
    compute_confirmed_research_content_hash,
    prepare_bars_for_immutable_chunk_write,
    provisional_bars,
    rebuild_research_merge,
    research_eligible_bars,
    resolve_pinned_as_of_for_resume,
)
from athena_research.commodity_data_audit.freeze_quality import (
    audit_freeze_bars,
    quality_report_to_dict,
)
from athena_research.commodity_data_audit.freeze_range import (
    RequestedRangeBoundary,
    requested_range_to_manifest_dict,
)
from athena_research.commodity_data_audit.freeze_registry import (
    DEFAULT_REQUESTED_START,
    MANIFEST_SCHEMA,
    MIN_BARS_BY_TIMEFRAME,
    NORMALIZED_ROOT,
    NORMALIZED_SCHEMA,
    RAW_ROOT,
    resolve_phase1_mt5_symbol,
    slug_symbol,
)
from athena_research.commodity_data_audit.freeze_store import (
    FreezeStoreError,
    chunk_path,
    git_commit,
    iter_utc_chunks,
    load_or_init_state,
    manifest_path,
    merge_bars_by_time,
    normalize_bars,
    normalized_path,
    publish_chunk_derived_merge,
    read_json,
    repo_root,
    resolve_path,
    resolve_raw_evidence_hashes,
    save_state,
    state_path,
    write_json_immutable,
    write_manifest_if_absent,
    hash_stable_json,
)
from athena_research.commodity_data_audit.mt5_reader import (
    CopyRatesFn,
    Mt5ReadError,
    broker_identity,
    copy_rates_range_utc,
    fetch_symbol_metadata,
)
from athena_research.commodity_data_audit.metadata_policy import (
    sanitize_broker_metadata,
    sanitize_symbol_metadata,
)
from athena_research.commodity_data_audit.spread_audit import (
    classify_spread_field,
    spread_audit_to_dict,
)

TOOL_VERSION = "commodity_freeze_phase1_v1"


@dataclass(frozen=True)
class FreezeSeriesResult:
    ok: bool
    canonical_symbol: str
    terminal_symbol: str
    timeframe: str
    bar_count: int
    manifest_path: str
    issues: tuple[str, ...]


def last_confirmed_bar_end(*, timeframe: str, as_of: datetime | None = None) -> datetime:
    as_of_utc = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return as_of_utc


def fetch_chunks_with_resume(
    *,
    canonical_symbol: str,
    terminal_symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    chunk_days: int,
    copy_rates: CopyRatesFn,
    raw_root: Path,
    resume: bool,
    as_of: datetime | None = None,
    new_acquisition_run: bool = False,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    slug = slug_symbol(canonical_symbol)
    chunks = iter_utc_chunks(requested_start, requested_end, chunk_days=chunk_days)
    state_file = state_path(raw_root, slug, timeframe)
    state = {"completed_chunks": []} if not resume else load_or_init_state(state_file)
    pinned_as_of, state = resolve_pinned_as_of_for_resume(
        state,
        requested_as_of=as_of,
        raw_root=raw_root,
        slug=slug,
        timeframe=timeframe,
        new_run=new_acquisition_run or not resume,
    )
    save_state(state_file, state)
    completed = set(state.get("completed_chunks") or [])
    loaded_chunks: list[list[dict[str, Any]]] = []

    for start, end in chunks:
        key = f"{start.isoformat()}__{end.isoformat()}"
        path = chunk_path(raw_root, slug, timeframe, start, end)
        if resume and key in completed and path.exists():
            loaded_chunks.append(read_json(path))
            continue
        bars = copy_rates(terminal_symbol, timeframe, start, end)
        annotated = prepare_bars_for_immutable_chunk_write(
            bars,
            timeframe=timeframe,
            pinned_as_of=pinned_as_of,
        )
        write_json_immutable(path, annotated)
        if key not in completed:
            state.setdefault("completed_chunks", []).append(key)
            save_state(state_file, state)
        loaded_chunks.append(annotated)

    merged = merge_bars_by_time(loaded_chunks)
    merged = rebuild_research_merge(raw_root, slug, timeframe, state=state)
    confirmed_count = len(
        research_eligible_bars(merged, timeframe=timeframe, pinned_as_of=pinned_as_of)
    )
    provisional_count = len(
        provisional_bars(merged, timeframe=timeframe, pinned_as_of=pinned_as_of)
    )
    publish_chunk_derived_merge(
        raw_root,
        slug,
        timeframe,
        pinned_as_of=pinned_as_of,
        confirmed_chunk_set_hash=compute_confirmed_chunk_set_hash(
            raw_root, slug, timeframe, pinned_as_of=pinned_as_of
        ),
        confirmed_research_content_hash=compute_confirmed_research_content_hash(
            raw_root, slug, timeframe, pinned_as_of=pinned_as_of
        ),
        confirmed_row_count=confirmed_count,
        provisional_row_count=provisional_count,
    )
    return merged, [], state


def freeze_one_series(
    *,
    canonical_symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    chunk_days: int,
    copy_rates: CopyRatesFn,
    symbol_metadata: dict[str, Any],
    broker: dict[str, Any],
    raw_root: Path | None = None,
    normalized_root: Path | None = None,
    resume: bool = True,
    as_of: datetime | None = None,
    new_acquisition_run: bool = False,
) -> FreezeSeriesResult:
    terminal_symbol = resolve_phase1_mt5_symbol(canonical_symbol)
    slug = slug_symbol(canonical_symbol)
    raw_root = raw_root or resolve_path(RAW_ROOT)
    normalized_root = normalized_root or resolve_path(NORMALIZED_ROOT)
    min_bars = MIN_BARS_BY_TIMEFRAME.get(timeframe.upper(), 1)

    merged, _warnings, state = fetch_chunks_with_resume(
        canonical_symbol=canonical_symbol,
        terminal_symbol=terminal_symbol,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        chunk_days=chunk_days,
        copy_rates=copy_rates,
        raw_root=raw_root,
        resume=resume,
        as_of=as_of,
        new_acquisition_run=new_acquisition_run,
    )
    pinned_as_of = datetime.fromisoformat(str(state["pinned_as_of"]))
    if pinned_as_of.tzinfo is None:
        pinned_as_of = pinned_as_of.replace(tzinfo=timezone.utc)
    confirmed = research_eligible_bars(
        merged,
        timeframe=timeframe,
        pinned_as_of=pinned_as_of,
    )
    provisional = provisional_bars(
        merged,
        timeframe=timeframe,
        pinned_as_of=pinned_as_of,
    )
    quality = audit_freeze_bars(
        confirmed,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        min_bars=min_bars,
        as_of=pinned_as_of,
    )

    spread = classify_spread_field(confirmed, point_size=symbol_metadata.get("point"))
    normalized = normalize_bars(confirmed)
    norm_file = normalized_path(normalized_root, slug, timeframe)
    merge_meta = publish_chunk_derived_merge(
        raw_root,
        slug,
        timeframe,
        pinned_as_of=pinned_as_of,
        confirmed_chunk_set_hash=compute_confirmed_chunk_set_hash(
            raw_root, slug, timeframe, pinned_as_of=pinned_as_of
        ),
        confirmed_research_content_hash=compute_confirmed_research_content_hash(
            raw_root, slug, timeframe, pinned_as_of=pinned_as_of
        ),
        confirmed_row_count=len(confirmed),
        provisional_row_count=len(provisional),
    )
    raw_hashes = resolve_raw_evidence_hashes(raw_root, slug, timeframe)
    norm_hash = write_json_immutable(norm_file, normalized)

    safe_broker = sanitize_broker_metadata(broker)
    safe_symbol_metadata = sanitize_symbol_metadata(symbol_metadata)

    requested_boundary = RequestedRangeBoundary(
        start_inclusive=requested_start.astimezone(timezone.utc),
        end_exclusive=requested_end.astimezone(timezone.utc),
        start_input=requested_start.date().isoformat(),
        end_input=requested_end.isoformat(),
        end_boundary_semantics="legacy_instant",
        end_boundary_inclusive=None,
        as_of=pinned_as_of,
    )

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "tool_version": TOOL_VERSION,
        "git_commit": git_commit(),
        "source": "mt5",
        "broker": safe_broker,
        "canonical_symbol": canonical_symbol,
        "terminal_symbol": terminal_symbol,
        "timeframe": timeframe.upper(),
        "requested_range": requested_range_to_manifest_dict(requested_boundary),
        "actual_range": {
            "start": quality.first_timestamp,
            "end": quality.last_timestamp,
        },
        "row_count": len(confirmed),
        "confirmed_row_count": len(confirmed),
        "provisional_row_count_at_capture": len(provisional),
        "pinned_as_of": pinned_as_of.isoformat(),
        "acquisition_run_id": state.get("acquisition_run_id"),
        "confirmed_chunk_set_hash": merge_meta.get("confirmed_chunk_set_hash"),
        "confirmed_research_content_hash": merge_meta.get("confirmed_research_content_hash"),
        "selected_chunk_revisions": state.get("chunk_revisions") or {},
        "normalized_schema": NORMALIZED_SCHEMA,
        "symbol_metadata": safe_symbol_metadata,
        "quality": quality_report_to_dict(quality),
        "spread_audit": spread_audit_to_dict(spread),
        "raw_path": merge_meta["snapshot_path"],
        "chunk_set_hash": raw_hashes["chunk_set_hash"],
        "merged_bar_content_hash": raw_hashes["merged_bar_content_hash"],
        "normalized_path": str(norm_file.relative_to(repo_root())).replace("\\", "/"),
        "raw_content_hash": raw_hashes["merged_bar_content_hash"],
        "legacy_merged_raw_file_hash": raw_hashes.get("legacy_merged_raw_file_hash") or None,
        "normalized_content_hash": norm_hash,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["content_hash"] = hash_stable_json(
        {k: v for k, v in manifest.items() if k != "content_hash"}
    )
    manifest_file = manifest_path(normalized_root, slug, timeframe)
    write_manifest_if_absent(manifest_file, manifest)

    issues = tuple(quality.issues)
    ok = quality.ok and len(confirmed) >= min_bars
    return FreezeSeriesResult(
        ok=ok,
        canonical_symbol=canonical_symbol,
        terminal_symbol=terminal_symbol,
        timeframe=timeframe.upper(),
        bar_count=len(confirmed),
        manifest_path=str(manifest_file.relative_to(repo_root())).replace("\\", "/"),
        issues=issues,
    )


def default_copy_rates(mt5: Any) -> CopyRatesFn:
    def _copy(terminal_symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return copy_rates_range_utc(mt5, terminal_symbol, timeframe, start, end)

    return _copy
