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
from config import CONFIG, get_ai_model

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


def _make_app(tmp_db: str, enabled: bool = True, openai_enabled: bool = True, primary_engine: str = "A"):
    app = Flask(__name__)
    cfg = dict(CONFIG)
    cfg["AI_REVIEW_PROVIDER"] = "claude"
    cfg["AI_REVIEW_FALLBACK_PROVIDERS"] = ""
    ai_cfg = dict(cfg["AI_CHART_REVIEW"])
    ai_cfg["ENABLED"] = enabled
    ai_cfg["PRIMARY_ENGINE"] = primary_engine
    cfg["OPENAI_REVIEW_ENABLED"] = openai_enabled
    ai_cfg["OPENAI_REVIEW_ENABLED"] = openai_enabled
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

    def _naked_analysis(sig, overlay_only=False):
        return {
            "score": 4.5,
            "max_possible": 6.0,
            "min_score_used": 4.0,
            "passed": True,
            "checklist_passed": True,
            "current_price": 65000.0,
            "recommended_stop_loss": 63000.0,
            "recommended_take_profit": 68000.0,
            "rr_used_for_gate": 1.8,
            "structural_verdict": "CLEAR",
            "regime": "TRENDING",
            "atr_source": "candle_atr_tf",
        }, {"symbol": sig.get("symbol"), "display": sig.get("pair"), "type": "crypto", "source": "binance"}, None

    runtime = SimpleNamespace(
        CONFIG=cfg,
        AUDIT_DB=tmp_db,
        json_safe=lambda x: x,
        log=MagicMock(),
        resolve_pair_fn=_resolve,
        analyze_pair_fn=_analyze,
        btc_bias_fn=lambda: "neutral",
        naked_analysis_fn=_naked_analysis,
        last_engine_b_rows_fn=lambda: [],
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


def test_route_rejects_extra_client_score_keys(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(confluence_score=99, passed=True)
    with patch("ai_review.providers.anthropic_provider.call_anthropic_chart_review") as mock_call:
        resp = client.post("/api/ai/chart-review", json=body)
        assert resp.status_code == 400
        assert "unexpected request keys" in resp.get_json()["error"]
        mock_call.assert_not_called()


def test_route_rejects_top_level_primary_engine_key(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(primary_engine="B")
    with patch("ai_review.providers.anthropic_provider.call_anthropic_chart_review") as mock_call:
        resp = client.post("/api/ai/chart-review", json=body)
        assert resp.status_code == 400
        assert "unexpected request keys" in resp.get_json()["error"]
        mock_call.assert_not_called()


def test_route_rejects_non_png_data_url(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(screenshot_base64="data:image/jpeg;base64,abc")
    resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 415


def test_route_rejects_openai_when_disabled(tmp_audit_db):
    app = _make_app(tmp_audit_db, openai_enabled=False)
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


def test_prompt_includes_chart_capture_metadata():
    prompt = build_chart_review_prompt(
        _engine_a_ctx(
            screenshot_overlays=["candles", "engine_b", "vwap"],
            chart_snapshot={
                "visibleCandleCount": 120,
                "visibleRange": {"from": "100", "to": "220"},
                "renderedLayers": ["candles", "engine_b", "vwap"],
                "engineBOverlayCount": 4,
                "engineBOverlayStatus": "ready",
                "indicatorLayerStates": {"engineB": True, "vwap": True},
            },
        )
    )

    assert "== CHART CAPTURE METADATA ==" in prompt
    assert "rendered_layers: candles, engine_b, vwap" in prompt
    assert "visible_candle_count: 120" in prompt
    assert "engine_b_overlay_status: ready" in prompt
    assert "engine_b_overlay_count: 4" in prompt


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


def _strategy_playbook_verdict(**overrides):
    payload = {
        "final_verdict": "NO_TRADE",
        "matched_strategy_model": "NONE",
        "direction": "NONE",
        "location_quality": "UNKNOWN",
        "trigger_quality": "MISSING",
        "rr_quality": "UNKNOWN",
        "classifier_agreement": "AGREE",
        "plain_english_reason": "No clean playbook setup is confirmed.",
    }
    payload.update(overrides)
    return payload


def test_normalizer_preserves_valid_strategy_playbook_verdict():
    verdict = _strategy_playbook_verdict()
    raw = json.dumps(
        {
            "decision": "WATCH_ONLY",
            "direction": "NEUTRAL",
            "confidence": 40,
            "entryAllowedNow": False,
            "chartReadSummary": "No confirmed setup.",
            "strategyPlaybookVerdict": verdict,
        }
    )
    out = normalize_chart_review_response(raw)

    assert out["strategyPlaybookVerdict"] == verdict
    assert out["structured"]["strategyPlaybookVerdict"] == verdict


def test_normalizer_rejects_invalid_strategy_playbook_verdict():
    raw = json.dumps(
        {
            "decision": "WATCH_ONLY",
            "direction": "NEUTRAL",
            "confidence": 40,
            "entryAllowedNow": False,
            "chartReadSummary": "No confirmed setup.",
            "strategyPlaybookVerdict": _strategy_playbook_verdict(
                matched_strategy_model="MADE_UP_MODEL"
            ),
        }
    )
    out = normalize_chart_review_response(raw)

    assert "strategyPlaybookVerdict" not in out
    assert "strategyPlaybookVerdict" not in out["structured"]
    assert "invalid_strategy_playbook_verdict" in out["tradeSkillWarnings"]


def test_normalizer_rejects_wrong_type_strategy_playbook_verdict():
    raw = json.dumps(
        {
            "decision": "WATCH_ONLY",
            "direction": "NEUTRAL",
            "confidence": 40,
            "entryAllowedNow": False,
            "chartReadSummary": "No confirmed setup.",
            "strategyPlaybookVerdict": "NONE",
        }
    )
    out = normalize_chart_review_response(raw)

    assert "strategyPlaybookVerdict" not in out
    assert "strategyPlaybookVerdict" not in out["structured"]
    assert "invalid_strategy_playbook_verdict" in out["tradeSkillWarnings"]


def test_normalizer_caution_on_parse_fail():
    out = normalize_chart_review_response("not json")
    assert out["verdict"] == "CAUTION"
    assert out["confidence"] == 0
    assert out["human_action"] == "wait"
    assert "parse failed" in out["risks"][0].lower()
    assert out["raw_model_response"] == "not json"


def test_concordance_agree_when_passed_and_valid():
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "VALID",
                "confidence": 80,
                "human_action": "take",
                "direction": "LONG",
            }
        )
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
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "VALID",
                "confidence": 80,
                "human_action": "take",
                "direction": "LONG",
            }
        )
    )
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


