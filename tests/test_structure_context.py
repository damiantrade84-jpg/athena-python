from athena_app.services.structure_context import apply_structure_context_to_score
import config


def test_structure_context_boosts_score_when_zone_and_alignment_confirm(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
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


def test_structure_context_accepts_engine_b_independent_direction_dict(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    structure = {
        "structural_verdict": "CLEAR",
        "zone_touched": True,
        "engine_b_independent_direction": {"direction": "LONG", "confidence": "high"},
    }

    out = apply_structure_context_to_score(
        structure,
        direction="LONG",
        base_score=1.5,
        max_score=3.0,
    )

    assert out["components"]["independent_direction_alignment"] == "aligned"
    assert out["adjusted_score"] > 1.5


def test_structure_context_penalizes_opposed_direction(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
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


def test_structure_context_uses_configured_component_bonus(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "standard")
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_COMPONENT_BONUSES",
        {
            "zone_proximity": 0.10,
            "ob_at_zone": 0.0,
            "fvg_overlap": 0.0,
            "liquidity_sweep": 0.0,
            "independent_direction_aligned": 0.0,
            "independent_direction_opposed": -0.08,
        },
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS",
        {"min": 0.85, "max": 1.50},
    )

    out = apply_structure_context_to_score(
        {
            "structural_verdict": "CLEAR",
            "zone_touched": True,
            "engine_b_independent_direction": "LONG",
        },
        direction="LONG",
        base_score=1.0,
        max_score=3.0,
    )

    assert out["applied"] is True
    assert out["multiplier"] == 1.10
    assert out["adjusted_score"] == 1.10


def test_structure_context_can_disable_score_influence(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", False)

    out = apply_structure_context_to_score(
        {
            "structural_verdict": "CLEAR",
            "zone_touched": True,
            "ob_at_zone": True,
            "engine_b_independent_direction": "LONG",
        },
        direction="LONG",
        base_score=1.5,
        max_score=3.0,
    )

    assert out["applied"] is False
    assert out["multiplier"] == 1.0
    assert out["adjusted_score"] == 1.5
    assert out["components"]["zone_proximity"] is True


def test_structure_context_applies_forex_session_bonus_only_when_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "standard")
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_FOREX_SESSION_STRUCTURE_WEIGHTING",
        {"ENABLED": True, "SCORE_INFLUENCE_ENABLED": True},
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_COMPONENT_BONUSES",
        {
            "zone_proximity": 0.0,
            "ob_at_zone": 0.0,
            "fvg_overlap": 0.0,
            "liquidity_sweep": 0.0,
            "independent_direction_aligned": 0.0,
            "independent_direction_opposed": -0.08,
        },
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS",
        {"min": 0.85, "max": 1.50},
    )

    out = apply_structure_context_to_score(
        {
            "structural_verdict": "CLEAR",
            "zone_touched": True,
            "asset_type": "forex",
            "forex_session_structure": {
                "session": "london_ny_overlap",
                "score_bonus": 0.03,
            },
        },
        direction="LONG",
        base_score=1.0,
        max_score=3.0,
    )

    assert out["applied"] is True
    assert out["multiplier"] == 1.03
    assert out["components"]["forex_session"] == "london_ny_overlap"
    assert out["components"]["forex_session_score_bonus"] == 0.03


def test_structure_context_applies_equity_session_bonus_only_when_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "standard")
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_EQUITY_SESSION_STRUCTURE_WEIGHTING",
        {"ENABLED": True, "SCORE_INFLUENCE_ENABLED": True},
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_COMPONENT_BONUSES",
        {
            "zone_proximity": 0.0,
            "ob_at_zone": 0.0,
            "fvg_overlap": 0.0,
            "liquidity_sweep": 0.0,
            "independent_direction_aligned": 0.0,
            "independent_direction_opposed": -0.08,
        },
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS",
        {"min": 0.85, "max": 1.50},
    )

    out = apply_structure_context_to_score(
        {
            "structural_verdict": "CLEAR",
            "zone_touched": True,
            "asset_type": "stock",
            "equity_session_structure": {
                "asset_class": "stock",
                "session": "us_cash_open",
                "score_bonus": 0.023,
            },
        },
        direction="LONG",
        base_score=1.0,
        max_score=3.0,
    )

    assert out["applied"] is True
    assert out["multiplier"] == 1.023
    assert out["components"]["equity_session"] == "us_cash_open"
    assert out["components"]["equity_session_score_bonus"] == 0.023


def test_structure_context_influence_level_scales_bonus(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "enhanced")
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL_MULTIPLIERS",
        {"standard": 1.0, "enhanced": 1.25},
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_COMPONENT_BONUSES",
        {
            "zone_proximity": 0.08,
            "ob_at_zone": 0.0,
            "fvg_overlap": 0.0,
            "liquidity_sweep": 0.0,
            "independent_direction_aligned": 0.0,
            "independent_direction_opposed": -0.08,
        },
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS",
        {"min": 0.85, "max": 1.50},
    )

    out = apply_structure_context_to_score(
        {"structural_verdict": "CLEAR", "zone_touched": True},
        direction="LONG",
        base_score=1.0,
        max_score=3.0,
    )

    assert out["multiplier"] == 1.10
    assert out["components"]["structure_influence_level"] == "enhanced"
    assert out["components"]["structure_influence_multiplier"] == 1.25


def test_structure_context_confluence_bonus_is_default_off(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "standard")
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_CONFLUENCE",
        {"ENABLED": False, "BONUS": 0.03},
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS",
        {"min": 0.85, "max": 1.50},
    )

    out = apply_structure_context_to_score(
        {
            "structural_verdict": "CLEAR",
            "zone_touched": True,
            "ob_at_zone": True,
            "fvg_overlap": True,
            "engine_b_independent_direction": "LONG",
        },
        direction="LONG",
        base_score=1.0,
        max_score=3.0,
    )

    assert out["multiplier"] == 1.22
    assert out["components"]["confluence_bonus"] == 0.0
