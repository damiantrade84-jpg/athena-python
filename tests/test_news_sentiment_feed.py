"""Unit tests for news_sentiment_feed (no athena import)."""

import json
from datetime import datetime, timezone

import pytest

import news_sentiment_feed as nsf
from news_sentiment_feed import (
    _latest_normalized_sentiment,
    _parse_news_ai_json,
    _strip_json_fences,
    apply_news_sentiment_to_scan_result,
    build_news_block,
    fetch_news_sources,
    news_to_confluence_vote,
    resolve_news_sources_for_pair,
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
    raw = 'Here is the analysis.\n\n{"pair": "EUR/USD", "sentiment_score": 0.0, "confidence": 0.5, "direction": "neutral", "key_themes": [], "major_event_detected": false, "major_event_description": null, "reasoning_summary": "x", "article_count_used": 1}'
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
    # Room below maxScore cap (forex scale 0-3, Engine A v2)
    res = {"score": 0.5, "maxScoreOverride": 3.0, "warnings": []}

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
    # delta = 0.1 * 3.0 * 0.5 = 0.15 -> 0.5 + 0.15 = 0.65
    assert res["score"] == pytest.approx(0.65)
    assert res["newsSentimentVote"] == 0.5
    assert res["newsSentimentDelta"] == pytest.approx(0.15)  # 0.1 * 3.0 * 0.5
    assert res["newsSentimentSummary"]["direction"] == "bullish"
    assert "article_count_used" in res["newsSentimentSummary"]
    assert "key_themes" in res["newsSentimentSummary"]
    assert any("News AI" in w for w in res["warnings"])


def test_resolve_news_sources_expands_direct_and_configured_macro_sources():
    pair = {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
    config = {
        "NEWS_SENTIMENT_MACRO_CONTEXT_ENABLED": True,
        "NEWS_SENTIMENT_DRIVER_MAP": {
            "XAU/USD": [
                "tag:usd",
                "tag:fed-rates",
                {"kind": "tag", "value": "geopolitics", "label": "safe-haven/geopolitics"},
            ],
            "commodity": ["tag:inflation"],
        },
    }

    sources = resolve_news_sources_for_pair(
        pair,
        lambda _p: "XAUUSD.FOREX",
        config,
    )

    keys = {(src["kind"], src["value"]) for src in sources}
    assert ("ticker", "XAUUSD.FOREX") in keys
    assert ("tag", "usd") in keys
    assert ("tag", "fed-rates") in keys
    assert ("tag", "geopolitics") in keys
    assert ("tag", "inflation") in keys


def test_fetch_news_sources_excludes_stale_articles_and_keeps_source_metadata(monkeypatch):
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "date": "2026-05-24T10:00:00+00:00",
                    "title": "Fresh gold headline",
                    "content": "Fresh",
                    "link": "https://example.com/fresh",
                },
                {
                    "date": "2026-05-20T10:00:00+00:00",
                    "title": "Stale gold headline",
                    "content": "Stale",
                    "link": "https://example.com/stale",
                },
            ]

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    monkeypatch.setattr(nsf.http_requests, "get", fake_get)

    bundle = fetch_news_sources(
        [{"kind": "ticker", "value": "XAUUSD.FOREX", "label": "direct"}],
        "test-key",
        {
            "NEWS_SENTIMENT_MAX_ARTICLE_AGE_HOURS": 24,
            "NEWS_SENTIMENT_MAX_ARTICLES_TOTAL": 5,
            "NEWS_SENTIMENT_MAX_ARTICLES_PER_SOURCE": 5,
        },
        now=now,
    )

    assert len(bundle["articles"]) == 1
    assert bundle["articles"][0]["title"] == "Fresh gold headline"
    assert bundle["articles"][0]["source"]["value"] == "XAUUSD.FOREX"
    assert bundle["staleArticleCount"] == 1
    assert calls[0][1]["from"] == "2026-05-23"


