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
    _engine_b_structural_target_price,
    _engine_b_structural_tp_buffer_atr_mult,
    _engine_b_micro_breakout_value,
    _asset_class_structure_adjustment,
    _engine_b_forex_session_structure_context,
    _engine_b_equity_session_structure_context,
    _crypto_structure_adjustment,
    engine,
    engine_b_confidence_passes,
)


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


def test_crypto_structure_adjustment_default_off_preserves_detection_threshold(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_VOLATILITY_ADJUSTMENT_ENABLED", False)
    candles = [
        {"open": 100.0, "high": 108.0, "low": 96.0, "close": 100.0}
        for _ in range(6)
    ]

    diag = _crypto_structure_adjustment("crypto", candles, atr=5.0)

    assert diag["enabled"] is False
    assert diag["applied"] is False
    assert diag["volatility_regime"] == "high_volatility"
    assert diag["swing_prominence_mult"] == pytest.approx(1.0)
    assert diag["bos_min_break_atr"] == pytest.approx(0.0)


def test_crypto_structure_adjustment_enabled_requires_stronger_bos(monkeypatch):
    local_engine = NakedEngine()
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_VOLATILITY_ADJUSTMENT_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_STRUCTURE_MULT", 2.0)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_BOS_MIN_BREAK_ATR", 0.10)

    highs = np.array([100.0, 105.0, 101.0, 104.0, 101.0, 105.3], dtype=float)
    lows = np.array([99.0, 100.0, 98.0, 100.0, 99.0, 101.0], dtype=float)
    closes = np.array([99.5, 104.5, 100.5, 103.5, 100.0, 105.3], dtype=float)
    swings = {"peak_idx": np.array([1, 3]), "trough_idx": np.array([2, 4])}

    legacy = local_engine._detect_bos(
        highs,
        lows,
        atr=2.0,
        closes=closes,
        swings=swings,
    )
    adjusted = local_engine._detect_bos(
        highs,
        lows,
        atr=2.0,
        closes=closes,
        swings=swings,
        min_break_atr=0.20,
    )

    assert legacy["bos_bull"] is True
    assert adjusted["bos_bull"] is False


def test_forex_session_structure_context_default_disabled_no_score_bonus(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_FOREX_SESSION_STRUCTURE_WEIGHTING",
        {"ENABLED": False, "SCORE_INFLUENCE_ENABLED": False},
    )

    diag = _engine_b_forex_session_structure_context(
        asset_type="forex",
        candle_time="2026-05-13T13:00:00Z",
        zone_context=True,
        ob_at_zone=True,
        fvg_overlap=True,
        liquidity_sweep=True,
    )

    assert diag["enabled"] is False
    assert diag["session"] == "london_ny_overlap"
    assert diag["session_quality"] == "high"
    assert diag["liquidity_sweep_active_session"] is True
    assert diag["score_bonus"] == 0.0


def test_forex_session_structure_context_enabled_weights_active_session(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_FOREX_SESSION_STRUCTURE_WEIGHTING",
        {
            "ENABLED": True,
            "SCORE_INFLUENCE_ENABLED": False,
            "HIGH_QUALITY_BONUS": 0.02,
            "MEDIUM_QUALITY_BONUS": 0.01,
            "LOW_QUALITY_PENALTY": -0.02,
            "LONDON_NY_OVERLAP_BONUS": 0.01,
            "LIQUIDITY_SWEEP_ACTIVE_SESSION_BONUS": 0.01,
            "MAX_ABS_SCORE_BONUS": 0.04,
        },
    )

    overlap = _engine_b_forex_session_structure_context(
        asset_type="forex",
        candle_time="2026-05-13T13:00:00Z",
        zone_context=True,
        ob_at_zone=True,
        fvg_overlap=True,
        liquidity_sweep=True,
    )
    asian = _engine_b_forex_session_structure_context(
        asset_type="forex",
        candle_time="2026-05-13T02:00:00Z",
        zone_context=True,
        ob_at_zone=False,
        fvg_overlap=False,
        liquidity_sweep=False,
    )
    crypto = _engine_b_forex_session_structure_context(
        asset_type="crypto",
        candle_time="2026-05-13T13:00:00Z",
        zone_context=True,
        ob_at_zone=True,
        fvg_overlap=True,
        liquidity_sweep=True,
    )

    assert overlap["session"] == "london_ny_overlap"
    assert overlap["score_bonus"] == pytest.approx(0.04)
    assert asian["session"] == "asian_off_hours"
    assert asian["score_bonus"] == pytest.approx(-0.02)
    assert crypto["score_bonus"] == 0.0


