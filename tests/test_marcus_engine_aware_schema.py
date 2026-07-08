"""Marcus text review must use engine-aware schema and reviewSource."""

from ai_schemas import EngineBResponse, EngineCMarcusResponse, enforce_marcus_grade_edge_consistency
from marcus_review import (
    marcus_response_model,
    marcus_review_source,
    marcus_structured_required_keys,
    resolve_marcus_engine_source,
)


def test_engine_b_review_source_and_schema():
    source = resolve_marcus_engine_source({"is_naked": True, "engine_source": "engine_b"})
    assert source == "engine_b"
    assert marcus_review_source(source) == "engine_b_marcus"
    assert marcus_response_model(source) is EngineBResponse

    payload = {
        "grade": "B",
        "edgeProbability": 55,
        "riskLevel": "Medium",
        "verdict": "Structure clear with acceptable RR.",
        "reviewSource": "engine_b_marcus",
        "style_ratings": {
            "scalp": {"grade": "C", "edgeProbability": 40, "riskLevel": "High"},
            "intraday": {"grade": "B", "edgeProbability": 55, "riskLevel": "Medium"},
            "swing": {"grade": "B", "edgeProbability": 58, "riskLevel": "Medium"},
        },
    }
    EngineBResponse.model_validate(payload)
    required = marcus_structured_required_keys(source)
    assert "trend_score" not in required
    assert "total_score" not in required


def test_engine_c_review_source_and_relaxed_schema():
    source = resolve_marcus_engine_source(
        {"decision_state": "execute", "conviction": 0.82, "engine_c": {"tier": "HIGH"}}
    )
    assert source == "engine_c"
    assert marcus_review_source(source) == "engine_c_marcus"
    assert marcus_response_model(source) is EngineCMarcusResponse

    payload = {
        "grade": "A",
        "edgeProbability": 78,
        "riskLevel": "Medium",
        "verdict": "Consensus execute with high conviction.",
        "reviewSource": "engine_c_marcus",
        "reason": "Engine C tier HIGH with aligned A/B.",
    }
    EngineCMarcusResponse.model_validate(payload)


def test_grade_edge_consistency_clamps_mismatch():
    result = {"grade": "B", "edgeProbability": 90, "warnings": []}
    enforce_marcus_grade_edge_consistency(result)
    assert result["edgeProbability"] == 55
    assert "GRADE_EDGE_MISMATCH_ADJUSTED" in result["warnings"]
