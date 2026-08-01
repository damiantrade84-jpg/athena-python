"""Tuning Lab (athena_experiment) indicator tests.

Two things get proven here:
1. The new pure indicator math (calc_cci/calc_williams_r/calc_roc/calc_mfi/
   calc_keltner) matches hand-computed reference values.
2. Wiring the new terms into Engine A's momentum/location/volume components and
   Engine B's confluence subscores is default-inert: with no config override,
   scoring output is unchanged whether or not the new snapshot fields are
   present, because every new blend weight defaults to 0.0.
"""

from __future__ import annotations

import pytest

from engine_a_v3.quant_scorer import _location_component, _momentum_component, _volume_component
from engine_b_quality import compute_confluence_subscores
from indicators import calc_cci, calc_keltner, calc_mfi, calc_roc, calc_williams_r


def _candle(high, low, close, vol=10.0):
    return {"high": high, "low": low, "close": close, "vol": vol}


# ── calc_roc ──────────────────────────────────────────────────────────────
def test_calc_roc_hand_computed():
    candles = [_candle(100, 100, 100.0)] * 12 + [_candle(110, 110, 110.0)]
    out = calc_roc(candles, period=12)
    assert out[:12] == [None] * 12
    assert out[12] == pytest.approx(10.0)


# ── calc_williams_r ──────────────────────────────────────────────────────
def test_calc_williams_r_hand_computed():
    candles = [_candle(110, 90, 100.0) for _ in range(13)] + [_candle(110, 90, 105.0)]
    out = calc_williams_r(candles, period=14)
    assert out[12] is None  # window not yet full
    assert out[13] == pytest.approx(-25.0)


# ── calc_cci ──────────────────────────────────────────────────────────────
def test_calc_cci_hand_computed():
    candles = [_candle(100, 100, 100.0) for _ in range(19)] + [_candle(130, 130, 130.0)]
    out = calc_cci(candles, period=20)
    assert out[18] is None
    assert out[19] == pytest.approx(2000.0 / 3.0, rel=1e-6)


# ── calc_mfi ──────────────────────────────────────────────────────────────
def test_calc_mfi_hand_computed():
    # Strictly rising typical price every bar in the window -> all positive
    # flow, zero negative flow -> MFI saturates at 100.
    candles = [_candle(100 + i, 100 + i, 100 + i, vol=10.0) for i in range(15)]
    out = calc_mfi(candles, period=14)
    assert out[13] is None  # n < period + 1 for indices before 14
    assert out[14] == pytest.approx(100.0)


def test_calc_mfi_missing_volume_stays_none():
    candles = [_candle(100 + i, 100 + i, 100 + i, vol=0.0) for i in range(15)]
    out = calc_mfi(candles, period=14)
    assert out[14] is None


# ── calc_keltner ──────────────────────────────────────────────────────────
def test_calc_keltner_bands_symmetric_around_ema():
    candles = [_candle(101 + i, 99 + i, 100 + i) for i in range(30)]
    bands = calc_keltner(candles, ema_period=10, atr_period=10, mult=2.0)
    for i in range(15, 30):
        mid, upper, lower = bands["mid"][i], bands["upper"][i], bands["lower"][i]
        assert mid is not None and upper is not None and lower is not None
        assert upper - mid == pytest.approx(mid - lower, rel=1e-9)
        assert upper > mid > lower


