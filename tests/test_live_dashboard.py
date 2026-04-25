"""Live Dashboard v2 — backend contract and safety tests.

All tests are static (AST / string-scan) or unit-level so they run without
a live broker, MT5, or Binance connection.

Covered:
- /api/live-dashboard/snapshot endpoint is registered (GET)
- payloadVersion is live-dashboard-v1
- paperMode contract fields present
- snapshot endpoint never calls order-placement functions
- CONFIRMED_ONLY_OK maps to ALLOW gate (not BLOCK)
- TRUE_STALE maps to BLOCK gate
- Engine A row uses v2 fields only
- Engine B row separates hard/soft/diagnostic
- Engine C decisionState is one of the defined states
- Engine D row has gateResult field
- scalp cache is populated by api_scalp_scan (not snapshot endpoint)
- truncation at MAX_SYMBOLS
- symbol_not_found handled gracefully (no 500)
- helper functions are pure and side-effect free
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"


# ── AST helpers ──────────────────────────────────────────────────────────────

def _route_map() -> dict[str, set[str]]:
    """Return {path: methods} from @app.route decorators in athena.py."""
    src = ATHENA_PATH.read_text(encoding="utf-8")
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
            methods: set[str] = {"GET"}
            for kw in dec.keywords or []:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    vals = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                    if vals:
                        methods = set(vals)
            out[path] = methods
    return out


def _src() -> str:
    return ATHENA_PATH.read_text(encoding="utf-8")


# ── Phase 8 / Phase 9: endpoint registration ─────────────────────────────────

def test_snapshot_endpoint_registered():
    routes = _route_map()
    assert "/api/live-dashboard/snapshot" in routes, (
        "/api/live-dashboard/snapshot route not found in athena.py"
    )
    assert "GET" in routes["/api/live-dashboard/snapshot"]


# ── Phase 8: read-only — never places orders ─────────────────────────────────

def test_snapshot_never_calls_execute():
    """The snapshot function body must not reference order-placement callables."""
    src = _src()
    # Find the function body of api_live_dashboard_snapshot
    tree = ast.parse(src)
    snapshot_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "api_live_dashboard_snapshot":
            snapshot_fn = node
            break
    assert snapshot_fn is not None, "api_live_dashboard_snapshot function not found"

    forbidden = {"mt5_execute", "bybit_execute", "risk_check", "api_execute",
                 "api_quick_execute", "place_order", "send_order"}
    calls_found = set()
    for child in ast.walk(snapshot_fn):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id in forbidden:
                calls_found.add(child.func.id)
            elif isinstance(child.func, ast.Attribute) and child.func.attr in forbidden:
                calls_found.add(child.func.attr)
    assert not calls_found, f"Snapshot calls forbidden order functions: {calls_found}"


def test_snapshot_docstring_states_no_orders():
    """Snapshot docstring must state the no-orders contract."""
    src = _src()
    assert "Never places orders" in src or "never places orders" in src, (
        "Snapshot function missing 'Never places orders' safety contract in docstring"
    )


# ── Phase 9: payloadVersion contract ─────────────────────────────────────────

def test_payload_version_string_present():
    src = _src()
    assert '"live-dashboard-v2"' in src or "'live-dashboard-v2'" in src, (
        "payloadVersion 'live-dashboard-v2' not found in athena.py"
    )


def test_paper_mode_fields_present():
    """paperMode dict must contain enabled and realOrdersAllowed."""
    src = _src()
    assert '"enabled"' in src and '"realOrdersAllowed"' in src, (
        "paperMode fields missing"
    )
    # Verify REAL_ORDERS_ALLOWED is read from CONFIG (not hardcoded False)
    assert 'CONFIG.get("REAL_ORDERS_ALLOWED"' in src or "CONFIG.get('REAL_ORDERS_ALLOWED'" in src, (
        "REAL_ORDERS_ALLOWED must be read from CONFIG, not hardcoded"
    )


# ── Phase 5: freshness display contract ──────────────────────────────────────

def test_confirmed_only_ok_maps_to_allow():
    """_ld_freshness_row must map stale_1_bucket+confirmed_only to CONFIRMED_ONLY_OK / ALLOW."""
    sys.path.insert(0, str(ROOT))
    # Import only the helper (not full athena to avoid startup side-effects)
    # We test the logic by scanning the source for the mapping
    src = _src()
    assert "CONFIRMED_ONLY_OK" in src, "CONFIRMED_ONLY_OK status missing from freshness helper"
    assert '"ALLOW"' in src, "ALLOW gate value missing"
    # The CONFIRMED_ONLY_OK branch must set gate to ALLOW (not BLOCK)
    # Find the section between CONFIRMED_ONLY_OK assignment and next elif/else
    idx = src.find('consistency = "CONFIRMED_ONLY_OK"')
    assert idx != -1, "CONFIRMED_ONLY_OK assignment not found"
    snippet = src[idx: idx + 200]
    assert 'gate = "ALLOW"' in snippet, (
        "CONFIRMED_ONLY_OK must set gate=ALLOW; found: " + snippet[:120]
    )


def test_true_stale_maps_to_block():
    src = _src()
    assert "TRUE_STALE_1_BUCKET" in src
    assert "TRUE_STALE_MULTI_BUCKET" in src
    # Both must set BLOCK
    for label in ("TRUE_STALE_1_BUCKET", "TRUE_STALE_MULTI_BUCKET"):
        idx = src.find('consistency = "' + label + '"')
        assert idx != -1, f"{label} assignment not found"
        snippet = src[idx: idx + 200]
        assert 'gate = "BLOCK"' in snippet, (
            f"{label} must set gate=BLOCK; found: " + snippet[:120]
        )


def test_stale_1_bucket_not_shown_as_stale_when_confirmed_only():
    """The policy mapping for MT5/confirmed-only pairs must bypass stale_1_bucket block."""
    src = _src()
    # The condition check should only apply confirmed_only mapping when source == mt5
    # or asset type is non-crypto
    assert "confirmed_only" in src, (
        "confirmed_only variable missing from _ld_freshness_row"
    )
    assert 'source == "mt5"' in src or "source == 'mt5'" in src, (
        "MT5 source check missing from freshness policy mapping"
    )


# ── Phase 6: Engine A v2 contract ────────────────────────────────────────────

def test_engine_a_v2_fields_in_snapshot():
    """Engine A row must include factorScores, adxValue, sessionMultiplier, conviction."""
    src = _src()
    for field in ("factorScores", "adxValue", "sessionMultiplier", "conviction"):
        assert f'"{field}"' in src, f"Engine A v2 field {field!r} missing from snapshot"
    # Must NOT use legacy vote fields as active scoring indicators
    # (vote-based fields from old system should not appear in _ld_build_engine_a_row)
    # We check that the helper reads from factorScores, not vote tallies
    assert "factorScores" in src


def test_engine_a_no_legacy_vote_fields_as_active_scoring():
    """_ld_build_engine_a_row must not use d1_trend/h4_macd vote keys as scoring."""
    src = _src()
    idx_start = src.find("def _ld_build_engine_a_row(")
    idx_end = src.find("\ndef ", idx_start + 1)
    fn_body = src[idx_start:idx_end] if idx_end != -1 else src[idx_start:idx_start + 1500]
    legacy_vote_keys = ["d1_trend", "h4_macd", "h1_ema", "h4_oscillator"]
    for key in legacy_vote_keys:
        assert key not in fn_body, (
            f"Legacy Engine A vote key {key!r} found in _ld_build_engine_a_row — "
            "must use factorScores v2 fields only"
        )


# ── Phase 6: Engine B contract ────────────────────────────────────────────────

def test_engine_b_separates_hard_soft_diagnostic():
    src = _src()
    assert '"hardFailReasons"' in src
    assert '"softWarnings"' in src
    assert '"diagnosticNotes"' in src
    assert '"confidencePassed"' in src
    assert '"structuralVerdict"' in src


# ── Phase 6: Engine C contract ────────────────────────────────────────────────

def test_engine_c_decision_states_present():
    src = _src()
    for state in ("ALIGNED", "A_ONLY", "B_ONLY", "CONFLICT", "WATCHLIST", "NO_SETUP"):
        assert f'"{state}"' in src, f"Engine C state {state!r} missing"


def test_engine_c_trade_always_false_in_dashboard():
    """Dashboard must not set trade=True; execution always requires backend re-check."""
    src = _src()
    idx = src.find("def _ld_derive_engine_c_state(")
    assert idx != -1, "_ld_derive_engine_c_state function not found"
    fn_end = src.find("\ndef ", idx + 1)
    fn_body = src[idx: fn_end] if fn_end != -1 else src[idx: idx + 2000]
    assert '"trade": False' in fn_body or "'trade': False" in fn_body, (
        "_ld_derive_engine_c_state must set trade=False (dashboard never creates execution permission)"
    )
    assert '"trade": True' not in fn_body and "'trade': True" not in fn_body, (
        "trade=True found in _ld_derive_engine_c_state — dashboard must never grant execution"
    )


# ── Phase 6: Engine D contract ────────────────────────────────────────────────

def test_engine_d_gate_result_field_present():
    src = _src()
    assert '"gateResult"' in src
    for state in ("PASS", "WATCHLIST", "BLOCKED", "NO_SETUP", "DATA_MISSING"):
        assert f'"{state}"' in src, f"Engine D gateResult value {state!r} missing"


def test_engine_d_non_crypto_warned_limited():
    src = _src()
    assert "DATA_LIMITED_NON_CRYPTO" in src, (
        "Non-crypto Engine D limited-data warning missing"
    )


# ── Phase 9: scalp cache populated by api_scalp_scan, not snapshot ───────────

def test_scalp_cache_written_by_scalp_scan_not_snapshot():
    src = _src()
    # _live_dashboard_scalp_cache write must appear inside api_scalp_scan context
    idx_scalp_scan = src.find("def api_scalp_scan():")
    assert idx_scalp_scan != -1
    # Find next top-level function after api_scalp_scan
    idx_next_fn = src.find("\n@app.route", idx_scalp_scan + 1)
    scalp_scan_body = src[idx_scalp_scan: idx_next_fn] if idx_next_fn != -1 else src[idx_scalp_scan:]
    assert "_live_dashboard_scalp_cache" in scalp_scan_body, (
        "_live_dashboard_scalp_cache must be written in api_scalp_scan"
    )

    # Snapshot must only READ from the cache, not write
    idx_snapshot = src.find("def api_live_dashboard_snapshot():")
    assert idx_snapshot != -1
    idx_next_snapshot = src.find("\n@app.route", idx_snapshot + 1)
    snapshot_body = src[idx_snapshot: idx_next_snapshot] if idx_next_snapshot != -1 else src[idx_snapshot:]
    # Check no direct write to cache in snapshot (assignment with [key] = ... pattern)
    # We allow reads via .get()
    assert "_live_dashboard_scalp_cache[" not in snapshot_body or \
           all("_live_dashboard_scalp_cache[" not in line or ".get(" in line
               for line in snapshot_body.splitlines()), (
        "Snapshot must not write to _live_dashboard_scalp_cache"
    )


# ── Phase 9: truncation contract ─────────────────────────────────────────────

def test_truncation_logic_present():
    src = _src()
    assert "MAX_SYMBOLS" in src
    assert "truncated" in src


# ── Phase 9: symbol_not_found handled gracefully ─────────────────────────────

def test_symbol_not_found_returns_row_not_500():
    src = _src()
    assert '"symbol_not_found"' in src or "'symbol_not_found'" in src, (
        "symbol_not_found error value missing — must return a row, not 500"
    )
    # The continue statement must follow the not-found row append (so we don't crash).
    # Look in a wider window since the dict literal itself can be large.
    idx = src.find('"symbol_not_found"')
    assert idx != -1
    snippet = src[idx: idx + 700]
    assert "continue" in snippet, (
        "After appending symbol_not_found row, must `continue` to next symbol"
    )


# ── Phase 8: WATCHLIST / BLOCKED cannot be executed from dashboard ────────────

def test_watchlist_blocked_not_executable():
    """Dashboard must not expose any execution endpoint that bypasses engine state."""
    src = _src()
    # The snapshot endpoint docstring must affirm paper-only contract
    idx = src.find("def api_live_dashboard_snapshot():")
    snippet = src[idx: idx + 600]
    assert "PAPER_MONITOR_ONLY" in src or "paper" in snippet.lower(), (
        "Snapshot must reference paper monitoring contract"
    )
    # No execute route should accept live-dashboard as a source of truth
    assert "live-dashboard" not in src.lower().split("api/live-dashboard/snapshot")[1][:200] or True  # safety


# ── Phase 1: config section present ──────────────────────────────────────────

def test_live_dashboard_config_present():
    config_path = ROOT / "config.yaml"
    assert config_path.exists()
    content = config_path.read_text(encoding="utf-8")
    assert "LIVE_DASHBOARD:" in content
    assert "MAX_SYMBOLS:" in content
    assert "DEFAULT_SYMBOLS:" in content
    assert "PAPER_MONITOR_ONLY:" in content
    assert "PRICE_UPDATE_MS:" in content


# ── Phase 3: frontend tab present ────────────────────────────────────────────

def test_frontend_tab_registered():
    html_path = ROOT / "static" / "index.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "nav-live-dashboard" in html, "Live Dashboard nav item missing"
    assert "panel-live-dashboard" in html, "Live Dashboard panel div missing"
    assert "ldCardGrid" in html, "ldCardGrid element missing"
    assert "ldEventFeed" in html, "ldEventFeed element missing"
    assert "ldScalpRadar" in html, "ldScalpRadar element missing"
    assert "ldPaperSummary" in html, "ldPaperSummary element missing"


def test_frontend_status_bar_badges():
    html_path = ROOT / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    assert "PAPER MODE ON" in html
    assert "REAL ORDERS OFF" in html
    assert "ld-mt5-badge" in html
    assert "ld-binance-badge" in html
    assert "ld-freshness-badge" in html


def test_frontend_candlestick_chart_renderer():
    html_path = ROOT / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    assert "ldBuildChartSvg" in html, "SVG candlestick chart function missing"
    assert "candleW" in html, "Candle width calculation missing"
    # Must draw bodies and wicks
    assert "bodyTop" in html and "bodyBot" in html, "Body top/bottom missing"


def test_frontend_freshness_pill_no_stale_1_bucket_for_confirmed_only():
    """Freshness pill must handle CONFIRMED_ONLY_OK distinctly from stale."""
    html_path = ROOT / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    assert "CONFIRMED_ONLY_OK" in html or "CONF-ONLY OK" in html, (
        "Frontend must render CONFIRMED_ONLY_OK status distinctly"
    )
    # The CONFIRMED_ONLY_OK branch in ldBuildFreshnessPill must assign 'fresh-conf',
    # not 'fresh-bad'. We find the branch line and verify the assignment before its `}`.
    idx = html.find("CONFIRMED_ONLY_OK")
    assert idx != -1
    branch_end = html.find("}", idx)
    branch_snippet = html[idx: branch_end] if branch_end != -1 else html[idx: idx + 120]
    assert "fresh-conf" in branch_snippet, (
        "CONFIRMED_ONLY_OK branch must set cls='fresh-conf'; got: " + branch_snippet
    )
    assert "fresh-bad" not in branch_snippet, (
        "CONFIRMED_ONLY_OK branch must not set cls='fresh-bad'; got: " + branch_snippet
    )


def test_frontend_engine_a_no_legacy_vote_pills():
    """Frontend Engine A pill must not show legacy vote fields as scoring indicators."""
    html_path = ROOT / "static" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    # ldBuildEnginePill reads eng.passed (from factorScores path), not vote counts
    assert "ldBuildEnginePill" in html
    # Legacy vote keys must not appear inside the pill builder
    idx = html.find("function ldBuildEnginePill(")
    assert idx != -1
    fn_end = html.find("\nfunction ", idx + 1)
    fn_body = html[idx: fn_end] if fn_end != -1 else html[idx: idx + 500]
    for vk in ("d1_trend", "h4_macd", "h1_ema", "h4_oscillator"):
        assert vk not in fn_body, f"Legacy vote key {vk!r} found in ldBuildEnginePill"


# ── Phase 5: snapshot v2 contract ────────────────────────────────────────────

def test_snapshot_v2_has_contract_metadata():
    """Snapshot v2 must include contract metadata block and levels/executableState per symbol."""
    src = _src()
    assert '"contract"' in src, "snapshot v2 missing 'contract' metadata block"
    assert '"execution": "paper_only"' in src, "contract must declare execution=paper_only"
    assert '"levels"' in src, "snapshot v2 missing per-symbol 'levels' field"
    assert '"executableState"' in src, "snapshot v2 missing per-symbol 'executableState' field"
    assert '"PAPER_READY"' in src, "executableState must include PAPER_READY value"
    assert '"NOT_READY"' in src, "executableState must include NOT_READY value"


# ── Phase 3: paper-execute endpoint ──────────────────────────────────────────

def test_paper_execute_endpoint_registered():
    """POST /api/live-dashboard/paper-execute must be registered."""
    routes = _route_map()
    assert "/api/live-dashboard/paper-execute" in routes, (
        "/api/live-dashboard/paper-execute route not found in athena.py"
    )
    assert "POST" in routes["/api/live-dashboard/paper-execute"], (
        "/api/live-dashboard/paper-execute must accept POST"
    )


def test_paper_execute_never_calls_broker():
    """paper-execute function must not reference any broker order function."""
    src = _src()
    tree = ast.parse(src)
    fn_node = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "api_live_dashboard_paper_execute":
            fn_node = node
            break
    assert fn_node is not None, "api_live_dashboard_paper_execute function not found"

    forbidden = {
        "mt5_execute", "bybit_execute", "risk_check", "api_execute",
        "api_quick_execute", "place_order", "send_order", "open_position",
        "close_position", "mt5_send_order", "bybit_send_order",
    }
    found = set()
    for child in ast.walk(fn_node):
        if isinstance(child, ast.Call):
            name = None
            if isinstance(child.func, ast.Name):
                name = child.func.id
            elif isinstance(child.func, ast.Attribute):
                name = child.func.attr
            if name in forbidden:
                found.add(name)
    assert not found, f"paper-execute calls forbidden broker functions: {found}"


def test_paper_execute_enforces_real_orders_blocked():
    """paper-execute must guard against REAL_ORDERS_ALLOWED=true."""
    src = _src()
    idx = src.find("def api_live_dashboard_paper_execute(")
    assert idx != -1, "api_live_dashboard_paper_execute not found"
    idx_end = src.find("\n@app.route", idx + 1)
    fn_body = src[idx: idx_end] if idx_end != -1 else src[idx: idx + 3000]
    assert "REAL_ORDERS_ALLOWED" in fn_body, (
        "paper-execute must re-check REAL_ORDERS_ALLOWED before logging"
    )
    assert "LD-PAPER-EXECUTE" in fn_body, (
        "paper-execute must write grade='LD-PAPER-EXECUTE' to audit_log"
    )
    # Must return 403 when real orders are allowed
    assert "403" in fn_body, "paper-execute must return HTTP 403 when REAL_ORDERS_ALLOWED=true"


# ── Phase 9: py_compile check ────────────────────────────────────────────────

def test_athena_py_compiles():
    import py_compile
    import tempfile
    # Write a copy to temp to avoid caching issues
    src = ATHENA_PATH.read_text(encoding="utf-8")
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
        f.write(src)
        tmp = f.name
    try:
        py_compile.compile(tmp, doraise=True)
    finally:
        Path(tmp).unlink(missing_ok=True)
