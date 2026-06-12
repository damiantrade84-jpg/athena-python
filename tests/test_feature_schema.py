"""Feature schema tests."""

from __future__ import annotations

import pandas as pd

from athena_ase.features.schema import CATEGORICAL_FEATURES, CORE_FEATURES, schema_hash
from athena_research.ase.train import (
    chronological_train_eval_split,
    enriched_training_mask,
)


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


def test_chronological_train_eval_split_is_80_20_after_sorting():
    frame = pd.DataFrame(
        {
            "decision_time_ms": [50, 10, 40, 20, 30],
            "value": ["e", "a", "d", "b", "c"],
        }
    )

    train, eval_frame = chronological_train_eval_split(frame)

    assert train["value"].tolist() == ["a", "b", "c", "d"]
    assert eval_frame["value"].tolist() == ["e"]


def test_enriched_training_mask_requires_family_specific_verified_features():
    frame = pd.DataFrame(
        {
            "cot_pct": [0.2, float("nan")],
            "cot_delta_4w": [0.1, 0.2],
            "funding_z": [float("nan"), float("nan")],
            "oi_delta_z": [float("nan"), float("nan")],
        }
    )

    assert enriched_training_mask(frame, "forex").tolist() == [True, False]
    assert enriched_training_mask(frame, "equity").tolist() == [False, False]
