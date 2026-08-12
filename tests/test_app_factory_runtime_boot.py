from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"


def test_create_app_boots_runtime_services():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))

    create_app = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    )

    calls = [
        node
        for node in ast.walk(create_app)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]

    assert any(
        isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_athena"
        and call.func.attr == "ensure_runtime_services_started"
        for call in calls
    )


def test_runtime_services_start_suggested_trade_monitor():
    tree = ast.parse((ROOT / "athena.py").read_text(encoding="utf-8"))

    ensure_runtime = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "ensure_runtime_services_started"
    )

    calls = [node for node in ast.walk(ensure_runtime) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Name) and call.func.id == "start_suggested_trade_monitor"
        for call in calls
    )
