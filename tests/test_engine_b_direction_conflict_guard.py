"""Unit tests for Engine B independent-direction conflict guard."""

from __future__ import annotations

from athena_app.services.engine_b_direction import independent_conflict_blocks_emit


def test_conflict_guard_blocks_opposed_high_confidence():
    structure = {
        "engine_b_independent_direction": {
            "direction": "SHORT",
            "confidence": "HIGH",
            "reason": "Structural evidence bearish",
        }
    }
    assert independent_conflict_blocks_emit("LONG", structure, enabled=True) is True


def test_conflict_guard_ignores_medium_confidence():
    structure = {
        "engine_b_independent_direction": {
            "direction": "SHORT",
            "confidence": "MEDIUM",
        }
    }
    assert (
        independent_conflict_blocks_emit(
            "LONG",
            structure,
            enabled=True,
            min_confidence="HIGH",
        )
        is False
    )


def test_conflict_guard_disabled_by_default_path():
    structure = {
        "engine_b_independent_direction": {
            "direction": "SHORT",
            "confidence": "HIGH",
        }
    }
    assert independent_conflict_blocks_emit("LONG", structure, enabled=False) is False
