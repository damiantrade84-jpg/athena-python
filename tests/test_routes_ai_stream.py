"""Focused tests for the new SSE stream routes (Phase 6).

Covers: /api/ai/chart-review/stream and /api/ai/scalp-chart-review/stream
return 410 when AI_STREAMING_ENABLED is off (fail-closed fallback to the
non-streaming endpoint). Uses the real CONFIG dict.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from athena_app.api.routes_ai_chart_review import register_ai_chart_review_routes
from athena_app.api.routes_ai_scalp_chart_review import register_ai_scalp_chart_review_routes
from config import CONFIG


class _Log:
    def warning(self, *_a, **_k):
        pass

    def exception(self, *_a, **_k):
        pass

    def error(self, *_a, **_k):
        pass

    def debug(self, *_a, **_k):
        pass

    def info(self, *_a, **_k):
        pass


@pytest.fixture(autouse=True)
def _reset_streaming_gate():
    CONFIG.pop("AI_STREAMING_ENABLED", None)
    yield
    CONFIG.pop("AI_STREAMING_ENABLED", None)


def _chart_review_client():
    app = Flask(__name__)
    register_ai_chart_review_routes(
        app,
        SimpleNamespace(
            CONFIG={**CONFIG, "AI_CHART_REVIEW": {"ENABLED": True}},
            AUDIT_DB="",
            json_safe=lambda v: v,
            log=_Log(),
            resolve_pair_fn=lambda symbol: None,
            analyze_pair_fn=lambda *a, **k: None,
            btc_bias_fn=lambda: None,
            naked_analysis_fn=lambda *a, **k: None,
            last_engine_b_rows_fn=lambda: [],
        ),
    )
    return app.test_client()


def _scalp_review_client():
    app = Flask(__name__)
    register_ai_scalp_chart_review_routes(
        app,
        SimpleNamespace(
            CONFIG={**CONFIG, "AI_SCALP_CHART_REVIEW": {"ENABLED": True}},
            AUDIT_DB="",
            json_safe=lambda v: v,
            log=_Log(),
            resolve_pair_fn=lambda symbol: None,
            run_scalp_scan_fn=lambda pairs: {"signals": []},
            scalp_ui_signal_fn=lambda *a, **k: None,
        ),
    )
    return app.test_client()


def test_chart_review_stream_returns_410_when_disabled():
    CONFIG.pop("AI_STREAMING_ENABLED", None)
    resp = _chart_review_client().post("/api/ai/chart-review/stream", json={})
    assert resp.status_code == 410
    body = resp.get_json()
    assert body["error"] == "streaming_disabled"
    assert body["fallback"] == "/api/ai/chart-review"


def test_scalp_chart_review_stream_returns_410_when_disabled():
    CONFIG.pop("AI_STREAMING_ENABLED", None)
    resp = _scalp_review_client().post("/api/ai/scalp-chart-review/stream", json={})
    assert resp.status_code == 410
    body = resp.get_json()
    assert body["error"] == "streaming_disabled"
    assert body["fallback"] == "/api/ai/scalp-chart-review"