def test_dedup_skips_cache_when_requested_provider_differs(tmp_audit_db):
    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body_claude = _base_request(provider="claude")
    body_openai = _base_request(provider="openai")
    chart_cfg = dict(CONFIG["AI_CHART_REVIEW"])
    chart_cfg["OPENAI_REVIEW_ENABLED"] = True
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ) as mock_claude, patch(
        "ai_review.providers.router.call_openai_chart_review",
        return_value=_mock_provider_payload(
            raw_text='{"verdict":"VALID","confidence":80,"human_action":"take"}',
            provider="openai",
            model="gpt-5.5",
        ),
    ) as mock_openai, patch.dict(
        CONFIG,
        {"AI_REVIEW_FALLBACK_PROVIDERS": "", "AI_CHART_REVIEW": chart_cfg},
        clear=False,
    ):
        first = client.post("/api/ai/chart-review", json=body_claude)
        assert first.status_code == 200
        second = client.post("/api/ai/chart-review", json=body_openai)
        assert second.status_code == 200
        assert second.get_json()["dedup_hit"] is False
        assert mock_claude.call_count == 1
        assert mock_openai.call_count == 1


def test_router_default_resolves_to_global_provider():
    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    with patch(
        "ai_review.providers.router.call_openai_chart_review",
        return_value=_mock_provider_payload(raw_text="{}", provider="openai", model="gpt-5.5"),
    ) as mock_openai:
        with patch.dict(CONFIG, {"AI_REVIEW_PROVIDER": "openai", "AI_REVIEW_FALLBACK_PROVIDERS": ""}, clear=False):
            out = run_chart_review("default", payload)
        mock_openai.assert_called_once()
    assert out["provider"] == "openai"


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
    assert out["provider"] == "grok"
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


def test_router_resolves_openai_provider():
    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    with patch(
        "ai_review.providers.router.call_openai_chart_review",
        return_value=_mock_provider_payload(
            raw_text="{}",
            provider="openai",
            model="gpt-5.5",
        ),
    ) as mock_openai:
        out = run_chart_review("openai", payload)
        mock_openai.assert_called_once()
    assert out["provider"] == "openai"
    assert out["model"] == "gpt-5.5"


def test_xai_provider_missing_key_fails_closed(monkeypatch):
    from ai_review.providers.xai_provider import call_xai_chart_review
    from ai_review.provider_meta import ProviderChartReviewError

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    with patch.dict(CONFIG, {"XAI_API_KEY": "", "MOONSHOT_API_KEY": ""}, clear=False):
        with pytest.raises(ProviderChartReviewError) as excinfo:
            call_xai_chart_review(payload)
    assert excinfo.value.provider_status == "failed_auth"
    assert excinfo.value.provider == "grok"


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
    assert kwargs["model"] == get_ai_model(
        CONFIG,
        preferred_key="AI_CHART_REVIEW_XAI_MODEL",
        fallback="grok-4.3",
        provider="grok",
    )
    assert kwargs["max_tokens"] == CONFIG["AI_CHART_REVIEW"]["XAI_MAX_TOKENS"]
    assert "response_format" not in kwargs
    assert out["provider"] == "grok"
    assert out["raw_text"].startswith('{"verdict"')


def test_xai_provider_extracts_list_block_content(monkeypatch):
    from ai_review.providers.xai_provider import call_xai_chart_review

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review this chart"
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.model = "grok-4.3"
    mock_choice = MagicMock()
    mock_choice.message.content = [
        {"type": "text", "text": '{"verdict":"VALID","confidence":70,"human_action":"take"}'}
    ]
    mock_resp.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch(
        "ai_review.providers.xai_provider.create_ai_client",
        return_value=mock_client,
    ):
        out = call_xai_chart_review(payload)

    assert out["raw_text"].startswith('{"verdict"')


def test_xai_provider_empty_content_raises(monkeypatch):
    from ai_review.providers.xai_provider import call_xai_chart_review
    from ai_review.provider_meta import ProviderChartReviewError

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review this chart"
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.model = "grok-4.3"
    mock_choice = MagicMock()
    mock_choice.message.content = ""
    mock_resp.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch(
        "ai_review.providers.xai_provider.create_ai_client",
        return_value=mock_client,
    ):
        with pytest.raises(ProviderChartReviewError) as excinfo:
            call_xai_chart_review(payload)

    assert excinfo.value.provider_status == "empty_response"
    assert excinfo.value.provider == "grok"


def test_xai_provider_fenced_json_parses_through_normalizer(monkeypatch):
    from ai_review.providers.xai_provider import call_xai_chart_review

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review this chart"
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")

    fenced = (
        '```json\n'
        '{"verdict":"VALID","confidence":82,"human_action":"take","direction":"LONG"}\n'
        '```'
    )
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.model = "grok-4.3"
    mock_choice = MagicMock()
    mock_choice.message.content = fenced
    mock_resp.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch(
        "ai_review.providers.xai_provider.create_ai_client",
        return_value=mock_client,
    ):
        out = call_xai_chart_review(payload)

    normalized = normalize_chart_review_response(out["raw_text"])
    assert normalized["parse_success"] is True
    assert normalized["verdict"] == "VALID"


def test_xai_provider_json_mode_is_config_gated(monkeypatch):
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

    chart_cfg = dict(CONFIG["AI_CHART_REVIEW"])
    chart_cfg["XAI_JSON_MODE"] = True
    with patch(
        "ai_review.providers.xai_provider.create_ai_client",
        return_value=mock_client,
    ), patch.dict(CONFIG, {"AI_CHART_REVIEW": chart_cfg}, clear=False):
        call_xai_chart_review(payload)

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