def test_asset_class_structure_adjustment_default_disabled_no_detection_change(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ASSET_CLASS_ADJUSTMENTS_ENABLED", False)
    candles = [
        {"open": 100.0, "high": 103.0, "low": 98.0, "close": 100.0}
        for _ in range(8)
    ]

    diag = _asset_class_structure_adjustment("stock", None, candles, atr=2.0)

    assert diag["enabled"] is False
    assert diag["asset_class"] == "stock"
    assert diag["applied"] is False
    assert diag["swing_prominence_mult"] == pytest.approx(1.0)
    assert diag["bos_min_break_atr"] == pytest.approx(0.0)


def test_asset_class_structure_adjustment_enabled_is_non_forex_non_crypto(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ASSET_CLASS_ADJUSTMENTS_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ASSET_CLASS_VOLATILITY_AWARE_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ASSET_CLASS_STRUCTURE_MULT", {"stock": 1.20})
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ASSET_CLASS_BOS_MIN_BREAK_ATR", {"stock": 0.06})
    candles = [
        {"open": 100.0, "high": 103.0, "low": 98.0, "close": 100.0}
        for _ in range(8)
    ]

    stock_diag = _asset_class_structure_adjustment("stock", None, candles, atr=2.0)
    forex_diag = _asset_class_structure_adjustment("forex", None, candles, atr=2.0)
    crypto_diag = _asset_class_structure_adjustment("crypto", None, candles, atr=2.0)

    assert stock_diag["applied"] is True
    assert stock_diag["swing_prominence_mult"] == pytest.approx(1.20)
    assert stock_diag["bos_min_break_atr"] == pytest.approx(0.072)
    assert forex_diag["applied"] is False
    assert crypto_diag["applied"] is False


def test_equity_session_structure_context_enabled_for_stock_index_etf_only(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_EQUITY_SESSION_STRUCTURE_WEIGHTING",
        {
            "ENABLED": True,
            "SCORE_INFLUENCE_ENABLED": True,
            "HIGH_QUALITY_BONUS": 0.015,
            "MEDIUM_QUALITY_BONUS": 0.008,
            "OFF_HOURS_PENALTY": -0.015,
            "LIQUIDITY_SWEEP_ACTIVE_SESSION_BONUS": 0.008,
            "MAX_ABS_SCORE_BONUS": 0.03,
        },
    )

    stock = _engine_b_equity_session_structure_context(
        asset_class="stock",
        candle_time="2026-05-13T14:45:00Z",
        zone_context=True,
        ob_at_zone=True,
        fvg_overlap=False,
        liquidity_sweep=True,
    )
    commodity = _engine_b_equity_session_structure_context(
        asset_class="commodity",
        candle_time="2026-05-13T14:45:00Z",
        zone_context=True,
        ob_at_zone=True,
        fvg_overlap=False,
        liquidity_sweep=True,
    )

    assert stock["session"] == "us_cash_open"
    assert stock["session_quality"] == "high"
    assert stock["score_bonus"] == pytest.approx(0.023)
    assert commodity["score_bonus"] == 0.0


def test_calculate_confidence_carries_asset_class_and_equity_session_diagnostics():
    res = _base_res_long()
    res["asset_type"] = "stock"
    res["distance_to_res"] = 2.0
    res["asset_class_structure_diagnostics"] = {
        "enabled": True,
        "asset_class": "stock",
        "applied": True,
        "structure_mult": 1.1,
    }
    res["equity_session_structure"] = {
        "enabled": True,
        "asset_class": "stock",
        "session": "us_cash_open",
        "score_bonus": 0.023,
    }

    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )

    diag = out["engine_b_diagnostics"]
    assert diag["asset_class_structure"]["asset_class"] == "stock"
    assert diag["equity_session_structure"]["session"] == "us_cash_open"


def test_calculate_confidence_carries_forex_session_diagnostics_only_for_forex():
    res = _base_res_long()
    res["asset_type"] = "forex"
    res["distance_to_res"] = 2.0
    res["forex_session_structure"] = {
        "enabled": True,
        "session": "london_ny_overlap",
        "session_quality": "high",
        "liquidity_sweep_active_session": True,
        "score_bonus": 0.04,
    }

    forex_out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    crypto_res = dict(res)
    crypto_res["asset_type"] = "crypto"
    crypto_out = engine.calculate_confidence(
        crypto_res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )

    assert forex_out["engine_b_diagnostics"]["forex_session_structure"]["session"] == "london_ny_overlap"
    assert "forex_session_structure" not in crypto_out["engine_b_diagnostics"]


def test_calculate_confidence_carries_crypto_structure_diagnostics_only_for_crypto():
    res = _base_res_long()
    res["asset_type"] = "crypto"
    res["distance_to_res"] = 2.0
    res["crypto_structure_diagnostics"] = {
        "enabled": True,
        "applied": True,
        "volatility_regime": "high_volatility",
        "wick_dominance": 0.72,
        "structure_quality_score": 0.55,
    }

    crypto_out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    forex_res = dict(res)
    forex_res["asset_type"] = "forex"
    forex_out = engine.calculate_confidence(
        forex_res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )

    assert crypto_out["engine_b_diagnostics"]["crypto_structure"]["applied"] is True
    assert "crypto_structure" not in forex_out["engine_b_diagnostics"]


def test_analyze_structure_adds_crypto_structure_payload(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_VOLATILITY_ADJUSTMENT_ENABLED", True)
    candles = []
    for i in range(40):
        base = 100.0 + (i * 0.4)
        candles.append(
            {
                "open": base,
                "high": base + (5.0 if i % 6 == 0 else 2.0),
                "low": base - (4.0 if i % 7 == 0 else 1.5),
                "close": base + 0.8,
                "vol": 100.0 + i,
            }
        )

    out = NakedEngine().analyze_structure(
        d1_candles=candles,
        h4_candles=candles,
        h1_candles=candles,
        current_price=float(candles[-1]["close"]),
        direction="LONG",
        atr=5.0,
        regime="HIGH_VOLATILITY",
        asset_type="crypto",
        enable_zone_registry=False,
        enable_profile_context=False,
        pair={"display": "BTC/USDT", "type": "crypto", "source": "binance"},
    )

    diag = out.get("crypto_structure_diagnostics")
    assert isinstance(diag, dict)
    assert diag["enabled"] is True
    assert diag["volatility_regime"] in {"elevated_volatility", "high_volatility"}
    assert "wick_dominance" in diag
    assert "structure_quality_score" in diag


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


def test_forex_rr_can_satisfy_space_gate_when_config_enabled(monkeypatch):
    res = _base_res_long()
    res["asset_type"] = "forex"
    res["distance_to_res"] = 0.05
    style_profile = {"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False}

    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOREX_RR_CAN_SATISFY_SPACE_GATE", False)
    baseline = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile=style_profile,
    )

    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOREX_RR_CAN_SATISFY_SPACE_GATE", True)
    enabled = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile=style_profile,
    )

    assert baseline["room_ok"] is False
    assert baseline["space_gate_ok"] is False
    assert baseline["passed"] is False
    assert enabled["room_ok"] is False
    assert enabled["rr_ok"] is True
    assert enabled["space_gate_ok"] is True
    assert enabled["forex_rr_space_gate_enabled"] is True
    assert enabled["passed"] is True


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


def test_calculate_confidence_forex_adx_derives_regime_not_blocks_structure():
    """FIX 6: ADX no longer blocks structure; it drives regime classification."""
    import config

    res = _base_res_long()
    res["asset_type"] = "forex"
    res["d1_adx"] = 18.0  # Below old 25 min
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
        # FIX 6: Low ADX should NOT block structure anymore
        assert out.get("structure_ok") is True
        # ADX should derive regime instead
        assert out.get("_adx_derived_regime") == "RANGING"
        # Old diagnostic should NOT appear
        assert ENGINE_B_REASON_FOREX_ADX_LOW not in (
            out.get("engine_b_diagnostics") or {}
        ).get("reason_codes", [])
    finally:
        config.CONFIG["ENGINE_B_FOREX_ADX_MIN"] = old_min


def test_calculate_confidence_can_disable_structure_gate_for_bt_experiment_only():
    res = _base_res_long()
    res.update(
        {
            "current_swing_sequence": "LH_LL",
            "macro_swing_sequence": "LH_LL",
            "distance_to_res": 3.0,
            "recommended_stop_loss": 99.0,
            "recommended_take_profit": 103.0,
        }
    )

    baseline = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )
    disabled = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={
            "min_room_atr": 0.35,
            "min_rr": 1.0,
            "require_macro_align": False,
            "disable_structure_gate": True,
        },
    )

    assert baseline["structure_ok"] is False
    assert baseline["structure_gate_original_ok"] is False
    assert baseline["structure_gate_disabled"] is False
    assert disabled["structure_ok"] is True
    assert disabled["structure_gate_original_ok"] is False
    assert disabled["structure_gate_disabled"] is True
    assert disabled["struct_points"] == pytest.approx(1.0)
    assert ENGINE_B_REASON_SEQUENCE_COUNTER_TREND in disabled.get(
        "engine_b_diagnostics", {}
    ).get("reason_codes", [])


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
            [{"upper": 100.7, "lower": 100.6, "center": 100.65, "volume_strength": 1.0}],
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


