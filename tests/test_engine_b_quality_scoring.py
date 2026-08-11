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


def test_structure_alignment_break_evidence_beats_swing_sequence():
    # Default (sequence direction retired): a bare aligned BOS is the direction
    # authority and must outscore stale both-TF HH_HL alignment with no break.
    swing_only = compute_structure_alignment_score(
        {
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
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
    assert bare_bos > swing_only
    assert bare_bos == pytest.approx(0.85)
    assert swing_only == pytest.approx(0.0)


def test_guarded_sequence_and_sweep_receive_bounded_structure_quality(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False
    )
    guarded_sequence = compute_structure_alignment_score(
        {
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "fresh_sequence_structure_ok": True,
        },
        "LONG",
    )
    guarded_sweep = compute_structure_alignment_score(
        {
            "current_swing_sequence": "CONTRACTION",
            "macro_swing_sequence": "CONTRACTION",
            "liquidity_sweep_structure_ok": True,
        },
        "LONG",
    )
    directionless_sweep = compute_structure_alignment_score(
        {
            "current_swing_sequence": "CONTRACTION",
            "macro_swing_sequence": "CONTRACTION",
            "liquidity_sweep": True,
            "sweep_direction": None,
        },
        "LONG",
    )

    assert guarded_sequence == pytest.approx(0.55)
    assert guarded_sweep == pytest.approx(0.45)
    assert directionless_sweep == pytest.approx(0.0)


def test_structure_alignment_legacy_sequence_ladder_restorable(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", True
    )
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
    assert "volume_confirmation" not in out["quality_components"]
    assert out["profile_context"]["enabled"] is False
    assert "profile_reaction" not in out["quality_components"]
    assert out["volume_scoring_applicable"] is False
    assert out["quality_max_possible"] > 0
    # Quality percent is the only Engine B score with a usable range on a pass:
    # gate_pct is 100 by definition and pct floors near 83% because gate_score
    # always equals gate_max_possible when `passed` is true.
    assert out["gate_score"] == out["gate_max_possible"]
    assert out["quality_pct"] == pytest.approx(
        out["quality_points_net"] / out["quality_denominator"] * 100, abs=0.1
    )
    assert 0.0 < out["quality_pct"] < 100.0

    gate_ok, effective_min = engine_b_confidence_passes(
        out,
        {"min_score": 5.0, "style": "swing"},
        regime_label="RANGING",
        asset_type="forex",
    )
    assert out["passed"] is True
    # The style/regime floor is still resolved the same way (5.0 x 1.10 = 5.5),
    # but WHICH quantity it is compared against depends on the configured basis.
    # Assert the gate agrees with the comparison its own basis declares, rather
    # than pinning one basis's arithmetic — the `total` basis is structurally
    # non-binding for most style/regime combinations (gate_score always equals
    # gate_max_possible on a pass, so score >= 5.0 is guaranteed), which is why
    # ENGINE_B_MIN_SCORE_BASIS exists.
    assert effective_min == pytest.approx(5.5)
    basis = out["min_score_basis"]
    assert basis in {"total", "quality_ratio"}
    if basis == "total":
        assert gate_ok is (out["score"] >= effective_min)
        # swing x RANGING is one of only two combinations whose floor exceeds
        # the guaranteed 5.0 gate floor, so it can actually reject here.
        assert out["min_score_floor_binding"] is True
    else:
        min_ratio = config.CONFIG.get("ENGINE_B_MIN_QUALITY_RATIO_BY_STYLE", {}).get(
            "swing", 0.0
        )
        assert gate_ok is (out["quality_pct"] / 100.0 >= min_ratio)
        # The quality-ratio basis can reject at every style/regime combination.
        assert out["min_score_floor_binding"] is True


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
    assert out["volume_bonus"] == pytest.approx(0.0)
    assert out["bonus_points"] == pytest.approx(1.0)


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


def test_component_max_no_longer_double_weights_components():
    """COMPONENT_WEIGHTS sums to 1.0 and is the weight table on its own.

    Multiplying by COMPONENT_MAX applied a second, undeclared weight that ranked
    bos_followthrough (0.16 declared x 1.5 max) above structure_alignment (0.22
    declared) and halved session_context / liquidity_proximity.
    """
    subscores = {"structure_alignment": 1.0, "bos_followthrough": 1.0}
    cfg = {
        "COMPONENT_MAX": {"structure_alignment": 1.0, "bos_followthrough": 1.5},
        "COMPONENT_WEIGHTS": {"structure_alignment": 0.22, "bos_followthrough": 0.16},
        "APPLY_COMPONENT_MAX": False,
    }
    _points, _max, components = aggregate_quality_score(subscores, cfg)
    assert components["structure_alignment"] > components["bos_followthrough"]
    assert components["structure_alignment"] == pytest.approx(0.22)
    assert components["bos_followthrough"] == pytest.approx(0.16)

    legacy = aggregate_quality_score(subscores, {**cfg, "APPLY_COMPONENT_MAX": True})[2]
    assert legacy["bos_followthrough"] > legacy["structure_alignment"]


def test_inapplicable_components_are_pruned_not_scored_zero():
    """An unearnable component must not sit in the quality denominator.

    engine_b_subsystems has no carry/COT weights for stock/index/etf, so
    orderflow was a hard 0 for those classes while still consuming weight; only
    forex/equity rows carry a session payload that can move session_context.
    """
    res = {
        "atr": 1.0,
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "ob_at_zone": False,
        "fvg_overlap": False,
        "active_zone_distance": 0.4,
    }
    stock = compute_confluence_subscores(dict(res), "LONG", 1.0, asset_type="stock")
    assert "volume_confirmation" not in stock
    assert "orderflow" not in stock
    assert "session_context" not in stock

    with_session = compute_confluence_subscores(
        {**res, "equity_session_structure": {"score_influence_enabled": True, "score_bonus": 0.0}},
        "LONG",
        1.0,
        asset_type="stock",
    )
    assert "session_context" in with_session


def test_profile_and_volume_components_follow_source_applicability():
    res = {
        "atr": 1.0,
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "ob_at_zone": False,
        "fvg_overlap": False,
        "active_zone_distance": 0.4,
        "bos_volume_source_applicable": False,
        "profile_context": {"enabled": False, "trusted": False},
    }
    unavailable = compute_confluence_subscores(res, "LONG", 1.0, asset_type="crypto")
    assert "volume_confirmation" not in unavailable
    assert "profile_reaction" not in unavailable

    available = compute_confluence_subscores(
        {
            **res,
            "bos_volume_source_applicable": True,
            "profile_context": {"enabled": True, "trusted": True},
        },
        "LONG",
        1.0,
        asset_type="crypto",
    )
    assert "volume_confirmation" in available
    assert "profile_reaction" in available


def test_opposing_only_order_blocks_score_zero_confluence():
    """The 0.5 "strength unknown" default must not survive the directional filter."""
    res = {
        "atr": 1.0,
        "ob_at_zone": True,
        "order_blocks": [{"type": "bearish"}],
        "fvg_overlap": False,
    }
    opposing = compute_confluence_subscores(dict(res), "LONG", 1.0, asset_type="crypto")
    assert opposing["ob_confluence"] == 0.0

    aligned_no_strength = compute_confluence_subscores(
        {**res, "order_blocks": [{"type": "bullish"}]}, "LONG", 1.0, asset_type="crypto"
    )
    assert aligned_no_strength["ob_confluence"] == 0.5


def test_conviction_norm_uses_quality_not_saturated_total():
    """The total ratio is near-constant on a pass; conviction must not read it."""
    from engine_b_quality import engine_b_conviction_norm

    weak = {"score": 5.05, "max_possible": 6.0, "quality_pct": 5.4}
    strong = {"score": 5.95, "max_possible": 6.0, "quality_pct": 95.0}

    assert engine_b_conviction_norm(weak) == pytest.approx(0.054, abs=1e-3)
    assert engine_b_conviction_norm(strong) == pytest.approx(0.95, abs=1e-3)
    # Total basis compresses the same pair into a ~15-point band.
    total_spread = (5.95 / 6.0) - (5.05 / 6.0)
    quality_spread = engine_b_conviction_norm(strong) - engine_b_conviction_norm(weak)
    assert quality_spread > total_spread * 5


def test_conviction_norm_falls_back_to_total_for_legacy_payloads():
    from engine_b_quality import engine_b_conviction_norm

    assert engine_b_conviction_norm({"score": 3.0, "max_possible": 6.0}) == pytest.approx(0.5)
    assert engine_b_conviction_norm(None) == 0.0
