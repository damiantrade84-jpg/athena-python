"""Scan-time ASE ingest is a freshness top-up, not a historical rewrite."""

from __future__ import annotations

from datetime import datetime, timezone

from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
from athena_ase.data.ingest import bybit, fred
from athena_ase.data.ingest.common import bybit_series_id, price_series_id
from athena_ase.data.ptis import PTISStore, build_row
from tests.test_ptis_ingest import _seed_carry_db


def test_bybit_skips_kline_fetch_when_last_confirmed_present(tmp_path, monkeypatch):
    store = PTISStore(tmp_path / "ptis")
    now_ms = int(datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc).timestamp() * 1000)
    last_confirmed = (now_ms // 3_600_000) * 3_600_000
    sid = price_series_id("BYBIT", "BTCUSDT", "H1", "close")
    avail = compute_available_time_ms(
        AvailabilityRuleId.MT5_BAR,
        value_time_ms=last_confirmed,
        bar_close_ms=last_confirmed,
    )
    store.append_rows(sid, [build_row(sid, last_confirmed, avail, 68000.0)], source="BYBIT")

    calls: list[tuple] = []
    monkeypatch.setattr(
        bybit,
        "_fetch_klines",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [],
    )

    counts = bybit.ingest_symbol_klines(
        store, "BTCUSDT", timeframes=("H1",), now_ms=now_ms
    )

    assert calls == []
    assert counts == {}


def test_bybit_fetches_klines_when_series_missing(tmp_path, monkeypatch):
    store = PTISStore(tmp_path / "ptis")
    calls: list[tuple] = []
    monkeypatch.setattr(
        bybit,
        "_fetch_klines",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [],
    )

    bybit.ingest_symbol_klines(store, "BTCUSDT", timeframes=("H1",), now_ms=1_700_000_000_000)

    assert len(calls) == 1


def test_bybit_skips_derivatives_when_recent(tmp_path, monkeypatch):
    store = PTISStore(tmp_path / "ptis")
    now_ms = int(datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc).timestamp() * 1000)
    for field in ("funding", "oi"):
        sid = bybit_series_id("BTCUSDT", field)
        vt = now_ms - 30 * 60_000
        avail = compute_available_time_ms(
            AvailabilityRuleId.BYBIT_EVENT, value_time_ms=vt
        )
        store.append_rows(sid, [build_row(sid, vt, avail, 0.01)], source="BYBIT")

    calls: list[tuple] = []
    monkeypatch.setattr(
        bybit,
        "_fetch_derivatives",
        lambda *args, **kwargs: calls.append((args, kwargs)) or ([], []),
    )

    counts = bybit.ingest_symbol(store, "BTCUSDT", now_ms=now_ms)

    assert calls == []
    assert counts == {}


def test_fred_ingest_skips_rewrite_when_latest_present(tmp_path):
    carry_db = tmp_path / "carry.db"
    _seed_carry_db(carry_db)
    store = PTISStore(tmp_path / "ptis")
    first = fred.ingest_series(store, "FEDFUNDS", db_path=str(carry_db))
    second = fred.ingest_series(store, "FEDFUNDS", db_path=str(carry_db))
    assert first == 1
    assert second == 0