def test_openai_provider_missing_key_fails_closed(monkeypatch):
    from ai_review.provider_meta import ProviderChartReviewError
    from ai_review.providers.openai_provider import call_openai_chart_review

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch.dict(CONFIG, {"OPENAI_API_KEY": "", "OPENAI_REVIEW_ENABLED": True}, clear=False):
        with pytest.raises(ProviderChartReviewError) as excinfo:
            call_openai_chart_review(payload)
    assert excinfo.value.provider_status == "failed_auth"
    assert excinfo.value.provider == "openai"
    assert "OPENAI_API_KEY" in str(excinfo.value)


def test_openai_provider_builds_responses_payload_with_reasoning_and_image():
    from ai_review.providers.openai_provider import build_openai_responses_payload

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review this chart"
    cfg = {
        "OPENAI_REVIEW_MODEL": "gpt-5.5",
        "OPENAI_REVIEW_REASONING_EFFORT": "xhigh",
        "OPENAI_REVIEW_MAX_OUTPUT_TOKENS": 12000,
        "AI_CHART_REVIEW": {"OPENAI_MODEL": "gpt-5.5"},
    }
    out = build_openai_responses_payload(payload, cfg=cfg)

    assert out["model"] == "gpt-5.5"
    assert out["reasoning"] == {"effort": "xhigh"}
    assert out["max_output_tokens"] == 12000
    content = out["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "review this chart"}
    assert content[1] == {"type": "input_image", "image_url": payload.screenshot_base64}


def test_openai_provider_disables_sdk_retries_for_chart_review(monkeypatch):
    from ai_review.providers.openai_provider import call_openai_chart_review

    payload = MagicMock()
    payload.screenshot_base64 = _png_data_url()
    payload.prompt = "review this chart"
    calls = {}

    class _FakeResponses:
        def create(self, **kwargs):
            calls["create_kwargs"] = kwargs
            return SimpleNamespace(
                output_text='{"verdict":"VALID","confidence":80,"human_action":"take"}',
                model="gpt-5.5",
            )

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            calls["client_kwargs"] = kwargs
            self.responses = _FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    with patch.dict(
        CONFIG,
        {
            "OPENAI_REVIEW_ENABLED": True,
            "OPENAI_REVIEW_TIMEOUT_SECONDS": 7,
            "OPENAI_REVIEW_SDK_MAX_RETRIES": 0,
            "AI_CHART_REVIEW": {"OPENAI_MODEL": "gpt-5.5"},
        },
        clear=False,
    ):
        out = call_openai_chart_review(payload)

    assert calls["client_kwargs"]["max_retries"] == 0
    assert calls["create_kwargs"]["timeout"] == 7.0
    assert out["provider"] == "openai"


def test_validate_request_disabled_provider():
    cfg = dict(CONFIG["AI_CHART_REVIEW"])
    cfg["OPENAI_REVIEW_ENABLED"] = False
    err = validate_request(_base_request(provider="openai"), cfg)
    assert err is not None
    assert err.status == 403


def test_validate_request_uses_canonical_openai_enabled_over_legacy_allow_flag():
    cfg = dict(CONFIG["AI_CHART_REVIEW"])
    cfg["OPENAI_REVIEW_ENABLED"] = True
    cfg["ALLOW_OPENAI_PROVIDER"] = False
    err = validate_request(_base_request(provider="openai"), cfg)
    assert err is None


def test_summary_includes_provider_failure_when_fallback_used(tmp_audit_db):
    from ai_review.provider_meta import ProviderChartReviewError

    app = _make_app(tmp_audit_db)
    client = app.test_client()
    body = _base_request(provider="openai")
    openai_error = ProviderChartReviewError(
        "OPENAI_API_KEY not configured",
        provider_status="failed_auth",
        provider="openai",
    )
    chart_cfg = dict(CONFIG["AI_CHART_REVIEW"])
    chart_cfg["OPENAI_REVIEW_ENABLED"] = True
    with patch(
        "ai_review.providers.router.call_openai_chart_review",
        side_effect=openai_error,
    ), patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ), patch.dict(
        CONFIG,
        {
            "AI_REVIEW_FALLBACK_PROVIDERS": "claude",
            "AI_CHART_REVIEW": chart_cfg,
        },
        clear=False,
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["fallbackUsed"] is True
    assert data["selectedProvider"] == "openai"
    assert data["providerFailure"]["provider"] == "openai"
    summary = data.get("aiReviewSummary") or data.get("ai_review_summary")
    assert summary is not None
    assert summary["selectedProvider"] == "openai"
    assert summary["fallbackUsed"] is True
    assert summary["providerFailure"]["provider"] == "openai"
    assert "OPENAI_API_KEY" in summary["providerFailure"]["error"]


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


def test_context_diagnostics_forex_profile_levels_untrusted(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES",
        ["crypto", "stock"],
    )
    from market_structure import sanitize_engine_b_structure_profile_fields

    ctx = _engine_a_ctx(asset_group="forex")
    ctx["asset_class"] = "forex"
    ctx["structure_context"] = sanitize_engine_b_structure_profile_fields(
        {
            "prev_session_poc": 65150.0,
            "prev_session_vah": 66200.0,
            "prev_session_val": 64200.0,
        },
        "forex",
    )
    diag = build_context_diagnostics(ctx, {"missing_context": []})
    assert diag["resistanceMap"]["profileLevelsTrusted"] is False
    assert diag["resistanceMap"]["profileLevels"]["poc"] is None
    assert any(
        "volume profile" in label.lower()
        for label in diag["contextCompleteness"]["notApplicable"]
    )


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
    with patch.dict(CONFIG, {"AI_REVIEW_FALLBACK_PROVIDERS": ""}, clear=False):
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


def test_engine_a_verdict_wait_without_timing_contradiction_is_entry_rejected():
    """Wait/watch must not classify as engine_a_confirmed when timing is not flagged."""
    ctx = _engine_a_ctx(passed=True)
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "CAUTION",
                "confidence": 60,
                "human_action": "wait",
                "visual_confirmation": "direction aligned with engine bias",
                "visual_contradiction": "",
                "engine_a_alignment": "aligned with engine",
                "entry_quality": "acceptable location, wait for pullback",
                "atr_rr_assessment": "acceptable",
                "freshness_assessment": "fresh",
                "supporting_reasons": ["trend intact"],
                "risks": ["near-term extension"],
                "missing_context": [],
            }
        )
    )
    comparison = build_engine_a_verdict_comparison(ctx, ai, engine_snapshots=ctx["engine_snapshots"])
    assert comparison["chartConfirmsEngineADirection"] is True
    assert comparison["chartContradictsEntryTiming"] is not True
    assert comparison["comparisonVerdict"] == "engine_a_direction_confirmed_entry_rejected"
    assert comparison["finalDecision"] == "wait"


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