def test_d1_pd_array_conflict_window_is_configurable(monkeypatch):
    local_engine = NakedEngine()
    naked_cfg = dict(config.CONFIG.get("NAKED_ENGINE", {}) or {})
    naked_cfg["d1_pd_array_conflict_window_atr_mult"] = 1.5
    monkeypatch.setitem(config.CONFIG, "NAKED_ENGINE", naked_cfg)

    monkeypatch.setattr(
        local_engine,
        "_find_zones",
        lambda *_args, **_kwargs: (
            [{"upper": 103.0, "lower": 102.8, "center": 102.9, "volume_strength": 1.0}],
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
            "recent_high": 102.0,
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
        lambda *_args, **_kwargs: [
            {"type": "bearish", "top": 102.1, "bottom": 101.9, "strength": 75}
        ],
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
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "vol": 1000.0}
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
        asset_type="forex",
        enable_zone_registry=False,
        enable_profile_context=False,
    )

    assert result["d1_pd_array_conflict"] is False
    assert result["d1_conflict_metric_details"] == []


def test_rejection_wick_body_ratio_is_configurable(monkeypatch):
    local_engine = NakedEngine()
    naked_cfg = dict(config.CONFIG.get("NAKED_ENGINE", {}) or {})
    naked_cfg["rejection_wick_body_ratio"] = 0.8
    monkeypatch.setitem(config.CONFIG, "NAKED_ENGINE", naked_cfg)

    candles = [
        {"open": 99.8, "high": 100.1, "low": 99.5, "close": 99.9},
        {"open": 100.2, "high": 100.4, "low": 99.6, "close": 99.7},
        {"open": 100.0, "high": 101.1, "low": 99.1, "close": 101.0},
    ]

    trigger = local_engine._price_action_trigger(
        candles,
        direction="LONG",
        atr=1.0,
        zone_hit=False,
        bos_confirmed=False,
    )

    assert trigger["trigger_ok"] is True
    assert trigger["pattern"] == "REJECTION"


