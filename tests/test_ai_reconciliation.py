"""Unit tests for central AI verdict arbitration."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_reconciliation import arbitrate_ai


TRACE_ID = "trace-123"


def _debate(grade: str, allowed: bool = True) -> dict:
    return {"grade": grade, "allowed": allowed, "trace_id": TRACE_ID}


def _vision(state: str) -> dict:
    return {"ai_review_state": state, "trace_id": TRACE_ID}


class TestAiArbitration:
    def test_conflicting_verdicts_block_and_blocker_wins(self):
        decision = arbitrate_ai(
            {
                "trace_id": TRACE_ID,
                "debate": _debate("STRONG_GO", True),
                "vision": _vision("REJECT"),
            }
        )

        assert decision["execution_allowed"] is False
        assert decision["decision"] == "BLOCK"
        assert decision["reason"] == "VISION_REJECT"
        assert decision["blocking_sources"] == ["vision"]
        assert decision["supporting_sources"] == ["debate"]
        assert decision["conflict_flags"] == ["AI_CONFLICT_BLOCKER_WINS"]

    def test_missing_verdicts_are_neutral_not_confirming(self):
        decision = arbitrate_ai({"trace_id": TRACE_ID})

        assert decision["execution_allowed"] is True
        assert decision["decision"] == "ALLOW"
        assert decision["reason"] == "NO_AI_VERDICTS"
        assert decision["ai_review_state"] == "REVIEW_INCOMPLETE"
        assert decision["supporting_sources"] == []

    def test_ai_failure_defaults_to_block(self):
        decision = arbitrate_ai({"trace_id": TRACE_ID, "debate": _debate("ERROR", False)})

        assert decision["execution_allowed"] is False
        assert decision["decision"] == "BLOCK"
        assert decision["reason"] == "DEBATE_AI_FAILURE"

    def test_debate_skip_is_neutral(self):
        decision = arbitrate_ai({"trace_id": TRACE_ID, "debate": _debate("SKIP", True)})

        assert decision["execution_allowed"] is True
        assert decision["decision"] == "ALLOW"
        assert decision["reason"] == "AI_NEUTRAL_OR_SKIPPED"
        assert decision["neutral_sources"] == ["debate"]

    def test_debate_skip_plus_vision_confirm_allows_with_support(self):
        decision = arbitrate_ai(
            {
                "trace_id": TRACE_ID,
                "debate": _debate("SKIP", True),
                "vision": _vision("CONFIRM"),
            }
        )

        assert decision["execution_allowed"] is True
        assert decision["decision"] == "ALLOW"
        assert decision["reason"] == "AI_SUPPORTS_EXECUTION"
        assert decision["supporting_sources"] == ["vision"]
        assert decision["neutral_sources"] == ["debate"]

    def test_vision_confirm_allows(self):
        decision = arbitrate_ai({"trace_id": TRACE_ID, "vision": _vision("CONFIRM")})

        assert decision["execution_allowed"] is True
        assert decision["decision"] == "ALLOW"
        assert decision["ai_review_state"] == "CONFIRM"
        assert decision["supporting_sources"] == ["vision"]

    def test_vision_reject_blocks(self):
        decision = arbitrate_ai({"trace_id": TRACE_ID, "vision": _vision("REJECT")})

        assert decision["execution_allowed"] is False
        assert decision["decision"] == "BLOCK"
        assert decision["reason"] == "VISION_REJECT"
        assert decision["blocking_sources"] == ["vision"]

    def test_schema_invalid_input_blocks(self):
        decision = arbitrate_ai({"trace_id": TRACE_ID, "debate": {"grade": "STRONG_GO"}})

        assert decision["execution_allowed"] is False
        assert decision["decision"] == "BLOCK"
        assert decision["schema_valid"] is False
        assert decision["reason"] == "DEBATE_SCHEMA_INVALID"

    def test_trace_id_is_required_when_requested(self):
        decision = arbitrate_ai({"debate": {"grade": "STRONG_GO", "allowed": True}})

        assert decision["execution_allowed"] is False
        assert decision["decision"] == "BLOCK"
        assert decision["reason"] == "TRACE_ID_MISSING"
        assert decision["blocking_sources"] == ["trace_id"]
