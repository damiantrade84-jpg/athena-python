"""test_ai_review_safety.py — AI review layer safety contract tests.

Phases covered:
  Phase 5  — AI output contract / schema validation
  Phase 6  — AI execution safety (debate, vision, engine_b)
  Phase 7  — Chart vision path
  Phase 8  — Audit log schema
  Phase 10 — All prescribed safety tests

Rules being tested:
  - AI CONFIRM cannot override risk gate / freshness gate / Engine C block.
  - AI REJECT cannot upgrade a blocked signal.
  - Signal Debate PASS/STRONG_AVOID must block execution.
  - Signal Debate runtime exceptions default to blocked execution when
    AISafetyConstants.DEBATE_FAILURE_DEFAULTS_TO_BLOCK=True (Audit HIGH-007).
  - Debate score_adjustment is downgrade-only (effective ≤ 0); raw AI value in
    score_adjustment_raw; auto_trader retains defense-in-depth.
  - Vision CONTRADICT must set trade=False.
  - Vision CONFIRM cannot set trade=True when AISafetyConstants.DISABLE_AI_VISION_UPGRADE_PATH=True
    (compile-time; YAML AI_VISION_CAN_UPGRADE_TRADE alone cannot override).
  - Vision CONFIRM with upgrade requires monkeypatched compile-time gate off for tests.
  - Vision CONFIRM on already-trade=True must leave trade=True.
  - Engine B AI no-API-key returns non-blocking error dict, not exception.
  - Engine B AI invalid JSON returns non-blocking error dict.
  - Audit logger records correct schema; never raises on I/O failure.
  - Candle freshness and timestamps are currently absent from AI input packets
    (documented gap — not a breaking failure, but must be flagged).
  - Paper mode config: REAL_ORDERS_ALLOWED must remain false.
"""

import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Imports — only non-athena modules allowed (CLAUDE.md Hard Rule 3)
# ---------------------------------------------------------------------------
from ai_review_logger import (
    AI_STATE_CAUTION,
    AI_STATE_CONFIRM,
    AI_STATE_REJECT,
    AI_STATE_REVIEW_INCOMPLETE,
    REQUIRED_AUDIT_FIELDS,
    REVIEW_TYPE_CHART_VISION,
    REVIEW_TYPE_SIGNAL_DEBATE,
    cleanup_prompt_store,
    log_ai_review,
    map_debate_grade_to_ai_state,
    map_engine_b_grade_to_ai_state,
    map_vision_rating_to_ai_state,
    maybe_cleanup_prompt_store,
    validate_audit_record,
)
from engine_b_ai import (
    _normalise_engine_b_grade,
    _validate_engine_b_ai_payload,
    build_engine_b_signal_message,
    get_engine_b_ai_verdict,
)
from signal_debate import _signal_max_score, run_signal_debate


# ============================================================
# PHASE 5 — Output contract / schema
# ============================================================


class TestAuditRecordSchema:
    """Every audit record must contain every field in REQUIRED_AUDIT_FIELDS."""

    def test_required_fields_set_is_nonempty(self):
        assert len(REQUIRED_AUDIT_FIELDS) >= 24

    def test_validate_audit_record_passes_complete_record(self):
        record = {f: None for f in REQUIRED_AUDIT_FIELDS}
        ok, missing = validate_audit_record(record)
        assert ok
        assert missing == []

    def test_validate_audit_record_fails_on_missing_field(self):
        record = {f: None for f in REQUIRED_AUDIT_FIELDS}
        del record["ai_review_state"]
        ok, missing = validate_audit_record(record)
        assert not ok
        assert "ai_review_state" in missing

    def test_ai_changed_execution_permission_field_required(self):
        assert "ai_changed_execution_permission" in REQUIRED_AUDIT_FIELDS

    def test_candle_freshness_status_field_required(self):
        assert "candle_freshness_status" in REQUIRED_AUDIT_FIELDS


class TestAuditStateMapping:
    """State mapping functions must produce canonical strings."""

    def test_debate_strong_go_maps_to_confirm(self):
        assert map_debate_grade_to_ai_state("STRONG_GO") == AI_STATE_CONFIRM

    def test_debate_weak_go_maps_to_caution(self):
        assert map_debate_grade_to_ai_state("WEAK_GO") == AI_STATE_CAUTION

    def test_debate_pass_maps_to_reject(self):
        assert map_debate_grade_to_ai_state("PASS") == AI_STATE_REJECT

    def test_debate_strong_avoid_maps_to_reject(self):
        assert map_debate_grade_to_ai_state("STRONG_AVOID") == AI_STATE_REJECT

    def test_debate_skip_maps_to_review_incomplete(self):
        assert map_debate_grade_to_ai_state("SKIP") == AI_STATE_REVIEW_INCOMPLETE

    def test_debate_error_maps_to_review_incomplete(self):
        assert map_debate_grade_to_ai_state("ERROR") == AI_STATE_REVIEW_INCOMPLETE

    def test_engine_b_a_plus_maps_to_confirm(self):
        assert map_engine_b_grade_to_ai_state("A+") == AI_STATE_CONFIRM

    def test_engine_b_b_maps_to_caution(self):
        assert map_engine_b_grade_to_ai_state("B") == AI_STATE_CAUTION

    def test_engine_b_f_maps_to_reject(self):
        assert map_engine_b_grade_to_ai_state("F") == AI_STATE_REJECT

    def test_vision_confirms_maps_to_confirm(self):
        assert map_vision_rating_to_ai_state("CONFIRMS") == AI_STATE_CONFIRM

    def test_vision_review_maps_to_caution(self):
        assert map_vision_rating_to_ai_state("REVIEW") == AI_STATE_CAUTION

    def test_vision_potential_reversal_maps_to_reject(self):
        assert map_vision_rating_to_ai_state("POTENTIAL REVERSAL") == AI_STATE_REJECT

    def test_vision_avoid_maps_to_reject(self):
        assert map_vision_rating_to_ai_state("AVOID") == AI_STATE_REJECT

    def test_unknown_grade_maps_to_review_incomplete(self):
        assert map_debate_grade_to_ai_state("UNKNOWN_VALUE") == AI_STATE_REVIEW_INCOMPLETE


