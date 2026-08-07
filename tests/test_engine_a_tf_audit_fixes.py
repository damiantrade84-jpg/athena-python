"""Regression tests for the Engine A timeframe-structure audit fixes.

Covers:
  * trend-layer weights come from the calibrated profile, not the flat
    policy-role table (which inverted every configured D1-led stack)
  * momentum + the ADX gate stay on the configured anchor, not the trigger rung
  * M5 snapshots use intraday indicator periods, not H4-scale ones
  * the setup overlay survives a policy setup rung of H4 (SLOW adaptation)
  * score_pair fails closed when a named entry timeframe has no candles
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from engine_a_v3.quant_scorer import (
    _policy_momentum_anchor_mode,
    _policy_trend_weights_from_profile,
    _policy_trend_weights_from_profile_enabled,
    _resolve_policy_momentum_tf,
    score_pair,
)
from engine_a_v3.routing import route_specialist
from engine_a_v3.timeframes import (
    DIAGNOSTIC_V3_ENTRY_TIMEFRAMES,
    SETUP_PRIMARY_TIMEFRAMES,
    VALID_V3_ENTRY_TIMEFRAMES,
    resolve_setup_context_timeframe,
    resolve_setup_primary_timeframe,
)
from timeframe_policy import (
    LiquidityClass,
    SpeedClass,
    SpeedState,
    resolve_timeframe_policy,
)


def _policy(symbol, asset, group, style, speed_state=None):
    p = resolve_timeframe_policy(
        symbol, asset, group, style, speed_state, engine_id="engine_a"
    )
    return {
        "regime": p.regime_tf.value,
        "bias": p.bias_tf.value,
        "structure": p.structure_tf.value,
        "setup": p.setup_tf.value,
        "trigger": p.trigger_tf.value,
    }


# ── A1: trend weights ────────────────────────────────────────────────────────

def test_defaults_are_profile_weights_and_profile_anchor():
    assert _policy_trend_weights_from_profile_enabled() is True
    assert _policy_momentum_anchor_mode() == "profile"


def test_policy_trend_weights_stay_d1_led_for_majors():
    roles = _policy("EUR/USD", "forex", "forex_majors", "intraday")
    weights, diag = _policy_trend_weights_from_profile(
        roles, "forex_majors", "forex", "intraday"
    )
    # Balanced ladder D1/H4/H4/H1/M15: regime D1, bias+structure H4, setup H1.
    # Distinct regime/structure TFs keep D1-led profile weights without collapse.
    assert weights["D1"] == pytest.approx(0.42)
    assert weights.get("H4", 0.0) + weights.get("H1", 0.0) == pytest.approx(0.58)
    assert weights["D1"] > weights.get("H1", 0.0), "regime rung must not be the lightest"
    assert diag["trendWeightSource"] in {
        "policy_roles_profile_weights",
        "policy_roles_expanded_setup_trigger",
    }


def test_per_group_trend_weights_survive_under_policy():
    """energy_oil deliberately down-weights D1; that must not be flattened."""
    roles = _policy("WTI Oil", "commodity", "energy_oil", "intraday")
    energy, _ = _policy_trend_weights_from_profile(
        roles, "energy_oil", "commodity", "intraday"
    )
    majors, _ = _policy_trend_weights_from_profile(
        _policy("EUR/USD", "forex", "forex_majors", "intraday"),
        "forex_majors",
        "forex",
        "intraday",
    )
    assert energy["D1"] == pytest.approx(0.20)
    assert energy["H4"] == pytest.approx(0.45)
    assert energy != majors, "per-group weights must not collapse to one table"


def test_shared_role_timeframes_sum_and_are_reported():
    """Broad crosses: bias == structure == H4; setup H1 restores H1 weight."""
    roles = _policy("EUR/GBP", "forex", "forex_crosses", "intraday")
    assert roles["bias"] == roles["structure"] == "H4"
    assert roles["setup"] == "H1"
    weights, diag = _policy_trend_weights_from_profile(
        roles, "forex_crosses", "forex", "intraday"
    )
    assert weights["D1"] == pytest.approx(0.42)
    assert diag["trendLayersCollapsed"] == 1
    assert diag.get("trendLayersExpanded") is True
    assert set(weights) == {"D1", "H4", "H1"}
    assert weights["H4"] == pytest.approx(0.33)
    assert weights["H1"] == pytest.approx(0.25)


def test_swing_d1_collapse_expands_onto_setup_trigger():
    """Swing D1×3 regime/bias/structure expands onto H4 setup + H1 trigger."""
    roles = _policy("EUR/USD", "forex", "forex_majors", "swing")
    assert roles["regime"] == roles["bias"] == roles["structure"] == "D1"
    weights, diag = _policy_trend_weights_from_profile(
        roles, "forex_majors", "forex", "swing"
    )
    assert diag["trendLayersCollapsed"] == 2
    assert diag.get("trendLayersExpanded") is True
    assert weights["D1"] == pytest.approx(0.50)
    assert weights["H4"] == pytest.approx(0.30)
    assert weights["H1"] == pytest.approx(0.20)


def test_symbol_aware_role_group_prefers_fast_major_over_score_alias():
    """GBPUSD must not inherit forex_majors_standard-only rows via alias."""
    gbp = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    assert gbp.setup_tf.value == "H1"
    assert gbp.structure_tf.value == "H4"
    assert gbp.m5_policy.value == "conditional"
    eurusd = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    assert eurusd.setup_tf.value == "H1"
    assert eurusd.m5_policy.value == "disabled"


def test_legacy_role_table_is_reachable_for_rollback(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_A_POLICY_TREND_WEIGHTS_FROM_PROFILE", False
    )
    assert _policy_trend_weights_from_profile_enabled() is False


# ── A2: momentum anchor ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "group,asset,trigger",
    [
        ("forex_majors", "forex", "M5"),
        ("crypto_btc", "crypto", "M5"),
        ("us_stock_single", "stock", "M5"),
        ("us_indices_trackers", "index", "M5"),
        ("precious_trackers", "commodity", "M5"),
        ("crypto_alt_majors", "crypto", "M15"),
    ],
)
def test_momentum_anchor_is_not_the_trigger_rung(group, asset, trigger):
    assert _resolve_policy_momentum_tf(group, asset, "intraday", trigger) == "H4"


def test_group_momentum_override_still_wins():
    assert _resolve_policy_momentum_tf("bond_tlt", "stock", "intraday", "M15") == "D1"


def test_momentum_anchor_rollback_restores_trigger(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_MOMENTUM_ANCHOR", "policy_trigger")
    assert _resolve_policy_momentum_tf("forex_majors", "forex", "intraday", "M5") == "M5"


# ── A3: M5 indicator periods ─────────────────────────────────────────────────

def test_m5_periods_match_intraday_not_h4():
    from factor_scoring import (
        ENTRY_TF_PERIOD_OVERRIDE_TFS,
        _base_indicator_periods,
        _resolved_indicator_periods_for_tf,
    )

    assert "M5" in ENTRY_TF_PERIOD_OVERRIDE_TFS
    for group, asset in (
        ("forex_majors", "forex"),
        ("crypto_btc", "crypto"),
        ("us_stock_single", "stock"),
    ):
        base = _base_indicator_periods(group, asset)
        m5 = _resolved_indicator_periods_for_tf(group, asset, "M5")
        assert m5["rsi"] < base["rsi"]
        assert m5["ema_trend"] < base["ema_trend"]


# ── A4/A10: setup overlay timeframe contract ─────────────────────────────────

def test_setup_primary_set_covers_production_and_diagnostic_tfs():
    assert VALID_V3_ENTRY_TIMEFRAMES <= SETUP_PRIMARY_TIMEFRAMES
    assert DIAGNOSTIC_V3_ENTRY_TIMEFRAMES <= SETUP_PRIMARY_TIMEFRAMES
    assert "H4" in SETUP_PRIMARY_TIMEFRAMES
    assert resolve_setup_primary_timeframe("M1") is None


@pytest.mark.parametrize(
    "symbol,asset,group,style",
    [
        ("EUR/USD", "forex", "forex_majors", "swing"),
        ("EUR/GBP", "forex", "forex_crosses", "intraday"),
        ("USD/ZAR", "forex", "forex_exotics", "intraday"),
        ("BTC/USDT", "crypto", "crypto_btc", "swing"),
        ("XPT/USD", "commodity", "pgm_metals", "intraday"),
    ],
)
def test_setup_overlay_survives_slow_speed_adaptation(symbol, asset, group, style):
    slow = SpeedState(
        live_speed_class=SpeedClass.SLOW,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
    )
    setup_tf = _policy(symbol, asset, group, style, slow)["setup"]
    assert resolve_setup_primary_timeframe(setup_tf) == setup_tf


def test_setup_context_timeframe_is_one_rung_slower():
    assert resolve_setup_context_timeframe("H4", "intraday") == "D1"
    assert resolve_setup_context_timeframe("H4", "swing") == "D1"
    assert resolve_setup_context_timeframe("M15", "intraday") == "H4"
    assert resolve_setup_context_timeframe("H1", "swing") == "D1"


def test_h4_setup_returns_frames_instead_of_silently_empty():
    from engine_a_v3.setups import _resolve_setup_candle_frames

    rows = [{"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "vol": 1.0}] * 5
    candles = {"D1": rows, "H4": rows, "H1": rows}
    route = route_specialist({"display": "EUR/USD", "type": "forex"})
    primary, context, primary_tf, context_tf = _resolve_setup_candle_frames(
        route, "swing", candles, entry_tf_override="H4"
    )
    assert primary_tf == "H4"
    assert context_tf == "D1"
    assert primary and context


# ── A9: fail closed on a named-but-missing entry timeframe ───────────────────

def test_score_pair_fails_closed_when_policy_entry_candles_missing():
    route = route_specialist({"display": "EUR/USD", "type": "forex"})
    rows = [
        {
            "time": 1_700_000_000 + i * 3600,
            "open": 1.0,
            "high": 1.01,
            "low": 0.99,
            "close": 1.0,
            "vol": 100.0,
        }
        for i in range(300)
    ]
    # Named setup M30 with no M30 candles must fail closed (not fall back to H1).
    policy = _policy("EUR/USD", "forex", "forex_majors", "intraday")
    policy = {**policy, "setup": "M30", "setupTf": "M30"}
    quant = score_pair(
        route,
        "intraday",
        {"D1": rows, "H4": rows, "H1": rows},  # no M30 series supplied
        policy_timeframes=policy,
    )
    assert quant.factor_diagnostics.get("rejectionReason") == "missing_entry_candles"
    assert quant.direction == "FLAT"
    assert quant.confluence_score == 0.0
