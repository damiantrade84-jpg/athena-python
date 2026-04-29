"""Engine B observability — reason codes on diagnostics (no scoring assertions)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from market_structure import (
    ENGINE_B_REASON_ADVERSE_DXY,
    ENGINE_B_REASON_BOS_WITHOUT_VOLUME,
    ENGINE_B_REASON_D1_PD_ARRAY_CONFLICT,
    ENGINE_B_REASON_FOREX_ADX_LOW,
    ENGINE_B_REASON_NO_TRIGGER_PATTERN,
    ENGINE_B_REASON_RESISTANCE_TOO_CLOSE,
    ENGINE_B_REASON_SEQUENCE_COUNTER_TREND,
    ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE,
    ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
    ENGINE_B_REASON_TP_WRONG_SIDE,
    NakedEngine,
    engine,
    engine_b_confidence_passes,
)


def _set_crypto_profile_flags(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_PROFILE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_TARGET_V2_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_TRIGGER_PROFILE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_REQUIRE_STRUCTURAL_TARGET_FOR_PASS", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_MIN_RR", 1.2)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_LOCATION_ATR_BUFFER", 0.75)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_MIN_DISPLACEMENT_ATR", 0.35)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_MIN_VOLUME_RATIO", 1.2)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_MIN_TAKER_DELTA_RATIO", 0.55)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_ENTRY_TIMEFRAMES", ["M15", "M5"])


def _crypto_kline(open_, high, low, close, volume=100.0, taker_buy=None):
    candle = {
        "open_time": 1,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
        "vol": float(volume),
        "quote_volume": float(volume) * float(close),
        "number_of_trades": 10,
    }
    if taker_buy is not None:
        candle["taker_buy_base_volume"] = float(taker_buy)
        candle["taker_buy_quote_volume"] = float(taker_buy) * float(close)
    return candle


def _crypto_trigger_candles_long():
    candles = [
        _crypto_kline(100.0, 100.48, 99.95, 100.12, volume=100.0, taker_buy=50.0)
        for _ in range(20)
    ]
    candles.append(
        _crypto_kline(100.05, 100.50, 100.00, 100.45, volume=150.0, taker_buy=100.0)
    )
    return candles


def _micro_breakout_candles_long():
    candles = [
        {
            "open": 100.0,
            "high": 100.5,
            "low": 99.5,
            "close": 100.1,
            "vol": 100.0,
            "volume": 100.0,
        }
        for _ in range(20)
    ]
    candles[-2]["close"] = 100.2
    candles[-1].update({"open": 100.2, "high": 101.2, "low": 100.1, "close": 101.0, "vol": 160.0, "volume": 160.0})
    return candles


def _crypto_res_long_with_structural_target():
    res = _base_res_long()
    res.update(
        {
            "asset_type": "crypto",
            "structural_verdict": "CLEAR",
            "trigger_ok": False,
            "bos_confirmed": True,
            "strong_close": False,
            "inside_break_candle": False,
            "engulfing_candle": False,
            "liquidity_sweep": False,
            "choch_confirmed": False,
            "zone_touched": False,
            "near_active_zone": False,
            "ob_at_zone": False,
            "active_zone_distance": 0.5,
            "distance_to_res": 3.0,
            "nearest_support_zone": {"lower": 99.2, "upper": 100.1, "center": 99.8},
            "recommended_stop_loss": 99.0,
            "recommended_take_profit": 101.4,
            "crypto_target_selected_target_price": 101.4,
            "crypto_target_selected_target_tf": "H4",
            "crypto_target_selected_target_type": "resistance_zone",
            "crypto_target_selected_target_rr": 1.4,
            "crypto_target_selected_target_is_structural": True,
            "crypto_target_used_fallback_projection": False,
            "crypto_target_fallback_used_for_diagnostics_only": True,
            "crypto_target_fallback_projection_price": 102.0,
            "crypto_target_path_clear_to_tp2": True,
            "crypto_target_path_block_reason": None,
        }
    )
    return res


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


def test_calculate_confidence_forex_adx_below_min_blocks_structure():
    import config

    res = _base_res_long()
    res["asset_type"] = "forex"
    res["d1_adx"] = 18.0
    res["h4_adx"] = 20.0
    old_min = config.CONFIG.get("ENGINE_B_FOREX_ADX_MIN")
    config.CONFIG["ENGINE_B_FOREX_ADX_MIN"] = 25.0
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    try:
        assert out.get("structure_ok") is False
        assert ENGINE_B_REASON_FOREX_ADX_LOW in (
            out.get("engine_b_diagnostics") or {}
        ).get("reason_codes", [])
    finally:
        config.CONFIG["ENGINE_B_FOREX_ADX_MIN"] = old_min


def test_check_macro_correlation_detail_returns_reason_when_blocking():
    # Construct 60 bars: asset falls while DXY rises → negative correlation; last segment DXY up → block LONG
    rng = np.random.default_rng(42)
    t = np.arange(60, dtype=float)
    dxy = 100 + t * 0.05 + rng.normal(0, 0.02, size=60)
    asset = 200 - t * 0.12 + rng.normal(0, 0.05, size=60)
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


def test_analyze_structure_falls_back_to_rr_when_resistance_is_too_close(monkeypatch):
    local_engine = NakedEngine()

    monkeypatch.setattr(
        local_engine,
        "_find_zones",
        lambda *_args, **_kwargs: (
            [{"upper": 100.9, "lower": 100.8, "center": 100.85, "volume_strength": 1.0}],
            [{"upper": 99.7, "lower": 99.5, "center": 99.6, "volume_strength": 1.0}],
        ),
    )
    monkeypatch.setattr(
        local_engine,
        "_detect_fvg",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        local_engine,
        "_determine_sequence",
        lambda *_args, **_kwargs: {
            "state": "HH_HL",
            "recent_low": 99.6,
            "recent_high": 100.6,
            "has_equal_extrema": False,
        },
    )
    monkeypatch.setattr(
        local_engine,
        "_detect_bos",
        lambda *_args, **_kwargs: {
            "bos_bull": True,
            "bos_bear": False,
            "bos_volume_confirmed": True,
        },
    )
    monkeypatch.setattr(
        local_engine,
        "_detect_sweep",
        lambda *_args, **_kwargs: {
            "bull_sweep": False,
            "bear_sweep": False,
            "sweep_low": None,
            "sweep_high": None,
        },
    )
    monkeypatch.setattr(
        local_engine,
        "_detect_order_blocks",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        local_engine,
        "_detect_choch",
        lambda *_args, **_kwargs: {
            "choch_bull": False,
            "choch_bear": False,
            "choch_level": None,
        },
    )
    monkeypatch.setattr(
        local_engine,
        "_zone_context",
        lambda *_args, **_kwargs: {"distance": 0.2, "near_zone": True, "zone_touched": True},
    )
    monkeypatch.setattr(
        local_engine,
        "_price_action_trigger",
        lambda *_args, **_kwargs: {
            "pattern": "REJECTION",
            "trigger_ok": True,
            "rejection": True,
            "engulfing": False,
            "inside_break": False,
            "strong_close": True,
        },
    )

    candles = [
        {"open": 100.0, "high": 100.4, "low": 99.8, "close": 100.1, "vol": 1000.0}
        for _ in range(40)
    ]
    result = local_engine.analyze_structure(
        candles,
        candles,
        candles,
        current_price=100.0,
        direction="LONG",
        atr=1.0,
        regime="RANGING",
        fallback_rr=1.8,
        asset_type="stock",
        enable_zone_registry=False,
        enable_profile_context=False,
    )

    expected_tp = 100.0 + ((100.0 - result["recommended_stop_loss"]) * 1.8)
    assert result["tp_source"] == "fallback_rr"
    assert result["tp_structural_limited"] is True
    assert result["recommended_take_profit"] == pytest.approx(expected_tp)
    assert result["recommended_take_profit"] > 100.0


def test_calculate_confidence_structural_tp_wrong_side_emits_diagnostic():
    """Wrong structural TP emits ENGINE_B_REASON_TP_WRONG_SIDE.
    rr_ok is now True because the RR gate uses ATR execution levels (not structural TP).
    tp_side_ok reflects the structural TP side check for diagnostic purposes.
    """
    res = _base_res_long()
    res["recommended_take_profit"] = 99.8  # structural TP below entry — wrong side for LONG
    res["atr"] = 1.0
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False, "style": "intraday"},
    )
    # Structural TP diagnostic is still emitted
    assert out["tp_side_ok"] is False
    assert ENGINE_B_REASON_TP_WRONG_SIDE in out.get("engine_b_diagnostics", {}).get("reason_codes", [])
    # ATR execution TP (above entry) is valid so rr_ok=True with execution RR
    assert out["execution_levels_valid"] is True
    assert out["rr_used_for_gate"] > 0
    assert out["rr_ok"] is True
    # Execution levels are present
    assert out["execution_sl"] is not None
    assert out["execution_tp"] is not None


def test_calculate_confidence_flexible_mode_accepts_liquidity_sweep_catalyst():
    res = _base_res_long()
    res["bos_confirmed"] = False
    res["trigger_ok"] = False
    res["liquidity_sweep"] = True
    res["ob_at_zone"] = False
    res["distance_to_res"] = 3.0
    res["recommended_stop_loss"] = 99.0
    res["recommended_take_profit"] = 102.0

    style_profile = {
        "min_room_atr": 0.35,
        "min_rr": 1.5,
        "min_score": 5.0,
        "require_macro_align": False,
        "checklist_mode": "flexible",
    }
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile=style_profile,
    )
    gate_ok, min_score_scaled = engine_b_confidence_passes(
        out, style_profile, regime_label="RANGING"
    )

    assert out["entry_ok"] is True
    assert out["passed"] is True
    assert out["score"] == pytest.approx(5.0)
    assert min_score_scaled == 5.0
    assert gate_ok is True


def test_calculate_confidence_emits_no_trigger_pattern_when_missing():
    res = _base_res_long()
    res["trigger_ok"] = False
    res["bos_confirmed"] = False
    res["liquidity_sweep"] = False
    res["choch_confirmed"] = False
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    codes = out.get("engine_b_diagnostics", {}).get("reason_codes", [])
    assert ENGINE_B_REASON_NO_TRIGGER_PATTERN in codes


def test_engine_b_research_lab_micro_breakout_can_satisfy_entry_gate(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_RESEARCH_LAB_FACTORS", {
        "ENABLED": True,
        "ALLOW_GATE_UPGRADE": True,
        "GROUPS": {"commodity_other": ["micro_breakout"]},
    })

    res = _base_res_long()
    res.update({
        "asset_type": "commodity",
        "bos_confirmed": False,
        "trigger_ok": False,
        "strong_close": False,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "liquidity_sweep": False,
        "choch_confirmed": False,
        "distance_to_res": 3.0,
        "recommended_stop_loss": 100.0,
        "recommended_take_profit": 104.0,
    })

    out = engine.calculate_confidence(
        res,
        current_price=101.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=_micro_breakout_candles_long(),
        style_profile={
            "style": "intraday",
            "score_group": "commodity_other",
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "require_macro_align": False,
        },
    )

    assert out["original_trigger_ok"] is False
    assert out["trigger_ok"] is True
    assert out["entry_ok"] is True
    assert out["research_lab_entry_upgrade"] is True
    assert out["research_lab_detail"]["components"]["micro_breakout"]["passed"] is True
    assert ENGINE_B_REASON_NO_TRIGGER_PATTERN not in out.get("engine_b_diagnostics", {}).get("reason_codes", [])


def test_engine_b_research_lab_precious_trackers_maps_to_metals_group(monkeypatch):
    """XAU/XAG score groups are precious_trackers in scoring.py; RL config uses metals."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_RESEARCH_LAB_FACTORS", {
        "ENABLED": True,
        "ALLOW_GATE_UPGRADE": True,
        "GROUPS": {"metals": ["micro_breakout"]},
    })

    res = _base_res_long()
    res.update({
        "asset_type": "commodity",
        "bos_confirmed": False,
        "trigger_ok": False,
        "strong_close": False,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "liquidity_sweep": False,
        "choch_confirmed": False,
        "distance_to_res": 3.0,
        "recommended_stop_loss": 100.0,
        "recommended_take_profit": 104.0,
    })

    out = engine.calculate_confidence(
        res,
        current_price=101.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=_micro_breakout_candles_long(),
        style_profile={
            "style": "intraday",
            "score_group": "precious_trackers",
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "require_macro_align": False,
        },
    )

    detail = out["research_lab_detail"]
    assert detail.get("enabled") is True
    assert detail.get("score_group") == "metals"
    assert "micro_breakout" in detail.get("allowed", [])
    assert detail["components"]["micro_breakout"]["passed"] is True
    assert out["research_lab_entry_upgrade"] is True
    assert out["trigger_ok"] is True


