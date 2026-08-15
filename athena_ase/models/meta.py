"""Pooled per-family meta-classifier (ASE v2.1 §6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from athena_ase.features.build import CATEGORICAL_FEATURES, FEATURE_SCHEMA_CORE, categorical_code
from athena_research.ase.trials_registry import append_trial

RANDOM_STATE = 42

# max_iter is pinned globally (WO-ASE-INTEGRITY-2026-07 T0.3): sklearn's internal
# early-stopping uses a random validation split, which leaks train information
# through overlapping triple-barrier labels. Iteration count must come from the
# purged walk-forward harness, not a random split.
BASE_HGB_PARAMS: dict[str, Any] = {
    "max_iter": 300,
    "learning_rate": 0.06,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 200,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "class_weight": "balanced",
    "random_state": RANDOM_STATE,
}

@dataclass(frozen=True)
class MetaTrainResult:
    model: HistGradientBoostingClassifier
    config: dict[str, Any]
    config_hash: str
    # Diagnostic only (accuracy on the training rows themselves) — never a gate input.
    train_accuracy: float
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


def _raw_matrix(
    X: np.ndarray | list[dict[str, Any]],
    feature_names: Sequence[str],
) -> np.ndarray:
    """Feature matrix exactly as computed, with NaN left in place."""
    if isinstance(X, np.ndarray):
        return X.astype(float)
    matrix = np.empty((len(X), len(feature_names)), dtype=float)
    for i, row in enumerate(X):
        for j, name in enumerate(feature_names):
            value = row.get(name, np.nan)
            matrix[i, j] = (
                categorical_code(value)
                if name in CATEGORICAL_FEATURES
                else float(value)
            )
    return matrix


def _prepare_matrix(
    X: np.ndarray | list[dict[str, Any]],
    feature_names: Sequence[str],
) -> np.ndarray:
    matrix = _raw_matrix(X, feature_names)
    if len(matrix):
        all_missing = np.all(np.isnan(matrix), axis=0)
        matrix[:, all_missing] = 0.0
    return matrix


def all_nan_feature_columns(
    X: np.ndarray | list[dict[str, Any]],
    feature_names: Sequence[str],
) -> list[str]:
    """Columns entirely NaN in the raw matrix (the ones _prepare_matrix fills
    with 0.0). Recorded at train time so inference can apply the identical
    fill — HGB routes NaN and 0.0 differently, so unfilled live rows would
    silently diverge from the trained split structure.
    """
    matrix = _raw_matrix(X, feature_names)
    if not len(matrix):
        return []
    missing = np.all(np.isnan(matrix), axis=0)
    return [str(name) for name, is_nan in zip(feature_names, missing) if bool(is_nan)]


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
    """Train the fixed final-build HGB configuration."""
    names = tuple(feature_names) if feature_names is not None else FEATURE_SCHEMA_CORE
    X_mat = _prepare_matrix(X, names)
    y_arr = np.asarray(y, dtype=int)
    if len(y_arr) == 0:
        raise ValueError("cannot train meta-classifier with no rows")
    if len(np.unique(y_arr)) < 2:
        raise ValueError("meta-classifier requires both target classes")

    clf = build_classifier(names)
    clf.fit(X_mat, y_arr, sample_weight=sample_weight)
    score = float(clf.score(X_mat, y_arr))
    trial_row = (
        append_trial(
            family=family,
            horizon=horizon,
            config=BASE_HGB_PARAMS,
            results={"train_accuracy": score, "n_samples": len(y_arr)},
            notes="fixed meta HGB configuration",
        )
        if log_trials
        else {"config_hash": ""}
    )
    return MetaTrainResult(
        model=clf,
        config=dict(BASE_HGB_PARAMS),
        config_hash=trial_row.get("config_hash", ""),
        train_accuracy=score,
        feature_names=names,
    )
