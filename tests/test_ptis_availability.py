"""PTIS availability rules and asof() causality guards (ASE v2.1 §1.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from athena_ase.data.availability import (
    AvailabilityRuleId,
    compute_available_time_ms,
    cot_available_time_ms,
    cot_publication_friday_ms,
    fred_default_available_time_ms,
    rule_for_source,
    series_metadata_for_audit,
)
from athena_ase.data.ptis import (
    PTISStore,
    build_row,
    dataset_hash_from_bytes,
    ingest_with_rule,
)

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture
def ptis(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    yield store


def test_asof_never_returns_future_available_time(ptis: PTISStore):
    series_id = "EODHD:EURUSD:H1:close"
    t0 = _ms(datetime(2025, 6, 2, 12, 0, tzinfo=_UTC))
    avail = compute_available_time_ms(
        AvailabilityRuleId.EODHD_BAR, value_time_ms=t0, bar_close_ms=t0
    )
    ptis.register_series(series_id, "EODHD")
    ptis.append_rows(
        series_id,
        [
            build_row(series_id, t0, avail, 1.0850),
            build_row(series_id, t0 + 3_600_000, avail + 3_600_000, 1.0852),
        ],
        source="EODHD",
    )

    decision = avail + 1_000
    rows = ptis.asof(series_id, decision, n=10)
    assert len(rows) == 1
    assert all(r["available_time"] <= decision for r in rows)

    decision2 = avail + 3_600_000 + 1_000
    rows2 = ptis.asof(series_id, decision2, n=10)
    assert len(rows2) == 2
    assert all(r["available_time"] <= decision2 for r in rows2)


def test_asof_latest_revision_wins(ptis: PTISStore):
    series_id = "EODHD:TEST:H1:close"
    t0 = _ms(datetime(2025, 1, 1, 0, 0, tzinfo=_UTC))
    avail = t0 + 90_000
    ptis.register_series(series_id, "EODHD")
    ptis.append_rows(
        series_id,
        [
            build_row(series_id, t0, avail, 1.0, revision=0),
            build_row(series_id, t0, avail, 1.1, revision=1),
        ],
        source="EODHD",
    )
    row = ptis.asof(series_id, avail + 1, n=1)[0]
    assert row["value"] == pytest.approx(1.1)
    assert row["revision"] == 1


def test_cot_friday_publication_monday_availability():
    # COT week ending Tuesday 2025-06-03 → publish Fri 2025-06-06 15:30 ET
    tuesday = datetime(2025, 6, 3, 0, 0, tzinfo=_UTC)
    tuesday_ms = _ms(tuesday)
    pub_ms = cot_publication_friday_ms(tuesday_ms)
    pub_dt = datetime.fromtimestamp(pub_ms / 1000, tz=_UTC)
    assert pub_dt.astimezone(_ET).weekday() == 4
    assert pub_dt.astimezone(_ET).hour == 15
    assert pub_dt.astimezone(_ET).minute == 30

    avail_ms = cot_available_time_ms(tuesday_ms)
    avail_dt = datetime.fromtimestamp(avail_ms / 1000, tz=_UTC)
    assert avail_dt.weekday() == 0  # Monday
    assert avail_dt.hour == 0
    assert avail_dt > pub_dt


def test_cot_asof_respects_monday_lag(tmp_path):
    series_id = "CFTC:COT:6E:noncomm_net"
    tuesday_ms = _ms(datetime(2025, 6, 3, 0, 0, tzinfo=_UTC))
    avail_ms = cot_available_time_ms(tuesday_ms)
    store = PTISStore(tmp_path / "ptis")
    ingest_with_rule(
        store,
        series_id,
        "CFTC",
        AvailabilityRuleId.CFTC_COT,
        [(tuesday_ms, 125000.0)],
    )
    sunday_after_pub = avail_ms - 86_400_000
    assert len(store.asof(series_id, sunday_after_pub, n=1)) == 0
    assert len(store.asof(series_id, avail_ms, n=1)) == 1


def test_fred_default_one_business_day_noon_et():
    # Monday obs → Tuesday 12:00 ET
    monday = datetime(2025, 6, 2, 0, 0, tzinfo=_UTC)
    avail_ms = fred_default_available_time_ms(_ms(monday))
    avail = datetime.fromtimestamp(avail_ms / 1000, tz=_UTC).astimezone(_ET)
    assert avail.weekday() == 1
    assert avail.hour == 12


def test_fred_vintage_overrides_default():
    obs_ms = _ms(datetime(2025, 6, 2, 0, 0, tzinfo=_UTC))
    vintage_ms = _ms(datetime(2025, 6, 10, 16, 0, tzinfo=_UTC))
    avail = compute_available_time_ms(
        AvailabilityRuleId.FRED_DAILY,
        value_time_ms=obs_ms,
        realtime_start_ms=vintage_ms,
    )
    assert avail == vintage_ms


def test_eodhd_bar_close_plus_90_seconds():
    close_ms = _ms(datetime(2025, 6, 1, 13, 0, tzinfo=_UTC))
    avail = compute_available_time_ms(
        AvailabilityRuleId.EODHD_BAR,
        value_time_ms=close_ms,
        bar_close_ms=close_ms,
    )
    assert avail == close_ms + 90_000


def test_unverified_fred_flagged_in_audit():
    meta = series_metadata_for_audit("FRED:DFF", "FRED")
    assert meta["flag"] == "UNVERIFIED_LAG"
    assert meta["verified"] is False
    meta_policy = series_metadata_for_audit("FRED:FEDFUNDS", "FRED_POLICY")
    assert meta_policy["flag"] == "UNVERIFIED_LAG"


def test_rule_for_source_mapping():
    assert rule_for_source("EODHD") == AvailabilityRuleId.EODHD_BAR
    assert rule_for_source("CFTC") == AvailabilityRuleId.CFTC_COT
    assert rule_for_source("FRED") == AvailabilityRuleId.FRED_DAILY


def test_dataset_hash_deterministic():
    payload = b"same bytes produce same hash"
    assert dataset_hash_from_bytes(payload) == dataset_hash_from_bytes(payload)
    assert len(dataset_hash_from_bytes(payload)) == 64


def test_module_asof_matches_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_PTIS_ROOT", str(tmp_path / "ptis"))
    from athena_ase.data import ptis as ptis_mod

    ptis_mod._default_store = None
    store = PTISStore(tmp_path / "ptis")
    series_id = "BYBIT:BTCUSDT:funding"
    t0 = _ms(datetime(2025, 6, 1, 0, 0, tzinfo=_UTC))
    avail = compute_available_time_ms(AvailabilityRuleId.BYBIT_EVENT, value_time_ms=t0)
    store.register_series(series_id, "BYBIT")
    store.append_rows(series_id, [build_row(series_id, t0, avail, -0.0001)], source="BYBIT")

    from athena_ase.data.ptis import asof

    arr = asof(series_id, avail, n=1)
    assert len(arr) == 1
    assert arr[0]["value"] == pytest.approx(-0.0001)
