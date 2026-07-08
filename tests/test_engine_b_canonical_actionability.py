"""Regression tests for Engine B canonical actionability reconciliation."""

from __future__ import annotations

import pytest

from engine_b_canonical_actionability import (
    REASON_CONFLICT,
    REASON_PARTIAL_RR,
    STATUS_REJECT_CONFLICT,
    STATUS_REJECT_ENTRY_LOCATION,
    STATUS_REJECT_NO_ROOM,
    STATUS_REJECT_NO_TRIGGER,
    STATUS_REJECT_RR_QUALITY,
    STATUS_REJECT_STRUCTURAL_TP,
    STRUCTURE_REJECT,
    reconcile_engine_b_actionability,
)
from market_structure import (
    ENGINE_B_REASON_NO_TRIGGER_PATTERN,
    ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE,
    ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
)


def _base_conf(**overrides):
    conf = {
        "trigger_ok": True,
        "room_ok": True,
        "passed": False,
        "checklist_passed": False,
        "execution_rr1": 2.0,
        "execution_rr2": 2.0,
        "rr_used_for_gate": 1.8,
        "engine_b_diagnostics": {"reason_codes": []},
    }
    conf.update(overrides)
    return conf


def _zones_support(low=17.4993, high=17.5798):
    return {
        "nearest_support_zone": {"lower": low, "upper": high},
        "nearest_resistance_zone": {"lower": 17.5081, "upper": 17.6061},
    }


def test_short_entry_inside_support_rejects():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.52,
        conf=_base_conf(),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_actionable"] is False
    assert out["engine_b_canonical_status"] == STATUS_REJECT_ENTRY_LOCATION
    assert out["structural_status"] == STRUCTURE_REJECT


def test_short_tp1_inside_support_rejects():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.60,
        sl=17.65,
        tp1=17.55,
        conf=_base_conf(),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_actionable"] is False
    assert out["engine_b_canonical_status"] == STATUS_REJECT_STRUCTURAL_TP


def test_short_room_ok_false_rejects():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.60,
        sl=17.65,
        tp1=17.40,
        conf=_base_conf(room_ok=False),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM


def test_short_support_too_close_rejects():
    codes = [ENGINE_B_REASON_SUPPORT_TOO_CLOSE]
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.60,
        sl=17.65,
        tp1=17.40,
        conf=_base_conf(engine_b_diagnostics={"reason_codes": codes}),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM


def test_long_entry_inside_resistance_rejects():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.55,
        sl=17.50,
        tp1=17.70,
        conf=_base_conf(),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_ENTRY_LOCATION


def test_long_tp1_inside_resistance_rejects():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.55,
        conf=_base_conf(),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_STRUCTURAL_TP


def test_long_room_ok_false_rejects():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.85,
        conf=_base_conf(room_ok=False),
        res={"nearest_resistance_zone": {"lower": 17.70, "upper": 17.80}},
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM


def test_long_resistance_too_close_rejects():
    from market_structure import ENGINE_B_REASON_RESISTANCE_TOO_CLOSE

    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.85,
        conf=_base_conf(
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_RESISTANCE_TOO_CLOSE]}
        ),
        res={"nearest_resistance_zone": {"lower": 17.70, "upper": 17.80}},
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM


def test_confluence_score_cannot_override_reject():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.52445,
        conf=_base_conf(room_ok=False, score=99.0),
        res=_zones_support(),
        confluence_score=99.0,
    )
    assert out["engine_b_canonical_actionable"] is False


def test_ai_confidence_cannot_override_reject():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.52445,
        conf=_base_conf(room_ok=False),
        res=_zones_support(),
        ai_confidence=0.99,
    )
    assert out["engine_b_canonical_actionable"] is False


def test_playbook_no_ai_true_conflict():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.52445,
        conf=_base_conf(
            passed=True,
            trigger_ok=False,
            room_ok=False,
            engine_b_diagnostics={
                "reason_codes": [
                    ENGINE_B_REASON_NO_TRIGGER_PATTERN,
                    ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
                    ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE,
                ]
            },
        ),
        res=_zones_support(),
        style_profile={"min_rr": 1.35},
    )
    assert out["engine_b_canonical_actionable"] is False
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_TRIGGER
    assert REASON_CONFLICT in out["engine_b_rejection_reasons"]
    assert out["ai_calibration_pass_fallback_raw"] is True


def test_trigger_ok_false_rejects_no_trigger():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.20,
        conf=_base_conf(trigger_ok=False),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_TRIGGER


def test_no_trigger_pattern_rejects():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.20,
        conf=_base_conf(
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_NO_TRIGGER_PATTERN]}
        ),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_TRIGGER


def test_structural_tp_too_close_rejects():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.35,
        conf=_base_conf(
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE]}
        ),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_STRUCTURAL_TP


def test_rr1_below_style_min_partial_target_quality_fail():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.35,
        conf=_base_conf(
            execution_rr1=0.74,
            execution_rr2=1.8,
            rr_used_for_gate=1.8,
        ),
        res=_zones_support(low=17.10, high=17.20),
        style_profile={"min_rr": 1.35},
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_RR_QUALITY
    assert out["partial_target_quality_fail"] is True
    assert REASON_PARTIAL_RR in out["engine_b_rejection_reasons"]


def test_rr2_passing_cannot_override_structural_tp_failure():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.60,
        sl=17.65,
        tp1=17.52445,
        conf=_base_conf(
            execution_rr1=0.74,
            execution_rr2=1.8,
            rr_used_for_gate=1.8,
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE]},
        ),
        res=_zones_support(),
        style_profile={"min_rr": 1.35},
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_STRUCTURAL_TP
    assert out["engine_b_canonical_actionable"] is False