def test_calculate_confidence_emits_bos_without_volume():
    res = _base_res_long()
    res["bos_volume_confirmed"] = False
    res["trigger_ok"] = False
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    codes = out.get("engine_b_diagnostics", {}).get("reason_codes", [])
    assert ENGINE_B_REASON_BOS_WITHOUT_VOLUME in codes


def test_calculate_confidence_emits_d1_pd_array_conflict():
    res = _base_res_long()
    res["d1_pd_array_conflict"] = True
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    codes = out.get("engine_b_diagnostics", {}).get("reason_codes", [])
    assert ENGINE_B_REASON_D1_PD_ARRAY_CONFLICT in codes


def test_calculate_confidence_emits_structural_tp_too_close():
    res = _base_res_long()
    res["tp_structural_limited"] = True
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    codes = out.get("engine_b_diagnostics", {}).get("reason_codes", [])
    assert ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE in codes


def test_calculate_confidence_d1_penalty_is_reduced():
    """Default D1 PD-array penalty should be 0.25, not 0.5."""
    res = _base_res_long()
    res["d1_pd_array_conflict"] = True
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    assert out["d1_pd_conflict_penalty"] == pytest.approx(0.25)


def test_calculate_confidence_emits_sequence_counter_trend():
    res = _base_res_long()
    res["current_swing_sequence"] = "LH_LL"
    res["macro_swing_sequence"] = "LH_LL"
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    codes = out.get("engine_b_diagnostics", {}).get("reason_codes", [])
    assert ENGINE_B_REASON_SEQUENCE_COUNTER_TREND in codes
    assert out["structure_ok"] is False


