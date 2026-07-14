from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ghost_package_does_not_import_other_engine_scoring_or_auto_trader():
    forbidden = (
        "engine_a_v3",
        "factor_scoring",
        "engine_c",
        "scalp_engine",
        "athena_ase",
        "auto_trader",
    )
    violations = []
    for path in sorted((ROOT / "ghost_trade").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(forbidden):
                    violations.append(f"{path.name}:{name}")
    assert violations == []


def test_athena_registers_ghost_routes_with_shared_candle_dependencies():
    source = (ROOT / "athena.py").read_text(encoding="utf-8")

    assert "from ghost_trade.api import register_ghost_trade_routes" in source
    assert "from ghost_trade.runtime import build_ghost_trade_service" in source
    assert "register_ghost_trade_routes(" in source
    assert "fetch_bybit_klines=_fetch_bybit_klines" in source
    assert "fetch_mt5=fetch_mt5" in source
    assert "GhostScheduler" not in source
    assert "_ghost_trade_scheduler" not in source
