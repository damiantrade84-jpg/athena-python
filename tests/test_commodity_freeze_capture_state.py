from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from athena_research.commodity_data_audit.acquisition_run import (
    compute_confirmed_chunk_set_hash,
    infer_legacy_pinned_as_of,
    record_interval_revision,
    rebuild_research_merge,
    research_eligible_bars,
    write_interval_revision,
)
from athena_research.commodity_data_audit.capture_state import (
    CaptureState,
    annotate_bars_at_capture,
    provisional_bars,
)
from athena_research.commodity_data_audit.freeze import fetch_chunks_with_resume
from athena_research.commodity_data_audit.freeze_quality import exclude_forming_bar
from athena_research.commodity_data_audit.freeze_store import (
    FreezeStoreError,
    list_chunk_files,
    read_json,
    rebuild_merged_from_chunks,
    repo_root,
    state_path,
    write_json_immutable,
)


def _scratch_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"_tmp_capture_{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _bar(ts: datetime, close: float = 1.0) -> dict:
    return {
        "time": int(ts.timestamp()),
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "tick_volume": 100,
        "spread": 5,
        "real_volume": 0,
    }


def test_resume_after_forming_bar_closes_keeps_pinned_provisional_exclusion():
    tmp_path = _scratch_dir("forming_resume")
    try:
        pinned = datetime(2026, 6, 19, 19, 25, 47, tzinfo=timezone.utc)
        bars = [
            _bar(datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc), close=12.0),
            _bar(datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc), close=16.0),
            _bar(datetime(2026, 6, 19, 20, 0, tzinfo=timezone.utc), close=20.0),
        ]

        def _copy(_terminal: str, _tf: str, _s: datetime, _e: datetime) -> list[dict]:
            return bars

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=datetime(2026, 6, 19, tzinfo=timezone.utc),
            requested_end=datetime(2026, 6, 20, tzinfo=timezone.utc),
            chunk_days=30,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
            as_of=pinned,
            new_acquisition_run=True,
        )
        state = read_json(state_path(tmp_path, "XAG_USD", "H4"))
        merged = rebuild_merged_from_chunks(tmp_path, "XAG_USD", "H4")
        confirmed = research_eligible_bars(merged, timeframe="H4", pinned_as_of=pinned)
        provisional = provisional_bars(merged, timeframe="H4", pinned_as_of=pinned)
        assert len(confirmed) == 1
        assert len(provisional) == 2

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=datetime(2026, 6, 19, tzinfo=timezone.utc),
            requested_end=datetime(2026, 6, 20, tzinfo=timezone.utc),
            chunk_days=30,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        merged_after = rebuild_merged_from_chunks(tmp_path, "XAG_USD", "H4")
        confirmed_after = research_eligible_bars(
            merged_after, timeframe="H4", pinned_as_of=pinned
        )
        assert len(confirmed_after) == 1
        later = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
        would_confirm_now = exclude_forming_bar(merged_after, timeframe="H4", as_of=later)
        assert len(would_confirm_now) > len(confirmed_after)
        assert state["pinned_as_of"] == pinned.isoformat()
    finally:
        _cleanup(tmp_path)


def test_stale_provisional_ohlc_never_promoted_by_time():
    pinned = datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc)
    bar = {
        **_bar(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)),
        "capture_state": CaptureState.PROVISIONAL_AT_CAPTURE.value,
        "capture_as_of": pinned.isoformat(),
        "close": 9.99,
    }
    later = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert len(research_eligible_bars([bar], timeframe="H4", pinned_as_of=later)) == 0
    assert len(provisional_bars([bar], timeframe="H4", pinned_as_of=later)) == 1


def test_pinned_as_of_reused_on_resume_and_rejects_mismatch():
    tmp_path = _scratch_dir("pin_reuse")
    try:
        pinned = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)

        def _copy(_terminal: str, _tf: str, s: datetime, _e: datetime) -> list[dict]:
            return [_bar(s)]

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            requested_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
            chunk_days=20,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
            as_of=pinned,
            new_acquisition_run=True,
        )
        state = read_json(state_path(tmp_path, "XAG_USD", "H4"))
        assert state["pinned_as_of"] == pinned.isoformat()

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            requested_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
            chunk_days=20,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
        )
        state2 = read_json(state_path(tmp_path, "XAG_USD", "H4"))
        assert state2["pinned_as_of"] == pinned.isoformat()

        with pytest.raises(FreezeStoreError, match="requested as_of differs"):
            fetch_chunks_with_resume(
                canonical_symbol="XAG/USD",
                terminal_symbol="XAGUSD",
                timeframe="H4",
                requested_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                requested_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
                chunk_days=20,
                copy_rates=_copy,
                raw_root=tmp_path,
                resume=True,
                as_of=datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc),
            )
    finally:
        _cleanup(tmp_path)


