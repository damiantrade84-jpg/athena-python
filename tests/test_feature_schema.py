"""Feature schema tests."""

from __future__ import annotations

from athena_ase.features.schema import CATEGORICAL_FEATURES, CORE_FEATURES, schema_hash


def test_schema_hash_stable():
    h1 = schema_hash(horizon="intraday", enriched=False)
    h2 = schema_hash(horizon="intraday", enriched=False)
    assert h1 == h2
    assert len(h1) == 64


def test_swing_schema_differs_from_intraday():
    assert schema_hash(horizon="swing") != schema_hash(horizon="intraday")


def test_categorical_features_subset_of_core_or_enriched():
    assert "instrument_id" in CATEGORICAL_FEATURES
    assert "instrument_id" in CORE_FEATURES
