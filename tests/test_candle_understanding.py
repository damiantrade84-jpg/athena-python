"""Tests for diagnostic-first candle understanding layer."""

from __future__ import annotations

import pytest

from athena_ai.candle_understanding import derive_candle_understanding
from athena_ai.price_action_facts import (
    derive_candle_anatomy,
    derive_candle_timeframe_confidence,
    derive_effort_vs_result,
    derive_fvg_state,
    derive_ob_fvg_confluence,
    derive_regime_candle_gate,
    derive_sweep_event,
)
from config import CONFIG


def _bar(o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


# ----- anatomy ----------------------------------------------------------------


def test_anatomy_bullish_long_lower_wick() -> None:
    bars = [_bar(100, 101, 95, 100.5)]
    fact = derive_candle_anatomy(bars)
    last = fact["last"]
    assert last["direction"] == "bullish"
    assert last["lower_wick"] > last["upper_wick"]
    assert last["dominant_wick"] == "lower"
    assert last["is_rejection_shape"] is True


def test_anatomy_bearish_long_upper_wick() -> None:
    bars = [_bar(100, 106, 99.5, 99.8)]
    fact = derive_candle_anatomy(bars)
    last = fact["last"]
    assert last["direction"] == "bearish"
    assert last["dominant_wick"] == "upper"


def test_anatomy_zero_range_does_not_crash() -> None:
    bars = [_bar(100, 100, 100, 100)]
    fact = derive_candle_anatomy(bars)
    assert fact["last"] is not None
    assert fact["last"]["range_size"] >= 1e-12


def test_effort_missing_volume_does_not_crash() -> None:
    bars = [_bar(100, 101, 99, 100, v=0) for _ in range(3)]
    for b in bars:
        del b["volume"]
    fact = derive_effort_vs_result(bars)
    assert "warnings" in fact
    assert fact["confidence"] in ("none", "low", "medium", "high")


# ----- regime gate ------------------------------------------------------------


def test_regime_gate_trending_long_ha_streak() -> None:
    bars = []
    price = 100.0
    for _ in range(8):
        bars.append(_bar(price, price + 2, price, price + 1.5, v=100))
        price += 1.5
    gate = derive_regime_candle_gate(
        bars,
        engine_ctx={"regime": "TRENDING", "adx_value": 30},
        levels={},
        profile_location={"location": "inside_va"},
    )
    assert gate["regime"] == "TRENDING"
    if gate.get("ha_streak") and gate["ha_streak"] >= 3:
        assert gate["allow_long"] is True
        assert gate["allow_short"] is False


def test_regime_gate_ranging_requires_value_edge() -> None:
    gate = derive_regime_candle_gate(
        [_bar(100, 101, 99, 100)],
        engine_ctx={"regime": "RANGING", "adx_value": 18},
        levels={},
        profile_location={"location": "inside_va"},
    )
    assert gate["allow_long"] is False
    assert gate["allow_short"] is False
    assert "ranging_mid_range" in str(gate.get("suppression_reasons"))


def test_regime_gate_missing_adx_returns_unknown_warning() -> None:
    gate = derive_regime_candle_gate([_bar(100, 101, 99, 100)], engine_ctx={}, levels={})
    assert gate["regime"] == "UNKNOWN"
    assert gate["warnings"]


# ----- sweep ------------------------------------------------------------------


def test_sweep_bullish_swing_low_reclaim() -> None:
    bars = [
        _bar(101, 102, 100.5, 101.5),
        _bar(100.5, 101, 98.0, 100.2),  # wick below 99, close above
    ]
    sweep = derive_sweep_event(
        bars,
        swing_levels={"swing_low": 99.0},
        atr=1.0,
    )
    assert sweep["has_sweep"] is True
    assert sweep["side"] == "bullish"
    assert sweep["closed_back_inside"] is True


def test_sweep_acceptance_not_sweep() -> None:
    bars = [
        _bar(100, 101, 99, 100),
        _bar(100, 101, 98, 97),
        _bar(97, 98, 96, 96.5),
    ]
    sweep = derive_sweep_event(
        bars,
        liquidity_pools=[99.0],
        atr=1.0,
    )
    assert sweep.get("has_sweep") is False or sweep.get("bos_conflict") is True


def test_sweep_bearish_swing_high_reclaim() -> None:
    bars = [_bar(100, 105.0, 99.5, 100.5)]
    sweep = derive_sweep_event(bars, liquidity_pools=[104.0], atr=1.0)
    assert sweep["has_sweep"] is True
    assert sweep["side"] == "bearish"


def test_sweep_no_named_pool() -> None:
    bars = [_bar(100, 105, 99, 100.5)]
    sweep = derive_sweep_event(bars, atr=1.0)
    assert sweep["has_sweep"] is False
    assert "no_named_pool" in sweep.get("warnings", [])


def test_sweep_cross_asset_boosts_confidence() -> None:
    bars = [_bar(100.5, 101, 98.0, 100.2)]
    base = derive_sweep_event(bars, swing_levels={"swing_low": 99.0}, atr=1.0)
    boosted = derive_sweep_event(
        bars,
        swing_levels={"swing_low": 99.0},
        atr=1.0,
        correlated_ctx={"bias": "LONG"},
    )
    if base.get("has_sweep") and base.get("confidence") == "medium":
        assert boosted.get("confidence") == "high"


# ----- FVG ---------------------------------------------------------------------


def test_fvg_unfilled() -> None:
    state = derive_fvg_state({"top": 105, "bottom": 100, "type": "bullish"}, 106)
    assert state["fill_state"] == "unfilled"
    assert state["fill_pct"] == 0.0


def test_fvg_partial_fill() -> None:
    state = derive_fvg_state({"top": 105, "bottom": 100, "type": "bullish"}, 102)
    assert state["fill_state"] in ("partial", "ce_touched")
    assert 0 < state["fill_pct"] < 1


def test_fvg_full_fill_decays_not_deleted() -> None:
    state = derive_fvg_state({"top": 105, "bottom": 100, "mitigated": True}, 99)
    assert state["fill_state"] == "full"
    assert state["score_decay"] > 0


# ----- confluence --------------------------------------------------------------


def test_ob_fvg_overlap() -> None:
    out = derive_ob_fvg_confluence(
        [{"id": "ob1", "lower": 99, "upper": 101}],
        [{"id": "fvg1", "bottom": 100, "top": 103}],
    )
    assert out["has_confluence"] is True
    assert out["overlap_range"] is not None


def test_ob_fvg_no_overlap() -> None:
    out = derive_ob_fvg_confluence(
        [{"lower": 90, "upper": 91}],
        [{"bottom": 100, "top": 101}],
    )
    assert out["has_confluence"] is False


def test_ob_fvg_malformed_no_crash() -> None:
    out = derive_ob_fvg_confluence([{"bad": True}], [{"also_bad": True}])
    assert out["has_confluence"] is False


# ----- effort ------------------------------------------------------------------


def test_effort_high_volume_small_body() -> None:
    bars = [_bar(100, 101, 99, 100, v=100) for _ in range(5)]
    bars.append(_bar(100, 100.2, 99.9, 100.05, v=400))
    fact = derive_effort_vs_result(bars)
    assert fact["has_absorption"] is True


def test_effort_normal_volume_no_absorption() -> None:
    bars = [_bar(100, 101, 99, 100.5, v=100) for _ in range(6)]
    fact = derive_effort_vs_result(bars)
    assert fact["has_absorption"] is False


def test_effort_cvd_divergence_improves_confidence() -> None:
    bars = [_bar(100, 101, 99, 100, v=100) for _ in range(5)]
    bars.append(_bar(100, 100.2, 99.9, 100.05, v=400))
    without = derive_effort_vs_result(bars)
    with_cvd = derive_effort_vs_result(bars, cvd={"slope": -0.5})
    if without.get("has_absorption"):
        assert with_cvd.get("cvd_divergence") is True


# ----- timeframe confidence ----------------------------------------------------


def test_tf_confidence_engine_a_m15_discounted() -> None:
    a_m15 = derive_candle_timeframe_confidence("M15", engine="A")
    a_d1 = derive_candle_timeframe_confidence("D1", engine="A")
    assert a_m15["multiplier"] < a_d1["multiplier"]


def test_tf_confidence_engine_d_m5_not_heavily_penalized() -> None:
    d_m5 = derive_candle_timeframe_confidence("M5", engine="D")
    a_m5 = derive_candle_timeframe_confidence("M5", engine="A")
    assert d_m5["multiplier"] > a_m5["multiplier"]


def test_tf_confidence_unknown_tf_warning() -> None:
    out = derive_candle_timeframe_confidence("X99", engine="A")
    assert out["warnings"]
    assert out["multiplier"] is not None


# ----- integration scores ------------------------------------------------------


def test_effective_score_zero_when_live_weight_zero(monkeypatch) -> None:
    monkeypatch.setitem(CONFIG, "CANDLE_UNDERSTANDING_LIVE_WEIGHT", 0.0)
    bars = [_bar(100, 101, 99, 100.5, v=100) for _ in range(6)]
    block = derive_candle_understanding(
        candles=bars,
        engine_ctx={"regime": "TRENDING", "adx_value": 28},
        direction="LONG",
        timeframe="M15",
        engine="A",
    )
    assert block["scores"]["effective_candle_score"] == 0.0
    assert block["scores"]["report_only"] is True


def test_suppressed_regime_still_emits_anatomy(monkeypatch) -> None:
    monkeypatch.setitem(CONFIG, "CANDLE_UNDERSTANDING_LIVE_WEIGHT", 0.0)
    block = derive_candle_understanding(
        candles=[_bar(100, 101, 99, 100)],
        engine_ctx={"regime": "RANGING", "adx_value": 15},
        direction="LONG",
        timeframe="H4",
        engine="A",
    )
    assert block["facts"]["candle_anatomy"]["last"] is not None
    assert block["facts"]["regime_gate"]["allow_long"] is False
