"""Swing sequence must not alone pass Engine B structure direction (default)."""

from __future__ import annotations

import config
from market_structure import NakedEngine


def _res(**overrides):
    base = {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": False,
        "bos_mtf_confirmed": False,
        "bos_data": {"bos_bull": False, "bos_bear": False},
        "liquidity_sweep": False,
        "choch_confirmed": False,
        "zone_touched": True,
        "near_active_zone": True,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "ob_at_zone": False,
        "breaker_block": False,
        "distance_to_res": 5.0,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
    }
    base.update(overrides)
    return base


def _style():
    return {
        "style": "intraday",
        "min_score": 3.0,
        "min_room_atr": 0.35,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
        "entry_tf": "M15",
    }


def test_hh_hl_alone_does_not_pass_structure_ok(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(_res(), 100.0, "LONG", style_profile=_style())
    assert out["structure_ok"] is False
    assert out["hard_counter_active"] is False


def test_sweep_alone_does_not_pass_structure_without_sequence(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _res(liquidity_sweep=True, sweep_direction="LONG"),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is False


def test_bos_confirmed_still_passes_structure(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _res(bos_confirmed=True, bos_data={"bos_bull": True, "bos_bear": False}),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is True


def test_legacy_sequence_can_be_restored(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(_res(), 100.0, "LONG", style_profile=_style())
    assert out["structure_ok"] is True


def test_independent_direction_ignores_swings_without_bos(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    out = NakedEngine()._determine_independent_direction(
        "HH_HL",
        "HH_HL",
        {"bos_bull": False, "bos_bear": False},
        {},
        {},
        {},
    )
    # No BOS/CHoCH/sweep → no structural opinion from lagging HH_HL alone.
    assert out["direction"] is None
    assert out["votes"]["h1_swing"] == 0
    assert out["votes"]["h4_swing"] == 0
