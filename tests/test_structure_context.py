from athena_app.services.structure_context import apply_structure_context_to_score


def test_structure_context_boosts_score_when_zone_and_alignment_confirm():
    structure = {
        "structural_verdict": "CLEAR",
        "zone_touched": True,
        "near_active_zone": False,
        "ob_at_zone": True,
        "fvg_overlap": True,
        "liquidity_sweep": False,
        "engine_b_independent_direction": "LONG",
        "structure_tf": "D1",
    }

    out = apply_structure_context_to_score(
        structure,
        direction="LONG",
        base_score=1.5,
        max_score=3.0,
    )

    assert out["applied"] is True
    assert out["multiplier"] > 1.0
    assert out["adjusted_score"] > 1.5
    assert out["components"]["zone_proximity"] is True
    assert out["components"]["independent_direction_alignment"] == "aligned"


def test_structure_context_penalizes_opposed_direction():
    structure = {
        "structural_verdict": "CLEAR",
        "zone_touched": False,
        "near_active_zone": False,
        "ob_at_zone": False,
        "fvg_overlap": False,
        "liquidity_sweep": False,
        "engine_b_independent_direction": "SHORT",
        "structure_tf": "H1",
    }

    out = apply_structure_context_to_score(
        structure,
        direction="LONG",
        base_score=1.5,
        max_score=3.0,
    )

    assert out["applied"] is True
    assert out["multiplier"] < 1.0
    assert out["adjusted_score"] < 1.5
    assert out["components"]["independent_direction_alignment"] == "opposed"


def test_structure_context_is_neutral_when_structure_is_not_clear():
    out = apply_structure_context_to_score(
        {"structural_verdict": "ERROR"},
        direction="LONG",
        base_score=1.25,
        max_score=3.0,
    )

    assert out["applied"] is False
    assert out["multiplier"] == 1.0
    assert out["adjusted_score"] == 1.25