def test_crypto_taker_pressure_uses_binance_kline_fields():
    pressure = engine._crypto_kline_taker_pressure(
        _crypto_kline(100.0, 101.0, 99.0, 100.5, volume=100.0, taker_buy=60.0)
    )
    assert pressure["taker_data_missing"] is False
    assert pressure["taker_buy_ratio"] == pytest.approx(0.6)
    assert pressure["taker_sell_ratio"] == pytest.approx(0.4)

    missing = engine._crypto_kline_taker_pressure(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0}
    )
    assert missing["taker_data_missing"] is True


def test_crypto_profile_m15_trigger_can_create_pass_with_structural_target(monkeypatch):
    _set_crypto_profile_flags(monkeypatch)
    res = _crypto_res_long_with_structural_target()

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.2, "require_macro_align": False},
        crypto_entry_candles_by_tf={"M15": _crypto_trigger_candles_long(), "M5": []},
    )

    assert out["passed"] is True
    assert out["trigger_passed"] is True
    assert out["trigger_timeframe"] == "M15"
    assert out["selected_target_is_structural"] is True
    assert out["fallback_used_for_final_pass"] is False
    assert out["failed_gate_names"] == []


def test_crypto_profile_requires_real_h4_or_d1_target(monkeypatch):
    _set_crypto_profile_flags(monkeypatch)
    res = _crypto_res_long_with_structural_target()
    res["crypto_target_selected_target_price"] = None
    res["crypto_target_selected_target_tf"] = None
    res["crypto_target_selected_target_is_structural"] = False
    res["recommended_take_profit"] = 101.4

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.2, "require_macro_align": False},
        crypto_entry_candles_by_tf={"M15": _crypto_trigger_candles_long(), "M5": []},
    )

    assert out["passed"] is False
    assert out["crypto_target_v2_valid"] is False
    assert "target_v2" in out["failed_gate_names"]


