from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from athena_research.commodity_data_audit.freeze import fetch_chunks_with_resume
from athena_research.commodity_data_audit.freeze_quality import exclude_forming_bar
from athena_research.commodity_data_audit.freeze_store import (
    FreezeStoreError,
    compute_chunk_set_hash,
    hash_stable_json,
    latest_merge_pointer_path,
    list_chunk_files,
    merge_snapshot_path,
    merged_raw_path,
    publish_chunk_derived_merge,
    read_json,
    rebuild_merged_from_chunks,
    verify_manifest_raw_evidence_unchanged,
    write_json_immutable,
)
from athena_research.commodity_data_audit.store_forensic import compare_merged_bars


def _scratch_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"_tmp_store_{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _bar(ts: int, close: float = 1.0, spread: int = 5) -> dict:
    return {
        "time": ts,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "tick_volume": 100,
        "spread": spread,
        "real_volume": 0,
    }


def test_incremental_acquisition_adds_new_immutable_chunks():
    tmp_path = _scratch_dir("incremental")
    try:
        start = datetime(2014, 1, 1, tzinfo=timezone.utc)
        mid = datetime(2014, 2, 1, tzinfo=timezone.utc)
        end = datetime(2014, 3, 1, tzinfo=timezone.utc)

        def _copy(_terminal: str, _tf: str, s: datetime, e: datetime) -> list[dict]:
            return [_bar(int(s.timestamp()), close=float(s.day))]

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=start,
            requested_end=mid,
            chunk_days=15,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        first_chunks = list_chunk_files(tmp_path, "XAG_USD", "H4")
        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=start,
            requested_end=end,
            chunk_days=15,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        second_chunks = list_chunk_files(tmp_path, "XAG_USD", "H4")
        assert len(second_chunks) > len(first_chunks)
    finally:
        _cleanup(tmp_path)


def test_deterministic_merge_of_identical_chunks():
    tmp_path = _scratch_dir("deterministic")
    try:
        chunk_dir = tmp_path / "XAG_USD" / "H4" / "chunks"
        chunk_dir.mkdir(parents=True)
        bars = [_bar(1), _bar(2)]
        write_json_immutable(chunk_dir / "20140101T000000Z__20140201T000000Z.json", bars)
        first = publish_chunk_derived_merge(tmp_path, "XAG_USD", "H4")
        second = publish_chunk_derived_merge(tmp_path, "XAG_USD", "H4")
        assert first["chunk_set_hash"] == second["chunk_set_hash"]
        assert first["merged_bar_content_hash"] == second["merged_bar_content_hash"]
    finally:
        _cleanup(tmp_path)


def test_content_addressed_merge_snapshots_are_immutable():
    tmp_path = _scratch_dir("snapshot")
    try:
        chunk_dir = tmp_path / "XAG_USD" / "H4" / "chunks"
        chunk_dir.mkdir(parents=True)
        write_json_immutable(chunk_dir / "20140101T000000Z__20140201T000000Z.json", [_bar(1)])
        meta = publish_chunk_derived_merge(tmp_path, "XAG_USD", "H4")
        snapshot = merge_snapshot_path(tmp_path, "XAG_USD", "H4", meta["chunk_set_hash"])
        assert snapshot.exists()
        with pytest.raises(FreezeStoreError):
            write_json_immutable(snapshot, [_bar(2)])
    finally:
        _cleanup(tmp_path)


def test_broker_revision_at_existing_timestamp_fails_closed_on_chunk():
    tmp_path = _scratch_dir("broker_revision")
    try:
        chunk_file = tmp_path / "XAG_USD" / "H4" / "chunks" / "20140101T000000Z__20140201T000000Z.json"
        chunk_file.parent.mkdir(parents=True)
        write_json_immutable(chunk_file, [_bar(1, close=1.0)])
        with pytest.raises(FreezeStoreError):
            write_json_immutable(chunk_file, [_bar(1, close=1.1)])
    finally:
        _cleanup(tmp_path)


