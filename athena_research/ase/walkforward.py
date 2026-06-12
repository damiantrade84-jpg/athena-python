"""Walk-forward splits with purge and embargo (ASE v2.1 §9)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

HOLDOUT_FRACTION = 0.20
N_EXPANDING_FOLDS = 4


@dataclass(frozen=True)
class FoldSplit:
    train_idx: np.ndarray
    test_idx: np.ndarray
    fold: int


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_idx: np.ndarray
    test_idx: np.ndarray
    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int


@dataclass(frozen=True)
class WalkForwardManifest:
    holdout_idx: np.ndarray
    folds: tuple[WalkForwardFold, ...]
    holdout_start_ms: int
    max_hold_bars: int
    embargo_bars: int


def holdout_split(df: pd.DataFrame, *, holdout_frac: float = HOLDOUT_FRACTION) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("decision_time_ms")
    cut = int(len(df) * (1.0 - holdout_frac))
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def expanding_folds(df: pd.DataFrame, *, n_folds: int = N_EXPANDING_FOLDS) -> list[FoldSplit]:
    df = df.sort_values("decision_time_ms").reset_index(drop=True)
    n = len(df)
    if n == 0:
        return []
    test_size = max(n // (n_folds + 1), 1)
    splits: list[FoldSplit] = []
    for fold in range(n_folds):
        test_start = n - (n_folds - fold) * test_size
        test_end = min(test_start + test_size, n)
        if test_start <= 0 or test_start >= test_end:
            continue
        train_idx = np.arange(0, test_start)
        test_idx = np.arange(test_start, test_end)
        splits.append(FoldSplit(train_idx=train_idx, test_idx=test_idx, fold=fold))
    return splits


def _bar_ms(row: pd.Series, default_ms: int = 3_600_000) -> int:
    hz = str(row.get("horizon", "intraday"))
    return 3_600_000 if hz == "intraday" else 86_400_000


def purge_embargo_mask(
    df: pd.DataFrame,
    *,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    max_hold_bars: int,
) -> np.ndarray:
    test = df.iloc[test_idx]
    if test.empty:
        return train_idx
    test_start = int(test["decision_time_ms"].min())
    test_end = int(test["label_end_ms"].max()) if "label_end_ms" in test else int(test["decision_time_ms"].max())
    keep: list[int] = []
    for i in train_idx:
        row = df.iloc[int(i)]
        start = int(row.get("label_start_ms", row["decision_time_ms"]))
        end = int(row.get("label_end_ms", row["decision_time_ms"]))
        embargo_ms = (max_hold_bars + 1) * _bar_ms(row)
        overlaps = start <= test_end and end >= test_start
        pre_embargo = end >= test_start - embargo_ms and end < test_start
        post_embargo = start <= test_end + embargo_ms and start > test_end
        if overlaps or pre_embargo or post_embargo:
            continue
        keep.append(int(i))
    return np.asarray(keep, dtype=int)


def labels_leak_into_train(
    df: pd.DataFrame,
    *,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
) -> bool:
    test_keys = set(
        zip(
            df.iloc[test_idx]["instrument"].astype(str),
            df.iloc[test_idx]["decision_time_ms"].astype(int),
        )
    )
    train_keys = set(
        zip(
            df.iloc[train_idx]["instrument"].astype(str),
            df.iloc[train_idx]["decision_time_ms"].astype(int),
        )
    )
    return bool(test_keys & train_keys)


def build_walkforward_manifest(
    events: pd.DataFrame,
    *,
    max_hold_bars: int,
    holdout_fraction: float = HOLDOUT_FRACTION,
    n_folds: int = N_EXPANDING_FOLDS,
) -> WalkForwardManifest:
    """Chronological 20% holdout + 4 expanding folds on remaining 80%."""
    if events.empty:
        return WalkForwardManifest(
            holdout_idx=np.array([], dtype=int),
            folds=(),
            holdout_start_ms=0,
            max_hold_bars=max_hold_bars,
            embargo_bars=max_hold_bars + 1,
        )

    df = events.sort_values("decision_time_ms").reset_index(drop=True)
    n = len(df)
    holdout_n = max(1, int(round(n * holdout_fraction)))
    dev_n = n - holdout_n
    dev_df = df.iloc[:dev_n].copy()
    holdout_idx = np.arange(dev_n, n, dtype=int)
    holdout_start_ms = int(df.iloc[dev_n]["decision_time_ms"]) if dev_n < n else 0

    raw_folds = expanding_folds(dev_df.reset_index(drop=True), n_folds=n_folds)
    folds: list[WalkForwardFold] = []
    for split in raw_folds:
        purged_train = purge_embargo_mask(
            dev_df.reset_index(drop=True),
            train_idx=split.train_idx,
            test_idx=split.test_idx,
            max_hold_bars=max_hold_bars,
        )
        dev_times = dev_df["decision_time_ms"].to_numpy(dtype=np.int64)
        test_times = dev_times[split.test_idx]
        train_times = dev_times[purged_train] if len(purged_train) else np.array([], dtype=np.int64)
        folds.append(
            WalkForwardFold(
                fold_id=split.fold,
                train_idx=purged_train,
                test_idx=split.test_idx,
                train_start_ms=int(train_times[0]) if len(train_times) else 0,
                train_end_ms=int(train_times[-1]) if len(train_times) else 0,
                test_start_ms=int(test_times[0]),
                test_end_ms=int(test_times[-1]),
            )
        )

    return WalkForwardManifest(
        holdout_idx=holdout_idx,
        folds=tuple(folds),
        holdout_start_ms=holdout_start_ms,
        max_hold_bars=max_hold_bars,
        embargo_bars=max_hold_bars + 1,
    )


def manifest_to_dict(manifest: WalkForwardManifest) -> dict[str, Any]:
    return {
        "holdout_fraction": HOLDOUT_FRACTION,
        "n_folds": len(manifest.folds),
        "holdout_start_ms": manifest.holdout_start_ms,
        "max_hold_bars": manifest.max_hold_bars,
        "embargo_bars": manifest.embargo_bars,
        "folds": [
            {
                "fold_id": f.fold_id,
                "n_train": int(len(f.train_idx)),
                "n_test": int(len(f.test_idx)),
                "train_start_ms": f.train_start_ms,
                "train_end_ms": f.train_end_ms,
                "test_start_ms": f.test_start_ms,
                "test_end_ms": f.test_end_ms,
            }
            for f in manifest.folds
        ],
    }