def test_crypto_profile_fallback_target_cannot_create_final_pass(monkeypatch):
    _set_crypto_profile_flags(monkeypatch)
    res = _crypto_res_long_with_structural_target()
    res["crypto_target_selected_target_price"] = None
    res["crypto_target_selected_target_tf"] = None
    res["crypto_target_selected_target_is_structural"] = False
    res["crypto_target_used_fallback_projection"] = True
    res["crypto_target_fallback_used_for_diagnostics_only"] = False
    res["recommended_take_profit"] = 102.0

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.2, "require_macro_align": False},
        crypto_entry_candles_by_tf={"M15": _crypto_trigger_candles_long(), "M5": []},
    )

    assert out["passed"] is False
    assert out["fallback_used_for_final_pass"] is True
    assert "fallback_target" in out["failed_gate_names"]
    assert "target_v2" in out["failed_gate_names"]


def test_forex_engine_b_behavior_unchanged_when_crypto_flags_enabled(monkeypatch):
    _set_crypto_profile_flags(monkeypatch)
    res = _base_res_long()
    res["asset_type"] = "forex"
    res["distance_to_res"] = 3.0
    res["recommended_stop_loss"] = 99.0
    res["recommended_take_profit"] = 102.0

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.5, "require_macro_align": False},
        crypto_entry_candles_by_tf={"M15": _crypto_trigger_candles_long(), "M5": []},
    )

    assert out["crypto_profile_enabled"] is False
    assert out["trigger_ok"] is True
    assert out["passed"] is True
    assert out["failed_gate_names"] == []