def test_new_acquisition_run_pins_later_as_of():
    tmp_path = _scratch_dir("new_run")
    try:
        first_pin = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
        second_pin = datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)

        def _copy(_terminal: str, _tf: str, s: datetime, _e: datetime) -> list[dict]:
            return [_bar(s)]

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            requested_end=datetime(2024, 1, 15, tzinfo=timezone.utc),
            chunk_days=10,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
            as_of=first_pin,
            new_acquisition_run=True,
        )
        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            requested_end=datetime(2024, 1, 15, tzinfo=timezone.utc),
            chunk_days=10,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
            as_of=second_pin,
            new_acquisition_run=True,
        )
        state = read_json(state_path(tmp_path, "XAG_USD", "H4"))
        assert state["pinned_as_of"] == second_pin.isoformat()
    finally:
        _cleanup(tmp_path)


def test_same_interval_chunk_revision_preserves_primary_chunk():
    tmp_path = _scratch_dir("revision")
    try:
        pinned = datetime(2026, 6, 19, 19, 25, 47, tzinfo=timezone.utc)
        interval_key = "2026-06-19T00:00:00+00:00__2026-06-20T00:00:00+00:00"
        primary = [
            annotate_bars_at_capture(
                [_bar(datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc), close=16.0)],
                timeframe="H4",
                as_of=pinned,
            )[0]
        ]
        chunk_file = (
            tmp_path
            / "XAG_USD"
            / "H4"
            / "chunks"
            / "20260619T000000Z__20260620T000000Z.json"
        )
        write_json_immutable(chunk_file, primary)
        primary_hash = chunk_file.read_bytes()

        confirmed_bar = annotate_bars_at_capture(
            [_bar(datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc), close=16.5)],
            timeframe="H4",
            as_of=datetime(2026, 6, 19, 21, 0, tzinfo=timezone.utc),
        )[0]
        revision_path = write_interval_revision(
            tmp_path,
            "XAG_USD",
            "H4",
            interval_key=interval_key,
            revision_id="20260619T210000Z_confirmed",
            bars=[confirmed_bar],
            supersession_of=str(chunk_file.relative_to(repo_root())).replace("\\", "/"),
            pinned_as_of=datetime(2026, 6, 19, 21, 0, tzinfo=timezone.utc),
            revision_kind="confirmed_refresh",
        )
        assert chunk_file.read_bytes() == primary_hash
        assert Path(revision_path).exists()
    finally:
        _cleanup(tmp_path)


def test_confirmed_revision_supersedes_provisional_evidence_in_research_merge():
    tmp_path = _scratch_dir("supersede")
    try:
        pinned_old = datetime(2026, 6, 19, 19, 25, 47, tzinfo=timezone.utc)
        interval_key = "2026-06-19T00:00:00+00:00__2026-06-20T00:00:00+00:00"
        primary = annotate_bars_at_capture(
            [
                _bar(datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc), close=12.0),
                _bar(datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc), close=16.0),
            ],
            timeframe="H4",
            as_of=pinned_old,
        )
        chunk_file = (
            tmp_path
            / "XAG_USD"
            / "H4"
            / "chunks"
            / "20260619T000000Z__20260620T000000Z.json"
        )
        write_json_immutable(chunk_file, primary)
        revision_path = write_interval_revision(
            tmp_path,
            "XAG_USD",
            "H4",
            interval_key=interval_key,
            revision_id="20260619T210000Z_confirmed",
            bars=annotate_bars_at_capture(
                [_bar(datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc), close=16.5)],
                timeframe="H4",
                as_of=datetime(2026, 6, 19, 21, 0, tzinfo=timezone.utc),
            ),
            supersession_of=str(chunk_file.relative_to(repo_root())).replace("\\", "/"),
            pinned_as_of=datetime(2026, 6, 19, 21, 0, tzinfo=timezone.utc),
            revision_kind="confirmed_refresh",
        )
        state = {
            "completed_chunks": [interval_key],
            "pinned_as_of": pinned_old.isoformat(),
            "chunk_revisions": {},
        }
        state = record_interval_revision(
            state,
            interval_key=interval_key,
            primary_chunk=str(chunk_file.relative_to(repo_root())).replace("\\", "/"),
            revision_path=revision_path,
            revision_id="20260619T210000Z_confirmed",
            supersession_of=str(chunk_file.relative_to(repo_root())).replace("\\", "/"),
            pinned_as_of=datetime(2026, 6, 19, 21, 0, tzinfo=timezone.utc),
            revision_kind="confirmed_refresh",
        )
        merged = rebuild_research_merge(tmp_path, "XAG_USD", "H4", state=state)
        by_time = {int(bar["time"]): bar for bar in merged}
        ts_16 = int(datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc).timestamp())
        assert by_time[ts_16]["close"] == 16.5
        raw_only = rebuild_merged_from_chunks(tmp_path, "XAG_USD", "H4")
        assert {int(bar["time"]): bar["close"] for bar in raw_only}[ts_16] == 16.0
    finally:
        _cleanup(tmp_path)


