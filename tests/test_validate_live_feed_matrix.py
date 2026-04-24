#!/usr/bin/env python3
"""Tests for validate_live_feed_matrix.py."""
import json
from pathlib import Path

import pytest

# Import the validation functions
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from validate_live_feed_matrix import validate_matrix, validate_row


def test_validate_row_passes_confirmed_only_ok():
    """Test that a row with CONFIRMED_ONLY_OK status passes."""
    row = {
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "providerStatus": "ok",
        "consistency": {
            "status": "CONFIRMED_ONLY_OK",
            "reason": "intentional confirmed-only policy",
            "enginePolicies": {
                "engine_a": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
            }
        }
    }
    errors = validate_row(row, "BTCUSDT", "H1")
    assert len(errors) == 0, f"Expected no errors, got: {errors}"


def test_validate_row_passes_ok():
    """Test that a row with OK status passes."""
    row = {
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "providerStatus": "ok",
        "consistency": {
            "status": "OK",
            "reason": "all aligned",
            "enginePolicies": {
                "engine_a": {"policy": "FORMING_ALLOWED", "policyStatus": "POLICY_OK"},
                "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
            }
        }
    }
    errors = validate_row(row, "BTCUSDT", "H1")
    assert len(errors) == 0, f"Expected no errors, got: {errors}"


def test_validate_row_fails_provider_error():
    """Test that provider_error status fails."""
    row = {
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "providerStatus": "error",
        "consistency": {
            "status": "provider_error",
            "reason": "provider disconnected",
            "enginePolicies": {}
        }
    }
    errors = validate_row(row, "BTCUSDT", "H1")
    assert len(errors) > 0
    assert any("providerStatus" in e for e in errors)
    assert any("provider_error" in e for e in errors)


def test_validate_row_fails_warning_one_bucket_lag():
    """Test that WARNING_ONE_BUCKET_LAG status fails (genuine stale)."""
    row = {
        "symbol": "EURUSD",
        "timeframe": "D1",
        "providerStatus": "ok",
        "consistency": {
            "status": "WARNING_ONE_BUCKET_LAG",
            "reason": "one or more paths lag by one bucket",
            "enginePolicies": {
                "engine_a": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_LAG"},
                "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_LAG"},
                "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_LAG"},
                "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_LAG"},
            }
        }
    }
    errors = validate_row(row, "EURUSD", "D1")
    assert len(errors) > 0
    assert any("WARNING_ONE_BUCKET_LAG" in e for e in errors)


def test_validate_row_fails_error_path_mismatch():
    """Test that ERROR_PATH_MISMATCH status fails."""
    row = {
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "providerStatus": "ok",
        "consistency": {
            "status": "ERROR_PATH_MISMATCH",
            "reason": "confirmed epochs differ",
            "enginePolicies": {}
        }
    }
    errors = validate_row(row, "BTCUSDT", "H1")
    assert len(errors) > 0
    assert any("ERROR_PATH_MISMATCH" in e for e in errors)


def test_validate_row_fails_policy_lag():
    """Test that POLICY_LAG fails."""
    row = {
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "providerStatus": "ok",
        "consistency": {
            "status": "CONFIRMED_ONLY_OK",
            "reason": "intentional",
            "enginePolicies": {
                "engine_a": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_LAG"},
                "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
            }
        }
    }
    errors = validate_row(row, "BTCUSDT", "H1")
    assert len(errors) > 0
    assert any("engine_a policyStatus" in e for e in errors)


def test_validate_matrix_passes_complete():
    """Test that a complete matrix with all symbols/timeframes passes."""
    data = {
        "diagnostics": []
    }
    
    for symbol in ["EURUSD", "GBPUSD", "BTCUSDT", "ETHUSDT"]:
        for tf in ["H1", "H4", "D1"]:
            row = {
                "symbol": symbol,
                "timeframe": tf,
                "providerStatus": "ok",
                "consistency": {
                    "status": "CONFIRMED_ONLY_OK",
                    "reason": "intentional",
                    "enginePolicies": {
                        "engine_a": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                    }
                }
            }
            data["diagnostics"].append(row)
    
    is_valid, errors = validate_matrix(data)
    assert is_valid, f"Expected valid, got errors: {errors}"


def test_validate_matrix_fails_missing_symbol():
    """Test that missing symbol fails."""
    data = {
        "diagnostics": []
    }
    
    for symbol in ["EURUSD", "GBPUSD", "BTCUSDT"]:  # Missing ETHUSDT
        for tf in ["H1", "H4", "D1"]:
            row = {
                "symbol": symbol,
                "timeframe": tf,
                "providerStatus": "ok",
                "consistency": {
                    "status": "CONFIRMED_ONLY_OK",
                    "enginePolicies": {
                        "engine_a": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                    }
                }
            }
            data["diagnostics"].append(row)
    
    is_valid, errors = validate_matrix(data)
    assert not is_valid
    assert any("ETHUSDT" in e for e in errors)


def test_validate_matrix_fails_missing_timeframe():
    """Test that missing timeframe fails."""
    data = {
        "diagnostics": []
    }
    
    for symbol in ["EURUSD", "GBPUSD", "BTCUSDT", "ETHUSDT"]:
        for tf in ["H1", "H4"]:  # Missing D1
            row = {
                "symbol": symbol,
                "timeframe": tf,
                "providerStatus": "ok",
                "consistency": {
                    "status": "CONFIRMED_ONLY_OK",
                    "enginePolicies": {
                        "engine_a": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                    }
                }
            }
            data["diagnostics"].append(row)
    
    is_valid, errors = validate_matrix(data)
    assert not is_valid
    assert any("D1" in e for e in errors)


def test_validate_matrix_handles_mt5_suffix():
    """Test that MT5 symbol suffix (=X) is handled correctly."""
    data = {
        "diagnostics": []
    }
    
    for symbol in ["EURUSD=X", "GBPUSD=X", "BTCUSDT", "ETHUSDT"]:
        for tf in ["H1", "H4", "D1"]:
            row = {
                "symbol": symbol,
                "timeframe": tf,
                "providerStatus": "ok",
                "consistency": {
                    "status": "CONFIRMED_ONLY_OK",
                    "enginePolicies": {
                        "engine_a": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "engine_b": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "scanner": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                        "compare": {"policy": "CONFIRMED_ONLY", "policyStatus": "POLICY_OK"},
                    }
                }
            }
            data["diagnostics"].append(row)
    
    is_valid, errors = validate_matrix(data)
    assert is_valid, f"Expected valid, got errors: {errors}"
