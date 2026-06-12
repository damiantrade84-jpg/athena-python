"""Legacy Engine A bypass tests — ASE operational from day one."""

from __future__ import annotations

import pytest

from athena_ase.exceptions import LegacyEngineBypassed
from athena_ase.instruments import DEFAULT_INSTRUMENTS
from athena_ase.runtime.scan import run_ase_dual_horizon_scan
from engine_a_legacy_guard import (
    ASE_OPERATIONAL_FAMILIES,
    LEGACY_ENGINE_A_ENTRY_POINTS,
    assert_legacy_engine_allowed,
)
from scoring import calc_confluence


def test_entry_points_declared():
    assert "calc_confluence" in LEGACY_ENGINE_A_ENTRY_POINTS
    assert "analyze_pair" in LEGACY_ENGINE_A_ENTRY_POINTS


def test_all_model_families_raise_without_promotion_state():
    for family in ASE_OPERATIONAL_FAMILIES:
        pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex"}
        if family == "crypto":
            pair = {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto"}
        with pytest.raises(LegacyEngineBypassed):
            assert_legacy_engine_allowed(pair, entry_point="calc_confluence")


def test_calc_confluence_raises_for_operational_forex():
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex"}
    with pytest.raises(LegacyEngineBypassed):
        calc_confluence({}, {}, {}, 0.0, {}, pair, "neutral")


def test_representative_scan_returns_rows_for_all_instruments(monkeypatch):
    monkeypatch.setattr(
        "athena_ase.runtime.scan.generate_live_candidates",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "athena_ase.runtime.scan.predict_no_candidate",
        lambda inst, horizon, **_k: __import__(
            "athena_ase.contracts", fromlist=["flat_signal"]
        ).flat_signal(
            instrument=inst.symbol,
            family=inst.family,
            horizon=horizon,
            model_version="fixture",
            gate_result={"ok": True, "reason": "ok"},
        ),
    )

    payload = run_ase_dual_horizon_scan(write_journal=False, execute_trades=False)
    assert payload["signalCount"] == len(DEFAULT_INSTRUMENTS) * 2
