"""Purge/embargo leakage tests."""

from __future__ import annotations

import pandas as pd

from athena_research.ase.walkforward import expanding_folds, labels_leak_into_train, purge_embargo_mask


def test_fold_test_labels_not_in_training():
    df = pd.DataFrame(
        {
            "instrument": ["EURUSD"] * 8,
            "decision_time_ms": list(range(8)),
            "label_start_ms": list(range(8)),
            "label_end_ms": [x + 2 for x in range(8)],
        }
    )
    splits = expanding_folds(df, n_folds=2)
    assert splits
    split = splits[-1]
    purged = purge_embargo_mask(df, train_idx=split.train_idx, test_idx=split.test_idx, max_hold_bars=2)
    assert not labels_leak_into_train(df, train_idx=purged, test_idx=split.test_idx)
