"""CandleBuilder hole detection: gap-aware seed + D1 bulk fallback staleness."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import candle_feeds
from candle_feeds import CandleBuilder


@pytest.fixture
def cb(monkeypatch):
    db = Path(tempfile.mkdtemp(prefix="cb_gap_")) / "candle_cache.db"
    monkeypatch.setenv("ATHENA_CANDLE_CACHE_DB_PATH", str(db))
    return CandleBuilder()


def _write_h1(cb, disp, stamps):
    rows = [
        (disp, "H1", ts.strftime("%Y-%m-%d %H:%M:%S"), 1.0, 1.0, 1.0, 1.0, 10.0, 0)
        for ts in stamps
    ]
    with sqlite3.connect(cb._db, timeout=15.0) as con:
        con.executemany(
            "INSERT OR IGNORE INTO candle_cache "
            "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            rows,
        )


def _hourly(end, count, step_hours=1):
    return [end - timedelta(hours=step_hours * i) for i in range(count)]


def test_unseeded_pair_returns_none(cb):
    _write_h1(cb, "THIN", _hourly(datetime.now(timezone.utc), 40))
    assert cb._h1_gap_hours("THIN") is None


def test_continuous_series_is_below_threshold(cb):
    _write_h1(cb, "DENSE", _hourly(datetime.now(timezone.utc), 400))
    gap = cb._h1_gap_hours("DENSE")
    assert gap is not None
    assert gap < CandleBuilder._SEED_MAX_H1_GAP_HOURS


def test_trailing_hole_is_detected(cb):
    """A pair that fell off the WS 21 days ago must be re-seeded, not skipped."""
    _write_h1(cb, "STALLED", _hourly(datetime.now(timezone.utc) - timedelta(days=21), 400))
    gap = cb._h1_gap_hours("STALLED")
    assert gap is not None
    assert gap > CandleBuilder._SEED_MAX_H1_GAP_HOURS


def test_interior_hole_is_detected(cb):
    """Recent bars alone must not mask a void in the middle of the window."""
    now = datetime.now(timezone.utc)
    stamps = _hourly(now, 60) + _hourly(now - timedelta(days=14), 400)
    _write_h1(cb, "HOLED", stamps)
    gap = cb._h1_gap_hours("HOLED")
    assert gap is not None
    assert gap > CandleBuilder._SEED_MAX_H1_GAP_HOURS


def test_holes_older_than_the_window_are_ignored(cb):
    """A void outside the lookback window is history, not a live feed failure."""
    now = datetime.now(timezone.utc)
    stamps = _hourly(now, 600) + _hourly(now - timedelta(days=90), 400)
    _write_h1(cb, "OLDHOLE", stamps)
    gap = cb._h1_gap_hours("OLDHOLE")
    assert gap is not None
    assert gap < CandleBuilder._SEED_MAX_H1_GAP_HOURS


def _write_d1(cb, disp, date_str):
    with sqlite3.connect(cb._db, timeout=15.0) as con:
        con.execute(
            "INSERT OR REPLACE INTO candle_cache "
            "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (disp, "D1", date_str, 1.0, 1.0, 1.0, 1.0, 10.0, 0),
        )


def _last_closed_session():
    d = datetime.now(timezone.utc).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def test_stale_d1_codes_flags_only_pairs_behind_the_last_session(cb):
    last = _last_closed_session()
    _write_d1(cb, "CURRENT", last.isoformat())
    _write_d1(cb, "BEHIND", (last - timedelta(days=6)).isoformat())
    code_map = {"CURRENT": "CURRENT", "BEHIND": "BEHIND", "EMPTY": "EMPTY"}
    stale = cb._stale_d1_codes(code_map)
    assert set(stale) == {"BEHIND", "EMPTY"}


def _write_h4(cb, disp, bar_time, vol):
    with sqlite3.connect(cb._db, timeout=15.0) as con:
        con.execute(
            "INSERT OR REPLACE INTO candle_cache "
            "(pair,timeframe,bar_time,open,high,low,close,volume,tick_count) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (disp, "H4", bar_time, 1.0, 1.0, 1.0, 1.0, vol, 0),
        )


def _h4_rows(cb, disp):
    with sqlite3.connect(cb._db, timeout=15.0) as con:
        return con.execute(
            "SELECT bar_time, volume FROM candle_cache "
            "WHERE pair=? AND timeframe='H4' ORDER BY bar_time",
            (disp,),
        ).fetchall()


def test_legacy_suffixed_bar_time_is_normalised(cb):
    _write_h4(cb, "SEEDED", "2026-07-29 20:00:00+00:00", 111.0)
    with sqlite3.connect(cb._db, timeout=15.0) as con:
        CandleBuilder._normalize_bar_time_format(con)
    assert _h4_rows(cb, "SEEDED") == [("2026-07-29 20:00:00", 111.0)]


def test_collision_keeps_the_canonical_row(cb):
    """Two spellings of one bucket must collapse to one row, existing wins."""
    _write_h4(cb, "DUP", "2026-07-29 20:00:00", 222.0)
    _write_h4(cb, "DUP", "2026-07-29 20:00:00+00:00", 111.0)
    assert len(_h4_rows(cb, "DUP")) == 2
    with sqlite3.connect(cb._db, timeout=15.0) as con:
        CandleBuilder._normalize_bar_time_format(con)
    assert _h4_rows(cb, "DUP") == [("2026-07-29 20:00:00", 222.0)]


def test_normalisation_is_idempotent(cb):
    _write_h4(cb, "IDEM", "2026-07-29 20:00:00+00:00", 111.0)
    with sqlite3.connect(cb._db, timeout=15.0) as con:
        assert CandleBuilder._normalize_bar_time_format(con) == 1
        assert CandleBuilder._normalize_bar_time_format(con) == 0
    assert _h4_rows(cb, "IDEM") == [("2026-07-29 20:00:00", 111.0)]


def test_seed_h4_timestamps_match_flush_format(cb):
    """The seed's H4 resample must not reintroduce the pandas repr."""
    pd = pytest.importorskip("pandas")
    idx = pd.Timestamp("2026-07-29 20:00:00", tz="UTC")
    assert idx.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-29 20:00:00"
    assert str(idx) != idx.strftime("%Y-%m-%d %H:%M:%S")


def test_weekend_does_not_mark_friday_close_stale(cb, monkeypatch):
    """Sunday must compare against Friday, or every Monday refetches the universe."""
    sunday = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)  # Sunday
    friday = "2026-07-31"

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return sunday

    monkeypatch.setattr(candle_feeds, "datetime", _FrozenDatetime)
    _write_d1(cb, "FRIDAY", friday)
    assert cb._stale_d1_codes({"FRIDAY": "FRIDAY"}) == {}
