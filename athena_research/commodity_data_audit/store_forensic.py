from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from athena_research.commodity_data_audit.freeze_store import (
    hash_file_bytes,
    hash_stable_json,
    merge_bars_by_time,
    read_json,
)


class MergeCompareCategory(str, Enum):
    IDENTICAL = "identical"
    ROWS_ONLY_IN_EXISTING = "rows_only_in_existing"
    ROWS_ONLY_IN_REBUILT = "rows_only_in_rebuilt"
    CHANGED_SAME_TIMESTAMP = "changed_same_timestamp"
    DUPLICATE_TIMESTAMPS = "duplicate_timestamps"
    ORDERING_OR_SCHEMA = "ordering_or_schema"


@dataclass(frozen=True)
class ChunkInventoryRow:
    filename: str
    sha256: str
    row_count: int
    start_timestamp: str | None
    end_timestamp: str | None
    modified_at: str


@dataclass(frozen=True)
class MergeCompareReport:
    identical: bool
    existing_row_count: int
    rebuilt_row_count: int
    rows_only_in_existing: int
    rows_only_in_rebuilt: int
    changed_same_timestamp: int
    existing_duplicate_timestamps: int
    rebuilt_duplicate_timestamps: int
    categories: tuple[str, ...]
    existing_content_hash: str | None
    rebuilt_content_hash: str | None
    existing_file_hash: str | None
    sample_rows_only_in_existing: list[int]
    sample_rows_only_in_rebuilt: list[int]
    sample_changed_timestamps: list[int]


def _bar_range(bars: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    if not bars:
        return None, None
    times = sorted(int(bar["time"]) for bar in bars)
    start = datetime.fromtimestamp(times[0], tz=timezone.utc).isoformat()
    end = datetime.fromtimestamp(times[-1], tz=timezone.utc).isoformat()
    return start, end


def inventory_chunks(chunk_dir: Path) -> list[ChunkInventoryRow]:
    rows: list[ChunkInventoryRow] = []
    for path in sorted(chunk_dir.glob("*.json")):
        bars = read_json(path)
        start, end = _bar_range(bars)
        rows.append(
            ChunkInventoryRow(
                filename=path.name,
                sha256=hash_file_bytes(path),
                row_count=len(bars),
                start_timestamp=start,
                end_timestamp=end,
                modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            )
        )
    return rows


def rebuild_merged_from_chunk_dir(chunk_dir: Path) -> list[dict[str, Any]]:
    chunks = [read_json(path) for path in sorted(chunk_dir.glob("*.json"))]
    return merge_bars_by_time(chunks)


def compare_merged_bars(existing: list[dict[str, Any]], rebuilt: list[dict[str, Any]]) -> MergeCompareReport:
    existing_by_ts = {int(bar["time"]): bar for bar in existing}
    rebuilt_by_ts = {int(bar["time"]): bar for bar in rebuilt}
    only_existing = sorted(set(existing_by_ts) - set(rebuilt_by_ts))
    only_rebuilt = sorted(set(rebuilt_by_ts) - set(existing_by_ts))
    changed: list[int] = []
    for ts in sorted(set(existing_by_ts) & set(rebuilt_by_ts)):
        if hash_stable_json(existing_by_ts[ts]) != hash_stable_json(rebuilt_by_ts[ts]):
            changed.append(ts)

    from collections import Counter

    existing_dups = sum(1 for count in Counter(int(bar["time"]) for bar in existing).values() if count > 1)
    rebuilt_dups = sum(1 for count in Counter(int(bar["time"]) for bar in rebuilt).values() if count > 1)

    categories: list[str] = []
    if (
        not only_existing
        and not only_rebuilt
        and not changed
        and existing_dups == 0
        and rebuilt_dups == 0
        and len(existing) == len(rebuilt)
    ):
        categories.append(MergeCompareCategory.IDENTICAL.value)
    if only_existing:
        categories.append(MergeCompareCategory.ROWS_ONLY_IN_EXISTING.value)
    if only_rebuilt:
        categories.append(MergeCompareCategory.ROWS_ONLY_IN_REBUILT.value)
    if changed:
        categories.append(MergeCompareCategory.CHANGED_SAME_TIMESTAMP.value)
    if existing_dups or rebuilt_dups:
        categories.append(MergeCompareCategory.DUPLICATE_TIMESTAMPS.value)
    if [int(bar["time"]) for bar in existing] != [int(bar["time"]) for bar in rebuilt] and not only_existing and not only_rebuilt:
        categories.append(MergeCompareCategory.ORDERING_OR_SCHEMA.value)

    return MergeCompareReport(
        identical=MergeCompareCategory.IDENTICAL.value in categories,
        existing_row_count=len(existing),
        rebuilt_row_count=len(rebuilt),
        rows_only_in_existing=len(only_existing),
        rows_only_in_rebuilt=len(only_rebuilt),
        changed_same_timestamp=len(changed),
        existing_duplicate_timestamps=existing_dups,
        rebuilt_duplicate_timestamps=rebuilt_dups,
        categories=tuple(categories),
        existing_content_hash=hash_stable_json(existing) if existing else None,
        rebuilt_content_hash=hash_stable_json(rebuilt) if rebuilt else None,
        existing_file_hash=None,
        sample_rows_only_in_existing=only_existing[:10],
        sample_rows_only_in_rebuilt=only_rebuilt[:10],
        sample_changed_timestamps=changed[:10],
    )


def inventory_to_dict(rows: list[ChunkInventoryRow]) -> list[dict[str, Any]]:
    return [
        {
            "filename": row.filename,
            "sha256": row.sha256,
            "row_count": row.row_count,
            "start_timestamp": row.start_timestamp,
            "end_timestamp": row.end_timestamp,
            "modified_at": row.modified_at,
        }
        for row in rows
    ]


def compare_report_to_dict(report: MergeCompareReport) -> dict[str, Any]:
    return {
        "identical": report.identical,
        "existing_row_count": report.existing_row_count,
        "rebuilt_row_count": report.rebuilt_row_count,
        "rows_only_in_existing": report.rows_only_in_existing,
        "rows_only_in_rebuilt": report.rows_only_in_rebuilt,
        "changed_same_timestamp": report.changed_same_timestamp,
        "existing_duplicate_timestamps": report.existing_duplicate_timestamps,
        "rebuilt_duplicate_timestamps": report.rebuilt_duplicate_timestamps,
        "categories": list(report.categories),
        "existing_content_hash": report.existing_content_hash,
        "rebuilt_content_hash": report.rebuilt_content_hash,
        "sample_rows_only_in_existing": [
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in report.sample_rows_only_in_existing
        ],
        "sample_rows_only_in_rebuilt": [
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in report.sample_rows_only_in_rebuilt
        ],
        "sample_changed_timestamps": [
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in report.sample_changed_timestamps
        ],
    }
