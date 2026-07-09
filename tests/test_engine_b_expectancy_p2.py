"""Phase 2: structure_ok tighten, room-no-wall MTF, follow-through trap, macro fail-closed."""

from __future__ import annotations

import config
from market_structure import NakedEngine


def _base_res(**overrides):
    res = {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "LH_LL",
        "macro_swing_sequence": "LH_LL",
        "bos_confirmed": True,
        "bos_mtf_confirmed": False,
        "liquidity_sweep": True,
        "sweep_direction": "LONG",
        "choch_confirmed": False,
        "zone_touched": True,
        "near_active_zone": False,
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
    res.update(overrides)
    return res


def _style(**overrides):
    style = {
        "style": "intraday",
        "min_score": 4.0,
        "min_room_atr": 0.35,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
    }
    style.update(overrides)
    return style


def test_structure_ok_requires_bos_mtf_when_both_oppose(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    engine = NakedEngine()
    blocked = engine.calculate_confidence(
        _base_res(bos_mtf_confirmed=False),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    allowed = engine.calculate_confidence(
        _base_res(bos_mtf_confirmed=True),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert blocked["structure_ok"] is False
    assert allowed["structure_ok"] is True


def test_structure_ok_legacy_allows_sweep_when_gate_off(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(bos_confirmed=False, bos_mtf_confirmed=False, liquidity_sweep=True),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is True


def test_opposing_sweep_does_not_satisfy_structure_ok(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(
            bos_confirmed=False,
            liquidity_sweep=True,
            sweep_direction="SHORT",
            current_swing_sequence="LH_LL",
            macro_swing_sequence="LH_LL",
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is False


def test_room_no_wall_requires_h1_and_h4_trend(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ROOM_NO_WALL_REQUIRE_MTF", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    # No opposing wall (None distances) + only H1 aligned → fail under MTF require.
    blocked = NakedEngine().calculate_confidence(
        _base_res(
            distance_to_res=None,
            distance_to_sup=None,
            current_swing_sequence="HH_HL",
            macro_swing_sequence="LH_LL",
            bos_confirmed=True,
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    allowed = NakedEngine().calculate_confidence(
        _base_res(
            distance_to_res=None,
            distance_to_sup=None,
            current_swing_sequence="HH_HL",
            macro_swing_sequence="HH_HL",
            bos_confirmed=True,
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert blocked["room_ok"] is False
    assert allowed["room_ok"] is True


def test_follow_through_trap_blocks_entry_ok(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_FOLLOW_THROUGH",
        {
            "ENABLED": True,
            "DIAGNOSTICS_ENABLED": True,
            "BLOCK_ENTRY_ON_TRAP": True,
            "MAX_BONUS": 1.5,
            "MIN_PENALTY": -0.5,
        },
    )
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})

    # Need >=5 candles; breakout closes above 100, then two full reversals (trap).
    entry_candles = [
        {"open": 98.0, "high": 98.5, "low": 97.5, "close": 98.2, "vol": 100},
        {"open": 98.2, "high": 98.8, "low": 98.0, "close": 98.5, "vol": 100},
        {"open": 98.5, "high": 99.2, "low": 98.3, "close": 99.0, "vol": 110},
        {"open": 99.0, "high": 101.5, "low": 98.8, "close": 101.2, "vol": 200},  # breakout
        {"open": 101.0, "high": 101.1, "low": 98.5, "close": 98.6, "vol": 180},  # reverse
        {"open": 98.6, "high": 99.0, "low": 97.5, "close": 97.8, "vol": 160},  # reverse
    ]
    res = _base_res(
        current_swing_sequence="HH_HL",
        macro_swing_sequence="HH_HL",
        bos_confirmed=True,
        bos_data={"last_broken_high": 100.0, "last_broken_low": 95.0},
        trigger_ok=True,
        zone_touched=True,
    )
    out = NakedEngine().calculate_confidence(
        res, 100.0, "LONG", style_profile=_style(), entry_candles=entry_candles
    )
    assert out["entry_ok"] is False
    assert out.get("follow_through_trap_blocked") is True


def test_macro_short_history_fail_closed_when_required(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_MACRO_SHORT_HISTORY_FAIL_CLOSED", True)
    engine = NakedEngine()
    ok, reason = engine.check_macro_correlation_detail(
        [1.0] * 10, [1.0] * 10, "LONG", macro_required=True
    )
    assert ok is False
    assert reason == "macro_history_insufficient"
    ok2, reason2 = engine.check_macro_correlation_detail(
        [1.0] * 10, [1.0] * 10, "LONG", macro_required=False
    )
    assert ok2 is True
    assert reason2 is None