def test_engine_a_verdict_invalid_model_final_decision_falls_back_to_human_action():
    ctx = _engine_a_ctx(passed=True)
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "INVALID",
                "confidence": 35,
                "human_action": "reject",
                "decision": "NO_TRADE",
                "direction": "SHORT",
                "entryAllowedNow": False,
                "noTradeReason": "Stale HTF data and extended timing",
                "visual_confirmation": "direction aligned",
                "visual_contradiction": "entry timing contradicted after sharp drop",
                "engine_a_alignment": "direction aligned but execution context invalid",
                "entry_quality": "extended entry timing after sharp drop",
                "risks": ["stale HTF data", "late entry"],
                "engineAVerdictComparison": {
                    "comparisonVerdict": "engine_a_direction_confirmed_entry_rejected",
                    "chartConfirmsEngineADirection": True,
                    "chartContradictsEngineADirection": False,
                    "chartConfirmsEntryTiming": False,
                    "chartContradictsEntryTiming": True,
                    "finalDecision": "unknown",
                    "finalReason": "Direction aligned but execution context invalid",
                },
            }
        )
    )

    comparison = build_engine_a_verdict_comparison(ctx, ai, engine_snapshots=ctx["engine_snapshots"])

    assert comparison["finalDecision"] == "reject"
    assert comparison["comparisonVerdict"] == "engine_a_direction_confirmed_entry_rejected"


def test_engine_b_structured_verdict_comparison_survives_normalization():
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "INVALID",
                "confidence": 72,
                "human_action": "reject",
                "engine_b_alignment": "BOS confirms Engine B direction",
                "engineBVerdictComparison": {
                    "chartConfirmsEngineBDirection": True,
                    "chartContradictsEngineBDirection": False,
                    "chartConfirmsEntryTiming": False,
                    "chartContradictsEntryTiming": True,
                    "aiAgreesWithEngineB": False,
                    "comparisonVerdict": "engine_b_direction_confirmed_entry_rejected",
                    "finalDecision": "reject",
                    "finalReason": "Direction confirmed, but entry timing is invalid",
                },
            }
        ),
        review_type="engine_b_chart",
    )

    comparison = ai["structured"]["engineBVerdictComparison"]

    assert comparison["chartConfirmsEngineBDirection"] is True
    assert comparison["chartContradictsEngineBDirection"] is False
    assert comparison["chartConfirmsEntryTiming"] is False
    assert comparison["chartContradictsEntryTiming"] is True
    assert comparison["aiAgreesWithEngineB"] is False


def test_concordance_prefers_structured_entry_timing_over_visual_contradiction_text():
    ctx = _engine_a_ctx(passed=True)
    ai = normalize_chart_review_response(
        json.dumps(
            {
                "verdict": "INVALID",
                "confidence": 35,
                "human_action": "reject",
                "decision": "NO_TRADE",
                "direction": "SHORT",
                "entryAllowedNow": False,
                "noTradeReason": "Entry extended after sharp drop",
                "visual_confirmation": "direction aligned",
                "visual_contradiction": "entry timing contradicted after sharp drop",
                "entry_quality": "extended entry timing after sharp drop",
                "risks": ["late entry"],
                "engineAVerdictComparison": {
                    "chartConfirmsEngineADirection": True,
                    "chartContradictsEngineADirection": False,
                    "chartContradictsEntryTiming": True,
                },
            }
        )
    )

    concordance = compute_engine_a_ai_concordance(ctx, ai, cfg={})

    assert concordance["concordance"] == "disagree"
    assert concordance["divergence_type"] == "entry_displacement"


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


def test_route_response_exposes_non_visual_context_and_score_attribution(tmp_audit_db):
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
    assert data["nonVisualContext"] == data["engineANonVisualContext"]
    assert data["scoreAttribution"] == data["engineAScoreAttribution"]
    assert data["scoreAttribution"]["aiReviewCanMutateScore"] is False
    assert data["engine_a_context"]["score_attribution"]["aiReviewCanMutateScore"] is False
    assert "derivativesContext" in data
    assert data["derivativesContext"]["fundingRate"] == pytest.approx(0.0001)


def test_route_dedup_response_exposes_non_visual_context_and_score_attribution(tmp_audit_db):
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
    data = second.get_json()
    assert data["dedup_hit"] is True
    assert data["nonVisualContext"] == data["engineANonVisualContext"]
    assert data["scoreAttribution"] == data["engineAScoreAttribution"]
    assert data["scoreAttribution"]["finalEngineAScore"] == data["engine_a_context"]["confluence_score"]


def test_resolve_chart_review_analyze_style_does_not_infer_style_from_chart_tf():
    from ai_review.engine_a_context import resolve_chart_review_analyze_style

    assert resolve_chart_review_analyze_style("H4", {"chart_timeframe": "H4"}, {"type": "crypto"}) == "intraday"
    assert resolve_chart_review_analyze_style("H4", {"chart_timeframe": "M1"}, {"type": "crypto"}) == "intraday"
    assert resolve_chart_review_analyze_style("M1", {"signal_style": "scalp"}, {"type": "crypto"}) == "scalp"
    assert resolve_chart_review_analyze_style("D1", {"chart_timeframe": "D1"}, {"type": "stock"}) == "swing"


