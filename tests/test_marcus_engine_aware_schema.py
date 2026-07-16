"""Marcus text review must use engine-aware schema and reviewSource."""

from ai_schemas import (
    EngineBResponse,
    EngineCMarcusResponse,
    enforce_marcus_grade_edge_consistency,
    normalize_marcus_advisory_levels,
)
from marcus_review import (
    bind_marcus_selected_style,
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


def test_engine_b_coerces_numeric_advisory_levels():
    payload = {
        "grade": "B",
        "edgeProbability": 55,
        "riskLevel": "Medium",
        "verdict": "SL should sit below sweep low.",
        "reviewSource": "engine_b_marcus",
        "levelsVerdict": "adjust",
        "suggestedSL": 161.33921043846448,
        "suggestedTP": 163.12,
    }
    normalize_marcus_advisory_levels(payload)
    validated = EngineBResponse.model_validate(payload)
    assert validated.suggestedSL == "161.33921043846448"
    assert validated.suggestedTP == "163.12"


def test_marcus_headline_is_bound_to_caller_selected_style():
    result = bind_marcus_selected_style(
        {
            "grade": "A+",
            "edgeProbability": 82,
            "riskLevel": "Low",
            "style_ratings": {
                "intraday": {"grade": "B", "edgeProbability": 59, "riskLevel": "Medium"},
                "swing": {"grade": "A+", "edgeProbability": 82, "riskLevel": "Low"},
            },
        },
        "intraday",
    )

    assert result["resolvedStyle"] == "INTRADAY"
    assert result["tradeStyle"] == "INTRADAY"
    assert result["selectedStyleGrade"] == "B"
    assert result["grade"] == "B"
    assert result["edgeProbability"] == 59
    assert result["riskLevel"] == "Medium"


def test_marcus_does_not_borrow_nonselected_style_when_selected_is_missing():
    result = bind_marcus_selected_style(
        {
            "grade": "A+",
            "edgeProbability": 82,
            "riskLevel": "Low",
            "style_ratings": {
                "swing": {"grade": "A+", "edgeProbability": 82, "riskLevel": "Low"},
            },
        },
        "intraday",
    )

    assert result["resolvedStyle"] == "INTRADAY"
    assert result["selectedStyleGrade"] == "C"
    assert result["grade"] == "C"
    assert result["edgeProbability"] == 50.0
    assert result["riskLevel"] == "Medium"
