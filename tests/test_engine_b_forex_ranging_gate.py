"""Regression tests for the 2026-07-28 Engine B forex ranging-regime fixes.

Covers audit findings B1/B2:
- the style/regime score floor now binds for forex in RANGING regimes
  (per-asset ENGINE_B_REGIME_MULTIPLIERS override);
- in the ADX-derived RANGING regime, BOS-continuation evidence no longer
  satisfies the structure/location gates for forex;
- direction-aligned sweep (reversal) evidence still carries an entry there,
  and the hard-counter penalty is waived for that fade;
- TRENDING/NORMAL regimes are unaffected.
"""

from __future__ import annotations

import pytest

from config import CONFIG
from market_structure import (
    NakedEngine,
    engine_b_confidence_passes,
    engine_b_min_score_threshold,
)

_BLOCK_FLAG = "ENGINE_B_FOREX_RANGING_CONTINUATION_BLOCK_ENABLED"


def _profile():
    return {
        "style": "intraday",
        "min_score": 4.0,
        "min_room_atr": 0.7,
        "min_rr": 1.3,
        "fallback_rr": 1.8,
        "require_macro_align": False,
        "checklist_mode": "flexible",
        "entry_tf": "M15",
        "score_group": "forex_majors",
    }


def _res(**overrides):
    """Synthetic analyze_structure_direction output for a LONG candidate."""
    res = {
        "atr": 0.001,
        "trigger_atr": 0.001,
        "current_swing_sequence": "LH_LL",
        "macro_swing_sequence": "LH_LL",
        "asset_type": "forex",
        "d1_adx": 15.0,
        "h4_adx": 14.0,
        "bos_confirmed": True,
        "bos_mtf_confirmed": True,
        "choch_confirmed": False,
        "liquidity_sweep": False,
        "sweep_direction": None,
        "zone_touched": False,
        "near_active_zone": False,
        "ob_at_zone": False,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "trigger_ok": True,
        "trigger_timeframe": "M15",
        "m5_conditional_required": False,
        "recommended_stop_loss": 1.0985,
        "recommended_take_profit": 1.1040,
        "distance_to_res": 0.005,
        "distance_to_sup": 0.004,
        "nearest_resistance_zone": None,
        "nearest_support_zone": None,
        "active_zone_distance": None,
        "current_price": 1.1000,
        "pair_display": "EUR/USD",
        "bos_data": {},
        "historical_mode": True,
        "d1_pd_array_conflict": False,
        "prev_session_profile_valid": False,
        "latest_confirmed_trigger_candle_time": "2026-07-27T09:00:00+00:00",
    }
    res.update(overrides)
    return res


