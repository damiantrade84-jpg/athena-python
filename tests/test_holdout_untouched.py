"""Holdout untouched until explicit eval."""

from __future__ import annotations

from athena_ase.registry.holdout import HoldoutRegistry


def test_holdout_empty_until_eval():
    reg = HoldoutRegistry()
    assert not reg.holdout_done("forex", "swing")
