"""Feature schema tests (live schema in features/build.py)."""

from __future__ import annotations

import pandas as pd

from athena_ase.features.build import (
    CATEGORICAL_FEATURES,
    FEATURE_SCHEMA_CORE,
    FEATURE_SCHEMA_ENRICHED,
    schema_hash,
)
from athena_research.ase.train import (
    chronological_train_eval_split,
    enriched_training_mask,
)


def test_schema_hash_stable():
    h1 = schema_hash(FEATURE_SCHEMA_CORE)
    h2 = schema_hash(FEATURE_SCHEMA_CORE)
    assert h1 == h2
    assert len(h1) == 64


def test_enriched_schema_extends_core():
    assert FEATURE_SCHEMA_ENRICHED[: len(FEATURE_SCHEMA_CORE)] == FEATURE_SCHEMA_CORE
    assert set(FEATURE_SCHEMA_ENRICHED) - set(FEATURE_SCHEMA_CORE) == {
        "cot_pct",
        "cot_delta_4w",
        "funding_z",
        "oi_delta_z",
        "usd_index_ret_21",
        "us_real_yield_level",
        "us_real_yield_delta_21",
        "us_nominal_yield_level",
        "us_nominal_yield_delta_21",
        "quote_rate_level",
    }


def test_core_schema_contains_signal_block():
    for name in (
        "sig_tsmom",
        "sig_carry",
        "sig_xsec",
        "sig_mr",
        "sig_aggregate",
        "conflict_score",
        "layer1_confluence",
        "candidate_dir",
    ):
        assert name in FEATURE_SCHEMA_CORE


def test_categorical_features_subset_of_core():
    assert CATEGORICAL_FEATURES <= set(FEATURE_SCHEMA_CORE)
    assert "instrument_id" in CATEGORICAL_FEATURES


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
