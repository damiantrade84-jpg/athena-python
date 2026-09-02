"""Focused tests for the 2026-09-02 Engine A scoring review fixes and the
AUD/JPY Engine B post-mortem fixes. Pure paths only — no broker, no scan."""

from __future__ import annotations

import pytest

from config import CONFIG
from engine_a_v3.quant_scorer import (
    _location_trend_quality_scaling_params,
    _momentum_component,
    _subsystem_max_directional_share,
    _tf_trend,
    _trend_health_mult,
    _trend_structure_only_tfs,
    _volume_component,
    _volume_provenance_diagnostic,
)
from market_structure import (
    _engine_b_invalidate_state_memory_bos,
    resolve_engine_b_execution_levels,
)


# ── Engine A ─────────────────────────────────────────────────────────────────

_MOM_BASE = {"atr": 0.0030, "adx": 28.0, "adxPrev": 27.0, "adxSlope": 0.2}


def test_momentum_opposing_subterm_no_longer_adds_confidence(monkeypatch):
    coherent = {**_MOM_BASE, "rsi": 62, "plusDI": 26, "minusDI": 14, "macdHist": 0.0012, "macdHistPrev": 0.0010}
    conflicted = {**_MOM_BASE, "rsi": 62, "plusDI": 14, "minusDI": 26, "macdHist": 0.0012, "macdHistPrev": 0.0010}
    c_comp, _ = _momentum_component(coherent, "forex", "forex_majors")
    x_comp, x_diag = _momentum_component(conflicted, "forex", "forex_majors")
    assert x_comp.signal < c_comp.signal
    assert x_comp.quality < c_comp.quality
    assert x_diag.get("opposedTerms") == ["di"]
    # Reversible: legacy behaviour credits the opposing term in full.
    blend = dict(CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {})
    blend["ALIGNED_QUALITY_ONLY"] = False
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_MOMENTUM_BLEND", blend)
    legacy_comp, _ = _momentum_component(conflicted, "forex", "forex_majors")
    assert legacy_comp.quality == pytest.approx(c_comp.quality)


def test_structure_only_rungs_follow_the_setup_rung(monkeypatch):
    stack = {"D1": 0.42, "H4": 0.33, "H1": 0.25}
    assert _trend_structure_only_tfs(stack, "H1") == frozenset({"H1"})
    assert _trend_structure_only_tfs(stack, "H4") == frozenset({"H4", "H1"})
    # Equity intraday ladder: setup M30 is faster than every stack rung.
    assert _trend_structure_only_tfs(stack, "M30") == frozenset()
    assert _trend_structure_only_tfs(stack, None) == frozenset()
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_V3_TREND_STACK", {"SETUP_RUNG_STRUCTURE_VOTE_ONLY": False}
    )
    assert _trend_structure_only_tfs(stack, "H1") == frozenset()


def test_structure_only_vote_ignores_close_vs_ema():
    # Pullback: close below the trend EMA while the stack is ordered bullishly.
    snap = {"close": 99.0, "ema21": 100.0, "ema50": 98.0, "ema200": 95.0, "ema200Slope10": 0.5}
    full, _, _ = _tf_trend(snap)
    structure, label, _ = _tf_trend(snap, structure_only=True)
    assert full == pytest.approx(0.5)  # (-1 +1 +1 +1) / 4
    assert structure == pytest.approx(1.0)
    assert label == "UP"


def test_trend_health_adx_slope_reads_momentum_anchor(monkeypatch):
    snaps = {
        "H1": {"adxSlope": -2.0, "adx": 20.0, "adxPrev": 22.0},
        "H4": {"adxSlope": 0.5, "adx": 27.0, "adxPrev": 26.5},
    }
    health = dict(CONFIG.get("ENGINE_A_V3_TREND_HEALTH") or {})
    health["ADX_SLOPE_SOURCE"] = "momentum_anchor"
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_TREND_HEALTH", health)
    assert _trend_health_mult(1.0, snaps, None, None, entry_tf="H1", adx_tf="H4") == pytest.approx(1.0)
    health["ADX_SLOPE_SOURCE"] = "entry"
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_TREND_HEALTH", health)
    assert _trend_health_mult(1.0, snaps, None, None, entry_tf="H1", adx_tf="H4") == pytest.approx(0.75)
    health["ADX_SLOPE_SOURCE"] = "off"
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_TREND_HEALTH", health)
    assert _trend_health_mult(1.0, snaps, None, None, entry_tf="H1", adx_tf="H4") == pytest.approx(1.0)