def test_build_engine_a_prompt_context_includes_factor_scores():
    from ai_review.engine_a_context import build_engine_a_prompt_context

    ctx = _engine_a_ctx()
    ctx["factor_diagnostics"] = {
        **ctx["factor_diagnostics"],
        "factorScores": {"trend": 0.82, "momentum": 0.61, "addon": 0.15, "volume": 0.22},
    }
    ctx["indicator_snapshots"] = {"rsi": 54.2}
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    prompt_ctx = build_engine_a_prompt_context(ctx)
    assert prompt_ctx["diagnostics"]["trendScore"] == 0.82
    assert prompt_ctx["diagnostics"]["momentumScore"] == 0.61
    assert prompt_ctx["diagnostics"]["addonScore"] == 0.15
    assert prompt_ctx["diagnostics"]["volumeScore"] == 0.22
    assert prompt_ctx["diagnostics"]["rsi"] == 54.2


def test_build_engine_a_prompt_context_reads_live_v3_ortho_and_entry_contributions():
    ctx = _engine_a_ctx(asset_group="forex_majors", asset_class="forex")
    ctx["factor_diagnostics"] = {
        "entryTimeframe": "M30",
        "entryTfOverride": "M30",
        "entryUsesActiveCandle": True,
        "activeEntryGate": {"required": True, "passed": True, "timeframe": "M30"},
        "factorScores": {
            "trend": 0.82,
            "momentum": 0.61,
            "ortho": {"location": 0.44, "volume": 0.22},
        },
        "components": {
            "location": {
                "signal": 0.8,
                "quality": 0.55,
                "weight": 0.2,
                "contribution": 0.11,
                "available": True,
            }
        },
    }
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)

    prompt_ctx = build_engine_a_prompt_context(ctx)

    assert prompt_ctx["entryTimeframe"] == "M30"
    assert prompt_ctx["entryUsesActiveCandle"] is True
    assert prompt_ctx["diagnostics"]["locationScore"] == pytest.approx(0.44)
    assert prompt_ctx["diagnostics"]["volumeScore"] == pytest.approx(0.22)
    assert prompt_ctx["componentScores"]["location"]["contribution"] == pytest.approx(0.11)


def test_build_engine_a_prompt_context_surfaces_per_group_indicator_periods():
    """Engine A's per-group scoring periods (not the chart's fixed 50/200) reach the model."""
    ctx = _engine_a_ctx(asset_group="forex_majors", asset_class="forex")
    ctx["indicator_snapshots"] = {"rsi": 55.0, "rsi_tf": "H4"}
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)
    diag = build_engine_a_prompt_context(ctx)["diagnostics"]
    assert diag["emaTrendPeriod"] == 26
    assert diag["emaMomentumPeriod"] == 60
    assert diag["emaLongPeriod"] == 200
    assert diag["rsiPeriod"] == 18
    assert diag["rsiTimeframe"] == "H4"


def test_chart_indicator_parity_compares_rsi_atr_adx():
    """Chart-sent rsi14/atr14/adx14 are compared against Engine A H4 refs, not dropped."""
    from ai_review.engine_a_context import _chart_indicator_parity

    screenshot_meta = {
        "chart_snapshot": {
            "chartIndicators": {
                "timeframe": "H4",
                "ema50": 100.0,
                "ema200": 95.0,
                "rsi14": 70.0,
                "atr14": 5.0,
                "adx14": 40.0,
            }
        }
    }
    ema_levels = {"ema50": 100.0, "ema200": 95.0}
    engine_refs = {
        "rsi14": 55.0,
        "atr14": 4.0,
        "adx14": 20.0,
        "ema_periods": {"trend": 26, "long": 200, "momentum": 60},
        "rsi_tf": "H4",
    }
    block, warnings = _chart_indicator_parity(screenshot_meta, ema_levels, engine_refs)
    assert block["status"] == "values_differ"
    assert "chart_indicators_differ_from_engine_a" in warnings
    assert set(block["mismatches"]) == {"rsi14", "atr14", "adx14"}
    assert "ema50" not in block["mismatches"]
    assert block["engine_a_ema_periods"]["trend"] == 26
    assert block["rsi_timeframe"] == "H4"


def test_build_score_attribution_for_scan_payload():
    from ai_review.engine_a_context import build_score_attribution

    signal = {
        "confluenceScore": 2.4,
        "threshold": 2.0,
        "maxScore": 3.0,
        "preNewsScore": 2.33,
        "newsSentimentDelta": 0.07,
        "factorDiagnostics": {
            "intermarket_engine_a_delta": 0.08,
            "intermarket": {"engineADelta": 0.08},
        },
        "intermarketConfirmation": {"engineADelta": 0.08},
    }
    attr = build_score_attribution(signal)
    assert attr["finalEngineAScore"] == pytest.approx(2.4)
    assert attr["newsSentimentDelta"] == pytest.approx(0.07)
    assert attr["intermarketDelta"] == pytest.approx(0.08)
    assert attr["aiReviewCanMutateScore"] is False


