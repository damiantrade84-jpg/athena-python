"""P1-5 / P1-6: Engine B structure join and TP-clears-resistance gate.

The P1-5 root cause is a contract drift: the overlay renderer
(``engine_b_legacy_v1``) treats geometry keys as structure evidence while the
review context recognised only the scored keys. A geometry-only payload was
therefore drawn on the chart and discarded server-side with no error raised.
"""

from __future__ import annotations

from ai_review.engine_b_structure_contract import (
    describe_engine_b_structure_evidence,
    evaluate_tp_clears_resistance,
    has_engine_b_structure_evidence,
    resolve_nearest_levels,
)


def _geometry_only_payload():
    """What engine_b_legacy_v1 draws when no verdict was scored."""
    return {
        "bos_data": {"level": 65.2, "direction": "bull"},
        "choch_data": {"level": 64.1},
        "order_blocks": [{"lower": 64.0, "upper": 64.4}],
        "active_fvgs": [{"lower": 66.1, "upper": 66.5, "kind": "bear"}],
    }


def _scored_payload():
    return {
        "structural_verdict": "CLEAR",
        "score": 4.5,
        "passed": True,
        "nearest_support_zone": {"lower": 64.0, "upper": 64.5},
        "nearest_resistance_zone": {"lower": 67.0, "upper": 67.5},
    }


# --- P1-5 root cause --------------------------------------------------------


def test_geometry_only_payload_is_structure_evidence():
    """The regression: this payload used to be discarded server-side."""
    assert has_engine_b_structure_evidence(_geometry_only_payload()) is True


def test_geometry_only_payload_is_labelled_legacy_contract():
    described = describe_engine_b_structure_evidence(_geometry_only_payload())
    assert described["present"] is True
    assert described["contract"] == "engine_b_legacy_v1_geometry"
    assert "bos_data" in described["keys"]


def test_scored_payload_still_recognised():
    assert has_engine_b_structure_evidence(_scored_payload()) is True
    assert describe_engine_b_structure_evidence(_scored_payload())["contract"] in {
        "scored",
        "scored+geometry",
    }


def test_empty_and_sanitize_stamped_payloads_are_not_evidence():
    assert has_engine_b_structure_evidence({}) is False
    assert has_engine_b_structure_evidence(None) is False
    # sanitize_engine_b_structure_profile_fields({}) stamps only VP context.
    assert has_engine_b_structure_evidence({"profile_vp_context": {"x": 1}}) is False


def test_all_three_consumers_share_one_predicate():
    """Guards against the drift that caused P1-5 recurring."""
    from ai_review.context_diagnostics import _has_real_engine_b_structure
    from ai_review.engine_a_context import _engine_b_structure_evidence

    payload = _geometry_only_payload()
    assert _engine_b_structure_evidence(payload) is True
    assert _has_real_engine_b_structure(payload) is True
    assert has_engine_b_structure_evidence(payload) is True


# --- P1-5 nearest levels ----------------------------------------------------


def test_nearest_levels_flattened_from_zones():
    levels = resolve_nearest_levels(_scored_payload())
    assert levels["nearest_support"] == 64.0
    assert levels["nearest_resistance"] == 67.0
    assert levels["nearest_support_zone"] == (64.0, 64.5)


def test_nearest_levels_accept_low_high_aliases_and_reversed_bounds():
    levels = resolve_nearest_levels(
        {"nearest_resistance_zone": {"high": 67.0, "low": 67.5}}
    )
    assert levels["nearest_resistance_zone"] == (67.0, 67.5)


def test_nearest_levels_none_without_structure():
    levels = resolve_nearest_levels({})
    assert levels["nearest_support"] is None
    assert levels["nearest_resistance"] is None


# --- P1-6 tp_clears_resistance ---------------------------------------------


def test_tp_through_resistance_band_fails_the_gate():
    """The audit case: TP1 path runs through the mapped resistance band."""
    result = evaluate_tp_clears_resistance(
        direction="LONG",
        entry=65.92,
        targets={"tp1": 67.2, "tp2": 69.85},
        structure={"nearest_resistance_zone": {"lower": 67.0, "upper": 67.5}},
    )
    assert result["targets"]["tp1"]["clears"] is False
    assert result["targets"]["tp2"]["clears"] is True
    assert result["allClear"] is False
    assert result["downgradeRiskGeometry"] is True


def test_tp_short_of_band_passes():
    result = evaluate_tp_clears_resistance(
        direction="LONG",
        entry=65.9,
        targets={"tp1": 66.5},
        structure={"nearest_resistance_zone": {"lower": 67.0, "upper": 67.5}},
    )
    assert result["targets"]["tp1"]["clears"] is True
    assert result["downgradeRiskGeometry"] is False


def test_short_direction_uses_support_band():
    result = evaluate_tp_clears_resistance(
        direction="SHORT",
        entry=65.0,
        targets={"tp1": 64.2, "tp2": 63.5},
        structure={"nearest_support_zone": {"lower": 63.8, "upper": 64.4}},
    )
    assert result["targets"]["tp1"]["clears"] is False
    assert result["targets"]["tp2"]["clears"] is True


def test_gate_is_none_not_true_without_structure():
    """No mapped band must read as unevaluated, never as a pass."""
    result = evaluate_tp_clears_resistance(
        direction="LONG",
        entry=65.9,
        targets={"tp1": 68.4, "tp2": 69.8},
        structure={},
    )
    assert result["targets"]["tp1"]["clears"] is None
    assert result["allClear"] is None
    assert result["evaluated"] is False
    assert result["downgradeRiskGeometry"] is False
