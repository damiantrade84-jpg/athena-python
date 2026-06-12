"""PTIS ingest from legacy SQLite caches (ASE Phase 0 T0.2–T0.3)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
from athena_ase.data.ingest import cot as cot_ingest
from athena_ase.data.ingest import dukascopy, eodhd, fred, mt5
from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.signals.common import load_bar_series


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _seed_backtest_db(path, *, symbol="EURUSD", provider="eodhd_intraday"):
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            CREATE TABLE backtest_candles (
                cache_key TEXT NOT NULL,
                display TEXT,
                symbol TEXT,
                source TEXT,
                asset_type TEXT,
                timeframe TEXT NOT NULL,
                bar_time TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL DEFAULT 0,
                provider TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (cache_key, timeframe, bar_time)
            )
            """
        )
        key = "v2:pepperstone:eodhd_intraday:EURUSD"
        bar_time = "2025-06-02T12:00:00+00:00"
        con.execute(
            """
            INSERT INTO backtest_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key,
                "EUR/USD",
                symbol,
                "pepperstone",
                "forex",
                "H1",
                bar_time,
                1.08,
                1.09,
                1.07,
                1.085,
                1000,
                provider,
                1.0,
            ),
        )


def _seed_duka_db(path):
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            CREATE TABLE forex_volume (
                symbol TEXT, tf TEXT, bar_ts INTEGER, volume REAL,
                PRIMARY KEY (symbol, tf, bar_ts)
            )
            """
        )
        bar_ts = int(datetime(2025, 6, 2, 12, 0, tzinfo=timezone.utc).timestamp())
        con.execute(
            "INSERT INTO forex_volume VALUES (?,?,?,?)",
            ("EURUSD", "H1", bar_ts, 12345.0),
        )


def _seed_cot_db(path):
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            CREATE TABLE cot_net (
                asset TEXT, report_date TEXT, net_long INTEGER,
                PRIMARY KEY (asset, report_date)
            )
            """
        )
        con.execute(
            "INSERT INTO cot_net VALUES (?,?,?)",
            ("EUR", "2025-06-03", 50000),
        )


def _seed_carry_db(path):
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            CREATE TABLE rate_series (
                series_id TEXT, obs_date TEXT, rate REAL,
                PRIMARY KEY (series_id, obs_date)
            )
            """
        )
        con.execute(
            "INSERT INTO rate_series VALUES (?,?,?)",
            ("FEDFUNDS", "2025-06-02", 5.25),
        )


def test_eodhd_ingest_respects_bar_close_plus_90s(tmp_path, monkeypatch):
    bt_db = tmp_path / "bt.db"
    _seed_backtest_db(bt_db)
    store = PTISStore(tmp_path / "ptis")
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "source": "pepperstone",
        "type": "forex",
    }
    counts = eodhd.ingest_pair_tf(
        store, pair, "H1", "eodhd_intraday", db_path=str(bt_db)
    )
    sid = "EODHD:EURUSD:H1:close"
    assert counts[sid] == 1
    open_ms = _ms(datetime(2025, 6, 2, 12, 0, tzinfo=timezone.utc))
    close_ms = open_ms + 3_600_000
    expected_avail = compute_available_time_ms(
        AvailabilityRuleId.EODHD_BAR,
        value_time_ms=close_ms,
        bar_close_ms=close_ms,
    )
    before = store.asof(sid, expected_avail - 1, n=1)
    assert len(before) == 0
    row = store.asof(sid, expected_avail, n=1)[0]
    assert row["value"] == pytest.approx(1.085)
    assert row["available_time"] == expected_avail


def test_mt5_ingest_preserves_source_and_normalizes_forex_symbol(tmp_path):
    bt_db = tmp_path / "bt.db"
    _seed_backtest_db(bt_db, symbol="EURUSD=X", provider="mt5")
    store = PTISStore(tmp_path / "ptis")

    counts = mt5.ingest_all(store, db_path=str(bt_db))

    sid = "MT5:EURUSD:H1:close"
    assert counts[sid] == 1
    assert not store.series_exists("EODHD:EURUSD:H1:close")
    series = load_bar_series(
        store,
        "EURUSD",
        "intraday",
        _ms(datetime(2025, 6, 2, 0, 0, tzinfo=timezone.utc)),
        _ms(datetime(2025, 6, 3, 0, 0, tzinfo=timezone.utc)),
    )
    assert series is not None
    assert len(series.close_log) == 1


