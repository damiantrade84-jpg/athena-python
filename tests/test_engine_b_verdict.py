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


def test_engine_b_short_does_not_treat_longer_ema_as_direction_contradiction():
    out = build_engine_b_verdict_comparison(
        {
            "direction": "SHORT",
            "passed": True,
            "confluence_score": 6.0,
            "engine_snapshots": {"engineB": {"passed": True, "score": 6.0, "direction": "SHORT"}},
        },
        {
            "human_action": "wait",
            "visual_confirmation": "Price accepted below the longer EMAs after the impulse.",
            "chartReadSummary": "Along the EMA cluster, structure confirms the short.",
        },
    )
    assert out["chartContradictsEngineBDirection"] is not True
    assert out["chartConfirmsEngineBDirection"] is not False
    assert out["comparisonVerdict"] in {
        "engine_b_direction_confirmed_wait",
        "mixed",
        "engine_b_confirmed",
    }


def test_engine_b_short_whole_word_long_still_contradicts():
    out = build_engine_b_verdict_comparison(
        {
            "direction": "SHORT",
            "passed": True,
            "confluence_score": 6.0,
            "engine_snapshots": {"engineB": {"passed": True, "score": 6.0, "direction": "SHORT"}},
        },
        {
            "human_action": "wait",
            "visual_confirmation": "Chart looks long and bullish against the short.",
        },
    )
    assert out["chartConfirmsEngineBDirection"] is False