def test_fresh_candle_cannot_override_structural_rejection():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.52445,
        conf=_base_conf(room_ok=False),
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    assert out["engine_b_canonical_actionable"] is False
    assert out["candle_freshness_status"] == "fresh"


def test_marcus_reid_fixture():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.62,
        tp1=17.524450360418264,
        conf=_base_conf(
            passed=True,
            trigger_ok=False,
            room_ok=False,
            execution_rr1=0.74,
            execution_rr2=1.8,
            rr_used_for_gate=1.8,
            engine_b_diagnostics={
                "reason_codes": [
                    ENGINE_B_REASON_NO_TRIGGER_PATTERN,
                    ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
                    ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE,
                ]
            },
        ),
        res=_zones_support(17.4993, 17.5798),
        style_profile={"min_rr": 1.35},
    )
    assert out["engine_b_canonical_actionable"] is False
    assert out["entry_inside_support"] is True
    assert out["tp1_inside_support"] is True
    assert REASON_CONFLICT in out["engine_b_rejection_reasons"]
    assert REASON_PARTIAL_RR in out["engine_b_rejection_reasons"]


def _zones_long_in_resistance():
    return {
        "nearest_support_zone": {"lower": 161.0, "upper": 162.0},
        "nearest_resistance_zone": {"lower": 162.4573, "upper": 163.0009},
    }


def test_long_entry_inside_resistance_canonical_location_false():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=162.461,
        sl=161.9,
        tp1=163.2,
        conf=_base_conf(
            passed=True,
            location_ok=True,
            structure_ok=True,
            trigger_ok=True,
            room_ok=False,
            execution_rr1=2.1,
            execution_rr2=2.1,
            rr_used_for_gate=2.1,
            engine_b_diagnostics={
                "reason_codes": [
                    "resistance_too_close",
                    "structural_tp_too_close",
                ]
            },
        ),
        res=_zones_long_in_resistance(),
        style_profile={"min_rr": 1.3},
    )
    assert out["canonical_location_ok"] is False
    assert out["engine_b_canonical_status"] == STATUS_REJECT_ENTRY_LOCATION
    assert out["canonical_primary_reject_reason"] == "LONG_ENTRY_INSIDE_RESISTANCE"
    assert REASON_CONFLICT not in (out["canonical_secondary_reject_reasons"] or []) or (
        out["canonical_primary_reject_reason"] != REASON_CONFLICT
    )


def test_short_entry_inside_support_canonical_location_false():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.40,
        conf=_base_conf(location_ok=True),
        res=_zones_support(),
    )
    assert out["canonical_location_ok"] is False


def test_room_ok_false_canonical_room_false():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.85,
        conf=_base_conf(room_ok=False, rr_ok=True),
        res={"nearest_resistance_zone": {"lower": 17.70, "upper": 17.80}},
    )
    assert out["canonical_room_ok"] is False
    assert out["canonical_badge_state"]["room_rr"] == "FAIL"


def test_structural_tp_too_close_canonical_room_false():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.35,
        conf=_base_conf(
            room_ok=True,
            rr_ok=True,
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE]},
        ),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["canonical_room_ok"] is False
    assert out["canonical_badge_state"]["room_rr"] == "FAIL"


def test_rr_ok_cannot_override_room_ok_false():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.85,
        conf=_base_conf(room_ok=False, rr_ok=True, execution_rr1=2.5),
        res={"nearest_resistance_zone": {"lower": 17.70, "upper": 17.80}},
    )
    assert out["canonical_rr_ok"] is True
    assert out["canonical_room_ok"] is False
    assert out["canonical_badge_state"]["room_rr"] == "FAIL"


def test_confidence_passed_gate_failed_badge_state():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=162.461,
        sl=161.9,
        tp1=163.2,
        conf=_base_conf(passed=True, location_ok=True, room_ok=False),
        res=_zones_long_in_resistance(),
    )
    assert out["confidence_passed"] is True
    assert out["canonical_trade_ok"] is False
    assert out["canonical_badge_state"]["confidence"] == "PASSED_GATE_FAILED"


def test_canonical_trade_false_suppresses_executable_levels():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=162.461,
        sl=161.9,
        tp1=163.2,
        conf=_base_conf(
            passed=True,
            execution_sl=161.9,
            execution_tp1=163.2,
        ),
        res=_zones_long_in_resistance(),
    )
    assert out["suggested_levels_executable"] is False


def test_entry_location_priority_over_conflict():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=162.461,
        sl=161.9,
        tp1=163.2,
        conf=_base_conf(
            passed=True,
            is_actionable=False,
            location_ok=True,
            trigger_ok=True,
            room_ok=False,
        ),
        res=_zones_long_in_resistance(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_ENTRY_LOCATION
    assert REASON_CONFLICT in out["engine_b_rejection_reasons"]
    assert out["canonical_primary_reject_reason"] == "LONG_ENTRY_INSIDE_RESISTANCE"


def test_rejected_setup_canonical_badge_state_not_all_pass():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=162.461,
        sl=161.9,
        tp1=163.2,
        conf=_base_conf(passed=True, location_ok=True, room_ok=False),
        res=_zones_long_in_resistance(),
    )
    badge = out["canonical_badge_state"]
    assert badge["trade"] == "FAIL"
    assert badge["location"] == "FAIL"
    assert badge["confidence"] == "PASSED_GATE_FAILED"
