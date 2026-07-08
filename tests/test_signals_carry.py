"""Carry signal tests."""

from __future__ import annotations

import math

import pytest

from athena_ase.data.ingest.common import fred_series_id
from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.instruments import Instrument
from athena_ase.signals.carry import FRED_CURRENCY_SERIES, compute_carry


def _fred_row(store, fred_id, obs_date, rate, decision_ms):
    sid = fred_series_id(fred_id)
    vt = int(__import__("datetime").datetime.strptime(obs_date, "%Y-%m-%d").replace(tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
    av = decision_ms - 1
    store.register_series(sid, "FRED")
    store.append_rows(
        sid,
        [build_row(sid, vt, av, rate)],
        source="FRED",
        auto_register=False,
    )


def test_cvr_gradient_not_saturated():
    from athena_ase.signals.carry import _raw_strength_from_cvr

    s_low = _raw_strength_from_cvr(0.8, "forex")
    s_high = _raw_strength_from_cvr(1.6, "forex")
    assert 0.0 < s_low < 1.0
    assert 0.0 < s_high < 1.0
    assert s_low != s_high


def test_eurusd_carry_mapping_and_cvr(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    decision = 1_700_000_000_000
    _fred_row(store, FRED_CURRENCY_SERIES["EUR"], "2024-01-01", 3.0, decision)
    _fred_row(store, FRED_CURRENCY_SERIES["USD"], "2024-01-01", 5.0, decision)
    inst = Instrument("EURUSD", "EUR/USD", "forex", "major", "EURUSD.FOREX", "USDX")
    sigma = 0.01
    res = compute_carry(store, inst, decision, sigma)
    carry = 3.0 - 5.0
    cvr = max(-2.0, min(2.0, carry / (sigma * math.sqrt(252))))
    assert res.carry_annual == pytest.approx(carry)
    assert res.cvr == pytest.approx(cvr)
    assert res.direction == -1
    scale = 10.0
    assert res.raw_strength == pytest.approx(min(abs(cvr) / scale, 1.0))


def test_forex_pairs_have_distinct_carry_strengths(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    decision = 1_700_000_000_000
    _fred_row(store, FRED_CURRENCY_SERIES["EUR"], "2024-01-01", 3.0, decision)
    _fred_row(store, FRED_CURRENCY_SERIES["USD"], "2024-01-01", 5.0, decision)
    _fred_row(store, FRED_CURRENCY_SERIES["JPY"], "2024-01-01", -0.5, decision)
    sigma = 0.01
    eurusd = compute_carry(
        store, Instrument("EURUSD", "EUR/USD", "forex", "major"), decision, sigma
    )
    usdjpy = compute_carry(
        store, Instrument("USDJPY", "USD/JPY", "forex", "major"), decision, sigma
    )
    assert eurusd.raw_strength > 0.0
    assert usdjpy.raw_strength > 0.0
    assert eurusd.raw_strength != usdjpy.raw_strength
    assert eurusd.raw_strength < 1.0
    assert usdjpy.raw_strength < 1.0


def test_missing_fred_series_returns_none(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    inst = Instrument("EURUSD", "EUR/USD", "forex", "major", "EURUSD.FOREX", "USDX")
    res = compute_carry(store, inst, 1_700_000_000_000, 0.01)
    assert res.direction == 0
