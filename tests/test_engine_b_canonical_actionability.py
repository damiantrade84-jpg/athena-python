"""Regression tests for Engine B canonical actionability reconciliation.

Canonical statuses are gate-driven: they mirror the engine's real gate
decisions (structure/location/entry/space/rr/passed). Zone-containment,
capped-TP, partial-RR and legacy-field conflicts are diagnostics, not
rejections — Engine B is a zone-retest engine whose nearest zones are
selected to include bands containing price.
"""

from __future__ import annotations

from engine_b_canonical_actionability import (
    REASON_CONFLICT,
    REASON_PARTIAL_RR,
    STATUS_ACTIONABLE,
    STATUS_REJECT_CONFIDENCE,
    STATUS_REJECT_ENTRY_LOCATION,
    STATUS_REJECT_NO_ROOM,
    STATUS_REJECT_NO_TRIGGER,
    STATUS_REJECT_RR_QUALITY,
    STATUS_REJECT_STRUCTURAL_TP,
    STATUS_REJECT_STRUCTURE,
    STRUCTURE_PASS,
    STRUCTURE_REJECT,
    reconcile_engine_b_actionability,
)
from market_structure import (
    ENGINE_B_REASON_NO_TRIGGER_PATTERN,
    ENGINE_B_REASON_RESISTANCE_TOO_CLOSE,
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


def _passing_conf(**overrides):
    conf = _base_conf(
        passed=True,
        checklist_passed=True,
        structure_ok=True,
        location_ok=True,
        entry_ok=True,
        space_gate_ok=True,
        rr_ok=True,
    )
    conf.update(overrides)
    return conf


def _zones_support(low=17.4993, high=17.5798):
    return {
        "nearest_support_zone": {"lower": low, "upper": high},
        "nearest_resistance_zone": {"lower": 17.5081, "upper": 17.6061},
    }


# ── Passing signals must be canonically ACTIONABLE ──────────────────────────


def test_passing_signal_is_actionable():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.55,
        sl=17.45,
        tp1=17.90,
        conf=_passing_conf(),
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    assert out["engine_b_canonical_actionable"] is True
    assert out["engine_b_canonical_status"] == STATUS_ACTIONABLE
    assert out["structural_status"] == STRUCTURE_PASS
    assert out["canonical_trade_ok"] is True
    assert out["suggested_levels_executable"] is True
    assert out["engine_b_rejection_reasons"] == []


def test_passing_zone_retest_entry_inside_zone_is_diagnostic_not_reject():
    """Entry at/inside the zone is the Engine B setup, not a canonical failure."""
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.40,
        conf=_passing_conf(),
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    assert out["entry_inside_support"] is True
    assert out["engine_b_canonical_status"] == STATUS_ACTIONABLE
    assert "SHORT_ENTRY_INSIDE_SUPPORT" in out["engine_b_canonical_diagnostics"]


def test_reconcile_is_idempotent_on_own_output():
    """Re-running on a conf carrying prior canonical output must not self-reject."""
    conf = _passing_conf()
    first = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.55,
        sl=17.45,
        tp1=17.90,
        conf=conf,
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    conf.update(first)
    second = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.55,
        sl=17.45,
        tp1=17.90,
        conf=conf,
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    assert second["engine_b_canonical_status"] == STATUS_ACTIONABLE
    assert "ENGINE_B_ACTIONABLE_EXPLICIT_NO" not in second["engine_b_rejection_reasons"]
    assert REASON_CONFLICT not in second["engine_b_canonical_diagnostics"]


def test_unset_legacy_is_actionable_is_not_a_conflict():
    """Nothing upstream sets is_actionable — its absence must not reject."""
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.55,
        sl=17.45,
        tp1=17.90,
        conf=_passing_conf(),
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    assert out["engine_b_canonical_actionable"] is True
    assert REASON_CONFLICT not in out["engine_b_rejection_reasons"]
    assert REASON_CONFLICT not in out["engine_b_canonical_diagnostics"]


# ── Gate-driven rejections ───────────────────────────────────────────────────


def test_structure_gate_failed_rejects():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.55,
        sl=17.45,
        tp1=17.90,
        conf=_passing_conf(structure_ok=False, passed=False),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_STRUCTURE
    assert out["structural_status"] == STRUCTURE_REJECT
    assert out["canonical_structure_ok"] is False


def test_entry_gate_failed_rejects_no_trigger():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.20,
        conf=_base_conf(trigger_ok=False),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_TRIGGER


def test_entry_ok_catalyst_overrides_raw_trigger_flag():
    """BOS/sweep/CHoCH catalyst entries (entry_ok=True, trigger_ok=False) pass."""
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.20,
        conf=_passing_conf(
            trigger_ok=False,
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_NO_TRIGGER_PATTERN]},
        ),
        res=_zones_support(low=17.10, high=17.20),
        candle_freshness_status="fresh",
    )
    assert out["engine_b_canonical_status"] == STATUS_ACTIONABLE
    assert out["canonical_trigger_ok"] is True
    assert ENGINE_B_REASON_NO_TRIGGER_PATTERN in out["engine_b_canonical_diagnostics"]


