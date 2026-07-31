"""Engine B AI verdict comparison fail-closed boundaries."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_review.engine_b_verdict import build_engine_b_verdict_comparison


def test_ai_upgraded_engine_b_forced_false_when_engine_b_did_not_pass():
    out = build_engine_b_verdict_comparison(
        {
            "direction": "LONG",
            "passed": False,
            "confluence_score": 4.0,
            "structure_context": {"structural_verdict": "CLEAR"},
        },
        {
            "human_action": "trade",
            "visual_confirmation": "Chart confirms bullish structure and entry timing.",
        },
    )

    assert out["engineBPassed"] is False
    assert out["aiUpgradedEngineB"] is False
    assert out["finalDecision"] == "trade"


def test_ai_upgraded_engine_b_remains_advisory_when_engine_b_passed():
    out = build_engine_b_verdict_comparison(
        {
            "direction": "LONG",
            "passed": True,
            "confluence_score": 6.0,
            "engine_snapshots": {"engineB": {"passed": True, "score": 6.0, "direction": "LONG"}},
            "structure_context": {"structural_verdict": "CLEAR"},
        },
        {
            "human_action": "trade",
            "visual_confirmation": "Chart confirms aligned bullish structure.",
        },
        model_comparison={"aiUpgradedEngineB": True},
    )

    assert out["engineBPassed"] is True
    assert out["aiUpgradedEngineB"] is False
    assert out["modelClaims"]["aiUpgradedEngineB"] is True