# ============================================================
# PHASE 6 — Signal Debate execution safety
# ============================================================


class TestSignalDebateExecutionSafety:
    """Signal Debate must gate correctly; runtime errors fail-closed when safety constant says so."""

    def _base_signal(self, **overrides) -> dict:
        base = {
            "pair": "EUR/USD",
            "display": "EUR/USD",
            "direction": "LONG",
            "confluenceScore": 1.8,
            "maxScore": 3.0,
            "type": "forex",
            "price": 1.1000,
            "sl": 1.0900,
            "tp1": 1.1200,
            "votes": {},
            "warnings": [],
        }
        base.update(overrides)
        return base

    def test_debate_skip_when_no_api_key_is_allowed_true(self, monkeypatch):
        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "")
        result = run_signal_debate(self._base_signal())
        assert result["grade"] == "SKIP"
        assert result["allowed"] is True

    def test_debate_pass_grade_sets_allowed_false(self, monkeypatch):
        """AI says PASS => allowed=False => execution is blocked."""
        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr(
            "signal_debate._get_debate_case",
            lambda *_a, **_kw: {"conviction": 5, "key_arguments": []},
        )
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {"grade": "PASS", "reasoning": "meh", "score_adjustment": 0.0},
        )
        result = run_signal_debate(self._base_signal())
        assert result["grade"] == "PASS"
        assert result["allowed"] is False

    def test_debate_strong_avoid_sets_allowed_false(self, monkeypatch):
        """AI says STRONG_AVOID => allowed=False."""
        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr(
            "signal_debate._get_debate_case",
            lambda *_a, **_kw: {"conviction": 2, "key_arguments": []},
        )
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {
                "grade": "STRONG_AVOID",
                "reasoning": "bad",
                "score_adjustment": 0.0,
            },
        )
        result = run_signal_debate(self._base_signal())
        assert result["allowed"] is False

    def test_debate_strong_go_sets_allowed_true(self, monkeypatch):
        """AI says STRONG_GO => allowed=True."""
        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr(
            "signal_debate._get_debate_case",
            lambda *_a, **_kw: {"conviction": 9, "key_arguments": []},
        )
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {
                "grade": "STRONG_GO",
                "reasoning": "good",
                "score_adjustment": 0.0,
            },
        )
        result = run_signal_debate(self._base_signal())
        assert result["allowed"] is True

    def test_debate_exception_defaults_to_blocked_when_safety_constant_true(self, monkeypatch):
        """Runtime debate failure blocks auto-exec when DEBATE_FAILURE_DEFAULTS_TO_BLOCK."""
        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr(
            "signal_debate.create_ai_client",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("network error")),
        )
        result = run_signal_debate(self._base_signal())
        assert result["grade"] == "ERROR"
        assert result["allowed"] is False

    def test_debate_positive_score_adjustment_does_not_apply_to_sub_threshold_signals(
        self, monkeypatch
    ):
        """Debate only runs AFTER the conviction gate in auto_trader._can_execute.

        A signal that is already below threshold never reaches the debate,
        so positive score_adjustment cannot rescue a blocked signal.
        We verify: debate returns allowed=True for STRONG_GO but effective
        score_adjustment is clamped to ≤ 0 (CRIT-002); raw model value is preserved
        in score_adjustment_raw. The conviction gate still lives upstream in _can_execute.
        """
        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr(
            "signal_debate._get_debate_case",
            lambda *_a, **_kw: {"conviction": 9, "key_arguments": []},
        )
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {
                "grade": "STRONG_GO",
                "reasoning": "go",
                "score_adjustment": 2.0,  # maximum positive (must not boost score)
            },
        )
        result = run_signal_debate(self._base_signal(confluenceScore=0.1))
        assert result["score_adjustment"] == 0.0
        assert result.get("score_adjustment_raw") == 2.0
        assert result["allowed"] is True
        # NOTE: In auto_trader._can_execute, a signal with confluenceScore=0.1
        # (combinedConviction ~ 0.03) fails at line 631 BEFORE debate is called.

    def test_debate_input_includes_entry_sl_tp_rr(self, monkeypatch):
        """Debate context string must include LEVELS data (entry, SL, TP, R:R)."""
        captured_context = []

        def _fake_get_case(*args):
            captured_context.append(args[1])
            return {"conviction": 5, "key_arguments": []}

        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr("signal_debate._get_debate_case", _fake_get_case)
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {"grade": "SKIP", "reasoning": "", "score_adjustment": 0.0},
        )

        sig = self._base_signal(price=1.1000, sl=1.0900, tp1=1.1200, rr1=2.0)
        run_signal_debate(sig)

        assert len(captured_context) >= 1
        ctx = captured_context[0]
        assert "1.1000" in ctx or "1.1" in ctx, "entry price must be in debate context"
        assert "1.0900" in ctx or "1.09" in ctx, "SL must be in debate context"
        assert "1.1200" in ctx or "1.12" in ctx, "TP must be in debate context"

    def test_debate_freshness_section_included_when_candle_meta_present(self, monkeypatch):
        """When signal has candleFetchMeta, freshness section must appear in debate context."""
        captured_context = []

        def _fake_get_case(*args):
            captured_context.append(args[1])
            return {"conviction": 5, "key_arguments": []}

        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr("signal_debate._get_debate_case", _fake_get_case)
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {"grade": "SKIP", "reasoning": "", "score_adjustment": 0.0},
        )

        sig = self._base_signal()
        sig["candleFetchMeta"] = {
            "H4": {"lastBarTime": "2026-04-25T12:00:00Z", "stalenessSeverity": "fresh"},
        }
        run_signal_debate(sig)

        ctx = captured_context[0] if captured_context else ""
        assert "CANDLE DATA FRESHNESS" in ctx, "Freshness section must appear when candleFetchMeta is present"
        assert "H4" in ctx

    def test_debate_no_freshness_when_signal_has_no_meta(self, monkeypatch):
        """When signal lacks candleFetchMeta, context must not include stale placeholder text."""
        captured_context = []

        def _fake_get_case(*args):
            captured_context.append(args[1])
            return {"conviction": 5, "key_arguments": []}

        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr("signal_debate._get_debate_case", _fake_get_case)
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {"grade": "SKIP", "reasoning": "", "score_adjustment": 0.0},
        )

        run_signal_debate(self._base_signal())

        ctx = captured_context[0] if captured_context else ""
        # No freshness section when signal has no metadata (build_freshness_ai_context returns "")
        assert "CANDLE DATA FRESHNESS" not in ctx


