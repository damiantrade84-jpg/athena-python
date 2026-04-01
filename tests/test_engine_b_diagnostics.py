"""Engine B observability — reason codes on diagnostics (no scoring assertions)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from market_structure import (
    ENGINE_B_REASON_ADVERSE_DXY,
    ENGINE_B_REASON_RESISTANCE_TOO_CLOSE,
    ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
    engine,
)


def _base_res_long():
    return {
        "atr": 1.0,
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
        "distance_to_res": 0.05,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
        "profile_notes": "",
        "prev_session_profile_valid": False,
        "profile_in_play": False,
        "profile_reaction_strength": 0.0,
        "profile_bias": "neutral",
    }


def test_calculate_confidence_engine_b_diagnostics_resistance_too_close():
    """LONG with distance_to_res below min_room_atr * atr → resistance_too_close."""
    res = _base_res_long()
    res["distance_to_res"] = 0.05  # < 0.35 * 1.0 default min_room_atr
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    diag = out.get("engine_b_diagnostics") or {}
    assert diag.get("reason_codes") == [ENGINE_B_REASON_RESISTANCE_TOO_CLOSE]


def test_calculate_confidence_engine_b_diagnostics_support_too_close():
    res = _base_res_long()
    res["current_swing_sequence"] = "LH_LL"
    res["macro_swing_sequence"] = "LH_LL"
    res["distance_to_res"] = 5.0
    res["distance_to_sup"] = 0.05
    res["recommended_stop_loss"] = 101.0
    res["recommended_take_profit"] = 95.0
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="SHORT",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    diag = out.get("engine_b_diagnostics") or {}
    assert diag.get("reason_codes") == [ENGINE_B_REASON_SUPPORT_TOO_CLOSE]


def test_calculate_confidence_engine_b_diagnostics_empty_when_room_ok():
    res = _base_res_long()
    res["distance_to_res"] = 2.0
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    assert out.get("engine_b_diagnostics", {}).get("reason_codes") == []


def test_check_macro_correlation_detail_returns_reason_when_blocking():
    # Construct 40 bars: asset falls while DXY rises → negative correlation; last segment DXY up → block LONG
    rng = np.random.default_rng(42)
    t = np.arange(40, dtype=float)
    dxy = 100 + t * 0.05 + rng.normal(0, 0.02, size=40)
    asset = 200 - t * 0.12 + rng.normal(0, 0.05, size=40)
    ok, reason = engine.check_macro_correlation_detail(
        asset.tolist(), dxy.tolist(), "LONG"
    )
    assert ok is False
    assert reason == ENGINE_B_REASON_ADVERSE_DXY


def test_check_macro_correlation_detail_clear_insufficient_history():
    ok, reason = engine.check_macro_correlation_detail([1.0] * 10, [1.0] * 10, "LONG")
    assert ok is True
    assert reason is None


def test_check_macro_correlation_wrapper_matches_detail():
    a = list(np.linspace(200, 180, 35))
    d = list(np.linspace(100, 108, 35))
    d1, _ = engine.check_macro_correlation_detail(a, d, "LONG")
    d2 = engine.check_macro_correlation(a, d, "LONG")
    assert d1 == d2