# ── Engine A default-inert guarantees ──────────────────────────────────────
def test_momentum_component_ignores_extra_indicators_by_default():
    base_snap = {
        "rsi": 62.0,
        "plusDI": 24.0,
        "minusDI": 14.0,
        "macdHist": 0.12,
        "macdHistPrev": 0.08,
        "adx": 28.0,
    }
    extra_snap = dict(base_snap, stochK=95.0, cci=250.0, williamsR=-2.0, roc=9.0)
    base_comp, _base_diag = _momentum_component(base_snap, "forex", "forex_majors")
    extra_comp, extra_diag = _momentum_component(extra_snap, "forex", "forex_majors")
    assert extra_comp.signal == pytest.approx(base_comp.signal)
    assert extra_comp.quality == pytest.approx(base_comp.quality)
    # The terms are computed as None/absent in diagnostics since the
    # corresponding *_WEIGHT config defaults to 0.0 (blend loop is skipped).
    assert extra_diag.get("stochTerm") is None
    assert extra_diag.get("cciTerm") is None
    assert extra_diag.get("williamsRTerm") is None
    assert extra_diag.get("rocTerm") is None


def test_location_component_ignores_keltner_by_default():
    base_snap = {"close": 101.0, "ema21": 100.0, "atr": 2.0, "adx": 25.0}
    extra_snap = dict(base_snap, keltnerUpper=110.0, keltnerMid=100.0, keltnerLower=90.0)
    base_comp, base_regime = _location_component(base_snap, "forex", "forex_majors")
    extra_comp, extra_regime = _location_component(extra_snap, "forex", "forex_majors")
    assert extra_regime == base_regime
    assert extra_comp.signal == pytest.approx(base_comp.signal)
    assert extra_comp.quality == pytest.approx(base_comp.quality)


def test_volume_component_ignores_mfi_by_default():
    rows = [
        {"close": 100.0 + i * 0.3, "vol": 100.0 + i} for i in range(20)
    ]
    base_comp = _volume_component({}, rows, None)
    extra_comp = _volume_component({"mfi": 5.0}, rows, None)
    assert extra_comp.signal == pytest.approx(base_comp.signal)
    assert extra_comp.quality == pytest.approx(base_comp.quality)


# ── Engine B default-inert guarantee ───────────────────────────────────────
def test_engine_b_confluence_subscores_new_key_is_neutral_when_uncandled():
    res = {"pair_display": "EURUSD", "asset_type": "forex"}
    subscores = compute_confluence_subscores(res, "LONG", 1.0, asset_type="forex")
    assert subscores["momentum_oscillator_confluence"] == pytest.approx(0.5)


def test_engine_b_confluence_subscores_oscillator_candles_do_not_change_aggregate():
    # ENGINE_B_WEIGHTED_SCORING is enabled in this deployment's config.yaml, and
    # its COMPONENT_WEIGHTS list (the config.yaml override merged over
    # _DEFAULT_COMPONENT_WEIGHTS) has no entry for the new component, so it
    # stays at the code default of 0.0. Passing real oscillator candles must
    # change the raw subscore but cannot move the aggregate quality score.
    from engine_b_quality import (
        _DEFAULT_COMPONENT_WEIGHTS,
        aggregate_quality_score,
        apply_regime_component_weights,
        weighted_scoring_config,
    )

    assert _DEFAULT_COMPONENT_WEIGHTS["momentum_oscillator_confluence"] == 0.0

    res = {"pair_display": "BTCUSDT", "asset_type": "crypto"}
    candles = [_candle(100 + i, 98 + i, 99 + i, vol=10.0) for i in range(30)]

    without = compute_confluence_subscores(res, "LONG", 1.0, asset_type="crypto")
    with_osc = compute_confluence_subscores(
        res, "LONG", 1.0, asset_type="crypto", oscillator_candles=candles
    )
    # A real, non-neutral reading is produced (proves the wiring works)...
    assert with_osc["momentum_oscillator_confluence"] != pytest.approx(0.5)

    cfg = weighted_scoring_config()
    score_without, max_without, _ = aggregate_quality_score(
        apply_regime_component_weights(without, None, "crypto"), cfg
    )
    score_with, max_with, _ = aggregate_quality_score(
        apply_regime_component_weights(with_osc, None, "crypto"), cfg
    )
    # ...but it carries zero configured weight, so it cannot move the aggregate.
    assert score_with == pytest.approx(score_without)
    assert max_with == pytest.approx(max_without)