def test_no_trigger_pattern_with_entry_gate_failed_rejects():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.20,
        conf=_base_conf(
            entry_ok=False,
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_NO_TRIGGER_PATTERN]},
        ),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_TRIGGER
    assert ENGINE_B_REASON_NO_TRIGGER_PATTERN in out["engine_b_rejection_reasons"]


def test_location_gate_failed_rejects_entry_location():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.40,
        conf=_base_conf(location_ok=False),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_ENTRY_LOCATION
    assert out["canonical_primary_reject_reason"] == "SHORT_ENTRY_INSIDE_SUPPORT"
    assert out["canonical_location_ok"] is False


def test_location_gate_passed_keeps_canonical_location_ok():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.57697,
        sl=17.65,
        tp1=17.40,
        conf=_passing_conf(),
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    assert out["canonical_location_ok"] is True


def test_room_ok_false_rejects_no_room():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.85,
        conf=_base_conf(room_ok=False),
        res={"nearest_resistance_zone": {"lower": 17.70, "upper": 17.80}},
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM


def test_support_too_close_with_space_gate_failed_rejects():
    codes = [ENGINE_B_REASON_SUPPORT_TOO_CLOSE]
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.60,
        sl=17.65,
        tp1=17.40,
        conf=_base_conf(room_ok=False, engine_b_diagnostics={"reason_codes": codes}),
        res=_zones_support(),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM
    assert ENGINE_B_REASON_SUPPORT_TOO_CLOSE in out["engine_b_rejection_reasons"]


def test_resistance_too_close_with_space_gate_failed_rejects():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.85,
        conf=_base_conf(
            room_ok=False,
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_RESISTANCE_TOO_CLOSE]},
        ),
        res={"nearest_resistance_zone": {"lower": 17.70, "upper": 17.80}},
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM
    assert ENGINE_B_REASON_RESISTANCE_TOO_CLOSE in out["engine_b_rejection_reasons"]


def test_space_gate_substitution_overrides_raw_room_flag():
    """RR-substituted space gate (space_gate_ok=True, room_ok=False) passes."""
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.50,
        sl=17.45,
        tp1=17.85,
        conf=_passing_conf(room_ok=False),
        res={"nearest_resistance_zone": {"lower": 17.70, "upper": 17.80}},
        candle_freshness_status="fresh",
    )
    assert out["engine_b_canonical_status"] == STATUS_ACTIONABLE
    assert out["canonical_room_ok"] is True


def test_invalid_execution_levels_reject_structural_tp():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.35,
        conf=_base_conf(
            execution_levels_valid=False,
            execution_level_reject_reason="max_sl_exceeded",
        ),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_STRUCTURAL_TP
    assert "max_sl_exceeded" in out["engine_b_rejection_reasons"]


def test_rr_gate_failed_rejects_rr_quality():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.35,
        conf=_base_conf(rr_ok=False),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_RR_QUALITY
    assert out["canonical_rr_ok"] is False


def test_confidence_gate_failed_rejects_when_no_specific_gate():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.20,
        conf=_base_conf(),
        res=_zones_support(low=17.10, high=17.20),
    )
    assert out["engine_b_canonical_status"] == STATUS_REJECT_CONFIDENCE
    assert out["engine_b_canonical_actionable"] is False


# ── Score/AI-confidence cannot upgrade a reject ─────────────────────────────


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


