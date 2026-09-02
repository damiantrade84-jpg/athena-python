"""Focused regressions for the 2026-09-02 independent Engine B review fixes.

Pure-helper tests only (no athena.py import): BOS state memory + reference
window, multi-bar sweep reclaim, sweep quality on the raid bar, macro-gate
evidence, zone near-side band + S/R flips, breakout location freshness,
trigger-bar location distance, follow-through source, CVD volume grade, and
Engine B's regime-label independence.
"""

from __future__ import annotations

import numpy as np
import pytest

import config
from engine_b_phase2 import _sweep_quality, _volume_grade, build_phase2_context
from engine_b_snapshot import resolve_engine_b_regime_label
from market_structure import (
    NakedEngine,
    _engine_b_macro_alignment_evidence,
    _engine_b_zone_near_side_atr_mult,
)


@pytest.fixture
def engine():
    return NakedEngine()


def _series_with_break(age_after_break: int):
    base = [95, 97, 100, 98, 96, 97, 99, 101, 104, 102, 100, 99, 101, 103, 102, 101, 100, 102, 103, 101]
    hi, lo, cl = [], [], []
    for p in base:
        hi.append(p + 0.5)
        lo.append(p - 0.5)
        cl.append(p)
    hi.append(107.0)
    lo.append(103.0)
    cl.append(106.5)  # break bar: closes above the 104.5 swing high
    post = [107.5, 108.0, 107.0, 106.0, 105.5, 105.2, 105.0, 105.3, 105.6, 105.1, 105.4, 105.2]
    for p in post[:age_after_break]:
        hi.append(p + 0.4)
        lo.append(p - 0.4)
        cl.append(p)
    return np.array(hi), np.array(lo), np.array(cl)


# ── BOS reference window + state memory ───────────────────────────────────────


def test_bos_reference_does_not_drift_after_post_break_peaks_form(engine):
    """Reference = last N swings BEFORE the break bar (filter, then slice)."""
    for age in (1, 3, 6):
        hi, lo, cl = _series_with_break(age)
        bos = engine._detect_bos(hi, lo, 1.2, closes=cl)
        assert bos["bos_bull"] is True
        assert bos["last_broken_high"] == pytest.approx(104.5), age


def test_bos_state_persists_past_lookback_while_level_holds(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_LOOKBACK_BARS", 5)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_STATE_MEMORY_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_STATE_MAX_BARS", 60)
    hi, lo, cl = _series_with_break(10)
    bos = engine._detect_bos(hi, lo, 1.2, closes=cl)
    assert bos["bos_bull"] is True
    assert bos["bos_bull_recent"] is False  # outside the 5-bar event window
    assert bos["bos_state_source"] == "state_memory"
    assert bos["bos_bull_bar_age"] == 10
    assert bos["last_broken_high"] == pytest.approx(104.5)


def test_bos_state_memory_can_be_disabled(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_LOOKBACK_BARS", 5)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_STATE_MEMORY_ENABLED", False)
    hi, lo, cl = _series_with_break(10)
    bos = engine._detect_bos(hi, lo, 1.2, closes=cl)
    assert bos["bos_bull"] is False
    assert bos["bos_state_source"] == "none"


def test_bos_state_forgets_break_once_price_closes_back_through(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_LOOKBACK_BARS", 5)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_STATE_MEMORY_ENABLED", True)
    hi, lo, cl = _series_with_break(8)
    # A confirmed close back below the broken level negates the break.
    cl = cl.copy()
    cl[-2] = 104.0
    lo = lo.copy()
    lo[-2] = 103.6
    bos = engine._detect_bos(hi, lo, 1.2, closes=cl)
    assert bos["bos_bull"] is False