def test_volume_obv_vote_is_graded_not_binary(monkeypatch):
    # Mostly up bars with modest volume, one heavy down bar: net flow is a
    # fraction of total flow, so the vote is a fraction — not +/-1.
    candles = []
    price = 100.0
    for i in range(24):
        up = i % 4 != 3
        price += 0.5 if up else -0.4
        candles.append({"close": price, "vol": 100.0 if up else 220.0, "volSource": "bybit"})
    prov = _volume_provenance_diagnostic(candles, "crypto_btc", required=True, context=None)
    comp = _volume_component({}, candles, None, "crypto_btc", provenance=prov)
    assert comp.available
    # Graded: the vote is the signed net-flow share, well inside (-1, 1).
    # (Quality may still be lifted by the relative-volume surprise path.)
    assert 0.0 < abs(comp.signal) < 1.0
    blend = dict(CONFIG.get("ENGINE_A_V3_VOLUME_BLEND") or {})
    blend["GRADED_OBV_SIGNAL"] = False
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_VOLUME_BLEND", blend)
    legacy = _volume_component({}, candles, None, "crypto_btc", provenance=prov)
    assert abs(legacy.signal) == pytest.approx(1.0)


def test_location_scaling_and_subsystem_cap_defaults(monkeypatch):
    assert _location_trend_quality_scaling_params() == (True, pytest.approx(0.4))
    assert _subsystem_max_directional_share() == pytest.approx(0.10)
    subs = dict(CONFIG.get("ENGINE_A_V3_SUBSYSTEMS") or {})
    subs["MAX_DIRECTIONAL_SHARE"] = None
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SUBSYSTEMS", subs)
    assert _subsystem_max_directional_share() is None


# ── Engine B ─────────────────────────────────────────────────────────────────

def _state_memory_bull(age: int = 48) -> dict:
    return {
        "bos_bull": True,
        "bos_bear": False,
        "bos_bull_recent": False,
        "bos_bull_bar_age": age,
        "bos_state_source": "state_memory",
    }


def test_state_memory_bos_withdrawn_when_sequence_rolls_over_after_break():
    out = _engine_b_invalidate_state_memory_bos(
        _state_memory_bull(48),
        [("structure", {"state": "LH_LL", "last_swing_age": 6}), ("bias", {"state": "LH_LL", "last_swing_age": 6})],
    )
    assert out["bos_bull"] is False
    assert out["bos_state_source"] == "state_memory_invalidated"
    assert out["bos_state_invalidated"]["by"] == "opposing_sequence:structure"


def test_state_memory_bos_kept_when_sequence_predates_break_or_aligned():
    older = _engine_b_invalidate_state_memory_bos(
        _state_memory_bull(10),
        [("structure", {"state": "LH_LL", "last_swing_age": 30})],
    )
    assert older["bos_bull"] is True
    aligned = _engine_b_invalidate_state_memory_bos(
        _state_memory_bull(48),
        [("structure", {"state": "HH_HL", "last_swing_age": 3})],
    )
    assert aligned["bos_bull"] is True
    fresh = dict(_state_memory_bull(2))
    fresh["bos_state_source"] = "lookback"
    kept = _engine_b_invalidate_state_memory_bos(
        fresh, [("structure", {"state": "LH_LL", "last_swing_age": 1})]
    )
    assert kept["bos_bull"] is True


def test_state_memory_invalidation_is_reversible(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_BOS_STATE_SEQUENCE_INVALIDATION", False)
    out = _engine_b_invalidate_state_memory_bos(
        _state_memory_bull(48), [("structure", {"state": "LH_LL", "last_swing_age": 6})]
    )
    assert out["bos_bull"] is True


def test_price_anchored_structural_stop_is_not_structural():
    # AUD/JPY 2026-09-02 geometry: entry 113.69, "structural" SL 0.25 ATR
    # below the live price, structural TP 4 ATR away, H4 ATR 0.256.
    common: dict = dict(
        direction="LONG",
        entry=113.69,
        structural_sl=113.626,
        structural_tp=114.709,
        atr=0.256,
        style="intraday",
        asset_class="forex",
        min_rr=1.43,
        score_group="forex_crosses",
    )
    swing = resolve_engine_b_execution_levels(**common, structural_sl_anchor="swing", trigger_atr=0.0)
    price = resolve_engine_b_execution_levels(**common, structural_sl_anchor="price_below_swing", trigger_atr=0.0)
    assert price["structural_sl_valid"] is False
    assert price["sl_source"] != "structural"
    assert price["stop_distance_atr"] >= 0.75 - 1e-9
    assert price["stop_distance_atr"] > swing["stop_distance_atr"]


def test_trigger_atr_floor_widens_absolute_minimum_stop():
    common: dict = dict(
        direction="LONG",
        entry=113.69,
        structural_sl=113.626,
        structural_tp=114.709,
        atr=0.256,
        style="intraday",
        asset_class="forex",
        min_rr=1.43,
        score_group="forex_crosses",
        structural_sl_anchor="swing",
    )
    no_floor = resolve_engine_b_execution_levels(**common, trigger_atr=0.0)
    floored = resolve_engine_b_execution_levels(**common, trigger_atr=0.171)
    assert floored["stop_distance_atr"] > no_floor["stop_distance_atr"]
    expected = min(0.75, 1.0 * 0.171 / 0.256)
    assert floored["stop_distance_atr"] == pytest.approx(expected, abs=1e-3)
    assert floored["atr_sl_clamp_applied"]["trigger_floor_atr"] == pytest.approx(expected, abs=1e-3)