def test_build_engine_a_prompt_context_includes_non_visual_context_and_score_attribution():
    ctx = _engine_a_ctx(
        asset_group="crypto",
        factor_diagnostics={
            "factorScores": {"trend": 0.82, "momentum": 0.61, "addon": 0.15, "volume": 0.22},
            "addon_type": "funding+oi",
            "addon_value": 0.15,
            "addon_unsupported": False,
            "feedStatus": {"addon": "funding+oi:ok", "intermarket": "supportive"},
            "intermarket": {
                "verdict": "supportive",
                "score": 0.31,
                "engineADelta": 0.08,
                "supportDirection": "LONG",
                "supportStrength": "moderate",
                "stable": True,
                "flippedRecently": False,
                "activeWindow": 50,
                "topSupporting": [{"driver": "DXY"}],
                "topContradictory": [],
                "unavailablePriors": [{"driver": "US10Y_REAL_YIELD_PROXY", "reason": "insufficient_overlap"}],
                "severeContradiction": False,
                "explanation": "DXY supports.",
            },
            "macroContext": {
                "state": "usd_weaker",
                "proxyLabel": "DXY",
                "status": "ok",
            },
        },
        funding_oi={
            "fundingRate": 0.0001,
            "fundingRateZ": -0.4,
            "openInterest": 123456.0,
            "openInterestDelta": 1000.0,
            "openInterestDeltaPct": 1.5,
            "source": "bybit",
            "timestamp": "2026-05-21T16:00:00+00:00",
        },
        h4={"snap": {
            "order_book_imbalance": 0.2,
            "liquidity_wall_detection": -0.1,
            "orderflow_delta": 0.34,
            "liquidity_pressure": 0.12,
            "volume_momentum_spread": 0.08,
            "microstructure_exchange": "binance",
            "microstructure_age_sec": 12.0,
        }},
        newsSentimentVote=0.4,
        newsSentimentDelta=0.07,
        newsSentimentRawDelta=0.12,
        preNewsScore=2.33,
        newsSentimentSummary={
            "sentiment_score": 0.6,
            "confidence": 0.7,
            "direction": "bullish",
            "article_count_used": 4,
            "fresh_article_count": 3,
            "major_event_detected": True,
            "major_event_description": "ETF decision",
            "key_themes": ["ETF"],
            "reasoning_summary": "ETF flow supports risk.",
        },
    )
    ctx["engine_snapshots"] = extract_engine_snapshots({}, ctx)

    prompt_ctx = build_engine_a_prompt_context(ctx)
    non_visual = prompt_ctx["nonVisualContext"]
    assert prompt_ctx["engineANonVisualContext"] == non_visual
    assert non_visual["addonContext"]["addonType"] == "funding+oi"
    assert non_visual["derivativesContext"]["fundingRate"] == pytest.approx(0.0001)
    assert non_visual["microstructureContext"]["orderflowDelta"] == pytest.approx(0.34)
    assert non_visual["intermarketContext"]["engineADelta"] == pytest.approx(0.08)
    assert non_visual["newsContext"]["freshArticleCount"] == 3
    assert non_visual["macroContext"]["macroContext"]["proxyLabel"] == "DXY"
    assert prompt_ctx["scoreAttribution"]["technicalScoreRaw"] == pytest.approx(2.25)
    assert prompt_ctx["scoreAttribution"]["scoreAfterIntermarket"] == pytest.approx(2.33)
    assert prompt_ctx["scoreAttribution"]["newsSentimentDelta"] == pytest.approx(0.07)
    assert prompt_ctx["scoreAttribution"]["finalEngineAScore"] == pytest.approx(2.4)
    assert prompt_ctx["scoreAttribution"]["aiReviewCanMutateScore"] is False


def test_non_visual_context_marks_asset_specific_inputs_not_applicable():
    forex_ctx = _engine_a_ctx(
        asset_group="forex",
        factor_diagnostics={
            "addon_type": "carry",
            "addon_value": 0.05,
            "feedStatus": {"addon": "carry:ok"},
        },
    )
    forex_block = build_engine_a_prompt_context(forex_ctx)["nonVisualContext"]
    assert forex_block["addonContext"]["addonType"] == "carry"
    assert forex_block["derivativesContext"]["status"] == "not_applicable"

    gold_ctx = _engine_a_ctx(
        symbol="XAUUSD",
        asset_group="commodity",
        factor_diagnostics={
            "addon_type": "cot_proxy",
            "addon_value": 0.12,
            "feedStatus": {"addon": "cot_proxy:ok"},
            "intermarket": {"verdict": "neutral", "unavailablePriors": ["US10Y_REAL_YIELD_PROXY"]},
        },
    )
    gold_block = build_engine_a_prompt_context(gold_ctx)["nonVisualContext"]
    assert gold_block["addonContext"]["addonType"] == "cot_proxy"
    assert gold_block["intermarketContext"]["unavailablePriors"] == ["US10Y_REAL_YIELD_PROXY"]


def test_prompt_includes_non_visual_context_section_and_score_mutation_rules():
    prompt = build_chart_review_prompt(_engine_a_ctx())
    assert "== SERVER-TRUSTED NON-VISUAL ENGINE A CONTEXT ==" in prompt
    assert "Never change Engine A score or threshold" in prompt
    assert "Never claim addonScore is volume" in prompt


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
    assert eb_ctx["available"] is True


def test_build_engine_b_prompt_context_unavailable_without_structure():
    from ai_review.engine_a_context import build_engine_b_prompt_context

    ctx = _engine_a_ctx()
    ctx["structure_context"] = {}
    ctx["engine_snapshots"] = {"engineB": {}}
    eb_ctx = build_engine_b_prompt_context(ctx)
    assert eb_ctx["available"] is False


def test_build_engine_b_prompt_context_includes_ob_fvg_gates():
    from ai_review.engine_a_context import build_engine_b_prompt_context

    ctx = _engine_a_ctx()
    ctx["structure_context"] = {
        "structural_verdict": "CLEAR",
        "bos_confirmed": True,
        "ob_at_zone": True,
        "fvg_overlap": True,
        "sweep_direction": "LONG",
        "active_fvgs": [{"midpoint": 65500.0}],
        "structure_ok": True,
        "location_ok": True,
        "entry_ok": True,
        "room_ok": True,
        "rr_ok": True,
        "recommended_stop_loss": 63000.0,
        "recommended_take_profit": 68000.0,
        "rr": 1.8,
    }
    ctx["engine_snapshots"] = {
        "engineB": {"score": 4.0, "passed": True, "direction": "LONG"}
    }
    eb_ctx = build_engine_b_prompt_context(ctx)
    assert eb_ctx["available"] is True
    assert eb_ctx["obAtZone"] is True
    assert eb_ctx["fvgOverlap"] is True
    assert eb_ctx["activeFvgCount"] == 1
    assert eb_ctx["nearestFvgMid"] == 65500.0
    assert eb_ctx["sweepDirection"] == "LONG"
    assert eb_ctx["structureOk"] is True
    assert eb_ctx["executionSl"] == 63000.0
    assert eb_ctx["rr"] == 1.8


