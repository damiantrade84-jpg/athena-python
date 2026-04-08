"""Unit tests for news_sentiment_feed (no athena import)."""

import json

import pytest

import news_sentiment_feed as nsf
from news_sentiment_feed import (
    _latest_normalized_sentiment,
    _parse_news_ai_json,
    _strip_json_fences,
    apply_news_sentiment_to_scan_result,
    build_news_block,
    news_to_confluence_vote,
)


def test_news_to_confluence_vote_low_confidence():
    assert news_to_confluence_vote({"sentiment_score": 0.9, "confidence": 0.2}) is None


def test_news_to_confluence_vote_maps_product():
    v = news_to_confluence_vote({"sentiment_score": 0.5, "confidence": 0.8})
    assert v == pytest.approx(0.4)


def test_latest_normalized_sentiment_dict_list():
    data = {
        "EURUSD.FOREX": [
            {"date": "2026-01-01", "normalized": 0.1},
            {"date": "2026-03-01", "normalized": 0.6},
        ]
    }
    assert _latest_normalized_sentiment(data, "EURUSD.FOREX") == pytest.approx(0.6)


def test_latest_normalized_sentiment_single_dict():
    data = {"TEST.US": {"date": "2026-02-01", "normalized": -0.25}}
    assert _latest_normalized_sentiment(data, "TEST.US") == pytest.approx(-0.25)


def test_strip_json_fences():
    raw = '```json\n{"a": 1}\n```'
    assert json.loads(_strip_json_fences(raw)) == {"a": 1}


def test_parse_news_ai_json_plain():
    assert _parse_news_ai_json('{"pair": "X", "sentiment_score": 0.1}')["sentiment_score"] == 0.1


def test_parse_news_ai_json_after_prose():
    raw = 'Here is the analysis.\n\n{"pair": "EUR/USD", "sentiment_score": 0.0, "confidence": 0.5, "direction": "neutral", "key_themes": [], "major_event_detected": false, "major_event_description": null, "reasoning_summary": "x", "article_count_used": 1, "eodhd_pre_score": null, "eodhd_agreement": "unavailable"}'
    out = _parse_news_ai_json(raw)
    assert out["pair"] == "EUR/USD"
    assert out["direction"] == "neutral"


def test_parse_news_ai_json_empty_raises():
    import pytest as _pytest

    with _pytest.raises(ValueError, match="empty"):
        _parse_news_ai_json("   ")


def test_build_news_block_skips_non_dict():
    text = build_news_block([{"date": "2026-01-01", "title": "T", "content": "C"}, None, 3])
    assert "T" in text
    assert text.count("[") >= 1


def test_apply_news_sentiment_disabled_noop(monkeypatch):
    res = {"score": 2.0, "maxScoreOverride": 3.0, "warnings": []}

    def _boom(*_a, **_k):
        raise AssertionError("should not fetch when disabled")

    monkeypatch.setattr(
        "news_sentiment_feed.get_cached_news_confluence_vote", _boom
    )
    apply_news_sentiment_to_scan_result(
        res,
        {"display": "EUR/USD", "type": "forex"},
        config={"NEWS_SENTIMENT_CONFLUENCE_ENABLED": False},
        eodhd_ticker_for_pair=lambda _p: "EURUSD.FOREX",
    )
    assert res["score"] == 2.0


def test_apply_news_sentiment_blends_score(monkeypatch):
    monkeypatch.setenv("EODHD_KEY", "test")
    monkeypatch.setenv("XAI_API_KEY", "test")
    # Room below maxScore cap (forex scale 0-2)
    res = {"score": 0.5, "maxScoreOverride": 2.0, "warnings": []}

    def _fake_cached(*_a, **_k):
        return (0.5, {"direction": "bullish", "confidence": 0.9})

    monkeypatch.setattr(nsf, "get_cached_news_confluence_vote", _fake_cached)
    apply_news_sentiment_to_scan_result(
        res,
        {"display": "EUR/USD", "type": "forex"},
        config={
            "NEWS_SENTIMENT_CONFLUENCE_ENABLED": True,
            "NEWS_SENTIMENT_SCORE_IMPACT": 0.1,
            "NEWS_SENTIMENT_ATTACH_SUMMARY": True,
        },
        eodhd_ticker_for_pair=lambda _p: "EURUSD.FOREX",
    )
    # delta = 0.1 * 2.0 * 0.5 = 0.1 -> 0.5 + 0.1 = 0.6
    assert res["score"] == pytest.approx(0.6)
    assert res["newsSentimentVote"] == 0.5
    assert res["newsSentimentDelta"] == pytest.approx(0.1)  # 0.1 * 2.0 * 0.5
    assert any("News AI" in w for w in res["warnings"])
