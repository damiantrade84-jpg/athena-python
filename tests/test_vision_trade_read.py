from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vision_trade_read import build_vision_cache_key, parse_vision_trade_read


def test_structured_vision_json_parses_and_fresh_timestamp_allows_context():
    now = datetime.now(timezone.utc).isoformat()
    raw = '{"schema_version":"vision_trade_read.v1","right_edge_status":"CONFIRMS","tf_alignment":"ALIGNED","confirms_direction":true,"style_ratings":{"scalp":"STRONG","intraday":"MODERATE","swing":"NA"},"memo":"visible confirmation"}'
    out = parse_vision_trade_read(raw, chart_timestamp=now, latest_candle_ts=now, image_hash="abc", timeframe="H1")
    assert out["right_edge_status"] == "CONFIRMS"
    assert out["freshness_status"] == "fresh"
    assert out["allowed_for_execution_context"] is True


def test_footer_fallback_and_missing_timestamp_downgrades():
    raw = "RIGHT EDGE: REVIEW\nTF ALIGNMENT: CONFLICTED\nSCALP RATING: WEAK\nINTRADAY RATING: AVOID\nSWING RATING: NA"
    out = parse_vision_trade_read(raw, timeframe="H4")
    assert out["right_edge_status"] == "REVIEW"
    assert out["allowed_for_execution_context"] is False
    assert out["freshness_status"] == "unknown"


def test_stale_timestamp_rejects_execution_context():
    old = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    out = parse_vision_trade_read("{}", chart_timestamp=old, latest_candle_ts=old, timeframe="M5")
    assert out["freshness_status"] == "stale"
    assert out["allowed_for_execution_context"] is False


def test_future_timestamp_rejects_execution_context():
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    out = parse_vision_trade_read("{}", chart_timestamp=future, latest_candle_ts=future, timeframe="M5")

    assert out["freshness_status"] != "fresh"
    assert out["allowed_for_execution_context"] is False
    assert any("future" in warning.lower() for warning in out["warnings"])


def test_vision_cache_key_includes_execution_adjacent_fields():
    out = build_vision_cache_key(
        symbol="EUR/USD",
        timeframe_set="D1+H4",
        direction="LONG",
        trace_id="trace-1",
        latest_candle_ts="2026-05-14T00:00:00Z",
        image_hash="img",
    )
    assert out["ui_only"] is False
    assert "trace-1" in out["raw"]


def test_vision_cache_key_changes_with_latest_candle_and_marks_ui_only():
    first = build_vision_cache_key(
        symbol="EUR/USD",
        timeframe_set="D1+H4",
        direction="LONG",
        trace_id="trace-1",
        latest_candle_ts="2026-05-14T00:00:00Z",
        image_hash="img",
    )
    second = build_vision_cache_key(
        symbol="EUR/USD",
        timeframe_set="D1+H4",
        direction="LONG",
        trace_id="trace-1",
        latest_candle_ts="2026-05-14T04:00:00Z",
        image_hash="img",
    )
    ui_only = build_vision_cache_key(
        symbol="EUR/USD",
        timeframe_set="D1+H4",
        direction="LONG",
        trace_id="trace-1",
        latest_candle_ts=None,
        image_hash=None,
    )

    assert first["key"] != second["key"]
    assert ui_only["ui_only"] is True


def test_footer_parser_normalizes_potential_reversal_and_unknown_styles():
    now = datetime.now(timezone.utc).isoformat()
    raw = "RIGHT EDGE: POTENTIAL REVERSAL\nTF ALIGNMENT: ALIGNED\nSCALP RATING: CONTRADICTS\nINTRADAY RATING: BULLISH"

    out = parse_vision_trade_read(raw, chart_timestamp=now, latest_candle_ts=now, timeframe="H1")

    assert out["right_edge_status"] == "POTENTIAL_REVERSAL"
    assert out["tf_alignment"] == "ALIGNED"
    assert out["style_ratings"]["scalp"] == "CONTRADICTS"
    assert out["style_ratings"]["intraday"] == "NA"
