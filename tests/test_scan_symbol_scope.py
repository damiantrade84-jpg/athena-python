"""Engine A explicit symbol-scope contract for run_full_scan.

The Live Cockpit sends one operator selection to both engines, so Engine A's
``symbols`` scope must resolve the same pairs as Engine B's existing
``/api/scan-naked`` scope. Pure-path: parses scanner.py source for the wiring
assertions rather than importing the monolith runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from athena_app.services.scan_backtest_service import handle_scan_request
from scanner import _normalize_scan_symbol, _parse_scan_symbol_scope

ROOT = Path(__file__).resolve().parents[1]


# --- Scope parsing -----------------------------------------------------------


def test_symbol_normalisation_is_case_and_separator_insensitive():
    assert _normalize_scan_symbol("EUR/USD") == "EURUSD"
    assert _normalize_scan_symbol("eur/usd") == "EURUSD"
    assert _normalize_scan_symbol("EUR_USD") == "EURUSD"
    assert _normalize_scan_symbol("  eurusd  ") == "EURUSD"
    assert _normalize_scan_symbol("XAU/USD") == _normalize_scan_symbol("xauusd")
    assert _normalize_scan_symbol("BRENT OIL") == "BRENTOIL"


def test_normalisation_matches_engine_b_scope_semantics():
    """Engine B's /api/scan-naked uses the identical transform - keep them equal."""
    naked = (ROOT / "athena.py").read_text(encoding="utf-8", errors="replace")
    body = naked.split("def api_scan_naked(", 1)[1][:2000]
    assert '.upper().replace("/", "").replace("_", "").replace(" ", "").strip()' in body


@pytest.mark.parametrize("empty", [None, "", [], {}, 0, "   ", ",,", ";"])
def test_empty_scope_means_whole_universe(empty):
    # None is the signal for "no scope" - it must never collapse to an empty set,
    # which would silently scan nothing.
    assert _parse_scan_symbol_scope(empty) is None


def test_scope_accepts_comma_and_semicolon_strings():
    assert _parse_scan_symbol_scope("EUR/USD,GBP/USD") == {"EURUSD", "GBPUSD"}
    assert _parse_scan_symbol_scope("EUR/USD;GBP/USD") == {"EURUSD", "GBPUSD"}
    assert _parse_scan_symbol_scope(" EUR/USD , gbp/usd ") == {"EURUSD", "GBPUSD"}


def test_scope_accepts_lists_and_drops_blanks():
    assert _parse_scan_symbol_scope(["EUR/USD", "", "  ", "BTCUSDT"]) == {"EURUSD", "BTCUSDT"}


def test_scope_deduplicates_equivalent_spellings():
    assert _parse_scan_symbol_scope(["EUR/USD", "eurusd", "EUR_USD"]) == {"EURUSD"}


# --- Service pass-through ----------------------------------------------------


def test_handle_scan_request_forwards_the_symbol_scope():
    captured = {}

    def fake_run_full_scan(**kwargs):
        captured.update(kwargs)
        return {"signals": []}

    handle_scan_request(
        {"style": "swing", "symbols": "EUR/USD,GBP/USD", "asset_class": "forex"},
        run_full_scan=fake_run_full_scan,
    )
    assert captured["symbols"] == "EUR/USD,GBP/USD"
    assert captured["style"] == "swing"
    assert captured["asset_class"] == "forex"


def test_handle_scan_request_without_symbols_is_unchanged():
    captured = {}

    def fake_run_full_scan(**kwargs):
        captured.update(kwargs)
        return {"signals": []}

    handle_scan_request({"style": "auto"}, run_full_scan=fake_run_full_scan)
    assert captured["symbols"] is None          # whole-universe behaviour preserved
    assert captured["asset_class"] is None
    assert captured["refresh_market_data"] is False


# --- run_full_scan wiring ----------------------------------------------------


def _run_full_scan_body() -> str:
    src = (ROOT / "scanner.py").read_text(encoding="utf-8", errors="replace")
    return src.split("def run_full_scan(", 1)[1]


def test_run_full_scan_accepts_a_symbols_argument():
    import inspect

    from scanner import run_full_scan

    params = inspect.signature(run_full_scan).parameters
    assert "symbols" in params
    assert params["symbols"].default is None  # opt-in; existing callers unaffected


def test_symbol_scope_takes_precedence_over_asset_class():
    body = _run_full_scan_body()
    assert "if _symbol_scope is not None:" in body
    # The asset-class filter must be the *else* branch, not an extra filter, so an
    # explicit selection is never silently narrowed by a stale class.
    assert "elif _ac:" in body


def test_explicit_scope_skips_the_closed_market_prefilter():
    body = _run_full_scan_body()
    scope_branch = body.split("if _symbol_scope is not None:")[2]
    assert "analysis_pairs, session_skipped = list(active_pairs), []" in scope_branch
    # The unscoped path still prefilters closed exchanges.
    assert "_split_pairs_on_session_phase(" in body


def test_scope_matches_on_both_display_and_symbol():
    body = _run_full_scan_body()
    assert '_normalize_scan_symbol(p.get("display")) in _symbol_scope' in body
    assert '_normalize_scan_symbol(p.get("symbol")) in _symbol_scope' in body


def test_scope_is_applied_after_the_disabled_pair_filter():
    """A disabled pair must stay disabled even when explicitly requested."""
    body = _run_full_scan_body()
    disabled_idx = body.index('p["display"] not in r.disabled_pairs')
    scope_idx = body.index("if _symbol_scope is not None:", disabled_idx)
    assert disabled_idx < scope_idx
    # ...and the enabled flag is still honoured downstream.
    assert 'active_pairs = [p for p in candidate_pairs if p.get("enabled", True)]' in body
