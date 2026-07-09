"""Engine B weighted quality scoring and gate_pct propagation."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from engine_b_quality import (
    aggregate_quality_score,
    apply_regime_component_weights,
    compute_confluence_subscores,
    compute_structure_alignment_score,
    weighted_scoring_enabled,
)
from engine_b_subsystems import compute_subsystem_orderflow_score
from market_structure import NakedEngine, engine_b_confidence_passes


def _base_res_long():
    return {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "liquidity_sweep": False,
        "zone_touched": True,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "ob_at_zone": True,
        "order_blocks": [{"strength": 80}],
        "fvg_overlap": True,
        "nearest_support_zone": {"fvg_size_atr": 0.8},
        "active_zone_distance": 0.4,
        "distance_to_res": 3.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
        "structural_verdict": "CLEAR",
        "forex_session_structure": {
            "score_influence_enabled": True,
            "score_bonus": 0.02,
        },
    }


def test_structure_alignment_both_tf_beats_bare_bos():
    both = compute_structure_alignment_score(
        {
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "bos_confirmed": True,
        },
        "LONG",
    )
    bare_bos = compute_structure_alignment_score(
        {
            "current_swing_sequence": "RANGING",
            "macro_swing_sequence": "RANGING",
            "bos_confirmed": True,
        },
        "LONG",
    )
    assert both > bare_bos
    assert both == pytest.approx(1.0)
    assert bare_bos == pytest.approx(0.35)


def test_fvg_confluence_uses_size_not_bool_only():
    rich = compute_confluence_subscores(
        {
            "fvg_overlap": True,
            "nearest_support_zone": {"fvg_size_atr": 1.0},
            "ob_at_zone": False,
        },
        "LONG",
        1.0,
    )
    thin = compute_confluence_subscores(
        {
            "fvg_overlap": True,
            "nearest_support_zone": {"fvg_size_atr": 0.1},
            "ob_at_zone": False,
        },
        "LONG",
        1.0,
    )
    assert rich["fvg_confluence"] > thin["fvg_confluence"]


def test_weighted_scoring_populates_quality_fields(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_WEIGHTED_SCORING",
        {**config.CONFIG.get("ENGINE_B_WEIGHTED_SCORING", {}), "ENABLED": True},
    )
    engine = NakedEngine()
    out = engine.calculate_confidence(
        _base_res_long(),
        current_price=100.0,
        direction="LONG",
        style_profile={
            "style": "swing",
            "min_score": 5.0,
            "min_room_atr": 0.35,
            "min_rr": 1.5,
            "require_macro_align": False,
        },
    )
    assert out["weighted_scoring_enabled"] is True
    assert out["quality_components"]
    assert out["quality_max_possible"] > 0
    gate_ok, _ = engine_b_confidence_passes(
        out,
        {"min_score": 5.0, "style": "swing"},
        regime_label="RANGING",
        asset_type="forex",
    )
    assert out["passed"] is True
    assert gate_ok is True


def test_weighted_scoring_disabled_matches_legacy_bonus_shape(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_WEIGHTED_SCORING",
        {**config.CONFIG.get("ENGINE_B_WEIGHTED_SCORING", {}), "ENABLED": False},
    )
    engine = NakedEngine()
    res = _base_res_long()
    res["bos_mtf_confirmed"] = False
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    assert out["weighted_scoring_enabled"] is False
    assert out.get("quality_score", 0.0) == 0.0
    assert out["bonus_points"] == pytest.approx(2.0)


def test_regime_component_mult_boosts_ranging_fvg():
    subscores = {
        "fvg_confluence": 0.8,
        "ob_confluence": 0.7,
        "bos_followthrough": 0.5,
    }
    weighted = apply_regime_component_weights(subscores, "RANGING", "forex")
    assert weighted["fvg_confluence"] > subscores["fvg_confluence"]
    assert weighted["bos_followthrough"] < subscores["bos_followthrough"]


def test_subsystem_orderflow_missing_feed_is_neutral_zero(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_SUBSYSTEMS",
        {"ENABLED": True, "WEIGHTS_BY_FAMILY": {"forex": {"carry": 0.6, "sentiment": 0.4}}},
    )
    monkeypatch.setattr(
        "engine_b_subsystems._carry_entry",
        lambda *_args, **_kwargs: {"state": "unavailable"},
    )
    monkeypatch.setattr(
        "engine_b_subsystems._sentiment_entry",
        lambda *_args, **_kwargs: {"state": "unavailable"},
    )
    assert compute_subsystem_orderflow_score("forex", "EUR/USD", "LONG") == 0.0


def test_subsystem_orderflow_aligned_carry_scores_positive(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_SUBSYSTEMS",
        {"ENABLED": True, "WEIGHTS_BY_FAMILY": {"forex": {"carry": 1.0}}},
    )
    monkeypatch.setattr(
        "engine_b_subsystems._carry_entry",
        lambda *_args, **_kwargs: {"state": "available", "signal": 0.8, "quality": 0.7},
    )
    monkeypatch.setattr(
        "engine_b_subsystems._sentiment_entry",
        lambda *_args, **_kwargs: {"state": "na"},
    )
    score = compute_subsystem_orderflow_score("forex", "EUR/USD", "LONG")
    assert score > 0.0


def test_aggregate_quality_score_returns_points_and_max():
    cfg = {
        "COMPONENT_MAX": {"structure_alignment": 1.0},
        "COMPONENT_WEIGHTS": {"structure_alignment": 1.0},
    }
    points, max_pts, components = aggregate_quality_score(
        {"structure_alignment": 0.5}, cfg
    )
    assert points == pytest.approx(0.5)
    assert max_pts == pytest.approx(1.0)
    assert components["structure_alignment"] == pytest.approx(0.5)


def test_scanner_watchlist_uses_total_score_floor_not_gate_pct(monkeypatch):
    from scanner import _engine_b_structure_ready_watchlist_detail

    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STRUCTURE_READY_WATCHLIST",
        {"ENABLED": True, "MIN_SCORE_RATIO": 0.85},
    )
    base_conf = {
        "passed": False,
        "gate_score": 5.0,
        "gate_max_possible": 5.0,
        "gate_pct": 100,
        "max_possible": 11.0,
        "trigger_ok": False,
        "entry_ok": False,
        "structure_ok": True,
        "location_ok": True,
        "space_gate_ok": True,
        "rr_ok": True,
    }
    res = {
        "asset_type": "forex",
        "structural_verdict": "CLEAR",
        "bos_confirmed": True,
        "zone_touched": True,
    }

    # gate_pct is 100 whenever all mandatory gates pass, so it must not
    # satisfy the score floor on its own: low graded total -> no watchlist.
    low_quality = dict(base_conf, score=5.5)
    assert (
        _engine_b_structure_ready_watchlist_detail(low_quality, res, config=config.CONFIG)
        is None
    )

    # High graded total (score/max >= MIN_SCORE_RATIO) qualifies.
    high_quality = dict(base_conf, score=9.5)
    assert (
        _engine_b_structure_ready_watchlist_detail(high_quality, res, config=config.CONFIG)
        is not None
    )


def test_weighted_scoring_enabled_reads_config():
    assert isinstance(weighted_scoring_enabled(), bool)


def test_derive_engine_b_score_pct_prefers_graded_total_over_gate_pct():
    from ai_context import derive_engine_b_score_pct

    # gate_pct saturates at 100 for every emitted signal, so the headline must
    # come from the graded total (score/max), never gate_pct.
    conf = {
        "gate_pct": 100.0,
        "pct": 88.0,
        "score": 5.28,
        "max_possible": 6.04,
    }
    assert derive_engine_b_score_pct(conf) == 88.0


def test_derive_engine_b_score_pct_derives_when_provided_pct_stale():
    from ai_context import derive_engine_b_score_pct

    conf = {
        "gate_pct": 100.0,
        "pct": 100.0,
        "score": 5.28,
        "max_possible": 6.04,
    }
    assert round(derive_engine_b_score_pct(conf), 1) == 87.4


def test_quick_audit_engine_b_score_pct_uses_graded_total():
    from execution import _quick_audit_context

    ctx = _quick_audit_context(
        {"is_naked": True},
        {
            "gate_pct": 100.0,
            "pct": 88.0,
            "score": 5.3,
            "max_possible": 6.04,
            "structural_verdict": "CLEAR",
        },
    )
    assert ctx["score_pct"] == 88.0
