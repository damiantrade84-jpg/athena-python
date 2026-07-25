import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from market_structure import (
    NakedEngine,
    _engine_b_regime_gate,
    build_engine_b_profile_vp_context,
    engine_b_forex_asian_session_blocks_bar,
    engine_b_low_volatility_gate,
    engine_b_min_score_threshold,
    resolve_engine_b_execution_levels,
)


def test_engine_b_low_volatility_gate_is_shared_and_configurable():
    series = [10.0] * 50
    passed, detail = engine_b_low_volatility_gate(
        5.9,
        series,
        config_map={
            "ENGINE_B_LOW_VOLATILITY_GATE_ENABLED": True,
            "ENGINE_B_LOW_VOLATILITY_LOOKBACK": 50,
            "ENGINE_B_LOW_VOLATILITY_MIN_ATR_RATIO": 0.6,
        },
    )
    assert passed is False
    assert detail["threshold"] == pytest.approx(6.0)

    disabled, disabled_detail = engine_b_low_volatility_gate(
        1.0,
        series,
        config_map={"ENGINE_B_LOW_VOLATILITY_GATE_ENABLED": False},
    )
    assert disabled is True
    assert disabled_detail["reason"] == "disabled"


def test_engine_b_regime_multipliers_are_not_risk_inverted(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS", {})

    assert _engine_b_regime_gate("TRENDING", "forex") == pytest.approx(0.95)
    assert _engine_b_regime_gate("LOW_VOLATILITY", "forex") == pytest.approx(1.0)
    assert _engine_b_regime_gate("RANGING", "forex") == pytest.approx(1.10)
    assert _engine_b_regime_gate("HIGH_VOLATILITY", "forex") == pytest.approx(1.10)


def test_engine_b_regime_multiplier_is_applied_to_min_score(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_APPLY_TO_MIN_SCORE", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", False)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_REGIME_MULTIPLIERS",
        {"RANGING": 1.10, "UNKNOWN": 1.0},
    )

    assert engine_b_min_score_threshold(
        {"style": "intraday", "min_score": 4.5}, "RANGING", "forex"
    ) == pytest.approx(5.0)


def test_engine_b_style_min_score_differentiation_can_be_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_ENABLED", False)

    assert engine_b_min_score_threshold({"style": "scalp", "min_score": 4.0}, "UNKNOWN", "forex") == 4.0
    assert engine_b_min_score_threshold({"style": "intraday", "min_score": 4.0}, "UNKNOWN", "forex") == 4.5
    assert engine_b_min_score_threshold({"style": "swing", "min_score": 4.0}, "UNKNOWN", "forex") == 5.0


