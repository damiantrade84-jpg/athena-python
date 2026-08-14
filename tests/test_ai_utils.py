from ai_schemas import EngineAResponse
from ai_utils import (
    build_marcus_review_incomplete_result,
    decay_ai_numeric_fallback,
    parse_decay_ai_verdict,
)


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


def test_parse_decay_ai_verdict_accepts_json_and_loose_text():
    parsed = parse_decay_ai_verdict('{"verdict":"HOLD","urgency":"LOW","reasoning":"thesis intact"}')
    assert parsed["verdict"] == "HOLD"
    loose = parse_decay_ai_verdict("I would WATCH this closely until the next close.")
    assert loose["verdict"] == "WATCH"
    assert parse_decay_ai_verdict("") is None


def test_decay_ai_numeric_fallback_never_exits():
    watch = decay_ai_numeric_fallback(decay_pct=25.0, direction_flip=True)
    hold = decay_ai_numeric_fallback(decay_pct=2.0, direction_flip=False)
    assert watch["verdict"] == "WATCH"
    assert hold["verdict"] == "HOLD"
    assert watch["fallback"] == "numeric"
