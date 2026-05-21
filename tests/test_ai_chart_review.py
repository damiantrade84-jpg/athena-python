"""Tests for AI chart review v1 backend."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
from types import SimpleNamespace
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
from ai_review.summary import build_ai_review_summary
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
                }
            },
            "atrDiagnostics": {
                "atr_value": 1200.0,
                "atr_tf": "H4",
                "atr_source": "h4",
                "atr_age_seconds": 7200.0,
                "atr_confirmed_only": True,
            },
            "dataFreshness": {"allowed": True},
            "h1Candles": [{"time": "2026-05-21T16:00:00+00:00"}],
            "h4Candles": [{"time": "2026-05-21T16:00:00+00:00"}],
            "d1Candles": [{"time": "2026-05-20T00:00:00+00:00"}],
            "candleFetchMeta": {"pairSource": "binance"},
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


def test_prompt_threshold_not_hardcoded():
    source = open(
        os.path.join(os.path.dirname(__file__), "..", "ai_review", "prompt_builder.py"),
        encoding="utf-8",
    ).read()
    for line in source.splitlines():
        if "threshold:" in line.lower():
            assert "1.5" not in line and "2.0" not in line and "1.7" not in line


def test_prompt_includes_factor_diagnostics():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "trendCoherence" in prompt


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
    for key in (
        "provider",
        "model",
        "providerStatus",
        "fallbackUsed",
        "humanAction",
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
