"""API contract smoke checks for key endpoints.

These tests are intentionally lightweight and static (AST-based) so they can run
fast without requiring full app boot or live broker connections.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"
EXECUTION_PATH = ROOT / "execution.py"
GUARDIAN_ROUTES_PATH = ROOT / "guardian_routes.py"


def _endpoint_map_from_add_url_rule(src: str) -> dict[str, set[str]]:
    """Collect Flask `app.add_url_rule("/path", ..., methods=[...])` registrations."""
    tree = ast.parse(src)
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_url_rule"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        path = node.args[0].value
        if not isinstance(path, str):
            continue
        methods = {"GET"}
        for kw in node.keywords or []:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                vals = []
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant):
                        vals.append(str(elt.value))
                if vals:
                    methods = set(vals)
        out[path] = methods
    return out


def _endpoint_map_from_source(src: str) -> dict[str, set[str]]:
    tree = ast.parse(src)
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


def _endpoint_map() -> dict[str, set[str]]:
    """Merge @app.route decorators from monolith and split route modules."""
    merged: dict[str, set[str]] = {}
    for path in (ATHENA_PATH, EXECUTION_PATH, GUARDIAN_ROUTES_PATH):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        part = _endpoint_map_from_source(text)
        part.update(_endpoint_map_from_add_url_rule(text))
        for k, v in part.items():
            merged[k] = v
    return merged


def test_key_endpoints_exist_with_methods():
    ep = _endpoint_map()
    assert "/api/scan" in ep and "POST" in ep["/api/scan"]
    assert "/api/analyze" in ep and "POST" in ep["/api/analyze"]
    assert "/api/pair-scan" in ep and "POST" in ep["/api/pair-scan"]
    assert "/api/engine-c-scan" in ep and "POST" in ep["/api/engine-c-scan"]
    assert "/api/scalp-pairs" in ep and "GET" in ep["/api/scalp-pairs"]
    assert "/api/scalp-scan" in ep and "POST" in ep["/api/scalp-scan"]
    assert "/api/scalp-execute" in ep and "POST" in ep["/api/scalp-execute"]
    assert "/api/execute" in ep and "POST" in ep["/api/execute"]
    assert "/api/quick-execute" in ep and "POST" in ep["/api/quick-execute"]
    assert "/api/backtest" in ep and "POST" in ep["/api/backtest"]
    assert "/api/intermarket-matrix" in ep and "GET" in ep["/api/intermarket-matrix"]
    assert "/api/bt-min" in ep and "GET" in ep["/api/bt-min"] and "POST" in ep["/api/bt-min"]
    assert "/api/naked-style-thresholds" in ep and "GET" in ep["/api/naked-style-thresholds"]
    assert "POST" in ep["/api/naked-style-thresholds"]
    assert "/api/guardian/status" in ep and "GET" in ep["/api/guardian/status"]
    assert "/api/forensics/summary" in ep and "GET" in ep["/api/forensics/summary"]


def test_execute_payload_contract_strings_present():
    src = ATHENA_PATH.read_text(encoding="utf-8") + EXECUTION_PATH.read_text(
        encoding="utf-8"
    )
    assert '"signal" not in d' in src
    assert "Invalid payload" in src
    assert "SIGNAL_FLIPPED" in src


def test_execution_uses_aware_utc_timestamp_and_no_dead_macro_expression():
    src = EXECUTION_PATH.read_text(encoding="utf-8")
    assert "datetime.utcnow().isoformat()" not in src
    assert "datetime.now(timezone.utc).isoformat()" in src
    assert 'engine_b.get("macro_swing_sequence", "RANGING")' not in src


def test_live_forex_payload_exposes_explicit_regime_name():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert '"regimeName": _fx_regime.get("label", "RANGING")' in src

