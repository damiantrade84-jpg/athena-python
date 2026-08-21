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
    engine_b_conviction_norm,
    weighted_scoring_enabled,
)


@pytest.mark.parametrize("malformed", [float("nan"), float("inf"), float("-inf"), "bad"])
def test_engine_b_conviction_nonfinite_or_malformed_fails_closed(malformed):
    assert engine_b_conviction_norm({"quality_pct_net": malformed}) == 0.0
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
        # Real reaction-backed FVG context: the fallback (no-context) branch
        # no longer awards half-credit pads, so fixtures must carry the
        # evidence they claim.
        "fvg_context": {
            "direction": "LONG",
            "reaction_confirmed": True,
            "nearest": {
                "gap_size_atr": 0.8,
                "displacement_body_atr": 1.0,
                "fill_fraction": 0.0,
            },
        },
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
    assert bare_bos == pytest.approx(0.90)
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

    assert guarded_sequence == pytest.approx(0.70)
    assert guarded_sweep == pytest.approx(0.80)
    assert directionless_sweep == pytest.approx(0.0)


def test_structure_alignment_previous_rungs_restorable(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    previous = {
        **config.CONFIG.get("ENGINE_B_WEIGHTED_SCORING", {}),
        "STRUCTURE_ALIGNMENT": {
            "bos": 0.85,
            "choch": 0.70,
            "sweep": 0.45,
            "guarded_sequence": 0.55,
            "bos_mtf_bonus": 0.15,
        },
    }
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", previous)
    assert compute_structure_alignment_score({"bos_confirmed": True}, "LONG") == pytest.approx(0.85)
    assert compute_structure_alignment_score(
        {"liquidity_sweep_structure_ok": True}, "LONG"
    ) == pytest.approx(0.45)


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


def test_path_inapplicable_components_leave_the_denominator():
    """A sweep cannot be taxed for missing BOS followthrough, and vice versa."""
    sweep_res = {
        "atr": 1.0,
        "asset_type": "forex",
        "liquidity_sweep": True,
        "sweep_direction": "SHORT",
        "liquidity_sweep_structure_ok": True,
        "bos_confirmed": False,
        "zone_touched": True,
        "active_zone_distance": 0.0,
        "ob_at_zone": False,
        "fvg_overlap": False,
        "forex_session_structure": {"score_influence_enabled": True, "score_bonus": 0.0},
    }
    sweep = compute_confluence_subscores(sweep_res, "SHORT", 1.0, asset_type="forex")
    assert "bos_followthrough" not in sweep
    assert "sweep_quality" in sweep
    assert "bag_continuation" not in sweep
    assert "liquidity_proximity" in sweep

    bos_off_zone = compute_confluence_subscores(
        {
            "atr": 1.0,
            "asset_type": "forex",
            "bos_confirmed": True,
            "liquidity_sweep": False,
            "zone_touched": False,
            "near_active_zone": False,
            "ob_at_zone": False,
            "active_zone_distance": 3.0,
            "forex_session_structure": {"score_influence_enabled": True, "score_bonus": 0.0},
        },
        "LONG",
        1.0,
        asset_type="forex",
    )
    assert "bos_followthrough" in bos_off_zone
    assert "sweep_quality" not in bos_off_zone
    assert "liquidity_proximity" not in bos_off_zone


def test_shipped_weights_keep_structure_first_and_ict_as_bonus():
    """ICT terms matter more, but structure stays the largest single core weight."""
    from engine_b_quality import (
        _DEFAULT_COMPONENT_WEIGHTS,
        _DEFAULT_BONUS_COMPONENTS,
        weighted_scoring_config,
    )

    shipped = weighted_scoring_config().get("COMPONENT_WEIGHTS") or {}
    weights = {**_DEFAULT_COMPONENT_WEIGHTS, **(shipped if isinstance(shipped, dict) else {})}
    core_names = [
        name
        for name in weights
        if name not in _DEFAULT_BONUS_COMPONENTS and float(weights[name] or 0.0) > 0.0
    ]
    assert float(weights["structure_alignment"]) == pytest.approx(0.28)
    assert float(weights["sweep_quality"]) == pytest.approx(0.10)
    assert float(weights["bag_continuation"]) == pytest.approx(0.05)
    assert float(weights["ob_confluence"]) == pytest.approx(0.09)
    assert float(weights["fvg_confluence"]) == pytest.approx(0.07)
    assert max(float(weights[name]) for name in core_names) == pytest.approx(
        float(weights["structure_alignment"])
    )
    assert sum(float(value) for value in weights.values()) == pytest.approx(1.0)
    bonus = weighted_scoring_config().get("BONUS_COMPONENTS")
    assert bonus is None or set(bonus) == {"ob_confluence", "fvg_confluence"}


def test_bonus_components_do_not_enlarge_the_denominator():
    from engine_b_quality import aggregate_quality_score

    cfg = {
        "COMPONENT_WEIGHTS": {
            "structure_alignment": 0.30,
            "ob_confluence": 0.07,
            "fvg_confluence": 0.05,
        },
        "COMPONENT_MAX": {
            "structure_alignment": 1.0,
            "ob_confluence": 1.0,
            "fvg_confluence": 1.0,
        },
        "APPLY_COMPONENT_MAX": False,
        "BONUS_COMPONENTS": ["ob_confluence", "fvg_confluence"],
    }
    points, denom, parts = aggregate_quality_score(
        {"structure_alignment": 1.0, "ob_confluence": 1.0, "fvg_confluence": 0.0},
        cfg,
    )
    assert denom == pytest.approx(0.30)
    assert parts["ob_confluence"] == pytest.approx(0.07)
    assert points == pytest.approx(0.37)


def test_complete_sweep_at_zone_can_reach_high_quality(monkeypatch):
    """A finished sweep-at-zone path must be able to print in the 70-100 band."""
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_WEIGHTED_SCORING",
        {**config.CONFIG.get("ENGINE_B_WEIGHTED_SCORING", {}), "ENABLED": True},
    )
    res = {
        "atr": 1.0,
        "asset_type": "forex",
        "bos_confirmed": False,
        "choch_confirmed": False,
        "liquidity_sweep": True,
        "sweep_direction": "SHORT",
        "liquidity_sweep_structure_ok": True,
        "zone_touched": True,
        "near_active_zone": True,
        "ob_at_zone": False,
        "fvg_overlap": False,
        "order_blocks": [],
        "active_zone_distance": 0.0,
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "current_swing_sequence_age": 11,
        "structure_tf": "H4",
        "macro_sequence_tf": "H4",
        "trigger_ok": True,
        "structural_verdict": "CLEAR",
        "recommended_stop_loss": 1.02,
        "recommended_take_profit": 0.97,
        "distance_to_res": 0.01,
        "distance_to_sup": 0.03,
        "forex_session_structure": {
            "score_influence_enabled": True,
            "score_bonus": 0.06,
            "max_abs_score_bonus": 0.06,
        },
        "phase2_quality": {
            "pullback_quality": {"score": 0.90},
            "sweep_quality": {"score": 0.85},
            "volume_confirmation": {"score": 0.0},
        },
    }
    out = NakedEngine().calculate_confidence(
        res,
        current_price=1.00,
        direction="SHORT",
        style_profile={
            "style": "intraday",
            "min_score": 0.0,
            "min_room_atr": 0.35,
            "min_rr": 1.3,
            "require_macro_align": False,
        },
    )
    assert out["weighted_scoring_enabled"] is True
    assert out["quality_pct"] >= 75.0
    assert out["quality_pct"] <= 100.0
    assert "bos_followthrough" not in out["quality_components"]


