"""T0.3 — no random early-stopping validation split in the meta classifier."""

from __future__ import annotations

import numpy as np

from athena_ase.features.build import FEATURE_SCHEMA_CORE
from athena_ase.models.meta import BASE_HGB_PARAMS, build_classifier
from athena_ase.models.quantile_heads import QUANTILE_HGB_PARAMS


def test_no_early_stopping_or_validation_fraction():
    clf = build_classifier()
    assert clf.early_stopping is False
    assert "validation_fraction" not in BASE_HGB_PARAMS
    assert BASE_HGB_PARAMS["early_stopping"] is False
    assert QUANTILE_HGB_PARAMS["early_stopping"] is False


def test_two_fits_are_deterministic():
    from athena_ase.features.build import CATEGORICAL_FEATURES

    rng = np.random.default_rng(7)
    n = 300
    X = rng.normal(size=(n, len(FEATURE_SCHEMA_CORE)))
    for i, name in enumerate(FEATURE_SCHEMA_CORE):
        if name in CATEGORICAL_FEATURES:
            X[:, i] = rng.integers(0, 5, size=n).astype(float)
    y = (X[:, 0] + rng.normal(scale=0.5, size=n) > 0).astype(int)

    proba = []
    for _ in range(2):
        clf = build_classifier(FEATURE_SCHEMA_CORE)
        clf.fit(X, y)
        proba.append(clf.predict_proba(X))
    np.testing.assert_array_equal(proba[0], proba[1])