@pytest.fixture
def _deterministic(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    monkeypatch.setitem(
        CONFIG, "ENGINE_B_FOLLOW_THROUGH", {"ENABLED": False, "DIAGNOSTICS_ENABLED": False}
    )


def _confidence(res, direction="LONG"):
    return NakedEngine().calculate_confidence(
        res, float(res["current_price"]), direction, style_profile=_profile()
    )


# ── B2: entry-style gate ─────────────────────────────────────────────────────


@pytest.mark.usefixtures("_deterministic")
def test_ranging_forex_continuation_passes_without_flag(monkeypatch):
    monkeypatch.setitem(CONFIG, _BLOCK_FLAG, False)
    conf = _confidence(_res())
    assert conf["passed"] is True  # legacy behaviour: MTF BOS rescues structure


@pytest.mark.usefixtures("_deterministic")
def test_ranging_forex_continuation_blocked(monkeypatch):
    monkeypatch.setitem(CONFIG, _BLOCK_FLAG, True)
    conf = _confidence(_res())
    assert conf["passed"] is False
    assert conf["structure_ok"] is False
    assert conf["location_ok"] is False
    assert (
        "forex_ranging_continuation_blocked"
        in conf["engine_b_diagnostics"]["reason_codes"]
    )


@pytest.mark.usefixtures("_deterministic")
def test_ranging_forex_sweep_fade_allowed_and_penalty_waived(monkeypatch):
    monkeypatch.setitem(CONFIG, _BLOCK_FLAG, True)
    res = _res(
        bos_confirmed=False,
        bos_mtf_confirmed=False,
        strong_close=False,
        liquidity_sweep=True,
        sweep_direction="LONG",
        zone_touched=True,
        ob_at_zone=True,
        nearest_support_zone={"lower": 1.098, "upper": 1.1005, "center": 1.099},
    )
    conf = _confidence(res)
    assert conf["structure_ok"] is True
    assert conf["passed"] is True
    # gate_score 5 + ob bonus 1.0; the -1.0 hard_counter penalty must NOT apply.
    assert conf["gate_score"] == 5.0
    assert conf["score"] == pytest.approx(6.0)
    assert (
        "forex_ranging_continuation_blocked"
        not in conf["engine_b_diagnostics"]["reason_codes"]
    )


@pytest.mark.usefixtures("_deterministic")
def test_trending_forex_continuation_unaffected(monkeypatch):
    monkeypatch.setitem(CONFIG, _BLOCK_FLAG, True)
    conf = _confidence(_res(d1_adx=35.0, h4_adx=32.0))
    assert conf["passed"] is True
    assert conf["structure_ok"] is True


@pytest.mark.usefixtures("_deterministic")
def test_normal_forex_regime_continuation_unaffected(monkeypatch):
    monkeypatch.setitem(CONFIG, _BLOCK_FLAG, True)
    conf = _confidence(_res(d1_adx=25.0, h4_adx=22.0))
    assert conf["passed"] is True


@pytest.mark.usefixtures("_deterministic")
def test_non_forex_ranging_unaffected(monkeypatch):
    monkeypatch.setitem(CONFIG, _BLOCK_FLAG, True)
    conf = _confidence(_res(asset_type="crypto", pair_display="BTC/USDT"))
    assert conf["passed"] is True


# ── B1: binding score floor in RANGING forex ─────────────────────────────────

_RANGING_MULTIPLIERS = {
    "TRENDING": 0.95,
    "RANGING": 1.10,
    "HIGH_VOLATILITY": 1.10,
    "LOW_VOLATILITY": 1.0,
    "forex": {
        "TRENDING": 0.95,
        "RANGING": 1.25,
        "HIGH_VOLATILITY": 1.10,
        "LOW_VOLATILITY": 1.0,
    },
}


@pytest.fixture
def _floor_cfg(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", True)
    monkeypatch.setitem(
        CONFIG, "ENGINE_B_STYLE_MIN_SCORE_BY_STYLE", {"intraday": 4.5, "swing": 5.0}
    )
    monkeypatch.setitem(CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_APPLY_TO_MIN_SCORE", True)
    monkeypatch.setitem(CONFIG, "ENGINE_B_REGIME_MULTIPLIERS", _RANGING_MULTIPLIERS)
    monkeypatch.setitem(CONFIG, "ENGINE_B_MIN_SCORE_BASIS", "total")


@pytest.mark.usefixtures("_floor_cfg")
def test_ranging_floor_binds_for_forex_only():
    profile = {"style": "intraday", "min_score": 4.0}
    # 4.5 x 1.25 = 5.625 -> ceil 5.7 (> 5.0 gate floor: binding)
    assert engine_b_min_score_threshold(profile, "RANGING", "forex") == pytest.approx(5.7)
    # Flat table still governs other classes: 4.5 x 1.10 = 4.95 -> 5.0 (non-binding)
    assert engine_b_min_score_threshold(profile, "RANGING", "crypto") == pytest.approx(5.0)
    # TRENDING forex keeps the flat-table 0.95 multiplier: 4.5 x 0.95 = 4.275 -> 4.3
    assert engine_b_min_score_threshold(profile, "TRENDING", "forex") == pytest.approx(4.3)


@pytest.mark.usefixtures("_floor_cfg")
def test_confidence_passes_rejects_gate_only_score_in_ranging_forex():
    profile = {"style": "intraday", "min_score": 4.0}
    passed, floor = engine_b_confidence_passes(
        {"score": 5.4, "gate_max_possible": 5.0, "passed": True},
        profile,
        "RANGING",
        "forex",
    )
    assert floor == pytest.approx(5.7)
    assert passed is False
    passed_ok, _ = engine_b_confidence_passes(
        {"score": 5.8, "gate_max_possible": 5.0, "passed": True},
        profile,
        "RANGING",
        "forex",
    )
    assert passed_ok is True
