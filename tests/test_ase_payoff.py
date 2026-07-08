"""ASE payoff model tests (F3)."""

from __future__ import annotations

import pytest

from athena_ase.inference import predict as predict_mod


def test_payoff_q25_lower_than_q50(monkeypatch):
    monkeypatch.setitem(
        __import__("config").CONFIG,
        "ASE_PAYOFF",
        {"WIN_QUANTILE": "q25", "REALIZED_CAP_ENABLED": False},
    )
    mfe = {"q25": 0.4, "q50": 0.8}
    mae = {"q50": 0.3}
    q25_r = predict_mod._expected_net_r(0.6, mfe, mae)
    monkeypatch.setitem(
        __import__("config").CONFIG,
        "ASE_PAYOFF",
        {"WIN_QUANTILE": "q50", "REALIZED_CAP_ENABLED": False},
    )
    q50_r = predict_mod._expected_net_r(0.6, mfe, mae)
    assert q25_r < q50_r


def test_payoff_realized_cap_limits_expected_r(monkeypatch):
    monkeypatch.setitem(
        __import__("config").CONFIG,
        "ASE_PAYOFF",
        {"WIN_QUANTILE": "q50", "REALIZED_CAP_ENABLED": True, "REALIZED_CAP": 1.0},
    )
    capped = predict_mod._expected_net_r(
        0.7,
        {"q50": 1.0},
        {"q50": 0.2},
        realized_expectancy=0.05,
    )
    assert capped == pytest.approx(0.05)
