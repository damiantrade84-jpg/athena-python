"""Point-in-time funding/OI helpers for crypto backtests (mocked HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import data_feeds


@pytest.fixture(autouse=True)
def _clear_caches():
    data_feeds.clear_derivative_history_caches()
    data_feeds._oi_cache.clear()
    yield
    data_feeds.clear_derivative_history_caches()
    data_feeds._oi_cache.clear()


def test_point_in_time_funding_latest_at_or_before():
    rows = [
        {"ts_ms": 1000, "rate": 0.01},
        {"ts_ms": 2000, "rate": 0.02},
        {"ts_ms": 3000, "rate": 0.03},
    ]
    assert data_feeds.point_in_time_funding_rate(rows, 1500) == pytest.approx(0.01)
    assert data_feeds.point_in_time_funding_rate(rows, 2000) == pytest.approx(0.02)
    assert data_feeds.point_in_time_funding_rate(rows, 500) is None


def test_point_in_time_funding_no_future_leakage():
    rows = [{"ts_ms": 5000, "rate": 0.05}]
    assert data_feeds.point_in_time_funding_rate(rows, 2000) is None


def test_oi_data_for_divergence_uses_latest_at_or_before():
    oi_rows = [
        {"ts_ms": 1000, "oi": 100.0},
        {"ts_ms": 2000, "oi": 110.0},
        {"ts_ms": 3000, "oi": 121.0},
    ]
    d = data_feeds.build_oi_data_for_divergence(oi_rows, bar_ms=2500, prev_bar_ms=1500)
    assert d is not None
    assert d["oiChange"] == pytest.approx(10.0)
    assert d["oi"] == pytest.approx(110.0)


def test_oi_data_none_when_no_record_before_bar():
    oi_rows = [{"ts_ms": 5000, "oi": 1.0}]
    assert data_feeds.build_oi_data_for_divergence(oi_rows, bar_ms=2000, prev_bar_ms=1000) is None


def test_oi_data_none_when_prev_same_snapshot():
    oi_rows = [{"ts_ms": 1000, "oi": 100.0}]
    assert data_feeds.build_oi_data_for_divergence(oi_rows, bar_ms=1500, prev_bar_ms=500) is None


def test_bybit_funding_history_cache_reuse():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": "0.01",
                    "fundingRateTimestamp": "1000",
                }
            ]
        },
    }
    with patch.object(data_feeds, "http_requests") as mock_http:
        mock_http.get.return_value = mock_resp
        a = data_feeds._fetch_bybit_funding_history(
            "BTCUSDT", start_ms=0, end_ms=5000, limit=200
        )
        b = data_feeds._fetch_bybit_funding_history(
            "BTCUSDT", start_ms=0, end_ms=5000, limit=200
        )
        assert not a.get("error")
        assert a["list"]
        assert a == b
        assert mock_http.get.call_count == 1


def test_bybit_klines_are_normalized_and_sorted():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "retCode": 0,
        "result": {
            "list": [
                ["2000", "101", "103", "100", "102", "11", "1122"],
                ["1000", "100", "102", "99", "101", "10", "1010"],
            ]
        },
    }
    with patch.object(data_feeds, "http_requests") as mock_http:
        mock_http.get.return_value = mock_resp
        candles = data_feeds._fetch_bybit_klines("BTCUSDT", "H4", 2)

    assert [c["open_time"] for c in candles] == [1000, 2000]
    assert candles[0]["open"] == pytest.approx(100.0)
    assert candles[1]["vol"] == pytest.approx(11.0)


def test_bybit_open_interest_current_payload_uses_bybit_ticker():
    oi_resp = MagicMock()
    oi_resp.status_code = 200
    oi_resp.json.return_value = {
        "retCode": 0,
        "result": {
            "list": [
                {"timestamp": "2000", "openInterest": "110"},
                {"timestamp": "1000", "openInterest": "100"},
            ]
        },
    }
    ticker_resp = MagicMock()
    ticker_resp.status_code = 200
    ticker_resp.json.return_value = {
        "retCode": 0,
        "result": {"list": [{"lastPrice": "50000"}]},
    }

    with patch.object(data_feeds, "http_requests") as mock_http:
        mock_http.get.side_effect = [oi_resp, ticker_resp]
        out = data_feeds._fetch_bybit_open_interest("BTCUSDT")

    assert out["error"] is False
    assert out["source"] == "bybit"
    assert out["oi"] == pytest.approx(110.0)
    assert out["oiChange"] == pytest.approx(10.0)
    assert out["price"] == pytest.approx(50000.0)
