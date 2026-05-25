"""Engine B volume-profile asset gating (POC/VAH/VAL trusted asset types)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from ai_review.context_diagnostics import build_context_diagnostics
from athena_ai.price_action_facts import derive_price_action_facts
from athena_ai.strategy_classifier import classify_strategy
from market_structure import (
    NakedEngine,
    build_engine_b_profile_vp_context,
    engine_b_profile_context_enabled,
    sanitize_engine_b_structure_profile_fields,
)


def _base_res(asset_type: str = "forex"):
    return {
        "atr": 1.0,
        "asset_type": asset_type,
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
        "prev_session_profile_valid": True,
        "profile_in_play": True,
        "profile_reaction_strength": 0.8,
        "profile_bias": "bullish",
        "prev_session_poc": 100.5,
        "prev_session_vah": 101.5,
        "prev_session_val": 99.5,
        "profile_notes": "Rejected from previous-session VAH",
    }


def test_engine_b_profile_context_enabled_respects_asset_type(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES",
        ["crypto", "stock"],
    )
    assert engine_b_profile_context_enabled("crypto") is True
    assert engine_b_profile_context_enabled("stock") is True
    assert engine_b_profile_context_enabled("forex") is False
    assert engine_b_profile_context_enabled("index") is False
    assert engine_b_profile_context_enabled("commodity") is False


def test_build_engine_b_profile_vp_context_reason_for_forex(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES",
        ["crypto", "stock"],
    )
    ctx = build_engine_b_profile_vp_context("forex")
    assert ctx["enabled"] is False
    assert ctx["trusted"] is False
    assert ctx["reason"] == "asset_type_untrusted_for_volume_profile"


def test_calculate_confidence_forex_excludes_profile_slot(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES",
        ["crypto", "stock"],
    )
    style = {
        "style": "intraday",
        "min_score": 3.0,
        "min_room_atr": 0.35,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
    }
    forex_out = NakedEngine().calculate_confidence(
        _base_res("forex"),
        current_price=100.0,
        direction="LONG",
        style_profile=style,
    )
    crypto_out = NakedEngine().calculate_confidence(
        _base_res("crypto"),
        current_price=100.0,
        direction="LONG",
        style_profile=style,
    )
    assert forex_out["profile_context"]["enabled"] is False
    assert forex_out["profile_points"] == 0.0
    assert crypto_out["profile_points"] > 0.0
    assert crypto_out["max_possible"] == forex_out["max_possible"] + 1.0


def test_precompute_skips_vp_for_forex(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES",
        ["crypto", "stock"],
    )
    candles = [
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "vol": 1000.0,
            "time": f"2026-01-01T{h:02d}:00:00+00:00",
        }
        for h in range(30)
    ]
    pre = NakedEngine().precompute_structure_data(
        d1_candles=candles,
        h4_candles=candles,
        h1_candles=candles,
        current_price=100.5,
        atr=1.0,
        asset_type="forex",
        enable_profile_context=True,
        pair={"type": "forex", "display": "EURUSD", "symbol": "EURUSD"},
    )
    assert pre.get("enable_profile_context") is False
    assert pre.get("_vp_profile") is None


def test_sanitize_structure_strips_untrusted_profile_fields(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    structure = {
        "prev_session_poc": 100.0,
        "prev_session_vah": 101.0,
        "prev_session_val": 99.0,
        "prev_session_profile_valid": True,
        "profile_in_play": True,
        "profile_notes": "inside value",
    }
    out = sanitize_engine_b_structure_profile_fields(structure, "forex")
    assert out["profile_vp_context"]["enabled"] is False
    assert out["prev_session_poc"] is None
    assert out["prev_session_profile_valid"] is False
    assert out["profile_in_play"] is False
    assert out["profile_notes"] == ""


def test_context_diagnostics_marks_profile_not_applicable_for_forex(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES",
        ["crypto", "stock"],
    )
    ctx = {
        "asset_class": "forex",
        "asset_group": "forex",
        "structure_context": sanitize_engine_b_structure_profile_fields(
            {"prev_session_poc": 100.0}, "forex"
        ),
        "geometry": {"current_price": 100.0},
    }
    diag = build_context_diagnostics(ctx, {"missing_context": []})
    assert diag["resistanceMap"]["profileLevelsTrusted"] is False
    assert diag["resistanceMap"]["profileLevels"]["poc"] is None
    na_labels = diag["contextCompleteness"]["notApplicable"]
    assert any("volume profile" in label.lower() for label in na_labels)


def test_price_action_facts_skip_profile_for_forex(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    structure = sanitize_engine_b_structure_profile_fields(
        {
            "prev_session_poc": 100.0,
            "prev_session_vah": 101.0,
            "prev_session_val": 99.0,
        },
        "forex",
    )
    facts = derive_price_action_facts(
        {"structure_context": structure, "atr": {"atr_value": 1.0}},
        ohlcv_window=[
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100.2, "v": 100}
            for _ in range(8)
        ],
    )
    assert facts["_meta"]["profile_vp_trusted"] is False
    assert facts["profile_location"].get("poc") is None
    assert facts["profile_location"].get("reason") == "missing_close_or_poc"


def test_strategy_classifier_rejects_return_to_poc_for_untrusted_profile(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", True)
    facts = {
        "setup_candidates": {"hints": ["return_to_poc_hint"]},
        "wick_event": {},
        "breakout_state": {},
        "profile_location": {"location": "below_val", "poc": None},
        "profile_vp_context": build_engine_b_profile_vp_context("forex"),
        "volume_behavior": {"state": "normal"},
    }
    out = classify_strategy(
        price_action_facts=facts,
        engine_a_summary={"regime": "RANGING"},
        asset_group="forex",
    )
    rejected_ids = {item["id"] for item in out["rejected_models"]}
    assert "RETURN_TO_POC_MEAN_REVERSION" in rejected_ids
