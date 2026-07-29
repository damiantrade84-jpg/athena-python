"""Marcus advisory-rule parity: each engine must be rated on its own evidence.

Regressions guarded here (2026-07-27 Marcus audit):
  - Engine B rated on the saturated gate_pct instead of the graded total.
  - Engine B force-rejected by comparing the TP1 partial RR to the style min RR.
  - Engine A's advisory bucket driven by the model's self-reported total_score.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from ai_schemas import (
    _clamp_edge_to_grade_band,
    _engine_b_gate_score,
    _engine_b_graded_score,
    evaluate_marcus_advisory_rules,
    flag_marcus_component_score_contract,
)


def _engine_b_signal(**overrides):
    """Engine B scale-out payload as calculate_confidence emits it."""
    naked = {
        "score": 5.4,
        "max_possible": 11.5,
        "gate_score": 8.0,
        "gate_max_possible": 8.0,
        "gate_pct": 100.0,
        "pct": 83.2,
        "quality_pct": 28.0,
        "structural_verdict": "CLEAR",
        "structure_ok": True,
        "location_ok": True,
        "entry_ok": True,
        "trigger_ok": True,
        "rr_ok": True,
        "rr_passed": True,
        "room_ok": True,
        "space_gate_ok": True,
        "scale_out_active": True,
        "tp1_path_clear": True,
        "tp1_min_rr": 0.30,
        "style_min_rr": 2.0,
        "min_rr": 2.0,
        "rr_required": 2.0,
        # TP1 partial leg is gated on tp1_min_rr, not the style min.
        "execution_rr1": 0.85,
        "execution_rr2": 2.6,
        "rr_used_for_gate": 2.6,
        "execution_sl": 1.0850,
    }
    naked.update(overrides)
    return {
        "pair": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "price": 1.1000,
        "is_naked": True,
        "engine_source": "engine_b",
        "naked_data": naked,
        "engine_b": naked,
        "sl": 1.0850,
        "tp1": 1.1200,
        "rr1": naked["execution_rr1"],
        "rr2": naked["execution_rr2"],
        "min_rr": naked["min_rr"],
        "confluenceScore": naked["score"],
        "maxScore": naked["max_possible"],
    }


class TestEngineBGradedScore:
    def test_gate_pct_is_never_the_reported_score(self):
        signal = _engine_b_signal()
        # gate_pct saturates at 100 for every emitted signal ...
        assert _engine_b_gate_score(signal) == 100.0
        # ... so the reported score must come from the graded total instead.
        graded = _engine_b_graded_score(signal)
        assert graded is not None
        assert abs(graded - (5.4 / 11.5 * 100.0)) < 0.01

        out = evaluate_marcus_advisory_rules("engine_b", {"edgeProbability": 72}, signal)
        assert out["advisory_rule_score"] == round(graded, 2)
        assert out["advisory_rule_score_source"] == "engine_b_graded"
        assert out["advisory_gate_pct"] == 100.0

    def test_graded_score_discriminates_between_signals(self):
        weak = evaluate_marcus_advisory_rules(
            "engine_b", {"edgeProbability": 72}, _engine_b_signal(score=2.0)
        )
        strong = evaluate_marcus_advisory_rules(
            "engine_b", {"edgeProbability": 72}, _engine_b_signal(score=9.0)
        )
        assert weak["advisory_rule_score"] < strong["advisory_rule_score"]


class TestEngineBRiskReward:
    def test_scale_out_tp1_leg_does_not_trigger_rr_below_min(self):
        out = evaluate_marcus_advisory_rules(
            "engine_b", {"edgeProbability": 72}, _engine_b_signal()
        )
        assert "RR_BELOW_MIN" not in out["advisory_blocking_reasons"]
        assert out["advisory_rule_action"] != "reject"
        # The gated leg, not the TP1 partial, is what was compared.
        assert out["advisory_rr_value"] == 2.6

    def test_engine_rr_gate_failure_is_warning_in_advisory_mode(self):
        out = evaluate_marcus_advisory_rules(
            "engine_b",
            {"edgeProbability": 72},
            _engine_b_signal(rr_ok=False, rr_passed=False),
        )
        assert "RR_BELOW_MIN" not in out["advisory_blocking_reasons"]
        assert "RR_BELOW_MIN" in out["advisory_warnings"]
        assert out["advisory_rule_action"] != "reject"

    def test_numeric_fallback_when_engine_supplies_no_rr_verdict(self):
        signal = _engine_b_signal()
        for key in ("rr_ok", "rr_passed"):
            signal["naked_data"].pop(key)
        signal["naked_data"]["rr_used_for_gate"] = 1.1
        out = evaluate_marcus_advisory_rules("engine_b", {"edgeProbability": 72}, signal)
        assert "RR_BELOW_MIN" not in out["advisory_blocking_reasons"]
        assert "RR_BELOW_MIN" in out["advisory_warnings"]

    def test_master_toggle_restores_engine_b_rr_reject(self, monkeypatch):
        monkeypatch.setitem(config.CONFIG, "ENGINE_AB_PROFITABILITY_GATES_ENFORCED", True)
        out = evaluate_marcus_advisory_rules(
            "engine_b",
            {"edgeProbability": 72},
            _engine_b_signal(rr_ok=False, rr_passed=False),
        )
        assert "RR_BELOW_MIN" in out["advisory_blocking_reasons"]
        assert out["advisory_rule_action"] == "reject"

    def test_wide_valid_stop_is_warning_in_advisory_mode(self):
        signal = _engine_b_signal(execution_sl=1.0, rr_passed=True)
        signal["sl"] = 1.0
        out = evaluate_marcus_advisory_rules(
            "engine_b",
            {"edgeProbability": 72},
            signal,
        )
        assert "MAX_SL_EXCEEDED" not in out["advisory_blocking_reasons"]
        assert "MAX_SL_EXCEEDED" in out["advisory_warnings"]

    def test_missing_stop_and_blocked_tp_path_still_reject(self):
        missing_stop = _engine_b_signal()
        missing_stop.pop("sl")
        missing_stop["naked_data"].pop("execution_sl")
        out_missing = evaluate_marcus_advisory_rules(
            "engine_b", {"edgeProbability": 72}, missing_stop
        )
        out_path = evaluate_marcus_advisory_rules(
            "engine_b",
            {"edgeProbability": 72},
            _engine_b_signal(space_gate_ok=False, tp1_path_clear=False),
        )

        assert "NO_STOP_LOSS" in out_missing["advisory_blocking_reasons"]
        assert out_missing["advisory_rule_action"] == "reject"
        assert "ENGINE_B_SPACE_GATE_FAILED" in out_path["advisory_blocking_reasons"]
        assert "ENGINE_B_TP_PATH_BLOCKED" in out_path["advisory_blocking_reasons"]
        assert out_path["advisory_rule_action"] == "reject"


class TestEngineAScoreSource:
    ENGINE_A_SIGNAL = {
        "pair": "BTCUSDT",
        "type": "crypto",
        "direction": "LONG",
        "engine_source": "engine_a",
        "confluenceScore": 2.3,
        "maxScore": 3.0,
        "confluenceThreshold": 2.0,
        "qualified": True,
        "decision": "TRADE",
        "sl": 58800,
        "price": 60000,
        "tp1": 62400,
        "rr1": 2.0,
        "min_rr": 1.5,
    }

    def test_model_total_score_cannot_move_the_bucket(self):
        outs = [
            evaluate_marcus_advisory_rules(
                "engine_a", {"total_score": total}, dict(self.ENGINE_A_SIGNAL)
            )
            for total in (95.0, 60.0)
        ]
        assert outs[0]["advisory_rule_score"] == outs[1]["advisory_rule_score"]
        assert outs[0]["advisory_rule_bucket"] == outs[1]["advisory_rule_bucket"]
        assert all(o["advisory_rule_score_source"] == "engine_a_confluence" for o in outs)
        # Engine A's own evidence: 2.3 / 3.0.
        assert abs(outs[0]["advisory_rule_score"] - 76.67) < 0.01

    def test_model_score_cannot_grant_trade_allowed(self):
        out = evaluate_marcus_advisory_rules(
            "engine_a", {"total_score": 99.0}, dict(self.ENGINE_A_SIGNAL)
        )
        assert out["advisory_rule_trade_allowed"] is False
        assert out["advisory_ai_score"] == 99.0

    def test_model_score_still_used_when_engine_supplies_none(self):
        signal = {"pair": "BTCUSDT", "sl": 95.0, "rr1": 2.0}
        out = evaluate_marcus_advisory_rules("engine_a", {"total_score": 86.0}, signal)
        assert out["advisory_rule_score"] == 86.0
        assert out["advisory_rule_score_source"] == "ai_total_score"


class TestGradeEdgeClamp:
    def test_out_of_band_edge_clamps_to_nearest_edge_not_midpoint(self):
        # Grade B band is (45, 65); midpoint snapping would return 55.
        assert _clamp_edge_to_grade_band("B", 80) == (65.0, True)
        assert _clamp_edge_to_grade_band("B", 10) == (45.0, True)

    def test_in_band_edge_is_untouched(self):
        assert _clamp_edge_to_grade_band("B", 52) == (52, False)


class TestComponentScoreContract:
    def test_above_cap_components_are_flagged_not_silently_trusted(self):
        result = {
            "trend_score": 85,
            "structure_score": 80,
            "momentum_score": 70,
            "liquidity_score": 60,
            "risk_score": 75,
            "confirmation_score": 90,
            "total_score": 78,
            "warnings": [],
        }
        flag_marcus_component_score_contract(result)
        assert "COMPONENT_SCORE_ABOVE_CAP" in result["warnings"]
        assert "COMPONENT_SCORE_SUM_MISMATCH" in result["warnings"]
        # Values are recorded, never rewritten.
        assert result["trend_score"] == 85

    def test_in_contract_components_produce_no_warning(self):
        result = {
            "trend_score": 18,
            "structure_score": 17,
            "momentum_score": 13,
            "liquidity_score": 8,
            "risk_score": 12,
            "confirmation_score": 14,
            "total_score": 82,
            "warnings": [],
        }
        flag_marcus_component_score_contract(result)
        assert result["warnings"] == []
        assert result["componentScoreSum"] == 82.0
