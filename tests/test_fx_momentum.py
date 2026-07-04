"""Tests for athena_fx.momentum — TSMOM, XS momentum, family score."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from athena_fx.models import DIAG_MISSING_OHLC, FAMILY_MOMENTUM
from athena_fx.momentum import (
    export_family_distribution,
    momentum_family_score,
    tsmom_score,
    xs_momentum_scores,
)


def _make_total_return_rows(n: int, daily_ret: float, start: str = "2024-01-01") -> list[dict]:
    rows = []
    d = date.fromisoformat(start)
    for i in range(n):
        # Tiny alternating noise so realized vol > 0 (constant returns → vol guard).
        noise = 0.00001 * (1 if i % 2 == 0 else -1)
        r = daily_ret + noise
        rows.append({
            "date": (d + timedelta(days=i)).isoformat(),
            "long_total_return": r,
            "short_total_return": -r,
        })
    return rows


def _make_currency_indexes(
    currencies: dict[str, float],
    n: int = 260,
    start: str = "2024-01-01",
) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    d0 = date.fromisoformat(start)
    for ccy, daily_growth in currencies.items():
        idx = 100.0
        series = []
        for i in range(n):
            idx *= 1.0 + daily_growth
            series.append({
                "date": (d0 + timedelta(days=i)).isoformat(),
                "index": idx,
            })
        out[ccy] = series
    return out


def test_tsmom_continuous_uptrend_positive_not_discrete():
    rows = _make_total_return_rows(300, 0.001)
    as_of = rows[251]["date"]
    result = tsmom_score(rows, as_of, side="long")
    assert result is not None
    assert result["tsmom_score"] > 0
    discrete = {1.0, -1.0, 0.4, -0.4, 0.2, -0.2}
    assert result["tsmom_score"] not in discrete
    assert result["z_3m"] > 0
    assert result["z_6m"] > 0
    assert result["z_12m"] > 0


def test_tsmom_different_trends_different_scores():
    up = _make_total_return_rows(300, 0.002)
    down = _make_total_return_rows(300, -0.002)
    as_of = up[251]["date"]
    up_score = tsmom_score(up, as_of)["tsmom_score"]
    down_score = tsmom_score(down, as_of)["tsmom_score"]
    assert up_score > down_score
    assert up_score > 0
    assert down_score < 0


def test_tsmom_no_lookahead():
    rows = _make_total_return_rows(300, 0.001)
    as_of = rows[251]["date"]
    before = tsmom_score(rows[:252], as_of)
    future = rows + _make_total_return_rows(50, -0.05, start="2025-01-01")
    after = tsmom_score(future, as_of)
    assert before["tsmom_score"] == pytest.approx(after["tsmom_score"])


def test_tsmom_insufficient_history_returns_none():
    rows = _make_total_return_rows(200, 0.001)
    assert tsmom_score(rows, rows[-1]["date"]) is None


def test_xs_momentum_g8_only():
    indexes = _make_currency_indexes({
        "EUR": 0.001, "GBP": 0.0005, "JPY": -0.0005, "USD": 0.0,
        "CHF": 0.0002, "CAD": 0.0003, "AUD": 0.0015, "NZD": 0.0012,
        "SEK": 0.01,
    })
    as_of = indexes["EUR"][-1]["date"]
    scores = xs_momentum_scores(indexes, as_of)
    assert "SEK" not in scores
    assert set(scores.keys()).issubset({
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
    })
    assert len(scores) == 8
    vals = list(scores.values())
    assert min(vals) >= -1.0
    assert max(vals) <= 1.0


def test_momentum_family_score_one_family_with_both_subcomponents():
    tr_rows = _make_total_return_rows(300, 0.001)
    indexes = _make_currency_indexes({
        "EUR": 0.001, "GBP": 0.0005, "JPY": -0.0005, "USD": 0.0,
        "CHF": 0.0002, "CAD": 0.0003, "AUD": 0.0015, "NZD": 0.0012,
    })
    as_of = tr_rows[251]["date"]
    result = momentum_family_score("EURUSD", tr_rows, indexes, as_of)
    assert result.family == FAMILY_MOMENTUM
    assert result.status == "ok"
    assert result.score is not None
    sub = result.subcomponents
    assert "tsmom_score" in sub
    assert "z_3m" in sub
    assert "z_6m" in sub
    assert "z_12m" in sub
    assert "xs_momentum_score" in sub
    assert sub["xs_momentum_score"] is not None


def test_momentum_family_score_invalid_on_insufficient_history():
    rows = _make_total_return_rows(100, 0.001)
    result = momentum_family_score("EURUSD", rows, {}, rows[-1]["date"])
    assert result.status == "invalid"
    assert result.score is None
    assert any(d.code == DIAG_MISSING_OHLC for d in result.diagnostics)


def test_export_family_distribution_percentiles():
    scores = [
        {"family": "momentum", "symbol": "EURUSD", "as_of_date": "2024-06-01", "score": 0.5},
        {"family": "momentum", "symbol": "EURUSD", "as_of_date": "2024-07-01", "score": 0.7},
        {"family": "momentum", "symbol": "GBPUSD", "as_of_date": "2024-06-01", "score": -0.3},
        {"family": "momentum", "symbol": "GBPUSD", "as_of_date": "2024-07-01", "score": -0.1},
        {"family": "carry", "symbol": "AUDJPY", "as_of_date": "2024-06-01", "score": 0.02},
    ]
    dist = export_family_distribution(scores)
    assert dist["count"] == 5
    assert dist["date_range"] == ["2024-06-01", "2024-07-01"]
    mom_long = dist["by_family"]["momentum"]["long"]
    assert mom_long[50] == pytest.approx(0.6, rel=1e-3)
    assert "GBPUSD" in dist["by_symbol"]
    assert dist["by_symbol"]["GBPUSD"][50] == pytest.approx(-0.2, rel=1e-3)