def test_follow_through_diagnostics_can_run_without_score_impact(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_FOLLOW_THROUGH",
        {"ENABLED": False, "DIAGNOSTICS_ENABLED": True, "MAX_BONUS": 1.5, "MIN_PENALTY": -0.5},
    )
    res = _base_res_long()
    res["distance_to_res"] = 3.0
    baseline = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=[],
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )

    entry_candles = [
        {"open": 100.0, "high": 100.5, "low": 99.8, "close": 100.3}
        for _ in range(6)
    ]
    observed = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        learning_ctx=None,
        entry_candles=entry_candles,
        style_profile={"min_room_atr": 0.35, "min_rr": 1.0, "require_macro_align": False},
    )

    detail = observed["follow_through_detail"]
    assert observed["score"] == baseline["score"]
    assert observed["follow_through_bonus"] == 0.0
    assert detail["enabled"] is False
    assert detail["diagnostics_enabled"] is True
    assert detail["confidence"] == "insufficient_data"


def _fvg_fixture():
    candles = []
    for i in range(90):
        base = 100.0 + ((i % 9) * 0.05)
        candles.append(
            {
                "open": base,
                "high": base + 0.25,
                "low": base - 0.25,
                "close": base + 0.03,
                "vol": 1000.0,
            }
        )
    candles[4].update({"open": 110.0, "high": 111.0, "low": 109.0, "close": 109.5})
    candles[6].update({"open": 104.5, "high": 105.0, "low": 104.0, "close": 104.2})
    candles[10].update({"open": 107.8, "high": 108.0, "low": 106.0, "close": 107.2})
    candles[20].update({"open": 90.2, "high": 91.0, "low": 89.5, "close": 90.0})
    candles[22].update({"open": 96.4, "high": 97.0, "low": 96.0, "close": 96.5})
    candles[30].update({"open": 92.5, "high": 94.0, "low": 92.0, "close": 93.0})
    return candles


