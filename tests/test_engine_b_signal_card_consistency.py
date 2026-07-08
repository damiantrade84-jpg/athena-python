"""Tests for Engine B signal card UI consistency audit helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine_b_canonical_actionability import evaluate_ui_consistency, reconcile_engine_b_actionability
from tools.build_engine_b_signal_card_consistency_summary import build_summary


def test_evaluate_ui_consistency_detects_location_mismatch():
    conf = {
        "location_ok": True,
        "room_ok": True,
        "passed": True,
        "engine_b_rejection_reasons": ["LONG_ENTRY_INSIDE_RESISTANCE"],
        "execution_sl": 161.9,
        "execution_tp1": 163.2,
    }
    canonical_fields = {
        "raw_location_ok": True,
        "raw_room_ok": True,
        "raw_rr_ok": True,
        "confidence_passed": True,
        "canonical_trade_ok": False,
        "canonical_location_ok": False,
        "canonical_room_ok": False,
        "canonical_rr_ok": True,
        "canonical_structure_ok": True,
        "canonical_trigger_ok": True,
        "canonical_badge_state": {"confidence": "CONFIDENCE PASSED"},
        "canonical_secondary_reject_reasons": ["LONG_ENTRY_INSIDE_RESISTANCE"],
        "canonical_primary_reject_reason": "LONG_ENTRY_INSIDE_RESISTANCE",
    }
    out = evaluate_ui_consistency(conf=conf, canonical_fields=canonical_fields)
    assert out["ui_consistency_pass"] is False
    assert "location_ok_true_but_LONG_ENTRY_INSIDE_RESISTANCE" in out["inconsistency_reasons"]
    assert "confidence_passed_but_canonical_trade_false" in out["inconsistency_reasons"]
    assert out["suggested_levels_displayed"] is True


def test_evaluate_ui_consistency_passes_when_display_matches_canonical():
    conf = {
        "location_ok": True,
        "room_ok": False,
        "passed": True,
        "structural_tp_too_close": False,
    }
    canonical_fields = {
        "raw_location_ok": True,
        "raw_room_ok": False,
        "raw_rr_ok": True,
        "confidence_passed": True,
        "canonical_trade_ok": False,
        "canonical_location_ok": False,
        "canonical_room_ok": False,
        "canonical_rr_ok": True,
        "canonical_structure_ok": True,
        "canonical_trigger_ok": True,
        "canonical_badge_state": {"confidence": "PASSED_GATE_FAILED"},
    }
    out = evaluate_ui_consistency(conf=conf, canonical_fields=canonical_fields)
    assert out["displayed_location_badge"] == "FAIL"
    assert out["displayed_room_rr_badge"] == "FAIL"


def test_summary_builder_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        csv_path = base / "consistency.csv"
        csv_path.write_text(
            "timestamp,pair,direction,entry,canonical_trade_ok,confidence_passed,"
            "displayed_location_badge,displayed_room_rr_badge,suggested_levels_displayed,"
            "ui_consistency_pass,inconsistency_reasons\n"
            "t,SOL,LONG,1,false,true,OK,OK,true,false,location_ok_true\n",
            encoding="utf-8",
        )
        out_path = base / "summary.md"
        text = build_summary(csv_path, out_path)
        assert "Total cards checked" in text
        assert out_path.exists()


def test_reconcile_populates_signal_card_audit_fields():
    out = reconcile_engine_b_actionability(
        direction="LONG",
        entry=162.461,
        sl=161.9,
        tp1=163.2,
        conf={
            "passed": True,
            "location_ok": True,
            "structure_ok": True,
            "trigger_ok": True,
            "room_ok": False,
            "execution_sl": 161.9,
            "execution_tp1": 163.2,
            "engine_b_diagnostics": {"reason_codes": []},
        },
        res={
            "nearest_support_zone": {"lower": 161.0, "upper": 162.0},
            "nearest_resistance_zone": {"lower": 162.4573, "upper": 163.0009},
        },
    )
    assert "canonical_location_ok" in out
    assert "canonical_badge_state" in out
    assert out["raw_location_ok"] is True
    # Location gate passed → canonical location mirrors the engine gate; the
    # room/space gate failure is what makes the card non-actionable.
    assert out["canonical_location_ok"] is True
    assert out["canonical_room_ok"] is False
    assert out["canonical_trade_ok"] is False
