"""Legacy Engine A bypass tests."""

from __future__ import annotations

import pytest

from athena_ase.exceptions import LegacyEngineBypassed
from engine_a_legacy_guard import LEGACY_ENGINE_A_ENTRY_POINTS, assert_legacy_engine_allowed
from scoring import calc_confluence


def test_entry_points_declared():
    assert "calc_confluence" in LEGACY_ENGINE_A_ENTRY_POINTS
    assert "analyze_pair" in LEGACY_ENGINE_A_ENTRY_POINTS


def test_promoted_forex_raises(monkeypatch):
    monkeypatch.setattr(
        "engine_a_legacy_guard.get_family_state",
        lambda family: "DEMO" if family == "forex" else "SHADOW",
    )
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex"}
    with pytest.raises(LegacyEngineBypassed):
        assert_legacy_engine_allowed(pair, entry_point="calc_confluence")


def test_calc_confluence_raises_for_promoted_family(monkeypatch):
    monkeypatch.setattr(
        "engine_a_legacy_guard.get_family_state",
        lambda family: "DEMO" if family == "forex" else "SHADOW",
    )
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex"}
    with pytest.raises(LegacyEngineBypassed):
        calc_confluence({}, {}, {}, 0.0, {}, pair, "neutral")