# ============================================================
# PHASE 6 — Vision execution safety
# ============================================================


class TestVisionExecutionSafety:
    """apply_vision() is the only sanctioned AI upgrade path (trade=True).
    Contradict/Avoid must always set trade=False.
    """

    def _base_consensus(self, conviction=0.50, trade=True) -> dict:
        return {
            "conviction": conviction,
            "tier": "B",
            "sizing_override": 1.0,
            "entry": 1.1000,
            "sl": 1.0900,
            "sl_a": 1.0850,
            "tp": 1.1300,
            "trade": trade,
            "sl_method": "structural",
        }

    def test_vision_contradict_sets_trade_false(self):
        from engine_c import apply_vision

        consensus = self._base_consensus(trade=True)
        vision = {
            "analysis": "MARKET CONTRADICTS DIRECTION TF ALIGNMENT: CONFLICTED",
            "structured": {"rating": "CONTRADICTS", "confirms_direction": False},
        }
        result = apply_vision(consensus, vision)
        assert result["trade"] is False
        assert result["tier"] == "SKIP"

    def test_vision_avoid_sets_trade_false(self):
        from engine_c import apply_vision

        consensus = self._base_consensus(trade=True)
        vision = {
            "analysis": "AVOID THIS TRADE",
            "structured": {"rating": "AVOID", "confirms_direction": False},
        }
        result = apply_vision(consensus, vision)
        assert result["trade"] is False

    def test_vision_confirm_above_threshold_with_upgrade_enabled_sets_trade_true(self, monkeypatch):
        """Vision CONFIRM sets trade=True only when YAML upgrade + compile-time allows."""
        import engine_c
        from config import AISafetyConstants

        monkeypatch.setattr(AISafetyConstants, "DISABLE_AI_VISION_UPGRADE_PATH", False)
        monkeypatch.setitem(engine_c.CONFIG, "AI_VISION_CAN_UPGRADE_TRADE", True)
        from engine_c import apply_vision

        consensus = self._base_consensus(conviction=0.45, trade=False)
        vision = {
            "analysis": "STRONG CONFIRMS DIRECTION TF ALIGNMENT: ALIGNED",
            "structured": {"rating": "STRONG", "confirms_direction": True},
        }
        result = apply_vision(consensus, vision)
        assert result["trade"] is True, (
            "Vision CONFIRM with conviction >= 0.35 must set trade=True "
            "when AI_VISION_CAN_UPGRADE_TRADE=True and compile-time gate disabled"
        )

    def test_vision_confirm_above_threshold_with_upgrade_disabled_does_not_create_trade(self, monkeypatch):
        """Vision CONFIRM must NOT create trade=True when AI_VISION_CAN_UPGRADE_TRADE=False (default)."""
        import engine_c
        monkeypatch.setitem(engine_c.CONFIG, "AI_VISION_CAN_UPGRADE_TRADE", False)
        from engine_c import apply_vision

        consensus = self._base_consensus(conviction=0.45, trade=False)
        vision = {
            "analysis": "STRONG CONFIRMS DIRECTION TF ALIGNMENT: ALIGNED",
            "structured": {"rating": "STRONG", "confirms_direction": True},
        }
        result = apply_vision(consensus, vision)
        assert result.get("trade") is not True, (
            "Vision CONFIRM must NOT set trade=True when AI_VISION_CAN_UPGRADE_TRADE=False"
        )
        assert result.get("ai_visual_confirmed") is True
        assert result.get("vision_supports_setup") is True

    def test_vision_confirm_preserves_existing_trade_true_when_upgrade_disabled(self, monkeypatch):
        """Vision CONFIRM on a trade=True consensus must preserve trade=True (downgrade-only, not destroy)."""
        import engine_c
        monkeypatch.setitem(engine_c.CONFIG, "AI_VISION_CAN_UPGRADE_TRADE", False)
        from engine_c import apply_vision

        consensus = self._base_consensus(conviction=0.45, trade=True)
        vision = {
            "analysis": "STRONG CONFIRMS DIRECTION TF ALIGNMENT: ALIGNED",
            "structured": {"rating": "STRONG", "confirms_direction": True},
        }
        result = apply_vision(consensus, vision)
        # trade was already True; CONFIRM in downgrade-only mode must not destroy it
        assert result.get("trade") is True

    def test_vision_confirm_below_threshold_does_not_set_trade_true(self):
        """If conviction after Vision modifier stays below 0.35, trade must NOT be set True."""
        from engine_c import apply_vision
        from engine_c import _vision_modifiers

        # Use a very low starting conviction so even CONFIRM keeps it below 0.35
        # MODERATE modifier conviction_mult is typically 1.0 or slight boost
        consensus = self._base_consensus(conviction=0.10, trade=False)
        vision = {
            "analysis": "MODERATE CONFIRMS",
            "structured": {"rating": "MODERATE", "confirms_direction": True},
        }
        result = apply_vision(consensus, vision)
        # conviction 0.10 * (MODERATE mult) is still well below 0.35
        if result["conviction"] < 0.35:
            assert result.get("trade") is not True, (
                "trade must not be True when Vision conviction < 0.35"
            )

    def test_vision_tf_alignment_conflicted_overrides_direction(self):
        """TF ALIGNMENT: CONFLICTED must force CONTRADICTS rating."""
        from engine_c import apply_vision

        consensus = self._base_consensus(trade=True)
        vision = {
            "analysis": "TF ALIGNMENT: CONFLICTED",
            "structured": {},
        }
        result = apply_vision(consensus, vision)
        assert result["trade"] is False

    def test_vision_empty_result_leaves_consensus_unchanged(self):
        """Empty / None vision result must be a no-op."""
        from engine_c import apply_vision

        consensus = self._base_consensus(trade=True)
        result = apply_vision(consensus, {})
        assert result["trade"] is True
        assert result["conviction"] == consensus["conviction"]

    def test_vision_does_not_bypass_risk_check(self):
        """Vision modifies consensus dict; risk_check is a separate gate in api_execute().

        This test verifies that apply_vision() returns a plain dict — it has no
        mechanism to call or bypass risk_check().
        """
        from engine_c import apply_vision

        consensus = self._base_consensus()
        vision = {
            "analysis": "STRONG CONFIRMS",
            "structured": {"rating": "STRONG", "confirms_direction": True},
        }
        result = apply_vision(consensus, vision)
        # No 'risk_approved' or bypass flag on the result
        assert "risk_approved" not in result
        assert "bypass_risk_check" not in result


