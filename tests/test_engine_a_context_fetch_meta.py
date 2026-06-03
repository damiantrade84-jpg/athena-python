"""Regression tests for Engine A AI-review candleFetchMeta/cacheHit visibility.

Diagnostics-only fix: the AI-review context path must surface candle fetch
metadata (cacheHit) from candles_cache the same way the scanner path does. This
is forensics plumbing — it must never alter Engine A scoring/execution fields.
"""

from __future__ import annotations

from unittest.mock import patch

import ai_review.engine_a_context as eac


def _pair():
    return {"symbol": "BTCUSDT", "display": "BTCUSDT", "type": "crypto", "source": "binance"}


def _signal_with_cache_hit():
    return {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "confluenceScore": 2.4,
        "threshold": 2.0,
        "maxScore": 3.0,
        "price": 65000.0,
        "sl": 64000.0,
        "tp1": 67000.0,
        "rr1": 2.0,
        "atr": 1200.0,
        "regime": "trend",
        "timestamp": "2026-05-21T16:30:55+00:00",
        "atrDiagnostics": {"atr_value": 1200.0, "atr_tf": "H4", "atr_h4": 1200.0},
        "dataFreshness": {"allowed": True},
        "candleFetchMeta": {"pairSource": "binance", "H4": {"cacheHit": True}},
    }


def test_default_analyze_pair_forwards_preloaded_fetch_meta_from_cache():
    """_default_analyze_pair injects D1/H4/H1 cache metadata into scanner.analyze_pair."""
    captured: dict = {}

    def _fake_analyze_pair(pair, btc_bias, **kwargs):
        captured["pair"] = pair
        captured["btc_bias"] = btc_bias
        captured["kwargs"] = kwargs
        return {"symbol": pair["symbol"]}

    def _fake_meta(pair, tf, limit):
        return {"cacheHit": True, "tf": tf, "limit": limit}

    with patch("scanner.analyze_pair", _fake_analyze_pair), patch(
        "candles_cache.get_candle_fetch_meta", _fake_meta
    ):
        out = eac._default_analyze_pair(_pair(), "neutral", "intraday")

    assert out == {"symbol": "BTCUSDT"}
    assert captured["btc_bias"] == "neutral"
    assert captured["kwargs"]["style"] == "intraday"
    pfm = captured["kwargs"]["preloaded_fetch_meta"]
    assert set(pfm.keys()) == {"D1", "H4", "H1"}
    assert pfm["H4"]["cacheHit"] is True


def test_default_analyze_pair_preserves_supplied_preloaded_fetch_meta():
    """A caller-supplied preloaded_fetch_meta must not be overwritten or re-fetched."""
    captured: dict = {}

    def _fake_analyze_pair(pair, btc_bias, **kwargs):
        captured["kwargs"] = kwargs
        return {}

    sentinel = {"H4": {"cacheHit": "preexisting"}}

    def _fake_meta(pair, tf, limit):  # pragma: no cover - must not be called
        raise AssertionError("get_candle_fetch_meta must not run when meta is supplied")

    with patch("scanner.analyze_pair", _fake_analyze_pair), patch(
        "candles_cache.get_candle_fetch_meta", _fake_meta
    ):
        eac._default_analyze_pair(_pair(), "neutral", "swing", preloaded_fetch_meta=sentinel)

    assert captured["kwargs"]["preloaded_fetch_meta"] is sentinel


def test_default_analyze_pair_handles_none_metadata():
    """A cold/expired cache (get_candle_fetch_meta -> None) must not raise."""
    captured: dict = {}

    def _fake_analyze_pair(pair, btc_bias, **kwargs):
        captured["kwargs"] = kwargs
        return {}

    with patch("scanner.analyze_pair", _fake_analyze_pair), patch(
        "candles_cache.get_candle_fetch_meta", lambda *a, **k: None
    ):
        eac._default_analyze_pair(_pair(), "neutral", "swing")

    assert captured["kwargs"]["preloaded_fetch_meta"] == {"D1": None, "H4": None, "H1": None}


def test_assemble_with_injected_mock_simple_signature():
    """Injected analyze_pair_fn mocks with a simple signature keep working."""

    def _analyze(pair, btc_bias, style="swing"):
        return _signal_with_cache_hit()

    ctx = eac.assemble_engine_a_context(
        "BTCUSDT",
        "H4",
        resolve_pair_fn=lambda s: _pair(),
        analyze_pair_fn=_analyze,
        btc_bias_fn=lambda: "neutral",
    )
    assert ctx is not None
    assert ctx["symbol"] == "BTCUSDT"


def test_assemble_exposes_atr_cache_hit_when_h4_meta_present():
    """H4 cacheHit in candleFetchMeta surfaces as ctx['atr']['atr_cache_hit']."""

    def _analyze(pair, btc_bias, style="swing"):
        return _signal_with_cache_hit()

    ctx = eac.assemble_engine_a_context(
        "BTCUSDT",
        "H4",
        resolve_pair_fn=lambda s: _pair(),
        analyze_pair_fn=_analyze,
        btc_bias_fn=lambda: "neutral",
    )
    assert ctx["atr"]["atr_cache_hit"] is True


def test_fix_does_not_alter_scoring_or_execution_fields():
    """Diagnostics plumbing leaves Engine A score/threshold/SL/TP/RR untouched."""

    def _analyze(pair, btc_bias, style="swing"):
        return _signal_with_cache_hit()

    ctx = eac.assemble_engine_a_context(
        "BTCUSDT",
        "H4",
        resolve_pair_fn=lambda s: _pair(),
        analyze_pair_fn=_analyze,
        btc_bias_fn=lambda: "neutral",
    )
    assert ctx["confluence_score"] == 2.4
    assert ctx["threshold"] == 2.0
    assert ctx["max_score_override"] == 3.0
    assert ctx["direction"] == "LONG"
    assert ctx["geometry"]["stop_loss"] == 64000.0
    assert ctx["geometry"]["take_profit"] == 67000.0
    assert ctx["geometry"]["rr"] == 2.0
    assert ctx["score_attribution"]["aiReviewCanMutateScore"] is False
