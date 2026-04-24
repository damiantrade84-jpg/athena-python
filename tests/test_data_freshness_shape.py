"""Tests for data freshness gate shape validation.

Validates that the diagnostic row shape emitted by the API/CLI matches
the shape expected by risk_engine's evaluate_execution_data_freshness.
"""

import pytest
from athena_app.services.data_freshness import (
    build_live_feed_diagnostic,
    evaluate_execution_data_freshness,
)


def test_diagnostic_row_has_required_fields():
    """Verify build_live_feed_diagnostic returns all required fields."""
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex", "source": "mt5"}
    candles = [
        {"time": 1714000000, "open": 1.08, "high": 1.09, "low": 1.07, "close": 1.085, "volume": 1000},
        {"time": 1714003600, "open": 1.085, "high": 1.095, "low": 1.08, "close": 1.09, "volume": 1000},
    ]
    
    diag = build_live_feed_diagnostic(pair, "H1", candles)
    
    # Required fields for risk_engine evaluation
    required_fields = [
        "symbol",
        "timeframe",
        "stalenessSeverity",
        "lastBarEpoch",
        "lastBarIso",
        "expectedCurrentBucketEpoch",
        "expectedCurrentBucketIso",
        "bucketLag",
        "hasCurrentBucket",
    ]
    
    for field in required_fields:
        assert field in diag, f"Missing required field: {field}"


def test_evaluate_execution_data_freshness_accepts_diagnostic_shape():
    """Verify evaluate_execution_data_freshness can process diagnostic rows."""
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex", "source": "mt5"}
    candles = [
        {"time": 1714000000, "open": 1.08, "high": 1.09, "low": 1.07, "close": 1.085, "volume": 1000},
    ]
    
    diag = build_live_feed_diagnostic(pair, "H1", candles)
    
    # Simulate signal structure with candleFreshness metadata
    signal = {
        "candleFreshness": {
            "H1": diag,
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": [
                "missing_current_bucket",
                "stale_1_bucket",
                "stale_multi_bucket",
                "error_path_mismatch",
                "error_offset_mismatch",
            ],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    # Should return expected structure
    assert "allowed" in result
    assert "blocked" in result
    assert "warnings" in result
    assert "reason" in result


def test_stale_1_bucket_blocks_execution_when_gate_enabled():
    """Verify stale_1_bucket on H4 blocks execution when BLOCK_EXECUTION_ON_STALE is true."""
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex", "source": "mt5"}
    
    # Create a diagnostic with stalenessSeverity = "stale_1_bucket"
    candles = []  # Empty to trigger staleness
    diag = build_live_feed_diagnostic(pair, "H4", candles)
    # Manually set staleness severity for test
    diag["stalenessSeverity"] = "stale_1_bucket"
    
    signal = {
        "candleFreshness": {
            "H4": diag,
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["stale_1_bucket"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    assert result["allowed"] is False
    assert "STALE_DATA_BLOCK" in result["reason"]


def test_fresh_h4_allows_execution_when_gate_enabled():
    """Verify fresh H4 allows execution if all other risk gates pass."""
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex", "source": "mt5"}
    
    # Create a diagnostic with OK staleness
    candles = [
        {"time": 1714000000, "open": 1.08, "high": 1.09, "low": 1.07, "close": 1.085, "volume": 1000},
    ]
    diag = build_live_feed_diagnostic(pair, "H4", candles)
    diag["stalenessSeverity"] = "ok"
    
    signal = {
        "candleFreshness": {
            "H4": diag,
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["stale_1_bucket"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    assert result["allowed"] is True


def test_error_path_mismatch_blocks_execution():
    """Verify ERROR_PATH_MISMATCH blocks execution."""
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex", "source": "mt5"}
    
    signal = {
        "candleConsistency": {
            "H4": {
                "status": "ERROR_PATH_MISMATCH",
                "reason": "path epochs differ",
            }
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["error_path_mismatch"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    assert result["allowed"] is False
    assert "STALE_DATA_BLOCK" in result["reason"]


def test_error_offset_mismatch_blocks_execution():
    """Verify ERROR_OFFSET_MISMATCH blocks execution."""
    pair = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex", "source": "mt5"}
    
    signal = {
        "candleConsistency": {
            "H4": {
                "status": "ERROR_OFFSET_MISMATCH",
                "reason": "offset mismatch",
            }
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["error_offset_mismatch"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    assert result["allowed"] is False
    assert "STALE_DATA_BLOCK" in result["reason"]