def test_eodhd_normalized_sentiment_combined_after_llm_and_not_in_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        choices = [
            type(
                "Choice",
                (),
                {
                    "message": type(
                        "Message",
                        (),
                        {
                            "content": json.dumps(
                                {
                                    "sentiment_score": 0.5,
                                    "confidence": 0.8,
                                    "direction": "bullish",
                                    "key_themes": [],
                                    "major_event_detected": False,
                                    "major_event_description": None,
                                    "reasoning_summary": "LLM only.",
                                    "article_count_used": 1,
                                }
                            )
                        },
                    )()
                },
            )()
        ]

    class FakeCompletions:
        def create(self, **kwargs):
            captured["prompt"] = kwargs["messages"][1]["content"]
            return FakeResponse()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(nsf, "create_ai_client", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr(
        nsf,
        "fetch_news_sources",
        lambda *_a, **_k: {
            "articles": [{"date": "2026-05-24", "title": "Fresh", "content": "Body"}],
            "sourceMetadata": [{"kind": "ticker", "value": "EURUSD.FOREX"}],
            "freshArticleCount": 1,
            "staleArticleCount": 0,
        },
    )
    monkeypatch.setattr(nsf, "fetch_eodhd_sentiment", lambda *_a, **_k: -0.2)

    result = nsf.get_news_sentiment(
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        eodhd_api_key="eod",
        xai_api_key="xai",
        eodhd_ticker_for_pair=lambda _p: "EURUSD.FOREX",
        model="test-model",
        config={"NEWS_SENTIMENT_USE_EODHD_NORMALIZED": True},
    )

    assert result["eodhd_normalized_score"] == pytest.approx(-0.2)
    assert result["eodhd_agreement"] == "conflict"
    assert result["combined_vote"] != news_to_confluence_vote({"sentiment_score": 0.5, "confidence": 0.8})
    assert "eodhd_normalized_score" not in captured["prompt"]


def test_major_event_risk_and_guards_are_single_owner(monkeypatch):
    monkeypatch.setenv("EODHD_KEY", "test")
    monkeypatch.setenv("XAI_API_KEY", "test")
    res = {"score": 0.5, "maxScoreOverride": 3.0, "warnings": [], "final_score": 0.5}

    def _fake_cached(*_a, **_k):
        return (
            1.0,
            {
                "direction": "bullish",
                "confidence": 0.9,
                "sentiment_score": 1.0,
                "major_event_detected": True,
                "major_event_description": "FOMC surprise",
                "fresh_article_count": 2,
                "article_count_used": 2,
            },
        )

    monkeypatch.setattr(nsf, "get_cached_news_confluence_vote", _fake_cached)
    apply_news_sentiment_to_scan_result(
        res,
        {"display": "EUR/USD", "type": "forex"},
        config={
            "NEWS_SENTIMENT_CONFLUENCE_ENABLED": True,
            "NEWS_SENTIMENT_SCORE_IMPACT": 0.5,
            "NEWS_SENTIMENT_MAX_DELTA": 0.2,
            "NEWS_SENTIMENT_MAJOR_EVENT_MODE": "negative_delta_only",
        },
        eodhd_ticker_for_pair=lambda _p: "EURUSD.FOREX",
        threshold=1.0,
        max_score=3.0,
    )

    assert res["pre_news_score"] == pytest.approx(0.5)
    assert res["newsSentimentRawDelta"] == pytest.approx(1.5)
    assert res["newsSentimentDelta"] == pytest.approx(0.0)
    assert res["score"] == pytest.approx(0.5)
    assert res["majorEventRisk"] == {
        "majorEventDetected": True,
        "mode": "negative_delta_only",
        "action": "negative_delta_only",
        "reason": "FOMC surprise",
        "blocksAutoExecution": False,
    }


def test_major_event_block_auto_execution_mode(monkeypatch):
    monkeypatch.setenv("EODHD_KEY", "test")
    monkeypatch.setenv("XAI_API_KEY", "test")
    res = {"score": 2.0, "maxScoreOverride": 3.0, "warnings": [], "final_score": 2.0}

    monkeypatch.setattr(
        nsf,
        "get_cached_news_confluence_vote",
        lambda *_a, **_k: (
            0.5,
            {
                "direction": "bullish",
                "confidence": 0.8,
                "sentiment_score": 0.5,
                "major_event_detected": True,
                "major_event_description": "Emergency rate hike",
                "article_count_used": 3,
            },
        ),
    )
    apply_news_sentiment_to_scan_result(
        res,
        {"display": "XAU/USD", "type": "commodity"},
        config={
            "NEWS_SENTIMENT_CONFLUENCE_ENABLED": True,
            "NEWS_SENTIMENT_SCORE_IMPACT": 0.06,
            "NEWS_SENTIMENT_MAJOR_EVENT_MODE": "block_auto_execution",
        },
        eodhd_ticker_for_pair=lambda _p: "XAUUSD.FOREX",
        threshold=1.5,
        max_score=3.0,
    )

    assert res["majorEventRisk"]["mode"] == "block_auto_execution"
    assert res["majorEventRisk"]["action"] == "block_auto_execution"
    assert res["majorEventRisk"]["blocksAutoExecution"] is True
    assert res["majorEventRisk"]["reason"] == "Emergency rate hike"


def test_news_delta_is_capped(monkeypatch):
    monkeypatch.setenv("EODHD_KEY", "test")
    monkeypatch.setenv("XAI_API_KEY", "test")
    res = {"score": 2.0, "maxScoreOverride": 3.0, "warnings": []}

    monkeypatch.setattr(
        nsf,
        "get_cached_news_confluence_vote",
        lambda *_a, **_k: (1.0, {"direction": "bullish", "confidence": 0.9}),
    )
    apply_news_sentiment_to_scan_result(
        res,
        {"display": "BTC/USDT", "type": "crypto"},
        config={
            "NEWS_SENTIMENT_CONFLUENCE_ENABLED": True,
            "NEWS_SENTIMENT_SCORE_IMPACT": 1.0,
            "NEWS_SENTIMENT_MAX_DELTA": 0.25,
        },
        eodhd_ticker_for_pair=lambda _p: "BTC-USD.CC",
        threshold=1.0,
        max_score=3.0,
    )

    assert res["newsSentimentRawDelta"] == pytest.approx(3.0)
    assert res["newsSentimentDelta"] == pytest.approx(0.25)
    assert res["score"] == pytest.approx(2.25)
