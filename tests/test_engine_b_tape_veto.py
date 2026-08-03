"""Focused tests for the Engine B adverse-tape veto.

Added 2026-08-03 after AUD/USD LONG auto-executed into a 4.9-ATR confirmed
M15 slide: structure (Friday BOS) + zone retest passed every gate while the
confirmed trigger-TF tape was collapse-grade bearish. The veto refuses entries
whose last N confirmed trigger-TF closes moved against the signal direction by
>= ENGINE_B_TAPE_VETO_MIN_ADVERSE_ATR x trigger ATR; ordinary zone-retest
pullbacks must stay eligible.
"""

import config
from market_structure import (
    ENGINE_B_REASON_ADVERSE_TAPE,
    NakedEngine,
    _adverse_tape_veto,
)

import pytest


def _base_res(direction: str) -> dict:
    res = {
        "atr": 1.0,
        "asset_type": "stock",
        "near_active_zone": True,
        "bos_confirmed": True,
        "trigger_ok": True,
    }
    if direction == "LONG":
        res.update(
            {
                "current_swing_sequence": "HH_HL",
                "macro_swing_sequence": "HH_HL",
                "recommended_stop_loss": 90.0,
                "recommended_take_profit": 120.0,
                "distance_to_res": 10.0,
            }
        )
    else:
        res.update(
            {
                "current_swing_sequence": "LH_LL",
                "macro_swing_sequence": "LH_LL",
                "recommended_stop_loss": 110.0,
                "recommended_take_profit": 80.0,
                "distance_to_sup": 10.0,
            }
        )
    return res


def _candles(closes) -> list:
    return [{"close": c} for c in closes]


# NOTE: calculate_confidence receives the forming-inclusive trigger series; the
# veto evaluates the confirmed-only view (newest bar dropped by the shared
# follow-through convention). All fixtures therefore carry one extra trailing
# bar that is discarded before measurement.
COLLAPSE_CLOSES = [104.0, 103.5, 103.0, 102.5, 102.0, 101.5, 101.0, 100.5, 100.2]
MILD_PULLBACK_CLOSES = [100.9, 101.0, 101.1, 101.2, 101.1, 101.0, 100.9, 100.8, 100.7, 100.65]
RALLY_CLOSES = [96.0, 96.5, 97.0, 97.5, 98.0, 98.5, 99.0, 99.5, 99.8]


@pytest.fixture
def veto_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TAPE_VETO_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TAPE_VETO_BARS", 8)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TAPE_VETO_MIN_ADVERSE_ATR", 3.0)


def test_veto_blocks_long_into_collapse(veto_enabled):
    engine = NakedEngine()
    conf = engine.calculate_confidence(
        _base_res("LONG"),
        100.0,
        "LONG",
        entry_candles=_candles(COLLAPSE_CLOSES),
        style_profile={"style": "intraday", "min_rr": 1.0, "fallback_rr": 2.0},
    )
    assert conf["tape_ok"] is False
    assert conf["passed"] is False
    assert ENGINE_B_REASON_ADVERSE_TAPE in conf["failed_gate_names"]
    assert conf["tape_veto"]["vetoed"] is True
    assert conf["tape_veto"]["adverse_atr"] == pytest.approx(3.5, abs=0.05)


def test_veto_allows_normal_retest_pullback(veto_enabled):
    engine = NakedEngine()
    conf = engine.calculate_confidence(
        _base_res("LONG"),
        100.0,
        "LONG",
        entry_candles=_candles(MILD_PULLBACK_CLOSES),
        style_profile={"style": "intraday", "min_rr": 1.0, "fallback_rr": 2.0},
    )
    assert conf["tape_ok"] is True
    assert conf["passed"] is True


def test_veto_blocks_short_into_rally(veto_enabled):
    engine = NakedEngine()
    conf = engine.calculate_confidence(
        _base_res("SHORT"),
        100.0,
        "SHORT",
        entry_candles=_candles(RALLY_CLOSES),
        style_profile={"style": "intraday", "min_rr": 1.0, "fallback_rr": 2.0},
    )
    assert conf["tape_ok"] is False
    assert conf["passed"] is False
    assert ENGINE_B_REASON_ADVERSE_TAPE in conf["failed_gate_names"]


def test_veto_disabled_never_blocks(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TAPE_VETO_ENABLED", False)
    engine = NakedEngine()
    conf = engine.calculate_confidence(
        _base_res("LONG"),
        100.0,
        "LONG",
        entry_candles=_candles(COLLAPSE_CLOSES),
        style_profile={"style": "intraday", "min_rr": 1.0, "fallback_rr": 2.0},
    )
    assert conf["tape_ok"] is True
    assert conf["passed"] is True
    assert conf["tape_veto"]["enabled"] is False


def test_veto_insufficient_data_never_blocks(veto_enabled):
    ok, diag = _adverse_tape_veto(None, "LONG", 1.0)
    assert ok is True
    assert diag["vetoed"] is False
    engine = NakedEngine()
    conf = engine.calculate_confidence(
        _base_res("LONG"),
        100.0,
        "LONG",
        entry_candles=None,
        style_profile={"style": "intraday", "min_rr": 1.0, "fallback_rr": 2.0},
    )
    assert conf["tape_ok"] is True
    assert conf["passed"] is True
