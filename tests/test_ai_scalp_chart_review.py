"""Tests for Engine D scalp chart review backend."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from ai_scalp_review.concordance import compute_scalp_ai_concordance
from ai_scalp_review.engine_d_context import (
    assemble_engine_d_context,
    build_engine_d_prompt_context,
    build_engine_d_summary_for_strategy,
)
from ai_scalp_review.normalizer import normalize_scalp_chart_review_response
from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt
from ai_scalp_review.provider import run_scalp_chart_review
from ai_scalp_review.scalp_verdict import build_scalp_verdict_comparison
from ai_scalp_review.summary import build_scalp_ai_review_summary
from ai_review.provider_meta import ProviderChartReviewError
from ai_review.persistence import ensure_schema, find_recent_review_by_hash, record_review
from athena_app.api.routes_ai_scalp_chart_review import register_ai_scalp_chart_review_routes
from config import CONFIG

_PNG_1X1 = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c630001000005000108d4000000000049454e44ae426082"
    )
).decode()


def _png_data_url() -> str:
    return f"data:image/png;base64,{_PNG_1X1}"


def _recent_iso(seconds_ago: int = 5) -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _minimal_chart_snapshot(**overrides):
    snap = {
        "symbol": "BTCUSDT",
        "timeframe": "M1",
        "latestCandleTs": _recent_iso(30),
        "selectedSignal": {
            "direction": "LONG",
            "grade": "B",
            "entry": 65000.0,
            "stopLoss": 64920.0,
            "tp1": 65120.0,
            "setup_type": "lvn_reclaim",
        },
        "profile": {"poc": 64980.0, "vah": 65100.0, "val": 64850.0, "lvns": [], "hvns": []},
        "renderedLayers": {"candles": True, "profile": True, "candidateLevels": True},
    }
    snap.update(overrides)
    return snap


def _base_request(**overrides):
    body = {
        "symbol": "BTCUSDT",
        "timeframe": "M1",
        "provider": "default",
        "screenshot_base64": _png_data_url(),
        "screenshot_meta": {
            "width": 1280,
            "height": 720,
            "native_chart": True,
            "captured_at": _recent_iso(2),
            "chart_timeframe": "M1",
            "overlays": ["entry_sl_tp", "poc_vah_val"],
            "execution_tf": "M1",
            "chart_snapshot": _minimal_chart_snapshot(),
            "rendered_layers": {"candles": True, "profile": True, "candidateLevels": True},
        },
    }
    body.update(overrides)
    return body


def _mock_scan_signal(**overrides):
    signal = {
        "pair": "BTCUSDT",
        "display": "BTCUSDT",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "ai_grade": "B",
        "ai_score": 72.5,
        "executable": True,
        "execution_tf": "M1",
        "timeframe": "M1",
        "strict_fabio_pass": True,
        "timestamp": "2026-05-21T16:30:55+00:00",
        "scalpSetup": {
            "setupType": "lvn_reclaim",
            "entry": 65000.0,
            "stopLoss": 64920.0,
            "tp1": 65120.0,
            "rr1": 1.5,
        },
        "marketLocation": {
            "locationLabel": "nearPOC",
            "poc": 64980.0,
            "vah": 65100.0,
            "val": 64850.0,
            "lvnLevels": [64910.0],
        },
        "aggressionContext": {
            "aggressionLabel": "confirmed",
            "cvd": 1200.0,
            "cvdSlope": 0.4,
        },
        "sourceContract": {
            "orderflowSourceIsReal": True,
            "strictOrderflowSourcePass": True,
            "strictTimestampAlignmentPass": True,
            "latestCandleTs": "2026-05-21T16:00:00+00:00",
            "candleSource": "bybit",
            "orderflowSource": "bybit_trades",
            "vpSource": "bybit_trades",
        },
    }
    signal.update(overrides)
    return signal


def _engine_d_ctx(**overrides):
    ctx = {
        "symbol": "BTCUSDT",
        "timeframe": "M1",
        "execution_tf": "M1",
        "direction": "LONG",
        "ai_grade": "B",
        "ai_score": 72.5,
        "executable": True,
        "setup_available": True,
        "scan_timestamp": "2026-05-21T16:30:55+00:00",
        "latest_candle_ts": "2026-05-21T16:00:00+00:00",
        "chart_captured_at": "2026-05-21T16:31:02+00:00",
        "scalpSetup": _mock_scan_signal()["scalpSetup"],
        "marketLocation": _mock_scan_signal()["marketLocation"],
        "aggressionContext": _mock_scan_signal()["aggressionContext"],
        "sourceContract": _mock_scan_signal()["sourceContract"],
        "mismatch_warnings": [],
    }
    ctx.update(overrides)
    return ctx


def _mock_provider_payload(**overrides):
    body = {
        "verdict": "VALID",
        "confidence": 78,
        "setup_type": "lvn_reclaim",
        "visual_confirmation": "direction aligned",
        "visual_contradiction": "",
        "entry_quality": "acceptable near POC",
        "source_quality_assessment": "real orderflow",
        "supporting_reasons": ["setup confirmed"],
        "risks": [],
        "missing_context": [],
        "human_action": "take",
        "aiReviewSummary": {
            "overallScore": 76,
            "tradeabilityScore": 74,
            "visualConfirmationScore": 80,
            "entryQualityScore": 72,
            "riskScore": 78,
            "sourceQualityScore": 82,
            "confidence": 78,
            "humanAction": "trade",
            "setupType": "lvn_reclaim",
            "finalReason": "Chart confirms setup",
        },
        "scalpVerdictComparison": {
            "comparisonVerdict": "setup_confirmed",
            "chartConfirmsDirection": True,
            "chartContradictsDirection": False,
            "chartConfirmsEntryTiming": True,
            "chartContradictsEntryTiming": False,
            "sourceQualitySupportsReview": True,
            "finalDecision": "trade",
            "finalReason": "Chart confirms setup",
        },
        "contextCompleteness": {
            "score": 90,
            "status": "complete",
            "missingRequired": [],
            "missingOptional": [],
            "notApplicable": [],
            "metadata": {},
        },
    }
    body.update(overrides.get("ai") or {})
    raw = overrides.get("raw_text", json.dumps(body))
    base = {
        "raw_text": raw,
        "model": "claude-opus-4-7",
        "latency_ms": 42,
        "provider": "anthropic",
        "provider_status": "success",
        "fallback_used": False,
    }
    base.update({k: v for k, v in overrides.items() if k != "ai"})
    return base


def _make_app(tmp_db: str, enabled: bool = True):
    app = Flask(__name__)
    cfg = dict(CONFIG)
    cfg["AI_REVIEW_PROVIDER"] = "claude"
    cfg["AI_REVIEW_FALLBACK_PROVIDERS"] = ""
    scalp_cfg = dict(cfg["AI_SCALP_CHART_REVIEW"])
    scalp_cfg["ENABLED"] = enabled
    cfg["AI_SCALP_CHART_REVIEW"] = scalp_cfg

    def resolve_pair(symbol: str):
        if symbol.upper() in ("BTCUSDT", "BTC/USDT"):
            return {"display": "BTCUSDT", "symbol": "BTCUSDT"}
        return None

    def run_scalp_scan(pairs):
        return {"signals": [_mock_scan_signal()], "skipped": []}

    register_ai_scalp_chart_review_routes(
        app,
        SimpleNamespace(
            CONFIG=cfg,
            AUDIT_DB=tmp_db,
            json_safe=lambda x: x,
            resolve_pair_fn=resolve_pair,
            run_scalp_scan_fn=run_scalp_scan,
            scalp_ui_signal_fn=lambda s: s,
        ),
    )
    return app


def test_assemble_engine_d_context_picks_best_signal():
    ctx = assemble_engine_d_context(
        "BTCUSDT",
        "M1",
        screenshot_meta={"captured_at": "2026-05-21T16:31:02+00:00", "chart_timeframe": "M1"},
        resolve_pair_fn=lambda s: {"display": "BTCUSDT"},
        run_scalp_scan_fn=lambda pairs: {
            "signals": [
                _mock_scan_signal(direction="SHORT", ai_score=40),
                _mock_scan_signal(direction="LONG", ai_score=72.5),
            ]
        },
        scalp_ui_signal_fn=lambda s: s,
    )
    assert ctx is not None
    assert ctx["direction"] == "LONG"
    assert ctx["ai_grade"] == "B"
    assert ctx["ai_score"] == 72.5


def test_assemble_engine_d_context_populates_ohlcv_bars_from_signal():
    bars = [{"time": "2026-05-21T16:30:00+00:00", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}]
    signal = _mock_scan_signal(direction="LONG", ai_score=72.5)
    signal["m1Candles"] = bars
    ctx = assemble_engine_d_context(
        "BTCUSDT",
        "M1",
        screenshot_meta={"captured_at": "2026-05-21T16:31:02+00:00", "chart_timeframe": "M1"},
        resolve_pair_fn=lambda s: {"display": "BTCUSDT"},
        run_scalp_scan_fn=lambda pairs: {"signals": [signal]},
        scalp_ui_signal_fn=lambda s: s,
    )
    assert ctx is not None
    assert ctx["ohlcv_bars"] == bars


def test_scalp_ui_signal_forwards_candles_for_engine_d_review():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "athena.py"
    spec = spec_from_file_location("athena_scalp_ui_signal_module", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    bars = [{"time": "2026-05-21T16:30:00+00:00", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}]
    raw = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "ai_grade": "B",
        "ai_score": 72.5,
        "zone_type": "trend_continuation",
        "zone_level": 1.05,
        "m1Candles": bars,
    }
    ui = module._scalp_ui_signal(raw)
    ctx = assemble_engine_d_context(
        "BTCUSDT",
        "M1",
        screenshot_meta={"captured_at": "2026-05-21T16:31:02+00:00", "chart_timeframe": "M1"},
        resolve_pair_fn=lambda s: {"display": "BTCUSDT"},
        run_scalp_scan_fn=lambda pairs: {"signals": [ui]},
        scalp_ui_signal_fn=module._scalp_ui_signal,
    )
    assert ctx is not None
    assert ctx["ohlcv_bars"] == bars


def test_prompt_contains_engine_d_playbook_and_review_order():
    ctx = _engine_d_ctx()
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "ATHENA TRADE PLAYBOOKS" in prompt
    assert "Session Context" in prompt
    assert "sessionContext" in prompt
    assert "tradeSkillVersion" in prompt
    assert "Do not use NO_TRADE as generic caution" in prompt
    assert "Use NO_TRADE only with hard invalidation or a concrete noTradeReason" in prompt


def test_prompt_contains_engine_d_context():
    ctx = _engine_d_ctx()
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "engineDContext" in prompt
    assert "aiGrade" in prompt
    assert "nearPOC" in prompt or "locationLabel" in prompt


def test_request_rejects_extra_client_score_keys():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db = tmp.name
    app = _make_app(tmp_db)
    client = app.test_client()
    body = _base_request(ai_score=99, location="nearPOC")
    resp = client.post("/api/ai/scalp-chart-review", json=body)
    assert resp.status_code == 400
    assert "unexpected request keys" in resp.get_json()["error"]


def test_unknown_symbol_returns_422():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db = tmp.name
    app = Flask(__name__)
    cfg = dict(CONFIG)
    scalp_cfg = dict(cfg["AI_SCALP_CHART_REVIEW"])
    scalp_cfg["ENABLED"] = True
    cfg["AI_SCALP_CHART_REVIEW"] = scalp_cfg
    register_ai_scalp_chart_review_routes(
        app,
        SimpleNamespace(
            CONFIG=cfg,
            AUDIT_DB=tmp_db,
            json_safe=lambda x: x,
            resolve_pair_fn=lambda s: None,
            run_scalp_scan_fn=lambda pairs: {"signals": []},
            scalp_ui_signal_fn=lambda s: s,
        ),
    )
    resp = app.test_client().post("/api/ai/scalp-chart-review", json=_base_request(symbol="UNKNOWN"))
    assert resp.status_code == 422


@patch("athena_app.api.routes_ai_scalp_chart_review.run_scalp_chart_review")
def test_route_normalizes_opus_response(mock_run):
    mock_run.return_value = _mock_provider_payload()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db = tmp.name
    app = _make_app(tmp_db)
    resp = app.test_client().post("/api/ai/scalp-chart-review", json=_base_request())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["aiReviewSummary"]["overallScore"] == 76
    assert data["scalpVerdictComparison"]["comparisonVerdict"] == "setup_confirmed"
    assert data["engine_d_context"]["ai_grade"] == "B"
    assert data["concordance"]["engine"] == "D"
    assert data["reviewInputMeta"] == {
        "symbol": "BTCUSDT",
        "signalEngine": "D",
        "signalTimeframe": "M1",
        "chartTimeframe": "M1",
        "hasEngineASignal": False,
        "hasEngineBOverlay": False,
        "hasChartImage": True,
        "timeframeRouteApplied": False,
    }


def test_scalp_router_resolves_xai_provider():
    payload = SimpleNamespace(screenshot_base64=_png_data_url(), prompt="review")
    with patch(
        "ai_scalp_review.provider.call_xai_scalp_chart_review",
        return_value=_mock_provider_payload(
            raw_text="{}",
            provider="xai",
            model="grok-4.3",
        ),
        create=True,
    ) as mock_xai:
        out = run_scalp_chart_review("xai", payload)
        mock_xai.assert_called_once()
    assert out["provider"] == "grok"
    assert out["model"] == "grok-4.3"


def test_scalp_router_aliases_grok_to_xai_provider():
    payload = SimpleNamespace(screenshot_base64=_png_data_url(), prompt="review")
    with patch(
        "ai_scalp_review.provider.call_xai_scalp_chart_review",
        return_value=_mock_provider_payload(
            raw_text="{}",
            provider="xai",
            model="grok-4.3",
        ),
        create=True,
    ) as mock_xai:
        run_scalp_chart_review("grok", payload)
        mock_xai.assert_called_once()


def test_xai_scalp_provider_missing_key_fails_closed(monkeypatch):
    from ai_scalp_review.provider import call_xai_scalp_chart_review

    payload = SimpleNamespace(screenshot_base64=_png_data_url(), prompt="review")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    with patch.dict(CONFIG, {"XAI_API_KEY": "", "MOONSHOT_API_KEY": ""}, clear=False):
        with pytest.raises(ProviderChartReviewError) as excinfo:
            call_xai_scalp_chart_review(payload)
    assert excinfo.value.provider_status == "failed_auth"
    assert excinfo.value.provider == "grok"


def test_scalp_router_resolves_openai_provider():
    payload = SimpleNamespace(screenshot_base64=_png_data_url(), prompt="review")
    with patch(
        "ai_scalp_review.provider.call_openai_chart_review",
        return_value=_mock_provider_payload(
            raw_text="{}",
            provider="openai",
            model="gpt-5.5",
        ),
        create=True,
    ) as mock_openai:
        out = run_scalp_chart_review("openai", payload)
        mock_openai.assert_called_once()
    assert out["provider"] == "openai"
    assert out["model"] == "gpt-5.5"


def test_strategy_layer_receives_engine_d_summary():
    summary = build_engine_d_summary_for_strategy(_engine_d_ctx())
    assert summary["available"] is True
    assert summary["ai_grade"] == "B"


def test_dedup_scoped_by_review_type():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db = tmp.name
    ensure_schema(tmp_db)
    ctx = _engine_d_ctx()
    ai = normalize_scalp_chart_review_response(json.dumps(_mock_provider_payload()["raw_text"]))
    conc = compute_scalp_ai_concordance(ctx, ai)
    record_review(
        symbol="BTCUSDT",
        timeframe="M1",
        asset_group=None,
        provider="anthropic",
        model="claude-opus-4-7",
        latency_ms=10,
        screenshot_hash="abc123",
        screenshot_bytes=100,
        screenshot_meta={"native_chart": True},
        engine_d_context=ctx,
        ai_review=ai,
        concordance=conc,
        mismatch_warnings=[],
        audit_db=tmp_db,
        review_type="engine_d",
    )
    hit = find_recent_review_by_hash(
        "BTCUSDT", "M1", "abc123", 60, audit_db=tmp_db, review_type="engine_d"
    )
    assert hit is not None
    assert hit["engine_d_context"]["symbol"] == "BTCUSDT"
    miss = find_recent_review_by_hash(
        "BTCUSDT", "M1", "abc123", 60, audit_db=tmp_db, review_type="engine_a"
    )
    assert miss is None


def test_ensure_schema_migrates_legacy_table_without_review_type():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db = tmp.name
    legacy_sql = """
    CREATE TABLE ai_chart_reviews (
      review_id TEXT PRIMARY KEY,
      created_at TEXT NOT NULL,
      symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL,
      asset_group TEXT,
      provider TEXT NOT NULL,
      model TEXT NOT NULL,
      latency_ms INTEGER,
      screenshot_hash TEXT NOT NULL,
      screenshot_bytes INTEGER,
      screenshot_meta_json TEXT NOT NULL,
      engine_a_snapshot_json TEXT NOT NULL,
      ai_review_json TEXT NOT NULL,
      concordance_json TEXT NOT NULL,
      scan_timestamp TEXT,
      chart_captured_at TEXT,
      latest_candle_ts TEXT,
      mismatch_warnings_json TEXT,
      parse_success INTEGER NOT NULL
    );
    """
    with sqlite3.connect(tmp_db) as con:
        con.executescript(legacy_sql)
    ensure_schema(tmp_db)
    with sqlite3.connect(tmp_db) as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(ai_chart_reviews)").fetchall()}
        assert "review_type" in cols
        idx = con.execute(
            "SELECT name FROM sqlite_master WHERE name='idx_ai_chart_reviews_dedup'"
        ).fetchone()
        assert idx is not None


def test_build_engine_d_prompt_context_fields():
    ctx = _engine_d_ctx()
    prompt_ctx = build_engine_d_prompt_context(ctx)
    assert prompt_ctx["aiGrade"] == "B"
    assert prompt_ctx["sourceContract"]["strictOrderflowSourcePass"] is True
    assert "sessionContext" in prompt_ctx
    assert prompt_ctx["sessionContext"]["currentSession"]


def test_engine_d_prompt_context_maps_engine_market_state_to_playbook():
    ctx = _engine_d_ctx()
    ctx["signal"] = dict(ctx.get("signal") or {})
    ctx["signal"]["market_state"] = "balance"
    prompt_ctx = build_engine_d_prompt_context(ctx)
    assert prompt_ctx["marketState"] == "balancing"
    assert prompt_ctx["engineMarketState"] == "balance"

    ctx["signal"]["market_state"] = "imbalance"
    prompt_ctx = build_engine_d_prompt_context(ctx)
    assert prompt_ctx["marketState"] == "trending"
    assert prompt_ctx["engineMarketState"] == "imbalance"


def test_engine_d_context_passes_session_context():
    ctx = _engine_d_ctx()
    ctx["scan_timestamp"] = "2026-01-15T18:00:00+00:00"
    ctx["signal"] = dict(ctx.get("signal") or {})
    ctx["signal"]["type"] = "forex"
    ctx["signal"]["market_state"] = "balance"
    prompt_ctx = build_engine_d_prompt_context(ctx)
    sc = prompt_ctx["sessionContext"]
    assert sc["currentSession"] == "NY_MIDDAY"
    assert sc["deliveryWindow"] == "COMPRESSION_WINDOW"
    assert prompt_ctx["sessionConvictionHint"]["sessionConvictionAdjustment"] == "DOWNGRADE"


def test_midday_session_context_downgrades_marginal_setup():
    ctx = _engine_d_ctx()
    ctx["scan_timestamp"] = "2026-01-15T18:00:00+00:00"
    ctx["signal"] = dict(ctx.get("signal") or {})
    ctx["signal"]["type"] = "forex"
    ctx["signal"]["market_state"] = "balance"
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "NY_MIDDAY" in prompt
    assert "COMPRESSION_WINDOW" in prompt or "DOWNGRADE" in prompt
    prompt_ctx = build_engine_d_prompt_context(ctx)
    assert prompt_ctx["sessionContext"]["sessionConvictionAdjustment"] == "DOWNGRADE"


def test_summary_and_verdict_fallbacks():
    ctx = _engine_d_ctx()
    ai = normalize_scalp_chart_review_response(
        json.dumps({"verdict": "CAUTION", "confidence": 55, "human_action": "wait"})
    )
    meta = {"provider": "anthropic", "model": "claude-opus-4-7", "provider_status": "success"}
    summary = build_scalp_ai_review_summary(ctx, ai, meta)
    assert 0 <= summary["overallScore"] <= 100
    verdict = build_scalp_verdict_comparison(ctx, ai)
    assert verdict["setupProvided"] is True


def test_scalp_normalizer_accepts_valid_engine_d_structured_output():
    raw = json.dumps({
        "tradeSkillVersion": "athena_trade_skill.v1",
        "reviewType": "engine_d_scalp",
        "decision": "ENTRY_NOW",
        "direction": "LONG",
        "confidence": 85,
        "marketState": "trending",
        "locationAssessment": "Pullback to VAL with LVN support",
        "aggressionAssessment": "Buying aggression confirms direction",
        "entryModel": "PULLBACK_TO_VALUE_REJECTION",
        "invalidationLevel": 64920.0,
        "invalidationReason": "Break below VAL invalidates long",
        "entryAllowedNow": True,
        "requiredConfirmation": ["Hold above POC"],
        "chartReadSummary": "Clean M5 context with M1 execution zoom",
    })
    out = normalize_scalp_chart_review_response(raw, backend_levels={"stopLoss": 64920.0})
    assert out["decision"] == "ENTRY_NOW"
    assert out["entryAllowedNow"] is True
    assert out["marketState"] == "trending"


def test_scalp_normalizer_downgrades_entry_now_missing_invalidation():
    raw = json.dumps({
        "decision": "ENTRY_NOW",
        "direction": "LONG",
        "confidence": 80,
        "marketState": "trending",
        "locationAssessment": "ok",
        "aggressionAssessment": "ok",
        "entryModel": "SWEEP_AND_RECLAIM",
        "entryAllowedNow": True,
    })
    out = normalize_scalp_chart_review_response(raw)
    assert out["decision"] == "WATCH_ONLY"
    assert out["entryAllowedNow"] is False


def test_scalp_normalizer_no_trade_disables_entry():
    raw = json.dumps({
        "decision": "NO_TRADE",
        "direction": "LONG",
        "confidence": 40,
        "marketState": "choppy",
        "locationAssessment": "poor",
        "aggressionAssessment": "mixed",
        "entryModel": "NO_TRADE",
        "noTradeReason": "Choppy balance — no edge",
    })
    out = normalize_scalp_chart_review_response(raw)
    assert out["entryAllowedNow"] is False
    assert out["decision"] == "NO_TRADE"


def test_scalp_normalizer_sanitizes_suggested_trade_plan():
    raw = json.dumps({
        "verdict": "CAUTION",
        "confidence": 62,
        "human_action": "wait",
        "decision": "WAIT_FOR_ACCEPTANCE",
        "direction": "SHORT",
        "entryAllowedNow": False,
        "suggestedTradePlan": {
            "schemaVersion": "suggested_trade_plan.v1",
            "armable": True,
            "source": "ai_scalp_chart_review",
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "action": "WAIT_FOR_ZONE",
            "triggerType": "PULLBACK_TO_ZONE",
            "zoneLow": 64900,
            "zoneHigh": 65100,
            "contextTf": "M5",
            "entryTf": "M1",
            "executionTf": "M1",
            "invalidateBelow": 64850,
            "expiresInSeconds": 900,
        },
    })
    out = normalize_scalp_chart_review_response(raw)
    plan = out.get("suggestedTradePlan")
    assert isinstance(plan, dict)
    assert plan.get("source") == "ai_scalp_chart_review"
    assert plan.get("armable") is True
    assert plan.get("zoneLow") == 64900


def test_scalp_normalizer_malformed_suggested_trade_plan_not_armable():
    raw = json.dumps({
        "verdict": "CAUTION",
        "confidence": 40,
        "human_action": "wait",
        "decision": "WAIT_FOR_PULLBACK",
        "direction": "LONG",
        "entryAllowedNow": False,
        "suggestedTradePlan": {"action": "ENTRY_NOW", "direction": "LONG", "triggerType": "ACCEPTANCE_ABOVE", "level": 1},
    })
    out = normalize_scalp_chart_review_response(raw)
    plan = out.get("suggestedTradePlan")
    assert plan is None or (isinstance(plan, dict) and plan.get("armable") is False)


def test_ai_scalp_review_warns_on_client_server_profile_mismatch():
    meta = _base_request()["screenshot_meta"]
    meta["chart_snapshot"]["profile"]["poc"] = 1.0
    ctx = _engine_d_ctx()
    warnings = __import__("ai_scalp_review.engine_d_context", fromlist=["_validate_chart_snapshot_warnings"])._validate_chart_snapshot_warnings(ctx, meta)
    assert "client_server_profile_poc_mismatch" in warnings


def test_ai_scalp_review_rejects_severe_direction_mismatch():
    from ai_scalp_review.engine_d_context import severe_chart_snapshot_reject_reason

    meta = _base_request()["screenshot_meta"]
    meta["chart_snapshot"]["selectedSignal"]["direction"] = "SHORT"
    reason = severe_chart_snapshot_reject_reason("BTCUSDT", _engine_d_ctx(), meta, has_screenshot=True)
    assert reason == "direction_mismatch"


def test_ai_scalp_review_allows_direction_none_on_server():
    from ai_scalp_review.engine_d_context import severe_chart_snapshot_reject_reason, _validate_chart_snapshot_warnings

    meta = _base_request()["screenshot_meta"]
    meta["chart_snapshot"]["selectedSignal"]["direction"] = "SHORT"

    ctx = _engine_d_ctx(direction="NONE")
    reason = severe_chart_snapshot_reject_reason("BTCUSDT", ctx, meta, has_screenshot=True)
    assert reason is None

    warnings = _validate_chart_snapshot_warnings(ctx, meta)
    assert "client_server_direction_mismatch" not in warnings


def test_chart_data_stale_allows_normal_m5_bar_lag():
    from ai_scalp_review.engine_d_context import severe_chart_snapshot_reject_reason

    now = datetime.now(timezone.utc).replace(microsecond=0)
    meta = _base_request()["screenshot_meta"]
    meta["captured_at"] = now.isoformat()
    meta["chart_timeframe"] = "M5"
    meta["chart_snapshot"]["timeframe"] = "M5"
    meta["chart_snapshot"]["latestCandleTs"] = (now - timedelta(seconds=240)).isoformat()
    reason = severe_chart_snapshot_reject_reason("BTCUSDT", _engine_d_ctx(), meta, has_screenshot=True)
    assert reason is None


def test_chart_data_stale_rejects_m1_bar_far_behind_capture():
    from ai_scalp_review.engine_d_context import severe_chart_snapshot_reject_reason

    now = datetime.now(timezone.utc).replace(microsecond=0)
    meta = _base_request()["screenshot_meta"]
    meta["captured_at"] = now.isoformat()
    meta["chart_timeframe"] = "M1"
    meta["chart_snapshot"]["timeframe"] = "M1"
    meta["chart_snapshot"]["latestCandleTs"] = (now - timedelta(seconds=400)).isoformat()
    reason = severe_chart_snapshot_reject_reason("BTCUSDT", _engine_d_ctx(), meta, has_screenshot=True)
    assert reason == "chart_data_stale"


def test_ai_scalp_review_receives_chart_snapshot_layers():
    ctx = assemble_engine_d_context(
        "BTCUSDT",
        "M1",
        screenshot_meta=_base_request()["screenshot_meta"],
        resolve_pair_fn=lambda _s: {"display": "BTCUSDT"},
        run_scalp_scan_fn=lambda _pairs: {"signals": [_mock_scan_signal()]},
        scalp_ui_signal_fn=lambda s: s,
    )
    assert ctx is not None
    assert isinstance(ctx.get("clientChartSnapshot"), dict)
    assert ctx["clientChartSnapshot"].get("renderedLayers")


def test_ai_prompt_contains_fabio_carmine_chart_layer_rules():
    ctx = _engine_d_ctx()
    ctx["clientChartSnapshot"] = _minimal_chart_snapshot()
    prompt = build_scalp_chart_review_prompt(ctx)
    assert "Fabio / Carmine" in prompt
    assert "ENTRY_NOW" in prompt
    assert "middle-of-value" in prompt


def test_assemble_engine_d_context_watchlist_candidate():
    ctx = assemble_engine_d_context(
        "BTCUSDT",
        "M1",
        screenshot_meta={"captured_at": "2026-05-21T16:31:02+00:00", "chart_timeframe": "M1"},
        resolve_pair_fn=lambda s: {"display": "BTCUSDT"},
        run_scalp_scan_fn=lambda pairs: {
            "signals": [
                _mock_scan_signal(
                    gate_result="WATCHLIST",
                    executable=False,
                    strict_fabio_pass=False,
                    fail_reasons=[],
                    soft_warnings=["rr_below_min", "strict_fabio:proxy_aggression"],
                )
            ]
        },
        scalp_ui_signal_fn=lambda s: s,
    )
    assert ctx is not None
    assert ctx["gate_result"] == "WATCHLIST"
    assert ctx["executable"] is False


def test_assemble_engine_d_context_skipped_fallback():
    skipped_row = _mock_scan_signal(
        gate_result="WATCHLIST",
        executable=False,
        soft_warnings=["fee_guard_high_cost"],
    )
    ctx = assemble_engine_d_context(
        "BTCUSDT",
        "M1",
        resolve_pair_fn=lambda s: {"display": "BTCUSDT"},
        run_scalp_scan_fn=lambda pairs: {"signals": [], "skipped": [skipped_row]},
        scalp_ui_signal_fn=lambda s: s,
    )
    assert ctx is not None
    assert ctx["direction"] == "LONG"


def test_mismatch_warnings_chart_snapshot_direction():
    from ai_scalp_review.engine_d_context import _build_mismatch_warnings

    warnings = _build_mismatch_warnings(
        {"symbol": "BTCUSDT", "direction": "LONG", "execution_tf": "M1"},
        {
            "chart_timeframe": "M1",
            "chart_snapshot": {
                "symbol": "BTCUSDT",
                "selectedSignal": {"direction": "SHORT"},
            },
            "rendered_layers": {"candles": True},
        },
    )
    assert "client_server_direction_mismatch" in warnings


@patch("athena_app.api.routes_ai_scalp_chart_review.run_scalp_chart_review")
def test_route_accepts_watchlist_candidate(mock_run):
    mock_run.return_value = _mock_provider_payload()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db = tmp.name
    app = Flask(__name__)
    cfg = dict(CONFIG)
    scalp_cfg = dict(cfg["AI_SCALP_CHART_REVIEW"])
    scalp_cfg["ENABLED"] = True
    cfg["AI_SCALP_CHART_REVIEW"] = scalp_cfg

    def resolve_pair(symbol: str):
        return {"display": "BTCUSDT", "symbol": "BTCUSDT"}

    def run_scalp_scan(pairs):
        return {
            "signals": [
                _mock_scan_signal(
                    gate_result="WATCHLIST",
                    executable=False,
                    strict_fabio_pass=False,
                    soft_warnings=["rr_below_min"],
                )
            ]
        }

    register_ai_scalp_chart_review_routes(
        app,
        SimpleNamespace(
            CONFIG=cfg,
            AUDIT_DB=tmp_db,
            json_safe=lambda x: x,
            resolve_pair_fn=resolve_pair,
            run_scalp_scan_fn=run_scalp_scan,
            scalp_ui_signal_fn=lambda s: s,
        ),
    )
    body = _base_request()
    resp = app.test_client().post("/api/ai/scalp-chart-review", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["engine_d_context"]["gate_result"] == "WATCHLIST"


def test_prompt_contains_scalp_adjudication_topics():
    prompt = build_scalp_chart_review_prompt(_engine_d_ctx())
    lowered = prompt.lower()
    for phrase in (
        "session context",
        "effort vs result",
        "trapped traders",
        "poc magnet",
        "structural stop geometry",
    ):
        assert phrase in lowered