def test_forming_bar_differences_do_not_mutate_chunk_merge_snapshot():
    tmp_path = _scratch_dir("forming")
    try:
        as_of = datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc)
        chunk_dir = tmp_path / "XAG_USD" / "H4" / "chunks"
        chunk_dir.mkdir(parents=True)
        bars = [
            _bar(int(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp())),
            _bar(int(datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc).timestamp())),
        ]
        write_json_immutable(chunk_dir / "20140101T000000Z__20140201T000000Z.json", bars)
        meta = publish_chunk_derived_merge(tmp_path, "XAG_USD", "H4")
        confirmed = exclude_forming_bar(bars, timeframe="H4", as_of=as_of)
        snapshot = read_json(merge_snapshot_path(tmp_path, "XAG_USD", "H4", meta["chunk_set_hash"]))
        assert hash_stable_json(snapshot) == meta["merged_bar_content_hash"]
        assert len(confirmed) < len(snapshot)
    finally:
        _cleanup(tmp_path)


def test_interrupted_resume_rebuilds_same_content_hash():
    tmp_path = _scratch_dir("resume")
    try:
        start = datetime(2014, 1, 1, tzinfo=timezone.utc)
        end = datetime(2014, 3, 1, tzinfo=timezone.utc)

        def _copy(_terminal: str, _tf: str, s: datetime, e: datetime) -> list[dict]:
            return [_bar(int(s.timestamp()), close=float(s.day))]

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=start,
            requested_end=end,
            chunk_days=20,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        first_hash = compute_chunk_set_hash(tmp_path, "XAG_USD", "H4")
        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=start,
            requested_end=end,
            chunk_days=20,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        second_hash = compute_chunk_set_hash(tmp_path, "XAG_USD", "H4")
        assert first_hash == second_hash
    finally:
        _cleanup(tmp_path)


def test_valid_extension_updates_latest_pointer_without_fixed_path_conflict():
    tmp_path = _scratch_dir("extension")
    try:
        legacy = merged_raw_path(tmp_path, "XAG_USD", "H4")
        legacy.parent.mkdir(parents=True)
        legacy.write_text("[]\n", encoding="utf-8")
        chunk_dir = tmp_path / "XAG_USD" / "H4" / "chunks"
        chunk_dir.mkdir(parents=True)
        write_json_immutable(chunk_dir / "20140101T000000Z__20140201T000000Z.json", [_bar(1)])
        before = legacy.read_text(encoding="utf-8")
        publish_chunk_derived_merge(tmp_path, "XAG_USD", "H4")
        after = legacy.read_text(encoding="utf-8")
        assert before == after
        assert latest_merge_pointer_path(tmp_path, "XAG_USD", "H4").exists()
    finally:
        _cleanup(tmp_path)


def test_legacy_merged_and_rebuilt_compare_identical():
    raw_root = Path("logs/commodity_data_audit/raw/v1")
    xag_h4 = raw_root / "XAG_USD" / "H4"
    if not xag_h4.exists():
        pytest.skip("XAG forensic store not present")
    existing = read_json(xag_h4 / "merged_raw.json")
    rebuilt = rebuild_merged_from_chunks(raw_root, "XAG_USD", "H4")
    report = compare_merged_bars(existing, rebuilt)
    assert report.identical is True


def test_verify_manifest_accepts_legacy_file_or_content_hash():
    raw_hashes = {
        "chunk_set_hash": "abc",
        "merged_bar_content_hash": "content",
        "legacy_merged_raw_file_hash": "legacy",
    }
    verify_manifest_raw_evidence_unchanged({"raw_content_hash": "legacy"}, raw_hashes)
    verify_manifest_raw_evidence_unchanged({"raw_content_hash": "content", "chunk_set_hash": "abc"}, raw_hashes)
    with pytest.raises(FreezeStoreError):
        verify_manifest_raw_evidence_unchanged({"chunk_set_hash": "different"}, raw_hashes)
