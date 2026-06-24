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
    engine_b_min_score_threshold,
    resolve_engine_b_execution_levels,
)


def test_engine_b_regime_multipliers_are_not_risk_inverted(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS", {})

    assert _engine_b_regime_gate("TRENDING", "forex") == pytest.approx(0.95)
    assert _engine_b_regime_gate("LOW_VOLATILITY", "forex") == pytest.approx(0.90)
    assert _engine_b_regime_gate("RANGING", "forex") == pytest.approx(1.10)
    assert _engine_b_regime_gate("HIGH_VOLATILITY", "forex") == pytest.approx(1.10)


def test_engine_b_style_min_score_differentiation_can_be_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_ENABLED", False)

    assert engine_b_min_score_threshold({"style": "scalp", "min_score": 4.0}, "UNKNOWN", "forex") == 4.0
    assert engine_b_min_score_threshold({"style": "intraday", "min_score": 4.0}, "UNKNOWN", "forex") == 4.5
    assert engine_b_min_score_threshold({"style": "swing", "min_score": 4.0}, "UNKNOWN", "forex") == 5.0


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

    assert tight["stop_distance_atr"] == pytest.approx(0.75)
    assert wide["stop_distance_atr"] == pytest.approx(3.0)


def test_engine_b_profile_trust_uses_score_group_allow_list(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_TRUST_MODE", "score_group")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS", ["crypto_btc", "us_stock_single"])

    assert build_engine_b_profile_vp_context("forex", score_group="crypto_btc")["enabled"] is True
    assert build_engine_b_profile_vp_context("stock", score_group="stock_other")["enabled"] is False


def test_engine_b_forex_session_skip_includes_pre_asian_window(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOREX_PRE_ASIAN_SESSION_SKIP_ENABLED", True)

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