def test_deterministic_confirmed_chunk_set_hash():
    tmp_path = _scratch_dir("confirmed_hash")
    try:
        pinned = datetime(2026, 6, 19, 19, 25, 47, tzinfo=timezone.utc)
        bars = annotate_bars_at_capture(
            [
                _bar(datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc), close=12.0),
                _bar(datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc), close=16.0),
                _bar(datetime(2026, 6, 19, 20, 0, tzinfo=timezone.utc), close=20.0),
            ],
            timeframe="H4",
            as_of=pinned,
        )
        chunk_file = (
            tmp_path
            / "XAG_USD"
            / "H4"
            / "chunks"
            / "20260619T000000Z__20260620T000000Z.json"
        )
        write_json_immutable(chunk_file, bars)
        first = compute_confirmed_chunk_set_hash(
            tmp_path, "XAG_USD", "H4", pinned_as_of=pinned
        )
        second = compute_confirmed_chunk_set_hash(
            tmp_path, "XAG_USD", "H4", pinned_as_of=pinned
        )
        assert first == second
        full_rows = rebuild_merged_from_chunks(tmp_path, "XAG_USD", "H4")
        assert len(full_rows) == 3
        confirmed = research_eligible_bars(full_rows, timeframe="H4", pinned_as_of=pinned)
        assert len(confirmed) == 1
    finally:
        _cleanup(tmp_path)


def test_new_chunks_mark_capture_state_at_write():
    tmp_path = _scratch_dir("annotate")
    try:
        pinned = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)

        def _copy(_terminal: str, _tf: str, s: datetime, _e: datetime) -> list[dict]:
            return [_bar(s)]

        fetch_chunks_with_resume(
            canonical_symbol="XAG/USD",
            terminal_symbol="XAGUSD",
            timeframe="H4",
            requested_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            requested_end=datetime(2024, 1, 20, tzinfo=timezone.utc),
            chunk_days=10,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=True,
            as_of=pinned,
            new_acquisition_run=True,
        )
        chunk = read_json(list_chunk_files(tmp_path, "XAG_USD", "H4")[0])
        assert chunk[0]["capture_state"] in {
            CaptureState.CONFIRMED_AT_CAPTURE.value,
            CaptureState.PROVISIONAL_AT_CAPTURE.value,
        }
        assert chunk[0]["capture_as_of"] == pinned.isoformat()
    finally:
        _cleanup(tmp_path)


def test_xag_legacy_store_infers_pinned_as_of_and_yields_16514_confirmed():
    raw_root = Path("logs/commodity_data_audit/raw/v1")
    xag_h4 = raw_root / "XAG_USD" / "H4"
    if not xag_h4.exists() or not list_chunk_files(raw_root, "XAG_USD", "H4"):
        pytest.skip("XAG H4 forensic store not present")
    pinned = infer_legacy_pinned_as_of(raw_root, "XAG_USD", "H4")
    bars = rebuild_merged_from_chunks(raw_root, "XAG_USD", "H4")
    confirmed = research_eligible_bars(bars, timeframe="H4", pinned_as_of=pinned)
    provisional = provisional_bars(bars, timeframe="H4", pinned_as_of=pinned)
    assert len(bars) == 16516
    assert len(confirmed) == 16514
    assert len(provisional) == 2
    final_chunk = read_json(
        raw_root / "XAG_USD" / "H4" / "chunks" / "20260428T000000Z__20260620T000000Z.json"
    )
    final_provisional = provisional_bars(final_chunk, timeframe="H4", pinned_as_of=pinned)
    assert len(final_provisional) == 2
    times = {
        datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc).isoformat()
        for bar in final_provisional
    }
    assert times == {
        "2026-06-19T16:00:00+00:00",
        "2026-06-19T20:00:00+00:00",
    }