def test_engine_b_min_score_per_group_override_wins_over_style_floor(monkeypatch):
    """C3: when ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED is true AND a
    per-(group,style) min_score override exists in score_group_overrides, the
    override wins over the global style floor (audit MED #6)."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_ENABLED", False)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STYLE_MIN_SCORE_BY_STYLE",
        {"scalp": 4.0, "intraday": 4.5, "swing": 5.0},
    )
    monkeypatch.setitem(
        config.CONFIG,
        "NAKED_ENGINE",
        {
            "score_group_overrides": {
                "nat_gas": {
                    "intraday": {"min_score": 5.5},
                    "swing": {"min_score": 6.0},
                },
            },
        },
    )

    # Per-group override (5.5) wins over style floor (4.5).
    assert engine_b_min_score_threshold(
        {"style": "intraday", "min_score": 4.5, "score_group": "nat_gas"},
        "UNKNOWN",
        "commodity",
    ) == 5.5
    # Per-group override (6.0) wins over style floor (5.0).
    assert engine_b_min_score_threshold(
        {"style": "swing", "min_score": 5.0, "score_group": "nat_gas"},
        "UNKNOWN",
        "commodity",
    ) == 6.0
    # No per-group override for scalp → style floor (4.0) wins.
    assert engine_b_min_score_threshold(
        {"style": "scalp", "min_score": 4.0, "score_group": "nat_gas"},
        "UNKNOWN",
        "commodity",
    ) == 4.0
    # No per-group override for forex_majors → style floor wins.
    assert engine_b_min_score_threshold(
        {"style": "intraday", "min_score": 4.5, "score_group": "forex_majors"},
        "UNKNOWN",
        "forex",
    ) == 4.5


def test_engine_b_crypto_aggtrade_degraded_mode_penalizes_without_failing(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_MODE", "degraded")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_MISSING_PENALTY", 0.5)

    res = {
        "atr": 1.0,
        "asset_type": "crypto",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "liquidity_sweep": False,
        "choch_confirmed": False,
        "zone_touched": True,
        "near_active_zone": False,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "ob_at_zone": True,
        "breaker_block": False,
        "bos_mtf_confirmed": False,
        "distance_to_res": 5.0,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
        "aggtrade_required": True,
        "aggtrade_available": False,
        "aggtrade_reason": "insufficient_trade_buckets",
        "engine_b_data_fidelity": {
            "vp_uses_real_trade_buckets": False,
            "cvd_uses_real_trade_buckets": False,
        },
    }

    out = NakedEngine().calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        style_profile={
            "style": "intraday",
            "min_score": 4.0,
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "fallback_rr": 2.0,
            "require_macro_align": False,
        },
    )

    assert out["passed"] is True
    assert out["aggtrade_required"] is True
    assert out["aggtrade_missing_penalty"] == pytest.approx(0.5)
    assert "aggtrade_required_for_crypto" not in out["failed_gate_names"]


def test_engine_b_rr_space_substitution_keeps_min_atr_floor(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_RR_CAN_SATISFY_SPACE_GATE", {"default": True, "forex": True})
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR", 0.5)

    base = {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "liquidity_sweep": False,
        "choch_confirmed": False,
        "zone_touched": True,
        "near_active_zone": False,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "ob_at_zone": True,
        "breaker_block": False,
        "bos_mtf_confirmed": False,
        "distance_to_res": 0.4,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
    }
    style = {
        "style": "intraday",
        "min_score": 4.0,
        "min_room_atr": 1.0,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
    }

    blocked = NakedEngine().calculate_confidence(base, 100.0, "LONG", style_profile=style)
    allowed = NakedEngine().calculate_confidence({**base, "distance_to_res": 0.5}, 100.0, "LONG", style_profile=style)

    assert blocked["rr_ok"] is True
    assert blocked["space_gate_ok"] is False
    assert allowed["space_gate_ok"] is True


def test_engine_b_space_rr_substitute_profile_override_enables_gate(monkeypatch):
    """A1: space_rr_substitute=True in the per-(group,style) style_profile
    enables the RR-for-room substitute even when the global
    ENGINE_B_RR_CAN_SATISFY_SPACE_GATE flag is False (its default)."""
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_RR_CAN_SATISFY_SPACE_GATE",
        {"default": False, "forex": False, "crypto": False},
    )
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOREX_RR_CAN_SATISFY_SPACE_GATE", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR", 0.5)

    base = {
        "atr": 1.0,
        "asset_type": "crypto",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "liquidity_sweep": False,
        "choch_confirmed": False,
        "zone_touched": True,
        "near_active_zone": False,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "ob_at_zone": True,
        "breaker_block": False,
        "bos_mtf_confirmed": False,
        "distance_to_res": 0.5,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
    }
    style_with_override = {
        "style": "scalp",
        "min_score": 4.0,
        "min_room_atr": 1.0,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
        "space_rr_substitute": True,
    }
    style_without_override = {
        "style": "scalp",
        "min_score": 4.0,
        "min_room_atr": 1.0,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
    }

    with_override = NakedEngine().calculate_confidence(
        base, 100.0, "LONG", style_profile=style_with_override
    )
    without_override = NakedEngine().calculate_confidence(
        base, 100.0, "LONG", style_profile=style_without_override
    )

    assert with_override["rr_ok"] is True
    assert with_override["space_gate_ok"] is True
    assert with_override["rr_space_gate_enabled"] is True
    assert with_override["space_rr_substitute_override_active"] is True
    assert with_override["space_rr_substitute_override_value"] is True

    assert without_override["rr_ok"] is True
    assert without_override["space_gate_ok"] is False
    assert without_override["rr_space_gate_enabled"] is False
    assert without_override["space_rr_substitute_override_active"] is False
    assert without_override["space_rr_substitute_override_value"] is None


def test_engine_b_execution_sl_clamps_to_min_and_max_atr(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ATR_SL_CLAMPS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ENFORCE_MAX_SL_PCT", False)
    monkeypatch.setitem(
        config.CONFIG,
        "STYLE_ATR_MULTS",
        {
            **(config.CONFIG.get("STYLE_ATR_MULTS") or {}),
            "swing": {
                **((config.CONFIG.get("STYLE_ATR_MULTS") or {}).get("swing") or {}),
                "forex": {"sl": 5.0, "tp1": 8.0, "tp2": 12.0},
            },
        },
    )

    tight = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=99.8,
        structural_tp=104.0,
        atr=1.0,
        style="swing",
        asset_class="forex",
        min_rr=2.0,
        fallback_rr=3.0,
    )
    wide = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=104.0,
        atr=1.0,
        style="swing",
        asset_class="forex",
        min_rr=2.0,
        fallback_rr=3.0,
    )

    # `tight` structural levels already clear min_rr (4.0 ATR target over a 0.2
    # ATR stop = RR 20), so the 0.75 minimum leg — which WIDENS — must not undo
    # the _keep_structural_sl decision and cut that RR to 5.3. The 0.35 absolute
    # floor still binds: 0.2 ATR would inflate position size.
    assert tight["stop_distance_atr"] == pytest.approx(0.35)
    assert tight["sl_source"] == "structural_atr_clamp"
    assert tight["atr_sl_clamp_applied"]["min_leg_applied"] is False
    assert tight["atr_sl_clamp_applied"]["effective_min_sl_atr"] == pytest.approx(0.35)
    # The maximum leg is a risk cap and always applies: 5 ATR -> 3 ATR.
    assert wide["atr_sl_clamp_applied"]["after_atr"] == pytest.approx(3.0)
    assert wide["atr_sl_clamp_applied"]["min_leg_applied"] is True
    # The structural TP (4 ATR) then only reaches RR 1.33 against a 3 ATR stop,
    # so the stop tightens to 2 ATR to clear min_rr 2.0 rather than the target
    # being extended to fallback_rr 3.0 (which would have put TP at 109.0).
    assert wide["stop_distance_atr"] == pytest.approx(2.0)
    assert wide["execution_tp"] == pytest.approx(104.0)
    assert wide["tp_source"] == "structural"
    assert wide["rr_used_for_gate"] == pytest.approx(2.0)


def test_engine_b_structural_stop_inside_preferred_min_is_preserved(monkeypatch):
    """A 0.5 ATR structural stop clearing min_rr must survive untouched.

    This is the case the audit measured: the 0.75 minimum leg pushed a 0.30 ATR
    stop with RR 3.33 out to 0.75 ATR, cutting RR to 1.33 and 2.5x-ing risk. The
    tightest, best-located setups were penalised hardest.
    """
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ATR_SL_CLAMPS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ABSOLUTE_MIN_SL_ATR", 0.35)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ENFORCE_MAX_SL_PCT", False)

    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=99.5,      # 0.5 ATR — between the absolute and preferred min
        structural_tp=101.0,     # 1.0 ATR -> structural RR 2.0, clears min_rr 1.3
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.3,
        fallback_rr=1.8,
    )

    assert out["stop_distance_atr"] == pytest.approx(0.5)
    assert out["sl_source"] == "structural"
    assert out["atr_sl_clamp_applied"] is None
    assert out["rr_used_for_gate"] == pytest.approx(2.0)


def test_engine_b_min_sl_clamp_still_widens_when_structure_misses_rr(monkeypatch):
    """The minimum leg keeps protecting stops that structure has NOT justified."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ATR_SL_CLAMPS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ENFORCE_MAX_SL_PCT", False)

    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=99.8,      # 0.2 ATR
        structural_tp=100.2,     # 0.2 ATR -> structural RR 1.0, below min_rr 2.0
        atr=1.0,
        style="swing",
        asset_class="forex",
        min_rr=2.0,
        fallback_rr=3.0,
    )

    assert out["stop_distance_atr"] == pytest.approx(0.75)
    assert out["atr_sl_clamp_applied"]["min_leg_applied"] is True