def test_stacked_bos_zone_confluence_can_reach_full_quality(monkeypatch):
    """BOS + zone + followthrough + full core must be able to reach 85%+."""
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_WEIGHTED_SCORING",
        {**config.CONFIG.get("ENGINE_B_WEIGHTED_SCORING", {}), "ENABLED": True},
    )
    res = _base_res_long()
    res.update(
        {
            "bos_mtf_confirmed": True,
            "zone_touched": True,
            "near_active_zone": True,
            "active_zone_distance": 0.0,
            "current_swing_sequence_age": 1,
            "structure_tf": "H4",
            "macro_sequence_tf": "D1",
            "forex_session_structure": {
                "score_influence_enabled": True,
                "score_bonus": 0.06,
                "max_abs_score_bonus": 0.06,
            },
            "phase2_quality": {
                "pullback_quality": {"score": 1.0},
                "sweep_quality": {"score": 0.0},
                "volume_confirmation": {"score": 0.0},
            },
        }
    )
    from engine_b_quality import (
        aggregate_quality_score,
        apply_regime_component_weights,
        compute_confluence_subscores,
        weighted_scoring_config_for_group,
    )

    pruned: list[str] = []
    subs = compute_confluence_subscores(
        res,
        "LONG",
        1.0,
        bos_followthrough_norm=1.0,
        asset_type="forex",
        pruned_out=pruned,
    )
    weighted = apply_regime_component_weights(subs, None, "forex")
    points, denom, _parts = aggregate_quality_score(
        weighted, weighted_scoring_config_for_group("forex_majors")
    )
    assert denom > 0
    assert (points / denom) * 100.0 >= 85.0


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


