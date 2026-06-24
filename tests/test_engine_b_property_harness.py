import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings, strategies as st

import config
from market_structure import (
    _engine_b_regime_gate,
    build_engine_b_profile_vp_context,
    engine_b_min_score_threshold,
    resolve_engine_b_execution_levels,
)


STYLES = ("scalp", "intraday", "swing")
REGIMES = ("TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNKNOWN")
GROUPS = (
    "forex_majors",
    "forex_crosses",
    "forex_exotics",
    "crypto_btc",
    "crypto_eth",
    "crypto_alt_majors",
    "crypto_doge",
    "crypto_other",
    "precious_trackers",
    "energy_oil",
    "nat_gas",
    "us_stock_single",
    "stock_other",
    "smallcap_em_etf",
    "bond_tlt",
    "us_indices_trackers",
    "index_other",
)


@st.composite
def execution_cases(draw):
    entry = draw(st.floats(min_value=10.0, max_value=10000.0, allow_nan=False, allow_infinity=False))
    atr = draw(st.floats(min_value=0.01, max_value=min(100.0, entry * 0.05), allow_nan=False, allow_infinity=False))
    direction = draw(st.sampled_from(("LONG", "SHORT")))
    sl_distance = draw(st.floats(min_value=0.05, max_value=5.0, allow_nan=False, allow_infinity=False)) * atr
    structural_sl = entry - sl_distance if direction == "LONG" else entry + sl_distance

    tp_case = draw(st.sampled_from(("valid", "missing", "wrong_side", "below_min_rr")))
    min_rr = draw(st.floats(min_value=1.0, max_value=3.0, allow_nan=False, allow_infinity=False))
    if tp_case == "valid":
        rr = draw(st.floats(min_value=3.0, max_value=8.0, allow_nan=False, allow_infinity=False))
        structural_tp = entry + sl_distance * rr if direction == "LONG" else entry - sl_distance * rr
    elif tp_case == "below_min_rr":
        rr = draw(st.floats(min_value=0.2, max_value=0.99, allow_nan=False, allow_infinity=False))
        structural_tp = entry + sl_distance * rr if direction == "LONG" else entry - sl_distance * rr
    elif tp_case == "wrong_side":
        structural_tp = entry - sl_distance if direction == "LONG" else entry + sl_distance
    else:
        structural_tp = None

    return {
        "direction": direction,
        "entry": entry,
        "structural_sl": structural_sl,
        "structural_tp": structural_tp,
        "atr": atr,
        "min_rr": min_rr,
    }


@given(st.sampled_from(REGIMES))
@settings(max_examples=100, deadline=None)
def test_engine_b_regime_multiplier_is_bounded(regime):
    multiplier = _engine_b_regime_gate(regime, "forex")
    assert 0.0 <= multiplier <= 2.0


@given(
    style=st.sampled_from(STYLES),
    regime=st.sampled_from(REGIMES),
    min_score=st.floats(min_value=0.0, max_value=20.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_engine_b_scaled_min_score_never_negative(style, regime, min_score):
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(config.CONFIG, "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", False)
        out = engine_b_min_score_threshold({"style": style, "min_score": min_score}, regime, "forex")
    assert out >= 0.0


@given(asset_is_stock=st.booleans(), trusted_group=st.booleans())
@settings(max_examples=50, deadline=None)
def test_engine_b_profile_trust_score_group_mode_only_uses_allow_list(asset_is_stock, trusted_group):
    group = "crypto_btc" if trusted_group else "forex_majors"
    asset_type = "stock" if asset_is_stock else "forex"
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
        mp.setitem(config.CONFIG, "ENGINE_B_PROFILE_TRUST_MODE", "score_group")
        mp.setitem(config.CONFIG, "ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS", ["crypto_btc"])

        assert build_engine_b_profile_vp_context(asset_type, score_group=group)["enabled"] is trusted_group


@given(case=execution_cases(), style=st.sampled_from(STYLES), group=st.sampled_from(GROUPS))
@settings(
    max_examples=250,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_engine_b_execution_levels_invariants(case, style, group):
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(config.CONFIG, "ENGINE_B_ATR_SL_CLAMPS_ENABLED", True)
        mp.setitem(config.CONFIG, "ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75)
        mp.setitem(config.CONFIG, "ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0)
        mp.setitem(config.CONFIG, "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", True)
        mp.setitem(config.CONFIG, "ENGINE_B_ENFORCE_MAX_SL_PCT", False)

        out = resolve_engine_b_execution_levels(
            **case,
            style=style,
            asset_class="forex",
            fallback_rr=3.0,
            score_group=group,
        )
    if not out["execution_levels_valid"]:
        return

    entry = case["entry"]
    sl = out["execution_sl"]
    tp = out["execution_tp"]
    if case["direction"] == "LONG":
        assert sl < entry
        assert tp > entry
    else:
        assert sl > entry
        assert tp < entry

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    assert sl_dist > 0.0
    assert tp_dist / sl_dist >= case["min_rr"] - 1e-6

    sl_in_atr = sl_dist / case["atr"]
    assert sl_in_atr <= 3.0 + 1e-6
    assert sl_in_atr >= 0.75 - 1e-6


def test_engine_b_synthetic_fallback_disabled_rejects_missing_tp(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ENFORCE_MAX_SL_PCT", False)
    monkeypatch.setitem(
        config.CONFIG,
        "STYLE_ATR_MULTS",
        {**(config.CONFIG.get("STYLE_ATR_MULTS") or {}), "swing": {"forex": {}}},
    )
    monkeypatch.setitem(
        config.CONFIG,
        "ATR_CLASS",
        {**(config.CONFIG.get("ATR_CLASS") or {}), "forex": {}},
    )

    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=99.0,
        structural_tp=None,
        atr=1.0,
        style="swing",
        asset_class="forex",
        min_rr=2.0,
        fallback_rr=3.0,
    )

    assert out["execution_levels_valid"] is False
    assert out["execution_level_reject_reason"] in {"levels_missing", "missing_tp_synthesized"}