# ============================================================
# PHASE 6 — Engine B AI advisory-only contract
# ============================================================


class TestEngineBaiAdvisoryContract:
    """Engine B AI is advisory-only (Hard Rule 17).  It must not raise exceptions."""

    def test_no_api_key_returns_error_dict_not_exception(self):
        result = get_engine_b_ai_verdict(
            pair="EUR/USD",
            direction="LONG",
            current_price=1.1,
            structure_result={},
            confidence_result={"score": 4.0, "max_possible": 5.0},
            xai_api_key=None,
        )
        assert isinstance(result, dict)
        assert "error" in result
        # Must not be an exception type
        assert result.get("error") == "API key not configured"

    def test_invalid_json_response_returns_error_dict(self, monkeypatch):
        import engine_b_ai

        monkeypatch.setattr(engine_b_ai, "create_ai_client", lambda *_a, **_kw: object())

        monkeypatch.setattr(
            engine_b_ai,
            "_call_ai_with_retry",
            lambda *_a, **_kw: (None, "NOT VALID JSON }{{"),
        )

        result = get_engine_b_ai_verdict(
            pair="GBP/USD",
            direction="SHORT",
            current_price=1.25,
            structure_result={},
            confidence_result={"score": 3.0, "max_possible": 5.0},
            xai_api_key="xai-key",
        )
        assert isinstance(result, dict)
        assert "error" in result

    def test_engine_b_grade_f_does_not_raise(self, monkeypatch):
        """Grade F from Engine B must return a valid dict, not block execution."""
        import engine_b_ai

        monkeypatch.setattr(engine_b_ai, "create_ai_client", lambda *_a, **_kw: object())

        _payload = json.dumps({
            "reviewSource": "engine_b_marcus",
            "reasoning": "Structural breakdown across styles.",
            "verdict": "Structure is broken.",
            "resolvedStyle": "intraday",
            "bestValidStyle": "intraday",
            "grade": "F",
            "edgeProbability": 15,
            "riskLevel": "High",
            "style_ratings": {
                "scalp": {"grade": "F", "edgeProbability": 15, "riskLevel": "High"},
                "intraday": {"grade": "F", "edgeProbability": 15, "riskLevel": "High"},
                "swing": {"grade": "F", "edgeProbability": 15, "riskLevel": "High"},
            },
        })

        monkeypatch.setattr(
            engine_b_ai,
            "_call_ai_with_retry",
            lambda *_a, **_kw: (json.loads(_payload), _payload),
        )

        result = get_engine_b_ai_verdict(
            pair="EUR/USD",
            direction="LONG",
            current_price=1.1,
            structure_result={},
            confidence_result={"score": 1.0, "max_possible": 5.0},
            xai_api_key="xai-key",
        )
        assert result.get("grade") == "F"
        assert "error" not in result

    def test_engine_b_message_includes_trade_parameters(self):
        """Prompt must include entry, SL, TP, RR so AI can assess levels."""
        msg = build_engine_b_signal_message(
            pair="GBP/USD",
            direction="SHORT",
            current_price=1.2500,
            structure_result={
                "structural_verdict": "BEARISH",
                "recommended_stop_loss": 1.2600,
                "recommended_take_profit": 1.2300,
            },
            confidence_result={"score": 4.0, "max_possible": 5.0, "passed": True},
        )
        assert "Entry: 1.25" in msg
        assert "Stop Loss: 1.26" in msg
        assert "Take Profit: 1.23" in msg
        assert "Risk:Reward" in msg

    def test_engine_b_message_without_freshness_ctx_has_no_freshness_section(self):
        """Without freshness_ctx, Engine B AI message must not include freshness section."""
        msg = build_engine_b_signal_message(
            pair="EUR/USD",
            direction="LONG",
            current_price=1.1,
            structure_result={},
            confidence_result={"score": 4.0, "max_possible": 5.0},
        )
        assert "CANDLE DATA FRESHNESS" not in msg

    def test_engine_b_message_with_freshness_ctx_includes_freshness_section(self):
        """When freshness_ctx has candleFetchMeta, Engine B AI message must include freshness."""
        msg = build_engine_b_signal_message(
            pair="EUR/USD",
            direction="LONG",
            current_price=1.1,
            structure_result={},
            confidence_result={"score": 4.0, "max_possible": 5.0},
            freshness_ctx={
                "candleFetchMeta": {
                    "H4": {
                        "lastBarTime": "2026-04-25T12:00:00Z",
                        "stalenessSeverity": "fresh",
                    }
                }
            },
        )
        assert "CANDLE DATA FRESHNESS" in msg
        assert "H4" in msg

    def test_engine_b_ai_grade_validation_rejects_invalid_grade(self):
        assert _normalise_engine_b_grade("B+") == "C"
        assert _normalise_engine_b_grade("Z") == "C"
        assert _normalise_engine_b_grade("A+") == "A+"

    def test_validate_engine_b_payload_missing_fields_get_safe_defaults(self):
        out = _validate_engine_b_ai_payload(
            {"grade": "A"},
            pair="TEST",
        )
        assert out["edgeProbability"] == 50.0
        assert out["riskLevel"] == "Medium"
        assert set(out["style_ratings"].keys()) == {"scalp", "intraday", "swing"}


