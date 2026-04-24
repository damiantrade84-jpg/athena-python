from datetime import datetime

from athena_app.services.data_freshness import (
    build_live_feed_diagnostic,
    check_live_candle_consistency,
    evaluate_execution_data_freshness,
)
from config import CONFIG


def _epoch(iso_text):
    return int(datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp())


def _candle(iso_text):
    return {
        "time": iso_text,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
    }


def _diag(tf, last_iso, expected_iso, *, offset=0.0, severity="fresh", lag=0):
    return {
        "timeframe": tf,
        "lastBarEpoch": _epoch(last_iso),
        "expectedCurrentBucketEpoch": _epoch(expected_iso),
        "bucketLag": lag,
        "hasCurrentBucket": lag == 0,
        "stalenessSeverity": severity,
        "confirmedCount": 10,
        "formingCount": 0,
        "usesOffset": bool(offset),
        "offsetHours": offset,
    }


def test_live_feed_diagnostic_includes_execution_safe_fields(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}

    diagnostic = build_live_feed_diagnostic(
        pair,
        "H4",
        [_candle("2026-04-24T01:00:00Z"), _candle("2026-04-24T05:00:00Z")],
        fetch_meta={"cacheBypass": True, "upstream": "mt5"},
        time_now=_epoch("2026-04-24T06:30:00Z"),
        fetch_duration_ms=2.3456,
    )

    assert diagnostic["symbol"] == "EURUSD"
    assert diagnostic["asset_type"] == "forex"
    assert diagnostic["timeframe"] == "H4"
    assert diagnostic["source"] == "mt5"
    assert diagnostic["candle_count"] == 2
    assert diagnostic["fetchDurationMs"] == 2.346
    assert diagnostic["providerStatus"] == "ok"
    assert diagnostic["providerError"] is None
    assert diagnostic["cacheBypassed"] is True
    assert diagnostic["isFromCache"] is False
    assert diagnostic["usesOffset"] is True


def test_candle_consistency_all_paths_aligned_ok(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    aligned = _diag("H4", "2026-04-24T05:00:00Z", "2026-04-24T05:00:00Z", offset=1.0)

    result = check_live_candle_consistency(
        pair,
        "H4",
        {
            "raw_provider": dict(aligned),
            "cache": dict(aligned),
            "market_state": dict(aligned),
            "engine_a": dict(aligned),
            "engine_b": dict(aligned),
            "scanner": dict(aligned),
        },
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert result["status"] == "OK"


def test_candle_consistency_cache_one_bucket_lag_warns(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}

    result = check_live_candle_consistency(
        pair,
        "H4",
        {
            "raw_provider": _diag("H4", "2026-04-24T05:00:00Z", "2026-04-24T05:00:00Z", offset=1.0),
            "cache": _diag(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
            "engine_a": _diag("H4", "2026-04-24T05:00:00Z", "2026-04-24T05:00:00Z", offset=1.0),
            "engine_b": _diag("H4", "2026-04-24T05:00:00Z", "2026-04-24T05:00:00Z", offset=1.0),
        },
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert result["status"] == "WARNING_ONE_BUCKET_LAG"


def test_candle_consistency_engine_a_b_latest_mismatch_errors(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}

    result = check_live_candle_consistency(
        pair,
        "H4",
        {
            "engine_a": _diag("H4", "2026-04-24T05:00:00Z", "2026-04-24T05:00:00Z", offset=1.0),
            "engine_b": _diag("H4", "2026-04-24T01:00:00Z", "2026-04-24T05:00:00Z", offset=1.0),
        },
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert result["status"] == "ERROR_PATH_MISMATCH"


def test_candle_consistency_forex_h4_utc_bucket_when_offset_expected_errors(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}

    result = check_live_candle_consistency(
        pair,
        "H4",
        {
            "market_state": _diag(
                "H4",
                "2026-04-24T04:00:00Z",
                "2026-04-24T04:00:00Z",
                offset=0.0,
            )
        },
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert result["status"] == "ERROR_OFFSET_MISMATCH"


def test_candle_consistency_crypto_h4_with_forex_offset_errors(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "crypto", "display": "BTC/USDT", "symbol": "BTCUSDT", "source": "binance"}

    result = check_live_candle_consistency(
        pair,
        "H4",
        {
            "market_state": _diag(
                "H4",
                "2026-04-24T05:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
            )
        },
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert result["status"] == "ERROR_OFFSET_MISMATCH"


def test_evaluate_execution_data_freshness_disabled_gate_allows_with_warning():
    signal = {"candleFetchMeta": {"H4": {"stalenessSeverity": "stale_1_bucket"}}}
    config = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": False,
            "BLOCK_TIMEFRAMES": ["H4"],
            "BLOCK_SEVERITIES": ["stale_1_bucket"],
        }
    }

    result = evaluate_execution_data_freshness(signal, config)

    assert result["allowed"] is True
    assert result["blocked"][0]["severity"] == "stale_1_bucket"
