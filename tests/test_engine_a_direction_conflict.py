"""Unit tests for Engine A direction conflict helpers."""

from __future__ import annotations

import pytest

from athena_app.services.engine_a_direction import (
    apply_direction_conflict_to_result,
    engine_a_direction_conflict_flags,
)
from config import CONFIG


def test_engine_a_direction_conflict_flags_intermarket_contradictory():
    flags = engine_a_direction_conflict_flags(
        "LONG",
        intermarket_confirmation={"verdict": "contradictory", "score": -0.2},
        intermarket_guard_enabled=True,
        btc_guard_enabled=False,
    )
    assert flags["directionConflictedWithIntermarket"] is True
    assert flags["directionConflicted"] is True


def test_engine_a_direction_conflict_flags_btc_opposed_high_corr():
    flags = engine_a_direction_conflict_flags(
        "LONG",
        btc_bias="bearish",
        btc_correlation=0.85,
        btc_mult=0.90,
        intermarket_guard_enabled=False,
        btc_guard_enabled=True,
    )
    assert flags["directionConflictedWithBtc"] is True
    assert flags["directionConflicted"] is True


def test_apply_direction_conflict_to_result_sets_signal_executable_false(monkeypatch):
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_DIRECTION_INTERMARKET_CONFLICT_GUARD_ENABLED", True
    )
    result = apply_direction_conflict_to_result(
        {"direction": "SHORT", "signalExecutable": True},
        intermarket_confirmation={"verdict": "contradictory", "score": -0.3},
    )
    assert result["directionConflictedWithIntermarket"] is True
    assert result["signalExecutable"] is False
    assert result["direction"] == "SHORT"


def test_conflict_flags_disabled_by_default():
    flags = engine_a_direction_conflict_flags(
        "LONG",
        intermarket_confirmation={"verdict": "contradictory", "score": -0.3},
        btc_bias="bearish",
        btc_correlation=0.9,
        btc_mult=0.9,
        intermarket_guard_enabled=False,
        btc_guard_enabled=False,
    )
    assert flags["directionConflicted"] is False
