"""Quantile head helpers and bracket clamps (ASE v2.1 §6, §5)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from athena_ase.features.build import FEATURE_SCHEMA_CORE
from athena_ase.models.meta import _categorical_indices, _prepare_matrix

QUANTILES: tuple[float, ...] = (0.10, 0.25, 0.50, 0.75, 0.90)
QUANTILE_LABELS: tuple[str, ...] = ("q10", "q25", "q50", "q75", "q90")
TARGETS: tuple[str, ...] = ("net_R", "MAE_R", "MFE_R", "hold_bars")

QUANTILE_HGB_PARAMS: dict[str, Any] = {
    "max_iter": 200,
    "learning_rate": 0.06,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 20,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": 42,
}


def fix_quantile_crossing(row: dict[str, float]) -> dict[str, float]:
    keys = sorted(row.keys(), key=lambda k: float(k.replace("q", "")))
    vals = sorted(float(row[k]) for k in keys)
    return {k: v for k, v in zip(keys, vals)}


def train_quantile_heads(
    X: np.ndarray | list[dict[str, Any]],
    targets: Mapping[str, Sequence[float]],
    *,
    sample_weight: np.ndarray | None = None,
    feature_names: Sequence[str] | None = None,
) -> dict[str, dict[str, HistGradientBoostingRegressor]]:
    if feature_names is not None:
        names = tuple(feature_names)
    elif isinstance(X, np.ndarray):
        names = tuple(f"feature_{i}" for i in range(X.shape[1]))
    else:
        names = FEATURE_SCHEMA_CORE
    X_mat = _prepare_matrix(X, names)
    if len(X_mat) == 0:
        raise ValueError("cannot train quantile heads with no rows")

    heads: dict[str, dict[str, HistGradientBoostingRegressor]] = {}
    for target in TARGETS:
        if target not in targets:
            raise ValueError(f"missing quantile target: {target}")
        y = np.asarray(targets[target], dtype=float)
        if len(y) != len(X_mat):
            raise ValueError(f"quantile target length mismatch: {target}")
        block: dict[str, HistGradientBoostingRegressor] = {}
        for label, quantile in zip(QUANTILE_LABELS, QUANTILES):
            model = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=quantile,
                categorical_features=_categorical_indices(names),
                **QUANTILE_HGB_PARAMS,
            )
            model.fit(X_mat, y, sample_weight=sample_weight)
            block[label] = model
        heads[target] = block
    return heads


def predict_quantile_heads(
    heads: Mapping[str, Mapping[str, Any]],
    X: np.ndarray,
) -> dict[str, list[dict[str, float]]]:
    X_mat = np.asarray(X, dtype=float)
    predicted: dict[str, list[dict[str, float]]] = {}
    for target, block in heads.items():
        raw = {
            label: np.asarray(model.predict(X_mat), dtype=float)
            for label, model in block.items()
        }
        rows: list[dict[str, float]] = []
        for i in range(len(X_mat)):
            rows.append(
                fix_quantile_crossing(
                    {label: float(values[i]) for label, values in raw.items()}
                )
            )
        predicted[target] = rows
    return predicted


def clamp_brackets(
    *,
    entry: float,
    direction: int,
    sl: float,
    tp1: float,
    r_unit: float,
) -> tuple[float, float, str | None]:
    if r_unit <= 0:
        return sl, tp1, "invalid_r_unit"
    sl_dist = abs(entry - sl)
    min_sl = 0.5 * r_unit
    max_sl = 1.5 * r_unit
    if sl_dist < min_sl or sl_dist > max_sl:
        clamped = entry - direction * max(min_sl, min(sl_dist, max_sl))
        sl = clamped
        sl_dist = abs(entry - sl)
    tp_dist = abs(tp1 - entry)
    if tp_dist < 0.6 * sl_dist:
        return sl, tp1, "rr_floor_watch"
    return sl, tp1, None
