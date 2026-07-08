"""Training reference distributions for ASE drift monitor (PSI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from athena_ase.features.build import CATEGORICAL_FEATURES

MONITOR_REFERENCE_FILE = "monitor_reference.npz"


def build_monitor_reference(
    frame: Any,
    schema: tuple[str, ...],
    *,
    min_samples: int = 5,
) -> dict[str, np.ndarray]:
    """Extract per-feature reference arrays from a labeled/eval dataframe."""
    ref: dict[str, np.ndarray] = {}
    for feat in schema:
        if feat in CATEGORICAL_FEATURES:
            continue
        if feat not in frame.columns:
            continue
        vals = frame[feat].dropna().to_numpy(dtype=float)
        if len(vals) >= min_samples:
            ref[feat] = vals
    return ref


def save_monitor_reference(path: Path, reference: dict[str, np.ndarray]) -> str:
    """Persist reference arrays; returns sha256 hex of file bytes."""
    import hashlib

    path.parent.mkdir(parents=True, exist_ok=True)
    if reference:
        np.savez_compressed(path, **reference)
    else:
        np.savez_compressed(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_monitor_reference(path: Path) -> dict[str, np.ndarray]:
    if not path.exists():
        return {}
    with np.load(path, allow_pickle=False) as data:
        return {key: np.asarray(data[key], dtype=float) for key in data.files}