# ─── Engine B RR basis tests ──────────────────────────────────────────────────

from market_structure import resolve_engine_b_execution_levels


def test_resolve_engine_b_execution_levels_atr_sl_structural_tp_long():
    """Wide structural SL + valid structural TP → ATR SL + structural TP → good execution RR."""
    import config as _cfg
    # Wide structural SL would give structural_rr=0.6
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=97.0,   # 3 ATR below — wide structural stop
        structural_tp=101.8,  # structural target 1.8 ATR above
        atr=1.0,
        style="intraday",
        asset_class="forex",
    )
    assert out["structural_rr"] == pytest.approx(1.8 / 3.0, abs=1e-3)
    # ATR intraday SL for forex = 1.5 ATR
    assert out["execution_sl"] == pytest.approx(100.0 - 1.5 * 1.0, abs=1e-6)
    # execution TP = structural TP (valid)
    assert out["execution_tp"] == pytest.approx(101.8, abs=1e-6)
    # execution_rr = (101.8 - 100.0) / (100.0 - 98.5) = 1.8/1.5 = 1.2
    assert out["execution_rr"] == pytest.approx(1.8 / 1.5, abs=1e-3)
    assert out["rr_used_for_gate"] == pytest.approx(1.8 / 1.5, abs=1e-3)
    assert out["execution_levels_valid"] is True
    assert "atr" in out["rr_source"]


def test_resolve_engine_b_execution_levels_atr_sl_fallback_tp_when_structural_missing():
    """No structural TP → ATR SL + ATR TP."""
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.5,
        structural_tp=None,
        atr=1.0,
        style="intraday",
        asset_class="forex",
    )
    assert out["execution_sl"] is not None
    assert out["execution_tp"] is not None
    assert out["execution_tp"] > 100.0
    assert out["execution_rr"] > 0
    assert out["execution_levels_valid"] is True
    assert "atr" in out["tp_source"] if "tp_source" in out else True


def test_resolve_engine_b_execution_levels_structural_fallback_when_atr_config_missing():
    """If config has no ATR mults for an asset, fall back to structural levels."""
    import config as _cfg
    old_style = _cfg.CONFIG.get("STYLE_ATR_MULTS", {})
    old_atr = _cfg.CONFIG.get("ATR_CLASS", {})
    _cfg.CONFIG["STYLE_ATR_MULTS"] = {}
    _cfg.CONFIG["ATR_CLASS"] = {}
    try:
        out = resolve_engine_b_execution_levels(
            direction="LONG",
            entry=100.0,
            structural_sl=99.0,
            structural_tp=103.0,
            atr=1.0,
            style="intraday",
            asset_class="forex",
        )
        assert out["execution_sl"] == pytest.approx(99.0)
        assert out["execution_tp"] == pytest.approx(103.0)
        assert out["rr_source"] == "structural_sl_structural_tp"
        assert out["execution_levels_valid"] is True
    finally:
        _cfg.CONFIG["STYLE_ATR_MULTS"] = old_style
        _cfg.CONFIG["ATR_CLASS"] = old_atr


