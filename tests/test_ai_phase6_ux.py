"""Focused tests for Phase 6 UX-surfaces backend primitives.

Covers: evidence_refs building + ungrounded flag, confidence calibration
binning + gap, AI disagreement detection (long/short conflict + confidence
spread), what-if replay (deep-copy non-mutation + locked-field rejection).
No live LLM calls.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import ai_ux_surfaces as ux
from config import CONFIG


@pytest.fixture(autouse=True)
def _reset_phase6_flags():
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


# ---------------------------------------------------------------------------
# Evidence refs
# ---------------------------------------------------------------------------


def test_citations_disabled_by_default():
    out = ux.build_evidence_refs([{"claim_id": "c1", "text": "x"}], {"foo": 1})
    assert out == []


def test_citations_ground_known_field():
    CONFIG["AI_CITATIONS_ENABLED"] = True
    out = ux.build_evidence_refs(
        [{"claim_id": "c1", "text": "trend is up", "source_field": "trend"}],
        {"trend": "bullish"},
    )
    assert len(out) == 1
    assert out[0]["ungrounded"] is False
    assert out[0]["source_value"] == "bullish"


def test_citations_flag_ungrounded():
    CONFIG["AI_CITATIONS_ENABLED"] = True
    out = ux.build_evidence_refs(
        [{"claim_id": "c1", "text": "momentum strong", "source_field": "momentum"}],
        {"trend": "bullish"},  # no 'momentum' key
    )
    assert len(out) == 1
    assert out[0]["ungrounded"] is True


def test_citations_handles_bad_input():
    CONFIG["AI_CITATIONS_ENABLED"] = True
    assert ux.build_evidence_refs(None, {"a": 1}) == []
    assert ux.build_evidence_refs("not a list", {"a": 1}) == []  # type: ignore[arg-type]
    assert ux.build_evidence_refs([None, "x", 42], {"a": 1}) == []  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Confidence calibration
# ---------------------------------------------------------------------------


def test_calibration_disabled_by_default():
    out = ux.confidence_calibration([{"confidence": 0.9, "outcome": "win"}])
    assert out["enabled"] is False
    assert out["bins"] == []


def test_calibration_bins_samples():
    CONFIG["AI_CONFIDENCE_CALIBRATION_ENABLED"] = True
    samples = [
        {"confidence": 0.9, "outcome": "win"},
        {"confidence": 0.85, "outcome": "loss"},
        {"confidence": 0.5, "outcome": "win"},
        {"confidence": 0.3, "outcome": "loss"},
    ]
    out = ux.confidence_calibration(samples)
    assert out["enabled"] is True
    bins_by_label = {b["bin"]: b for b in out["bins"]}
    # 0.8-1.0 bin has 2 samples, 1 win -> acc 0.5
    assert bins_by_label["0.8-1.0"]["count"] == 2
    assert bins_by_label["0.8-1.0"]["realized_accuracy"] == 0.5
    assert bins_by_label["0.8-1.0"]["avg_confidence"] == 0.875
    # gap = 0.875 - 0.5 = 0.375
    assert bins_by_label["0.8-1.0"]["gap"] == 0.375


def test_calibration_empty_bins_when_no_samples():
    CONFIG["AI_CONFIDENCE_CALIBRATION_ENABLED"] = True
    out = ux.confidence_calibration([])
    assert out["enabled"] is True
    assert all(b["count"] == 0 for b in out["bins"])


def test_calibration_handles_bad_input():
    CONFIG["AI_CONFIDENCE_CALIBRATION_ENABLED"] = True
    out = ux.confidence_calibration(None)  # type: ignore[arg-type]
    assert out["bins"] == []
    out2 = ux.confidence_calibration([None, "x", 42, {"confidence": "bad", "outcome": "win"}])  # type: ignore[list-item]
    # No valid samples landed in bins
    assert all(b["count"] == 0 for b in out2["bins"])


# ---------------------------------------------------------------------------
# AI disagreement
# ---------------------------------------------------------------------------


def test_disagreement_disabled_by_default():
    out = ux.detect_ai_disagreement({"marcus": {"decision": "long"}, "vision": {"decision": "short"}})
    assert out["enabled"] is False
    assert out["divergent"] is False


def test_disagreement_flags_long_short_conflict():
    CONFIG["AI_DISAGREEMENT_VIZ_ENABLED"] = True
    out = ux.detect_ai_disagreement(
        {"marcus": {"decision": "long", "confidence": 0.8}, "vision": {"decision": "short", "confidence": 0.7}}
    )
    assert out["enabled"] is True
    assert out["decision_agreement"] is False
    assert out["divergent"] is True


def test_disagreement_neutral_compatible():
    CONFIG["AI_DISAGREEMENT_VIZ_ENABLED"] = True
    out = ux.detect_ai_disagreement(
        {"marcus": {"decision": "long"}, "debate": {"decision": "neutral"}}
    )
    assert out["decision_agreement"] is True
    assert out["divergent"] is False


def test_disagreement_flags_confidence_spread():
    CONFIG["AI_DISAGREEMENT_VIZ_ENABLED"] = True
    out = ux.detect_ai_disagreement(
        {"marcus": {"decision": "long", "confidence": 0.9}, "vision": {"decision": "long", "confidence": 0.3}}
    )
    # Same direction but big confidence spread
    assert out["decision_agreement"] is True
    assert out["confidence_spread"] == 0.6
    assert out["divergent"] is True


def test_disagreement_handles_bad_input():
    CONFIG["AI_DISAGREEMENT_VIZ_ENABLED"] = True
    out = ux.detect_ai_disagreement(None)  # type: ignore[arg-type]
    assert out["divergent"] is False
    out2 = ux.detect_ai_disagreement({})
    assert out2["divergent"] is False


# ---------------------------------------------------------------------------
# What-if replay
# ---------------------------------------------------------------------------


def test_whatif_disabled_by_default():
    out = ux.whatif_replay({"trend": "up"}, {"trend": "down"})
    assert out["enabled"] is False
    assert out["hypothetical_context"] is None


def test_whatif_applies_overrides_to_copy():
    CONFIG["AI_WHATIF_ENABLED"] = True
    base = {"trend": "up", "score": 5}
    out = ux.whatif_replay(base, {"trend": "down", "score": 9})
    assert out["enabled"] is True
    assert out["hypothetical_context"]["trend"] == "down"
    assert out["hypothetical_context"]["score"] == 9
    # Original base NOT mutated
    assert base["trend"] == "up"
    assert base["score"] == 5
    assert set(out["applied_overrides"]) == {"trend", "score"}
    assert out["rejected_overrides"] == []


def test_whatif_rejects_locked_fields():
    CONFIG["AI_WHATIF_ENABLED"] = True
    out = ux.whatif_replay(
        {"trend": "up"},
        {"risk_max_loss": 1000, "execution_enabled": True, "sl_pips": 20, "trend": "down", "config": "x"},
    )
    assert "trend" in out["applied_overrides"]
    for k in ("risk_max_loss", "execution_enabled", "sl_pips", "config"):
        assert k in out["rejected_overrides"]
    assert "risk_max_loss" not in out["hypothetical_context"]
    assert "config" not in out["hypothetical_context"]


def test_whatif_handles_bad_input():
    CONFIG["AI_WHATIF_ENABLED"] = True
    out = ux.whatif_replay(None, {"trend": "down"})  # type: ignore[arg-type]
    assert out["hypothetical_context"] == {"trend": "down"}
    out2 = ux.whatif_replay({"a": 1}, None)  # type: ignore[arg-type]
    assert out2["hypothetical_context"] == {"a": 1}
    assert out2["applied_overrides"] == []
