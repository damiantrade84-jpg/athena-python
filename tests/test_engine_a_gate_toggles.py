"""Unit tests for Engine A optional timing/session master toggles."""

from __future__ import annotations

from engine_a_v3.gate_toggles import (
    entry_timing_gates_enabled,
    session_gates_enabled,
)


def test_entry_timing_gates_default_off():
    assert entry_timing_gates_enabled({}) is False
    assert entry_timing_gates_enabled({"ENGINE_A_ENTRY_TIMING_GATES_ENABLED": False}) is False
    assert entry_timing_gates_enabled({"ENGINE_A_ENTRY_TIMING_GATES_ENABLED": True}) is True


def test_session_gates_default_off():
    assert session_gates_enabled({}) is False
    assert session_gates_enabled({"ENGINE_A_SESSION_GATES_ENABLED": False}) is False
    assert session_gates_enabled({"ENGINE_A_SESSION_GATES_ENABLED": True}) is True
