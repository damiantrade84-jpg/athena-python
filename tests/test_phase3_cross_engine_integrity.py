from __future__ import annotations

import ast
from pathlib import Path

from ai_context import (
    build_ai_calibration_context,
    build_ai_calibration_context_string,
    build_ai_review_packet,
)
from ai_playbooks.engine_a_playbook import get_engine_a_playbook
from ai_playbooks.engine_b_playbook import get_engine_b_playbook
from scanner import _engine_b_scan_direction_input


def test_engine_b_independent_direction_never_falls_back_to_engine_a() -> None:
    assert (
        _engine_b_scan_direction_input(
            "LONG",
            "SHORT",
            engine_b_only=False,
            independent_enabled=True,
        )
        == "SHORT"
    )
    assert (
        _engine_b_scan_direction_input(
            "LONG",
            None,
            engine_b_only=False,
            independent_enabled=True,
        )
        is None
    )
    assert (
        _engine_b_scan_direction_input(
            "LONG",
            None,
            engine_b_only=False,
            independent_enabled=False,
        )
        == "LONG"
    )


def test_engine_b_primary_marcus_context_does_not_fabricate_engine_a() -> None:
    signal = {
        "engine_source": "engine_b",
        "is_naked": True,
        "pair": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        # Legacy generic aliases remain valid for top-level rendering, but are
        # not Engine A evidence.
        "confluenceScore": 4.2,
        "maxScore": 6.0,
        "scoreNorm": 0.7,
        "naked_data": {
            "score": 4.2,
            "max_possible": 6.0,
            "structural_verdict": "CLEAR",
            "direction": "SHORT",
            "structure_tf_actual": "H1",
            "setup_tf_policy": "M30",
            "trigger_tf_actual": "M15",
            "execution_tf_actual": "M15",
            "atr_tf_actual": "H1",
        },
    }

    context = build_ai_calibration_context(
        signal, "Engine B naked market structure", "intraday"
    )
    assert context["engine_a"]["confluenceScore"] is None
    assert context["engine_a"]["maxScore"] is None
    assert context["engine_a"]["scoreNorm"] is None
    assert context["engine_b"]["structure_score"] == 4.2
    assert context["engine_b"]["struct_tf"] == "H1"
    assert context["engine_b"]["setup_tf"] == "M30"
    assert context["engine_b"]["trigger_tf"] == "M15"
    assert context["engine_b"]["atr_tf"] == "H1"

    text = build_ai_calibration_context_string(
        signal, "Engine B naked market structure", "intraday"
    )
    assert "Engine A raw score" not in text
    assert "Engine A ScoreNorm" not in text
    assert "Engine B score: 4.2 / 6.0" in text

    packet = build_ai_review_packet(signal)
    assert packet["engine_a"]["score"] is None
    assert packet["engine_a"]["direction"] is None


def test_marcus_playbooks_use_runtime_timeframe_roles() -> None:
    engine_a = get_engine_a_playbook()
    engine_b = get_engine_b_playbook()

    assert "roleMapping" in engine_a["timeframeContract"]
    assert (
        engine_a["timeframeContract"]["roleMapping"]["momentum"]
        == "factorDiagnostics.scoringTimeframes.momentum and triggerTf"
    )
    assert "timeframeMatrix" not in engine_b
    assert "liveTriggerOverrides" not in engine_b
    assert "Server-supplied engineBContext" in engine_b["timeframeAuthority"]


def test_marcus_h4_raw_block_is_labeled_as_trend_reference_only() -> None:
    source = (Path(__file__).resolve().parents[1] / "athena.py").read_text(
        encoding="utf-8"
    )
    ast.parse(source)
    assert "RAW H4 TREND-LAYER REFERENCE" in source
    assert "Do not use this H4-only block to verify policy setup/location" in source
    assert "Engine A scoring periods:" not in source
