"""Engine B scan freshness gate regression tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scanner import _engine_b_scan_freshness_stale_tfs


def test_engine_b_scan_freshness_gate_disabled_returns_empty():
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    stale, diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}],
        [],
        [],
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": False},
    )
    assert stale == []
    assert diag == {}


@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_engine_b_scan_freshness_gate_blocks_stale_multi_bucket(mock_diag):
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_multi_bucket" if tf == "H4" else "fresh",
        "bucketLag": 3,
    }
    pair = {"display": "AAPL", "type": "stock", "source": "eodhd"}
    stale, diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
    )
    assert stale == ["H4:stale_multi_bucket"]
    assert "H4" in diag


@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_engine_b_scan_freshness_gate_allows_fresh(mock_diag):
    mock_diag.return_value = {"stalenessSeverity": "fresh", "bucketLag": 0}
    pair = {"display": "BTC/USDT", "type": "crypto", "source": "binance"}
    stale, _diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
    )
    assert stale == []
