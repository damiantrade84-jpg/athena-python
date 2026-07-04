"""Tests for athena_fx.total_return — swap-adjusted return series."""
from __future__ import annotations

import pytest

from athena_fx.models import StrictFactorDataError
from athena_fx import store as fx_store
from athena_fx.total_return import (
    build_total_return_series,
    currency_total_return_indexes,
    persist_total_return,
)


def _audit_row(**overrides) -> dict:
    base = {
        "symbol": "AUDJPY",
        "status": "ok",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "daily_swap_return_long": 0.00001,
        "daily_swap_return_short": -0.000008,
        "swap_rollover3days": 3,
    }
    base.update(overrides)
    return base


def _five_bar_fixture():
    """Mon–Fri week; Wed 2026-01-07 is triple-swap day (rollover3=3)."""
    return [
        {"time": "2026-01-05", "close": 100.0},
        {"time": "2026-01-06", "close": 101.0},
        {"time": "2026-01-07", "close": 102.0},  # Wednesday
        {"time": "2026-01-08", "close": 101.5},
        {"time": "2026-01-09", "close": 103.0},
    ]


def test_total_return_long_short_math_and_triple_swap():
    bars = _five_bar_fixture()
    audit = _audit_row()
    rows = build_total_return_series(bars, audit, symbol="AUDJPY", strict=True)

    assert len(rows) == 4  # first bar skipped (no prior close)

    # First output row: 2026-01-06, spot 101/100 - 1 = 0.01
    assert rows[0].date == "2026-01-06"
    assert rows[0].spot_return == pytest.approx(0.01, rel=1e-9)
    assert rows[0].long_swap_return == pytest.approx(0.00001)
    assert rows[0].long_total_return == pytest.approx(0.01 + 0.00001, rel=1e-9)
    assert rows[0].short_total_return == pytest.approx(-0.01 + (-0.000008), rel=1e-9)

    # Wednesday triple swap: 3x daily rate
    wed = next(r for r in rows if r.date == "2026-01-07")
    assert wed.long_swap_return == pytest.approx(0.00001 * 3, rel=1e-12)
    assert wed.short_swap_return == pytest.approx(-0.000008 * 3, rel=1e-12)


def test_no_lookahead_first_bar_skipped():
    bars = _five_bar_fixture()
    rows = build_total_return_series(bars, _audit_row(), symbol="AUDJPY")
    assert rows[0].date != "2026-01-05"
    assert rows[0].spot_return == pytest.approx(0.01)


def test_index_compounding():
    bars = _five_bar_fixture()
    rows = build_total_return_series(bars, _audit_row(), symbol="AUDJPY")
    idx = 100.0
    for row in rows:
        idx *= 1.0 + row.long_total_return
        assert row.long_total_return_index == pytest.approx(idx, rel=1e-9)


def test_strict_missing_swap_raises():
    bars = _five_bar_fixture()
    with pytest.raises(StrictFactorDataError) as exc:
        build_total_return_series(bars, None, symbol="AUDJPY", strict=True)
    assert any(d.code == "missing_swap" for d in exc.value.diagnostics)


def test_degraded_mode_zero_swap():
    bars = _five_bar_fixture()
    rows = build_total_return_series(bars, None, symbol="AUDJPY", strict=False)
    assert len(rows) == 4
    assert all(r.data_quality_status == "degraded" for r in rows)
    assert all(r.long_swap_return == 0.0 for r in rows)
    assert all(r.short_swap_return == 0.0 for r in rows)


def test_invalid_audit_degraded():
    bars = _five_bar_fixture()
    rows = build_total_return_series(
        bars,
        {"status": "invalid", "generated_at": "2026-01-01T00:00:00+00:00"},
        symbol="AUDJPY",
        strict=False,
    )
    assert all(r.data_quality_status == "degraded" for r in rows)


@pytest.fixture
def fx_db(tmp_path, monkeypatch):
    db = str(tmp_path / "fx_tr.db")
    monkeypatch.setenv("ATHENA_FX_DB_PATH", db)
    return db


def test_persist_and_load_roundtrip(fx_db):
    bars = _five_bar_fixture()
    audit = _audit_row()
    rows = build_total_return_series(bars, audit, symbol="AUDJPY")
    persist_total_return(rows, db_path=fx_db)
    loaded = fx_store.load_total_return("AUDJPY", db_path=fx_db)
    assert len(loaded) == len(rows)
    for src, dst in zip(rows, loaded):
        assert dst["date"] == src.date
        assert dst["long_total_return_index"] == pytest.approx(
            src.long_total_return_index, rel=1e-9
        )
        assert dst["data_quality_status"] == src.data_quality_status


def test_currency_total_return_indexes():
    bars = _five_bar_fixture()
    audit = _audit_row()
    audjpy_rows = build_total_return_series(bars, audit, symbol="AUDJPY")
    eurusd_rows = build_total_return_series(
        [
            {"time": "2026-01-05", "close": 1.10},
            {"time": "2026-01-06", "close": 1.11},
            {"time": "2026-01-07", "close": 1.12},
        ],
        {**audit, "symbol": "EURUSD"},
        symbol="EURUSD",
    )
    usdjpy_rows = build_total_return_series(
        [
            {"time": "2026-01-05", "close": 150.0},
            {"time": "2026-01-06", "close": 151.0},
            {"time": "2026-01-07", "close": 152.0},
        ],
        {**audit, "symbol": "USDJPY"},
        symbol="USDJPY",
    )

    indexes = currency_total_return_indexes({
        "AUDJPY": audjpy_rows,
        "EURUSD": eurusd_rows,
        "USDJPY": usdjpy_rows,
    })
    assert "EUR" in indexes
    assert "JPY" in indexes
    assert indexes["EUR"][0]["index"] == pytest.approx(eurusd_rows[0].long_total_return_index)
    assert indexes["JPY"][0]["index"] == pytest.approx(usdjpy_rows[0].short_total_return_index)
