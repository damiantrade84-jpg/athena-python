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


@patch("athena_app.services.data_freshness.d1_consumed_by_score_group")
@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_engine_b_scan_freshness_gate_skips_d1_when_group_does_not_consume_d1(
    mock_diag, mock_d1_consumed
):
    """B3: when d1_consumed_by_score_group returns False for (group, style),
    stale D1 must NOT block Engine B scoring. The diagnostic is still recorded."""
    mock_d1_consumed.return_value = False
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_multi_bucket" if tf == "D1" else "fresh",
        "bucketLag": 3,
    }
    pair = {"display": "WTI/USD", "type": "commodity", "source": "mt5"}
    stale, diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
        score_group="energy_oil",
        style="intraday",
    )
    assert stale == []
    assert "D1" in diag
    assert diag["D1"]["stalenessSeverity"] == "stale_multi_bucket"
    mock_d1_consumed.assert_called_once_with("energy_oil", "intraday")


@patch("athena_app.services.data_freshness.d1_consumed_by_score_group")
@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_engine_b_scan_freshness_gate_blocks_d1_when_group_consumes_d1(
    mock_diag, mock_d1_consumed
):
    """B3: when d1_consumed_by_score_group returns True, stale D1 still blocks."""
    mock_d1_consumed.return_value = True
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_multi_bucket" if tf == "D1" else "fresh",
        "bucketLag": 3,
    }
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    stale, diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
        score_group="forex_majors",
        style="intraday",
    )
    assert stale == ["D1:stale_multi_bucket"]
    assert diag["D1"]["stalenessSeverity"] == "stale_multi_bucket"


@patch("athena_app.services.data_freshness.d1_consumed_by_score_group")
@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_engine_b_scan_freshness_gate_blocks_d1_when_score_group_omitted(
    mock_diag, mock_d1_consumed
):
    """B3: when score_group is not supplied, the historic fail-closed D1 gate
    is preserved (d1_consumed_by_score_group is not consulted)."""
    mock_d1_consumed.return_value = False  # would skip if called
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_multi_bucket" if tf == "D1" else "fresh",
        "bucketLag": 3,
    }
    pair = {"display": "WTI/USD", "type": "commodity", "source": "mt5"}
    stale, _diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
    )
    assert stale == ["D1:stale_multi_bucket"]
    mock_d1_consumed.assert_not_called()


@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_entry_tf_mt5_equity_stale_1_bucket_is_tolerated(mock_diag):
    """MT5 equity CFDs lag ~1 M15 bucket; the entry-TF gate must not block them
    on stale_1_bucket (the live-quote gate guards the executable price)."""
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_1_bucket" if tf == "M15" else "fresh",
        "bucketLag": 1,
    }
    for pair_type in ("stock", "etf", "etf_bond"):
        pair = {"display": "SPY", "type": pair_type, "source": "mt5"}
        stale, diag = _engine_b_scan_freshness_stale_tfs(
            pair,
            [{"time": 1}] * 5,
            [{"time": 2}] * 5,
            [{"time": 3}] * 5,
            config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
            active_entry_tfs={"M15": [{"time": 4}] * 5},
        )
        assert stale == [], f"{pair_type} should not be blocked on entry-TF stale_1_bucket"
        assert diag["M15"]["stalenessSeverity"] == "stale_1_bucket"


@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_entry_tf_mt5_equity_stale_multi_bucket_still_blocks(mock_diag):
    """Only one-bucket lag is tolerated; genuinely stale equity entry bars fail closed."""
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_multi_bucket" if tf == "M15" else "fresh",
        "bucketLag": 3,
    }
    pair = {"display": "SPY", "type": "etf", "source": "mt5"}
    stale, _diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
        active_entry_tfs={"M15": [{"time": 4}] * 5},
    )
    assert stale == ["M15:stale_multi_bucket"]


@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_entry_tf_crypto_stale_1_bucket_still_blocks(mock_diag):
    """The grace is equity-only: real-time crypto/forex feeds must still fail
    closed on a stale entry bucket."""
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_1_bucket" if tf == "M15" else "fresh",
        "bucketLag": 1,
    }
    pair = {"display": "BTC/USDT", "type": "crypto", "source": "binance"}
    stale, _diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
        active_entry_tfs={"M15": [{"time": 4}] * 5},
    )
    assert stale == ["M15:stale_1_bucket"]


@patch("athena_app.services.market_state.candle_freshness_diagnostic")
def test_entry_tf_mt5_equity_grace_config_reversible(mock_diag):
    """Setting ENGINE_B_ENTRY_TF_ALLOW_MT5_EQUITY_STALE_1 False restores the
    strict entry-TF gate for equities."""
    mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
        "stalenessSeverity": "stale_1_bucket" if tf == "M15" else "fresh",
        "bucketLag": 1,
    }
    pair = {"display": "SPY", "type": "etf", "source": "mt5"}
    stale, _diag = _engine_b_scan_freshness_stale_tfs(
        pair,
        [{"time": 1}] * 5,
        [{"time": 2}] * 5,
        [{"time": 3}] * 5,
        config={
            "ENGINE_B_SCAN_FRESHNESS_GATE": True,
            "ENGINE_B_ENTRY_TF_ALLOW_MT5_EQUITY_STALE_1": False,
        },
        active_entry_tfs={"M15": [{"time": 4}] * 5},
    )
    assert stale == ["M15:stale_1_bucket"]
