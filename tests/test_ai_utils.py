from ai_schemas import EngineAResponse
from ai_utils import build_marcus_review_incomplete_result


def test_marcus_review_incomplete_result_is_fail_closed_and_schema_valid():
    result = build_marcus_review_incomplete_result(
        {
            "pair": "BTC/USDT",
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "price": 100.0,
            "sl": 105.0,
            "tp1": 90.0,
            "style": "scalp",
        },
        reason="empty provider response",
        provider="openai",
        selected_provider="openai",
        model="gpt-5.5",
    )

    EngineAResponse.model_validate(result)
    assert result["grade"] == "F"
    assert result["riskLevel"] == "High"
    assert result["edgeProbability"] == 0
    assert result["parse_success"] is False
    assert result["ai_action"] == "reject"
    assert "empty provider response" in result["warnings"][0]
    assert "error" not in result
    assert result["style_ratings"]["scalp"]["grade"] == "F"
