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
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_LIQUIDITY_SWEEP_STRUCTURE_ENABLED", False)
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


def test_guarded_fresh_sequence_passes_without_legacy_global_switch(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FRESH_SEQUENCE_STRUCTURE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_LIQUIDITY_SWEEP_STRUCTURE_ENABLED", False)
    out = NakedEngine().calculate_confidence(
        _res(
            current_swing_sequence_age=2,
            macro_swing_sequence_age=3,
            structure_tf="H4",
            macro_sequence_tf="D1",
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )

    assert out["structure_ok"] is True
    assert out["fresh_sequence_structure_ok"] is True
    assert "fresh_sequence" in out["structure_direction_sources"]


def test_guarded_sequence_fails_closed_when_stale(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FRESH_SEQUENCE_STRUCTURE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_FRESH_BARS", 10)
    out = NakedEngine().calculate_confidence(
        _res(
            current_swing_sequence_age=11,
            macro_swing_sequence_age=2,
            structure_tf="H4",
            macro_sequence_tf="D1",
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )

    assert out["structure_ok"] is False
    assert out["fresh_sequence_structure_ok"] is False


def test_guarded_sequence_does_not_double_require_same_timeframe(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FRESH_SEQUENCE_STRUCTURE_ENABLED", True)
    out = NakedEngine().calculate_confidence(
        _res(
            current_swing_sequence_age=2,
            macro_swing_sequence_age=None,
            structure_tf="H4",
            macro_sequence_tf="H4",
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )

    assert out["structure_ok"] is True
    assert out["guarded_structure_diagnostics"]["macro_sequence_independent"] is False


def test_guarded_liquidity_sweep_requires_direction_and_location(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FRESH_SEQUENCE_STRUCTURE_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_LIQUIDITY_SWEEP_STRUCTURE_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_LIQUIDITY_SWEEP_STRUCTURE_REQUIRE_LOCATION", True
    )
    base = {
        "current_swing_sequence": "CONTRACTION",
        "macro_swing_sequence": "CONTRACTION",
        "liquidity_sweep": True,
        "sweep_direction": "LONG",
    }

    at_location = NakedEngine().calculate_confidence(
        _res(**base), 100.0, "LONG", style_profile=_style()
    )
    off_location = NakedEngine().calculate_confidence(
        _res(
            **base,
            zone_touched=False,
            near_active_zone=False,
            ob_at_zone=False,
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    missing_direction = NakedEngine().calculate_confidence(
        _res(**{**base, "sweep_direction": None}),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    malformed_false_location = NakedEngine().calculate_confidence(
        _res(
            **base,
            zone_touched=False,
            near_active_zone="False",
            ob_at_zone=False,
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )

    assert at_location["structure_ok"] is True
    assert at_location["liquidity_sweep_structure_ok"] is True
    assert "liquidity_sweep" in at_location["structure_direction_sources"]
    assert off_location["structure_ok"] is False
    assert missing_direction["structure_ok"] is False
    assert malformed_false_location["structure_ok"] is False


def test_guarded_liquidity_sweep_rejects_fresh_opposing_sequence(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FRESH_SEQUENCE_STRUCTURE_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_LIQUIDITY_SWEEP_STRUCTURE_ENABLED", True)
    out = NakedEngine().calculate_confidence(
        _res(
            current_swing_sequence="LH_LL",
            current_swing_sequence_age=2,
            macro_swing_sequence="CONTRACTION",
            macro_swing_sequence_age=2,
            liquidity_sweep=True,
            sweep_direction="LONG",
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )

    assert out["structure_ok"] is False
    assert out["liquidity_sweep_structure_ok"] is False
    assert out["guarded_structure_diagnostics"]["fresh_opposing_sequence"] is True


def test_opposing_bos_still_overrides_restored_structure_paths(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FRESH_SEQUENCE_STRUCTURE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_LIQUIDITY_SWEEP_STRUCTURE_ENABLED", True)
    out = NakedEngine().calculate_confidence(
        _res(
            current_swing_sequence_age=1,
            macro_swing_sequence_age=1,
            structure_tf="H4",
            macro_sequence_tf="D1",
            liquidity_sweep=True,
            sweep_direction="LONG",
            bos_data={"bos_bull": False, "bos_bear": True},
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )

    assert out["structure_ok"] is False
    assert out["structure_bos_direction_conflict"] is True
    assert out["fresh_sequence_structure_ok"] is False
    assert out["liquidity_sweep_structure_ok"] is False
