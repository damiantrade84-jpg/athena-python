"""P2-6: full component decomposition and documented contribution semantics."""

from __future__ import annotations

from ai_review.score_attribution import (
    COMPONENT_NAMES,
    CONTRIBUTION_SEMANTICS,
    build_component_decomposition,
)


def _audit_factor_diagnostics():
    """The audit payload: only trend and location carried a contribution."""
    return {
        "components": {
            "trend": {
                "signal": 0.62,
                "quality": 0.71,
                "weight": 0.4886,
                "contribution": 0.278,
                "available": True,
            },
            "location": {
                "signal": 0.11,
                "quality": 0.24,
                "weight": 0.2273,
                "contribution": 0.056,
                "available": True,
            },
        }
    }


def test_all_seven_components_are_emitted():
    result = build_component_decomposition(_audit_factor_diagnostics(), raw_score=1.222)
    for name in COMPONENT_NAMES:
        assert name in result["components"], f"{name} missing from decomposition"
    assert result["componentCount"] >= 7


def test_absent_components_are_visible_gaps_not_silent_drops():
    result = build_component_decomposition(_audit_factor_diagnostics(), raw_score=1.222)
    assert result["complete"] is False
    assert "momentum" in result["missingComponents"]
    assert result["components"]["momentum"]["present"] is False
    assert result["components"]["momentum"]["contribution"] is None
    assert "missing from factorDiagnostics" in result["components"]["momentum"]["reason"]


def test_unattributed_residual_is_reported():
    """73% unattributed must be stated, not left for the reader to notice."""
    result = build_component_decomposition(_audit_factor_diagnostics(), raw_score=1.222)
    assert result["attributedTotal"] == 0.334
    assert result["unattributed"] == 0.888
    assert result["unattributedPct"] == 72.67


def test_contribution_semantics_are_documented_in_payload():
    result = build_component_decomposition(_audit_factor_diagnostics(), raw_score=1.222)
    semantics = result["contributionSemantics"]
    assert semantics is CONTRIBUTION_SEMANTICS
    assert "weight x quality" in semantics["formula"]
    # The multiplicative-vs-additive ambiguity must be stated explicitly.
    assert "NOT an exact decomposition" in semantics["combination"]
    assert "will not equal rawScore" in semantics["readingWarning"]


def test_complete_payload_reports_complete():
    components = {
        name: {"signal": 0.1, "quality": 0.5, "weight": 0.14, "contribution": 0.07,
               "available": True}
        for name in COMPONENT_NAMES
    }
    result = build_component_decomposition({"components": components}, raw_score=0.49)
    assert result["complete"] is True
    assert result["missingComponents"] == []
    assert result["attributedTotal"] == 0.49
    assert result["unattributed"] == 0.0


def test_unexpected_component_is_surfaced_not_dropped():
    payload = _audit_factor_diagnostics()
    payload["components"]["experimental_factor"] = {
        "signal": 0.2, "quality": 0.3, "weight": 0.1, "contribution": 0.03,
        "available": True,
    }
    result = build_component_decomposition(payload, raw_score=1.222)
    assert result["components"]["experimental_factor"]["unexpectedComponent"] is True


def test_empty_factor_diagnostics_yields_no_attribution():
    result = build_component_decomposition({}, raw_score=1.222)
    assert result["attributedTotal"] is None
    assert result["unattributed"] is None
    assert result["complete"] is False
    assert len(result["missingComponents"]) == len(COMPONENT_NAMES)


def test_flat_deadband_zeroed_contributions_still_attribute():
    """A FLAT resolution zeroes every contribution — that must read as 0, not None."""
    components = {
        name: {"signal": 0.1, "quality": 0.5, "weight": 0.14, "contribution": 0.0,
               "available": True}
        for name in COMPONENT_NAMES
    }
    result = build_component_decomposition({"components": components}, raw_score=0.0)
    assert result["attributedTotal"] == 0.0
    assert result["complete"] is True
    assert result["unattributedPct"] is None  # raw_score 0 -> no percentage
