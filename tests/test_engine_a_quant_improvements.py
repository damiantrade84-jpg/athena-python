"""Targeted tests for the 2026-06-09 Engine A quant-review changes.

Covers:
  1. confidence_engine.indicator_agreement no longer counts single-indicator
     factor groups as perfect agreement (bug fix, always on).
  2. Multiplier attribution + correlated penalty product telemetry
     (report-only, always on) and scoring.annotate_multiplier_flips.
  3. ENGINE_A_CORRELATED_PENALTY_GUARD (config-gated, default off).
  4. ENGINE_A_TREND_MAGNITUDE (config-gated, default off).
  5. ENGINE_A_MOMENTUM_SMOOTHING (config-gated, default off).
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from confidence_engine import _DEFAULT_FACTOR_MAP, indicator_agreement
from factor_scoring import (
    _coherent_trend_score,
    _momentum_quality,
    compute_factor_scores,
)
from scoring import annotate_multiplier_flips


def _snap(direction="long", *, adx=30.0, momentum=None, atr=1.0):
    if direction == "long":
        snap = {
            "ema21": 110.0,
            "ema50": 100.0,
            "ema200": 90.0,
            "close": 111.0,
            "atr": atr,
        }
    else:
        snap = {
            "ema21": 90.0,
            "ema50": 100.0,
            "ema200": 110.0,
            "close": 89.0,
            "atr": atr,
        }
    snap["adx"] = adx
    if momentum == "bullish":
        snap.update({"rsi": 60.0, "macdHist": 0.5})
    elif momentum == "bearish":
        snap.update({"rsi": 40.0, "macdHist": -0.5})
    return snap


def _score(h4=None, **kwargs):
    return compute_factor_scores(
        d1_snap=kwargs.pop("d1", None) or _snap("long"),
        h4_snap=h4 if h4 is not None else _snap("long"),
        h1_snap=kwargs.pop("h1", None) or _snap("long"),
        pair=kwargs.pop("pair", None) or {"type": "stock", "display": "TEST"},
        d1_candles=[],
        h4_candles=[],
        h1_candles=[],
        volume_ratio=1.0,
        **kwargs,
    )


# ── 1. indicator_agreement single-group exclusion ────────────────────────────

def test_indicator_agreement_returns_none_for_single_indicator_groups_only():
    # adx_z is alone in trend_strength; previously counted as MAD 0.0
    # ("perfect agreement") and produced a 1.0 component from no information.
    assert indicator_agreement({}, {"adx_z": 0.5}, _DEFAULT_FACTOR_MAP) is None


def test_indicator_agreement_disagreement_not_diluted_by_single_groups():
    disagreeing = {"ema_trend": 1.0, "h4_ema_trend": -1.0}
    base = indicator_agreement({}, disagreeing, _DEFAULT_FACTOR_MAP)
    with_single = indicator_agreement(
        {}, {**disagreeing, "adx_z": 0.5, "volume_ratio": 1.2}, _DEFAULT_FACTOR_MAP
    )
    assert base is not None and base < 0.6  # real disagreement stays low
    # Single-indicator groups no longer drag the mean toward agreement.
    assert with_single == base


# ── 2. Multiplier attribution telemetry ──────────────────────────────────────

def test_multiplier_attribution_reports_di_align_counterfactual():
    h4 = _snap("long", momentum="bullish")
    h4.update({"plusDI": 10.0, "minusDI": 30.0})  # di_diff -20 → severe_opposed
    result = _score(h4=h4)
    attr = result["multiplier_attribution"]
    assert "di_align" in attr
    di = attr["di_align"]
    assert di["multiplier"] < 1.0
    assert di["score_without"] > result["final_score"]
    # Counterfactual consistency: score_without ≈ final_score / multiplier
    # (mean reversion disabled by default → additive term is zero).
    assert math.isclose(
        di["score_without"],
        min(3.0, result["final_score"] / di["multiplier"]),
        rel_tol=0.01,
    )


def test_correlated_penalty_product_always_reported(monkeypatch):
    # Product is telemetry: reported even when the guard itself is disabled.
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_CORRELATED_PENALTY_GUARD", {"ENABLED": False}
    )
    result = _score(h4=_snap("long", momentum="bullish"))
    guard = result["correlated_penalty_guard"]
    assert guard["enabled"] is False
    assert guard["applied"] is False
    assert 0.0 < guard["product"] <= 1.0


def test_annotate_multiplier_flips_marks_threshold_crossers():
    attr = {
        "di_align": {"multiplier": 0.35, "score_without": 2.0},
        "session": {"multiplier": 0.95, "score_without": 1.3},
    }
    annotate_multiplier_flips(attr, final_score=1.2, threshold=1.5)
    assert attr["di_align"]["would_flip_decision"] is True
    assert attr["session"]["would_flip_decision"] is False


# ── 3. Correlated penalty guard (config-gated) ───────────────────────────────

def test_penalty_guard_disabled_is_noop(monkeypatch):
    h4 = _snap("long", momentum="bullish")
    h4.update({"plusDI": 10.0, "minusDI": 30.0})
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_CORRELATED_PENALTY_GUARD", {"ENABLED": False}
    )
    off = _score(h4=h4)
    assert off["correlated_penalty_guard"]["applied"] is False
    assert "correlated_penalty_guard" not in off["feed_status"]


def test_penalty_guard_floors_stacked_penalties_when_enabled(monkeypatch):
    h4 = _snap("long", momentum="bullish")
    h4.update({"plusDI": 10.0, "minusDI": 30.0})  # severe_opposed → mult 0.35
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_CORRELATED_PENALTY_GUARD", {"ENABLED": False}
    )
    off = _score(h4=h4)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_CORRELATED_PENALTY_GUARD",
        {"ENABLED": True, "MIN_COMBINED_MULT": 0.90},
    )
    on = _score(h4=h4)
    assert on["correlated_penalty_guard"]["applied"] is True
    assert on["feed_status"]["correlated_penalty_guard"] == "floored"
    assert on["final_score"] > off["final_score"]
    # Combined penalty floored at 0.90: score scales by floor/product.
    product = off["correlated_penalty_guard"]["product"]
    assert math.isclose(
        on["final_score"], min(3.0, off["final_score"] * 0.90 / product), rel_tol=0.01
    )


# ── 4. Trend magnitude scaling (config-gated) ────────────────────────────────

def _weak_gap_snap():
    # EMAs barely separated relative to ATR.
    return {
        "ema21": 100.05,
        "ema50": 100.0,
        "ema200": 99.95,
        "close": 100.1,
        "atr": 5.0,
        "adx": 30.0,
    }


def test_trend_magnitude_disabled_keeps_sign_only_scoring(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_TREND_MAGNITUDE", {"ENABLED": False})
    weak = _weak_gap_snap()
    strong = _snap("long")
    weak_score, weak_dir, weak_detail = _coherent_trend_score(
        weak, weak, weak, "stock", d1_prev=weak, h4_prev=weak, h1_prev=weak
    )
    strong_score, strong_dir, _ = _coherent_trend_score(
        strong, strong, strong, "stock",
        d1_prev=strong, h4_prev=strong, h1_prev=strong,
    )
    assert weak_dir == strong_dir == "LONG"
    assert math.isclose(weak_score, strong_score, rel_tol=1e-9)
    assert "trend_magnitude_mult" not in weak_detail


def test_trend_magnitude_enabled_scales_weak_gaps_down(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_TREND_MAGNITUDE",
        {"ENABLED": True, "ATR_K": 2.0, "MIN_MULT": 0.25},
    )
    weak = _weak_gap_snap()
    strong = _snap("long")
    weak_score, weak_dir, weak_detail = _coherent_trend_score(
        weak, weak, weak, "stock", d1_prev=weak, h4_prev=weak, h1_prev=weak
    )
    strong_score, _, strong_detail = _coherent_trend_score(
        strong, strong, strong, "stock",
        d1_prev=strong, h4_prev=strong, h1_prev=strong,
    )
    assert weak_dir == "LONG"  # direction unchanged
    assert weak_score < strong_score
    # MIN_MULT floor: a confirmed cross is never zeroed outright.
    assert weak_score >= strong_score / 3.0 * 0.25 * 0.9
    assert 0.25 <= weak_detail["trend_magnitude_mult"] < 0.35
    assert strong_detail["trend_magnitude_mult"] > 0.9


# ── 5. Momentum smoothing (config-gated) ─────────────────────────────────────

def _mom_snap(rsi, macd_hist=0.5, atr=1.0):
    return {"rsi": rsi, "macdHist": macd_hist, "atr": atr}


def test_momentum_smoothing_disabled_preserves_step_values(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_MOMENTUM_SMOOTHING", {"ENABLED": False})
    below = _momentum_quality(_mom_snap(49.9), "LONG", "stock")
    above = _momentum_quality(_mom_snap(50.1), "LONG", "stock")
    assert above - below > 0.15  # plateau jump at the midline persists


def test_momentum_smoothing_removes_rsi_midline_discontinuity(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_MOMENTUM_SMOOTHING",
        {"ENABLED": True, "RSI_RAMP_WIDTH": 2.0, "MACD_NORM_EPS_ATR": 0.05},
    )
    below = _momentum_quality(_mom_snap(49.9), "LONG", "stock")
    above = _momentum_quality(_mom_snap(50.1), "LONG", "stock")
    assert abs(above - below) < 0.05


def test_momentum_smoothing_plateaus_match_step_logic_away_from_edges(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_MOMENTUM_SMOOTHING", {"ENABLED": False})
    step = _momentum_quality(_mom_snap(60.0), "LONG", "stock")
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_MOMENTUM_SMOOTHING",
        {"ENABLED": True, "RSI_RAMP_WIDTH": 2.0, "MACD_NORM_EPS_ATR": 0.05},
    )
    smooth = _momentum_quality(_mom_snap(60.0), "LONG", "stock")
    assert math.isclose(step, smooth, rel_tol=1e-9)


def test_momentum_smoothing_removes_macd_zero_discontinuity(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_MOMENTUM_SMOOTHING", {"ENABLED": False})
    pos_step = _momentum_quality(_mom_snap(60.0, macd_hist=0.001), "LONG", "stock")
    neg_step = _momentum_quality(_mom_snap(60.0, macd_hist=-0.001), "LONG", "stock")
    assert pos_step - neg_step > 0.2  # sign-flip step

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_MOMENTUM_SMOOTHING",
        {"ENABLED": True, "RSI_RAMP_WIDTH": 2.0, "MACD_NORM_EPS_ATR": 0.05},
    )
    pos = _momentum_quality(_mom_snap(60.0, macd_hist=0.001), "LONG", "stock")
    neg = _momentum_quality(_mom_snap(60.0, macd_hist=-0.001), "LONG", "stock")
    assert abs(pos - neg) < 0.05


def test_momentum_smoothing_short_direction_mirrors_long(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_MOMENTUM_SMOOTHING",
        {"ENABLED": True, "RSI_RAMP_WIDTH": 2.0, "MACD_NORM_EPS_ATR": 0.05},
    )
    long_q = _momentum_quality(_mom_snap(60.0, macd_hist=0.5), "LONG", "stock")
    short_q = _momentum_quality(_mom_snap(40.0, macd_hist=-0.5), "SHORT", "stock")
    assert math.isclose(long_q, short_q, rel_tol=1e-9)
