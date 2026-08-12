"""P2-4: factorDiagnostics are required inputs to the completeness scorer."""

from __future__ import annotations

import json

from ai_review.context_diagnostics import (
    FACTOR_UNVERIFIABLE_SCORE_CAP,
    build_context_diagnostics,
)
from ai_review.normalizer import normalize_chart_review_response


def _ctx(**overrides):
    ctx = {
        "symbol": "XAG/USD",
        "timeframe": "H4",
        "asset_group": "precious_trackers",
        "direction": "LONG",
        "passed": True,
        "primary_engine": "A",
        "review_type": "engine_a_chart",
        "factor_diagnostics": {
            "trendCoherence": {"agreement_count": 4, "coherence_ratio": 0.8},
            "minDirectional": 0.05,
            "effectiveMinDirectional": 0.05,
        },
    }
    ctx.update(overrides)
    return ctx


def _ai(**overrides):
    payload = {
        "verdict": "CAUTION",
        "confidence": 58,
        "human_action": "wait",
    }
    payload.update(overrides)
    return normalize_chart_review_response(json.dumps(payload))


def test_empty_factor_diagnostics_is_unverifiable_not_partial_84():
    """Scan 1 regression: empty factorDiagnostics must not score 84/partial."""
    diagnostics = build_context_diagnostics(_ctx(factor_diagnostics={}), _ai())
    completeness = diagnostics["contextCompleteness"]

    assert completeness["status"] == "unverifiable"
    assert completeness["score"] <= FACTOR_UNVERIFIABLE_SCORE_CAP
    assert completeness["score"] != 84
    assert completeness["factorDiagnosticsVerifiable"] is False
    assert set(completeness["missingFactorDiagnostics"]) == {
        "factor_min_directional",
        "factor_effective_min_directional",
        "factor_trend_coherence",
    }


def test_missing_factor_diagnostics_block_key_appears_in_required():
    diagnostics = build_context_diagnostics(_ctx(factor_diagnostics={}), _ai())
    required_keys = [item["key"] for item in diagnostics["missingContextDetailed"]["required"]]
    assert "factor_min_directional" in required_keys
    for item in diagnostics["missingContextDetailed"]["required"]:
        if item["key"].startswith("factor_"):
            assert item["blocksTrade"] is True
            assert item["impact"] == "high"


def test_missing_only_min_directional_still_unverifiable():
    ctx = _ctx(
        factor_diagnostics={
            "trendCoherence": {"agreement_count": 4},
            "effectiveMinDirectional": 0.05,
        }
    )
    completeness = build_context_diagnostics(ctx, _ai())["contextCompleteness"]
    assert completeness["status"] == "unverifiable"
    assert completeness["missingFactorDiagnostics"] == ["factor_min_directional"]


def test_empty_trend_coherence_dict_is_not_evidence():
    """An empty dict carries no agreement_count / coherence_ratio."""
    ctx = _ctx(
        factor_diagnostics={
            "trendCoherence": {},
            "minDirectional": 0.05,
            "effectiveMinDirectional": 0.05,
        }
    )
    completeness = build_context_diagnostics(ctx, _ai())["contextCompleteness"]
    assert completeness["status"] == "unverifiable"
    assert completeness["missingFactorDiagnostics"] == ["factor_trend_coherence"]


def test_zero_valued_min_directional_counts_as_present():
    """0.0 is a real floor, not a missing field."""
    ctx = _ctx(
        factor_diagnostics={
            "trendCoherence": {"agreement_count": 0},
            "minDirectional": 0.0,
            "effectiveMinDirectional": 0.0,
        }
    )
    completeness = build_context_diagnostics(ctx, _ai())["contextCompleteness"]
    assert completeness["factorDiagnosticsVerifiable"] is True
    assert completeness["status"] != "unverifiable"


def test_snake_case_aliases_accepted():
    ctx = _ctx(
        factor_diagnostics={
            "trend_coherence": {"agreement_count": 3},
            "min_directional": 0.05,
            "effective_min_directional": 0.06,
        }
    )
    completeness = build_context_diagnostics(ctx, _ai())["contextCompleteness"]
    assert completeness["factorDiagnosticsVerifiable"] is True


def test_engine_b_review_is_exempt():
    """Engine B is naked structure and never emits factorDiagnostics."""
    ctx = _ctx(
        factor_diagnostics={},
        primary_engine="B",
        review_type="engine_b_chart",
    )
    completeness = build_context_diagnostics(ctx, _ai())["contextCompleteness"]
    assert completeness["status"] != "unverifiable"
    assert completeness["missingFactorDiagnostics"] == []


def test_context_without_declared_engine_is_exempt():
    """Ad-hoc contexts that declare no engine must not trip the requirement."""
    ctx = _ctx(factor_diagnostics={})
    ctx.pop("primary_engine")
    ctx.pop("review_type")
    completeness = build_context_diagnostics(ctx, _ai())["contextCompleteness"]
    assert completeness["status"] != "unverifiable"


def test_complete_factor_diagnostics_do_not_cap_score():
    completeness = build_context_diagnostics(_ctx(), _ai())["contextCompleteness"]
    assert completeness["factorDiagnosticsVerifiable"] is True
    assert completeness["missingFactorDiagnostics"] == []
    assert completeness["score"] > FACTOR_UNVERIFIABLE_SCORE_CAP
