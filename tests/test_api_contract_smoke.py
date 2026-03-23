"""API contract smoke checks for key endpoints.

These tests are intentionally lightweight and static (AST-based) so they can run
fast without requiring full app boot or live broker connections.
"""

from __future__ import annotations

import ast
from pathlib import Path


ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"


def _endpoint_map() -> dict[str, set[str]]:
    tree = ast.parse(ATHENA_PATH.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not (isinstance(func, ast.Attribute) and func.attr == "route"):
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = dec.args[0].value
            methods = {"GET"}
            for kw in dec.keywords or []:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    vals = []
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant):
                            vals.append(str(elt.value))
                    if vals:
                        methods = set(vals)
            out[path] = methods
    return out


def test_key_endpoints_exist_with_methods():
    ep = _endpoint_map()
    assert "/api/scan" in ep and "POST" in ep["/api/scan"]
    assert "/api/analyze" in ep and "POST" in ep["/api/analyze"]
    assert "/api/engine-c-scan" in ep and "POST" in ep["/api/engine-c-scan"]
    assert "/api/execute" in ep and "POST" in ep["/api/execute"]
    assert "/api/quick-execute" in ep and "POST" in ep["/api/quick-execute"]
    assert "/api/backtest" in ep and "POST" in ep["/api/backtest"]


def test_execute_payload_contract_strings_present():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert '"signal" not in d' in src
    assert "Invalid payload" in src
    assert "SIGNAL_FLIPPED" in src

