"""Feature causality and PTIS import guard (ASE v2.1 §5, §1.1)."""

from __future__ import annotations

import ast
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
