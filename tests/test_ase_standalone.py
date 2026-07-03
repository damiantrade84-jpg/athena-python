"""ASE standalone wiring — Engine A must not be blocked; ASE scan stays isolated."""

from __future__ import annotations

import importlib

import pytest


def test_scoring_does_not_reference_legacy_guard():
    import scoring

    source = open(scoring.__file__, encoding="utf-8").read()
    assert "engine_a_legacy_guard" not in source


def test_analyze_pair_does_not_reference_legacy_guard():
    import athena

    source = open(athena.__file__, encoding="utf-8").read()
    assert "engine_a_legacy_guard" not in source


def test_legacy_guard_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("engine_a_legacy_guard")


def test_engine_c_does_not_import_ase_normalizer():
    import engine_c

    source = open(engine_c.__file__, encoding="utf-8").read()
    assert "normalise_ase_for_engine_c" not in source
    assert "is_ase_engine_a_signal" not in source


def test_coffee_excluded_from_ase_universe():
    from athena_ase.universe import UNIVERSE, instrument_by_symbol

    symbols = {inst.symbol for inst in UNIVERSE}
    assert "COFFEE" not in symbols
    assert instrument_by_symbol("COFFEE") is None
    assert instrument_by_symbol("Coffee") is None