def test_build_engine_b_prompt_context_includes_geometry_and_scale_out_fields():
    from ai_review.engine_a_context import build_engine_b_prompt_context

    ctx = {
        "direction": "SHORT",
        "structure_context": {
            "structural_verdict": "CLEAR",
            "nearest_support_zone": {"lower": 6.6525, "upper": 6.8345},
            "nearest_resistance_zone": {"lower": 6.9, "upper": 7.0},
            "distance_to_sup": -0.1295,
            "distance_to_sup_pct": -1.9314,
        },
        "engine_b_confidence": {
            "space_gate_ok": False,
            "room_ok": False,
            "execution_tp1": 6.592244191324525,
            "execution_tp2": 6.415925468206346,
            "execution_rr1": 0.7021,
            "execution_rr2": 1.8,
            "tp1_min_rr": 0.3,
            "style_min_rr": 1.4,
            "exit_strategy": "scale_out_structural_tp1_fallback_tp2",
            "runner_tp_requires_structural_break": True,
            "tp1_path_clear": False,
            "scale_out_active": True,
            "scale_out_space_ok": False,
        },
        "engine_snapshots": {"engineB": {"passed": False, "direction": "SHORT"}},
    }
    eb_ctx = build_engine_b_prompt_context(ctx)
    assert eb_ctx["spaceGateOk"] is False
    assert eb_ctx["executionTp1"] == pytest.approx(6.592244191324525)
    assert eb_ctx["executionTp2"] == pytest.approx(6.415925468206346)
    assert eb_ctx["executionRr1"] == pytest.approx(0.7021)
    assert eb_ctx["executionRr2"] == pytest.approx(1.8)
    assert eb_ctx["tp1MinRr"] == pytest.approx(0.3)
    assert eb_ctx["exitStrategy"] == "scale_out_structural_tp1_fallback_tp2"
    assert eb_ctx["runnerTpRequiresStructuralBreak"] is True
    assert eb_ctx["tp1PathClear"] is False
    assert eb_ctx["nearestSupportZone"]["lower"] == pytest.approx(6.6525)
    assert eb_ctx["nearestSupportZone"]["upper"] == pytest.approx(6.8345)


def test_build_engine_b_prompt_context_includes_live_tf_gates_and_score_contributions():
    from ai_review.engine_a_context import build_engine_b_prompt_context

    ctx = {
        "asset_class": "forex",
        "asset_group": "forex_majors",
        "symbol": "EURUSD",
        "direction": "LONG",
        "structure_context": {
            "structural_verdict": "CLEAR",
            "structure_timeframe": "H4",
            "zone_tf": "H4",
            "entry_tf": "M15",
            "atr_tf": "H4",
        },
        "engine_b_confidence": {
            "structure_ok": True,
            "location_ok": True,
            "entry_ok": True,
            "space_gate_ok": True,
            "rr_ok": True,
            "trigger_timeframe_expected": "M15",
            "trigger_timeframe_actual": "M15",
            "trigger_timeframe_gate_ok": True,
            "gate_score": 5.0,
            "gate_max_possible": 5.0,
            "quality_score": 2.25,
            "quality_max_possible": 4.5,
            "quality_components": {"structure": 0.8, "location": 0.7},
            "execution_sl": 1.075,
            "rr_used_for_gate": 1.8,
            "rr_required": 1.2,
        },
        "geometry": {"candidate_entry": 1.1, "stop_loss": 1.075, "rr": 1.8},
        "engine_snapshots": {"engineB": {"score": 7.25, "passed": True, "direction": "LONG"}},
    }

    eb_ctx = build_engine_b_prompt_context(ctx)

    assert eb_ctx["structTf"] == "H4"
    assert eb_ctx["zoneTf"] == "H4"
    assert eb_ctx["triggerTf"] == "M15"
    assert eb_ctx["atrTf"] == "H4"
    assert eb_ctx["triggerTimeframeGateOk"] is True
    assert eb_ctx["gateScore"] == pytest.approx(5.0)
    assert eb_ctx["qualityComponents"]["location"] == pytest.approx(0.7)
    assert eb_ctx["maxSlFraction"] == pytest.approx(0.025)
    assert eb_ctx["maxSlPassed"] is True


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
                "profile_vp_context": {"enabled": True, "trusted": True, "reason": "crypto"},
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


