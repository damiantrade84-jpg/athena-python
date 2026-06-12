"""Adapter gating tests."""

from __future__ import annotations

from athena_ase.models.adapters import ADAPTER_FEATURES, adapter_eligible


def test_adapter_requires_min_labels_and_brier():
    assert not adapter_eligible(label_count=100, oos_signals=40, folds_improved=3, brier_delta=0.0)
    assert adapter_eligible(label_count=600, oos_signals=40, folds_improved=3, brier_delta=0.0)
    assert not adapter_eligible(label_count=600, oos_signals=40, folds_improved=3, brier_delta=0.01)


def test_adapter_feature_cap():
    assert len(ADAPTER_FEATURES) <= 3
