"""Focused tests for the Phase 3 golden-set harness and drift detector.

Covers: golden case structure, evaluate_response pass/fail paths,
run_golden_set aggregation, drift detector thresholds + skip-when-small,
config gating. No live LLM calls (uses a stub llm_call).
"""

from __future__ import annotations

import os
import sys

import pytest

# Ensure repo root importable when run from anywhere
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tests.ai_eval import golden_set
import ai_drift_detector as drift


# ---------------------------------------------------------------------------
# golden_set
# ---------------------------------------------------------------------------


def test_golden_cases_have_required_keys():
    for c in golden_set.GOLDEN_CASES:
        assert "id" in c, f"case missing id: {c}"
        assert "surface" in c, f"case {c.get('id')} missing surface"
        assert "inputs" in c, f"case {c.get('id')} missing inputs"
        assert "expect" in c, f"case {c.get('id')} missing expect"


def test_list_surfaces_returns_distinct():
    surfaces = golden_set.list_surfaces()
    assert len(surfaces) == len(set(surfaces))
    # Should include the core surfaces
    assert "marcus" in surfaces
    assert "engine_b" in surfaces
    assert "vision" in surfaces


def test_get_cases_filter_by_surface():
    marcus = golden_set.get_cases("marcus")
    assert all(c["surface"] == "marcus" for c in marcus)
    assert len(marcus) >= 2


def test_evaluate_response_pass():
    case = golden_set.get_cases("marcus")[0]
    resp = {"decision": "long", "confidence": 0.7}
    out = golden_set.evaluate_response(case, resp)
    assert out["pass"] is True
    assert out["failures"] == []


def test_evaluate_response_missing_required_field():
    case = golden_set.get_cases("marcus")[0]
    resp = {"decision": "long"}  # missing confidence
    out = golden_set.evaluate_response(case, resp)
    assert out["pass"] is False
    assert any("missing_field:confidence" in f for f in out["failures"])


def test_evaluate_response_decision_out_of_range():
    case = golden_set.get_cases("marcus")[0]
    resp = {"decision": "yolo", "confidence": 0.5}
    out = golden_set.evaluate_response(case, resp)
    assert out["pass"] is False
    assert any("decision_out_of_range" in f for f in out["failures"])


def test_evaluate_response_confidence_out_of_range():
    case = golden_set.get_cases("marcus")[0]
    resp = {"decision": "long", "confidence": 1.5}
    out = golden_set.evaluate_response(case, resp)
    assert out["pass"] is False
    assert any("confidence_out_of_range" in f for f in out["failures"])


def test_evaluate_response_forbidden_key_present():
    case = next(c for c in golden_set.GOLDEN_CASES if c["surface"] == "vision")
    resp = {"decision": "long", "auto_upgrade_trade": True}
    out = golden_set.evaluate_response(case, resp)
    assert out["pass"] is False
    assert any("forbidden_field_present:auto_upgrade_trade" in f for f in out["failures"])


def test_evaluate_response_non_dict():
    case = golden_set.get_cases("marcus")[0]
    out = golden_set.evaluate_response(case, None)
    assert out["pass"] is False
    assert "response_not_dict" in out["failures"]


def test_run_golden_set_with_stub_passing():
    def stub(case, inputs):
        return {"decision": "long", "confidence": 0.6, "stance": "bull", "verdict": "bull", "sentiment": "neutral", "confirmation": True}

    out = golden_set.run_golden_set(stub)
    assert out["total"] == len(golden_set.GOLDEN_CASES)
    assert out["total"] == out["passed"] + out["failed"]
    assert out["do_not_auto_apply"] is True


def test_run_golden_set_records_exceptions():
    def bad(case, inputs):
        raise RuntimeError("boom")

    out = golden_set.run_golden_set(bad)
    assert out["total"] == len(golden_set.GOLDEN_CASES)
    assert out["failed"] == out["total"]
    for r in out["results"]:
        assert r["pass"] is False
        assert any("llm_call_exception" in f for f in r["failures"])


def test_run_golden_set_surface_filter():
    def stub(case, inputs):
        return {"decision": "long", "confidence": 0.6, "stance": "bull", "verdict": "bull", "sentiment": "neutral", "confirmation": True}

    out = golden_set.run_golden_set(stub, surface="marcus")
    assert all(r["surface"] == "marcus" for r in out["results"])
    assert out["total"] == len(golden_set.get_cases("marcus"))


# ---------------------------------------------------------------------------
# drift detector
# ---------------------------------------------------------------------------


def _mk_samples(n, decision="long", entry=True, confidence=0.6):
    return [{"decision": decision, "entryAllowedNow": entry, "confidence": confidence} for _ in range(n)]


def test_drift_disabled_by_default():
    drift.CONFIG.pop("AI_DRIFT_DETECTION_ENABLED", None)
    assert drift.is_drift_detection_enabled() is False
    assert drift.detect_drift_if_enabled([], [], surface="x") is None


def test_drift_enabled_when_configured():
    drift.CONFIG["AI_DRIFT_DETECTION_ENABLED"] = True
    try:
        assert drift.is_drift_detection_enabled() is True
    finally:
        drift.CONFIG.pop("AI_DRIFT_DETECTION_ENABLED", None)


def test_drift_skipped_when_insufficient_samples():
    out = drift.detect_drift(_mk_samples(3), _mk_samples(15), surface="marcus")
    assert out["skipped"] == "insufficient_samples"
    assert out["findings"] == []
    assert out["do_not_auto_apply"] is True


def test_drift_no_finding_when_distributions_match():
    tw = _mk_samples(50, decision="long", entry=True, confidence=0.6)
    lw = _mk_samples(50, decision="long", entry=True, confidence=0.6)
    out = drift.detect_drift(tw, lw, surface="marcus")
    assert out["findings"] == []


def test_drift_flags_entry_rate_change():
    tw = _mk_samples(50, decision="long", entry=True, confidence=0.6)
    lw = _mk_samples(50, decision="long", entry=False, confidence=0.6)
    out = drift.detect_drift(tw, lw, surface="marcus")
    metrics = [f["metric"] for f in out["findings"]]
    assert "entry_allowed_rate" in metrics


def test_drift_flags_confidence_change():
    tw = _mk_samples(50, decision="long", entry=True, confidence=0.9)
    lw = _mk_samples(50, decision="long", entry=True, confidence=0.3)
    out = drift.detect_drift(tw, lw, surface="marcus")
    metrics = [f["metric"] for f in out["findings"]]
    assert "mean_confidence" in metrics


def test_drift_flags_decision_distribution_change():
    tw = _mk_samples(50, decision="long", entry=True, confidence=0.6)
    lw = _mk_samples(50, decision="short", entry=True, confidence=0.6)
    out = drift.detect_drift(tw, lw, surface="marcus")
    metrics = [f["metric"] for f in out["findings"]]
    assert "decision_distribution" in metrics


def test_drift_never_raises_on_bad_input():
    out = drift.detect_drift(None, "not a list", surface="x")  # type: ignore[arg-type]
    assert out["findings"] == []
    assert out["do_not_auto_apply"] is True


def test_drift_thresholds_respected():
    # Small confidence delta below default threshold (0.10) → no finding
    tw = _mk_samples(50, decision="long", entry=True, confidence=0.62)
    lw = _mk_samples(50, decision="long", entry=True, confidence=0.58)
    out = drift.detect_drift(tw, lw, surface="marcus")
    metrics = [f["metric"] for f in out["findings"]]
    assert "mean_confidence" not in metrics
