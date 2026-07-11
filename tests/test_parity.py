"""Research/runtime parity tests."""

from __future__ import annotations

from athena_ase.inference.parity import check_parity, parity_hash
from athena_research.ase import parity as research_parity


def test_parity_hash_equal_on_same_fixture():
    rows = [{"instrument": "EURUSD", "p_cal": 0.6, "expectedNetR": 0.12}]
    assert check_parity(rows, list(rows))
    assert parity_hash(rows) == parity_hash(list(rows))


def test_feature_matrix_hash_changes_when_consumed_feature_changes():
    first = [{"instrument": "EURUSD", "schema": ["ret_z_1"], "values": [0.1]}]
    second = [{"instrument": "EURUSD", "schema": ["ret_z_1"], "values": [0.2]}]

    assert research_parity.feature_matrix_hash(first) != research_parity.feature_matrix_hash(second)