def test_rr_gate_uses_execution_rr_not_structural_when_structural_rr_fails():
    """Key regression: structural RR fails but execution RR passes → rr_ok=True."""
    res = _base_res_long()
    res["atr"] = 1.0
    res["recommended_stop_loss"] = 97.0   # wide structural SL → structural_rr = 0.6
    res["recommended_take_profit"] = 101.8  # structural target
    res["distance_to_res"] = 3.0

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.2, "require_macro_align": False, "style": "intraday"},
    )
    assert out["structural_rr"] == pytest.approx(1.8 / 3.0, abs=1e-3)
    assert out["execution_rr"] >= 1.2
    assert out["rr_used_for_gate"] >= 1.2
    assert out["rr_ok"] is True
    assert "atr" in out["rr_source"]


def test_rr_gate_fails_when_execution_rr_also_insufficient():
    """If both structural and execution RR are below min_rr, rr_ok must be False."""
    res = _base_res_long()
    res["atr"] = 1.0
    # Structural TP only 0.5 ATR above entry — gives execution_rr = 0.5/1.5 ≈ 0.33
    res["recommended_stop_loss"] = 99.5
    res["recommended_take_profit"] = 100.5
    res["distance_to_res"] = 3.0

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.2, "require_macro_align": False, "style": "intraday"},
    )
    assert out["execution_rr"] < 1.2
    assert out["rr_ok"] is False


def test_rr_gate_short_direction_atr_sl():
    """SHORT direction: ATR SL above entry, structural TP below entry."""
    res = {
        "atr": 1.0,
        "current_swing_sequence": "LH_LL",
        "macro_swing_sequence": "LH_LL",
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
        "ob_at_zone": False,
        "breaker_block": False,
        "bos_mtf_confirmed": False,
        "distance_to_res": 5.0,
        "distance_to_sup": 0.5,
        "recommended_stop_loss": 103.5,   # wide structural SL (above entry for SHORT)
        "recommended_take_profit": 98.2,  # structural target below entry
        "profile_notes": "",
        "prev_session_profile_valid": False,
        "profile_in_play": False,
        "profile_reaction_strength": 0.0,
        "profile_bias": "neutral",
    }
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="SHORT",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False, "style": "intraday"},
    )
    # Execution SL should be ATR above entry for SHORT
    assert out["execution_sl"] > 100.0
    # Execution TP is structural (98.2, below entry)
    assert out["execution_tp"] < 100.0
    assert out["execution_levels_valid"] is True
    assert out["rr_used_for_gate"] > 0


def test_execution_levels_exposed_in_confidence_output():
    """All execution-level keys are present in calculate_confidence return dict."""
    res = _base_res_long()
    res["atr"] = 1.0
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False, "style": "intraday"},
    )
    for key in (
        "structural_sl", "structural_tp", "structural_rr",
        "execution_sl", "execution_tp", "execution_rr",
        "rr_used_for_gate", "rr_source", "level_mode",
        "execution_levels_valid", "execution_level_reject_reason",
    ):
        assert key in out, f"Missing key: {key}"


def test_crypto_fallback_projection_cannot_pass_final_rr_gate(monkeypatch):
    """Fallback projection used for final pass → rr_ok may be True from ATR but crypto gates block passed."""
    _set_crypto_profile_flags(monkeypatch)
    res = _crypto_res_long_with_structural_target()
    res["crypto_target_selected_target_price"] = None
    res["crypto_target_selected_target_tf"] = None
    res["crypto_target_selected_target_is_structural"] = False
    res["crypto_target_used_fallback_projection"] = True
    res["crypto_target_fallback_used_for_diagnostics_only"] = False
    res["recommended_take_profit"] = 102.0

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.2, "require_macro_align": False, "style": "intraday"},
        crypto_entry_candles_by_tf={"M15": _crypto_trigger_candles_long(), "M5": []},
    )
    assert out["passed"] is False
    assert out["fallback_used_for_final_pass"] is True
