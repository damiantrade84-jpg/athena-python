"""Pooled per-family meta-classifier (ASE v2.1 §6)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from athena_ase.features.build import CATEGORICAL_FEATURES, FEATURE_SCHEMA_CORE
from athena_research.ase.trials_registry import append_trial

RANDOM_STATE = 42

BASE_HGB_PARAMS: dict[str, Any] = {
    "max_iter": 400,
    "learning_rate": 0.06,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 200,
    "l2_regularization": 1.0,
    "early_stopping": True,
    "validation_fraction": 0.15,
    "class_weight": "balanced",
    "random_state": RANDOM_STATE,
}

HYPERPARAM_GRID: dict[str, list[Any]] = {
    "learning_rate": [0.04, 0.06, 0.08],
    "max_leaf_nodes": [23, 31, 47],
    "min_samples_leaf": [100, 200],
}


@dataclass(frozen=True)
class MetaTrainResult:
    model: HistGradientBoostingClassifier
    config: dict[str, Any]
    config_hash: str
    fold_score: float
    feature_names: tuple[str, ...]


def _categorical_indices(feature_names: Sequence[str]) -> list[int]:
    name_to_idx = {n: i for i, n in enumerate(feature_names)}
    return [name_to_idx[n] for n in CATEGORICAL_FEATURES if n in name_to_idx]


def _monotonic_constraints(feature_names: Sequence[str]) -> list[int]:
    out = [0] * len(feature_names)
    for i, name in enumerate(feature_names):
        if name == "agreement_count":
            out[i] = 1
    return out


def _prepare_matrix(
    X: np.ndarray | list[dict[str, Any]],
    feature_names: Sequence[str],
) -> np.ndarray:
    if isinstance(X, np.ndarray):
        return X.astype(float)
    matrix = np.empty((len(X), len(feature_names)), dtype=object)
    for i, row in enumerate(X):
        for j, name in enumerate(feature_names):
            matrix[i, j] = row.get(name, np.nan)
    return matrix


def build_classifier(
    feature_names: Sequence[str] | None = None,
    **overrides: Any,
) -> HistGradientBoostingClassifier:
    names = tuple(feature_names) if feature_names is not None else FEATURE_SCHEMA_CORE
    params = {**BASE_HGB_PARAMS, **overrides}
    params["categorical_features"] = _categorical_indices(names)
    params["monotonic_cst"] = _monotonic_constraints(names)
    return HistGradientBoostingClassifier(**params)


def train_meta_classifier(
    X: np.ndarray | list[dict[str, Any]],
    y: Sequence[int],
    *,
    sample_weight: np.ndarray | None = None,
    feature_names: Sequence[str] | None = None,
    family: str = "all",
    horizon: str = "all",
    log_trials: bool = True,
) -> MetaTrainResult:
    """Train with 3×3×2 grid; log every config to trials registry."""
    names = tuple(feature_names) if feature_names is not None else FEATURE_SCHEMA_CORE
    X_mat = _prepare_matrix(X, names)
    y_arr = np.asarray(y, dtype=int)
    best_model: HistGradientBoostingClassifier | None = None
    best_score = -np.inf
    best_config: dict[str, Any] = {}
    best_hash = ""

    grid_keys = list(HYPERPARAM_GRID.keys())
    grid_values = [HYPERPARAM_GRID[k] for k in grid_keys]

    for combo in itertools.product(*grid_values):
        overrides = dict(zip(grid_keys, combo))
        clf = build_classifier(names, **overrides)
        clf.fit(X_mat, y_arr, sample_weight=sample_weight)
        score = float(clf.score(X_mat, y_arr))
        config = {**BASE_HGB_PARAMS, **overrides}
        trial_row = append_trial(
            family=family,
            horizon=horizon,
            config=config,
            results={"train_accuracy": score, "n_samples": len(y_arr)},
            notes="meta HGB grid search",
        ) if log_trials else {"config_hash": ""}

        if score > best_score:
            best_score = score
            best_model = clf
            best_config = config
            best_hash = trial_row.get("config_hash", "")

    assert best_model is not None
    return MetaTrainResult(
        model=best_model,
        config=best_config,
        config_hash=best_hash,
        fold_score=best_score,
        feature_names=names,
    )
