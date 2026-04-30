"""MT5 D1 broker session-ahead tail trim (UTC D1 grid vs session-stamped forming daily)."""

from datetime import datetime

import pytest

from athena_app.services.data_freshness import check_live_candle_consistency
from athena_app.services.market_state import (
    candle_freshness_diagnostic,
    trim_mt5_d1_broker_session_ahead_tail,
)


def _epoch(iso_text: str) -> float:
    return float(
        datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp()
    )


def _candle(iso_text: str) -> dict:
    return {
        "time": iso_text,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
    }


def test_trim_mt5_d1_removes_session_ahead_tail_when_expected_bucket_present() -> None:
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    t0 = _epoch("2026-04-29T21:30:00Z")
    series = [
        _candle("2026-04-24T00:00:00Z"),
        _candle("2026-04-27T00:00:00Z"),
        _candle("2026-04-28T00:00:00Z"),
        _candle("2026-04-29T00:00:00Z"),
        _candle("2026-04-30T00:00:00Z"),
    ]
    out, trimmed = trim_mt5_d1_broker_session_ahead_tail(pair, "D1", series, time_now=t0)
    assert trimmed is True
    assert len(out) == len(series) - 1
    assert out[-1]["time"] == "2026-04-29T00:00:00Z"


def test_trim_mt5_d1_noop_when_expected_bucket_missing_from_body() -> None:
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    t0 = _epoch("2026-04-29T21:30:00Z")
    series = [
        _candle("2026-04-26T00:00:00Z"),
        _candle("2026-04-27T00:00:00Z"),
        _candle("2026-04-30T00:00:00Z"),
    ]
    out, trimmed = trim_mt5_d1_broker_session_ahead_tail(pair, "D1", series, time_now=t0)
    assert trimmed is False
    assert out == series


def test_trim_mt5_d1_noop_non_mt5() -> None:
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "eodhd"}
    t0 = _epoch("2026-04-29T21:30:00Z")
    series = [
        _candle("2026-04-29T00:00:00Z"),
        _candle("2026-04-30T00:00:00Z"),
    ]
    out, trimmed = trim_mt5_d1_broker_session_ahead_tail(pair, "D1", series, time_now=t0)
    assert trimmed is False
    assert out == series


def test_candle_freshness_diagnostic_fresh_after_session_ahead_trim() -> None:
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    t0 = _epoch("2026-04-29T21:30:00Z")
    series = [
        _candle("2026-04-28T00:00:00Z"),
        _candle("2026-04-29T00:00:00Z"),
        _candle("2026-04-30T00:00:00Z"),
    ]
    diag = candle_freshness_diagnostic(pair, "D1", series, time_now=t0)
    assert diag["stalenessSeverity"] == "fresh"
    assert diag.get("mt5D1SessionAheadTrimmed") is True
    assert diag["hasCurrentBucket"] is True


def test_check_live_candle_consistency_not_error_stale_multi_bucket_after_trim() -> None:
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    t0 = _epoch("2026-04-29T21:30:00Z")
    series = [
        _candle("2026-04-28T00:00:00Z"),
        _candle("2026-04-29T00:00:00Z"),
        _candle("2026-04-30T00:00:00Z"),
    ]
    r = check_live_candle_consistency(
        pair,
        "D1",
        {
            "raw_provider": series,
            "engine_a": series,
            "engine_b": series,
            "scanner": series,
            "compare": series,
        },
        time_now=t0,
    )
    assert r["status"] != "ERROR_STALE_MULTI_BUCKET", r
