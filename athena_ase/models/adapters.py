"""Shallow per-instrument adapters (ASE v2.1 §7)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterCriteria:
    min_labels: int = 500
    min_oos_signals: int = 30
    min_folds_improved: int = 3
    max_features: int = 3


def adapter_eligible(
    *,
    label_count: int,
    oos_signals: int,
    folds_improved: int,
    brier_delta: float,
    criteria: AdapterCriteria | None = None,
) -> bool:
    c = criteria or AdapterCriteria()
    if label_count < c.min_labels:
        return False
    if oos_signals < c.min_oos_signals:
        return False
    if folds_improved < c.min_folds_improved:
        return False
    if brier_delta > 0:
        return False
    return True


ADAPTER_FEATURES = ("pooled_logit", "ret_z_1", "vol_level")
