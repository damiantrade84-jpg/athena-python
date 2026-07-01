"""Focused tests for Phase 6 UX surface routes (routes_ai_ux.py).

Covers: config gating (disabled -> enabled:false/empty), enabled shape,
fail-closed on bad input, what-if locked-prefix rejection, calibration
returns bins shape. Uses the real CONFIG dict (mutating keys) like
tests/test_ai_phase6_ux.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from athena_app.api.routes_ai_ux import register_ai_ux_routes
from config import CONFIG


@pytest.fixture(autouse=True)
def _reset_ux_gates():
    for k in (
        "AI_CITATIONS_ENABLED",
        "AI_CONFIDENCE_CALIBRATION_ENABLED",
        "AI_DISAGREEMENT_VIZ_ENABLED",
        "AI_WHATIF_ENABLED",
    ):
        CONFIG.pop(k, None)
    yield
    for k in (
        "AI_CITATIONS_ENABLED",
        "AI_CONFIDENCE_CALIBRATION_ENABLED",
        "AI_DISAGREEMENT_VIZ_ENABLED",
        "AI_WHATIF_ENABLED",
    ):
        CONFIG.pop(k, None)


class _Log:
    def warning(self, *_a, **_k):
        pass

    def error(self, *_a, **_k):
        pass

    def debug(self, *_a, **_k):
        pass


def _client():
    app = Flask(__name__)
    register_ai_ux_routes(app, SimpleNamespace(CONFIG=CONFIG, AUDIT_DB="", log=_Log()))
    return app.test_client()


# ---------------------------------------------------------------------------
# evidence-refs
# ---------------------------------------------------------------------------


def test_evidence_refs_disabled_returns_empty():
    resp = _client().post("/api/ai/evidence-refs", json={"claims": [{"text": "x"}], "sources": {}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["enabled"] is False
    assert body["evidence_refs"] == []
    assert body["do_not_auto_apply"] is True


def test_evidence_refs_enabled_returns_grounded_refs():
    CONFIG["AI_CITATIONS_ENABLED"] = True
    resp = _client().post(
        "/api/ai/evidence-refs",
        json={
            "claims": [{"claim_id": "c1", "text": "trend up", "source_field": "trend"}],
            "sources": {"trend": "bullish"},
        },
    )
    body = resp.get_json()
    assert body["enabled"] is True
    assert len(body["evidence_refs"]) == 1
    assert body["evidence_refs"][0]["ungrounded"] is False
    assert body["evidence_refs"][0]["source_value"] == "bullish"


def test_evidence_refs_handles_bad_body():
    CONFIG["AI_CITATIONS_ENABLED"] = True
    resp = _client().post("/api/ai/evidence-refs", json=None)  # type: ignore[arg-type]
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["evidence_refs"] == []


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


def test_calibration_disabled_returns_empty_bins():
    resp = _client().get("/api/ai/calibration?surface=marcus&lookback_days=30")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["enabled"] is False
    assert body["bins"] == []
    assert body["do_not_auto_apply"] is True


def test_calibration_enabled_returns_bin_structure():
    CONFIG["AI_CONFIDENCE_CALIBRATION_ENABLED"] = True
    resp = _client().get("/api/ai/calibration?surface=marcus&lookback_days=7")
    body = resp.get_json()
    assert body["enabled"] is True
    assert isinstance(body["bins"], list)
    # No samples in test env -> all bins have count 0
    assert all(b["count"] == 0 for b in body["bins"])
    assert body["surface"] == "marcus"
    assert body["lookback_days"] == 7


def test_calibration_clamps_bad_lookback():
    CONFIG["AI_CONFIDENCE_CALIBRATION_ENABLED"] = True
    resp = _client().get("/api/ai/calibration?lookback_days=99999")
    body = resp.get_json()
    assert body["lookback_days"] == 365


# ---------------------------------------------------------------------------
# disagreement
# ---------------------------------------------------------------------------


def test_disagreement_disabled_returns_not_divergent():
    resp = _client().post(
        "/api/ai/disagreement",
        json={"verdicts": {"marcus": {"decision": "long"}, "vision": {"decision": "short"}}},
    )
    body = resp.get_json()
    assert body["enabled"] is False
    assert body["divergent"] is False


def test_disagreement_enabled_flags_conflict():
    CONFIG["AI_DISAGREEMENT_VIZ_ENABLED"] = True
    resp = _client().post(
        "/api/ai/disagreement",
        json={"verdicts": {"marcus": {"decision": "long", "confidence": 0.8}, "vision": {"decision": "short", "confidence": 0.7}}},
    )
    body = resp.get_json()
    assert body["enabled"] is True
    assert body["divergent"] is True
    assert body["decision_agreement"] is False


def test_disagreement_handles_bad_body():
    CONFIG["AI_DISAGREEMENT_VIZ_ENABLED"] = True
    resp = _client().post("/api/ai/disagreement", json=None)  # type: ignore[arg-type]
    body = resp.get_json()
    assert body["divergent"] is False


# ---------------------------------------------------------------------------
# what-if
# ---------------------------------------------------------------------------


def test_whatif_disabled_returns_none_context():
    resp = _client().post("/api/ai/whatif", json={"base_context": {"trend": "up"}, "overrides": {"trend": "down"}})
    body = resp.get_json()
    assert body["enabled"] is False
    assert body["hypothetical_context"] is None


def test_whatif_enabled_applies_overrides():
    CONFIG["AI_WHATIF_ENABLED"] = True
    resp = _client().post(
        "/api/ai/whatif",
        json={"base_context": {"trend": "up"}, "overrides": {"trend": "down", "score": 9}},
    )
    body = resp.get_json()
    assert body["enabled"] is True
    assert body["hypothetical_context"]["trend"] == "down"
    assert body["hypothetical_context"]["score"] == 9
    assert set(body["applied_overrides"]) == {"trend", "score"}
    assert body["do_not_auto_apply"] is True


def test_whatif_rejects_locked_prefixes():
    CONFIG["AI_WHATIF_ENABLED"] = True
    resp = _client().post(
        "/api/ai/whatif",
        json={"base_context": {"trend": "up"}, "overrides": {"risk_max_loss": 1000, "sl_pips": 20, "trend": "down"}},
    )
    body = resp.get_json()
    assert "trend" in body["applied_overrides"]
    assert "risk_max_loss" in body["rejected_overrides"]
    assert "sl_pips" in body["rejected_overrides"]
    assert "risk_max_loss" not in body["hypothetical_context"]


def test_whatif_handles_bad_body():
    CONFIG["AI_WHATIF_ENABLED"] = True
    resp = _client().post("/api/ai/whatif", json=None)  # type: ignore[arg-type]
    body = resp.get_json()
    # base_context defaults to {} ; overrides defaults to {}
    assert body["hypothetical_context"] == {}
    assert body["applied_overrides"] == []
