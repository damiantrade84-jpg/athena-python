import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner
from scanner import annotate_signal_for_scan


def test_annotate_signal_for_scan_includes_forex_zero_score_diagnostics():
    signal = {
        "confluenceScore": 0.0,
        "trendState": "NONE",
        "warnings": [],
        "factorScores": {
            "zero_score_reasons": [
                "hurst_veto_trend",
                "trend_gate_adx_below_min",
                "breakout_inactive",
            ]
        },
    }
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex", "enabled": True}

    out = annotate_signal_for_scan(
        signal,
        pair,
        threshold=0.85,
        ds_ctx={},
        earnings_ctx={},
        closed_exchanges=set(),
        news_ctx={},
    )

    codes = [d.get("code") for d in out.get("scanDiagnostics", [])]
    details = [d.get("detail") for d in out.get("scanDiagnostics", [])]

    assert "low_confluence" in codes
    assert "forex_hurst_veto_trend" in codes
    assert "forex_trend_gate_adx_below_min" in codes
    assert "forex_breakout_inactive" in codes
    assert "Hurst veto blocked the trend path" in details
    assert "Trend gate blocked: ADX below minimum" in details


def test_annotate_signal_for_scan_skips_forex_zero_diagnostics_for_nonzero_scores():
    signal = {
        "confluenceScore": 0.79,
        "trendState": "TREND_PULLBACK",
        "warnings": [],
        "factorScores": {
            "zero_score_reasons": ["hurst_veto_trend"]
        },
    }
    pair = {"display": "NZD/USD", "symbol": "NZDUSD=X", "type": "forex", "enabled": True}

    out = annotate_signal_for_scan(
        signal,
        pair,
        threshold=0.85,
        ds_ctx={},
        earnings_ctx={},
        closed_exchanges=set(),
        news_ctx={},
    )

    codes = [d.get("code") for d in out.get("scanDiagnostics", [])]

    assert "low_confluence" in codes
    assert "forex_hurst_veto_trend" not in codes


def test_annotate_signal_for_scan_handles_missing_warnings_key_with_event_reasons():
    """Upstream signals may omit ``warnings``; event-risk augmentation must not KeyError."""
    signal = {
        "confluenceScore": 1.0,
        "trendState": "TRENDING",
    }
    pair = {"display": "AAPL", "symbol": "AAPL.US", "type": "stock", "enabled": True}
    earnings_ctx = {"AAPL.US": {"daysTo": 2}}

    out = annotate_signal_for_scan(
        signal,
        pair,
        threshold=0.5,
        ds_ctx={},
        earnings_ctx=earnings_ctx,
        closed_exchanges=set(),
        news_ctx={},
    )

    assert isinstance(out.get("warnings"), list)
    assert any("EVENT RISK:" in str(w) and "Earnings" in str(w) for w in out["warnings"])


def test_fetch_scan_h4_candles_reuses_intermarket_preload_for_non_bybit_pair():
    preloaded = [{"time": "2026-05-28T00:00:00Z", "close": 1.1}]

    class Runtime:
        def fetch_candles(self, *_args, **_kwargs):
            raise AssertionError("preloaded H4 should avoid a duplicate fetch")

    candles, meta, crypto_bybit = scanner._fetch_scan_h4_candles(
        Runtime(),
        {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"},
        220,
        preloaded,
    )

    assert candles is preloaded
    assert meta is None
    assert crypto_bybit is False


def test_fetch_scan_h4_candles_ignores_preload_for_crypto_bybit_signal_feed(monkeypatch):
    preloaded = [{"time": "2026-05-28T00:00:00Z", "close": 100.0}]
    fetched = [{"time": "2026-05-28T04:00:00Z", "close": 101.0}]
    calls = {"count": 0}

    monkeypatch.setattr(scanner, "resolve_crypto_signal_feed", lambda *_a, **_k: "bybit")

    def _fake_fetch(_runtime, _pair, tf, limit):
        calls["count"] += 1
        assert tf == "H4"
        assert limit == 220
        return fetched, {"signalFeed": "bybit"}

    monkeypatch.setattr(scanner, "_fetch_ab_crypto_signal_candles", _fake_fetch)

    candles, meta, crypto_bybit = scanner._fetch_scan_h4_candles(
        object(),
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
        220,
        preloaded,
    )

    assert calls["count"] == 1
    assert candles is fetched
    assert meta == {"signalFeed": "bybit"}
    assert crypto_bybit is True
