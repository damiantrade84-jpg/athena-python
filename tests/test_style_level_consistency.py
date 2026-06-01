"""Unit tests for style-aware TP/SL consistency paths."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from athena_app.services.candle_service import recompute_levels_for_style
from config import CONFIG
import engine_c
from engine_c import compute_consensus, normalise_engine_a, normalise_engine_b
from indicators import calc_levels


def test_calc_levels_uses_style_multipliers():
    price = 100.0
    atr = 2.0
    pair_type = "forex"

    scalp = calc_levels(price, atr, "LONG", pair_type, regime_state=1, style="scalp")
    intraday = calc_levels(
        price, atr, "LONG", pair_type, regime_state=1, style="intraday"
    )
    swing = calc_levels(price, atr, "LONG", pair_type, regime_state=1, style="swing")

    scalp_risk = abs(price - scalp["sl"])
    intraday_risk = abs(price - intraday["sl"])
    swing_risk = abs(price - swing["sl"])

    assert scalp_risk < intraday_risk < swing_risk
    assert scalp["tp2"] < intraday["tp2"] < swing["tp2"]


def test_calc_levels_exposes_base_and_effective_multipliers():
    price = 100.0
    atr = 2.0
    pair_type = "forex"
    style = "intraday"
    regime_state = 2

    levels = calc_levels(
        price, atr, "LONG", pair_type, regime_state=regime_state, style=style
    )

    base = CONFIG["STYLE_ATR_MULTS"][style][pair_type]
    regime_factor = levels["regimeFactor"]

    assert levels["mults_base"] == {
        "sl": float(base["sl"]),
        "tp1": float(base["tp1"]),
        "tp2": float(base["tp2"]),
    }
    assert levels["mults_effective"] == {
        "sl": float(base["sl"]) * regime_factor,
        "tp1": float(base["tp1"]) * regime_factor,
        "tp2": float(base["tp2"]) * regime_factor,
    }
    assert levels["mults"] == levels["mults_effective"]
    assert levels["sl"] == price - atr * levels["mults_effective"]["sl"]
    assert levels["tp1"] == price + atr * levels["mults_effective"]["tp1"]
    assert levels["tp2"] == price + atr * levels["mults_effective"]["tp2"]


def test_recompute_levels_for_style_keeps_forming_bar_parity_with_analyze_pair():
    pair_obj = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    sig = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "regime": {"label": "HIGH_VOLATILITY"},
    }

    d1 = [{"close": 1.0}] * 10
    h4 = [{"close": 1.0}] * 10
    h1 = [{"close": 1.0}] * 10

    captured = {"indicator_calls": [], "atr_style": None, "regime_state": None}

    def _resolve_pair(_sig):
        return pair_obj

    def _fetch(_pair, tf, _limit):
        return {"D1": d1, "H4": h4, "H1": h1}[tf]

    def _calc_indicators_with_normalized(candles, pair_type):
        captured["indicator_calls"].append((len(candles), pair_type))
        return {"snap": {"atr": 0.0012, "close": candles[-1]["close"]}}

    def _atr_for_levels(_d1i, _h4i, _h1i, **kwargs):
        captured["atr_style"] = kwargs.get("style")
        return 0.0012

    def _calc_levels(_price, _atr, _direction, _ptype, regime_state=None, style="swing"):
        captured["regime_state"] = regime_state
        return {"sl": 1.09, "tp1": 1.11, "tp2": 1.12, "rr1": 1.0, "rr2": 2.0}

    out = recompute_levels_for_style(
        sig,
        "intraday",
        resolve_pair_from_signal=_resolve_pair,
        fetch_candles=_fetch,
        calc_indicators_with_normalized=_calc_indicators_with_normalized,
        atr_for_levels=_atr_for_levels,
        calc_levels=_calc_levels,
        config={"D1_CANDLES": 10, "H4_CANDLES": 10, "H1_CANDLES": 10},
    )

    assert out["pip_mode"] == "intraday"
    assert captured["atr_style"] == "intraday"
    assert captured["regime_state"] == 2  # HIGH_VOLATILITY
    assert captured["indicator_calls"] == [
        (10, "forex"),
        (10, "forex"),
        (10, "forex"),
    ]


def test_recompute_levels_for_style_uses_level_atr_class_helper():
    pair_obj = {"display": "SPY", "symbol": "SPY", "type": "stock"}
    captured = {"level_class": None}

    def _calc_levels(_price, _atr, _direction, pair_type, **_kwargs):
        captured["level_class"] = pair_type
        return {"sl": 99.0, "tp1": 103.0, "tp2": 105.0, "rr1": 1.5, "rr2": 2.5}

    out = recompute_levels_for_style(
        {"pair": "SPY", "direction": "LONG", "price": 100.0},
        "intraday",
        resolve_pair_from_signal=lambda _sig: pair_obj,
        fetch_candles=lambda _pair, _tf, _limit: [{"close": 100.0}] * 10,
        calc_indicators_with_normalized=lambda candles, _ptype: {"snap": {"atr": 2.0}},
        atr_for_levels=lambda *_args, **_kwargs: 2.0,
        calc_levels=_calc_levels,
        config={"D1_CANDLES": 10, "H4_CANDLES": 10, "H1_CANDLES": 10},
        get_pair_level_atr_class=lambda _pair: "etf",
    )

    assert captured["level_class"] == "etf"
    assert out["levels"]["sl"] == 99.0


def test_recompute_levels_for_style_prefers_bybit_atr_for_crypto_levels():
    pair_obj = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
    captured = {"atr": None}

    def _calc_levels(_price, atr, _direction, _pair_type, **_kwargs):
        captured["atr"] = atr
        return {"sl": 95.0, "tp1": 110.0, "tp2": 120.0, "rr1": 2.0, "rr2": 4.0}

    recompute_levels_for_style(
        {"pair": "BTC/USDT", "direction": "LONG", "price": 100.0},
        "intraday",
        resolve_pair_from_signal=lambda _sig: pair_obj,
        fetch_candles=lambda _pair, _tf, _limit: [{"close": 100.0}] * 10,
        calc_indicators_with_normalized=lambda candles, _ptype: {"snap": {"atr": 1.0}},
        atr_for_levels=lambda *_args, **_kwargs: 1.0,
        calc_levels=_calc_levels,
        config={
            "D1_CANDLES": 10,
            "H4_CANDLES": 10,
            "H1_CANDLES": 10,
            "ENGINE_A_CRYPTO_LEVELS_FEED": "bybit",
            "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK": False,
        },
        bybit_atr_for_levels=lambda _pair, _style: 3.0,
    )

    assert captured["atr"] == 3.0


def test_recompute_levels_for_style_fails_closed_when_bybit_atr_missing():
    pair_obj = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}

    with pytest.raises(ValueError, match="Bybit ATR unavailable"):
        recompute_levels_for_style(
            {"pair": "BTC/USDT", "direction": "LONG", "price": 100.0},
            "intraday",
            resolve_pair_from_signal=lambda _sig: pair_obj,
            fetch_candles=lambda _pair, _tf, _limit: [{"close": 100.0}] * 10,
            calc_indicators_with_normalized=lambda candles, _ptype: {"snap": {"atr": 1.0}},
            atr_for_levels=lambda *_args, **_kwargs: 1.0,
            calc_levels=lambda *_args, **_kwargs: {},
            config={
                "D1_CANDLES": 10,
                "H4_CANDLES": 10,
                "H1_CANDLES": 10,
                "ENGINE_A_CRYPTO_LEVELS_FEED": "bybit",
                "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK": False,
            },
            bybit_atr_for_levels=lambda _pair, _style: None,
        )


def test_engine_c_normalise_engine_a_prefers_style_field():
    norm = normalise_engine_a(
        {
            "confluenceScore": 1.2,
            "maxScore": 3.0,
            "confluencePct": 40,
            "direction": "LONG",
            "style": "intraday",
            "tradeStyle": "swing",
        }
    )
    assert norm["style"] == "intraday"


def test_engine_c_normalise_engine_a_uses_raw_score_ratio_not_confluence_pct():
    norm = normalise_engine_a(
        {
            "confluenceScore": 1.5,
            "maxScore": 3.0,
            "confluencePct": 100,
            "direction": "LONG",
        }
    )
    assert norm["score_norm"] == 0.5


def test_engine_c_normalise_engine_a_disabled_trade_gate_is_not_a_signal():
    norm = normalise_engine_a(
        {
            "confluenceScore": 2.4,
            "maxScore": 3.0,
            "direction": "LONG",
            "engineATradeEnabled": False,
            "engineATradeGate": {"reason": "research-only"},
        }
    )

    assert norm["score_norm"] == 0.8
    assert norm["has_signal"] is False
    assert norm["has_partial_signal"] is False
    assert norm["trade_enabled"] is False


def test_engine_c_normalise_engine_a_missing_trade_gate_blocks_when_evidence_active(monkeypatch):
    monkeypatch.setitem(__import__("config").CONFIG, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED", True)
    norm = normalise_engine_a(
        {
            "confluenceScore": 2.4,
            "maxScore": 3.0,
            "direction": "LONG",
        }
    )
    assert norm["has_signal"] is False
    assert norm["has_partial_signal"] is False
    assert norm["trade_enabled"] is False


def test_engine_c_normalise_engine_a_missing_trade_gate_legacy_when_master_disabled(monkeypatch):
    monkeypatch.setitem(__import__("config").CONFIG, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED", False)
    norm = normalise_engine_a(
        {
            "confluenceScore": 2.4,
            "maxScore": 3.0,
            "direction": "LONG",
        }
    )
    assert norm["has_signal"] is True
    assert norm["trade_enabled"] is True


def test_engine_c_normalise_engine_b_uses_checklist_ratio_when_pct_missing():
    norm = normalise_engine_b(
        {
            "structural_verdict": "CLEAR",
            "direction": "LONG",
            "recommended_stop_loss": 99.0,
            "recommended_take_profit": 103.0,
        },
        confidence_b={"score": 4.0, "max_possible": 5.0, "pct": 0.0},
    )
    assert norm["score_norm"] == 0.8


def test_engine_c_consensus_blends_normalized_engine_scores(monkeypatch):
    monkeypatch.setattr(engine_c, "get_engine_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        engine_c, "get_dynamic_engine_weights", lambda *_args, **_kwargs: {"weights": None}
    )
    monkeypatch.setattr(
        engine_c,
        "predict_calibrated_prob",
        lambda *_args, **_kwargs: {"calibrated_prob": None},
    )
    monkeypatch.setattr(engine_c, "record_signal_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine_c, "apply_meta_policy", lambda result, _meta: result)

    result = compute_consensus(
        signal_a={
            "confluenceScore": 1.5,
            "maxScore": 3.0,
            "direction": "LONG",
            "sl": 99.0,
            "tp1": 103.0,
            "tp2": 105.0,
            "rr1": 2.0,
            "price": 100.0,
            "style": "swing",
        },
        signal_b={
            "structural_verdict": "CLEAR",
            "direction": "LONG",
            "recommended_stop_loss": 98.5,
            "recommended_take_profit": 104.0,
            "order_blocks": [],
        },
        confidence_b={
            "score": 4.0,
            "max_possible": 5.0,
            "pct": 0.0,
            "passed": True,
            "rr": 2.0,
            "structure_ok": True,
            "zone_ok": True,
            "trigger_ok": True,
        },
        asset_type="forex",
        regime="TRENDING",
        entry_price=100.0,
        atr=1.0,
    )

    assert result["components"]["a_norm"] == 0.5
    assert result["components"]["b_norm"] == 0.8
    # TRENDING weights updated C-1: A 0.65→0.40, B 0.35→0.60 (2026-04-21 audit)
    # conviction = 0.5*0.40 + 0.8*0.60 = 0.20 + 0.48 = 0.68
    assert result["conviction"] == 0.68


def test_engine_c_exposes_intermarket_multiplier_in_confirmation_payload(monkeypatch):
    monkeypatch.setattr(engine_c, "get_engine_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        engine_c, "get_dynamic_engine_weights", lambda *_args, **_kwargs: {"weights": None}
    )
    monkeypatch.setattr(
        engine_c,
        "predict_calibrated_prob",
        lambda *_args, **_kwargs: {"calibrated_prob": None},
    )
    monkeypatch.setattr(engine_c, "record_signal_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine_c, "apply_meta_policy", lambda result, _meta: result)
    monkeypatch.setitem(
        CONFIG,
        "INTERMARKET_CONFIRMATION",
        {
            **(CONFIG.get("INTERMARKET_CONFIRMATION", {}) or {}),
            "enabled": True,
            "engine_c_enabled": True,
            "engine_c_conviction_cap": 0.08,
        },
    )

    result = compute_consensus(
        signal_a={
            "confluenceScore": 1.5,
            "maxScore": 3.0,
            "direction": "LONG",
            "sl": 99.0,
            "tp1": 103.0,
            "tp2": 105.0,
            "rr1": 2.0,
            "price": 100.0,
            "style": "swing",
            "intermarketConfirmation": {
                "score": 0.5,
                "stable": True,
                "supportStrength": "moderate",
            },
        },
        signal_b={
            "structural_verdict": "CLEAR",
            "direction": "LONG",
            "recommended_stop_loss": 98.5,
            "recommended_take_profit": 104.0,
            "order_blocks": [],
        },
        confidence_b={
            "score": 4.0,
            "max_possible": 5.0,
            "pct": 0.0,
            "passed": True,
            "rr": 2.0,
            "structure_ok": True,
            "zone_ok": True,
            "trigger_ok": True,
        },
        asset_type="forex",
        regime="TRENDING",
        entry_price=100.0,
        atr=1.0,
    )

    assert result["intermarket_multiplier"] == 1.04
    assert result["intermarket_confirmation"]["engineCMultiplier"] == 1.04

def test_engine_c_aligned_uses_b_execution_levels_when_passed(monkeypatch):
    monkeypatch.setattr(engine_c, "get_engine_context", lambda *_a, **_k: {})
    monkeypatch.setattr(
        engine_c, "get_dynamic_engine_weights", lambda *_a, **_k: {"weights": None}
    )
    monkeypatch.setattr(
        engine_c,
        "predict_calibrated_prob",
        lambda *_a, **_k: {"calibrated_prob": None},
    )
    monkeypatch.setattr(engine_c, "record_signal_event", lambda *_a, **_k: None)
    monkeypatch.setattr(engine_c, "apply_meta_policy", lambda result, _meta: result)

    result = engine_c.compute_consensus(
        signal_a={
            "confluenceScore": 1.5,
            "maxScore": 3.0,
            "direction": "LONG",
            "sl": 97.0,
            "tp1": 103.0,
            "price": 100.0,
            "style": "intraday",
        },
        signal_b={
            "structural_verdict": "CLEAR",
            "direction": "LONG",
            "recommended_stop_loss": 99.0,
            "recommended_take_profit": 101.3,
            "order_blocks": [],
        },
        confidence_b={
            "score": 4.0,
            "max_possible": 5.0,
            "pct": 0.0,
            "passed": True,
            "execution_sl": 99.0,
            "execution_tp": 101.3,
            "structure_ok": True,
            "zone_ok": True,
            "trigger_ok": True,
        },
        asset_type="forex",
        regime="TRENDING",
        entry_price=100.0,
        atr=1.0,
    )
    assert result["verdict"] == "ALIGNED"
    assert result["sl"] == pytest.approx(99.0)
    assert result["tp"] == pytest.approx(101.3)
    assert result["rr"] == pytest.approx(1.3)