def test_bos_state_memory_surfaces_order_block_for_retest(engine, monkeypatch):
    """The OB detector keys on the BOS flag; state memory keeps it visible."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_LOOKBACK_BARS", 5)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_BOS_STATE_MEMORY_ENABLED", True)
    hi, lo, cl = _series_with_break(10)
    candles = []
    for i, (h, l, c) in enumerate(zip(hi, lo, cl)):
        prev_close = cl[i - 1] if i > 0 else c
        candles.append({"open": float(prev_close), "high": float(h), "low": float(l), "close": float(c), "vol": 100.0})
    bos = engine._detect_bos(hi, lo, 1.2, closes=cl)
    obs = engine._detect_order_blocks(candles, bos, 1.2, structure_tf="H4")
    assert bos["bos_bull"] is True
    assert obs and obs[0]["type"] == "bullish"


# ── Sweep detection ───────────────────────────────────────────────────────────


def _sweep_series(reclaim_on_next_bar: bool):
    highs = [101.0] * 20
    lows = [99.0] * 20
    closes = [100.0] * 20
    # swept level = prior swing low at 99.0 (passed explicitly)
    # raid bar 4 bars back: wick to 98.0
    lows[-4] = 98.0
    if reclaim_on_next_bar:
        closes[-4] = 98.8  # closes below the level
        closes[-3] = 99.6  # next bar reclaims
    else:
        closes[-4] = 99.5  # same-bar reclaim
    return np.array(highs), np.array(lows), np.array(closes)


def test_sweep_same_bar_reclaim_reports_raid_bar_index(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWEEP_LOOKBACK_BARS", 8)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWEEP_RECLAIM_BARS", 2)
    hi, lo, cl = _sweep_series(reclaim_on_next_bar=False)
    out = engine._detect_sweep(hi, lo, cl, 1.0, swing_high=101.0, swing_low=99.0)
    assert out["bull_sweep"] is True
    assert out["sweep_bar_index"] == len(cl) - 4
    assert out["sweep_reclaim_bar_index"] == len(cl) - 4
    assert out["swept_level"] == pytest.approx(99.0)


def test_sweep_two_bar_reclaim_is_detected(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWEEP_LOOKBACK_BARS", 8)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWEEP_RECLAIM_BARS", 2)
    hi, lo, cl = _sweep_series(reclaim_on_next_bar=True)
    out = engine._detect_sweep(hi, lo, cl, 1.0, swing_high=101.0, swing_low=99.0)
    assert out["bull_sweep"] is True
    assert out["sweep_bar_index"] == len(cl) - 4
    assert out["sweep_reclaim_bar_index"] == len(cl) - 3


def test_sweep_two_bar_reclaim_disabled_with_legacy_setting(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWEEP_LOOKBACK_BARS", 8)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWEEP_RECLAIM_BARS", 1)
    hi, lo, cl = _sweep_series(reclaim_on_next_bar=True)
    out = engine._detect_sweep(hi, lo, cl, 1.0, swing_high=101.0, swing_low=99.0)
    assert out["bull_sweep"] is False


# ── Sweep quality reads the raid bar ──────────────────────────────────────────


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_sweep_quality_grades_raid_bar_not_newest_bar():
    cands = [_bar(101, 101.3, 100.7, 101.0)] * 8 + [_bar(100.9, 101.0, 98.6, 100.8)] + [
        _bar(100.8, 101.1, 100.6, 100.9)
    ] * 4
    raid_idx = 8
    graded = _sweep_quality(
        cands, "LONG", 1.5, True, "high", False, direction_aligned=True,
        sweep_bar_index=raid_idx, reclaim_bar_index=raid_idx, swept_level=100.7,
    )
    legacy = _sweep_quality(cands, "LONG", 1.5, True, "high", False, direction_aligned=True)
    assert graded["sweep_bar_index"] == raid_idx
    assert graded["sweep_bar_age"] == 4
    assert graded["wick_atr"] == pytest.approx((100.7 - 98.6) / 1.5)
    assert graded["wick_atr"] > legacy["wick_atr"]
    # Liquidity resting at the level: the 8 equal lows BEFORE the raid.
    assert graded["tests"] == 8
    assert graded["score"] > legacy["score"]


def test_phase2_context_uses_structure_series_for_sweep():
    struct = [_bar(101, 101.3, 100.7, 101.0)] * 8 + [_bar(100.9, 101.0, 98.6, 100.8)] + [
        _bar(100.8, 101.1, 100.6, 100.9)
    ] * 4
    trigger = [_bar(100.9, 101.0, 100.8, 100.9)] * 30
    res = {
        "bos_data": {},
        "liquidity_sweep": True,
        "sweep_direction": "LONG",
        "sweep_data": {"sweep_bar_index": 8, "sweep_reclaim_bar_index": 8, "swept_level": 100.7},
    }
    ctx = build_phase2_context(
        res=res, candles=trigger, direction="LONG", atr=1.5, active_zone=None,
        structure_candles=struct, structure_atr=1.5,
    )
    assert ctx["sweep_quality"]["sweep_bar_index"] == 8
    assert ctx["sweep_quality"]["wick_atr"] == pytest.approx((100.7 - 98.6) / 1.5)


def test_volume_grade_resolves_cvd_alignment_from_direction():
    res = {"bos_data": {"bos_volume_available": False}, "aggtrade_cvd_direction": "LONG"}
    strong = _volume_grade(res, asset_type="crypto", aggtrade_available=True, direction="LONG")
    moderate = _volume_grade(res, asset_type="crypto", aggtrade_available=True, direction="SHORT")
    assert strong["grade"] == "strong"
    assert moderate["grade"] == "moderate"


# ── require_macro_align evidence ──────────────────────────────────────────────


def _macro_res(**overrides):
    base = {
        "macro_swing_sequence": "HH_HL",
        "macro_swing_sequence_age": 2,
        "d1_bos_data": {"bos_bull": False, "bos_bear": False},
        "htf_bias": {"state": "unclear", "direction": None},
    }
    base.update(overrides)
    return base


def test_macro_alignment_fresh_sequence_counts(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_ALIGN_EVIDENCE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_FRESH_BARS", 10)
    assert _engine_b_macro_alignment_evidence(_macro_res(), "LONG") is True
    assert _engine_b_macro_alignment_evidence(_macro_res(), "SHORT") is False


def test_macro_alignment_stale_or_unmeasured_sequence_does_not_count(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_ALIGN_EVIDENCE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_FRESH_BARS", 10)
    assert _engine_b_macro_alignment_evidence(_macro_res(macro_swing_sequence_age=25), "LONG") is False
    assert _engine_b_macro_alignment_evidence(_macro_res(macro_swing_sequence_age=None), "LONG") is False


def test_macro_alignment_d1_bos_and_htf_bias_count(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_ALIGN_EVIDENCE_ENABLED", True)
    res = _macro_res(macro_swing_sequence="RANGING", d1_bos_data={"bos_bull": True, "bos_bear": False})
    assert _engine_b_macro_alignment_evidence(res, "LONG") is True
    res = _macro_res(macro_swing_sequence="RANGING", htf_bias={"state": "valid", "direction": "SHORT"})
    assert _engine_b_macro_alignment_evidence(res, "SHORT") is True


def test_macro_alignment_denied_by_exclusive_opposing_d1_bos(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_ALIGN_EVIDENCE_ENABLED", True)
    res = _macro_res(d1_bos_data={"bos_bull": False, "bos_bear": True})
    assert _engine_b_macro_alignment_evidence(res, "LONG") is False


def test_macro_alignment_flag_off_restores_legacy_false(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_ALIGN_EVIDENCE_ENABLED", False)
    assert _engine_b_macro_alignment_evidence(_macro_res(), "LONG") is False


def _passing_res():
    return {
        "atr": 1.0, "trigger_atr": 0.4, "asset_type": "commodity", "current_price": 100.0,
        "current_swing_sequence": "HH_HL", "macro_swing_sequence": "HH_HL",
        "current_swing_sequence_age": 2, "macro_swing_sequence_age": 2,
        "structure_tf": "D1", "macro_sequence_tf": "D1",
        "bos_confirmed": True, "bos_recent": True,
        "bos_data": {"bos_bull": True, "bos_bear": False, "last_broken_high": 99.0},
        "d1_bos_data": {"bos_bull": True, "bos_bear": False},
        "bos_mtf_confirmed": False, "choch_confirmed": False, "liquidity_sweep": False,
        "zone_touched": True, "near_active_zone": True, "active_zone_distance": 0.0,
        "ob_at_zone": False, "fvg_overlap": False,
        "trigger_ok": True, "trigger_pattern": "REJECTION", "trigger_timeframe": "H1",
        "strong_close": True, "engulfing_candle": False, "inside_break_candle": False,
        "recommended_stop_loss": 98.0, "recommended_take_profit": 106.0,
        "nearest_resistance_zone": {"lower": 106.5, "upper": 107.5, "center": 107.0},
        "nearest_support_zone": {"lower": 97.5, "upper": 99.5, "center": 98.0},
        "distance_to_res": 6.5, "distance_to_sup": 0.5,
        "structural_verdict": "CLEAR", "bos_volume_source_applicable": False,
        "phase2_quality": {"version": "x"}, "invalidation_context": {},
        "htf_bias": {"state": "unclear"},
    }


def test_commodity_swing_macro_gate_can_pass_with_aligned_bias_evidence(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_ALIGN_EVIDENCE_ENABLED", True)
    prof = {
        "style": "swing", "entry_tf": "H1", "min_rr": 2.0, "fallback_rr": 2.5,
        "min_room_atr": 1.0, "score_group": "energy_oil",
        "require_macro_align": {"commodity": True},
    }
    conf = engine.calculate_confidence(_passing_res(), 100.0, "LONG", style_profile=prof)
    assert conf["macro_ok"] is True
    assert conf["passed"] is True
    assert "macro" not in conf["failed_gate_names"]


def test_commodity_swing_macro_gate_still_fails_without_bias_evidence(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_ALIGN_EVIDENCE_ENABLED", True)
    res = _passing_res()
    res["macro_swing_sequence"] = "RANGING"
    res["d1_bos_data"] = {"bos_bull": False, "bos_bear": False}
    prof = {
        "style": "swing", "entry_tf": "H1", "min_rr": 2.0, "fallback_rr": 2.5,
        "min_room_atr": 1.0, "score_group": "energy_oil",
        "require_macro_align": {"commodity": True},
    }
    conf = engine.calculate_confidence(res, 100.0, "LONG", style_profile=prof)
    assert conf["macro_ok"] is False
    assert conf["passed"] is False
    assert "macro" in conf["failed_gate_names"]


# ── Breakout location freshness / trigger-bar distance ────────────────────────


def test_breakout_location_requires_fresh_break(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    res = _passing_res()
    res.update({"zone_touched": False, "near_active_zone": False, "active_zone_distance": 5.0,
                "trigger_ok": False, "bos_recent": False})
    prof = {"style": "intraday", "entry_tf": "H1", "min_rr": 1.0, "fallback_rr": 1.8, "min_room_atr": 0.2,
            "allow_breakout_entry": True}
    stale = engine.calculate_confidence(res, 100.0, "LONG", style_profile=prof)
    assert stale["breakout_ok"] is False
    res["bos_recent"] = True
    fresh = engine.calculate_confidence(res, 100.0, "LONG", style_profile=prof)
    assert fresh["breakout_ok"] is True


def test_trigger_at_location_uses_trigger_bar_distance(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TRIGGER_AT_LOCATION_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TRIGGER_AT_LOCATION_MAX_ATR", 1.0)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TRIGGER_AT_LOCATION_USE_TRIGGER_BAR", True)
    res = _passing_res()
    # Price now 0.9 ATR from the zone (passes the current-price term), but the
    # trigger bar fired 3 ATR away.
    res.update({"zone_touched": False, "near_active_zone": False, "active_zone_distance": 0.9,
                "bos_confirmed": False, "bos_recent": False, "bos_data": {},
                "liquidity_sweep": False, "trigger_bar_zone_distance": 3.0})
    prof = {"style": "intraday", "entry_tf": "H1", "min_rr": 1.0, "fallback_rr": 1.8, "min_room_atr": 0.2}
    conf = engine.calculate_confidence(res, 100.0, "LONG", style_profile=prof)
    assert conf["trigger_at_location"] is False
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TRIGGER_AT_LOCATION_USE_TRIGGER_BAR", False)
    legacy = engine.calculate_confidence(res, 100.0, "LONG", style_profile=prof)
    assert legacy["trigger_at_location"] is True


def test_follow_through_prefers_structure_stamp(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOLLOW_THROUGH_SOURCE", "structure")
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_FOLLOW_THROUGH",
        {"ENABLED": True, "DIAGNOSTICS_ENABLED": True, "BLOCK_ENTRY_ON_TRAP": True, "MAX_BONUS": 1.5, "MIN_PENALTY": -0.5},
    )
    res = _passing_res()
    res["structure_follow_through"] = {"score": -0.5, "confidence": "trap", "bars_checked": 3,
                                       "breakout_bar_index": 10, "source": "structure"}
    prof = {"style": "intraday", "entry_tf": "H1", "min_rr": 1.0, "fallback_rr": 1.8, "min_room_atr": 0.2}
    conf = engine.calculate_confidence(res, 100.0, "LONG", style_profile=prof)
    assert conf["follow_through_trap_blocked"] is True
    assert conf["follow_through_detail"]["source"] == "structure"
    assert conf["entry_ok"] is False


# ── Zone geometry ─────────────────────────────────────────────────────────────


def test_zone_near_side_band_config_resolution(monkeypatch):
    buf = {"upper": 0.5, "lower": 1.2}
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_location_band_atr_mult", {"RANGING": 0.5, "default": 0.45})
    assert _engine_b_zone_near_side_atr_mult("RANGING", buf) == pytest.approx(0.5)
    assert _engine_b_zone_near_side_atr_mult("TRENDING", buf) == pytest.approx(0.45)
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_location_band_atr_mult", 0.3)
    assert _engine_b_zone_near_side_atr_mult("RANGING", buf) == pytest.approx(0.3)
    monkeypatch.delitem(config.CONFIG["NAKED_ENGINE"], "zone_location_band_atr_mult")
    assert _engine_b_zone_near_side_atr_mult("RANGING", buf) == pytest.approx(1.2)


def _zone_candles(prices):
    return [{"open": p, "high": p + 0.5, "low": p - 0.5, "close": p, "vol": 100.0} for p in prices]


def test_find_zones_uses_near_side_band_and_stamps_pivot(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_location_band_atr_mult", {"RANGING": 0.5})
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_multipliers", {"RANGING": {"upper": 0.5, "lower": 1.2, "sl": 1.0}})
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ZONE_FLIP_ENABLED", True)
    prices = [100, 101, 102, 103, 104, 110, 104, 103, 102, 101, 100, 101, 102, 103, 104, 105, 104, 103, 102]
    candles = _zone_candles(prices)
    highs = np.array([c["high"] for c in candles])
    lows = np.array([c["low"] for c in candles])
    res_zones, _ = engine._find_zones(highs, lows, 1.0, "RANGING", candles, structure_tf="H4")
    top = max(res_zones, key=lambda z: z["center"])
    assert top["pivot"] == pytest.approx(110.5)
    assert top["upper"] == pytest.approx(111.0)
    assert top["lower"] == pytest.approx(110.0)  # 0.5 ATR band, not 1.2


def test_find_zones_flips_broken_resistance_into_support(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_location_band_atr_mult", {"RANGING": 0.5})
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_multipliers", {"RANGING": {"upper": 0.5, "lower": 1.2, "sl": 1.0}})
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ZONE_FLIP_ENABLED", True)
    # Peak at 110, later accepted above (closes 113+), then holding above 110.
    prices = [100, 102, 104, 106, 110, 106, 104, 103, 104, 106, 108, 113, 114, 113, 112, 111.5, 112, 112.5, 112]
    candles = _zone_candles(prices)
    highs = np.array([c["high"] for c in candles])
    lows = np.array([c["low"] for c in candles])
    res_zones, sup_zones = engine._find_zones(highs, lows, 1.0, "RANGING", candles, structure_tf="H4")
    assert all(z["center"] != pytest.approx(110.5) for z in res_zones)
    flips = [z for z in sup_zones if z.get("is_flip")]
    assert flips and flips[0]["pivot"] == pytest.approx(110.5)
    assert flips[0]["flip_from"] == "resistance"
    assert flips[0]["lower"] == pytest.approx(110.0)
    assert flips[0]["upper"] == pytest.approx(111.0)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ZONE_FLIP_ENABLED", False)
    _, sup_legacy = engine._find_zones(highs, lows, 1.0, "RANGING", candles, structure_tf="H4")
    assert not any(z.get("is_flip") for z in sup_legacy)


def test_find_zones_failed_flip_is_dropped(engine, monkeypatch):
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_location_band_atr_mult", {"RANGING": 0.5})
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "zone_multipliers", {"RANGING": {"upper": 0.5, "lower": 1.2, "sl": 1.0}})
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ZONE_FLIP_ENABLED", True)
    # Broke above 110, then fell back and closed well below it: no flip.
    prices = [100, 102, 104, 106, 110, 106, 104, 103, 104, 106, 108, 113, 114, 111, 108, 106, 105, 106, 105]
    candles = _zone_candles(prices)
    highs = np.array([c["high"] for c in candles])
    lows = np.array([c["low"] for c in candles])
    _, sup_zones = engine._find_zones(highs, lows, 1.0, "RANGING", candles, structure_tf="H4")
    assert not any(z.get("is_flip") and z["pivot"] == pytest.approx(110.5) for z in sup_zones)


# ── Regime label independence ─────────────────────────────────────────────────


def test_engine_b_regime_label_ignores_engine_a_hint_by_default(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_LABEL_INDEPENDENT", True)
    # Too few candles for detect_regime -> RANGING fallback, hint must not win.
    assert resolve_engine_b_regime_label([], "forex", {"label": "TREND_PULLBACK"}) == "RANGING"
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_LABEL_INDEPENDENT", False)
    assert resolve_engine_b_regime_label([], "forex", {"label": "TREND_PULLBACK"}) == "TRENDING"
