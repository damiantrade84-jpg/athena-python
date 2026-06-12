"""Feature causality and PTIS import guard (ASE v2.1 §5, §1.1)."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


def test_feature_builder_imports_only_asof_from_data_layer():
    build_path = Path(__file__).resolve().parents[1] / "athena_ase" / "features" / "build.py"
    source = build_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    data_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "athena_ase.data"
        ):
            for alias in node.names:
                data_imports.append(alias.name)

    assert data_imports == ["asof"], (
        "features/build.py must import only asof from athena_ase.data; "
        f"found: {data_imports}"
    )


def test_feature_builder_does_not_import_legacy_scoring_modules():
    build_path = Path(__file__).resolve().parents[1] / "athena_ase" / "features" / "build.py"
    source = build_path.read_text(encoding="utf-8").lower()
    banned = ("scoring", "factor_scoring", "forex_scoring", "indicators.py")
    for token in banned:
        assert token not in source


def test_categorical_encoding_is_stable():
    from athena_ase.features.build import categorical_code

    expected = int.from_bytes(hashlib.sha256(b"EURUSD").digest()[:8], "big") % 255
    assert categorical_code("EURUSD") == expected


def test_training_matrix_neutralizes_all_missing_columns():
    import numpy as np

    from athena_ase.models.meta import _prepare_matrix

    matrix = _prepare_matrix(
        [{"ret_z_1": float("nan"), "vol_level": 1.0}, {"ret_z_1": float("nan"), "vol_level": 2.0}],
        ("ret_z_1", "vol_level"),
    ).astype(float)
    assert np.array_equal(matrix[:, 0], np.zeros(2))
    assert np.array_equal(matrix[:, 1], np.array([1.0, 2.0]))