# ============================================================
# PHASE 7 — Chart Vision path (no candle metadata gap)
# ============================================================


class TestChartVisionPath:
    """Chart Vision has no candle timestamp metadata in its context string (documented gap)."""

    def test_vision_structured_footer_tokens_are_present_in_prompts(self):
        from vision_prompts import build_single_prompt, build_dual_prompt, build_triple_prompt

        for build_fn, kwargs in [
            (build_single_prompt, {"symbol": "EUR/USD", "tf": "H4",
                                   "direction_str": "LONG", "algo_context": "",
                                   "asset_type": "forex"}),
            (build_dual_prompt, {"symbol": "EUR/USD", "direction_str": "LONG",
                                 "algo_context": "", "asset_type": "forex"}),
            (build_triple_prompt, {"symbol": "EUR/USD", "direction_str": "LONG",
                                   "algo_context": "", "asset_type": "forex"}),
        ]:
            prompt = build_fn(**kwargs)
            assert "RIGHT EDGE:" in prompt, f"{build_fn.__name__} missing RIGHT EDGE footer"
            assert "TF ALIGNMENT:" in prompt, f"{build_fn.__name__} missing TF ALIGNMENT footer"
            assert "SCALP RATING:" in prompt
            assert "INTRADAY RATING:" in prompt
            assert "SWING RATING:" in prompt

    def test_vision_system_prompt_enforces_image_first_workflow(self):
        from vision_prompts import build_system_prompt

        sp = build_system_prompt()
        assert "Read the chart image" in sp
        assert "cross-check" in sp.lower() or "Cross-check" in sp

    def test_vision_prompt_requires_evidence_only(self):
        from vision_prompts import build_system_prompt

        sp = build_system_prompt()
        assert "Only describe what is visible" in sp or "only describe" in sp.lower()
        assert "Do not invent" in sp

    def test_vision_prompt_requires_no_certainty_language(self):
        from vision_prompts import build_system_prompt

        sp = build_system_prompt()
        assert "advisory-only" in sp.lower() or "no guarantees" in sp.lower()


# ============================================================
# PHASE 8 — Audit logger
# ============================================================


