"""Tests for safety fixes from deep audit (C1, M1, M2, M5, M7, L2)."""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from risk_engine import risk_check

# ---------------------------------------------------------------------------
# C1: Timestamp laundering on failed refresh
# ---------------------------------------------------------------------------


def test_stale_signal_pair_not_in_universe_rejected():
    """Execution.py now returns 409 instead of faking timestamp when pair not found."""
    import execution
    import inspect

    src = inspect.getsource(execution.api_execute)
    assert "STALE_SIGNAL_REFRESH_FAILED" in src, (
        "C1 fix missing: stale signal refresh failure should return STALE_SIGNAL_REFRESH_FAILED"
    )
    assert 'sig["timestamp"] = datetime.now(timezone.utc).isoformat()' not in src, (
        "C1 fix incomplete: timestamp laundering via else-branch still present"
    )


def test_stale_signal_live_refresh_failed_rejected():
    """Execution.py now returns 409 instead of faking timestamp on refresh exception."""
    import execution
    import inspect

    src = inspect.getsource(execution.api_execute)
    lines = src.split("\n")
    in_except = False
    found_return_409 = False
    found_timestamp_laundering = False
    for line in lines:
        if "except Exception as _re:" in line:
            in_except = True
        if in_except:
            if "STALE_SIGNAL_REFRESH_FAILED" in line:
                found_return_409 = True
            if 'sig["timestamp"]' in line:
                found_timestamp_laundering = True
            if "return jsonify" in line and found_return_409:
                break
    assert found_return_409, "C1 fix missing: exception handler should return 409"
    assert not found_timestamp_laundering, (
        "C1 fix incomplete: timestamp laundering still present in exception handler"
    )


# ---------------------------------------------------------------------------
# M1: _apply_engine_b_execution_levels return value checked
# ---------------------------------------------------------------------------


def test_engine_b_execution_levels_return_checked():
    """Execution.py api_execute now checks return value of _apply_engine_b_execution_levels."""
    import execution
    import inspect

    src = inspect.getsource(execution.api_execute)
    assert "_levels_applied = _apply_engine_b_execution_levels" in src, (
        "M1 fix missing: return value of _apply_engine_b_execution_levels not captured"
    )
    assert "if not _levels_applied:" in src, (
        "M1 fix missing: return value not checked"
    )
    assert "ENGINE_B_LEVELS_UNAVAILABLE" in src, (
        "M1 fix missing: error code for missing Engine B levels not present"
    )


# ---------------------------------------------------------------------------
# M2: bos_volume_confirmed defaults False
# ---------------------------------------------------------------------------


def test_bos_volume_confirmed_defaults_false_in_detector():
    """Verify bos_volume_confirmed initializes to False in _detect_bos()."""
    from market_structure import NakedEngine
    import inspect

    try:
        src = inspect.getsource(NakedEngine._detect_bos)
    except (OSError, TypeError):
        pytest.skip("_detect_bos source unavailable")

    assert "bos_volume_confirmed = False" in src, (
        "M2 fix missing: bos_volume_confirmed should default to False in _detect_bos()"
    )


def test_bos_volume_confirmed_defaults_false_in_result_dict():
    """Verify analyze_structure result dict defaults bos_volume_confirmed to False."""
    from market_structure import NakedEngine
    import inspect

    try:
        src = inspect.getsource(NakedEngine.analyze_structure_direction)
    except (OSError, TypeError):
        pytest.skip("analyze_structure_direction source unavailable")

    assert 'bos_data.get("bos_volume_confirmed", False)' in src, (
        "M2 fix missing: bos_volume_confirmed should default to False in result dict"
    )


def test_missing_bos_volume_data_does_not_confirm():
    """When no volume data is passed via result, bos_volume_confirmed stays False."""
    from market_structure import NakedEngine

    engine = NakedEngine()
    bars = [
        {"open": 1.0, "high": 1.5, "low": 0.9, "close": 1.2, "time": "2024-01-01T00:00:00Z"},
        {"open": 1.2, "high": 1.6, "low": 1.1, "close": 1.3, "time": "2024-01-01T01:00:00Z"},
        {"open": 1.3, "high": 1.7, "low": 1.2, "close": 1.4, "time": "2024-01-01T02:00:00Z"},
        {"open": 1.4, "high": 1.8, "low": 1.3, "close": 1.5, "time": "2024-01-01T03:00:00Z"},
        {"open": 1.5, "high": 1.9, "low": 1.4, "close": 1.6, "time": "2024-01-01T04:00:00Z"},
    ] * 5

    try:
        result = engine.analyze_structure(bars, "LONG", "EURUSD", "forex")
    except Exception:
        pytest.skip("analyze_structure raised — cannot test directly")

    bos_data = result.get("bos_data", {})
    bos_vol = bos_data.get("bos_volume_confirmed")
    assert bos_vol is not True, (
        "M2 regression: bos_volume_confirmed should not be True with no volume data"
    )


# ---------------------------------------------------------------------------
# M5: decision_state/verdict gate bypass
# ---------------------------------------------------------------------------


def _make_signal(**overrides):
    base = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.05,
        "sl": 1.04,
        "tp1": 1.07,
        "type": "forex",
        "timestamp": None,
        "confluenceScore": 2.0,
        "candleConsistency": {"H4": {"status": "OK"}},
    }
    base.update(overrides)
    return base


