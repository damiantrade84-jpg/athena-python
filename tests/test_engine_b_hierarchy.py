"""Engine B top-down ICT/SMC hierarchy (HTF bias -> MTF confirmation -> LTF entry).

Covers the pure engine_b_hierarchy evaluators plus the calculate_confidence
wiring for bias_mode legacy / hierarchical / strict.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from engine_b_hierarchy import (
    DEFAULT_CONFIG,
    HIERARCHY_VERSION,
    evaluate_directional_hierarchy,
    evaluate_htf_bias,
    normalize_bias_mode,
    resample_d1_to_w1,
)
from market_structure import NakedEngine


# ── Fixtures / builders ───────────────────────────────────────────────────────


def _bull_sweep():
    return {"bull_sweep": True, "bear_sweep": False, "sweep_low": 95.0, "sweep_high": None}


def _bear_sweep():
    return {"bull_sweep": False, "bear_sweep": True, "sweep_low": None, "sweep_high": 105.0}


def _bull_bos():
    return {"bos_bull": True, "bos_bear": False, "last_broken_high": 102.0}


def _bear_bos():
    return {"bos_bull": False, "bos_bear": True, "last_broken_low": 98.0}


def _fvg(side="bullish", disp=1.2, mitigated=False, ce=99.5):
    return {
        "type": side,
        "mitigated": mitigated,
        "displacement_body_atr": disp,
        "ce": ce,
        "top": ce + 0.5,
        "bottom": ce - 0.5,
    }


def _ob(side="bullish", mitigated=False, strength=75):
    return {
        "type": side,
        "mitigated": mitigated,
        "strength": strength,
        "displacement": 1.4,
        "top": 100.0,
        "bottom": 99.0,
    }


def _valid_long_bias():
    return evaluate_htf_bias(
        style="intraday",
        d1_sweep=_bull_sweep(),
        d1_bos=_bull_bos(),
        d1_fvgs=[_fvg("bullish")],
        current_price=100.0,
        d1_atr=1.0,
    )


# ── evaluate_htf_bias ─────────────────────────────────────────────────────────


def test_htf_bias_full_bull_narrative_is_valid_long():
    bias = _valid_long_bias()
    assert bias["state"] == "valid"
    assert bias["direction"] == "LONG"
    assert bias["strength"] == pytest.approx(1.0)
    names = {e["name"] for e in bias["evidence"]}
    assert {"d1_liquidity_sweep", "d1_displacement_bos", "d1_fvg_narrative"} <= names
    assert bias["bull_score"] >= DEFAULT_CONFIG["MIN_BIAS_SCORE"]


def test_htf_bias_no_evidence_is_unclear():
    bias = evaluate_htf_bias(style="intraday")
    assert bias["state"] == "unclear"
    assert bias["direction"] is None
    assert bias["strength"] == 0.0
    assert bias["evidence"] == []


def test_htf_bias_sequence_alone_never_establishes_bias():
    # Swing sequence (HH_HL) is supporting evidence only — a bare sequence read
    # must stay below MIN_BIAS_SCORE (mirrors the HH_HL-not-direction discipline).
    bias = evaluate_htf_bias(style="intraday", d1_sequence={"state": "HH_HL"})
    assert bias["state"] == "unclear"
    assert bias["direction"] is None
    assert bias["bull_score"] == pytest.approx(DEFAULT_CONFIG["WEIGHT_SEQUENCE"])


def test_htf_bias_both_sides_is_conflicting():
    bias = evaluate_htf_bias(
        style="intraday", d1_sweep={"bull_sweep": True, "bear_sweep": True}
    )
    assert bias["bull_score"] == pytest.approx(3.0)
    assert bias["bear_score"] == pytest.approx(3.0)
    assert bias["state"] == "conflicting"
    assert bias["direction"] is None


def test_htf_bias_dominant_bear_narrative_is_valid_short():
    bias = evaluate_htf_bias(
        style="intraday",
        d1_sweep=_bear_sweep(),
        d1_bos=_bear_bos(),
        d1_order_blocks=[_ob("bearish")],
    )
    assert bias["state"] == "valid"
    assert bias["direction"] == "SHORT"


def test_htf_bias_fvg_without_displacement_does_not_count():
    bias = evaluate_htf_bias(
        style="intraday", d1_fvgs=[_fvg("bullish", disp=0.3)]
    )
    assert bias["state"] == "unclear"
    assert all(e["name"] != "d1_fvg_narrative" for e in bias["evidence"])


def test_htf_bias_mitigated_arrays_do_not_count():
    bias = evaluate_htf_bias(
        style="intraday",
        d1_fvgs=[_fvg("bullish", mitigated=True)],
        d1_order_blocks=[_ob("bullish", mitigated=True)],
    )
    assert bias["state"] == "unclear"
    assert bias["evidence"] == []


def test_htf_bias_weekly_only_applies_to_swing_style():
    # Daily OB alone (1.5) is below MIN_BIAS_SCORE; weekly alignment (2.0) tips
    # swing mode over the floor. Intraday must ignore weekly evidence.
    kwargs = dict(
        d1_order_blocks=[_ob("bullish")],
        w1_evidence={"sequence": "HH_HL", "bos_bull": False, "bos_bear": False, "bars": 10},
    )
    swing = evaluate_htf_bias(style="swing", **kwargs)
    intraday = evaluate_htf_bias(style="intraday", **kwargs)
    assert swing["state"] == "valid"
    assert swing["direction"] == "LONG"
    assert swing["weekly_used"] is True
    assert intraday["state"] == "unclear"
    assert intraday["weekly_used"] is False


def test_htf_bias_draw_on_liquidity_lists_aligned_arrays():
    bias = _valid_long_bias()
    assert bias["draw_on_liquidity"]
    assert all(d["type"] in ("fvg", "order_block") for d in bias["draw_on_liquidity"])
    assert bias["draw_on_liquidity"][0]["distance_atr"] == pytest.approx(0.5)


def test_htf_bias_partial_config_merges_onto_defaults():
    bias = evaluate_htf_bias(
        style="intraday",
        d1_sequence={"state": "HH_HL"},
        config={"MIN_BIAS_SCORE": 1.0},  # lowered floor: sequence now qualifies
    )
    assert bias["state"] == "valid"
    assert bias["direction"] == "LONG"
    # Untouched keys keep their defaults.
    assert bias["min_bias_strength"] == DEFAULT_CONFIG["MIN_BIAS_STRENGTH"]


# ── evaluate_directional_hierarchy ────────────────────────────────────────────


def test_legacy_mode_is_advisory_only():
    out = evaluate_directional_hierarchy(
        mode="legacy",
        htf_bias=_valid_long_bias(),
        direction="LONG",
        mtf_evidence={"bos": True},
        ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "confirmed"
    assert out["applied"] is False
    assert out["score_multiplier"] == 1.0
    assert out["score_bonus"] == 0.0
    assert out["blocked"] is False


def test_hierarchical_aligned_with_mtf_confirms_and_bonuses():
    out = evaluate_directional_hierarchy(
        mode="hierarchical",
        htf_bias=_valid_long_bias(),
        direction="LONG",
        mtf_evidence={"bos": True},
        ltf_evidence={"trigger": True},
    )
    assert out["applied"] is True
    assert out["decision"] == "confirmed"
    assert out["blocked"] is False
    assert out["score_multiplier"] == 1.0
    assert out["score_bonus"] == pytest.approx(DEFAULT_CONFIG["MTF_CONFIRM_BONUS"])
    assert out["stages"]["htf_bias"]["passed"] is True
    assert out["stages"]["mtf_confirmation"]["passed"] is True


def test_hierarchical_aligned_without_mtf_is_accepted_with_haircut():
    out = evaluate_directional_hierarchy(
        mode="hierarchical",
        htf_bias=_valid_long_bias(),
        direction="LONG",
        mtf_evidence={"bos": False, "choch": False, "sweep": False},
        ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "accepted"
    assert out["blocked"] is False
    assert out["score_multiplier"] == pytest.approx(DEFAULT_CONFIG["ALIGNED_NO_MTF_MULT"])


def test_hierarchical_unclear_bias_reduces_confidence():
    out = evaluate_directional_hierarchy(
        mode="hierarchical",
        htf_bias=None,
        direction="LONG",
        mtf_evidence={"bos": True},
        ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "reduced"
    assert out["blocked"] is False
    assert out["score_multiplier"] == pytest.approx(DEFAULT_CONFIG["UNCLEAR_SCORE_MULT"])
    assert out["stages"]["htf_bias"]["state"] == "unclear"


def test_hierarchical_conflicting_bias_blocks():
    conflict = evaluate_htf_bias(
        style="intraday", d1_sweep={"bull_sweep": True, "bear_sweep": True}
    )
    out = evaluate_directional_hierarchy(
        mode="hierarchical", htf_bias=conflict, direction="LONG",
        mtf_evidence={"bos": True}, ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "blocked"
    assert out["blocked"] is True
    assert "htf_bias_conflicting" in out["block_reasons"]


def test_hierarchical_counter_bias_blocks():
    out = evaluate_directional_hierarchy(
        mode="hierarchical",
        htf_bias=_valid_long_bias(),
        direction="SHORT",
        mtf_evidence={"bos": True},
        ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "blocked"
    assert out["blocked"] is True
    assert "counter_htf_bias" in out["block_reasons"]


def test_hierarchical_counter_bias_penalty_mode():
    out = evaluate_directional_hierarchy(
        mode="hierarchical",
        htf_bias=_valid_long_bias(),
        direction="SHORT",
        mtf_evidence={"bos": True},
        ltf_evidence={"trigger": True},
        config={"COUNTER_BIAS_MODE": "penalty"},
    )
    assert out["decision"] == "reduced"
    assert out["blocked"] is False
    assert out["score_multiplier"] == pytest.approx(DEFAULT_CONFIG["COUNTER_BIAS_SCORE_MULT"])


def test_strict_state_machine_blocks_at_htf_when_unclear():
    out = evaluate_directional_hierarchy(
        mode="strict", htf_bias=None, direction="LONG",
        mtf_evidence={"bos": True}, ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "blocked"
    assert out["block_reasons"] == ["htf_bias_unclear"]


def test_strict_state_machine_blocks_counter_bias_at_htf():
    out = evaluate_directional_hierarchy(
        mode="strict", htf_bias=_valid_long_bias(), direction="SHORT",
        mtf_evidence={"bos": True}, ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "blocked"
    assert "counter_htf_bias" in out["block_reasons"]


def test_strict_state_machine_requires_mtf_confirmation():
    out = evaluate_directional_hierarchy(
        mode="strict", htf_bias=_valid_long_bias(), direction="LONG",
        mtf_evidence={"bos": False, "choch": False, "sweep": False},
        ltf_evidence={"trigger": True},
    )
    assert out["decision"] == "blocked"
    assert out["block_reasons"] == ["mtf_confirmation_missing"]


def test_strict_state_machine_requires_ltf_entry():
    out = evaluate_directional_hierarchy(
        mode="strict", htf_bias=_valid_long_bias(), direction="LONG",
        mtf_evidence={"bos": True},
        ltf_evidence={"trigger": False, "fvg_reaction": False, "sweep_at_zone": False},
    )
    assert out["decision"] == "blocked"
    assert out["block_reasons"] == ["ltf_entry_missing"]


def test_strict_state_machine_full_chain_confirms():
    out = evaluate_directional_hierarchy(
        mode="strict", htf_bias=_valid_long_bias(), direction="LONG",
        mtf_evidence={"choch": True}, ltf_evidence={"fvg_reaction": True},
    )
    assert out["decision"] == "confirmed"
    assert out["blocked"] is False
    assert out["score_bonus"] == pytest.approx(DEFAULT_CONFIG["MTF_CONFIRM_BONUS"])


def test_bias_mode_normalization():
    assert normalize_bias_mode("hierarchical") == "hierarchical"
    assert normalize_bias_mode("STRICT") == "strict"
    assert normalize_bias_mode("bogus") == "legacy"
    assert normalize_bias_mode(None) == "legacy"


# ── resample_d1_to_w1 ─────────────────────────────────────────────────────────


def test_resample_d1_to_w1_aggregates_iso_weeks():
    d1 = [
        # ISO week 2026-W30
        {"time": "2026-07-20T00:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100.5, "vol": 10},
        {"time": "2026-07-21T00:00:00Z", "open": 100.5, "high": 103, "low": 100, "close": 102, "vol": 12},
        {"time": "2026-07-22T00:00:00Z", "open": 102, "high": 102.5, "low": 98, "close": 99, "vol": 8},
        # ISO week 2026-W31
        {"time": "2026-07-27T00:00:00Z", "open": 99, "high": 100, "low": 97, "close": 98, "vol": 5},
        {"time": "2026-07-28T00:00:00Z", "open": 98, "high": 101, "low": 97.5, "close": 100.5, "vol": 7},
    ]
    w1 = resample_d1_to_w1(d1)
    assert len(w1) == 2
    assert w1[0]["open"] == 100
    assert w1[0]["high"] == 103
    assert w1[0]["low"] == 98
    assert w1[0]["close"] == 99
    assert w1[0]["vol"] == 30
    assert w1[1]["open"] == 99
    assert w1[1]["high"] == 101
    assert w1[1]["low"] == 97
    assert w1[1]["close"] == 100.5


def test_resample_d1_to_w1_skips_untimed_candles():
    assert resample_d1_to_w1([{"open": 1, "high": 2, "low": 0.5, "close": 1.5}]) == []
    assert resample_d1_to_w1(None) == []


# ── calculate_confidence wiring ───────────────────────────────────────────────


def _base_res_long():
    """Minimal passing LONG structure result (mirrors test_engine_b_quality_scoring)."""
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
    }


def _profile():
    return {"style": "intraday", "min_score": 0.0, "min_room_atr": 0.35,
            "min_rr": 1.0, "require_macro_align": False}


def _confidence(res, direction="LONG"):
    return NakedEngine().calculate_confidence(
        res, current_price=100.0, direction=direction, style_profile=_profile()
    )


def test_confidence_legacy_mode_leaves_scoring_untouched():
    res = _base_res_long()
    baseline = _confidence(res)
    res_with_bias = _base_res_long()
    res_with_bias["htf_bias"] = _valid_long_bias()
    out = _confidence(res_with_bias)
    assert out["bias_mode"] == "legacy"
    assert out["hierarchy"]["applied"] is False
    assert out["score"] == pytest.approx(baseline["score"])
    assert out["passed"] == baseline["passed"]


def test_confidence_hierarchical_aligned_adds_mtf_bonus(monkeypatch):
    baseline = _confidence(_base_res_long())  # legacy mode baseline
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BIAS_MODE", "hierarchical")
    res = _base_res_long()
    res["htf_bias"] = _valid_long_bias()
    out = _confidence(res)
    assert out["bias_mode"] == "hierarchical"
    assert out["hierarchy"]["decision"] == "confirmed"  # bos_confirmed = MTF evidence
    assert out["score"] == pytest.approx(
        baseline["score"] + DEFAULT_CONFIG["MTF_CONFIRM_BONUS"]
    )
    assert out["passed"] is True


def test_confidence_hierarchical_unclear_bias_cuts_score(monkeypatch):
    baseline = _confidence(_base_res_long())  # legacy mode baseline
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BIAS_MODE", "hierarchical")
    # No htf_bias on res -> Daily narrative unestablished -> reduced.
    out = _confidence(_base_res_long())
    assert out["hierarchy"]["decision"] == "reduced"
    assert out["score"] == pytest.approx(
        baseline["score"] * DEFAULT_CONFIG["UNCLEAR_SCORE_MULT"]
    )
    # Soft mode reduces confidence but does not hard-block the gate stack.
    assert out["passed"] is True


def test_confidence_hierarchical_counter_bias_blocks(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BIAS_MODE", "hierarchical")
    res = _base_res_long()
    res["htf_bias"] = evaluate_htf_bias(
        style="intraday", d1_sweep=_bear_sweep(), d1_bos=_bear_bos()
    )  # valid SHORT Daily narrative vs LONG candidate
    out = _confidence(res)
    assert out["passed"] is False
    assert out["hierarchy"]["decision"] == "blocked"
    assert "counter_htf_bias" in out["failed_gate_names"]
    assert "engine_b_hierarchy_blocked" in out["hard_fail_reasons"]


def test_confidence_strict_mode_blocks_without_htf_bias(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BIAS_MODE", "strict")
    out = _confidence(_base_res_long())  # no htf_bias -> State 1 fails
    assert out["passed"] is False
    assert "htf_bias_unclear" in out["failed_gate_names"]
    assert out["hierarchy"]["blocked"] is True


def test_confidence_strict_mode_full_chain_passes(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BIAS_MODE", "strict")
    res = _base_res_long()
    res["htf_bias"] = _valid_long_bias()
    out = _confidence(res)
    # HTF valid+aligned, MTF bos_confirmed, LTF trigger_ok -> full chain passes.
    assert out["hierarchy"]["decision"] == "confirmed"
    assert out["passed"] is True
    assert out["hierarchy"]["stages"]["ltf_entry"]["passed"] is True


def test_confidence_hierarchy_fields_versioned(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BIAS_MODE", "hierarchical")
    res = _base_res_long()
    res["htf_bias"] = _valid_long_bias()
    out = _confidence(res)
    assert out["hierarchy"]["version"] == HIERARCHY_VERSION
    assert out["htf_bias"]["version"] == HIERARCHY_VERSION
    assert out["hierarchy"]["stages"]["htf_bias"]["direction"] == "LONG"
