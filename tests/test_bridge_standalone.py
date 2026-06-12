"""ASE execution bridge import-graph isolation."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASE_ROOT = ROOT / "athena_ase"

BANNED = frozenset(
    {
        "scoring",
        "factor_scoring",
        "forex_scoring",
        "engine_a_trade_gate",
        "engine_b_ai",
        "engine_c",
        "engine_c_ai",
        "market_structure",
        "scalp_engine",
        "engine_d",
    }
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


def test_ase_package_excludes_engine_abcd_modules():
    violations: list[str] = []
    for path in ASE_ROOT.rglob("*.py"):
        for imp in _imports_in_file(path):
            if imp in BANNED:
                violations.append(f"{path.relative_to(ROOT)} imports {imp}")
    assert not violations, "\n".join(violations)


def test_bridge_allows_risk_and_executor_primitives_only_at_runtime():
    bridge = (ASE_ROOT / "execution" / "bridge.py").read_text(encoding="utf-8")
    assert "from risk_engine import risk_check" in bridge or "risk_engine" in bridge
    assert "mt5_executor" in bridge
    assert "bybit_executor" in bridge
    assert "engine_c" not in bridge
    assert "scoring" not in bridge
