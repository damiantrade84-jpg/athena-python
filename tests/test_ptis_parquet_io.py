"""Tests for safe PTIS/journal Parquet I/O."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
from athena_ase.data.parquet_io import is_valid_parquet_file, read_parquet_safe, write_parquet_atomic
from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.execution.journal import trade_journal_summary


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_read_rejects_four_byte_parquet(tmp_path: Path):
    part = tmp_path / "2025" / "data.parquet"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"null")

    frame = read_parquet_safe(part)

    assert frame.empty
    assert not part.exists()
    quarantined = list(part.parent.glob("data.parquet.corrupt.*"))
    assert len(quarantined) == 1


def test_read_missing_engine_raises_without_quarantine(tmp_path: Path, monkeypatch):
    part = tmp_path / "2025" / "data.parquet"
    part.parent.mkdir(parents=True)
    write_parquet_atomic(part, pd.DataFrame({"a": [1]}))

    def _no_engine(*args, **kwargs):
        raise ImportError("Unable to find a usable engine")

    monkeypatch.setattr(pd, "read_parquet", _no_engine)
    with pytest.raises(ImportError):
        read_parquet_safe(part)

    assert part.exists()
    assert list(part.parent.glob("data.parquet.corrupt.*")) == []


def test_append_rows_merges_after_quarantined_corrupt_existing(tmp_path: Path):
    store = PTISStore(tmp_path / "ptis")
    series_id = "MT5:GLD:H1:close"
    part_path = store.partition_file("MT5", series_id, 2025)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b"null")

    t0 = _ms(datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc))
    avail = compute_available_time_ms(
        AvailabilityRuleId.MT5_BAR,
        value_time_ms=t0,
        bar_close_ms=t0,
    )
    n = store.append_rows(
        series_id,
        [build_row(series_id, t0, avail, 210.0)],
        source="MT5",
        auto_register=True,
    )

    assert n == 1
    assert part_path.exists()
    assert is_valid_parquet_file(part_path)
    row = store.asof(series_id, avail, n=1)[0]
    assert row["value"] == pytest.approx(210.0)


def test_write_parquet_atomic_survives_concurrent_read(tmp_path: Path):
    store = PTISStore(tmp_path / "ptis")
    series_id = "MT5:EURUSD:H1:close"
    store.register_series(series_id, "MT5")
    t_base = _ms(datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc))
    errors: list[str] = []

    def reader() -> None:
        for _ in range(40):
            try:
                store.load_window(series_id, t_base, t_base + 40 * 3_600_000)
            except Exception as exc:
                errors.append(str(exc))
            time.sleep(0.002)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    for i in range(20):
        vt = t_base + i * 3_600_000
        store.append_rows(
            series_id,
            [build_row(series_id, vt, vt + 90_000, 1.08 + i * 0.0001)],
            source="MT5",
            auto_register=False,
        )
    thread.join(timeout=5.0)

    assert errors == []


def test_trade_journal_summary_tolerates_corrupt_file(tmp_path: Path):
    journal = tmp_path / "ase_trade_journal.parquet"
    journal.write_bytes(b"null")

    summary = trade_journal_summary(journal)

    assert summary["totalRows"] == 0
    assert not journal.exists()


def test_write_parquet_atomic_produces_valid_file(tmp_path: Path):
    path = tmp_path / "out.parquet"
    df = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
    write_parquet_atomic(path, df)
    assert is_valid_parquet_file(path)
    loaded = pd.read_parquet(path)
    assert len(loaded) == 2
