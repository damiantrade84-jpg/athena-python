"""Phase 4: commodity params, NA subsystem denom, equity volume floor."""

from __future__ import annotations

from engine_a_v3.profile import baseline_profile
from engine_a_v3.setups import _commodity_setup_params
from engine_a_v3.subsystems import ST_AVAILABLE, ST_NA, ST_NEUTRAL, ST_UNAVAILABLE
from config import CONFIG


def test_commodity_setup_params_from_config(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_COMMODITY_SETUP_PARAMS",
        {"nat_gas": {"lookback": 10, "touch_distance_atr": 0.7, "max_extension_atr": 1.6}},
    )
    lookback, touch, extension = _commodity_setup_params("nat_gas")
    assert lookback == 10
    assert touch == 0.7
    assert extension == 1.6


def test_commodity_setup_params_defaults():
    lookback, touch, extension = _commodity_setup_params("energy_oil")
    assert lookback == 16
    assert abs(touch - 0.45) < 1e-9


def test_index_quant_weights_prefer_trend():
    profile = baseline_profile("us_indices_trackers", "intraday")
    weights = dict(profile.weights)
    assert weights["trend"] >= weights["momentum"]
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_na_subsystem_does_not_shrink_core_budget(monkeypatch):
    """NA/unavailable subsystems must not reduce core component weights."""
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_SUBSYSTEMS",
        {"ENABLED": True, "WEIGHTS_BY_FAMILY": {"forex": {"carry": 0.14, "sentiment": 0.08}}},
    )
    from engine_a_v3.quant_scorer import CORE_COMPONENTS
    from engine_a_v3.subsystems import resolve_subsystem_weights

    family = "forex"
    weights = {"trend": 0.42, "momentum": 0.28, "location": 0.21, "volume": 0.09}
    subsystem_states = {
        "intermarket": ST_NA,
        "carry": ST_NA,
        "sentiment": ST_UNAVAILABLE,
        "macro": ST_NA,
        "microstructure": ST_NEUTRAL,
    }
    sub_budget = sum(
        resolve_subsystem_weights(family).get(name, 0.0)
        for name in ("intermarket", "carry", "sentiment", "macro", "microstructure")
        if subsystem_states.get(name) == ST_AVAILABLE
    )
    assert sub_budget == 0.0
    price_scale = max(0.65, 1.0 - sub_budget) if sub_budget > 0 else 1.0
    assert price_scale == 1.0
    for name in CORE_COMPONENTS:
        assert abs(weights[name] * price_scale - weights[name]) < 1e-12


def test_score_group_subsystem_override_wins_over_family(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_SUBSYSTEMS",
        {
            "ENABLED": True,
            "WEIGHTS_BY_FAMILY": {"forex": {"carry": 0.14, "sentiment": 0.08}},
            "WEIGHTS_BY_SCORE_GROUP": {
                "forex_crosses": {"carry": 0.05, "sentiment": 0.03}
            },
        },
    )
    from engine_a_v3.subsystems import (
        resolve_subsystem_weights,
        subsystem_weight_scope,
    )

    majors = resolve_subsystem_weights("forex", "forex_majors")
    crosses = resolve_subsystem_weights("forex", "forex_crosses")
    assert majors["carry"] == 0.14
    assert majors["sentiment"] == 0.08
    assert crosses["carry"] == 0.05
    assert crosses["sentiment"] == 0.03
    assert subsystem_weight_scope("forex", "forex_majors") == "family_override"
    assert subsystem_weight_scope("forex", "forex_crosses") == "score_group"