def test_clean_bos_zone_setup_can_reach_half_quality(monkeypatch):
    """A typical BOS + zone-retest must print well above half of quality.

    Path-exclusive terms and bonus-only OB/FVG must not keep a complete
    BOS-at-zone card permanently under 60%.
    """
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_WEIGHTED_SCORING",
        {**config.CONFIG.get("ENGINE_B_WEIGHTED_SCORING", {}), "ENABLED": True},
    )
    res = _base_res_long()
    res.update(
        {
            "bos_mtf_confirmed": False,
            "liquidity_sweep": False,
            "ob_at_zone": False,
            "fvg_overlap": False,
            "order_blocks": [],
            "active_zone_distance": 0.0,
            "phase2_quality": {
                "pullback_quality": {"score": 0.55},
                "sweep_quality": {"score": 0.0},
                "volume_confirmation": {"score": 0.0},
            },
        }
    )
    out = NakedEngine().calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        style_profile={
            "style": "intraday",
            "min_score": 0.0,
            "min_room_atr": 0.35,
            "min_rr": 1.3,
            "require_macro_align": False,
        },
    )
    assert out["weighted_scoring_enabled"] is True
    assert out["quality_pct"] is not None
    assert out["quality_pct"] >= 60.0
    assert out["quality_pct"] < 100.0
    assert "sweep_quality" not in out["quality_components"]
    assert "bag_continuation" not in out["quality_components"]


def test_d1_conflict_does_not_zero_quality_pct(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_WEIGHTED_SCORING",
        {**config.CONFIG.get("ENGINE_B_WEIGHTED_SCORING", {}), "ENABLED": True},
    )
    res = _base_res_long()
    res["d1_pd_array_conflict"] = True
    out = NakedEngine().calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        style_profile={
            "style": "intraday",
            "min_score": 0.0,
            "min_room_atr": 0.35,
            "min_rr": 1.3,
            "require_macro_align": False,
        },
    )
    assert out["quality_pct"] > 25.0
    assert out["quality_penalty_applied"] > 0.0
    assert out["quality_pct"] == pytest.approx(
        out["quality_points_gross"] / out["quality_denominator"] * 100, abs=0.1
    )
    assert out["quality_pct_net"] == pytest.approx(
        out["quality_points_net"] / out["quality_denominator"] * 100, abs=0.1
    )
    assert out["quality_pct_net"] < out["quality_pct"]


def test_crypto_structure_quality_uses_scanned_direction():
    from market_structure import _crypto_structure_quality_score

    bos = {"bos_bull": False, "bos_bear": True, "bos_volume_confirmed": True}
    choch = {"choch_bull": False, "choch_bear": True}
    sweep = {"bull_sweep": False, "bear_sweep": True}
    long_score = _crypto_structure_quality_score(
        bos, choch, sweep, "LH_LL", "LONG", 0.4
    )
    short_score = _crypto_structure_quality_score(
        bos, choch, sweep, "LH_LL", "SHORT", 0.4
    )
    assert short_score > long_score
