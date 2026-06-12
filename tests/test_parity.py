"""Research/runtime parity tests."""

from __future__ import annotations

from athena_ase.inference.parity import check_parity, parity_hash


def test_parity_hash_equal_on_same_fixture():
    rows = [{"instrument": "EURUSD", "p_cal": 0.6, "expectedNetR": 0.12}]
    assert check_parity(rows, list(rows))
    assert parity_hash(rows) == parity_hash(list(rows))