def test_stale_candles_reject_data():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=17.55,
        sl=17.45,
        tp1=17.90,
        conf=_passing_conf(),
        res=_zones_support(),
        candle_freshness_status="stale",
    )
    assert out["engine_b_canonical_status"] == "REJECT_DATA"
    assert out["engine_b_canonical_actionable"] is False


# ── Diagnostics (not rejections) ─────────────────────────────────────────────


def test_partial_rr_is_diagnostic_when_gate_rr_passes():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.35,
        conf=_passing_conf(
            execution_rr1=0.74,
            execution_rr2=1.8,
            rr_used_for_gate=1.8,
        ),
        res=_zones_support(low=17.10, high=17.20),
        style_profile={"min_rr": 1.35},
        candle_freshness_status="fresh",
    )
    assert out["partial_target_quality_fail"] is True
    assert REASON_PARTIAL_RR in out["engine_b_canonical_diagnostics"]
    assert REASON_PARTIAL_RR not in out["engine_b_rejection_reasons"]
    assert out["engine_b_canonical_status"] == STATUS_ACTIONABLE


def test_structural_tp_too_close_is_diagnostic_when_gates_pass():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.40,
        sl=17.45,
        tp1=17.35,
        conf=_passing_conf(
            engine_b_diagnostics={"reason_codes": [ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE]},
        ),
        res=_zones_support(low=17.10, high=17.20),
        candle_freshness_status="fresh",
    )
    assert out["engine_b_canonical_status"] == STATUS_ACTIONABLE
    assert ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE in out["engine_b_canonical_diagnostics"]
    assert out["canonical_room_ok"] is True
    assert out["canonical_badge_state"]["room_rr"] == "PASS"


def test_tp1_inside_zone_is_diagnostic():
    out = reconcile_engine_b_actionability(
        direction="SHORT",
        entry=17.60,
        sl=17.65,
        tp1=17.55,
        conf=_passing_conf(),
        res=_zones_support(),
        candle_freshness_status="fresh",
    )
    assert "SHORT_TP1_INSIDE_SUPPORT" in out["engine_b_canonical_diagnostics"]
    assert out["engine_b_canonical_status"] == STATUS_ACTIONABLE


def test_explicit_legacy_no_with_passed_true_records_conflict_diagnostic():
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
        res={
            "nearest_support_zone": {"lower": 161.0, "upper": 162.0},
            "nearest_resistance_zone": {"lower": 162.4573, "upper": 163.0009},
        },
    )
    # Space gate (room fallback) fail outranks the legacy explicit-NO debug status.
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_ROOM
    assert REASON_CONFLICT in out["engine_b_canonical_diagnostics"]
    assert out["engine_b_canonical_actionable"] is False


# ── Marcus Reid fixture: catalyst-less SHORT with capped TP1 ────────────────


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
    assert out["engine_b_canonical_status"] == STATUS_REJECT_NO_TRIGGER
    assert out["entry_inside_support"] is True
    assert out["tp1_inside_support"] is True
    assert REASON_PARTIAL_RR in out["engine_b_canonical_diagnostics"]
    assert out["ai_calibration_pass_fallback_raw"] is True


# ── Badge state ──────────────────────────────────────────────────────────────


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
        res={
            "nearest_support_zone": {"lower": 161.0, "upper": 162.0},
            "nearest_resistance_zone": {"lower": 162.4573, "upper": 163.0009},
        },
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
            room_ok=False,
            execution_sl=161.9,
            execution_tp1=163.2,
        ),
        res={
            "nearest_support_zone": {"lower": 161.0, "upper": 162.0},
            "nearest_resistance_zone": {"lower": 162.4573, "upper": 163.0009},
        },
    )
    assert out["suggested_levels_executable"] is False


def test_rejected_setup_canonical_badge_state_not_all_pass():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=162.461,
        sl=161.9,
        tp1=163.2,
        conf=_base_conf(passed=True, location_ok=True, room_ok=False),
        res={
            "nearest_support_zone": {"lower": 161.0, "upper": 162.0},
            "nearest_resistance_zone": {"lower": 162.4573, "upper": 163.0009},
        },
    )
    badge = out["canonical_badge_state"]
    assert badge["trade"] == "FAIL"
    assert badge["location"] == "PASS"
    assert badge["room_rr"] == "FAIL"
    assert badge["confidence"] == "PASSED_GATE_FAILED"