def _crypto_policy_fresh_ctx(**overrides):
    ctx = _engine_a_ctx(asset_group="crypto")
    ctx["freshness"] = {
        "data_freshness_allowed": True,
        "execution_blocked": [],
        "candleFreshnessSummary": {
            "dataFreshnessAllowed": True,
            "perTimeframe": {
                "H4": {
                    "severity": "stale_1_bucket",
                    "bucketLag": 1,
                    "consistencyStatus": "CONFIRMED_ONLY_OK",
                    "policyNote": "policy_ok_not_stale",
                },
                "D1": {
                    "severity": "stale_1_bucket",
                    "bucketLag": 1,
                    "consistencyStatus": "CONFIRMED_ONLY_OK",
                    "policyNote": "policy_ok_not_stale",
                },
                "H1": {
                    "severity": "stale_1_bucket",
                    "bucketLag": 1,
                    "consistencyStatus": "CONFIRMED_ONLY_OK",
                    "policyNote": "policy_ok_not_stale",
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


def test_crypto_confirmed_only_stale_1_not_in_abort_reasons():
    from ai_review.engine_a_context import (
        _execution_abort_reasons,
        build_engine_a_prompt_context,
    )

    data_freshness = {
        "allowed": True,
        "blocked": [
            {"timeframe": "H4", "severity": "stale_1_bucket"},
            {"timeframe": "D1", "severity": "stale_1_bucket"},
        ],
    }
    candle_consistency = {
        "H4": {"status": "CONFIRMED_ONLY_OK"},
        "D1": {"status": "CONFIRMED_ONLY_OK"},
    }
    assert _execution_abort_reasons(data_freshness, candle_consistency) == []

    ctx = _crypto_policy_fresh_ctx()
    prompt_ctx = build_engine_a_prompt_context(ctx)
    assert prompt_ctx["abortReasons"] == []
    assert prompt_ctx["dataFreshnessAllowed"] is True
    assert prompt_ctx["candleFreshnessSummary"]["perTimeframe"]["H4"]["policyNote"] == "policy_ok_not_stale"


def test_prompt_includes_crypto_confirmed_only_freshness_wording():
    ctx = _engine_a_ctx(asset_group="crypto")
    ctx["freshness"] = _crypto_policy_fresh_ctx()["freshness"]
    prompt = build_chart_review_prompt(ctx)
    assert "policy_ok_not_stale" in prompt or "policyNote=policy_ok_not_stale" in prompt
    assert "do NOT list H4/D1/H1 as stale" in prompt


def test_prompt_generalizes_confirmed_only_stale_1_wording_for_stocks():
    ctx = _engine_a_ctx(symbol="AAPL", asset_group="stock")
    ctx["freshness"] = _crypto_policy_fresh_ctx()["freshness"]
    prompt = build_chart_review_prompt(ctx)
    assert "Candle freshness (confirmed-only paths)" in prompt
    assert "crypto 24/7" not in prompt
    assert "policy_ok_not_stale" in prompt


def test_verdict_strips_false_h4_d1_stale_downgrade_when_policy_ok():
    ctx = _crypto_policy_fresh_ctx(passed=True)
    model_comparison = {
        "comparisonVerdict": "engine_a_direction_confirmed_entry_rejected",
        "downgradeReasons": [
            "entry extended after impulse",
            "H4/D1 stale",
        ],
        "finalDecision": "wait",
    }
    ai_review = {
        "human_action": "wait",
        "visual_confirmation": "aligned",
        "entry_quality": "extended after impulse",
    }
    comparison = build_engine_a_verdict_comparison(
        ctx, ai_review, model_comparison=model_comparison
    )
    reasons = comparison.get("downgradeReasons") or []
    assert "H4/D1 stale" not in reasons
    assert "entry extended after impulse" in reasons


def test_verdict_keeps_h4_d1_stale_when_execution_blocked():
    ctx = _crypto_policy_fresh_ctx(passed=True)
    ctx["freshness"]["data_freshness_allowed"] = False
    ctx["freshness"]["execution_blocked"] = ["H4:stale_multi_bucket"]
    ctx["freshness"]["candleFreshnessSummary"]["perTimeframe"]["H4"] = {
        "severity": "stale_multi_bucket",
        "bucketLag": 3,
        "consistencyStatus": "ERROR_STALE_MULTI_BUCKET",
        "policyNote": "execution_stale",
    }
    ai_review = {"human_action": "reject"}
    comparison = build_engine_a_verdict_comparison(
        ctx,
        ai_review,
        model_comparison={
            "downgradeReasons": ["H4/D1 stale"],
            "finalDecision": "reject",
        },
    )
    assert "H4/D1 stale" in (comparison.get("downgradeReasons") or [])


def _base_request_engine_b(**overrides):
    body = _base_request()
    meta = dict(body.get("screenshot_meta") or {})
    meta["candidate_direction"] = "LONG"
    body["screenshot_meta"] = meta
    body.update(overrides)
    return body


def test_route_screenshot_meta_signal_engine_b_overrides_config(tmp_audit_db):
    app = _make_app(tmp_audit_db, primary_engine="A")
    client = app.test_client()
    body = _base_request(
        screenshot_meta={
            **(_base_request()["screenshot_meta"]),
            "candidate_direction": "LONG",
            "signal_engine": "B",
        }
    )
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("primaryEngine") == "B"
    assert data.get("engine_b_context") is not None
    assert data["reviewInputMeta"]["signalEngine"] == "B"


def test_route_primary_engine_b_returns_engine_b_context(tmp_audit_db):
    app = _make_app(tmp_audit_db, primary_engine="B")
    client = app.test_client()
    body = _base_request_engine_b()
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("primaryEngine") == "B"
    assert data.get("engine_b_context") is not None
    assert data.get("engineBContext") == data["engine_b_context"]
    assert data["engine_b_context"]["primary_engine"] == "B"
    assert data.get("concordance", {}).get("engine") == "B"
    assert data["reviewInputMeta"]["signalEngine"] == "B"


def test_route_primary_engine_b_fail_closed_without_direction(tmp_audit_db):
    app = _make_app(tmp_audit_db, primary_engine="B")
    client = app.test_client()
    body = _base_request()
    resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 422
    assert "Engine B returned no result" in resp.get_json()["error"]


def test_route_invalid_metadata_engine_falls_back_safely_to_a(tmp_audit_db):
    app = _make_app(tmp_audit_db, primary_engine="B")
    client = app.test_client()
    body = _base_request(
        screenshot_meta={
            **(_base_request()["screenshot_meta"]),
            "candidate_direction": "LONG",
            "primary_engine": "not-an-engine",
        }
    )
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ):
        resp = client.post("/api/ai/chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("primaryEngine") == "A"
    assert data.get("engine_a_context") is not None
    assert data["reviewInputMeta"]["signalEngine"] == "A"


def test_dedup_engine_b_separate_from_engine_a(tmp_audit_db):
    app_a = _make_app(tmp_audit_db, primary_engine="A")
    app_b = _make_app(tmp_audit_db, primary_engine="B")
    body_a = _base_request()
    body_b = _base_request_engine_b()
    with patch(
        "ai_review.providers.router.call_anthropic_chart_review",
        return_value=_mock_provider_payload(),
    ) as mock_call:
        first_a = app_a.test_client().post("/api/ai/chart-review", json=body_a)
        assert first_a.status_code == 200
        assert mock_call.call_count == 1
        first_b = app_b.test_client().post("/api/ai/chart-review", json=body_b)
        assert first_b.status_code == 200
        assert mock_call.call_count == 2