def test_engine_b_min_sl_clamp_respect_flag_restores_legacy_widening(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ATR_SL_CLAMPS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ENFORCE_MAX_SL_PCT", False)
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_ATR_SL_CLAMP_MIN_RESPECTS_STRUCTURAL", False
    )

    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=99.8,
        structural_tp=104.0,
        atr=1.0,
        style="swing",
        asset_class="forex",
        min_rr=2.0,
        fallback_rr=3.0,
    )

    assert out["stop_distance_atr"] == pytest.approx(0.75)
    assert out["atr_sl_clamp_applied"]["min_leg_applied"] is True


def test_engine_b_profile_trust_uses_score_group_allow_list(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_TRUST_MODE", "score_group")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS", ["crypto_btc", "us_stock_single"])

    assert build_engine_b_profile_vp_context("forex", score_group="crypto_btc")["enabled"] is True
    assert build_engine_b_profile_vp_context("stock", score_group="stock_other")["enabled"] is False


def test_engine_b_forex_session_skip_includes_pre_asian_window(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED", True)

    assert engine_b_forex_asian_session_blocks_bar(
        [{"time": "2026-06-24T23:00:00+00:00"}],
        "forex",
        "EUR/USD",
    ) is True
    assert engine_b_forex_asian_session_blocks_bar(
        [{"time": "2026-06-24T23:00:00+00:00"}],
        "forex",
        "AUD/JPY",
    ) is False


def test_engine_b_asian_active_currency_list_is_config_driven(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ASIAN_ACTIVE_CURRENCIES", ["AUD"])
    candles = [{"time": "2026-06-24T01:00:00+00:00"}]

    assert engine_b_forex_asian_session_blocks_bar(
        candles, "forex", "EUR/JPY"
    ) is True
    assert engine_b_forex_asian_session_blocks_bar(
        candles, "forex", "EUR/AUD"
    ) is False
