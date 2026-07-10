"""T0.2 — fold stability comes from genuinely OOS per-fold retraining."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from athena_ase.features.build import active_feature_schema
from athena_ase.horizon import HORIZONS
from athena_research.ase.train import _fit_and_threshold, train_family_horizon
from athena_research.ase.walkforward import expanding_folds, purge_embargo_mask

_BAR_MS = 86_400_000  # swing / D1


def _labeled_frame(n: int = 500, hold_bars: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    df = pd.DataFrame(
        {
            "decision_time_ms": np.arange(n) * _BAR_MS,
            "horizon": "swing",
            "instrument": "EURUSD",
            "net_R": rng.normal(size=n),
        }
    )
    df["label_start_ms"] = df["decision_time_ms"]
    df["label_end_ms"] = df["decision_time_ms"] + hold_bars * _BAR_MS
    return df


def test_purged_folds_never_overlap_test_window():
    df = _labeled_frame()
    max_hold = HORIZONS["swing"].max_hold_bars
    embargo_ms = (max_hold + 1) * _BAR_MS
    folds = expanding_folds(df)
    assert folds
    for fold in folds:
        purged = purge_embargo_mask(
            df, train_idx=fold.train_idx, test_idx=fold.test_idx, max_hold_bars=max_hold
        )
        assert len(purged)
        train = df.iloc[purged]
        test = df.iloc[fold.test_idx]
        test_start = int(test["decision_time_ms"].min())
        assert int(train["decision_time_ms"].max()) < test_start - embargo_ms
        # No train label interval may reach into the test window (purge).
        assert int(train["label_end_ms"].max()) < test_start


def test_fold_models_are_fold_local():
    # The fold loop must retrain per fold via _fit_and_threshold rather than
    # re-scoring through the single 80% model / global threshold.
    src = inspect.getsource(train_family_horizon)
    assert "purge_embargo_mask(" in src
    assert 'fold_fit = _fit_and_threshold(' in src
    assert 'fold_fit["threshold"].threshold' in src
    assert '"oos": True' in src

    # Distinct training frames produce distinct fitted model objects.
    schema = active_feature_schema(enriched=False)

    def _frame(seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        n = 400
        f = pd.DataFrame(rng.normal(size=(n, len(schema))), columns=list(schema))
        f["y"] = (f[schema[0]] > 0).astype(int)
        f["net_R"] = np.where(f["y"] == 1, 0.7, -1.0)
        f["MAE_R"] = np.abs(rng.normal(size=n))
        f["MFE_R"] = np.abs(rng.normal(size=n))
        f["hold_bars"] = 3
        f["sample_weight"] = 1.0
        f["decision_time_ms"] = np.arange(n) * _BAR_MS
        return f

    a = _fit_and_threshold(_frame(1), feature_names=schema, family="forex", horizon="swing", log_trials=False)
    b = _fit_and_threshold(_frame(2), feature_names=schema, family="forex", horizon="swing", log_trials=False)
    assert a["result"].model is not b["result"].model
