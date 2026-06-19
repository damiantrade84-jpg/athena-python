from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from athena_research.commodity_data_audit.freeze import fetch_chunks_with_resume
from athena_research.commodity_data_audit.freeze_quality import audit_freeze_bars, filter_bars_to_requested_range
from athena_research.commodity_data_audit.freeze_range import resolve_requested_range
from athena_research.commodity_data_audit.freeze_registry import QA_ALGORITHM_VERSION
from athena_research.commodity_data_audit.freeze_reqa import reqa_one_series
from athena_research.commodity_data_audit.freeze_store import (
    hash_file_bytes,
    merge_bars_by_time,
    read_json,
    write_json_mutable,
    write_manifest_if_absent,
)
from athena_research.commodity_data_audit.gap_classification import (
    GapClass,
    SessionScheduleInference,
    classify_gap,
    classify_series_gaps,
)
from athena_research.commodity_data_audit.spread_audit import classify_spread_field


def _scratch_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / f"_tmp_qa_{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _bar(ts: int, close: float = 1.0, spread: int = 10) -> dict:
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


def _ts(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp())


def test_requested_start_on_holiday_is_not_truncation():
    report = audit_freeze_bars(
        [_bar(_ts(2014, 1, 2, 0), spread=5), _bar(_ts(2014, 1, 2, 4), spread=5)],
        timeframe="H4",
        requested_start=datetime(2014, 1, 1, tzinfo=timezone.utc),
        requested_end=datetime(2014, 2, 1, tzinfo=timezone.utc),
        min_bars=1,
        as_of=datetime(2014, 2, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert report.terminal_truncation_suspected is False


def test_chunk_start_on_weekend_does_not_flag_truncation():
    tmp_path = _scratch_dir("weekend_chunk")
    try:
        start = datetime(2014, 1, 4, tzinfo=timezone.utc)
        end = datetime(2014, 1, 11, tzinfo=timezone.utc)

        def _copy(_terminal: str, _tf: str, _s: datetime, _e: datetime) -> list[dict]:
            return [_bar(_ts(2014, 1, 6, 0), spread=5)]

        merged, warnings = fetch_chunks_with_resume(
            canonical_symbol="XAU/USD",
            terminal_symbol="XAUUSD",
            timeframe="H4",
            requested_start=start,
            requested_end=end,
            chunk_days=7,
            copy_rates=_copy,
            raw_root=tmp_path,
            resume=False,
        )
        assert warnings == []
        report = audit_freeze_bars(
            merged,
            timeframe="H4",
            requested_start=start,
            requested_end=end,
            min_bars=1,
            as_of=end,
        )
        assert report.terminal_truncation_suspected is False
    finally:
        _cleanup(tmp_path)


def test_normal_weekend_gap_classified_expected():
    timestamps = [
        pd.Timestamp(_ts(2014, 1, 3, 20), unit="s", tz="UTC"),
        pd.Timestamp(_ts(2014, 1, 6, 0), unit="s", tz="UTC"),
    ]
    report = classify_series_gaps(timestamps, timeframe="H4")
    assert report.counts[GapClass.EXPECTED_WEEKEND_CLOSURE.value] == 1


def test_genuine_missing_weekday_h4_bar_is_abnormal():
    schedule = SessionScheduleInference(
        active_slots={(dow, hour): True for dow in range(5) for hour in (0, 4, 8, 12, 16, 20)},
        slot_confidence={(dow, hour): 0.9 for dow in range(5) for hour in (0, 4, 8, 12, 16, 20)},
        overall_confidence=0.9,
        sample_bar_count=100,
        sample_week_count=10,
    )
    gap = classify_gap(
        prev=pd.Timestamp(datetime(2014, 1, 7, 0, tzinfo=timezone.utc)),
        curr=pd.Timestamp(datetime(2014, 1, 7, 12, tzinfo=timezone.utc)),
        timeframe="H4",
        schedule=schedule,
    )
    assert gap is not None
    assert gap.classification == GapClass.ABNORMAL_ACTIVE_SESSION_GAP


def test_chunk_boundary_continuity_without_truncation():
    merged = merge_bars_by_time(
        [
            [_bar(_ts(2014, 1, 1, 0)), _bar(_ts(2014, 1, 1, 4))],
            [_bar(_ts(2014, 1, 1, 4), close=2.0), _bar(_ts(2014, 1, 1, 8), close=3.0)],
        ]
    )
    report = classify_series_gaps(
        [pd.Timestamp(bar["time"], unit="s", tz="UTC") for bar in merged],
        timeframe="H4",
    )
    assert report.counts[GapClass.ABNORMAL_ACTIVE_SESSION_GAP.value] == 0


def test_initial_history_truncation_only_when_material():
    report = audit_freeze_bars(
        [_bar(_ts(2014, 1, 15, 0)), _bar(_ts(2014, 1, 15, 4))],
        timeframe="H4",
        requested_start=datetime(2014, 1, 1, tzinfo=timezone.utc),
        requested_end=datetime(2014, 6, 1, tzinfo=timezone.utc),
        min_bars=1,
        as_of=datetime(2014, 6, 1, tzinfo=timezone.utc),
    )
    assert report.terminal_truncation_suspected is True


def test_zero_spread_treated_as_missing():
    audit = classify_spread_field(
        [_bar(_ts(2014, 1, 2), spread=0), _bar(_ts(2014, 1, 2, 4), spread=5)],
        point_size=0.01,
    )
    assert audit.missing_spread_count == 1
    assert audit.observed_usable_count == 1
    assert audit.zero_count == 1


def test_date_only_end_date_uses_inclusive_end_of_day():
    boundary = resolve_requested_range(start_date="2014-01-01", end_date="2014-01-02")
    assert boundary.end_boundary_semantics == "inclusive_end_of_utc_day"
    assert boundary.end_exclusive == datetime(2014, 1, 3, tzinfo=timezone.utc)


def test_explicit_end_time_is_exclusive():
    boundary = resolve_requested_range(
        start_date="2014-01-01",
        end_time="2014-01-02T12:00:00+00:00",
    )
    filtered = filter_bars_to_requested_range(
        [_bar(_ts(2014, 1, 2, 8)), _bar(_ts(2014, 1, 2, 12))],
        start_inclusive=boundary.start_inclusive,
        end_exclusive=boundary.end_exclusive,
    )
    assert [bar["time"] for bar in filtered] == [_ts(2014, 1, 2, 8)]


def test_reqa_preserves_original_raw_hashes():
    tmp_path = _scratch_dir("reqa")
    try:
        raw_root = tmp_path / "raw"
        norm_root = tmp_path / "norm"
        end = datetime(2014, 2, 1, tzinfo=timezone.utc)
        t0 = _ts(2014, 1, 2, 0)
        bars = [_bar(t0 + (idx * 4 * 3600), close=1.0 + idx * 0.01, spread=5 + idx) for idx in range(520)]
        merged_path = raw_root / "XAU_USD" / "H4" / "merged_raw.json"
        merged_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_mutable(merged_path, bars)
        raw_hash = hash_file_bytes(merged_path)

        original_manifest_path = norm_root / "XAU_USD" / "H4.manifest.json"
        write_manifest_if_absent(
            original_manifest_path,
            {
                "schema": "commodity_freeze_manifest_v1",
                "raw_content_hash": raw_hash,
                "normalized_content_hash": "normhash",
                "raw_path": "raw/XAU_USD/H4/merged_raw.json",
                "normalized_path": "norm/XAU_USD/H4.json",
                "symbol_metadata": {"point": 0.01},
                "broker": {"company": "Pepperstone Group Limited", "server": "demo"},
                "source": "mt5",
                "normalized_schema": "commodity_ohlcv_v1",
                "quality": {"details": {"as_of": end.isoformat()}},
            },
        )
        original_manifest_hash = hash_file_bytes(original_manifest_path)
        boundary = resolve_requested_range(start_date="2014-01-01", end_date="2014-01-31")
        result = reqa_one_series(
            canonical_symbol="XAU/USD",
            timeframe="H4",
            requested_range=boundary,
            raw_root=raw_root,
            normalized_root=norm_root,
            original_manifest_path=original_manifest_path,
            as_of=end,
        )
        manifest = read_json(norm_root / "XAU_USD" / f"H4.manifest.{QA_ALGORITHM_VERSION}.json")
        assert manifest["raw_content_hash"] == raw_hash
        assert manifest["original_manifest_content_hash"] == original_manifest_hash
        assert hash_file_bytes(merged_path) == raw_hash
    finally:
        _cleanup(tmp_path)
