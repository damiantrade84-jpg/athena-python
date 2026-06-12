"""Ensure ASE packages do not import legacy Engine A modules."""

from __future__ import annotations

import ast
from pathlib import Path

BANNED = frozenset(
    {
        "factor_scoring",
        "scoring",
        "feature_normalizer",
        "engine_a_trade_gate",
        "forex_scoring",
    }
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    ROOT / "athena_ase",
    ROOT / "athena_research" / "ase",
)


def _imports_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module.split(".")[0])
    return found


def test_ase_import_graph_excludes_legacy_engine_a():
    violations: list[str] = []
    for pkg in PACKAGES:
        if not pkg.exists():
            continue
        for path in pkg.rglob("*.py"):
            for imp in _imports_in_file(path):
                if imp in BANNED:
                    violations.append(f"{path.relative_to(ROOT)} imports {imp}")
    assert not violations, "\n".join(violations)
