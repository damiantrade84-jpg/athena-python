"""Tests for AI chart review v1 backend."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from ai_review.concordance import compute_engine_a_ai_concordance
from ai_review.engine_snapshots import extract_engine_snapshots
from ai_review.freshness import classify_atr_freshness
from ai_review.normalizer import normalize_chart_review_response
from ai_review.persistence import ensure_schema, find_recent_review_by_hash, record_review
from ai_review.prompt_builder import build_chart_review_prompt
from ai_review.provider_meta import apply_parse_fallback, build_provider_meta
from ai_review.providers.router import run_chart_review
from ai_review.engine_a_context import build_engine_a_prompt_context
from ai_review.engine_a_verdict import build_engine_a_verdict_comparison
from ai_review.summary import build_ai_review_summary
from ai_review.context_diagnostics import build_context_diagnostics, sanitize_ai_review_missing_context
from ai_review.timestamp_contract import evaluate_timestamp_mismatch
from ai_review.validation import validate_request
from athena_app.api.routes_ai_chart_review import register_ai_chart_review_routes
from config import CONFIG

_PNG_1X1 = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c630001000005000108d4000000000049454e44ae426082"
    )
).decode()


def _png_data_url() -> str:
    return f"data:image/png;base64,{_PNG_1X1}"


def _base_request(**overrides):
    body = {
        "symbol": "BTCUSDT",
        "timeframe": "H4",
        "provider": "default",
        "screenshot_base64": _png_data_url(),
        "screenshot_meta": {
            "width": 1280,
            "height": 720,
            "native_chart": True,
            "captured_at": "2026-05-21T16:31:02+00:00",
            "chart_timeframe": "H4",
            "overlays": ["candles"],
        },
    }
    body.update(overrides)
    return body


def _engine_a_ctx(**overrides):
    ctx = {
        "symbol": "BTCUSDT",
        "timeframe": "H4",
        "asset_group": "crypto",
        "direction": "LONG",
        "regime": "trend",
        "scan_timestamp": "2026-05-21T16:30:55+00:00",
        "candidate_timestamp": "2026-05-21T16:30:55+00:00",
        "latest_candle_ts": "2026-05-21T16:00:00+00:00",
        "confluence_score": 2.4,
        "threshold": 2.0,
        "max_score_override": 3.0,
        "passed": True,
        "factor_diagnostics": {"trendCoherence": {"aligned": True}},
        "multiplier_diagnostics": {"equity_session_multiplier": 1.02},
        "equity_session": {
            "applied": True,
            "reason": "us_cash_open",
            "utc_hour": 14,
            "multiplier": 1.02,
        },
        "directional_alignment": {"directionalScore": 0.8},
        "atr": {
            "atr_value": 1200.0,
            "atr_tf": "H4",
            "atr_source": "h4",
            "atr_age_seconds": 7200.0,
            "atr_confirmed_only": True,
            "atr_freshness_status": "fresh",
            "max_expected_age_seconds": 18000,
        },
        "geometry": {
            "candidate_entry": 65000.0,
            "current_price": 65000.0,
            "stop_loss": 64000.0,
            "take_profit": 67000.0,
            "risk_points": 1000.0,
            "reward_points": 2000.0,
            "rr": 2.0,
            "price_displacement_from_candidate_entry": 0.0,
            "sl_tp_source": "engine_a_levels",
        },
        "freshness": {"stale_warnings": []},
    }
    ctx.update(overrides)
    return ctx


def _mock_provider_payload(**overrides):
    body = {
        "verdict": "VALID",
        "confidence": 80,
        "setup_type": "breakout",
        "visual_confirmation": "aligned",
        "visual_contradiction": "",
        "engine_a_alignment": "aligned",
        "atr_rr_assessment": "good",
        "freshness_assessment": "fresh",
        "entry_quality": "good",
        "supporting_reasons": ["ok"],
        "risks": [],
        "missing_context": [],
        "human_action": "take",
    }
    body.update(overrides.get("ai") or {})
    raw = overrides.get("raw_text", json.dumps(body))
    base = {
        "raw_text": raw,
        "model": "claude-opus-4-7",
        "latency_ms": 50,
        "provider": "anthropic",
        "provider_status": "success",
        "fallback_used": False,
    }
    base.update({k: v for k, v in overrides.items() if k != "ai"})
    return base


def _make_app(tmp_db: str, enabled: bool = True):
    app = Flask(__name__)
    cfg = dict(CONFIG)
    ai_cfg = dict(cfg["AI_CHART_REVIEW"])
    ai_cfg["ENABLED"] = enabled
    cfg["AI_CHART_REVIEW"] = ai_cfg

    def _resolve(symbol: str):
        return {"symbol": symbol, "display": symbol, "type": "crypto", "source": "binance"}

    def _analyze(pair, btc_bias, style="swing"):
        return {
            "symbol": pair["symbol"],
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
            "factorDiagnostics": {
                "equity_session": {
                    "enabled": True,
                    "multiplier": 1.02,
                    "utc_hour": 14,
                    "reason": "us_cash_open",
                },
                "trendCoherence": {
                    "ema50_value": 64800.0,
                    "ema200_value": 62300.0,
                },
            },
            "atrDiagnostics": {
                "atr_value": 1200.0,
                "atr_tf": "H4",
                "atr_source": "h4",
                "atr_age_seconds": 7200.0,
                "atr_confirmed_only": True,
                "atr_h4": 1200.0,
                "atr_d1": 2100.0,
            },
            "dataFreshness": {"allowed": True},
            "h1Candles": [{"time": "2026-05-21T16:00:00+00:00", "high": 65300.0}],
            "h4Candles": [{"time": "2026-05-21T16:00:00+00:00", "high": 65400.0}],
            "d1Candles": [{"time": "2026-05-20T00:00:00+00:00", "high": 67250.0}],
            "candleFetchMeta": {"pairSource": "binance"},
            "fundingRate": 0.0001,
            "oiData": {
                "oi": 123456.0,
                "oiChange": 1.5,
                "source": "bybit",
                "ts": 1779381000,
            },
            "oiContext": {
                "oi_change_pct": 1.5,
                "price_change_pct": 0.8,
            },
            "engine_b": {
                "nearest_resistance_zone": {"lower": 66800.0, "upper": 67200.0},
                "distance_to_res": 1800.0,
                "recommended_take_profit": 67000.0,
                "structural_target_candidates": [
                    {
                        "target_type": "resistance_zone",
                        "target_price": 67000.0,
                        "selected": True,
                    }
                ],
                "prev_session_poc": 65150.0,
                "prev_session_vah": 66200.0,
                "prev_session_val": 64200.0,
                "d1_order_blocks": [],
            },
        }

    runtime = SimpleNamespace(
        CONFIG=cfg,
        AUDIT_DB=tmp_db,
        json_safe=lambda x: x,
        log=MagicMock(),
        resolve_pair_fn=_resolve,
        analyze_pair_fn=_analyze,
        btc_bias_fn=lambda: "neutral",
    )
    register_ai_chart_review_routes(app, runtime)
    return app


@pytest.fixture()
def tmp_audit_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    ensure_schema(path)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


def test_route_rejects_missing_screenshot(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    del body["screenshot_base64"]
    with patch("ai_review.providers.anthropic_provider.call_anthropic_chart_review") as mock_call:
        resp = client.post("/api/ai/chart-review", json=body)
        assert resp.status_code == 400
        mock_call.assert_not_called()


def test_route_rejects_missing_symbol_or_timeframe(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    body.pop("symbol")
    resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 400


def test_route_rejects_non_png_data_url(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(screenshot_base64="data:image/jpeg;base64,abc")
    resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 415


def test_route_rejects_openai_when_disabled(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(provider="openai")
    resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 403


def test_anthropic_strips_data_url_prefix():
    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.model = "claude-opus-4-7"
    mock_resp.content = [MagicMock(type="text", text='{"verdict":"VALID","confidence":80,"human_action":"take"}')]
    mock_client.messages.create.return_value = mock_resp

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic", return_value=mock_client):
            call_anthropic_chart_review = __import__(
                "ai_review.providers.anthropic_provider",
                fromlist=["call_anthropic_chart_review"],
            ).call_anthropic_chart_review
            call_anthropic_chart_review(payload)

    content = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    image_data = content[0]["source"]["data"]
    assert not str(image_data).startswith("data:")
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[0]["type"] == "image"
    assert content[1]["type"] == "text"


def test_anthropic_uses_max_tokens_from_config():
    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.model = "claude-opus-4-7"
    mock_resp.content = [MagicMock(type="text", text="{}")]
    mock_client.messages.create.return_value = mock_resp

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic", return_value=mock_client):
            with patch.dict(CONFIG["AI_CHART_REVIEW"], {"MAX_TOKENS": 4000}):
                from ai_review.providers.anthropic_provider import call_anthropic_chart_review

                call_anthropic_chart_review(payload)

    assert mock_client.messages.create.call_args.kwargs["max_tokens"] >= 1500


def test_prompt_includes_engine_a_score():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "2.4" in prompt


def test_default_resolve_pair_accepts_slashless_forex_alias(monkeypatch):
    fake_athena = ModuleType("athena")
    fake_athena.ALL_PAIRS = [
        {"symbol": "EURCHF=X", "display": "EUR/CHF", "type": "forex", "source": "mt5"}
    ]
    fake_athena.CONFIG = {"EXCHANGE_SOURCE": "binance"}
    monkeypatch.setitem(sys.modules, "athena", fake_athena)

    from ai_review.engine_a_context import _default_resolve_pair

    pair = _default_resolve_pair("EURCHF")

    assert pair["display"] == "EUR/CHF"
    assert pair["symbol"] == "EURCHF=X"
    assert pair["type"] == "forex"
    assert pair["source"] == "mt5"


def test_prompt_threshold_not_hardcoded():
    source = open(
        os.path.join(os.path.dirname(__file__), "..", "ai_review", "prompt_builder.py"),
        encoding="utf-8",
    ).read()
    for line in source.splitlines():
        if "threshold:" in line.lower():
            assert "1.5" not in line and "2.0" not in line and "1.7" not in line


def test_prompt_includes_engine_a_context_json():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "engineAContext" in prompt
    assert '"direction": "LONG"' in prompt or '"direction": "LONG",' in prompt
    assert "engineAVerdictComparison" in prompt


def test_prompt_instructs_no_trade_only_for_hard_reasons():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "Do not use NO_TRADE as generic caution" in prompt
    assert "Use NO_TRADE only with hard invalidation or a concrete noTradeReason" in prompt


def test_build_engine_a_prompt_context_null_when_missing():
    ctx = _engine_a_ctx(threshold=None, confluence_score=None)
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    block = build_engine_a_prompt_context(ctx)
    assert block["threshold"] is None
    assert block["score"] is None


def test_prompt_includes_equity_session_applied_and_multiplier():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "us_cash_open" in prompt
    assert "1.02" in prompt


def test_prompt_includes_atr_diagnostics():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "1200.0" in prompt
    assert "H4" in prompt
    assert "7200.0" in prompt


def test_prompt_includes_sl_tp_rr():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "64000.0" in prompt
    assert "67000.0" in prompt
    assert "2.0" in prompt


def test_prompt_includes_tf_aware_atr_freshness_wording():
    source = open(
        os.path.join(os.path.dirname(__file__), "..", "ai_review", "prompt_builder.py"),
        encoding="utf-8",
    ).read()
    assert "D1 confirmed-only ATR" in source


def test_d1_confirmed_only_atr_not_auto_stale():
    result = classify_atr_freshness("D1", 100000, True)
    assert result["status"] == "expected_lag"


def test_missing_atr_creates_uncertainty():
    ctx = _engine_a_ctx()
    ctx["atr"]["atr_value"] = None
    prompt = build_chart_review_prompt(ctx)
    assert "unavailable" in prompt
    freshness = classify_atr_freshness("H4", None, True)
    assert freshness["status"] == "unknown"


def test_normalizer_stable_schema_valid_json():
    raw = json.dumps(
        {
            "verdict": "VALID",
            "confidence": 76,
            "setup_type": "breakout",
            "visual_confirmation": "ok",
            "visual_contradiction": "",
            "engine_a_alignment": "aligned",
            "atr_rr_assessment": "good",
            "freshness_assessment": "fresh",
            "entry_quality": "good",
            "supporting_reasons": ["a"],
            "risks": ["b"],
            "missing_context": [],
            "human_action": "take",
        }
    )
    out = normalize_chart_review_response(raw)
    for key in (
        "verdict",
        "confidence",
        "setup_type",
        "visual_confirmation",
        "visual_contradiction",
        "engine_a_alignment",
        "atr_rr_assessment",
        "freshness_assessment",
        "entry_quality",
        "supporting_reasons",
        "risks",
        "missing_context",
        "human_action",
        "raw_model_response",
    ):
        assert key in out
    assert out["parse_success"] is True


def test_normalizer_caution_on_parse_fail():
    out = normalize_chart_review_response("not json")
    assert out["verdict"] == "CAUTION"
    assert out["confidence"] == 0
    assert out["human_action"] == "wait"
    assert "parse failed" in out["risks"][0].lower()
    assert out["raw_model_response"] == "not json"


def test_concordance_agree_when_passed_and_valid():
    ai = normalize_chart_review_response(
        json.dumps({"verdict": "VALID", "confidence": 80, "human_action": "take"})
    )
    out = compute_engine_a_ai_concordance(_engine_a_ctx(), ai)
    assert out["concordance"] == "agree"


def test_concordance_partial_when_passed_and_caution():
    ai = normalize_chart_review_response(
        json.dumps({"verdict": "CAUTION", "confidence": 50, "human_action": "wait"})
    )
    out = compute_engine_a_ai_concordance(_engine_a_ctx(), ai)
    assert out["concordance"] == "partial"
    assert out["divergence_type"] != "missing_context"


def test_concordance_optional_missing_does_not_set_missing_context_divergence():
    ctx = _engine_a_ctx(asset_group="forex")
    ctx["screenshot_overlays"] = []
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "CAUTION",
                "confidence": 55,
                "human_action": "wait",
                "missing_context": ["engineB.score"],
            }
        )
    )
    ai = sanitize_ai_review_missing_context(ctx, ai)
    out = compute_engine_a_ai_concordance(ctx, ai)
    assert out["concordance"] == "partial"
    assert out["divergence_type"] != "missing_context"


def test_concordance_disagree_when_passed_and_invalid():
    ai = normalize_chart_review_response(
        json.dumps({"verdict": "INVALID", "confidence": 20, "human_action": "reject"})
    )
    out = compute_engine_a_ai_concordance(_engine_a_ctx(), ai)
    assert out["concordance"] == "disagree"


def test_persistence_stores_engine_a_ai_concordance(tmp_audit_db):
    ai = normalize_chart_review_response(json.dumps({"verdict": "VALID", "confidence": 80, "human_action": "take"}))
    concordance = compute_engine_a_ai_concordance(_engine_a_ctx(), ai)
    record_review(
        symbol="BTCUSDT",
        timeframe="H4",
        asset_group="crypto",
        provider="anthropic",
        model="claude-opus-4-7",
        latency_ms=100,
        screenshot_hash="abc123",
        screenshot_bytes=100,
        screenshot_meta={"native_chart": True},
        engine_a_context=_engine_a_ctx(),
        ai_review=ai,
        concordance=concordance,
        mismatch_warnings=[],
        audit_db=tmp_audit_db,
    )
    with sqlite3.connect(tmp_audit_db) as con:
        row = con.execute("SELECT engine_a_snapshot_json, ai_review_json, concordance_json FROM ai_chart_reviews").fetchone()
    assert row is not None
    assert json.loads(row[0])["confluence_score"] == 2.4
    assert json.loads(row[1])["verdict"] == "VALID"
    assert json.loads(row[2])["concordance"] == "agree"


def test_persistence_stores_hash_not_full_base64(tmp_audit_db):
    ai = normalize_chart_review_response(json.dumps({"verdict": "VALID", "confidence": 80, "human_action": "take"}))
    concordance = compute_engine_a_ai_concordance(_engine_a_ctx(), ai)
    record_review(
        symbol="BTCUSDT",
        timeframe="H4",
        asset_group="crypto",
        provider="anthropic",
        model="claude-opus-4-7",
        latency_ms=100,
        screenshot_hash="abc123",
        screenshot_bytes=100,
        screenshot_meta={"native_chart": True, "width": 1280},
        engine_a_context=_engine_a_ctx(),
        ai_review=ai,
        concordance=concordance,
        mismatch_warnings=[],
        audit_db=tmp_audit_db,
    )
    with sqlite3.connect(tmp_audit_db) as con:
        row = con.execute(
            "SELECT screenshot_meta_json, screenshot_hash FROM ai_chart_reviews"
        ).fetchone()
    assert "base64" not in (row[0] or "").lower()
    assert row[1]


def test_timestamp_mismatch_creates_warning():
    ctx = _engine_a_ctx(scan_timestamp="2026-05-21T16:30:55+00:00")
    meta = {"captured_at": "2026-05-21T16:34:15+00:00"}
    warnings = evaluate_timestamp_mismatch(ctx, meta, {"MISMATCH_WARN_MAX_SECONDS": 120})
    assert warnings


def test_route_rejects_image_above_max_bytes(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    big = base64.b64encode(b"x" * (3 * 1024 * 1024)).decode()
    body = _base_request(screenshot_base64=f"data:image/png;base64,{big}")
    resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 413


def test_dedup_within_window_skips_api_call(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ) as mock_call:
        first = client.post("/api/ai/chart-review", json=body)
        assert first.status_code == 200
        second = client.post("/api/ai/chart-review", json=body)
        assert second.status_code == 200
        assert second.get_json()["dedup_hit"] is True
        assert mock_call.call_count == 1


def test_router_default_resolves_to_anthropic():
    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(raw_text="{}"),
    ) as mock_anthropic:
        run_chart_review("default", payload)
        mock_anthropic.assert_called_once()


def test_router_resolves_xai_provider():
    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    with patch(
        "ai_review.providers.router.call_xai_chart_review",
        return_value=_mock_provider_payload(
            raw_text="{}",
            provider="xai",
            model="grok-4.3",
        ),
        create=True,
    ) as mock_xai:
        out = run_chart_review("xai", payload)
        mock_xai.assert_called_once()
    assert out["provider"] == "xai"
    assert out["model"] == "grok-4.3"


def test_router_aliases_grok_to_xai_provider():
    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    with patch(
        "ai_review.providers.router.call_xai_chart_review",
        return_value=_mock_provider_payload(
            raw_text="{}",
            provider="xai",
            model="grok-4.3",
        ),
        create=True,
    ) as mock_xai:
        run_chart_review("grok", payload)
        mock_xai.assert_called_once()


def test_xai_provider_missing_key_fails_closed(monkeypatch):
    from ai_review.providers.xai_provider import call_xai_chart_review
    from ai_review.provider_meta import ProviderChartReviewError

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with patch.dict(CONFIG, {"XAI_API_KEY": ""}, clear=False):
        with pytest.raises(ProviderChartReviewError) as excinfo:
            call_xai_chart_review(payload)
    assert excinfo.value.provider_status == "failed_auth"
    assert excinfo.value.provider == "xai"


def test_xai_provider_posts_png_data_url_as_image_url(monkeypatch):
    from ai_review.providers.xai_provider import call_xai_chart_review

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review this chart"
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.model = "grok-4.3"
    mock_choice = MagicMock()
    mock_choice.message.content = '{"verdict":"VALID","confidence":80,"human_action":"take"}'
    mock_resp.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch(
        "ai_review.providers.xai_provider.create_ai_client",
        return_value=mock_client,
    ):
        out = call_xai_chart_review(payload)

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    content = kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "review this chart"}
    assert content[1] == {"type": "image_url", "image_url": {"url": payload.screenshot_base64}}
    assert kwargs["model"] == "grok-4.3"
    assert out["provider"] == "xai"
    assert out["raw_text"].startswith('{"verdict"')


def test_validate_request_disabled_provider():
    cfg = dict(CONFIG["AI_CHART_REVIEW"])
    err = validate_request(_base_request(provider="openai"), cfg)
    assert err is not None
    assert err.status == 403


def test_summary_always_present_on_success(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    summary = data.get("ai_review_summary")
    assert summary is not None
    assert data.get("aiReviewSummary") == summary
    for key in (
        "provider",
        "model",
        "providerStatus",
        "fallbackUsed",
        "humanAction",
        "setupType",
        "overallScore",
        "tradeabilityScore",
        "engineAlignmentScore",
        "visualConfirmationScore",
        "entryQualityScore",
        "riskScore",
        "confidence",
        "finalReason",
        "engineA",
    ):
        assert key in summary
    assert data.get("engineAVerdictComparison") is not None
    assert data.get("engine_a_verdict_comparison") == data["engineAVerdictComparison"]
    assert data["reviewInputMeta"] == {
        "symbol": "BTCUSDT",
        "signalEngine": "A",
        "signalTimeframe": "H4",
        "chartTimeframe": "H4",
        "hasEngineASignal": True,
        "hasEngineBOverlay": True,
        "hasChartImage": True,
        "timeframeRouteApplied": bool(data["timeframeRoute"].get("enabled")),
    }


def test_timeframe_route_attached_to_success_response(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(timeframe="H4")
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(
            ai={
                "verdict": "CAUTION",
                "human_action": "wait",
                "visual_confirmation": "direction ok",
                "engine_a_alignment": "aligned with engine",
                "entry_quality": "poor timing extended above VWAP",
                "risks": ["late entry"],
            }
        ),
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    route = data["timeframeRoute"]
    assert data["timeframe_route"] == route
    assert data["engine_a_context"]["timeframe_route"] == route
    assert route["contextTf"] == "H4"
    assert route["entryTf"] == "H1"
    assert route["executionTf"] == "M15"
    assert route["autoSelectTf"] == "H1"
    assert route["mode"] == "entry_wait"


def test_timeframe_route_dedup_response_has_route(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(timeframe="H4")
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(
            ai={
                "verdict": "CAUTION",
                "human_action": "wait",
                "visual_confirmation": "direction ok",
                "engine_a_alignment": "aligned with engine",
                "entry_quality": "poor timing extended above VWAP",
                "risks": ["late entry"],
            }
        ),
    ) as mock_call:
        first = client.post("/api/ai/chart-review", json=body)
        second = client.post("/api/ai/chart-review", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    data = second.get_json()
    assert data["dedup_hit"] is True
    assert data["timeframeRoute"]["autoSelectTf"] == "H1"
    assert data["timeframe_route"] == data["timeframeRoute"]
    assert data["engine_a_context"]["timeframe_route"] == data["timeframeRoute"]
    assert data["reviewInputMeta"]["symbol"] == "BTCUSDT"
    assert data["reviewInputMeta"]["timeframeRouteApplied"] is True
    assert mock_call.call_count == 1


def test_context_diagnostics_attached_to_success_response(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(
            ai={
                "atr_rr_assessment": "H4 ATR confirms risk and TP clears resistance",
                "missing_context": [
                    "chart captured timestamp",
                    "equity_session multiplier unavailable / not applied",
                ],
            }
        ),
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["contextCompleteness"]["metadata"]["chartCapturedAt"] == "2026-05-21T16:31:02+00:00"
    assert data["contextCompleteness"]["metadata"]["chartProvider"] is None
    assert "chart captured timestamp" not in data["contextCompleteness"]["missingRequired"]
    assert "chart captured timestamp" not in data["contextCompleteness"]["missingOptional"]
    assert "equity_session" in [item["key"] for item in data["missingContextDetailed"]["notApplicable"]]
    assert data["atrDiagnostics"]["atrH4"] == 1200.0
    assert data["atrDiagnostics"]["atrChartTf"] == 1200.0
    assert data["resistanceMap"]["nearestResistance"] == 66800.0
    assert data["resistanceMap"]["tp"] == 67000.0
    assert data["resistanceMap"]["tpClearsResistance"] is True


def test_context_diagnostics_forex_engine_b_missing_not_optional():
    ctx = _engine_a_ctx(asset_group="forex")
    ctx["screenshot_overlays"] = []
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "CAUTION",
                "confidence": 55,
                "human_action": "wait",
                "missing_context": ["engineB.score"],
            }
        )
    )
    diag = build_context_diagnostics(ctx, ai)
    assert "engineB.score" not in diag["contextCompleteness"]["missingOptional"]
    na_labels = diag["contextCompleteness"]["notApplicable"]
    assert any("Engine B" in label or "Funding" in label for label in na_labels)


def test_context_diagnostics_crypto_equity_session_not_applicable_does_not_penalize():
    ctx = _engine_a_ctx()
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "VALID",
                "confidence": 80,
                "human_action": "take",
                "missing_context": ["equity_session multiplier unavailable / not applied"],
            }
        )
    )
    diagnostics = build_context_diagnostics(ctx, ai)
    assert diagnostics["contextCompleteness"]["score"] == 100
    assert diagnostics["contextCompleteness"]["status"] == "complete"
    assert diagnostics["missingContextDetailed"]["required"] == []
    assert diagnostics["missingContextDetailed"]["optional"] == []
    assert diagnostics["missingContextDetailed"]["notApplicable"] == [
        {
            "key": "equity_session",
            "label": "Equity session multiplier",
            "reason": "asset_group crypto does not use equity session multiplier",
        }
    ]


def test_context_diagnostics_funding_oi_reference_without_numbers_is_missing_optional():
    ctx = _engine_a_ctx(funding_oi={})
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "CAUTION",
                "confidence": 55,
                "human_action": "wait",
                "supporting_reasons": ["Funding/OI add-on shows ok"],
            }
        )
    )
    diagnostics = build_context_diagnostics(ctx, ai)
    assert diagnostics["fundingOi"]["fundingRate"] is None
    assert "funding_oi_numeric" in [item["key"] for item in diagnostics["missingContextDetailed"]["optional"]]
    assert "Funding/OI numeric values" in diagnostics["contextCompleteness"]["missingOptional"]


def test_context_diagnostics_required_missing_reduces_tradeability():
    ctx = _engine_a_ctx()
    ai_base = normalize_chart_review_response(
        json.dumps({"verdict": "VALID", "confidence": 80, "human_action": "take"})
    )
    ai_missing = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "VALID",
                "confidence": 80,
                "human_action": "take",
                "missing_context": ["No higher-TF resistance map to validate TP 67000"],
            }
        )
    )
    concordance = compute_engine_a_ai_concordance(ctx, ai_base)
    meta = build_provider_meta(provider="anthropic", model="claude-opus-4-7")
    base = build_ai_review_summary(ctx, ai_base, concordance, meta)
    missing = build_ai_review_summary(ctx, ai_missing, concordance, meta)
    assert missing["tradeabilityScore"] < base["tradeabilityScore"]


def test_summary_engine_a_from_context():
    ctx = _engine_a_ctx()
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    ai = normalize_chart_review_response(
        json.dumps({"verdict": "VALID", "confidence": 80, "human_action": "take"})
    )
    concordance = compute_engine_a_ai_concordance(ctx, ai)
    meta = build_provider_meta(provider="anthropic", model="claude-opus-4-7")
    summary = build_ai_review_summary(ctx, ai, concordance, meta, engine_snapshots=ctx.get("engine_snapshots"))
    assert summary["engineA"]["score"] == 2.4
    assert summary["engineA"]["threshold"] == 2.0
    assert summary["engineA"]["passed"] is True


def test_summary_engine_a_missing_null():
    ctx = _engine_a_ctx(threshold=None)
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    ai = normalize_chart_review_response(
        json.dumps({"verdict": "CAUTION", "confidence": 50, "human_action": "wait"})
    )
    concordance = compute_engine_a_ai_concordance(ctx, ai)
    meta = build_provider_meta(provider="anthropic", model="claude-opus-4-7")
    summary = build_ai_review_summary(ctx, ai, concordance, meta, engine_snapshots=ctx["engine_snapshots"])
    assert summary["engineA"]["threshold"] is None


def test_provider_status_not_success_on_missing_key(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 503
    data = resp.get_json()
    assert data.get("provider_status") != "success"
    assert data.get("provider_status") == "failed_auth"
    assert "ai_review_summary" not in data


def test_parse_fallback_provider_status(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(raw_text="not json at all"),
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    summary = resp.get_json().get("ai_review_summary")
    assert summary["providerStatus"] == "fallback_used"
    assert summary["fallbackUsed"] is True
    assert summary["model"] == "deterministic_normalizer"
    assert "opus" not in summary["model"].lower()


def test_engine_a_verdict_wait_extension_downgrade():
    ctx = _engine_a_ctx(passed=True)
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "CAUTION",
                "confidence": 55,
                "human_action": "wait",
                "visual_confirmation": "direction ok",
                "visual_contradiction": "",
                "engine_a_alignment": "aligned with engine",
                "entry_quality": "resistance cluster entry, poor timing extended above VWAP",
                "atr_rr_assessment": "acceptable",
                "freshness_assessment": "fresh",
                "supporting_reasons": [],
                "risks": ["late entry", "compression"],
                "missing_context": [],
            }
        )
    )
    comparison = build_engine_a_verdict_comparison(ctx, ai, engine_snapshots=ctx["engine_snapshots"])
    assert comparison["engineABiasValid"] is True
    assert comparison["chartConfirmsEngineADirection"] is True
    assert comparison["chartContradictsEntryTiming"] is True
    assert comparison["aiDowngradedEngineA"] is True
    assert comparison["comparisonVerdict"] == "engine_a_direction_confirmed_entry_rejected"
    assert comparison["finalDecision"] == "wait"


def test_wait_high_alignment_low_tradeability():
    ctx = _engine_a_ctx(passed=True)
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "CAUTION",
                "confidence": 55,
                "human_action": "wait",
                "visual_confirmation": "direction ok",
                "visual_contradiction": "",
                "engine_a_alignment": "aligned with engine",
                "entry_quality": "resistance cluster entry, poor timing",
                "atr_rr_assessment": "acceptable",
                "freshness_assessment": "fresh",
                "supporting_reasons": [],
                "risks": ["late entry", "compression"],
                "missing_context": [],
            }
        )
    )
    concordance = compute_engine_a_ai_concordance(ctx, ai)
    meta = build_provider_meta(provider="anthropic", model="claude-opus-4-7")
    summary = build_ai_review_summary(ctx, ai, concordance, meta, engine_snapshots=ctx["engine_snapshots"])
    assert summary["humanAction"] == "wait"
    assert summary["engineAlignmentScore"] > summary["tradeabilityScore"]


def test_summary_engine_bcd_null_safe():
    snapshots = extract_engine_snapshots({}, _engine_a_ctx())
    summary = build_ai_review_summary(
        _engine_a_ctx(),
        normalize_chart_review_response(json.dumps({"verdict": "CAUTION", "confidence": 40, "human_action": "wait"})),
        compute_engine_a_ai_concordance(
            _engine_a_ctx(),
            normalize_chart_review_response(json.dumps({"verdict": "CAUTION", "confidence": 40, "human_action": "wait"})),
        ),
        build_provider_meta(provider="anthropic", model="m"),
        engine_snapshots=snapshots,
    )
    assert summary["engineB"]["score"] is None
    assert summary["engineC"]["decisionState"] is None
    assert summary["engineD"]["setupType"] is None


def test_apply_parse_fallback_overrides_model():
    meta = build_provider_meta(provider="anthropic", model="claude-opus-4-7")
    ai = normalize_chart_review_response("broken")
    out = apply_parse_fallback(meta, ai)
    assert out["provider_status"] == "fallback_used"
    assert out["fallback_used"] is True
    assert out["model"] == "deterministic_normalizer"


def test_dedup_recomputes_summary(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request()
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ):
        first = client.post("/api/ai/chart-review", json=body)
        second = client.post("/api/ai/chart-review", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json().get("ai_review_summary") is not None
    assert second.get_json()["ai_review_summary"]["providerStatus"] == "success"


def test_resolve_chart_review_analyze_style_maps_h4_to_intraday():
    from ai_review.engine_a_context import resolve_chart_review_analyze_style

    assert resolve_chart_review_analyze_style("H4", {"chart_timeframe": "H4"}, {"type": "crypto"}) == "intraday"
    assert resolve_chart_review_analyze_style("H4", {"chart_timeframe": "M1"}, {"type": "crypto"}) == "scalp"
    assert resolve_chart_review_analyze_style("D1", {"chart_timeframe": "D1"}, {"type": "stock"}) == "swing"


def test_build_engine_a_prompt_context_includes_factor_scores():
    from ai_review.engine_a_context import build_engine_a_prompt_context

    ctx = _engine_a_ctx()
    ctx["factor_diagnostics"] = {
        **ctx["factor_diagnostics"],
        "factorScores": {"trend": 0.82, "momentum": 0.61, "addon": 0.15},
    }
    ctx["indicator_snapshots"] = {"rsi": 54.2}
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    prompt_ctx = build_engine_a_prompt_context(ctx)
    assert prompt_ctx["diagnostics"]["trendScore"] == 0.82
    assert prompt_ctx["diagnostics"]["momentumScore"] == 0.61
    assert prompt_ctx["diagnostics"]["addonScore"] == 0.15
    assert prompt_ctx["diagnostics"]["rsi"] == 54.2


def test_build_engine_b_prompt_context_from_structure():
    from ai_review.engine_a_context import build_engine_b_prompt_context

    ctx = _engine_a_ctx()
    ctx["structure_context"] = {
        "structural_verdict": "CLEAR",
        "bos_confirmed": True,
        "nearest_resistance_zone": {"level": 67000.0},
    }
    ctx["engine_snapshots"] = {
        "engineB": {
            "score": 3.5,
            "maxScore": 5.0,
            "threshold": 3.0,
            "passed": True,
            "direction": "LONG",
        }
    }
    eb_ctx = build_engine_b_prompt_context(ctx)
    assert eb_ctx["passed"] is True
    assert eb_ctx["nearestResistance"] == 67000.0
    assert eb_ctx["structuralVerdict"] == "CLEAR"


def test_default_resolve_pair_unknown_symbol_returns_none(monkeypatch):
    fake_athena = ModuleType("athena")
    fake_athena.ALL_PAIRS = []
    fake_athena.CONFIG = {"EXCHANGE_SOURCE": "binance"}
    monkeypatch.setitem(sys.modules, "athena", fake_athena)

    from ai_review.engine_a_context import _default_resolve_pair

    assert _default_resolve_pair("NOTAREALSYMBOL999") is None


def test_strategy_layer_receives_engine_b_summary():
    from ai_review.engine_a_context import build_engine_b_summary_for_strategy

    ctx = _engine_a_ctx()
    ctx["structure_context"] = {"structural_verdict": "CLEAR"}
    ctx["engine_snapshots"] = {"engineB": {"score": 4.0, "passed": True, "direction": "LONG"}}
    summary = build_engine_b_summary_for_strategy(ctx)
    assert summary["available"] is True
    assert summary["structural_verdict"] == "CLEAR"


def test_price_action_facts_reads_flat_engine_b_poc_keys():
    from athena_ai.price_action_facts import derive_price_action_facts

    facts = derive_price_action_facts(
        engine_a_ctx={
            "structure_context": {
                "prev_session_poc": 65000.0,
                "prev_session_vah": 65500.0,
                "prev_session_val": 64500.0,
            },
            "atr": {"atr_value": 500.0, "atr_chart_tf": 500.0},
        },
        ohlcv_window=[{"open": 65100, "high": 65200, "low": 65050, "close": 65150}],
    )
    assert facts["profile_location"]["poc"] == 65000.0
    assert facts["profile_location"]["confidence"] in ("high", "medium", "low")


def test_prompt_includes_engine_b_context_block():
    from ai_review.prompt_builder import build_chart_review_prompt

    ctx = _engine_a_ctx()
    ctx["structure_context"] = {"structural_verdict": "CLEAR", "bos_confirmed": True}
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    prompt = build_chart_review_prompt(ctx)
    assert "engineBContext" in prompt
    assert "engineAContext" in prompt


def test_prompt_includes_trade_skill_version_and_playbooks():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "tradeSkillVersion" in prompt
    assert "athena_trade_skill.v1" in prompt
    assert "ATHENA TRADE PLAYBOOKS" in prompt
    assert "Engine A Confluence" in prompt or "engine" in prompt.lower()


def test_normalizer_direction_valid_timing_poor_entry_not_allowed():
    raw = json.dumps({
        "verdict": "CAUTION",
        "confidence": 72,
        "human_action": "wait",
        "decision": "WAIT_FOR_PULLBACK",
        "direction": "LONG",
        "entryAllowedNow": True,
        "waitReason": "Extended entry — wait for pullback",
        "engineAVerdictComparison": {
            "comparisonVerdict": "engine_a_direction_confirmed_entry_rejected",
            "chartContradictsEntryTiming": True,
        },
    })
    out = normalize_chart_review_response(raw)
    assert out["entryAllowedNow"] is False
    assert out["decision"] == "WAIT_FOR_PULLBACK"


def test_normalizer_sanitizes_suggested_trade_plan():
    raw = json.dumps({
        "verdict": "CAUTION",
        "confidence": 60,
        "human_action": "wait",
        "decision": "WAIT_FOR_PULLBACK",
        "direction": "LONG",
        "entryAllowedNow": False,
        "suggestedTradePlan": {
            "schemaVersion": "suggested_trade_plan.v1",
            "armable": True,
            "source": "ai_chart_review",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "action": "WAIT_FOR_LEVEL",
            "triggerType": "ACCEPTANCE_ABOVE",
            "level": 65000,
            "expiresInSeconds": 1200,
        },
    })
    out = normalize_chart_review_response(raw)
    plan = out.get("suggestedTradePlan")
    assert isinstance(plan, dict)
    assert plan.get("armable") is True
    assert plan.get("level") == 65000


def test_normalizer_strips_malformed_suggested_trade_plan():
    raw = json.dumps({
        "verdict": "CAUTION",
        "confidence": 40,
        "human_action": "wait",
        "decision": "WAIT_FOR_ACCEPTANCE",
        "direction": "LONG",
        "entryAllowedNow": False,
        "suggestedTradePlan": {
            "direction": "MAYBE",
            "action": "WAIT_FOR_LEVEL",
            "triggerType": "ACCEPTANCE_ABOVE",
        },
    })
    out = normalize_chart_review_response(raw)
    plan = out.get("suggestedTradePlan")
    assert isinstance(plan, dict)
    assert plan.get("armable") is False

