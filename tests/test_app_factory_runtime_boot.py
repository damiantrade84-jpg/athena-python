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
