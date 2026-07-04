"""Tests for athena_fx.value — REER value family."""
from __future__ import annotations

from datetime import date

import pytest

from athena_fx.models import DIAG_MISSING_REER, FAMILY_VALUE
from athena_fx.value import ingest_reer_rows, reer_zscore, value_family_score


def _monthly_reer_rows(
    currency: str,
    n_months: int,
    base_value: float,
    drift: float,
    start_year: int = 2019,
    start_month: int = 1,
    *,
    publication_lag_days: int | None = None,
) -> list[dict]:
    rows = []
    y, m = start_year, start_month
    val = base_value
    for _ in range(n_months):
        obs = f"{y:04d}-{m:02d}"
        row: dict = {
            "currency": currency,
            "observation_month": obs,
            "reer_value": val,
            "source": "test",
        }
        if publication_lag_days is not None:
            pub = date(y, m, 28) + __import__("datetime").timedelta(days=publication_lag_days)
            row["publication_date"] = pub.isoformat()
        rows.append(row)
        val += drift
        m += 1
        if m > 12:
            m = 1
            y += 1
    return rows


@pytest.fixture
def fx_db(tmp_path, monkeypatch):
    db = str(tmp_path / "fx_value.db")
    monkeypatch.setenv("ATHENA_FX_DB_PATH", db)
    return db


def test_conservative_lag_when_publication_date_missing(fx_db):
    rows = [{
        "currency": "EUR",
        "observation_month": "2024-01",
        "reer_value": 100.0,
        "source": "test",
    }]
    ingest_reer_rows(rows, db_path=fx_db)
    loaded = __import__("athena_fx.store", fromlist=["store"]).load_reer_observations(
        currency="EUR", db_path=fx_db,
    )
    assert len(loaded) == 1
    assert loaded[0]["available_date"] == "2024-03-01"
    assert loaded[0]["lag_method"] == "conservative"


def test_no_lookahead_publication_date(fx_db):
    eur_rows = _monthly_reer_rows("EUR", 65, 100.0, 0.05, publication_lag_days=15)
    usd_rows = _monthly_reer_rows("USD", 65, 100.0, 0.05, publication_lag_days=15)
    ingest_reer_rows(eur_rows + usd_rows, db_path=fx_db)

    # Before any publication is available
    early = reer_zscore("EUR", "2019-01-01", db_path=fx_db)
    assert early is None

    # After publications available through mid-2024 (non-flat series for nonzero std)
    late = reer_zscore("EUR", "2024-12-01", db_path=fx_db)
    assert late is not None


def test_pair_score_bullish_when_base_undervalued_quote_overvalued(fx_db):
    # Stable history then shock: EUR deeply undervalued, USD overvalued
    eur = _monthly_reer_rows("EUR", 62, 100.0, 0.0)
    eur[-1]["reer_value"] = 80.0  # sharp drop → negative z
    eur[-2]["reer_value"] = 99.0
    usd = _monthly_reer_rows("USD", 62, 100.0, 0.0)
    usd[-1]["reer_value"] = 120.0  # sharp rise → positive z
    usd[-2]["reer_value"] = 101.0
    for row in eur + usd:
        row["publication_date"] = f"{row['observation_month']}-28"
    ingest_reer_rows(eur + usd, db_path=fx_db)

    result = value_family_score("EURUSD", "2024-06-01", db_path=fx_db)
    assert result.status == "ok"
    assert result.family == FAMILY_VALUE
    assert result.subcomponents["base_z"] < -1.0
    assert result.subcomponents["quote_z"] > 1.0
    assert result.score > 0
    assert result.direction == "bullish"
    assert result.subcomponents["rebalance_note"] == "monthly"


def test_missing_reer_invalid(fx_db):
    result = value_family_score("EURUSD", "2024-06-01", db_path=fx_db)
    assert result.status == "invalid"
    assert result.score is None
    assert any(d.code == DIAG_MISSING_REER for d in result.diagnostics)
