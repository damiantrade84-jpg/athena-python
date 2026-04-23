"""Focused Engine B AI regressions for message formatting and annotations."""

import os
import sys
from typing import Optional, get_type_hints

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_b_ai import build_engine_b_signal_message, get_engine_b_ai_verdict


def test_build_engine_b_signal_message_preserves_explicit_zero_engine_a_values():
    message = build_engine_b_signal_message(
        pair="EUR/USD",
        direction="LONG",
        current_price=1.1,
        structure_result={},
        confidence_result={"score": 4.0, "max_possible": 5.0, "passed": True},
        engine_a_ctx={
            "direction": "LONG",
            "confluenceScore": 0.0,
            "score": 2.5,
            "maxScore": 0.0,
            "max_score": 3.0,
        },
    )

    assert "Engine A Confluence: 0.00 / 0.0 (0%)" in message


def test_get_engine_b_ai_verdict_api_key_annotation_is_optional():
    hints = get_type_hints(get_engine_b_ai_verdict)

    assert hints["xai_api_key"] == Optional[str]
