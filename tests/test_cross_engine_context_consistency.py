from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest

from ai_context import build_ai_review_packet
from cross_engine_context import (
    CROSS_ENGINE_CONTEXT_COLUMNS,
    build_cross_engine_context,
    build_cross_engine_context_consistency_summary,
    write_cross_engine_context_consistency_report,
)


def _doge_engine_a_with_b_rejection() -> dict:
    return {
        "timestamp": "2026-07-08T10:00:00Z",
        "pair": "DOGE/USDT",
        "type": "crypto",
        "engine_source": "ENGINE_A",
        "direction": "SHORT",
        "confluenceScore": 2.06,
        "maxScore": 3.0,
        "confidence": 0.69,
        "validationStatus": "UNVALIDATED",
        "engineATradeEnabled": True,
        "price": 0.071790,
        "sl": 0.075540,
        "tp1": 0.068040,
        "tp2": 0.064561,
        "rr1": 1.0,
        "min_rr": 1.5,
        "factorDiagnostics": {
            "trend_supports_direction": True,
            "momentum_supports_direction": True,
            "location_supports_direction": True,
            "volume_supports_direction": True,
            "breakout_close": False,
            "level_retest": False,
        },
        "engine_b": {
            "direction": "LONG",
            "current_swing_sequence": "LH_LL",
            "macro_swing_sequence": "LH_LL",
            "engine_b_canonical_actionable": False,
            "score": 0.0,
            "max_possible": 3.0,
            "nearest_support_zone": {"lower": 0.0707, "upper": 0.0732},
            "nearest_resistance_zone": {"lower": 0.0771, "upper": 0.0801},
            "engine_b_rejection_reasons": ["SHORT_ENTRY_INSIDE_SUPPORT"],
        },
    }


def test_engine_b_rejection_preserves_engine_a_native_card_and_adds_context_only():
    signal = _doge_engine_a_with_b_rejection()

    out = build_cross_engine_context(signal, primary_card_engine="engine_a")

    assert out["engine_a_native_context"]["engine_a_native_direction"] == "SHORT"
    assert out["engine_a_native_context"]["engine_a_native_score"] == pytest.approx(2.06)
    assert out["engine_a_native_context"]["engine_a_native_actionable"] is True
    assert out["engine_a_native_context"]["engine_a_native_entry"] == pytest.approx(0.071790)
    assert out["engine_a_native_context"]["engine_a_native_sl"] == pytest.approx(0.075540)
    assert out["engine_a_native_context"]["engine_a_native_tp1"] == pytest.approx(0.068040)
    assert out["engine_a_native_context"]["engine_a_native_tp2"] == pytest.approx(0.064561)
    assert out["engine_a_native_context"]["engine_a_native_validation_status"] == "UNVALIDATED"
    assert out["engine_a_native_context"]["engine_a_native_reasons"]["breakout_close"] is False
    assert out["engine_a_native_warning"] == "Engine A RR1 below configured style minimum."

    cross = out["cross_engine_context"]
    assert "Engine B context: Engine A SHORT entry is inside Engine B support zone." in cross["cross_engine_warnings"]
    assert cross["cross_engine_conflict"] == "Engine direction conflict."
    assert cross["native_score_changed_by_cross_engine"] is False
    assert cross["native_actionable_changed_by_cross_engine"] is False
    assert cross["card_hidden_by_cross_engine"] is False
    assert cross["levels_removed_by_cross_engine"] is False
    assert cross["consistency_pass"] is True
    forbidden = " ".join(cross["cross_engine_warnings"] + [cross["cross_engine_conflict"] or ""]).lower()
    assert "veto" not in forbidden
    assert "blocked by other engine" not in forbidden
    assert "suppressed by other engine" not in forbidden


def test_engine_a_rejection_preserves_engine_b_native_context():
    signal = {
        "timestamp": "2026-07-08T11:00:00Z",
        "pair": "EUR/JPY",
        "engine_source": "ENGINE_B",
        "direction": "LONG",
        "engine_a": {
            "direction": "SHORT",
            "score": 0.25,
            "actionable": False,
            "reasons": ["min_directional_failed"],
        },
        "naked_data": {
            "direction": "LONG",
            "score": 2.4,
            "max_possible": 3.0,
            "confidence": 0.8,
            "engine_b_canonical_actionable": True,
            "entry": 171.25,
            "execution_sl": 170.8,
            "execution_tp1": 172.6,
            "engine_b_rejection_reasons": [],
        },
    }

    out = build_cross_engine_context(signal, primary_card_engine="engine_b")

    assert out["engine_b_native_context"]["engine_b_native_direction"] == "LONG"
    assert out["engine_b_native_context"]["engine_b_native_score"] == pytest.approx(2.4)
    assert out["engine_b_native_context"]["engine_b_native_actionable"] is True
    assert out["engine_b_native_context"]["engine_b_native_entry"] == pytest.approx(171.25)
    assert out["engine_b_native_context"]["engine_b_native_sl"] == pytest.approx(170.8)
    assert out["engine_b_native_context"]["engine_b_native_tp1"] == pytest.approx(172.6)
    assert out["cross_engine_context"]["cross_engine_conflict"] == "Engine direction conflict."
    assert out["cross_engine_context"]["native_actionable_changed_by_cross_engine"] is False


def test_marcus_packet_keeps_native_and_cross_engine_context_separate():
    packet = build_ai_review_packet(_doge_engine_a_with_b_rejection())

    assert "engine_a_native_context" in packet
    assert "engine_b_native_context" in packet
    assert "cross_engine_context" in packet
    assert "risk_context" in packet
    assert "learning_context" in packet
    assert packet["engine_a_native_context"]["engine_a_native_score"] == pytest.approx(2.06)
    assert packet["engine_b_native_context"]["engine_b_native_score"] == pytest.approx(0.0)
    assert packet["cross_engine_context"]["cross_engine_location_warning"]
    assert packet["engine_a"]["score"] == pytest.approx(2.06)
    assert packet["engine_b"]["confidence"] == pytest.approx(0.0)


def test_cross_engine_report_writer_creates_required_csv_and_summary():
    signal = _doge_engine_a_with_b_rejection()
    out = build_cross_engine_context(signal, primary_card_engine="engine_a")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        csv_path = base / "cross_engine_context_consistency.csv"
        summary_path = base / "cross_engine_context_consistency_summary.md"

        write_cross_engine_context_consistency_report([out], csv_path)
        text = build_cross_engine_context_consistency_summary(csv_path, summary_path)

        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        assert list(rows[0].keys()) == CROSS_ENGINE_CONTEXT_COLUMNS
        assert rows[0]["pair"] == "DOGE/USDT"
        assert rows[0]["primary_card_engine"] == "engine_a"
        assert rows[0]["native_score_changed_by_cross_engine"] == "False"
        assert rows[0]["consistency_pass"] == "True"
        assert "Total cards checked: **1**" in text
        assert summary_path.exists()


def test_signals_panel_declares_native_and_cross_engine_sections():
    source = Path("static/react-app/app/src/components/panels/SignalsPanel.tsx").read_text(encoding="utf-8")

    assert "Native Engine Signal" in source
    assert "Native Engine Diagnostics" in source
    assert "Native Engine Levels" in source
    assert "Cross-engine Context" in source
    assert "AI Review Notes" in source
