"""API contract smoke checks for key endpoints.

These tests are intentionally lightweight and static (AST-based) so they can run
fast without requiring full app boot or live broker connections.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.route_contract_helpers import endpoint_map_from_files


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"
EXECUTION_PATH = ROOT / "execution.py"
GUARDIAN_ROUTES_PATH = ROOT / "guardian_routes.py"
ROUTE_FILES = [
    ATHENA_PATH,
    EXECUTION_PATH,
    GUARDIAN_ROUTES_PATH,
    ROOT / "athena_app" / "api" / "routes_backtest.py",
    ROOT / "athena_app" / "api" / "routes_broker_status.py",
    ROOT / "athena_app" / "api" / "routes_execution.py",
    ROOT / "athena_app" / "api" / "routes_live_dashboard.py",
    ROOT / "athena_app" / "api" / "routes_lottery.py",
    ROOT / "athena_app" / "api" / "routes_market_data.py",
    ROOT / "athena_app" / "api" / "routes_scan.py",
    ROOT / "athena_app" / "api" / "routes_status.py",
]


def _endpoint_map() -> dict[str, set[str]]:
    """Merge @app.route decorators from monolith and split route modules."""
    return endpoint_map_from_files(ROUTE_FILES)


def _function_source_from_files(name: str) -> str:
    for path in ROUTE_FILES:
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.unparse(node)
    return ""


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
    assert "/api/live-feed-diagnostics" in ep and "GET" in ep["/api/live-feed-diagnostics"]
    assert "POST" in ep["/api/live-feed-diagnostics"]
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
    # Engine A v2: forex uses unified calc_confluence; regimeName still exposed in signal dict
    assert '"regimeName": res.get("regimeName")' in src


def test_live_feed_diagnostics_route_is_read_only():
    """Verify /api/live-feed-diagnostics does not call order execution functions."""
    func_source = _function_source_from_files("api_live_feed_diagnostics")
    if not func_source:
        raise AssertionError("api_live_feed_diagnostics function not found")
    forbidden_calls = [
        "mt5_send_order",
        "bybit_send_order",
        "execute_trade",
        "place_order",
        "send_order",
        "open_position",
        "close_position",
    ]
    for forbidden in forbidden_calls:
        assert forbidden not in func_source, f"Found forbidden call {forbidden} in api_live_feed_diagnostics"


def test_scan_response_includes_payload_version_and_contract():
    """Verify scan response includes payloadVersion and contract metadata."""
    scanner_src = (ROOT / "scanner.py").read_text(encoding="utf-8")
    assert '"payloadVersion": "2.0"' in scanner_src
    assert '"contract": {' in scanner_src
    assert '"engineA": "v2_factor_scoring"' in scanner_src
    assert '"engineB": "naked_structure"' in scanner_src
    assert '"engineC": "consensus"' in scanner_src
    assert '"engineD": "scalp_vp"' in scanner_src
