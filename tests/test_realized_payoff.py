"""Realized journal payoff tests (signal-quality suggestion #2)."""

from __future__ import annotations

import pandas as pd
import pytest

from athena_ase.inference import predict as predict_mod
from athena_ase.inference.realized_payoff import RealizedPayoff


def test_realized_exit_payoff_from_journal(monkeypatch):
    journal = pd.DataFrame(
        [
            {
                "modelFamily": "forex",
                "horizon": "intraday",
                "decisionStatus": "TRADE",
                "probabilityPositive": 0.62,
                "realizedNetR": 1.0,
                "reconciledAt": "2026-01-01",
            },
            {
                "modelFamily": "forex",
                "horizon": "intraday",
                "decisionStatus": "TRADE",
                "probabilityPositive": 0.58,
                "realizedNetR": 0.5,
                "reconciledAt": "2026-01-02",
            },
            {
                "modelFamily": "forex",
                "horizon": "intraday",
                "decisionStatus": "TRADE",
                "probabilityPositive": 0.55,
                "realizedNetR": -1.0,
                "reconciledAt": "2026-01-03",
            },
            {
                "modelFamily": "forex",
                "horizon": "intraday",
                "decisionStatus": "TRADE",
                "probabilityPositive": 0.57,
                "realizedNetR": -0.5,
                "reconciledAt": "2026-01-04",
            },
            {
                "modelFamily": "forex",
                "horizon": "intraday",
                "decisionStatus": "TRADE",
                "probabilityPositive": 0.60,
                "realizedNetR": -0.25,
                "reconciledAt": "2026-01-05",
            },
        ]
    )
    import athena_ase.inference.realized_payoff as rp_mod

    monkeypatch.setattr(rp_mod, "_journal_frame", lambda: journal)
    rp = rp_mod.realized_exit_payoff("forex", "intraday")
    assert rp is not None
    assert rp.e_win == pytest.approx(0.75)
    assert rp.e_loss == pytest.approx(-0.5833333333, rel=1e-4)
    assert rp.win_count == 2
    assert rp.loss_count == 3


def test_expected_net_r_uses_realized_exit_when_enabled(monkeypatch):
    monkeypatch.setitem(
        __import__("config").CONFIG,
        "ASE_PAYOFF",
        {
            "USE_REALIZED_EXIT_R": True,
            "REALIZED_CAP_ENABLED": False,
            "REALIZED_MIN_TRADES": 3,
        },
    )
    monkeypatch.setattr(
        predict_mod,
        "realized_exit_payoff",
        lambda family, horizon: RealizedPayoff(
            e_win=0.8,
            e_loss=-0.4,
            win_count=10,
            loss_count=8,
            expectancy=0.1,
        ),
    )
    modeled = predict_mod._expected_net_r(
        0.6,
        {"q50": 1.0},
        {"q50": 0.5},
        family="forex",
        horizon="intraday",
    )
    assert modeled == pytest.approx(0.6 * 0.8 + 0.4 * (-0.4))