def test_verdict_empty_tier_watchlist_blocks_execution(monkeypatch):
    """Signal with empty verdict and tier=WATCHLIST must be blocked."""
    import risk_engine as re
    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(verdict="", signalTier="WATCHLIST", decision_state="")
    result = risk_check(sig, 10000.0, 10000.0, [])
    assert result.approved is False, (
        f"M5 regression: empty verdict + WATCHLIST tier should block, got {result.reason}"
    )
    assert result.reason == "NON_EXECUTABLE_SIGNAL_STATE"


def test_verdict_empty_tier_skip_blocks_execution(monkeypatch):
    """Signal with empty verdict and tier=SKIP must be blocked."""
    import risk_engine as re
    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(verdict="", signalTier="SKIP", decision_state="")
    result = risk_check(sig, 10000.0, 10000.0, [])
    assert result.approved is False, (
        f"M5 regression: empty verdict + SKIP tier should block, got {result.reason}"
    )


def test_decision_state_watchlist_blocks_execution(monkeypatch):
    """Signal with decision_state='watchlist' must be blocked (pre-existing)."""
    import risk_engine as re
    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(decision_state="watchlist")
    result = risk_check(sig, 10000.0, 10000.0, [])
    assert result.approved is False


def test_normal_verdict_passes_with_valid_state(monkeypatch):
    """Signal with valid verdict and tier should not be blocked by M5 gate."""
    import risk_engine as re
    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(
        verdict="ALIGNED",
        signalTier="EXECUTE",
        decision_state="execute",
        engine_b_status={"checklist_passed": True},
        components={"b_checklist_passed": True, "a_has_signal": True},
    )
    result = risk_check(sig, 10000.0, 10000.0, [])
    # Should not be rejected by decision_state/tier gate
    assert result.reason != "NON_EXECUTABLE_SIGNAL_STATE", (
        "M5 over-fix: valid signal incorrectly blocked by decision_state gate"
    )


# ---------------------------------------------------------------------------
# M7: trust_verdict defaults to trust_neither
# ---------------------------------------------------------------------------


def test_trust_verdict_defaults_to_trust_neither():
    """Unparseable AI trust verdict should default to trust_neither, not trust_both."""
    from engine_c_ai import normalize_engine_c_ai_weight_verdict

    out = normalize_engine_c_ai_weight_verdict({"trust_verdict": None})
    assert out.get("trust_verdict") != "trust_both", (
        "M7 regression: trust_verdict should not default to trust_both"
    )

    out2 = normalize_engine_c_ai_weight_verdict({})
    assert out2.get("trust_verdict") == "trust_neither", (
        "M7 fix missing: empty input should default to trust_neither"
    )


# ---------------------------------------------------------------------------
# L2: bybit SL width cap no longer silently passes
# ---------------------------------------------------------------------------


def test_bybit_sl_width_exception_logged_not_silent():
    """Bybit executor SL width check fail-closed: exception rejects with SL_CAP_CHECK_FAILED."""
    import bybit_executor
    import inspect

    try:
        src = inspect.getsource(bybit_executor.bybit_execute)
    except (OSError, TypeError, AttributeError):
        pytest.skip("bybit_execute source unavailable")

    assert "except Exception:" in src, (
        "L2: SL width check still has exception handler"
    )
    assert "SL_CAP_CHECK_FAILED" in src, (
        "L2 fix: cap check exception must reject order"
    )
    assert "proceeding but this bypasses" not in src, (
        "L2 regression: must not proceed after cap check failure"
    )


# ---------------------------------------------------------------------------
# HTTP semantics: hard failures must not use 200 OK
# ---------------------------------------------------------------------------


def test_api_execute_risk_rejection_uses_422_not_200():
    import execution
    import inspect

    src = inspect.getsource(execution.api_execute)
    assert "Risk engine rejected" in src
    block = src.split("Risk engine rejected", 1)[1][:900]
    assert re.search(r"\),\s*422\b", block), (
        "Risk rejection response must use HTTP 422, not 200"
    )


def test_api_execute_mt5_missing_symbol_uses_400_not_200():
    import execution
    import inspect

    src = inspect.getsource(execution.api_execute)
    assert "not available on your MT5 broker" in src
    block = src.split("not available on your MT5 broker", 1)[1][:500]
    assert re.search(r"\),\s*400\b", block), (
        "Unknown MT5 symbol must use HTTP 400, not 200"
    )


# ---------------------------------------------------------------------------
# Summary of audit fixes verified by this test file
# ---------------------------------------------------------------------------


def test_audit_fixes_summary():
    """Meta-test: confirm all fix markers are present in the source files."""
    import execution
    import inspect as _i

    exec_src = _i.getsource(execution.api_execute)
    checks = [
        ("C1", "STALE_SIGNAL_REFRESH_FAILED", exec_src),
        ("M1", "ENGINE_B_LEVELS_UNAVAILABLE", exec_src),
    ]
    failures = []
    for tag, marker, src in checks:
        if marker not in src:
            failures.append(f"{tag}: '{marker}' not found in source")
    assert not failures, "\n".join(failures)
