"""Regression tests for Engine B analysis-path direction resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from athena_app.services.engine_b_direction import (
    NO_CLEAR_DIRECTION_ERROR,
    INVALID_EXECUTION_DIRECTION_ERROR,
    annotate_signal_direction_metadata,
    engine_b_direction_alignment,
    independent_conflict_blocks_emit,
    normalize_caller_direction,
    resolve_analysis_direction,
)
from engine_b_subsystems import engine_b_pick_directional_candidate

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_normalize_caller_direction_accepts_explicit_side():
    direction, err = normalize_caller_direction("short")
    assert direction == "SHORT"
    assert err is None


def test_normalize_caller_direction_defers_when_missing():
    direction, err = normalize_caller_direction(None)
    assert direction is None
    assert err is None


def test_normalize_caller_direction_rejects_missing_in_execution_mode():
    direction, err = normalize_caller_direction("", execution_mode=True)
    assert direction is None
    assert err == INVALID_EXECUTION_DIRECTION_ERROR


def test_resolve_analysis_direction_uses_probe_when_missing():
    def _probe(**_kwargs):
        return "SHORT", {"engine_b_independent_direction": {"direction": "SHORT"}}, {"score": 4.2}

    direction, res, conf, source, err = resolve_analysis_direction(
        direction=None,
        probe_fn=_probe,
        probe_kwargs={},
    )
    assert err is None
    assert direction == "SHORT"
    assert source == "independent_probe"
    assert res is not None
    assert conf is not None


def test_resolve_analysis_direction_fail_closed_when_probe_has_no_clear():
    def _probe(**_kwargs):
        return None, None, None

    direction, res, conf, source, err = resolve_analysis_direction(
        direction=None,
        probe_fn=_probe,
        probe_kwargs={},
    )
    assert direction is None
    assert res is None
    assert conf is None
    assert source == ""
    assert err == NO_CLEAR_DIRECTION_ERROR


def test_compute_naked_analysis_no_longer_defaults_missing_direction_to_long():
    src = (REPO_ROOT / "athena.py").read_text(encoding="utf-8")
    assert 'sig.get("direction", "LONG")' not in src
    assert "normalize_caller_direction" in src
    assert "resolve_analysis_direction" in src


def test_compute_naked_analysis_routes_crypto_through_ab_signal_feed():
    src = (REPO_ROOT / "athena.py").read_text(encoding="utf-8")
    chunk = src.split("def _compute_naked_analysis(", 1)[1].split(
        '@app.route("/api/naked-analysis"', 1
    )[0]
    assert "_fetch_ab_crypto_signal_candles(pair_obj, tf, limit)" in chunk
    assert "fetch_market_state as _fms" not in chunk


def test_api_scan_naked_passes_canonical_d1_h4_h1_to_analyze_structure():
    src = (REPO_ROOT / "athena.py").read_text(encoding="utf-8")
    start = src.index("def api_scan_naked(")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    assert "h4_candles = _tf_map.get(\"H4\", [])" in body
    assert "h1_candles = _tf_map.get(\"H1\", [])" in body
    assert "resolve_engine_b_h4_snap" in body


def test_engine_b_direction_alignment_labels():
    assert engine_b_direction_alignment("LONG", {"direction": "LONG"}) == "aligned"
    assert engine_b_direction_alignment("LONG", {"direction": "SHORT"}) == "opposed"
    assert engine_b_direction_alignment("LONG", None) == "none"


def test_independent_conflict_blocks_emit_only_when_enabled():
    structure = {
        "engine_b_independent_direction": {
            "direction": "SHORT",
            "confidence": "HIGH",
        }
    }
    assert independent_conflict_blocks_emit("LONG", structure, enabled=True) is True
    assert independent_conflict_blocks_emit("SHORT", structure, enabled=True) is False
    assert independent_conflict_blocks_emit("LONG", structure, enabled=False) is False


def test_engine_b_pick_directional_candidate_requires_gap_when_configured():
    candidates = [
        {"direction": "LONG", "score": 4.1},
        {"direction": "SHORT", "score": 4.0},
    ]
    assert engine_b_pick_directional_candidate(candidates, min_gap=0.0)["direction"] == "LONG"
    assert engine_b_pick_directional_candidate(candidates, min_gap=0.5) is None


def test_annotate_signal_direction_metadata_adds_alignment_fields():
    signal: dict[str, Any] = {}
    annotate_signal_direction_metadata(
        signal,
        {"engine_b_independent_direction": {"direction": "SHORT", "confidence": "HIGH"}},
        "LONG",
    )
    assert signal["engine_b_direction_alignment"] == "opposed"
    assert signal["engine_b_independent_direction"]["direction"] == "SHORT"
