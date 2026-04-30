"""Unit tests for AI safety helpers — trace IDs, temperatures, schema clamp (no athena import)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_schemas import JudgeVerdictResponse
from ai_signal_trace import ensure_trace_id, generate_signal_trace_id
from config import AISafetyConstants, AITemperatureConfig
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
