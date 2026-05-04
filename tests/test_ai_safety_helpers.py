"""Unit tests for AI safety helpers — trace IDs, temperatures, schema clamp (no athena import)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_schemas import (
    EngineAResponse,
    JudgeVerdictResponse,
    evaluate_engine_a_ai_advisory_rules,
)
from ai_signal_trace import ensure_trace_id, generate_signal_trace_id
from config import AISafetyConstants, AITemperatureConfig
from ai_review_logger import extract_engine_d_audit_state
from prompt_versions import PROMPT_VERSIONS, get_prompt_version


class TestSignalTraceId:
    """Audit HIGH-010 — UUIDv7 when available, else timestamp-prefixed uuid4."""

    def test_generate_non_empty(self):
        tid = generate_signal_trace_id()
        assert isinstance(tid, str)
        assert len(tid) >= 8

    def test_ensure_trace_id_idempotent(self):
        sig = {}
        ensure_trace_id(sig)
        t1 = sig["trace_id"]
        ensure_trace_id(sig)
        assert sig["trace_id"] == t1


class TestAITemperatureGates:
    """Audit CRIT-005 — gate surfaces forced to 0.0 sampling temperature."""

    def test_debate_judge_zero_when_forced(self):
        assert AISafetyConstants.FORCE_ZERO_TEMP_ON_GATES is True
        assert AITemperatureConfig.get_temperature("debate_judge") == 0.0

    def test_vision_zero_when_forced(self):
        assert AITemperatureConfig.get_temperature("vision") == 0.0


class TestPromptVersionRegistry:
    """Audit ATH-003 — registry tracks Marcus expert prompt id."""

    def test_marcus_maps_to_configured_version(self):
        ver = get_prompt_version("marcus_expert")
        assert ver == PROMPT_VERSIONS["marcus_expert"]
        assert "EXPERT_PROMPT" in ver


class TestJudgeVerdictSchemaClamp:
    """Backward-compatible field name score_adjustment; positive values clamped."""

    def test_positive_adjustment_clamped_in_schema(self):
        j = JudgeVerdictResponse(grade="WEAK_GO", reasoning="x", score_adjustment=0.5)
        assert j.score_adjustment == 0.0

    def test_negative_adjustment_preserved(self):
        j = JudgeVerdictResponse(grade="WEAK_GO", reasoning="x", score_adjustment=-0.2)
        assert j.score_adjustment == -0.2


class TestMarcusStructuredRules:
    def test_engine_a_response_accepts_structured_scores(self):
        parsed = EngineAResponse(
            symbol="BTCUSDT",
            timeframe="H4",
            bias="long",
            setup_type="breakout_retest",
            trend_score=18,
            structure_score=17,
            momentum_score=13,
            liquidity_score=8,
            risk_score=7,
            confirmation_score=14,
            total_score=77,
            grade="B",
            ai_action="needs_confirmation",
            blocking_reasons=["RR_BELOW_MIN"],
            reason="Good structure, but risk/reward is below required threshold.",
            verdict="Needs confirmation.",
            narrative="trend_score 18 and structure_score 17 support the setup.",
            entryZone="100",
            invalidation="95",
            keyLevels="S1 95 / R1 110",
            positionSizing="Quarter due to RR.",
            tradeStyle="INTRADAY",
            tradeStyleReason="H4/H1 setup.",
            warnings=["RR_BELOW_MIN"],
            edgeProbability=68,
            riskLevel="Medium",
        )

        assert parsed.total_score == 77

    def test_advisory_rules_reject_missing_stop_loss(self):
        out = evaluate_engine_a_ai_advisory_rules(
            {"total_score": 88},
            {"pair": "BTCUSDT", "rr1": 2.0},
        )

        assert out["advisory_rule_trade_allowed"] is False
        assert out["advisory_rule_action"] == "reject"
        assert "NO_STOP_LOSS" in out["advisory_blocking_reasons"]

    def test_advisory_rules_bucket_watchlist_without_blockers(self):
        out = evaluate_engine_a_ai_advisory_rules(
            {"total_score": 70},
            {"pair": "BTCUSDT", "sl": 95.0, "rr1": 2.0},
        )

        assert out["advisory_rule_trade_allowed"] is False
        assert out["advisory_rule_action"] == "watchlist_only"
        assert out["advisory_rule_bucket"] == "65_74_watchlist_only"

    def test_advisory_rules_allow_only_high_quality_without_blockers(self):
        out = evaluate_engine_a_ai_advisory_rules(
            {"total_score": 86},
            {"pair": "BTCUSDT", "sl": 95.0, "rr1": 2.0},
        )

        assert out["advisory_rule_trade_allowed"] is True
        assert out["advisory_rule_action"] == "high_quality_review"


class TestEngineDAuditState:
    def test_extract_engine_d_state_from_scalp_signal(self):
        state = extract_engine_d_audit_state(
            {
                "engine": "SCALP",
                "setup_type": "mean_reversion",
                "quality_score": 82,
                "grade": "B",
                "market_state": "balance",
            }
        )

        assert state["engine"] == "SCALP"
        assert state["quality_score"] == 82
        assert state["market_state"] == "balance"

    def test_extract_engine_d_state_returns_none_for_engine_a_signal(self):
        assert extract_engine_d_audit_state({"engine": "engine_a"}) is None