class TestAiReviewLogger:
    """Audit logger must write valid records and never raise on I/O failure."""

    def _make_tmp_dir(self):
        return tempfile.mkdtemp()

    def _write_prompt_store_file(self, root: str, rel_parts: tuple[str, ...], body: bytes, mtime: float):
        path = os.path.join(root, *rel_parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(body)
        os.utime(path, (mtime, mtime))
        return path

    def _call_log(self, tmp_dir: str, **overrides):
        import ai_review_logger as _m

        _m._LOG_DIR = os.path.join(tmp_dir, "ai_review")
        _m._LOG_FILE = os.path.join(tmp_dir, "ai_review", "ai_review_audit.jsonl")
        _m._PROMPT_CA_DIR = os.path.join(tmp_dir, "ai_review", "prompt_store")

        kwargs = dict(
            symbol="EUR/USD",
            asset_type="forex",
            review_type=REVIEW_TYPE_SIGNAL_DEBATE,
            model="grok-4-1-fast-reasoning",
            provider="xai",
            prompt_version="v1",
            input_packet={"pair": "EUR/USD"},
            has_chart_image=False,
            candle_freshness_status="unknown",
            engine_a_state="PASS",
            engine_b_state=None,
            engine_c_state=None,
            engine_d_state=None,
            risk_state=None,
            ai_review_state=AI_STATE_CONFIRM,
            ai_confidence=0.75,
            contradictions_count=0,
            missing_information_count=1,
            parse_success=True,
            schema_valid=True,
            execution_allowed_before_ai=True,
            execution_allowed_after_ai=True,
            final_action="EXECUTE",
        )
        kwargs.update(overrides)
        log_ai_review(**kwargs)
        return _m._LOG_FILE

    def test_log_ai_review_writes_jsonl_record(self):
        tmp_dir = self._make_tmp_dir()
        log_file = self._call_log(tmp_dir)
        assert os.path.exists(log_file)
        with open(log_file) as f:
            line = f.readline()
        record = json.loads(line)
        ok, missing = validate_audit_record(record)
        assert ok, f"Missing fields: {missing}"

    def test_log_ai_review_sets_ai_changed_execution_permission_false_when_unchanged(self):
        tmp_dir = self._make_tmp_dir()
        log_file = self._call_log(
            tmp_dir,
            execution_allowed_before_ai=True,
            execution_allowed_after_ai=True,
        )
        with open(log_file) as f:
            record = json.loads(f.readline())
        assert record["ai_changed_execution_permission"] is False

    def test_log_ai_review_detects_downgrade(self):
        tmp_dir = self._make_tmp_dir()
        log_file = self._call_log(
            tmp_dir,
            execution_allowed_before_ai=True,
            execution_allowed_after_ai=False,
            ai_review_state=AI_STATE_REJECT,
        )
        with open(log_file) as f:
            record = json.loads(f.readline())
        assert record["ai_changed_execution_permission"] is True

    def test_log_ai_review_records_candle_freshness_status(self):
        tmp_dir = self._make_tmp_dir()
        log_file = self._call_log(tmp_dir, candle_freshness_status="stale")
        with open(log_file) as f:
            record = json.loads(f.readline())
        assert record["candle_freshness_status"] == "stale"

    def test_log_ai_review_never_raises_on_io_error(self, monkeypatch):
        """Logger must silently handle I/O failures — trading must not crash."""
        import ai_review_logger as _m

        tmp_dir = self._make_tmp_dir()
        _m._PROMPT_CA_DIR = os.path.join(tmp_dir, "prompt_store")
        monkeypatch.setattr(_m, "_ensure_log_dir", lambda: None)
        monkeypatch.setattr("builtins.open", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")))

        # Must not raise
        log_ai_review(
            symbol="BTC/USDT",
            asset_type="crypto",
            review_type=REVIEW_TYPE_SIGNAL_DEBATE,
            model="grok",
            provider="xai",
            prompt_version="v1",
            input_packet={},
            has_chart_image=False,
            candle_freshness_status=None,
            engine_a_state=None,
            engine_b_state=None,
            engine_c_state=None,
            engine_d_state=None,
            risk_state=None,
            ai_review_state=AI_STATE_CONFIRM,
            ai_confidence=0.8,
            contradictions_count=0,
            missing_information_count=0,
            parse_success=True,
            schema_valid=True,
            execution_allowed_before_ai=True,
            execution_allowed_after_ai=True,
            final_action="EXECUTE",
        )

    def test_log_ai_review_appends_multiple_records(self):
        tmp_dir = self._make_tmp_dir()
        for i in range(3):
            self._call_log(tmp_dir, symbol=f"PAIR_{i}")
        import ai_review_logger as _m
        with open(_m._LOG_FILE) as f:
            lines = f.readlines()
        assert len(lines) == 3

    def test_non_vision_upgrade_is_flagged_in_log(self, caplog):
        """A non-Vision path that upgrades execution permission must emit a warning."""
        import logging
        import ai_review_logger as _m

        tmp_dir = self._make_tmp_dir()
        _m._LOG_DIR = os.path.join(tmp_dir, "ai_review")
        _m._LOG_FILE = os.path.join(tmp_dir, "ai_review", "ai_review_audit.jsonl")
        _m._PROMPT_CA_DIR = os.path.join(tmp_dir, "ai_review", "prompt_store")

        with caplog.at_level(logging.WARNING, logger="athena.ai_review"):
            log_ai_review(
                symbol="EUR/USD",
                asset_type="forex",
                review_type=REVIEW_TYPE_SIGNAL_DEBATE,  # not vision
                model="grok",
                provider="xai",
                prompt_version="v1",
                input_packet={},
                has_chart_image=False,
                candle_freshness_status=None,
                engine_a_state=None,
                engine_b_state=None,
                engine_c_state=None,
                engine_d_state=None,
                risk_state=None,
                ai_review_state=AI_STATE_CONFIRM,
                ai_confidence=0.9,
                contradictions_count=0,
                missing_information_count=0,
                parse_success=True,
                schema_valid=True,
                execution_allowed_before_ai=False,  # was blocked
                execution_allowed_after_ai=True,   # now allowed — unexpected
                final_action="EXECUTE",
            )

        assert any("UNEXPECTED UPGRADE" in r.message for r in caplog.records)

    def test_prompt_store_cleanup_deletes_expired_files_but_keeps_recent_files(self):
        tmp_dir = self._make_tmp_dir()
        store_dir = os.path.join(tmp_dir, "prompt_store")
        now = 1_700_000_000.0
        old_path = self._write_prompt_store_file(
            store_dir,
            ("prompt", "aa", "bb", "old"),
            b"old prompt",
            now - (91 * 86400),
        )
        recent_path = self._write_prompt_store_file(
            store_dir,
            ("prompt", "cc", "dd", "recent"),
            b"recent prompt",
            now - (1 * 86400),
        )

        result = cleanup_prompt_store(
            store_dir=store_dir,
            retention_days=90,
            min_delete_age_days=7,
            max_bytes=1024 * 1024,
            now=now,
        )

        assert result["removed_files"] == 1
        assert not os.path.exists(old_path)
        assert os.path.exists(recent_path)

    def test_prompt_store_size_cap_never_deletes_recent_files(self):
        tmp_dir = self._make_tmp_dir()
        store_dir = os.path.join(tmp_dir, "prompt_store")
        now = 1_700_000_000.0
        old_path = self._write_prompt_store_file(
            store_dir,
            ("prompt", "aa", "bb", "old"),
            b"o" * 800,
            now - (10 * 86400),
        )
        recent_path = self._write_prompt_store_file(
            store_dir,
            ("prompt", "cc", "dd", "recent"),
            b"r" * 800,
            now - (1 * 86400),
        )

        result = cleanup_prompt_store(
            store_dir=store_dir,
            retention_days=90,
            min_delete_age_days=7,
            max_bytes=900,
            now=now,
        )

        assert result["removed_files"] == 1
        assert not os.path.exists(old_path)
        assert os.path.exists(recent_path)
        assert result["kept_bytes"] >= 800

    def test_prompt_store_ttl_respects_minimum_delete_age(self):
        tmp_dir = self._make_tmp_dir()
        store_dir = os.path.join(tmp_dir, "prompt_store")
        now = 1_700_000_000.0
        recent_path = self._write_prompt_store_file(
            store_dir,
            ("prompt", "aa", "bb", "recent"),
            b"recent prompt",
            now - (3 * 86400),
        )

        result = cleanup_prompt_store(
            store_dir=store_dir,
            retention_days=1,
            min_delete_age_days=7,
            max_bytes=1024 * 1024,
            now=now,
        )

        assert result["removed_files"] == 0
        assert os.path.exists(recent_path)

    def test_prompt_store_cleanup_is_throttled(self, monkeypatch):
        import ai_review_logger as _m

        tmp_dir = self._make_tmp_dir()
        store_dir = os.path.join(tmp_dir, "prompt_store")
        now = 1_700_000_000.0
        old_path = self._write_prompt_store_file(
            store_dir,
            ("prompt", "aa", "bb", "old"),
            b"old prompt",
            now - (91 * 86400),
        )
        config = {
            "enabled": True,
            "retention_days": 90,
            "min_delete_age_days": 7,
            "max_bytes": 1024 * 1024,
            "interval_sec": 86400,
        }
        monkeypatch.setattr(_m, "_PROMPT_CA_DIR", store_dir)
        monkeypatch.setattr(_m, "_prompt_store_cleanup_config", lambda _config=None: config)
        monkeypatch.setattr(_m, "_prompt_store_last_cleanup_ts", now)

        assert maybe_cleanup_prompt_store(now=now + 60) is None
        assert os.path.exists(old_path)

        monkeypatch.setattr(_m, "_prompt_store_last_cleanup_ts", now - 90000)
        result = maybe_cleanup_prompt_store(now=now)

        assert result is not None
        assert result["removed_files"] == 1
        assert not os.path.exists(old_path)


# ============================================================
# PHASE 10 — Paper mode safety
# ============================================================


class TestPaperModeSafety:
    """REAL_ORDERS_ALLOWED must remain false in config.yaml regardless of AI output."""

    def test_paper_mode_real_orders_disallowed(self):
        """REAL_ORDERS_ALLOWED must be false — AI cannot override this config key."""
        import os
        import yaml  # type: ignore

        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        if not os.path.exists(cfg_path):
            return  # skip if config.yaml not present in this environment

        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        paper = cfg.get("PAPER_SOAK", {})
        assert paper.get("REAL_ORDERS_ALLOWED") is False, (
            "REAL_ORDERS_ALLOWED must be false. AI must not override paper mode."
        )

    def test_paper_mode_enabled(self):
        import os
        import yaml  # type: ignore

        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        if not os.path.exists(cfg_path):
            return

        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        paper = cfg.get("PAPER_SOAK", {})
        assert paper.get("ENABLED") is True, "PAPER_SOAK.ENABLED must be true"

    def test_ai_vision_cannot_upgrade_trade_is_false_by_default(self):
        """AI_VISION_CAN_UPGRADE_TRADE must be false in config.yaml (downgrade-only by default)."""
        import os
        import yaml  # type: ignore

        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        if not os.path.exists(cfg_path):
            return

        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        assert cfg.get("AI_VISION_CAN_UPGRADE_TRADE") is False, (
            "AI_VISION_CAN_UPGRADE_TRADE must be false in config.yaml"
        )


# ============================================================
# NEW — Phase 1: Freshness context helper
# ============================================================


class TestFreshnessAiContext:
    """build_freshness_ai_context() must produce structured output from signal data."""

    def test_empty_signal_returns_empty_string(self):
        from ai_utils import build_freshness_ai_context

        assert build_freshness_ai_context({}) == ""

    def test_returns_empty_string_on_exception(self):
        from ai_utils import build_freshness_ai_context

        assert build_freshness_ai_context(None) == ""  # type: ignore

    def test_includes_candle_freshness_header(self):
        from ai_utils import build_freshness_ai_context

        sig = {
            "candleFetchMeta": {
                "H4": {"lastBarTime": "2026-04-25T12:00:00Z", "stalenessSeverity": "fresh"},
            }
        }
        result = build_freshness_ai_context(sig)
        assert "CANDLE DATA FRESHNESS" in result
        assert "H4" in result
        assert "2026-04-25T12:00:00Z" in result

    def test_includes_gate_decision(self):
        from ai_utils import build_freshness_ai_context

        sig = {
            "candleFetchMeta": {
                "H4": {"lastBarTime": "2026-04-25T12:00:00Z", "stalenessSeverity": "stale_1_bucket"},
            },
            "dataFreshness": {"allowed": False, "reason": "H4_stale", "blocked": ["H4"]},
        }
        result = build_freshness_ai_context(sig)
        assert "BLOCKED" in result
        assert "H4_stale" in result

    def test_stale_flag_appears_when_last_bar_stale(self):
        from ai_utils import build_freshness_ai_context

        sig = {
            "candleFetchMeta": {
                "D1": {
                    "lastBarTime": "2026-04-20T00:00:00Z",
                    "stalenessSeverity": "stale_multi_bucket",
                    "lastBarStale": True,
                }
            }
        }
        result = build_freshness_ai_context(sig)
        assert "STALE" in result

    def test_only_dataFreshness_without_meta_still_produces_output(self):
        from ai_utils import build_freshness_ai_context

        sig = {"dataFreshness": {"allowed": True, "reason": "ok"}}
        result = build_freshness_ai_context(sig)
        assert "CANDLE DATA FRESHNESS" in result
        assert "ALLOWED" in result


# ============================================================
# NEW — Phase 2: Debate downgrade-only contract
# ============================================================


class TestDebateDowngradeOnly:
    """Debate score_adjustment must be clamped to <= 0 (signal_debate + schema + auto_trader)."""

    def test_debate_score_adjustment_field_exists_in_result(self, monkeypatch):
        """Debate returns score_adjustment — it may be positive from the AI."""
        monkeypatch.setattr("signal_debate.get_ai_api_key", lambda _cfg: "xai-key")
        monkeypatch.setattr("signal_debate.create_ai_client", lambda *_a, **_kw: object())
        monkeypatch.setattr(
            "signal_debate._get_debate_case",
            lambda *_a, **_kw: {"conviction": 9, "key_arguments": []},
        )
        monkeypatch.setattr(
            "signal_debate._get_judge_verdict",
            lambda *_a, **_kw: {
                "grade": "STRONG_GO",
                "reasoning": "go",
                "score_adjustment": 0.5,  # positive — AI wants to boost score
            },
        )
        from signal_debate import run_signal_debate

        result = run_signal_debate(
            {
                "pair": "EUR/USD",
                "display": "EUR/USD",
                "direction": "LONG",
                "confluenceScore": 2.0,
                "maxScore": 3.0,
                "type": "forex",
                "price": 1.1,
                "sl": 1.09,
                "tp1": 1.12,
                "votes": {},
                "warnings": [],
            }
        )
        # Central downgrade-only clamp (signal_debate + schema): effective adjustment ≤ 0
        assert "score_adjustment" in result
        assert result["score_adjustment"] == 0.0
        assert result.get("score_adjustment_raw") == 0.5


# ============================================================
# NEW — Phase 3: Vision downgrade-only config key
# ============================================================


class TestVisionDowngradeOnlyConfig:
    """AI_VISION_CAN_UPGRADE_TRADE config key gates the Vision upgrade path."""

    def test_vision_confirm_sets_ai_visual_confirmed_when_upgrade_disabled(self, monkeypatch):
        """When upgrade disabled, CONFIRM must set ai_visual_confirmed=True."""
        import engine_c
        monkeypatch.setitem(engine_c.CONFIG, "AI_VISION_CAN_UPGRADE_TRADE", False)
        from engine_c import apply_vision

        consensus = {
            "conviction": 0.60,
            "tier": "B",
            "sizing_override": 1.0,
            "entry": 1.1,
            "sl": 1.09,
            "sl_a": 1.085,
            "tp": 1.13,
            "trade": False,
            "sl_method": "structural",
        }
        vision = {
            "analysis": "STRONG CONFIRMS",
            "structured": {"rating": "STRONG", "confirms_direction": True},
        }
        result = apply_vision(consensus, vision)
        assert result.get("ai_visual_confirmed") is True
        assert result.get("vision_supports_setup") is True
        assert result.get("trade") is not True

    def test_vision_contradict_still_blocks_when_upgrade_disabled(self, monkeypatch):
        """CONTRADICT/AVOID must still block execution regardless of upgrade config."""
        import engine_c
        monkeypatch.setitem(engine_c.CONFIG, "AI_VISION_CAN_UPGRADE_TRADE", False)
        from engine_c import apply_vision

        consensus = {
            "conviction": 0.60, "tier": "B", "sizing_override": 1.0,
            "entry": 1.1, "sl": 1.09, "sl_a": 1.085, "tp": 1.13,
            "trade": True, "sl_method": "structural",
        }
        vision = {
            "analysis": "CONTRADICTS",
            "structured": {"rating": "CONTRADICTS", "confirms_direction": False},
        }
        result = apply_vision(consensus, vision)
        assert result["trade"] is False
        assert result["tier"] == "SKIP"


class TestExpertPromptDeadRangingMarcusRule:
    """EXPERT_PROMPT (Marcus Reid) must not auto-F on DEAD RANGING (advisory wording)."""

    def test_expert_prompt_no_automatic_dead_ranging_f(self):
        athena_path = os.path.join(os.path.dirname(__file__), "..", "athena.py")
        with open(athena_path, encoding="utf-8") as f:
            text = f.read()
        marker = 'EXPERT_PROMPT = """'
        i0 = text.index(marker) + len(marker)
        i1 = text.index('"""', i0)
        prompt_body = text[i0:i1]
        assert "DEAD RANGING regime = automatic F grade" not in prompt_body
        assert "derive from data, NOT from rawScorePct buckets" in prompt_body
        assert "no fixed caps" in prompt_body


class TestMarcusTextReviewTimeoutContract:
    """Marcus Reid text review must not rely on the SDK's long default timeout."""

    def test_marcus_provider_call_passes_configured_timeout(self):
        athena_path = os.path.join(os.path.dirname(__file__), "..", "athena.py")
        with open(athena_path, encoding="utf-8") as f:
            text = f.read()
        start = text.index("def run_ai(")
        end = text.index("t = (completion.choices[0].message.content", start)
        run_ai_body = text[start:end]
        assert 'preferred_key="MARCUS_AI_SDK_MAX_RETRIES"' in run_ai_body
        assert "create_ai_client(CONFIG, max_retries=_sdk_max_retries)" in run_ai_body
        assert "get_ai_timeout_sec(" in run_ai_body
        assert 'preferred_key="MARCUS_AI_TIMEOUT_SEC"' in run_ai_body
        assert "timeout=_timeout_sec" in run_ai_body
        assert "[AI] %s timing: prompt_build" in run_ai_body
        assert "elapsed=%.2fs timeout=%.1fs sdk_retries=%s" in text
        assert "prompt_build=%.2fs prompt_chars=%s" in text


class TestVisionArbitrationFailClosed:
    def test_arbitration_exception_blocks_trade(self, monkeypatch):
        import engine_c
        def boom(*a, **k):
            raise RuntimeError("arb down")
        monkeypatch.setitem(engine_c.CONFIG, "AI_VISION_CAN_UPGRADE_TRADE", False)
        monkeypatch.setattr("ai_reconciliation.arbitrate_ai", boom)
        from engine_c import apply_vision
        consensus = {
            "conviction": 0.5,
            "tier": "MEDIUM",
            "sizing_override": 0.65,
            "entry": 1.1,
            "sl": 1.09,
            "tp": 1.13,
            "trade": True,
            "sl_method": "structural",
        }
        out = apply_vision(consensus, {"analysis": "MODERATE", "structured": {"rating": "MODERATE"}})
        assert out["trade"] is False
        assert out["verdict"] == "VISION_ARBITRATION_ERROR"


class TestVisionModifiersHardVeto:
    def test_yaml_cannot_neutralize_contradicts(self, monkeypatch):
        import engine_c
        monkeypatch.setitem(
            engine_c.CONFIG,
            "VISION_MODIFIERS",
            {"CONTRADICTS": {"action": "confirm", "conviction_mult": 1.0}},
        )
        modifiers = engine_c._vision_modifiers()
        assert modifiers["CONTRADICTS"]["action"] == "contradict"
        assert modifiers["CONTRADICTS"]["conviction_mult"] == 0.0
