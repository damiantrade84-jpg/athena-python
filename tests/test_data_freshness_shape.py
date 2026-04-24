"""Tests for data freshness gate shape validation.

Validates that the diagnostic row shape emitted by the API/CLI matches
the shape expected by risk_engine's evaluate_execution_data_freshness.
"""

import pytest
from athena_app.services.data_freshness import (
    build_live_feed_diagnostic,
    evaluate_execution_data_freshness,
    check_live_candle_consistency,
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


def test_confirmed_only_ok_allows_execution():
    """Verify CONFIRMED_ONLY_OK does not block execution (intentional confirmed-only policy)."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    signal = {
        "candleConsistency": {
            "H1": {
                "status": "CONFIRMED_ONLY_OK",
                "reason": "engine paths use confirmed-only policy (intentional one-bucket lag behind raw forming)",
            }
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["warning_one_bucket_lag"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    # CONFIRMED_ONLY_OK should not block
    assert result["allowed"] is True
    assert len(result["blocked"]) == 0


def test_confirmed_only_ok_with_raw_current():
    """Verify CONFIRMED_ONLY_OK classification when raw has current bucket and engines use confirmed-only."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    # Simulate raw provider with current forming candle at 16:00:00Z
    current_time = 1777046400  # 2026-04-24T16:00:00Z
    candles = [
        {"time": "2026-04-24T15:00:00Z", "open": 1.08, "high": 1.09, "low": 1.07, "close": 1.085, "volume": 1000},
        {"time": "2026-04-24T16:00:00Z", "open": 1.085, "high": 1.095, "low": 1.08, "close": 1.09, "volume": 1000},
    ]
    
    result = check_live_candle_consistency(
        pair,
        "H1",
        {
            "raw_provider": candles,
            "engine_b": candles[:-1],  # Confirmed-only (15:00:00Z)
            "scanner": candles[:-1],  # Confirmed-only (15:00:00Z)
        },
        time_now=current_time,
    )
    
    assert result["status"] == "CONFIRMED_ONLY_OK"
    assert "confirmed-only policy" in result["reason"]
    assert result["enginePolicies"]["engine_b"]["policy"] == "CONFIRMED_ONLY"
    assert result["enginePolicies"]["scanner"]["policy"] == "CONFIRMED_ONLY"


def test_true_one_bucket_lag_when_confirmed_only_but_stale():
    """Verify ERROR_STALE_MULTI_BUCKET when raw provider is missing current bucket."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    # Simulate raw provider WITHOUT current bucket (stale)
    current_time = 1777046400  # 2026-04-24T16:00:00Z
    candles = [
        {"time": "2026-04-24T14:00:00Z", "open": 1.08, "high": 1.09, "low": 1.07, "close": 1.085, "volume": 1000},
        {"time": "2026-04-24T15:00:00Z", "open": 1.085, "high": 1.095, "low": 1.08, "close": 1.09, "volume": 1000},
    ]
    
    result = check_live_candle_consistency(
        pair,
        "H1",
        {
            "raw_provider": candles,  # No current bucket (stale)
            "engine_b": candles[:-1],  # Even more stale (14:00:00Z)
            "scanner": candles[:-1],
        },
        time_now=current_time,
    )
    
    assert result["status"] == "ERROR_STALE_MULTI_BUCKET"  # Missing current bucket is an error


def test_gate_confirmed_only_ok_allows_execution():
    """Phase 2: CONFIRMED_ONLY_OK at top-level allows execution even if per-path shows stale_1_bucket."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    signal = {
        "candleConsistency": {
            "H1": {
                "status": "CONFIRMED_ONLY_OK",
                "reason": "engine paths use confirmed-only policy",
                "paths": {
                    "engine_b": {"stalenessSeverity": "stale_1_bucket"},  # Per-path shows lag
                    "scanner": {"stalenessSeverity": "stale_1_bucket"},
                }
            }
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["warning_one_bucket_lag", "stale_1_bucket"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    # CONFIRMED_ONLY_OK at top-level should allow even with per-path stale_1_bucket
    assert result["allowed"] is True
    assert len(result["blocked"]) == 0


def test_gate_warning_one_bucket_lag_blocks_execution():
    """Phase 2: WARNING_ONE_BUCKET_LAG at top-level blocks execution."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    signal = {
        "candleConsistency": {
            "H1": {
                "status": "WARNING_ONE_BUCKET_LAG",
                "reason": "one or more paths lag by one bucket",
            }
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["warning_one_bucket_lag"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    assert result["allowed"] is False
    assert "STALE_DATA_BLOCK" in result["reason"]


def test_gate_error_stale_multi_bucket_blocks_execution():
    """Phase 2: ERROR_STALE_MULTI_BUCKET blocks execution."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    signal = {
        "candleConsistency": {
            "H1": {
                "status": "ERROR_STALE_MULTI_BUCKET",
                "reason": "missing current bucket or lag multiple buckets",
            }
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["error_stale_multi_bucket"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    assert result["allowed"] is False
    assert "STALE_DATA_BLOCK" in result["reason"]


def test_gate_provider_error_blocks_execution():
    """Phase 2: provider_error blocks execution."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    signal = {
        "candleFreshness": {
            "H1": {
                "stalenessSeverity": "provider_error",
                "providerStatus": "error",
                "providerError": "Connection timeout",
            }
        }
    }
    
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["provider_error"],
        }
    }
    
    result = evaluate_execution_data_freshness(signal, config)
    
    assert result["allowed"] is False


def test_gate_error_path_mismatch_blocks_execution():
    """Phase 2: ERROR_PATH_MISMATCH blocks execution."""
    pair = {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}
    
    signal = {
        "candleConsistency": {
            "H1": {
                "status": "ERROR_PATH_MISMATCH",
                "reason": "path latest epochs differ",
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


def test_gate_error_offset_mismatch_blocks_execution():
    """Phase 2: ERROR_OFFSET_MISMATCH blocks execution."""
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