def test_dukascopy_ingest_bar_close_plus_5m(tmp_path):
    duka_db = tmp_path / "duka.db"
    _seed_duka_db(duka_db)
    store = PTISStore(tmp_path / "ptis")
    n = dukascopy.ingest_symbol(store, "EURUSD", tf="H1", db_path=str(duka_db))
    assert n == 1
    sid = "DUKASCOPY:EURUSD:H1:volume"
    open_ms = _ms(datetime(2025, 6, 2, 12, 0, tzinfo=timezone.utc))
    close_ms = open_ms + 3_600_000
    expected_avail = close_ms + 5 * 60_000
    assert len(store.asof(sid, expected_avail - 1, n=1)) == 0
    row = store.asof(sid, expected_avail, n=1)[0]
    assert row["value"] == pytest.approx(12345.0)


def test_cot_ingest_monday_availability(tmp_path):
    cot_db = tmp_path / "cot.db"
    _seed_cot_db(cot_db)
    store = PTISStore(tmp_path / "ptis")
    cot_ingest.ingest_asset(store, "EUR", db_path=str(cot_db))
    sid = "CFTC:COT:EUR:noncomm_net"
    rows = store.asof(sid, 2**62 - 1, n=1)
    assert len(rows) == 1
    assert rows[0]["available_time"] > rows[0]["value_time"]


def test_fred_ingest_flags_unverified_in_catalog(tmp_path):
    carry_db = tmp_path / "carry.db"
    _seed_carry_db(carry_db)
    store = PTISStore(tmp_path / "ptis")
    fred.ingest_series(store, "FEDFUNDS", db_path=str(carry_db))
    audit = {r["series_id"]: r for r in store.audit_rows()}
    assert audit["FRED:FEDFUNDS:rate"]["flag"] == "UNVERIFIED_LAG"


def test_list_cached_pair_providers(tmp_path):
    bt_db = tmp_path / f"{uuid4().hex}.db"
    _seed_backtest_db(bt_db)
    pairs = eodhd.list_cached_pair_providers(db_path=str(bt_db))
    assert len(pairs) == 1
    assert pairs[0]["symbol"] == "EURUSD"


def test_append_unregistered_with_source_auto_registers(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    series_id = "EODHD:NEW:H1:close"
    t0 = _ms(datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc))
    avail = t0 + 90_000
    n = store.append_rows(
        series_id,
        [build_row(series_id, t0, avail, 1.23)],
        source="EODHD",
        auto_register=True,
    )
    assert n == 1
    assert store.series_exists(series_id)
    row = store.asof(series_id, avail, n=1)[0]
    assert row["value"] == pytest.approx(1.23)


def test_append_unregistered_without_auto_register_raises(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    series_id = "EODHD:NEW:H1:close"
    t0 = _ms(datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc))
    with pytest.raises(KeyError, match="not registered"):
        store.append_rows(
            series_id,
            [build_row(series_id, t0, t0 + 90_000, 1.0)],
            source="EODHD",
            auto_register=False,
        )


def test_asof_reuses_cached_series_frame(tmp_path, monkeypatch):
    store = PTISStore(tmp_path / "ptis")
    series_id = "EODHD:CACHE:H1:close"
    t0 = _ms(datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc))
    store.append_rows(
        series_id,
        [build_row(series_id, t0, t0 + 90_000, 1.23)],
        source="EODHD",
        auto_register=True,
    )

    from athena_ase.data import ptis as ptis_mod

    original = ptis_mod.pd.read_parquet
    calls = 0

    def counted_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ptis_mod.pd, "read_parquet", counted_read)
    assert len(store.asof(series_id, t0 + 90_000)) == 1
    assert len(store.asof(series_id, t0 + 90_000)) == 1
    assert calls == 1