def test_engine_b_fast_fvg_detection_matches_legacy(monkeypatch):
    local_engine = NakedEngine()
    candles = _fvg_fixture()

    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FAST_FVG_DETECTION", False)
    legacy = local_engine._detect_fvg(candles)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FAST_FVG_DETECTION", True)
    fast = local_engine._detect_fvg(candles)

    assert fast == legacy
    assert any(fvg.get("mitigated") for fvg in fast)


def test_engine_b_fast_fvg_detection_falls_back_to_legacy(monkeypatch):
    local_engine = NakedEngine()
    candles = _fvg_fixture()
    expected = local_engine._detect_fvg_legacy(candles)

    def _raise_fast(_candles):
        raise RuntimeError("fast path failure")

    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FAST_FVG_DETECTION", True)
    monkeypatch.setattr(local_engine, "_detect_fvg_fast", _raise_fast)

    assert local_engine._detect_fvg(candles) == expected


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
    # FIX 2: RANGING multiplier is now 0.90 (was 1.30), so 5.0 * 0.90 = 4.5 → round = 4
    assert min_score_scaled == 4.0
    assert gate_ok is True


def test_engine_b_confidence_passes_enforces_min_score_floor():
    style_profile = {"min_score": 5.0}
    gate_ok, min_score_scaled = engine_b_confidence_passes(
        {"passed": True, "score": 3.0}, style_profile, regime_label="RANGING"
    )

    assert min_score_scaled == 4.0
    assert gate_ok is False


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


def test_engine_b_research_lab_default_does_not_upgrade_entry_gate(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_RESEARCH_LAB_FACTORS", {
        "ENABLED": True,
        "ALLOW_GATE_UPGRADE": False,
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

    assert out["research_lab_detail"]["raw_entry_ok"] is True
    assert out["research_lab_entry_upgrade"] is False
    assert out["trigger_ok"] is False
    assert out["entry_ok"] is False


def test_engine_b_micro_breakout_requires_prev_close_inside_prior_range():
    candles = _micro_breakout_candles_long()
    candles[-2]["close"] = 100.8

    out = _engine_b_micro_breakout_value("LONG", candles, range_bars=18)

    assert out["passed"] is False
    assert out["signal"] == "no_break"


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


def test_structural_target_uses_dedicated_tp_buffer(monkeypatch):
    naked_cfg = dict(config.CONFIG.get("NAKED_ENGINE", {}) or {})
    naked_cfg["structural_tp_buffer_atr_mult"] = 0.25
    monkeypatch.setitem(config.CONFIG, "NAKED_ENGINE", naked_cfg)

    buffer_mult = _engine_b_structural_tp_buffer_atr_mult()
    long_zone = {"lower": 101.30, "upper": 101.80}
    short_zone = {"lower": 98.20, "upper": 98.70}

    assert buffer_mult == pytest.approx(0.25)
    assert _engine_b_structural_target_price(long_zone, "LONG", 1.0, buffer_mult) == pytest.approx(101.05)
    assert _engine_b_structural_target_price(short_zone, "SHORT", 1.0, buffer_mult) == pytest.approx(98.95)


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


