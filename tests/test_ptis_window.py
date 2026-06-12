"""PTIS load_window parity and partition tests."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import numpy as np
import pytest

from athena_ase.data.ptis import PTISStore, build_row

_MS_H = 3_600_000


def _ms(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture
def store(tmp_path):
    return PTISStore(tmp_path / "ptis")


def _seed_series(store: PTISStore, series_id: str, points: list[tuple[int, float, int, int]]):
    """(value_time, value, available_time, revision)"""
    store.register_series(series_id, "EODHD")
    rows = [
        build_row(series_id, vt, av, val, revision=rev)
        for vt, val, av, rev in points
    ]
    store.append_rows(series_id, rows, source="EODHD", auto_register=False)


def test_load_window_asof_parity_random_decisions(store: PTISStore):
    series_id = "EODHD:TEST:H1:close"
    base = _ms(2024, 6, 1, 0)
    pts = []
    for i in range(200):
        vt = base + i * _MS_H
        av = vt + 90_000
        rev = 1 if i == 150 else 0
        pts.append((vt, 1.0 + i * 0.001, av, rev))
    _seed_series(store, series_id, pts)

    rng = random.Random(42)
    for _ in range(50):
        i = rng.randint(20, 199)
        t = pts[i][2] + rng.randint(0, 5000)
        n = rng.randint(1, 10)
        expected = store.asof(series_id, t, n)
        window = store.load_window(series_id, base, pts[-1][0])
        masked = window[window["available_time"] <= t]
        got = masked[-n:] if len(masked) >= n else masked
        assert len(got) == len(expected)
        if len(expected):
            np.testing.assert_array_equal(got["value_time"], expected["value_time"])
            np.testing.assert_array_almost_equal(got["value"], expected["value"])


def test_load_window_revision_wins(store: PTISStore):
    series_id = "EODHD:REV:H1:close"
    vt = _ms(2025, 1, 1, 12)
    av = vt + 90_000
    _seed_series(
        store,
        series_id,
        [(vt, 1.0, av, 0), (vt, 2.0, av, 1)],
    )
    rows = store.load_window(series_id, vt, vt)
    assert len(rows) == 1
    assert rows[0]["value"] == pytest.approx(2.0)


def test_load_window_spans_year_boundary(store: PTISStore):
    series_id = "EODHD:YEAR:H1:close"
    dec = _ms(2024, 12, 31, 23)
    jan = _ms(2025, 1, 1, 0)
    _seed_series(
        store,
        series_id,
        [(dec, 1.0, dec + 90_000, 0), (jan, 2.0, jan + 90_000, 0)],
    )
    rows = store.load_window(series_id, dec, jan)
    assert len(rows) == 2
