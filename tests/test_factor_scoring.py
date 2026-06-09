import math
import sys
import time
import types

import pytest

import factor_scoring
from config import CONFIG
from factor_scoring import (
    _coherent_trend_score,
    _momentum_quality,
    _oi_addon,
    _previous_indicator_snap,
    build_oi_context_for_factor_scoring,
    compute_factor_scores,
    make_regime_smoothing_context,
)
from indicators import calc_indicators_with_normalized


def _snap(direction="long", *, adx=30.0, include_adx=True, momentum=None):
    if direction == "long":
        snap = {
            "ema21": 110.0,
            "ema50": 100.0,
            "ema200": 90.0,
            "close": 111.0,
            "atr": 1.0,
        }
    elif direction == "short":
        snap = {
            "ema21": 90.0,
            "ema50": 100.0,
            "ema200": 110.0,
            "close": 89.0,
            "atr": 1.0,
        }
    else:
        snap = {"close": 100.0, "atr": 1.0}

    if include_adx:
        snap["adx"] = adx
    if momentum == "bullish":
        snap.update({"rsi": 60.0, "macdHist": 0.5})
    elif momentum == "bearish":
        snap.update({"rsi": 40.0, "macdHist": -0.5})
    return snap


def _score(
    d1=None,
    h4=None,
    h1=None,
    *,
    pair=None,
    funding_rate=None,
    bar_time=None,
    volume_ratio=1.0,
    macro_context=None,
    intermarket_context=None,
    d1_candles=None,
    h4_candles=None,
    h1_candles=None,
    volume_threshold=None,
    oi_context=None,
    structure_result=None,
):
    return compute_factor_scores(
        d1_snap=d1 if d1 is not None else _snap("long"),
        h4_snap=h4 if h4 is not None else _snap("long"),
        h1_snap=h1 if h1 is not None else _snap("long"),
        pair=pair or {"type": "stock", "display": "TEST"},
        d1_candles=d1_candles or [],
        h4_candles=h4_candles or [],
        h1_candles=h1_candles or [],
        volume_ratio=volume_ratio,
        funding_rate=funding_rate,
        bar_time=bar_time,
        macro_context=macro_context,
        intermarket_context=intermarket_context,
        volume_threshold=volume_threshold,
        oi_context=oi_context,
        structure_result=structure_result,
    )


def _candles(n=80, *, trend=0.2, volume_trend=10.0):
    rows = []
    close = 100.0
    volume = 1000.0
    for i in range(n):
        close += trend
        volume += volume_trend
        rows.append({
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "vol": max(1.0, volume),
            "volume": max(1.0, volume),
        })
    return rows


def _stochastic_cross_candles():
    closes = [100.0] * 60 + [90.0, 90.0, 100.0, 90.0, 90.0, 103.0]
    return [
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "vol": 1000.0,
            "volume": 1000.0,
        }
        for close in closes
    ]


def test_momentum_quality_rsi_divergence_dampens_opposing_direction(monkeypatch):
    """EA-01: h4_candles must reach calc_rsi_divergence; result is dampened 0.4× (not zeroed)."""
    import indicators

    h4_snap = _snap("long", momentum="bullish")
    candles = _candles(80)

    monkeypatch.setattr(indicators, "calc_rsi_divergence", lambda c, lookback=None: None)
    mq_baseline_long = _momentum_quality(h4_snap, "LONG", "stock", h4_candles=candles)

    monkeypatch.setattr(indicators, "calc_rsi_divergence", lambda c, lookback=None: "bearish")
    mq_long = _momentum_quality(h4_snap, "LONG", "stock", h4_candles=candles)
    assert mq_long > 0.0  # no longer hard-zeroed
    assert mq_long < mq_baseline_long  # meaningfully reduced
    assert math.isclose(mq_long, mq_baseline_long * 0.4, rel_tol=1e-6)

    short_snap = _snap("short", momentum="bearish")
    monkeypatch.setattr(indicators, "calc_rsi_divergence", lambda c, lookback=None: None)
    mq_baseline_short = _momentum_quality(short_snap, "SHORT", "stock", h4_candles=candles)
    monkeypatch.setattr(indicators, "calc_rsi_divergence", lambda c, lookback=None: "bullish")
    mq_short = _momentum_quality(short_snap, "SHORT", "stock", h4_candles=candles)
    assert mq_short > 0.0
    assert math.isclose(mq_short, mq_baseline_short * 0.4, rel_tol=1e-6)


def test_momentum_quality_opposing_macd_can_penalize_below_neutral():
    h4_snap = _snap("long")
    h4_snap.update({"rsi": 70.0, "macdHist": -1.0})

    mq = _momentum_quality(h4_snap, "LONG", "stock", h4_candles=_candles(10))

    assert mq < 0.0


def test_momentum_quality_skips_divergence_without_enough_candles():
    h4_snap = _snap("long", momentum="bullish")
    short_candles = _candles(10)
    mq = _momentum_quality(h4_snap, "LONG", "stock", h4_candles=short_candles)
    assert mq > 0.0


def test_engine_a_rejects_non_finite_h4_atr():
    h4 = _snap("long", momentum="bullish")
    h4["atr"] = float("nan")

    out = _score(
        d1=_snap("long", momentum="bullish"),
        h4=h4,
        h1=_snap("long", momentum="bullish"),
        pair={"type": "stock", "display": "TEST"},
    )

    assert math.isfinite(out["final_score"])
    assert out["final_score"] == 0.0
    assert out["abort_reason"] == "atr_invalid_abort"
    assert out["feed_status"]["atr"] == "invalid"


def test_public_helpers_match_current_contract():
    ctx = make_regime_smoothing_context()
    assert set(ctx) == {"history", "committed", "lock"}

    oi_context = build_oi_context_for_factor_scoring(
        {"oiChange": 6.0, "ts": time.time()},
        [{"close": 100.0}, {"close": 105.0}],
        {"close": 110.0},
    )
    assert oi_context == {
        "oi_change_pct": 6.0,
        "price_change_pct": pytest.approx(10.0),
    }


def test_adx_hard_fail_blocks_to_zero_score():
    # Stage 1.2: ADX sigmoid replaced 3-tier step.
    # Hard abort now at ADX ≤ 10 (mult = 0.0), soft zone 10-30.
    result = _score(
        _snap("long", adx=10.0),
        _snap("long", adx=10.0),
        _snap("long", adx=10.0),
    )

    assert result["final_score"] == 0.0
    assert result["adx_multiplier"] == 0.0
    assert result["abort_reason"] == "adx_hard_abort"


def test_missing_adx_blocks_when_configured_fail_safe_enabled(monkeypatch):
    monkeypatch.setitem(CONFIG, "ADX_MISSING_BOTH_ABORT", True)
    result = _score(
        _snap("long", include_adx=False),
        _snap("long", include_adx=False),
        _snap("long", include_adx=False),
    )

    assert result["adx_source"] == "missing_both_abort"
    assert result["adx_multiplier"] == pytest.approx(0.0)
    assert result["final_score"] == 0.0
    assert result["abort_reason"] == "adx_missing_both_abort"


def test_missing_adx_can_use_legacy_soft_multiplier_when_explicitly_disabled(monkeypatch):
    monkeypatch.setitem(CONFIG, "ADX_MISSING_BOTH_ABORT", False)
    result = _score(
        _snap("long", include_adx=False),
        _snap("long", include_adx=False),
        _snap("long", include_adx=False),
    )

    assert result["adx_source"] == "missing"
    assert result["adx_multiplier"] == pytest.approx(0.5)
    assert result["final_score"] > 0.0


def test_crypto_combo_against_cap_matches_confirm_cap(monkeypatch):
    monkeypatch.setitem(CONFIG, "FACTOR_CRYPTO_ADDON_COMBO_CONFIRM_CAP", 0.25)
    monkeypatch.setitem(CONFIG, "FACTOR_CRYPTO_ADDON_COMBO_AGAINST_CAP", -0.25)

    value, addon_type, status = factor_scoring._asset_addon(
        {"type": "crypto", "display": "BTC/USDT"},
        "LONG",
        funding_rate=0.02,
        bar_time=None,
        oi_context={"oi_change_pct": 2.0, "price_change_pct": -1.0},
    )

    assert addon_type == "funding+oi"
    assert status == "ok"
    assert value == pytest.approx(-0.25)


def test_cot_unsupported_commodity_can_fallback_to_real_volume(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity"])
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_STOCK_VOLUME_ADDON",
        {"ENABLED": True, "ASSET_TYPES": ["stock", "index"]},
    )
    monkeypatch.setattr(factor_scoring, "_cot_formula_supported", lambda _display: False)

    value, addon_type, status = factor_scoring._asset_addon(
        {"type": "commodity", "display": "WTI Oil"},
        "LONG",
        funding_rate=None,
        bar_time=None,
        h4_candles=_candles(80, trend=0.4, volume_trend=25.0),
        volume_ratio=1.8,
    )

    assert addon_type == "volume"
    assert status == "ok"
    assert value > 0


def test_vwap_filter_crypto_only_when_enabled(monkeypatch):
    monkeypatch.setitem(
        factor_scoring.CONFIG,
        "ENGINE_A_VWAP_FILTER",
        {"ENABLED": True, "MAX_BOOST": 0.03, "MAX_PENALTY": -0.03, "CANDLE_LOOKBACK": 10},
    )
    candles = [{"close": 100.0, "high": 101.0, "low": 99.0, "volume": 1000, "confirmed": True}] * 12
    mult, detail = factor_scoring._vwap_direction_filter(candles, "LONG", "forex")
    assert mult == 1.0
    assert detail.get("reason") == "crypto_only"


def test_vwap_filter_skips_explicit_unfinalized_last_bar(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_VWAP_FILTER",
        {"ENABLED": True, "CANDLE_LOOKBACK": 3, "MAX_BOOST": 0.03, "MAX_PENALTY": -0.03},
    )
    candles = _candles(5)
    candles[-1]["is_final"] = False

    mult, detail = factor_scoring._vwap_direction_filter(candles, "LONG", "crypto")

    assert mult == pytest.approx(1.0)
    assert detail["applied"] is False
    assert detail["reason"] == "unfinalized_bar"


def test_zero_and_success_results_expose_signal_status_and_breakdown(monkeypatch):
    out = _score(
        d1=_snap("long", momentum="bullish"),
        h4=_snap("long", momentum="bullish"),
        h1=_snap("long", momentum="bullish"),
        h4_candles=_candles(80),
        pair={"type": "stock", "display": "TEST"},
    )

    assert out["signal_status"] == "ok"
    assert out["factor_breakdown"]["trend"] == out["factor_scores"]["trend"]

    z = factor_scoring._zero_result(
        {"type": "stock", "display": "TEST"},
        "RANGING",
        {},
        {},
        reason="adx_missing_both_abort",
        direction="LONG",
    )

    assert z["signal_status"] == "blocked"
    assert z["factor_breakdown"]["abort_reason"] == "adx_missing_both_abort"


def test_calc_confluence_hard_abort_confidence_is_zero(monkeypatch):
    """A hard-aborted Engine A signal must not regain confidence from proxy/session components."""
    monkeypatch.setitem(CONFIG, "ADX_MISSING_BOTH_ABORT", True)

    def _unexpected_confidence(**_kwargs):
        raise AssertionError("hard-abort confidence path should not call compute_confidence")

    monkeypatch.setattr("confidence_engine.compute_confidence", _unexpected_confidence)
    from scoring import calc_confluence

    d1 = {"snap": _snap("long", include_adx=False)}
    h4 = {"snap": _snap("long", include_adx=False)}
    h1 = {"snap": _snap("long", include_adx=False)}
    candles = _candles(trend=0.2, volume_trend=10.0)

    out = calc_confluence(
        d1,
        h4,
        h1,
        vr=1.0,
        stoch={"k": [], "d": []},
        pair={"type": "forex", "display": "EUR/USD"},
        btc_bias="neutral",
        d1_candles=candles,
        h4_candles=candles,
        h1_candles=candles,
    )

    assert out["confidence"] == pytest.approx(0.0)
    assert out["confidenceDetail"]["reason"] == "adx_missing_both_abort"
    assert out["factorDiagnostics"]["abortReason"] == "adx_missing_both_abort"
    assert any("ADX unavailable" in str(w) for w in out.get("warnings", []))


def test_forex_adx_source_uses_stronger_h4_when_configured(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_ADX_SOURCE_BY_CLASS",
        {"forex_majors": "max"},
    )

    result = _score(
        {"ema21": 110.0, "ema200": 100.0, "adx": 14.0, "close": 100.0, "atr": 1.0},
        {
            "ema21": 110.0,
            "ema50": 100.0,
            "adx": 30.0,
            "rsi": 55.0,
            "macdHist": 0.2,
            "close": 100.0,
            "atr": 1.0,
            "plusDI": 25.0,
            "minusDI": 15.0,
        },
        {"ema21": 110.0, "ema50": 100.0, "close": 100.0, "atr": 1.0},
        pair={"type": "forex", "display": "EUR/USD"},
    )

    assert result["adx_source"] == "h4"
    assert result["adx_multiplier"] == pytest.approx(1.0)


def test_forex_adx_gate_uses_score_group_threshold_band(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_ADX_SOURCE_BY_CLASS", {"forex_majors": "max"})
    monkeypatch.setitem(CONFIG, "ADX_TREND_MIN_CLASS", {"forex_majors": 20})
    monkeypatch.setitem(CONFIG, "FACTOR_ADX_HARD_FAIL_CLASS", {"forex_majors": 10})

    mult, adx, source = factor_scoring._adx_gate(
        {"adx": 12.0},
        {"adx": 15.0},
        "forex",
        score_group="forex_majors",
    )

    assert source == "h4"
    assert adx == pytest.approx(15.0)
    assert mult == pytest.approx(0.5)


def test_forex_directional_score_caps_single_h1_vote_to_one_point(monkeypatch):
    import carry_feed

    monkeypatch.setattr(carry_feed, "get_carry_z", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(carry_feed, "get_carry_differential", lambda _display: 0.0)
    # Isolate the single-vote cap from the magnitude scaler (tested separately).
    monkeypatch.setitem(CONFIG, "ENGINE_A_TREND_MAGNITUDE", {"ENABLED": False})

    result = _score(
        {"adx": 25.0, "close": 100.0, "atr": 1.0},
        {
            "adx": 25.0,
            "rsi": 55.0,
            "macdHist": 0.2,
            "close": 100.0,
            "atr": 1.0,
            "plusDI": 25.0,
            "minusDI": 15.0,
        },
        {"ema21": 101.0, "ema50": 100.0, "close": 101.0, "atr": 1.0},
        pair={"type": "forex", "display": "GBP/USD"},
    )

    assert result["direction"] == "LONG"
    assert result["directional_score"] == pytest.approx(1.0)
    assert result["trend_coherence"]["tf_coverage"] == pytest.approx(1.0 / 3.0, rel=1e-3)


def test_forex_trend_agreement_reports_exact_components():
    score, direction, detail = _coherent_trend_score(
        {"ema21": 110.0, "ema200": 100.0},
        {"ema21": 95.0, "ema50": 100.0},
        {"ema21": 110.0, "ema50": 100.0},
        "forex",
        score_group="forex_majors",
    )

    assert score == pytest.approx(1.2)
    assert direction == "LONG"
    assert detail["agreement_count"] == 2
    assert detail["coherence_ratio"] == pytest.approx(0.4)
    assert detail["agreement_components"] == ["d1_ema_trend", "ema_trend"]
    assert detail["vote_components"] == [
        {"component": "d1_ema_trend", "direction": "LONG", "weight": 0.5},
        {"component": "h4_ema_trend", "direction": "SHORT", "weight": 0.3},
        {"component": "ema_trend", "direction": "LONG", "weight": 0.2},
    ]


def _gbpusd_like_forex_snaps(*, close_inside_cluster=True):
    """D1 LONG + H1 LONG + H4 SHORT votes; optional H4 close inside EMA band."""
    d1 = {
        "ema21": 110.0,
        "ema200": 100.0,
        "adx": 25.0,
        "close": 100.0,
        "atr": 1.0,
    }
    h1 = {
        "ema21": 110.0,
        "ema50": 100.0,
        "close": 100.0,
        "atr": 1.0,
    }
    h4 = {
        "ema21": 98.0,
        "ema50": 100.0,
        "ema200": 101.0,
        "adx": 25.0,
        "atr": 1.0,
        "rsi": 55.0,
        "macdHist": 0.2,
        "plusDI": 25.0,
        "minusDI": 15.0,
    }
    if close_inside_cluster:
        h4["close"] = 99.5
    else:
        h4["close"] = 94.0
    return d1, h4, h1


def _full_alignment_clean_forex_snaps():
    """D1/H4/H1 all LONG with price above bullish H4 EMA stack."""
    d1 = {
        "ema21": 110.0,
        "ema200": 100.0,
        "adx": 25.0,
        "close": 111.0,
        "atr": 1.0,
    }
    h4 = {
        "ema21": 110.0,
        "ema50": 105.0,
        "ema200": 100.0,
        "adx": 25.0,
        "atr": 1.0,
        "close": 111.0,
        "rsi": 55.0,
        "macdHist": 0.2,
        "plusDI": 25.0,
        "minusDI": 15.0,
    }
    h1 = {
        "ema21": 110.0,
        "ema50": 105.0,
        "close": 111.0,
        "atr": 1.0,
    }
    return d1, h4, h1


def _full_alignment_contradiction_forex_snaps():
    """D1/H4/H1 all LONG but H4 close inside tight EMA cluster."""
    d1 = {
        "ema21": 110.0,
        "ema200": 100.0,
        "adx": 25.0,
        "close": 100.0,
        "atr": 1.0,
    }
    h1 = {
        "ema21": 110.0,
        "ema50": 100.0,
        "close": 100.0,
        "atr": 1.0,
    }
    h4 = {
        "ema21": 100.25,
        "ema50": 100.1,
        "ema200": 100.0,
        "adx": 25.0,
        "atr": 1.0,
        "close": 100.12,
        "rsi": 55.0,
        "macdHist": 0.2,
        "plusDI": 25.0,
        "minusDI": 15.0,
    }
    return d1, h4, h1


def _patch_forex_carry(monkeypatch):
    import carry_feed

    monkeypatch.setattr(carry_feed, "get_carry_z", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(carry_feed, "get_carry_differential", lambda _display: 0.0)


def test_gbpusd_like_cluster_diagnostics_show_risk(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    result = _score(d1, h4, h1, pair={"type": "forex", "display": "GBP/USD"})

    tc = result["trend_coherence"]
    assert tc["coherence_ratio"] == pytest.approx(0.4)
    assert tc["agreement_count"] == 2
    assert tc["price_inside_ema_cluster"] is True
    assert tc["at_or_below_resistance"] is True
    assert tc["clean_continuation"] is False
    assert tc["ema_cluster_penalty_applied"] is False
    assert result["adx_passed"] is True
    assert result["adx_timeframe_used"] in ("d1", "h4")


def test_gbpusd_like_score_unchanged_with_penalty_disabled(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)

    d1, h4_in, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    d1b, h4_out, h1b = _gbpusd_like_forex_snaps(close_inside_cluster=False)

    inside = _score(d1, h4_in, h1, pair={"type": "forex", "display": "GBP/USD"})
    outside = _score(d1b, h4_out, h1b, pair={"type": "forex", "display": "GBP/USD"})

    assert inside["directional_score"] == pytest.approx(outside["directional_score"])
    assert inside["final_score"] == pytest.approx(outside["final_score"])


def test_gbpusd_like_penalty_soft_cap_when_enabled(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    baseline = _score(d1, h4, h1, pair={"type": "forex", "display": "GBP/USD"})
    assert baseline["final_score"] > 0.0
    cap = baseline["final_score"] * 0.75

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "soft_cap")
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_MAX_SCORE_CAP", cap)
    penalized = _score(d1, h4, h1, pair={"type": "forex", "display": "GBP/USD"})

    assert penalized["trend_coherence"]["ema_cluster_penalty_applied"] is True
    assert penalized["trend_coherence"]["ema_cluster_penalty_reason"] == "ema_cluster_soft_cap"
    assert penalized["final_score"] <= cap
    assert penalized["final_score"] < baseline["final_score"]


def test_gbpusd_like_penalty_multiplier_when_enabled(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    baseline = _score(d1, h4, h1, pair={"type": "forex", "display": "GBP/USD"})

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MULT", 0.85)
    penalized = _score(d1, h4, h1, pair={"type": "forex", "display": "GBP/USD"})

    assert penalized["trend_coherence"]["ema_cluster_penalty_reason"] == "ema_cluster_multiplier"
    assert penalized["final_score"] < baseline["final_score"]


def test_clean_long_above_stack_not_penalized(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)

    d1 = _snap("long", adx=25.0)
    h4 = _snap("long", adx=25.0, momentum="bullish")
    h1 = _snap("long")
    result = _score(d1, h4, h1, pair={"type": "forex", "display": "EUR/USD"})

    assert result["direction"] == "LONG"
    assert result["trend_coherence"]["clean_continuation"] is True
    assert result["trend_coherence"]["ema_cluster_penalty_applied"] is False


def test_clean_short_below_stack_not_penalized(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)

    d1 = _snap("short", adx=25.0)
    h4 = _snap("short", adx=25.0, momentum="bearish")
    h1 = _snap("short")
    result = _score(d1, h4, h1, pair={"type": "forex", "display": "EUR/USD"})

    assert result["direction"] == "SHORT"
    assert result["trend_coherence"]["clean_continuation"] is True
    assert result["trend_coherence"]["ema_cluster_penalty_applied"] is False


def test_engine_b_fields_do_not_affect_cluster_diagnostics(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    base_pair = {"type": "forex", "display": "GBP/USD"}
    contaminated_pair = {
        **base_pair,
        "engine_b_score": 5.0,
        "engine_b_bias": "SHORT",
        "bos_confirmed": True,
        "ob_at_zone": True,
        "engine_b_confidence": 1.0,
    }

    base = _score(d1, h4, h1, pair=base_pair)
    contaminated = _score(d1, h4, h1, pair=contaminated_pair)

    cluster_keys = (
        "price_inside_ema_cluster",
        "at_or_below_resistance",
        "ema_cluster_width_atr",
        "nearest_ema_resistance_distance_atr",
        "price_vs_ema21",
    )
    for key in cluster_keys:
        assert contaminated["trend_coherence"][key] == base["trend_coherence"][key]


def test_gbpusd_like_default_config_applies_multiplier_penalty(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    result = _score(d1, h4, h1, pair={"type": "forex", "display": "GBP/USD"})

    tc = result["trend_coherence"]
    assert tc["coherence_ratio"] == pytest.approx(0.4)
    assert tc["ema_cluster_penalty_applied"] is True
    assert tc["ema_cluster_penalty_reason"] == "ema_cluster_multiplier"
    assert result["feed_status"].get("ema_cluster_penalty") == "ema_cluster_multiplier"


def test_gbpusd_like_default_penalty_reduces_final_score_by_multiplier(monkeypatch):
    _patch_forex_carry(monkeypatch)
    mult = float(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MULT", 0.85))

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    pair = {"type": "forex", "display": "GBP/USD"}

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)
    baseline = _score(d1, h4, h1, pair=pair)

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")
    penalized = _score(d1, h4, h1, pair=pair)

    assert penalized["final_score"] == pytest.approx(baseline["final_score"] * mult, rel=1e-3)
    assert penalized["directional_score"] == pytest.approx(baseline["directional_score"])


def test_full_alignment_clean_location_not_penalized_by_default(monkeypatch):
    _patch_forex_carry(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")

    d1, h4, h1 = _full_alignment_clean_forex_snaps()
    result = _score(d1, h4, h1, pair={"type": "forex", "display": "GBP/JPY"})

    tc = result["trend_coherence"]
    assert tc["agreement_count"] == 3
    assert tc["coherence_ratio"] == pytest.approx(1.0)
    assert tc["clean_continuation"] is True
    assert tc["ema_cluster_penalty_applied"] is False


def test_full_alignment_strong_contradiction_penalized_by_default(monkeypatch):
    _patch_forex_carry(monkeypatch)
    mult = float(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MULT", 0.85))

    d1, h4, h1 = _full_alignment_contradiction_forex_snaps()
    pair = {"type": "forex", "display": "GBP/JPY"}

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)
    baseline = _score(d1, h4, h1, pair=pair)

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")
    penalized = _score(d1, h4, h1, pair=pair)

    tc = penalized["trend_coherence"]
    assert tc["agreement_count"] == 3
    assert tc["coherence_ratio"] == pytest.approx(1.0)
    assert tc["strong_price_contradiction"] is True
    assert tc["clean_continuation"] is False
    assert tc["ema_cluster_penalty_applied"] is True
    assert tc["ema_cluster_penalty_reason"] == "ema_cluster_multiplier"
    assert penalized["final_score"] == pytest.approx(baseline["final_score"] * mult, rel=1e-3)


def test_non_forex_scoring_unchanged_when_forex_penalty_enabled(monkeypatch):
    stock_pair = {"type": "stock", "display": "AAPL"}
    crypto_pair = {"type": "crypto", "display": "BTC/USDT"}

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")
    stock_on = _score(pair=stock_pair, h4=_snap("long", momentum="bullish"))
    crypto_on = _score(pair=crypto_pair, h4=_snap("long", momentum="bullish"), funding_rate=0.0001)

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)
    stock_off = _score(pair=stock_pair, h4=_snap("long", momentum="bullish"))
    crypto_off = _score(pair=crypto_pair, h4=_snap("long", momentum="bullish"), funding_rate=0.0001)

    assert stock_on["final_score"] == pytest.approx(stock_off["final_score"])
    assert crypto_on["final_score"] == pytest.approx(crypto_off["final_score"])


def test_ema_cluster_penalty_applied_once_not_double(monkeypatch):
    _patch_forex_carry(monkeypatch)
    mult = float(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MULT", 0.85))

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    pair = {"type": "forex", "display": "GBP/USD"}

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)
    baseline = _score(d1, h4, h1, pair=pair)

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")
    penalized = _score(d1, h4, h1, pair=pair)

    assert penalized["directional_score"] == pytest.approx(baseline["directional_score"])
    assert penalized["directional_ramp_multiplier"] == pytest.approx(baseline["directional_ramp_multiplier"])
    assert penalized["final_score"] / baseline["final_score"] == pytest.approx(mult, rel=1e-3)
    assert penalized["feed_status"].get("ema_cluster_penalty") == "ema_cluster_multiplier"


def test_soft_cap_inactive_under_default_multiplier_mode(monkeypatch):
    _patch_forex_carry(monkeypatch)
    mult = float(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MULT", 0.85))
    cap = float(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_MAX_SCORE_CAP", 2.0))

    d1, h4, h1 = _gbpusd_like_forex_snaps(close_inside_cluster=True)
    pair = {"type": "forex", "display": "GBP/USD"}

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)
    baseline = _score(d1, h4, h1, pair=pair)

    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "multiplier")
    penalized = _score(d1, h4, h1, pair=pair)

    expected = baseline["final_score"] * mult
    assert penalized["final_score"] == pytest.approx(expected, rel=1e-3)
    assert penalized["trend_coherence"]["ema_cluster_penalty_reason"] == "ema_cluster_multiplier"
    assert penalized["final_score"] > cap or expected <= cap


def test_forex_score_ignores_engine_b_fields_on_pair(monkeypatch):
    import carry_feed

    monkeypatch.setattr(carry_feed, "get_carry_z", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(carry_feed, "get_carry_differential", lambda _display: 0.0)

    base_pair = {"type": "forex", "display": "GBP/USD"}
    contaminated_pair = {
        **base_pair,
        "engine_b_score": 5.0,
        "engine_b_bias": "SHORT",
        "bos_confirmed": True,
        "ob_at_zone": True,
        "engine_b_confidence": 1.0,
    }

    base = _score(pair=base_pair)
    contaminated = _score(pair=contaminated_pair)

    assert contaminated["final_score"] == pytest.approx(base["final_score"])
    assert contaminated["direction"] == base["direction"]
    assert contaminated["directional_score"] == pytest.approx(base["directional_score"])


def test_forex_score_reachability_restored_without_lowering_threshold(monkeypatch):
    import carry_feed
    from scoring import get_score_threshold

    pair = {"type": "forex", "display": "EUR/USD"}
    floors = dict(CONFIG.get("ENGINE_A_CONVICTION_FLOOR_BY_CLASS") or {})
    floors["forex_majors"] = 0.60
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CONVICTION_FLOOR_BY_CLASS", floors)
    monkeypatch.setattr(carry_feed, "get_carry_z", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(carry_feed, "get_carry_differential", lambda _display: 0.0)

    result = _score(
        {"ema21": 110.0, "ema200": 100.0, "adx": 25.0, "close": 100.0, "atr": 1.0},
        {
            "ema21": 110.0,
            "ema50": 100.0,
            "adx": 25.0,
            "rsi": 55.0,
            "macdHist": 0.2,
            "close": 100.0,
            "atr": 1.0,
            "plusDI": 25.0,
            "minusDI": 15.0,
        },
        {"ema21": 110.0, "ema50": 100.0, "close": 100.0, "atr": 1.0},
        pair=pair,
    )

    assert get_score_threshold(pair) == pytest.approx(2.1)
    assert result["engine_a_asset_diagnostics"]["conviction_floor"] == pytest.approx(0.60)
    assert result["final_score"] >= get_score_threshold(pair)


def test_full_bullish_alignment_is_stronger_than_partial_alignment():
    full = _score(_snap("long"), _snap("long"), _snap("long"))
    d1_only = _score(_snap("long"), {}, {})

    assert full["direction"] == "LONG"
    assert d1_only["direction"] == "LONG"
    assert full["final_score"] > d1_only["final_score"]
    assert full["factor_scores"]["trend"] > d1_only["factor_scores"]["trend"]


def test_single_tf_trend_coverage_uses_configured_class_max_weight(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "INDICATOR_WEIGHTS",
        {
            "trend": {
                "stock": {
                    "d1_ema_trend": 0.4,
                    "h4_ema_trend": 0.35,
                    "ema_trend": 0.25,
                }
            }
        },
    )

    score, direction, detail = _coherent_trend_score(_snap("long"), {}, {}, "stock")

    assert direction == "LONG"
    assert score > 0
    assert detail["tf_coverage"] == pytest.approx(0.3333)


def test_full_bearish_alignment_is_stronger_than_partial_alignment():
    full = _score(_snap("short"), _snap("short"), _snap("short"))
    d1_only = _score(_snap("short"), {}, {})

    assert full["direction"] == "SHORT"
    assert d1_only["direction"] == "SHORT"
    assert full["final_score"] > d1_only["final_score"]
    assert abs(full["factor_scores"]["trend"]) > abs(d1_only["factor_scores"]["trend"])


def test_h4_h1_against_d1_perfect_tie_returns_no_direction():
    full_short = _score(_snap("short"), _snap("short"), _snap("short"))
    conflicted = _score(_snap("long"), _snap("short"), _snap("short"))

    assert full_short["direction"] == "SHORT"
    assert conflicted["direction"] is None
    assert conflicted["final_score"] == 0.0
    assert conflicted["trend_coherence"]["weighted_tf_tie"] is True
    assert conflicted["trend_coherence"]["error"] == "weighted_tf_tie"
    assert abs(conflicted["factor_scores"]["trend"]) < abs(full_short["factor_scores"]["trend"])


def test_bullish_rsi_macd_momentum_increases_long_conviction():
    neutral = _score(_snap("long"), _snap("long"), _snap("long"))
    bullish = _score(_snap("long"), _snap("long", momentum="bullish"), _snap("long"))

    assert bullish["direction"] == "LONG"
    assert bullish["momentum_quality"] > neutral["momentum_quality"]
    assert bullish["conviction"] > neutral["conviction"]
    assert bullish["final_score"] > neutral["final_score"]


def test_bearish_rsi_macd_momentum_supports_short_direction():
    neutral = _score(_snap("short"), _snap("short"), _snap("short"))
    bearish = _score(_snap("short"), _snap("short", momentum="bearish"), _snap("short"))

    assert bearish["direction"] == "SHORT"
    assert bearish["momentum_quality"] > neutral["momentum_quality"]
    assert bearish["conviction"] > neutral["conviction"]
    assert bearish["final_score"] > neutral["final_score"]


def test_zero_macd_histogram_does_not_fallback_to_macd_line_z():
    neutral = _momentum_quality(
        {"rsi": 60.0, "macdHist": 0.0, "macdLine_z": 3.0},
        "LONG",
        "stock",
    )
    confirming = _momentum_quality(
        {"rsi": 60.0, "macdHist": 0.5, "macdLine_z": -3.0},
        "LONG",
        "stock",
    )

    assert neutral < confirming


def test_oi_addon_covers_symmetric_long_and_short_quadrants():
    assert factor_scoring._ADDON_AGAINST == pytest.approx(-0.25)
    assert _oi_addon({"oi_change_pct": -4.0, "price_change_pct": -2.0}, "SHORT") == pytest.approx(0.20)
    assert _oi_addon({"oi_change_pct": 4.0, "price_change_pct": -2.0}, "LONG") == pytest.approx(-0.25)
    assert _oi_addon({"oi_change_pct": -4.0, "price_change_pct": 2.0}, "LONG") == pytest.approx(0.0)


def test_missing_d1_ema200_is_flagged_instead_of_using_ema50():
    d1 = _snap("long")
    d1.pop("ema200")
    h4 = _snap("short")
    h1 = _snap("short")

    result = _score(d1=d1, h4=h4, h1=h1)

    assert result["direction"] == "SHORT"
    assert result["trend_coherence"]["d1_ema200_missing"] is True
    assert "d1" not in result["trend_coherence"]


def test_compute_factor_scores_uses_prior_candle_snapshots_for_hysteresis():
    current_d1 = _snap("long")
    current_h4 = _snap("long")
    current_h1 = _snap("long")
    flat_candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "vol": 1000.0}
        for _ in range(240)
    ]

    result = _score(
        current_d1,
        current_h4,
        current_h1,
        d1_candles=flat_candles,
        h4_candles=flat_candles,
        h1_candles=flat_candles,
    )

    assert result["final_score"] == 0.0
    assert result["abort_reason"] == "indeterminate_trend"
    assert result["trend_coherence"]["hysteresis_prev_available"] == {
        "d1": True,
        "h4": True,
        "h1": True,
    }


def _trending_forex_candles(n: int = 120, *, start: float = 1.10, step: float = 0.0008):
    """Monotonic uptrend series long enough for EMA200 on D1-style windows."""
    candles = []
    price = start
    for i in range(n):
        candles.append(
            {
                "open": price,
                "high": price + 0.0005,
                "low": price - 0.0003,
                "close": price,
                "vol": 1000.0,
                "time": f"2026-01-{(i % 28) + 1:02d}T12:00:00+00:00",
            }
        )
        price += step
    return candles


def test_previous_indicator_snap_matches_live_analyze_pair_indicator_path():
    """Regression: prev bar must use calc_indicators_with_normalized + score_group."""
    candles = _trending_forex_candles(120)
    score_group = "forex_majors"
    prev = _previous_indicator_snap(candles, pair_id="EUR/USD", tf="H4", score_group=score_group, asset_type="forex")
    expected = calc_indicators_with_normalized(
        candles[:-1], "forex", score_group=score_group
    ).get("snap")
    assert isinstance(prev, dict)
    assert isinstance(expected, dict)
    for key in ("ema21", "ema50", "ema200"):
        assert prev.get(key) == expected.get(key)


def test_forex_trending_candles_not_indeterminate_when_group_adjustments_on(monkeypatch):
    """May 2026 regression: path-split hysteresis aborted all forex trend votes."""
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    candles = _trending_forex_candles(240)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD"}
    d1 = calc_indicators_with_normalized(candles, "forex", score_group="forex_majors")
    h4 = calc_indicators_with_normalized(candles, "forex", score_group="forex_majors")
    h1 = calc_indicators_with_normalized(candles, "forex", score_group="forex_majors")
    result = compute_factor_scores(
        d1["snap"],
        h4["snap"],
        h1["snap"],
        pair,
        candles,
        candles,
        candles,
        1.0,
    )
    assert result.get("abort_reason") != "indeterminate_trend"
    assert result.get("direction") in ("LONG", "SHORT")


def test_intermarket_confirmation_adjusts_engine_a_when_enabled(monkeypatch):
    cfg = {
        **(CONFIG.get("INTERMARKET_CONFIRMATION", {}) or {}),
        "enabled": True,
        "engine_a_enabled": True,
        "engine_a_score_cap": 0.18,
        "lead_lag_enabled": False,
    }
    monkeypatch.setitem(CONFIG, "INTERMARKET_CONFIRMATION", cfg)
    context = {
        "drivers": [
            {
                "driver": "DXY",
                "driverAssetClass": "macro",
                "sourceType": "both",
                "priorRelation": "inverse",
                "effectivePriorRelation": "inverse",
                "summary": {
                    "regime": {"relation": "inverse", "label": "strongly inverse"},
                    "current": {
                        "correlation": -0.82,
                        "stability": 0.91,
                        "signPersistence": 0.88,
                        "volAdjustedScore": -0.70,
                        "targetRecentChangePct": 1.1,
                        "driverRecentChangePct": -1.4,
                        "window": 50,
                        "lastBarContradiction": False,
                        "flippedRecently": False,
                    }
                },
            }
        ],
        "unavailablePriors": [],
    }

    baseline = _score(pair={"type": "forex", "display": "EUR/USD"})
    adjusted = _score(
        pair={"type": "forex", "display": "EUR/USD"},
        intermarket_context=context,
    )

    assert adjusted["intermarket_confirmation"]["verdict"] == "supportive"
    assert adjusted["intermarket_engine_a_delta"] > 0.0
    assert adjusted["final_score"] > baseline["final_score"]


def test_crypto_addon_conviction_positive_zero_negative_ordering():
    pair = {"type": "crypto", "display": "BTC/USDT"}

    positive = _score(pair=pair, funding_rate=-0.0002)
    zero = _score(pair=pair, funding_rate=0.0001)
    negative = _score(pair=pair, funding_rate=0.0010)

    assert positive["addon_value"] == pytest.approx(0.20)
    assert zero["addon_value"] == pytest.approx(0.0)
    assert negative["addon_value"] == pytest.approx(-0.25)
    assert positive["conviction"] > zero["conviction"] > negative["conviction"]
    assert positive["final_score"] > zero["final_score"] > negative["final_score"]


def test_crypto_funding_and_oi_same_side_can_exceed_single_addon_cap(monkeypatch):
    monkeypatch.setitem(CONFIG, "FACTOR_CRYPTO_ADDON_COMBO_CONFIRM_CAP", 0.25)
    pair = {"type": "crypto", "display": "BTC/USDT"}

    result = _score(
        pair=pair,
        funding_rate=-0.0002,
        oi_context={"oi_change_pct": 4.0, "price_change_pct": 2.0},
    )

    assert result["addon_value"] == pytest.approx(0.25)


def test_stock_enrichment_flags_are_reported_advisory_only(monkeypatch):
    monkeypatch.setitem(CONFIG, "INSIDER_TRADING_DIAGNOSTIC_ENABLED", True)
    monkeypatch.setitem(CONFIG, "FUNDAMENTALS_DIAGNOSTIC_ENABLED", True)

    result = _score(pair={"type": "stock", "display": "AAPL"})

    assert result["feed_status"]["insider_trading"] == "advisory_only"
    assert result["feed_status"]["fundamentals"] == "advisory_only"


def test_research_lab_factor_config_gate_can_disable_scoring(monkeypatch):
    cfg = dict(CONFIG.get("ENGINE_A_RESEARCH_LAB_FACTORS", {}))
    cfg["ENABLED"] = False
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "forex", "display": "EUR/AUD"},
        d1_candles=_candles(),
    )

    assert result["factor_scores"]["research_lab"] == 0.0
    assert result["research_lab_detail"]["enabled"] is False


def test_research_lab_factor_adds_bounded_candidate_context(monkeypatch):
    # Stage 2.6: Research lab uses universal factors — GROUPS config ignored.
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "forex", "display": "EUR/AUD"},
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    assert result["research_lab_value"] == pytest.approx(0.15)
    assert result["factor_scores"]["research_lab"] == pytest.approx(0.15)
    assert result["research_lab_detail"]["score_group"] == "universal"
    assert result["research_lab_detail"]["components"]["obv_divergence"]["signal"] == "confirming"
    assert "research_lab" in result["active_nondirectional_factors"]


def test_research_lab_class_profile_is_config_gated(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["obv_divergence", "price_momentum"],
    }
    class_cfg = {
        "us_stock_single": {
            "BONUS": 0.05,
            "PENALTY": -0.04,
            "MAX_ABS": 0.05,
            "FACTORS": ["obv_divergence"],
        }
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS_BY_CLASS", class_cfg)
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)

    baseline = _score(
        pair={"type": "stock", "display": "AAPL"},
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    adjusted = _score(
        pair={"type": "stock", "display": "AAPL"},
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    assert baseline["research_lab_detail"]["class_adjusted"] is False
    assert adjusted["research_lab_detail"]["class_adjusted"] is True
    assert adjusted["research_lab_detail"]["profile_source"] == "score_group:us_stock_single"
    assert adjusted["research_lab_detail"]["allowed"] == ["obv_divergence"]
    assert adjusted["research_lab_value"] == pytest.approx(0.05)


def test_research_lab_class_profile_falls_back_to_asset_type(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["obv_divergence"],
    }
    class_cfg = {
        "commodity": {
            "BONUS": 0.07,
            "PENALTY": -0.04,
            "MAX_ABS": 0.07,
            "FACTORS": ["obv_divergence"],
        }
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS_BY_CLASS", class_cfg)
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)

    result = _score(
        pair={"type": "commodity", "display": "UNKNOWN_COMMODITY"},
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    assert result["research_lab_detail"]["profile_source"] == "asset_type:commodity"
    assert result["research_lab_detail"]["class_adjusted"] is True
    assert result["research_lab_value"] == pytest.approx(0.07)


def test_research_lab_class_profile_uses_default_when_no_class_match(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["obv_divergence"],
    }
    class_cfg = {
        "default": {
            "BONUS": 0.04,
            "PENALTY": -0.02,
            "MAX_ABS": 0.04,
            "FACTORS": ["obv_divergence"],
        }
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS_BY_CLASS", class_cfg)
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)

    result = _score(
        pair={"type": "stock", "display": "UNKNOWN_STOCK"},
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    assert result["research_lab_detail"]["profile_source"] == "default"
    assert result["research_lab_detail"]["class_adjusted"] is True
    assert result["research_lab_value"] == pytest.approx(0.04)


def test_research_lab_addon_is_clamped_to_addon_ceiling(monkeypatch):
    # Stage 1.4: _ADDON_CONFIRM = 0.20; when post-research total exceeds the
    # single-signal band, the wider combo cap (0.25) applies.
    cfg = {
        "ENABLED": True,
        "BONUS": 0.20,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "crypto", "display": "BTC/USDT"},
        funding_rate=-0.0002,
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    assert result["research_lab_value"] == pytest.approx(0.20)
    assert result["addon_value"] == pytest.approx(0.25)


def test_research_lab_stochastic_candidate_applies_only_to_confirmed_h4_alt_allowlist(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["stochastic_cross"],
        "STOCHASTIC_CROSS": {
            "ENABLED": True,
            "PAPER_TOOL_ONLY": True,
            "ASSET_TYPES": ["crypto"],
            "SYMBOLS": ["AVAX/USDT", "SOL/USDT", "LINK/USDT"],
            "TIMEFRAME": "H4",
            "K_PERIODS": [5, 14],
            "K_SMOOTH": 3,
            "D_SMOOTH": 3,
        },
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "crypto", "display": "AVAX/USDT"},
        h4_candles=_stochastic_cross_candles(),
        d1_candles=_candles(n=80, trend=0.0),
    )

    assert result["research_lab_value"] == pytest.approx(0.15)
    detail = result["research_lab_detail"]["components"]["stochastic_cross"]
    assert detail["signal"] == "bull_cross"
    assert detail["timeframe"] == "H4"
    assert detail["paper_tool_only"] is True


def test_research_lab_stochastic_candidate_allows_btc_and_eth_when_symbol_allowlist_is_empty(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["stochastic_cross"],
        "STOCHASTIC_CROSS": {
            "ENABLED": True,
            "PAPER_TOOL_ONLY": True,
            "ASSET_TYPES": ["crypto"],
            "SYMBOLS": [],
            "TIMEFRAME": "H4",
            "K_PERIODS": [5, 14],
            "K_SMOOTH": 3,
            "D_SMOOTH": 3,
        },
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    for symbol in ("BTC/USDT", "ETH/USDT"):
        result = _score(
            pair={"type": "crypto", "display": symbol},
            h4_candles=_stochastic_cross_candles(),
        )

        assert result["research_lab_value"] == pytest.approx(0.15)
        detail = result["research_lab_detail"]["components"]["stochastic_cross"]
        assert detail["signal"] == "bull_cross"


def test_research_lab_stochastic_candidate_still_rejects_non_crypto(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["stochastic_cross"],
        "STOCHASTIC_CROSS": {
            "ENABLED": True,
            "PAPER_TOOL_ONLY": True,
            "ASSET_TYPES": ["crypto"],
            "SYMBOLS": [],
            "TIMEFRAME": "H4",
            "K_PERIODS": [5, 14],
            "K_SMOOTH": 3,
            "D_SMOOTH": 3,
        },
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "forex", "display": "EUR/USD"},
        h4_candles=_stochastic_cross_candles(),
    )

    assert result["research_lab_value"] == pytest.approx(0.0)
    detail = result["research_lab_detail"]["components"]["stochastic_cross"]
    assert detail["signal"] == "out_of_scope"
    assert detail["reason"] == "asset_type_not_enabled"


def test_weights_report_effective_values_when_addon_is_unsupported():
    result = _score(pair={"type": "stock", "display": "TEST"})

    assert result["addon_unsupported"] is True
    assert result["weights"]["addon"] == pytest.approx(0.0)
    assert "base" in result["weights"]


def test_stock_index_cot_proxy_formulas_are_config_gated(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity", "index", "stock"])
    monkeypatch.setattr(
        "factor_scoring._cot_addon_with_status",
        lambda *_args, **_kwargs: (0.20, "ok"),
    )

    result = _score(pair={"type": "stock", "display": "SPY"})

    assert result["addon_type"] == "cot_proxy"
    assert result["addon_unsupported"] is False
    assert result["feed_status"]["addon"] == "cot_proxy:ok"


def test_commodity_without_cot_formula_is_explicitly_unsupported(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity", "index", "stock"])

    result = _score(pair={"type": "commodity", "display": "Cocoa"})

    assert result["addon_type"] == "cot"
    assert result["addon_unsupported"] is True
    assert result["feed_status"]["addon"] == "cot:unsupported"


def test_pair_volume_threshold_drives_volume_adjustment():
    loose = _score(volume_ratio=2.0, volume_threshold=1.1)
    strict = _score(volume_ratio=2.0, volume_threshold=2.5)

    assert loose["final_score"] > strict["final_score"]


def test_research_lab_factor_supports_commodity_group_candidates(monkeypatch):
    # Stage 2.6: Universal factors — aroon_trend is legacy but still callable.
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["aroon_trend"],
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "commodity", "display": "XAG/USD"},
        d1_candles=_candles(trend=0.2, volume_trend=0.0),
    )

    assert result["research_lab_value"] == pytest.approx(0.0)
    assert result["research_lab_detail"]["score_group"] == "universal"
    assert result["research_lab_detail"]["components"]["aroon_trend"]["signal"] == "unsupported_legacy"


def test_calc_confluence_factor_diagnostics_includes_research_lab(monkeypatch):
    """research_lab_* from compute_factor_scores must appear on API-bound factorDiagnostics."""
    from scoring import calc_confluence

    # Stage 2.6: Universal factors — GROUPS ignored.
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    pair = {"type": "forex", "display": "EUR/AUD"}
    d1 = {"snap": _snap("long")}
    h4 = {"snap": _snap("long")}
    h1 = {"snap": _snap("long")}
    candles = _candles(trend=0.2, volume_trend=10.0)
    out = calc_confluence(
        d1,
        h4,
        h1,
        vr=1.0,
        stoch={"k": [], "d": []},
        pair=pair,
        btc_bias="neutral",
        d1_candles=candles,
        h4_candles=candles,
        h1_candles=candles,
    )
    fd = out["factorDiagnostics"]
    assert fd.get("researchLabValue") == pytest.approx(0.15)
    detail = fd.get("researchLabDetail") or {}
    assert detail.get("score_group") == "universal"
    asset_diag = fd.get("engineAAssetDiagnostics") or {}
    assert asset_diag.get("score_group") == "forex_crosses"
    assert "factor_weights" in asset_diag
    assert "volatility" in asset_diag
    assert detail.get("components", {}).get("obv_divergence", {}).get("signal") == "confirming"
    dual = fd.get("regimeLabelsDualCapture") or {}
    assert dual.get("trendState") == out["trendState"]
    assert dual.get("factorRegime") == out.get("regimeName")


def test_calc_confluence_warns_when_adx_missing_soft_multiplier(monkeypatch):
    """Legacy soft ADX path surfaces an explicit warning (fail-closed uses ADX_MISSING_BOTH_ABORT)."""
    monkeypatch.setitem(CONFIG, "ADX_MISSING_BOTH_ABORT", False)
    from scoring import calc_confluence

    pair = {"type": "forex", "display": "EUR/USD"}
    d1 = {"snap": _snap("long", include_adx=False)}
    h4 = {"snap": _snap("long", include_adx=False)}
    h1 = {"snap": _snap("long", include_adx=False)}
    candles = _candles(trend=0.2, volume_trend=10.0)
    out = calc_confluence(
        d1,
        h4,
        h1,
        vr=1.0,
        stoch={"k": [], "d": []},
        pair=pair,
        btc_bias="neutral",
        d1_candles=candles,
        h4_candles=candles,
        h1_candles=candles,
    )
    assert any("ADX unavailable" in str(w) for w in out.get("warnings", []))


def test_forex_raw_carry_penalty_removed_but_addon_still_uses_carry(monkeypatch):
    import carry_feed

    monkeypatch.setattr(carry_feed, "get_carry_z", lambda *_args, **_kwargs: 1.0)

    def boom(_display):
        raise AssertionError("cost penalty must not read raw carry differential")

    monkeypatch.setattr(carry_feed, "get_carry_differential", boom)
    result = _score(pair={"type": "forex", "display": "EUR/USD"})
    assert result["addon_type"] == "carry"
    assert result["addon_value"] == pytest.approx(0.20)
    assert result["feed_status"].get("addon") == "carry:ok"
    removed_key = "forex" + "_carry_cost"
    assert removed_key not in result["feed_status"]


def test_forex_carry_addon_error_remains_advisory_without_cost_status(monkeypatch):
    import carry_feed

    def boom(*_args, **_kwargs):
        raise RuntimeError("carry unavailable")

    monkeypatch.setattr(carry_feed, "get_carry_z", boom)
    result = _score(pair={"type": "forex", "display": "EUR/USD"})
    assert result["feed_status"].get("addon") == "carry:error"
    removed_key = "forex" + "_carry_cost"
    assert removed_key not in result["feed_status"]


def test_conviction_floor_default_is_explicit_and_no_momentum_uses_floor_blend(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)
    floor = float(CONFIG["FACTOR_CONVICTION_FLOOR"])
    result = _score(_snap("long"), _snap("long"), _snap("long"))

    # floor is config-driven (default 0.60 in config.yaml, _CONVICTION_FLOOR_DEFAULT = 0.20 in code)
    assert floor > 0.0
    # With no momentum (macdHist=0, rsi neutral), stock/index pair has no addon so the
    # ADDON_UNSUPPORTED_SPLIT_TO_BASE rule redistributes addon weight: half to base,
    # half to momentum.  Effective base weight becomes base + addon * split_to_base.
    base_w = float(CONFIG.get("FACTOR_BASE_WEIGHT", 0.20))
    addon_w = float(CONFIG.get("FACTOR_ADDON_WEIGHT", 0.30))
    split = float(CONFIG.get("ADDON_UNSUPPORTED_SPLIT_TO_BASE", 0.0))
    expected_base = base_w + addon_w * split
    assert result["conviction"] == pytest.approx(expected_base)
    # final_score depends on trend_score * adx * vol_scaler * di_align * (floor + (1-floor)*conviction)
    # Just verify it's in valid range and formula is consistent
    assert 0.0 < result["final_score"] < 3.0
    # Verify the formula components are present
    assert result["factor_scores"]["momentum"] == pytest.approx(0.0)


class TestRegimeFloorSensitivity:
    """#4: an explicit class conviction floor should NOT fully clobber the
    RANGING/HIGH_VOLATILITY floor reduction for regime-sensitive classes.

    Forex (and any class not in ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES)
    keeps its flat class floor for score reachability; stock/index/commodity/crypto
    re-apply the reduced regime floor in noisy regimes so weak momentum counts less.
    """

    SENSITIVE = ["crypto", "stock", "index", "commodity"]

    def _set(self, monkeypatch):
        monkeypatch.setitem(
            CONFIG, "ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES", self.SENSITIVE
        )

    def test_forex_keeps_flat_floor_in_ranging(self, monkeypatch):
        from factor_scoring import _apply_regime_floor_sensitivity
        self._set(monkeypatch)
        assert _apply_regime_floor_sensitivity(0.60, "RANGING", "forex_majors", "forex", 0.10) == pytest.approx(0.60)

    def test_stock_reduced_in_ranging(self, monkeypatch):
        from factor_scoring import _apply_regime_floor_sensitivity
        self._set(monkeypatch)
        assert _apply_regime_floor_sensitivity(0.22, "RANGING", "us_stock_single", "stock", 0.10) == pytest.approx(0.10)

    def test_index_reduced_in_high_volatility(self, monkeypatch):
        from factor_scoring import _apply_regime_floor_sensitivity
        self._set(monkeypatch)
        assert _apply_regime_floor_sensitivity(0.24, "HIGH_VOLATILITY", "us_indices_trackers", "index", 0.10) == pytest.approx(0.10)

    def test_crypto_reduced_in_ranging(self, monkeypatch):
        from factor_scoring import _apply_regime_floor_sensitivity
        self._set(monkeypatch)
        assert _apply_regime_floor_sensitivity(0.15, "RANGING", "crypto_btc", "crypto", 0.10) == pytest.approx(0.10)

    def test_sensitive_class_unchanged_in_trending(self, monkeypatch):
        from factor_scoring import _apply_regime_floor_sensitivity
        self._set(monkeypatch)
        assert _apply_regime_floor_sensitivity(0.22, "TRENDING", "us_stock_single", "stock", 0.10) == pytest.approx(0.22)

    def test_only_lowers_never_raises(self, monkeypatch):
        from factor_scoring import _apply_regime_floor_sensitivity
        self._set(monkeypatch)
        # class floor already below the regime floor → keep the lower class floor
        assert _apply_regime_floor_sensitivity(0.05, "RANGING", "stock", "stock", 0.10) == pytest.approx(0.05)


def test_final_score_is_clamped_to_zero_to_three_contract():
    high = _score(
        _snap("long"),
        _snap("long", momentum="bullish"),
        _snap("long"),
        pair={"type": "crypto", "display": "BTC/USDT"},
        funding_rate=-0.0002,
    )
    blocked = _score(
        _snap("long", adx=1.0),
        _snap("long", adx=1.0),
        _snap("long", adx=1.0),
    )

    assert 0.0 <= high["final_score"] <= 3.0
    assert 0.0 <= blocked["final_score"] <= 3.0


def test_directional_gate_hard_cut_aborts_below_min(monkeypatch):
    """abs(trend_score) below FACTOR_MIN_DIRECTIONAL → final_score=0,
    min_directional_failed=True, abort_reason='min_directional_failed'."""
    # Force the threshold above the engine's structural minimum (~0.40 from a
    # single H1 vote) so the hard-cut path is exercised on a normal trend.
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)
    monkeypatch.setitem(CONFIG, "FACTOR_MIN_DIRECTIONAL", 5.0)
    monkeypatch.setitem(CONFIG, "FACTOR_DIRECTIONAL_SOFT_SPAN", 0.0)
    result = _score(_snap("long"), _snap("long"), _snap("long"))
    assert result["final_score"] == 0.0
    assert result["min_directional_failed"] is True
    assert result["abort_reason"] == "min_directional_failed"
    assert result["min_directional_threshold"] == pytest.approx(5.0)


def test_directional_gate_soft_span_scales_base_score(monkeypatch):
    """abs(trend_score) inside [min, min+span] → ramp multiplier between 0 and 1
    is multiplied into base_score; outside the span the multiplier is 1.0."""
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)
    # Strong trend (all three TFs aligned) sits well above the span → mult=1.0.
    full = _score(_snap("long"), _snap("long"), _snap("long"))
    assert full["directional_ramp_multiplier"] == pytest.approx(1.0)
    assert full["min_directional_failed"] is False

    # Position the span so the actual trend_score lands inside it.
    abs_trend = abs(full["directional_score"])
    monkeypatch.setitem(CONFIG, "FACTOR_MIN_DIRECTIONAL", abs_trend - 0.10)
    monkeypatch.setitem(CONFIG, "FACTOR_DIRECTIONAL_SOFT_SPAN", 0.40)
    ramped = _score(_snap("long"), _snap("long"), _snap("long"))
    expected_mult = 0.10 / 0.40
    assert ramped["directional_ramp_multiplier"] == pytest.approx(expected_mult, abs=1e-3)
    # Ramp scales base_score → ramped final_score is strictly less than full.
    assert 0.0 < ramped["final_score"] < full["final_score"]


def test_score_group_overrides_asset_type_for_rsi_bounds(monkeypatch):
    """Score-group entry beats asset_type when both are present in RSI_BOUNDS.

    Canonical case: GLD carried as type=stock with score_group=precious_trackers
    should pick up the commodity-style 75/25 RSI bounds rather than the equity 70/30.
    """
    # Force a RSI value that lies in different zones depending on bounds:
    # RSI 72 is overbought under stock (70/30) → -0.25, but neutral-confirming
    # under precious_trackers (75/25) → +0.50.  Different mom_quality → different score.
    snap = _snap("long")
    snap["rsi"] = 72.0
    snap["macdHist"] = 0.0  # isolate RSI contribution

    stock_pair = {"type": "stock", "display": "AAPL"}
    gold_pair = {"type": "stock", "display": "GLD", "score_group": "precious_trackers"}

    stock = _score(snap, snap, snap, pair=stock_pair)
    gold = _score(snap, snap, snap, pair=gold_pair)

    # GLD interpreted via precious_trackers RSI bounds confirms momentum;
    # AAPL via stock RSI bounds penalises it.
    assert gold["momentum_quality"] > stock["momentum_quality"]


def test_directional_gate_uses_crypto_specific_thresholds():
    """Crypto reads FACTOR_MIN_DIRECTIONAL_CRYPTO and the _CRYPTO span."""
    pair = {"type": "crypto", "display": "BTC/USDT"}
    result = _score(_snap("long"), _snap("long"), _snap("long"), pair=pair)
    expected_min = float(CONFIG.get("FACTOR_MIN_DIRECTIONAL_CRYPTO", 0.15))
    expected_span = float(CONFIG.get("FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO", 0.30))
    assert result["min_directional_threshold"] == pytest.approx(expected_min)
    assert result["effective_min_directional"] == pytest.approx(expected_min + expected_span)


def test_volatility_scaler_replaces_session_multiplier():
    # Volatility scaler is now per-class (VOLATILITY_SCALER_BANDS) — each asset
    # class has its own low/high ATR% boundaries because forex H4 ATR% lives near
    # 0.1% while crypto routinely sits at 1-2%.  This test confirms the scaler
    # correctly boosts inside its class's low band and penalises above the high band.

    # Forex band: low=0.0005, high=0.0025.  ATR/close = 0.0001/1.0 = 0.01% < 0.05% → boost.
    low_vol_d1 = {"ema21": 110.0, "ema200": 100.0, "adx": 25.0, "close": 1.0, "atr": 0.0001, "plusDI": 25.0, "minusDI": 15.0}
    low_vol_h4 = {"ema21": 110.0, "ema50": 100.0, "adx": 25.0, "rsi": 55.0, "macdHist": 0.0, "close": 1.0, "atr": 0.0001, "plusDI": 25.0, "minusDI": 15.0}
    low_vol_h1 = {"ema21": 110.0, "ema50": 100.0, "close": 1.0, "atr": 0.0001}
    low_vol = _score(
        d1=low_vol_d1, h4=low_vol_h4, h1=low_vol_h1,
        pair={"type": "forex", "display": "TEST/FX"},
    )
    # Stock band: low=0.005, high=0.020.  ATR/close = 5.0/100.0 = 5% > 2% → penalty.
    high_vol_d1 = {"ema21": 110.0, "ema200": 100.0, "adx": 25.0, "close": 100.0, "atr": 5.0, "plusDI": 25.0, "minusDI": 15.0}
    high_vol_h4 = {"ema21": 110.0, "ema50": 100.0, "adx": 25.0, "rsi": 55.0, "macdHist": 0.0, "close": 100.0, "atr": 5.0, "plusDI": 25.0, "minusDI": 15.0}
    high_vol_h1 = {"ema21": 110.0, "ema50": 100.0, "close": 100.0, "atr": 5.0}
    high_vol = _score(
        d1=high_vol_d1, h4=high_vol_h4, h1=high_vol_h1,
        pair={"type": "stock", "display": "AAPL"},
    )

    # Session multiplier is 1.0 unless FACTOR_FOREX_SESSION_MULT.ENABLED (forex only).
    assert low_vol["session_multiplier"] == pytest.approx(1.0)
    assert high_vol["session_multiplier"] == pytest.approx(1.0)
    # Low vol gets boosted, high vol gets penalised
    assert float(low_vol.get("feed_status", {}).get("vol_scaler", 1.0)) > 1.0
    assert float(high_vol.get("feed_status", {}).get("vol_scaler", 1.0)) < 1.0


def test_volume_macro_and_intermarket_context_affect_score_bounded():
    # Phase 2 parameter wiring: volume_ratio, macro_context, intermarket_context
    # now contribute small bounded adjustments (±5% max) to the final score.
    baseline = _score(pair={"type": "stock", "display": "AAPL"})

    # Extreme volume_ratio (>1.5) → +3% boost capped
    high_vol = _score(
        pair={"type": "stock", "display": "AAPL"},
        volume_ratio=999.0,
    )
    # Low volume_ratio (<0.5) → -3% penalty capped
    low_vol = _score(
        pair={"type": "stock", "display": "AAPL"},
        volume_ratio=0.1,
    )
    # risk_on macro → +2% boost
    risk_on = _score(
        pair={"type": "stock", "display": "AAPL"},
        macro_context={"state": "risk_on"},
    )
    # risk_off macro → -2% penalty
    risk_off = _score(
        pair={"type": "stock", "display": "AAPL"},
        macro_context={"state": "risk_off"},
    )
    # Intermarket divergence → -2% penalty
    div = _score(
        pair={"type": "stock", "display": "AAPL"},
        intermarket_context={"divergence": True},
    )
    # Combined: all three at extremes → ±5% total cap
    combined = _score(
        pair={"type": "stock", "display": "AAPL"},
        volume_ratio=999.0,
        macro_context={"state": "risk_on"},
        intermarket_context={"divergence": True, "divergence_score": 1.0},
    )

    _base = baseline["final_score"]
    # volume_ratio=999 → capped at +3% boost
    assert high_vol["final_score"] == pytest.approx(_base * 1.03, abs=1e-4)
    # volume_ratio=0.1 → (0.1-0.5)*0.06 = -0.024 (proportional, not full -3%)
    assert low_vol["final_score"] == pytest.approx(_base * 0.976, abs=1e-3)
    # macro risk_on → +2%
    assert risk_on["final_score"] == pytest.approx(_base * 1.02, abs=1e-4)
    # macro risk_off → -2%
    assert risk_off["final_score"] == pytest.approx(_base * 0.98, abs=1e-4)
    # intermarket divergence → -2%
    assert div["final_score"] == pytest.approx(_base * 0.98, abs=1e-4)
    # Combined: +3% vol +2% macro -2% inter = +3% (capped at +5%)
    assert combined["final_score"] == pytest.approx(_base * 1.03, abs=1e-4)
    # factor_scores unchanged (adjustments apply to final_score only)
    assert high_vol["factor_scores"] == baseline["factor_scores"]


def test_compute_factor_scores_populates_filtered_indicators_for_confidence_engine():
    out = _score()
    fi = out.get("filtered_indicators")
    assert isinstance(fi, dict)
    assert len(fi) >= 1


def test_calc_confluence_exposes_di_align_mult_for_ui():
    from scoring import calc_confluence

    d1 = {"snap": {"ema21": 110.0, "ema200": 100.0, "adx": 25.0, "close": 1.0, "atr": 0.0001}}
    h4 = {
        "snap": {
            "ema21": 110.0,
            "ema50": 100.0,
            "adx": 25.0,
            "rsi": 55.0,
            "macdHist": 0.0,
            "close": 1.0,
            "atr": 0.0001,
            "plusDI": 10.0,
            "minusDI": 30.0,
        }
    }
    h1 = {"snap": {"ema21": 110.0, "ema50": 100.0, "rsi": 55.0, "macdHist": 0.0, "close": 1.0}}
    out = calc_confluence(
        d1,
        h4,
        h1,
        1.0,
        {},
        {"display": "EUR/USD", "type": "forex"},
        "neutral",
    )
    fd = out.get("factorDiagnostics") or {}
    assert fd.get("diAlignMult") == pytest.approx(0.35)
    assert fd.get("feedStatus", {}).get("di_align") == "0.35"


def test_di_alignment_conflict_is_diagnostic_not_info_log(caplog):
    d1 = {"ema21": 110.0, "ema200": 100.0, "adx": 25.0, "close": 1.0, "atr": 0.0001}
    h4 = {
        "ema21": 110.0,
        "ema50": 100.0,
        "adx": 25.0,
        "rsi": 55.0,
        "macdHist": 0.0,
        "close": 1.0,
        "atr": 0.0001,
        "plusDI": 10.0,
        "minusDI": 30.0,
    }
    h1 = {"ema21": 110.0, "ema50": 100.0, "rsi": 55.0, "macdHist": 0.0, "close": 1.0}
    result = compute_factor_scores(
        d1,
        h4,
        h1,
        {"display": "EUR/CHF", "type": "forex"},
        [],
        [],
        [],
        1.0,
    )

    # DI alignment conflict is a configurable soft penalty, not a hard abort.
    assert result["feed_status"]["di_align"] == "0.35"
    assert result["final_score"] > 0.0
    assert "DI alignment conflict" not in caplog.text


def test_zero_result_carries_empty_filtered_indicators():
    from factor_scoring import _zero_result

    z = _zero_result(
        {"type": "stock", "display": "TEST"},
        "RANGING",
        {},
        {"adx": "ok"},
        reason="test",
    )
    assert z.get("filtered_indicators") == {}


def test_score_group_adjustments_default_disabled_preserves_score(monkeypatch):
    pair = {"type": "stock", "display": "AAPL"}
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)
    baseline = _score(pair=pair, h4=_snap("long", momentum="bullish"))

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_FACTOR_WEIGHTS_BY_CLASS",
        {"us_stock_single": {"momentum": 0.0, "addon": 0.0, "base": 1.0}},
    )
    unchanged = _score(pair=pair, h4=_snap("long", momentum="bullish"))

    assert unchanged["final_score"] == baseline["final_score"]
    assert unchanged["weights"] == baseline["weights"]
    assert unchanged["engine_a_asset_diagnostics"]["score_group_adjustments_enabled"] is False


def test_score_group_adjustments_change_stock_only_when_enabled(monkeypatch):
    pair = {"type": "stock", "display": "AAPL"}
    baseline = _score(pair=pair)

    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_FACTOR_WEIGHTS_BY_CLASS",
        {"us_stock_single": {"momentum": 0.05, "addon": 0.0, "base": 0.95}},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_ADDON_UNSUPPORTED_SPLIT_BY_CLASS", {"stock": 0.0})
    adjusted = _score(pair=pair)

    assert adjusted["score_group"] == "us_stock_single"
    assert adjusted["weights"]["base"] == pytest.approx(0.95)
    assert adjusted["weights"]["momentum"] == pytest.approx(0.05)
    assert adjusted["final_score"] != baseline["final_score"]
    assert adjusted["engine_a_asset_diagnostics"]["factor_weights"]["configured"]["base"] == pytest.approx(0.95)


def test_score_group_adjustments_do_not_change_forex_or_crypto_without_matching_maps(monkeypatch):
    forex_pair = {"type": "forex", "display": "EUR/USD"}
    crypto_pair = {"type": "crypto", "display": "BTC/USDT"}

    # The class-keyed maps now carry real forex/crypto entries (EMA/RSI/weights/floor),
    # so isolate the invariant under test by neutralizing every gated map to target
    # only us_stock_single/stock. With no forex/crypto entry, enabling adjustments must
    # leave them on universal defaults — identical to the adjustments-disabled baseline.
    for key in (
        "ENGINE_A_EMA_PERIODS_BY_CLASS",
        "ENGINE_A_RSI_PERIOD_BY_CLASS",
        "ENGINE_A_MACD_PARAMS_BY_CLASS",
        "ENGINE_A_CONVICTION_FLOOR_BY_CLASS",
        "ENGINE_A_ADDON_UNSUPPORTED_SPLIT_BY_CLASS",
        "ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS",
        "ENGINE_A_RESEARCH_LAB_FACTORS_BY_CLASS",
        "ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES",
    ):
        monkeypatch.setitem(CONFIG, key, {})
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_FACTOR_WEIGHTS_BY_CLASS",
        {"us_stock_single": {"momentum": 0.05, "addon": 0.0, "base": 0.95}},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS", {"stock": {"min_directional": 0.9, "soft_span": 0.1}})

    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)
    forex_base = _score(pair=forex_pair, h4=_snap("long", momentum="bullish"))
    crypto_base = _score(pair=crypto_pair, h4=_snap("long", momentum="bullish"), funding_rate=0.0001)

    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    forex_grouped = _score(pair=forex_pair, h4=_snap("long", momentum="bullish"))
    crypto_grouped = _score(pair=crypto_pair, h4=_snap("long", momentum="bullish"), funding_rate=0.0001)

    assert forex_grouped["final_score"] == forex_base["final_score"]
    assert crypto_grouped["final_score"] == crypto_base["final_score"]


def test_volume_momentum_spread_is_class_adjustment_gated(monkeypatch):
    pair = {"type": "crypto", "display": "BTC/USDT"}
    h4 = _snap("long")
    h4["volume_momentum_spread"] = 1.0
    monkeypatch.setitem(
        CONFIG,
        "INDICATOR_WEIGHTS",
        {
            "momentum": {
                "crypto": {"rsi_z": 0.4, "macdLine_z": 0.2, "volume_momentum_spread": 0.4}
            }
        },
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)
    baseline = _score(pair=pair, h4=h4, funding_rate=0.0001)

    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    aligned = _score(pair=pair, h4=h4, funding_rate=0.0001)
    opposed_h4 = dict(h4)
    opposed_h4["volume_momentum_spread"] = -1.0
    opposed = _score(pair=pair, h4=opposed_h4, funding_rate=0.0001)

    assert aligned["momentum_quality"] > baseline["momentum_quality"]
    assert opposed["momentum_quality"] < aligned["momentum_quality"]


def test_volatility_regime_adjustment_is_config_gated(monkeypatch):
    pair = {"type": "stock", "display": "AAPL"}
    monkeypatch.setattr(
        factor_scoring,
        "detect_regime",
        lambda *args, **kwargs: {"regime": "HIGH_VOLATILITY"},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_VOLATILITY_REGIME_ADJUSTMENT_ENABLED", False)
    baseline = _score(pair=pair, h4=_snap("long", momentum="bullish"))

    monkeypatch.setitem(CONFIG, "ENGINE_A_VOLATILITY_REGIME_ADJUSTMENT_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_VOLATILITY_REGIME_MULTIPLIERS", {"stock": {"HIGH_VOLATILITY": 0.90}})
    monkeypatch.setitem(CONFIG, "ENGINE_A_VOLATILITY_REGIME_MULT_BOUNDS", {"min": 0.80, "max": 1.10})
    adjusted = _score(pair=pair, h4=_snap("long", momentum="bullish"))

    assert adjusted["volatility_regime_multiplier"] == pytest.approx(0.9)
    assert adjusted["final_score"] < baseline["final_score"]
    assert adjusted["engine_a_asset_diagnostics"]["volatility"]["regime_multiplier"]["applied"] is True


def test_equity_session_liquidity_weighting_is_config_gated(monkeypatch):
    pair = {"type": "stock", "display": "AAPL"}
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_EQUITY_SESSION_LIQUIDITY_WEIGHTING",
        {
            "ENABLED": True,
            "ASSET_TYPES": ["stock", "index"],
            "ACTIVE_UTC_START_HOUR": 13.5,
            "ACTIVE_UTC_END_HOUR": 20.0,
            "ACTIVE_MULT": 1.02,
            "OFF_HOURS_MULT": 0.98,
        },
    )

    active = _score(pair=pair, h4=_snap("long", momentum="bullish"), bar_time="2026-05-13T14:00:00+00:00")
    off_hours = _score(pair=pair, h4=_snap("long", momentum="bullish"), bar_time="2026-05-13T02:00:00+00:00")

    assert active["equity_session_multiplier"] == pytest.approx(1.02)
    assert off_hours["equity_session_multiplier"] == pytest.approx(0.98)
    assert active["final_score"] > off_hours["final_score"]


def test_engine_a_structure_context_adjustment_is_optional(monkeypatch):
    pair = {"type": "stock", "display": "AAPL"}
    structure = {
        "structural_verdict": "CLEAR",
        "zone_touched": True,
        "ob_at_zone": True,
        "fvg_overlap": True,
        "engine_b_independent_direction": "LONG",
        "asset_type": "stock",
    }

    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_ENABLED", False)
    baseline = _score(pair=pair, h4=_snap("long", momentum="bullish"), structure_result=structure)

    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_INFLUENCE_LEVEL", "standard")
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS", {"min": 0.85, "max": 1.50})
    adjusted = _score(pair=pair, h4=_snap("long", momentum="bullish"), structure_result=structure)

    assert baseline["structure_context_adjustment"]["enabled"] is False
    assert adjusted["structure_context_adjustment"]["applied"] is True
    assert adjusted["final_score"] > baseline["final_score"]


def test_engine_a_structure_context_remains_default_disabled():
    assert CONFIG.get("ENGINE_A_STRUCTURE_CONTEXT_ENABLED") is False


def test_engine_a_correlated_overlay_guard_disabled_preserves_current_score(monkeypatch):
    pair = {"type": "stock", "display": "AAPL"}
    candles = _candles(trend=0.2, volume_trend=10.0)
    structure = {
        "structural_verdict": "CLEAR",
        "zone_touched": True,
        "ob_at_zone": True,
        "fvg_overlap": True,
        "engine_b_independent_direction": "LONG",
        "asset_type": "stock",
    }
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_RESEARCH_LAB_FACTORS",
        {"ENABLED": True, "BONUS": 0.15, "PENALTY": -0.10, "MAX_ABS": 0.20, "FACTORS": ["obv_divergence"]},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CORRELATED_OVERLAY_GUARD_ENABLED", False)
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS", {"min": 0.85, "max": 1.50})

    current = _score(
        pair=pair,
        h4=_snap("long", momentum="bullish"),
        d1_candles=candles,
        h4_candles=candles,
        structure_result=structure,
    )

    assert current["engine_a_correlated_overlay_guard"]["warning"] is True
    assert current["engine_a_correlated_overlay_guard"]["capped"] is False
    assert current["final_score"] == pytest.approx(
        current["structure_context_adjustment"]["correlated_overlay_guard"]["input_adjusted_score"],
        abs=1e-4,
    )


def test_engine_a_correlated_overlay_guard_enabled_caps_combined_uplift(monkeypatch):
    pair = {"type": "stock", "display": "AAPL"}
    candles = _candles(trend=0.2, volume_trend=10.0)
    structure = {
        "structural_verdict": "CLEAR",
        "zone_touched": True,
        "ob_at_zone": True,
        "fvg_overlap": True,
        "liquidity_sweep": True,
        "engine_b_independent_direction": "LONG",
        "asset_type": "stock",
    }
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_RESEARCH_LAB_FACTORS",
        {"ENABLED": True, "BONUS": 0.15, "PENALTY": -0.10, "MAX_ABS": 0.20, "FACTORS": ["obv_divergence"]},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CORRELATED_OVERLAY_GUARD_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CORRELATED_OVERLAY_MAX_TOTAL_UPLIFT", 0.05)
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_B_STRUCTURE_SCORE_MULT_BOUNDS", {"min": 0.85, "max": 1.50})

    capped = _score(
        pair=pair,
        h4=_snap("long", momentum="bullish"),
        d1_candles=candles,
        h4_candles=candles,
        structure_result=structure,
    )
    guard = capped["engine_a_correlated_overlay_guard"]

    assert guard["warning"] is True
    assert guard["capped"] is True
    assert guard["combined_positive_uplift"] > guard["max_total_positive_uplift"]
    assert capped["final_score"] < guard["input_adjusted_score"]
    assert capped["feed_status"]["correlated_overlay_guard"] == "capped"


def _trx_like_crypto_snaps():
    """TRX/USDT H4-like bullish EMA stack with weak ADX and stretched price."""
    base = {
        "ema21": 0.35781,
        "ema50": 0.35495,
        "ema200": 0.34,
        "close": 0.36342,
        "atr": 0.0091,
        "adx": 21.66,
        "rsi": 62.0,
        "macdHist": 0.4,
        "plusDI": 24.0,
        "minusDI": 16.0,
    }
    d1 = dict(base)
    d1["ema200"] = 0.33
    h4 = dict(base)
    h1 = dict(base)
    return d1, h4, h1


def _trx_like_h4_candles(n: int = 100):
    """Flat low VWAP anchor + late rally; confirms 3TF hysteresis and sub-MA volume."""
    rows = []
    ramp_start = n - 25
    for i in range(n):
        if i < ramp_start:
            close = 0.302
            vol = 5.8e6
        elif i < n - 1:
            close = 0.31 + ((i - ramp_start) / max(n - ramp_start - 1, 1)) * 0.05342
            vol = 5.5e6
        else:
            close = 0.36342
            vol = 3.9e6
        rows.append(
            {
                "open": close - 0.001,
                "high": close + 0.002,
                "low": close - 0.002,
                "close": close,
                "vol": vol,
                "volume": vol,
            }
        )
    return rows


def _enable_crypto_late_trend(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_ADX_STRONG_THRESHOLD", 25)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_MULT", 0.85)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_VWAP_EXTENSION_ATR_WARN", 3.0)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_VOLUME_MA_LOOKBACK", 20)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_RESEARCH_LAB_FACTORS",
        {"ENABLED": False, "BONUS": 0.15, "PENALTY": -0.10, "MAX_ABS": 0.20, "FACTORS": []},
    )


def _trx_like_score_inputs():
    """3TF LONG votes (no hysteresis block) + H4 TRX prices for late-trend diagnostics."""
    d1 = _snap("long", adx=21.66, momentum="bullish")
    h1 = _snap("long", adx=21.66, momentum="bullish")
    h4 = _snap("long", adx=21.66, momentum="bullish")
    h4.update(
        {
            "close": 0.36342,
            "ema21": 0.35781,
            "ema50": 0.35495,
            "atr": 0.0091,
            "rsi": 62.0,
            "macdHist": 0.4,
            "plusDI": 24.0,
            "minusDI": 16.0,
        }
    )
    candles = _trx_like_h4_candles()
    pair = {"type": "crypto", "display": "TRX/USDT"}
    return d1, h4, h1, candles, pair


def test_trx_like_late_trend_reduces_score_direction_stays_long(monkeypatch):
    _enable_crypto_late_trend(monkeypatch)
    d1, h4, h1, candles, pair = _trx_like_score_inputs()

    _funding = -0.0002
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", False)
    baseline = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
        volume_ratio=0.7,
        funding_rate=_funding,
    )

    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", True)
    penalized = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
        volume_ratio=0.7,
        funding_rate=_funding,
    )

    diag = penalized["crypto_engine_a_diagnostics"]
    assert diag is not None
    assert diag["crypto_late_trend_risk"] is True
    assert diag["late_trend_adjustment_applied"] is True
    assert "adx_weak" in (diag["crypto_late_trend_reason"] or "")
    assert "vwap_extended" in (diag["crypto_late_trend_reason"] or "")
    assert "volume_below_ma" in (diag["crypto_late_trend_reason"] or "")
    assert penalized["direction"] == "LONG"
    assert baseline["final_score"] == pytest.approx(3.0, abs=0.05)
    assert penalized["final_score"] < 3.0
    assert penalized["final_score"] == pytest.approx(baseline["final_score"] * 0.85, rel=1e-2)


def test_late_trend_strong_adx_rising_skips_adjustment(monkeypatch):
    _enable_crypto_late_trend(monkeypatch)
    d1, h4, h1, candles, pair = _trx_like_score_inputs()
    for snap in (d1, h4, h1):
        snap["adx"] = 28.0
        snap["adxSlope"] = 0.5
        snap["adxPrev"] = 26.0
    result = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
        funding_rate=-0.0002,
    )
    diag = result["crypto_engine_a_diagnostics"]
    assert diag is not None
    assert diag.get("late_trend_adjustment_applied") is not True
    assert result["final_score"] == pytest.approx(3.0, abs=0.05)


def test_late_trend_volume_above_ma_still_applies_penalty(monkeypatch):
    """Elevated volume must not bypass late-trend penalty (BNB audit fix)."""
    _enable_crypto_late_trend(monkeypatch)
    d1, h4, h1, candles, pair = _trx_like_score_inputs()
    candles[-1]["vol"] = 8.0e6
    candles[-1]["volume"] = 8.0e6
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", False)
    baseline = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
        funding_rate=-0.0002,
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", True)
    result = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
        funding_rate=-0.0002,
    )
    diag = result["crypto_engine_a_diagnostics"]
    assert diag is not None
    assert diag["crypto_late_trend_risk"] is True
    assert diag["volume_below_ma"] is False
    assert diag["late_trend_adjustment_applied"] is True
    assert result["final_score"] == pytest.approx(baseline["final_score"] * 0.85, rel=1e-2)


def test_late_trend_price_near_vwap_skips_adjustment(monkeypatch):
    _enable_crypto_late_trend(monkeypatch)
    d1, h4, h1 = _trx_like_crypto_snaps()
    flat_price = 0.305
    candles = []
    for i in range(100):
        candles.append(
            {
                "open": flat_price,
                "high": flat_price + 0.001,
                "low": flat_price - 0.001,
                "close": flat_price,
                "vol": 4.0e6,
                "volume": 4.0e6,
            }
        )
    for snap in (d1, h4, h1):
        snap["close"] = flat_price
        snap["ema21"] = flat_price - 0.001
        snap["ema50"] = flat_price - 0.002
        snap["adx"] = 21.66
    result = _score(
        d1=d1, h4=h4, h1=h1,
        pair={"type": "crypto", "display": "TRX/USDT"},
        d1_candles=candles, h4_candles=candles, h1_candles=candles,
    )
    diag = result["crypto_engine_a_diagnostics"]
    assert diag is not None
    assert diag.get("vwap_extended") is False
    assert diag.get("late_trend_adjustment_applied") is not True


def test_late_trend_non_crypto_unchanged(monkeypatch):
    _enable_crypto_late_trend(monkeypatch)
    stock_pair = {"type": "stock", "display": "AAPL"}
    stock_on = _score(pair=stock_pair, h4=_snap("long", momentum="bullish"))
    stock_off = _score(pair=stock_pair, h4=_snap("long", momentum="bullish"))
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", False)
    stock_off2 = _score(pair=stock_pair, h4=_snap("long", momentum="bullish"))
    assert stock_on["final_score"] == pytest.approx(stock_off2["final_score"])
    assert stock_on.get("crypto_engine_a_diagnostics") is None


def test_late_trend_adjustment_applied_once(monkeypatch):
    _enable_crypto_late_trend(monkeypatch)
    mult = float(CONFIG.get("ENGINE_A_CRYPTO_LATE_TREND_MULT", 0.85))
    d1, h4, h1, candles, pair = _trx_like_score_inputs()

    _funding = -0.0002
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", False)
    baseline = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
        funding_rate=_funding,
    )

    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", True)
    penalized = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
        funding_rate=_funding,
    )

    assert penalized["final_score"] == pytest.approx(baseline["final_score"] * mult, rel=1e-3)
    assert penalized["feed_status"].get("crypto_late_trend") == "crypto_late_trend_multiplier"
    assert "bybit" not in str(penalized["feed_status"]).lower()


def test_late_trend_diagnostics_populated_when_adjustment_disabled(monkeypatch):
    _enable_crypto_late_trend(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", False)
    d1, h4, h1, candles, pair = _trx_like_score_inputs()
    result = _score(
        d1=d1, h4=h4, h1=h1, pair=pair,
        d1_candles=[], h4_candles=candles, h1_candles=[],
    )
    diag = result["crypto_engine_a_diagnostics"]
    assert diag is not None
    assert diag["adx_value"] == pytest.approx(21.66)
    assert diag["adx_below_threshold"] is True
    assert diag["late_trend_adjustment_applied"] is False


# ── Stock/ETF Volume Addon ───────────────────────────────────────────────────


def _rising_volume_candles(n=30, direction="LONG"):
    """H4 candles with rising volume aligned with direction."""
    rows = []
    close = 200.0
    vol = 500.0
    step = 0.5 if direction == "LONG" else -0.5
    for i in range(n):
        close += step
        vol += 20.0
        rows.append({
            "open": close - step,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "vol": vol,
        })
    return rows


def test_stock_volume_addon_disabled_returns_unsupported(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_STOCK_VOLUME_ADDON", {"ENABLED": False})
    monkeypatch.setitem(CONFIG, "ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity"])
    result = _score(
        pair={"type": "stock", "display": "PLTR"},
        h4_candles=_rising_volume_candles(),
    )
    assert result["addon_unsupported"] is True


def test_stock_volume_addon_with_volume_data(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity"])
    monkeypatch.setitem(CONFIG, "ENGINE_A_STOCK_VOLUME_ADDON", {
        "ENABLED": True,
        "ASSET_TYPES": ["stock", "etf", "etf_bond", "index"],
        "MIN_VOLUME_BARS": 5,
    })
    result = _score(
        pair={"type": "stock", "display": "PLTR"},
        h4_candles=_rising_volume_candles(30, "LONG"),
    )
    assert result["addon_unsupported"] is False
    assert result.get("addon_type") == "volume"


def test_stock_volume_addon_zero_volume_fallback(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity"])
    monkeypatch.setitem(CONFIG, "ENGINE_A_STOCK_VOLUME_ADDON", {
        "ENABLED": True,
        "ASSET_TYPES": ["stock"],
        "MIN_VOLUME_BARS": 10,
    })
    zero_vol_candles = [
        {"open": 100, "high": 101, "low": 99, "close": 100.5, "vol": 0}
        for _ in range(30)
    ]
    result = _score(
        pair={"type": "stock", "display": "PLTR"},
        h4_candles=zero_vol_candles,
    )
    assert result["addon_unsupported"] is True


def test_stock_volume_addon_confirms_long(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_STOCK_VOLUME_ADDON", {
        "ENABLED": True,
        "ASSET_TYPES": ["stock"],
        "MIN_VOLUME_BARS": 5,
    })
    candles = _rising_volume_candles(30, "LONG")
    from factor_scoring import _stock_volume_addon_with_status
    val, status = _stock_volume_addon_with_status(candles, "LONG", 1.8)
    assert status == "ok"
    assert val > 0


def test_etf_volume_addon_activates(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_STOCK_VOLUME_ADDON", {
        "ENABLED": True,
        "ASSET_TYPES": ["stock", "etf", "etf_bond"],
        "MIN_VOLUME_BARS": 5,
    })
    result = _score(
        pair={"type": "etf", "display": "SPY"},
        h4_candles=_rising_volume_candles(30, "LONG"),
    )
    assert result["addon_unsupported"] is False
    assert result.get("addon_type") == "volume"


def test_orthogonal_vote_providers_scale_and_fail_closed(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "carry_feed",
        types.SimpleNamespace(get_carry_z=lambda _display, as_of_date=None: 2.5),
    )
    monkeypatch.setitem(
        sys.modules,
        "cot_feed",
        types.SimpleNamespace(get_cot_z=lambda _display, as_of_date=None: -2.5),
    )
    monkeypatch.setitem(
        sys.modules,
        "vol_skew_feed",
        types.SimpleNamespace(get_vol_skew_z=lambda _display, as_of_date=None: None),
    )

    assert factor_scoring._vote_carry("EUR/USD", "LONG", "2024-01-02T00:00:00Z") == (1.0, "ok")
    assert factor_scoring._vote_carry("EUR/USD", "SHORT", "2024-01-02T00:00:00Z") == (-1.0, "ok")
    assert factor_scoring._vote_cot("EUR/USD", "LONG", "2024-01-02T00:00:00Z") == (-1.0, "ok")
    assert factor_scoring._vote_cot("EUR/USD", "SHORT", "2024-01-02T00:00:00Z") == (1.0, "ok")

    assert factor_scoring._vote_funding(0.03, "LONG", {"mean": 0.0, "std": 0.01}) == (-1.0, "ok")
    assert factor_scoring._vote_funding(0.03, "SHORT", {"mean": 0.0, "std": 0.01}) == (1.0, "ok")
    assert factor_scoring._vote_funding(None, "LONG") == (0.0, "neutral")

    oi_ctx = {"oi_change_pct": 5.0, "price_change_pct": 2.0}
    assert factor_scoring._vote_oi_divergence(oi_ctx, "LONG") == (1.0, "ok")
    assert factor_scoring._vote_oi_divergence(oi_ctx, "SHORT") == (-1.0, "ok")
    assert factor_scoring._vote_oi_divergence(None, "LONG") == (0.0, "neutral")

    macro_ctx = {"vol_skew": 2.5}
    assert factor_scoring._vote_vol_skew("XAU/USD", "commodity", "LONG", None, macro_ctx) == (-1.0, "ok")
    assert factor_scoring._vote_vol_skew("XAU/USD", "commodity", "SHORT", None, macro_ctx) == (1.0, "ok")
    assert factor_scoring._vote_vol_skew("XAU/USD", "commodity", "LONG", None, {}) == (0.0, "neutral")


def test_continuous_funding_vote_scales_and_matches_discrete_direction(monkeypatch):
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_SCALE", 0.0005)
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_Z_REF", 2.0)
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_BASELINE", 0.0001)
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_NOISE_BAND", 0.0001)
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_USE_ZSCORE", False)

    assert factor_scoring._funding_vote_continuous(0.05, "LONG", None) < -0.5
    assert factor_scoring._funding_vote_continuous(-0.05, "LONG", None) > 0.5
    assert factor_scoring._funding_vote_continuous(0.0001, "LONG", None) == pytest.approx(0.0)

    for rate in (0.0003, -0.0002):
        for direction in ("LONG", "SHORT"):
            discrete = factor_scoring._funding_addon(rate, direction)
            continuous = factor_scoring._funding_vote_continuous(rate, direction, None)
            assert (continuous > 0) == (discrete == factor_scoring._ADDON_CONFIRM)
            assert (continuous < 0) == (discrete == factor_scoring._ADDON_AGAINST)

    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_USE_ZSCORE", True)
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_Z_THRESHOLD", 1.0)
    assert factor_scoring._funding_vote_continuous(0.005, "LONG", {"mean": 0.0, "std": 0.01}) == 0.0
    assert factor_scoring._funding_vote_continuous(0.03, "LONG", {"mean": 0.0, "std": 0.01}) < -0.5


def test_continuous_oi_vote_matches_discrete_semantics_and_prefers_smoothed(monkeypatch):
    monkeypatch.setitem(CONFIG, "FACTOR_OI_REF_PCT", 5.0)
    monkeypatch.setitem(CONFIG, "FACTOR_OI_USE_SMOOTHED", True)

    long_confirm = {"oi_change_pct": 6.0, "price_change_pct": 2.0}
    assert factor_scoring._oi_addon(long_confirm, "LONG") == factor_scoring._ADDON_CONFIRM
    assert factor_scoring._oi_vote_continuous(long_confirm, "LONG") == pytest.approx(1.0)
    assert factor_scoring._oi_vote_continuous({"oi_change_pct": 0.2, "price_change_pct": 1.0}, "LONG") == 0.0

    short_covering = {"oi_change_pct": -6.0, "price_change_pct": 2.0}
    assert factor_scoring._oi_addon(short_covering, "LONG") == factor_scoring._ADDON_NEUTRAL
    assert factor_scoring._oi_vote_continuous(short_covering, "LONG") == 0.0

    smoothed = {"oi_change_pct": 6.0, "oi_change_pct_smoothed": -6.0, "price_change_pct": 2.0}
    assert factor_scoring._oi_vote_continuous(smoothed, "LONG") == 0.0
    assert factor_scoring._oi_vote_continuous(smoothed, "SHORT") == pytest.approx(-0.5)


def test_cot_votes_and_addons_stay_confirming_at_extremes(monkeypatch):
    def _fake_cot_z(display, as_of_date=None):
        return {"CROWDED": 2.6, "MID": 1.0}[display]

    monkeypatch.setitem(
        sys.modules,
        "cot_feed",
        types.SimpleNamespace(
            get_cot_z=_fake_cot_z,
            _PAIR_FORMULA={"CROWDED": object(), "MID": object()},
        ),
    )

    assert factor_scoring._vote_cot("CROWDED", "LONG", "2025-01-15") == (1.0, "ok")
    assert factor_scoring._vote_cot("CROWDED", "SHORT", "2025-01-15") == (-1.0, "ok")
    assert factor_scoring._vote_cot("MID", "LONG", "2025-01-15") == (0.5, "ok")
    assert factor_scoring._cot_addon_with_status("CROWDED", "commodity", "LONG", "2025-01-15") == (
        factor_scoring._ADDON_CONFIRM,
        "ok",
    )


def test_orthogonal_vote_vector_weights_and_squashes_once(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_ORTHO_FACTOR_WEIGHTS",
        {"crypto": {"carry": 0.0, "cot": 0.10, "funding": 0.45, "oi_divergence": 0.35, "vol_skew": 0.0}},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_TANH_K", 1.0)
    monkeypatch.setattr(factor_scoring, "_vote_carry", lambda *_args: (0.4, "ok"))
    monkeypatch.setattr(factor_scoring, "_vote_cot", lambda *_args: (0.5, "ok"))
    monkeypatch.setattr(factor_scoring, "_vote_funding", lambda *_args: (-1.0, "ok"))
    monkeypatch.setattr(factor_scoring, "_vote_oi_divergence", lambda *_args: (0.5, "ok"))
    monkeypatch.setattr(factor_scoring, "_vote_vol_skew", lambda *_args: (0.2, "ok"))

    ortho_term, detail = factor_scoring._orthogonal_vote_vector(
        {"type": "crypto", "display": "BTC/USDT"},
        "LONG",
        "2024-01-02T00:00:00Z",
        funding_rate=0.01,
        oi_context={"oi_change_pct": 5.0, "price_change_pct": 2.0},
        funding_stats=None,
        macro_context={},
    )

    assert set(detail["votes"]) == {"carry", "cot", "funding", "oi_divergence", "vol_skew"}
    assert detail["weighted_sum"] == pytest.approx(-0.225)
    assert ortho_term == pytest.approx(math.tanh(-0.225))
    assert detail["ortho_term"] == pytest.approx(round(math.tanh(-0.225), 4))
    assert detail["enabled"] is True


def test_orthogonal_vote_vector_debug_writes_jsonl(monkeypatch, tmp_path):
    debug_path = tmp_path / "nested" / "ortho_votes.jsonl"
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_FACTOR_WEIGHTS", {"crypto": {"funding": 0.45}})
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_TANH_K", 1.0)
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_DEBUG", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_DEBUG_PATH", str(debug_path))
    monkeypatch.setattr(factor_scoring, "_vote_carry", lambda *_args: (0.0, "neutral"))
    monkeypatch.setattr(factor_scoring, "_vote_cot", lambda *_args: (0.0, "neutral"))
    monkeypatch.setattr(factor_scoring, "_vote_funding", lambda *_args: (-0.75, "ok"))
    monkeypatch.setattr(factor_scoring, "_vote_oi_divergence", lambda *_args: (0.0, "neutral"))
    monkeypatch.setattr(factor_scoring, "_vote_vol_skew", lambda *_args: (0.0, "neutral"))

    _term, detail = factor_scoring._orthogonal_vote_vector(
        {"type": "crypto", "display": "BTC/USDT", "symbol": "BTCUSDT"},
        "LONG",
        "2025-02-03T04:00:00Z",
        funding_rate=0.01,
        oi_context={},
        funding_stats=None,
        macro_context={},
    )

    lines = debug_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = __import__("json").loads(lines[0])
    assert rec["display"] == "BTC/USDT"
    assert rec["score_group"] == "crypto_btc"
    assert rec["direction"] == "LONG"
    assert rec["bar_time"] == "2025-02-03T04:00:00Z"
    assert rec["weighted_sum"] == detail["weighted_sum"]
    assert rec["ortho_term"] == detail["ortho_term"]
    assert rec["votes"]["funding"] == {"vote": -0.75, "weight": 0.45, "status": "ok"}


def test_orthogonal_vote_vector_prefers_crypto_score_group_weights(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_ORTHO_FACTOR_WEIGHTS",
        {
            "crypto": {"funding": 0.99, "oi_divergence": 0.99, "cot": 0.99},
            "crypto_btc": {"funding": 0.45, "oi_divergence": 0.35, "cot": 0.15},
            "crypto_alt_majors": {"funding": 0.25, "oi_divergence": 0.20, "cot": 0.0},
        },
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_TANH_K", 1.0)
    monkeypatch.setattr(factor_scoring, "_vote_carry", lambda *_args: (0.0, "neutral"))
    monkeypatch.setattr(factor_scoring, "_vote_cot", lambda *_args: (0.0, "neutral"))
    monkeypatch.setattr(factor_scoring, "_vote_funding", lambda *_args: (0.0, "neutral"))
    monkeypatch.setattr(factor_scoring, "_vote_oi_divergence", lambda *_args: (0.0, "neutral"))
    monkeypatch.setattr(factor_scoring, "_vote_vol_skew", lambda *_args: (0.0, "neutral"))

    _btc_term, btc = factor_scoring._orthogonal_vote_vector(
        {"type": "crypto", "display": "BTC/USDT", "symbol": "BTCUSDT"},
        "LONG",
        None,
        funding_rate=0.01,
        oi_context={},
        funding_stats=None,
        macro_context={},
    )
    _sol_term, sol = factor_scoring._orthogonal_vote_vector(
        {"type": "crypto", "display": "SOL/USDT", "symbol": "SOLUSDT"},
        "LONG",
        None,
        funding_rate=0.01,
        oi_context={},
        funding_stats=None,
        macro_context={},
    )

    assert btc["votes"]["funding"]["weight"] == pytest.approx(0.45)
    assert btc["votes"]["oi_divergence"]["weight"] == pytest.approx(0.35)
    assert btc["votes"]["cot"]["weight"] == pytest.approx(0.15)
    assert sol["votes"]["funding"]["weight"] == pytest.approx(0.25)
    assert sol["votes"]["oi_divergence"]["weight"] == pytest.approx(0.20)
    assert sol["votes"]["cot"]["weight"] == pytest.approx(0.0)


def test_vol_skew_vote_uses_feed_when_macro_context_is_absent(monkeypatch):
    fake_feed = types.SimpleNamespace(
        get_vol_skew_z=lambda display, as_of_date=None: 2.0
        if display == "XAU/USD" and as_of_date == "2025-01-02"
        else None
    )
    monkeypatch.setitem(sys.modules, "vol_skew_feed", fake_feed)

    vote, status = factor_scoring._vote_vol_skew(
        "XAU/USD",
        "commodity",
        "LONG",
        "2025-01-02T00:00:00Z",
        None,
    )

    assert vote == pytest.approx(-1.0)
    assert status == "ok"


def test_ortho_disabled_preserves_legacy_score_and_skips_vector(monkeypatch):
    pair = {"type": "crypto", "display": "BTC/USDT"}
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_ENABLED", False)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("orthogonal vote vector must not run when disabled")

    monkeypatch.setattr(factor_scoring, "_orthogonal_vote_vector", fail_if_called)
    disabled = _score(
        _snap("long", momentum="bullish"),
        _snap("long", momentum="bullish"),
        _snap("long", momentum="bullish"),
        pair=pair,
        funding_rate=-0.0002,
    )

    monkeypatch.delitem(CONFIG, "ENGINE_A_ORTHO_VOTE_ENABLED", raising=False)
    absent = _score(
        _snap("long", momentum="bullish"),
        _snap("long", momentum="bullish"),
        _snap("long", momentum="bullish"),
        pair=pair,
        funding_rate=-0.0002,
    )

    assert disabled["final_score"] == absent["final_score"]
    assert disabled["conviction"] == absent["conviction"]
    assert disabled["factor_scores"]["addon"] == absent["factor_scores"]["addon"]
    assert disabled["orthoEnabled"] is False
    assert disabled["orthoVote"] is None
    assert "ortho" not in disabled["factor_scores"]
    assert "ortho_term" not in disabled["factor_scores"]


def test_ortho_enabled_replaces_addon_term_and_exposes_votes(monkeypatch):
    pair = {"type": "crypto", "display": "BTC/USDT"}
    detail = {
        "votes": {
            "carry": {"vote": 0.0, "weight": 0.0, "status": "neutral"},
            "cot": {"vote": 0.5, "weight": 0.1, "status": "ok"},
            "funding": {"vote": 1.0, "weight": 0.45, "status": "ok"},
            "oi_divergence": {"vote": 0.5, "weight": 0.35, "status": "ok"},
            "vol_skew": {"vote": 0.0, "weight": 0.0, "status": "neutral"},
        },
        "weighted_sum": 0.675,
        "ortho_term": 1.0,
        "enabled": True,
    }

    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_CONVICTION_WEIGHT_BY_CLASS", {"crypto": 0.25})
    monkeypatch.setattr(factor_scoring, "_orthogonal_vote_vector", lambda *_args, **_kwargs: (1.0, detail))

    enabled = _score(
        _snap("long", momentum="bullish"),
        _snap("long", momentum="bullish"),
        _snap("long", momentum="bullish"),
        pair=pair,
        funding_rate=0.02,
        oi_context={"oi_change_pct": 5.0, "price_change_pct": 2.0},
    )

    assert enabled["orthoEnabled"] is True
    assert enabled["orthoVote"] == detail
    assert enabled["factor_scores"]["ortho"] == {
        "carry": 0.0,
        "cot": 0.5,
        "funding": 1.0,
        "oi_divergence": 0.5,
        "vol_skew": 0.0,
    }
    assert enabled["factor_scores"]["ortho_term"] == 1.0
    assert enabled["weights"]["ortho"] == pytest.approx(0.25)


def test_calc_confluence_bridges_ortho_diagnostics_without_signing_nested_votes(monkeypatch):
    from scoring import calc_confluence

    pair = {"type": "crypto", "display": "BTC/USDT"}
    snap = _snap("long", momentum="bullish")
    detail = {
        "votes": {
            "carry": {"vote": 0.0, "weight": 0.0, "status": "neutral"},
            "cot": {"vote": 0.5, "weight": 0.1, "status": "ok"},
            "funding": {"vote": 1.0, "weight": 0.45, "status": "ok"},
            "oi_divergence": {"vote": 0.5, "weight": 0.35, "status": "ok"},
            "vol_skew": {"vote": 0.0, "weight": 0.0, "status": "neutral"},
        },
        "weighted_sum": 0.675,
        "ortho_term": 1.0,
        "enabled": True,
    }

    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_VOTE_ENABLED", True)
    monkeypatch.setitem(CONFIG, "ENGINE_A_ORTHO_CONVICTION_WEIGHT_BY_CLASS", {"crypto": 0.25})
    monkeypatch.setattr(factor_scoring, "_orthogonal_vote_vector", lambda *_args, **_kwargs: (1.0, detail))

    out = calc_confluence(
        {"snap": snap},
        {"snap": snap},
        {"snap": snap},
        1.0,
        {},
        pair,
        "neutral",
        funding_rate=0.02,
        oi_context={"oi_change_pct": 5.0, "price_change_pct": 2.0},
    )

    assert out["factor_scores"]["ortho"]["funding"] == 1.0
    fd = out["factorDiagnostics"]
    assert fd["orthoEnabled"] is True
    assert fd["orthoVote"] == detail
    assert "FACTOR_ORTHO" not in out["votes"]


def test_di_alignment_multiplier_is_smooth(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    
    d1 = _snap("long")
    h1 = _snap("long")
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD"}
    
    # Run at 2.5 adverse (di_diff = -2.5)
    h4_minor = _snap("long")
    h4_minor["plusDI"] = 20.0
    h4_minor["minusDI"] = 22.5
    
    res_minor = compute_factor_scores(
        d1, h4_minor, h1, pair, [], [], [], 1.0
    )
    
    # Run at 5.0 adverse (di_diff = -5.0)
    h4_limit = _snap("long")
    h4_limit["plusDI"] = 20.0
    h4_limit["minusDI"] = 25.0
    
    res_limit = compute_factor_scores(
        d1, h4_limit, h1, pair, [], [], [], 1.0
    )
    
    assert res_limit["feed_status"]["di_align"] == "0.85"
    assert res_minor["feed_status"]["di_align"] == "0.93"


def test_volume_price_concordance_correct_weighting():
    candles = [
        {"close": 10.0, "vol": 100},
        {"close": 11.0, "vol": 100},
        {"close": 12.0, "vol": 100},
        {"close": 13.0, "vol": 100},
        {"close": 14.0, "vol": 100},
        {"close": 13.0, "vol": 1000},  # Down bar on huge volume
    ]
    concordance = factor_scoring._volume_price_concordance(candles, "LONG", lookback=5)
    assert concordance < 0.0
    assert concordance == pytest.approx(-0.42857, abs=1e-4)


def test_price_momentum_volatility_normalization(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "FACTORS": ["price_momentum"],
        "ROC_VOL_NORM": True
    })
    
    snap_hi = {"atr": 4.0, "close": 100.0}
    candles = [{"close": 100.0} for _ in range(15)]
    candles[-1] = {"close": 105.0} # ROC = 5%
    
    val_hi, detail_hi = factor_scoring._research_price_momentum_value(
        "LONG", candles, 0.15, -0.10, snap=snap_hi
    )
    assert val_hi == 0.0  # Dynamic threshold of 5% (max capped) not cleared by 5% ROC (strictly > threshold)
    assert detail_hi["threshold"] == 0.05
    
    snap_lo = {"atr": 0.5, "close": 100.0}
    val_lo, detail_lo = factor_scoring._research_price_momentum_value(
        "LONG", candles, 0.15, -0.10, snap=snap_lo
    )
    assert val_lo == 0.15  # Dynamic threshold of 0.71% cleared by 5% ROC
    assert detail_lo["threshold"] == pytest.approx(0.0071, abs=1e-4)


def test_macro_context_usd_relative_strength_score(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    
    d1 = _snap("long")
    h1 = _snap("long")
    h4 = _snap("long")
    pair = {"type": "commodity", "display": "XAU/USD", "symbol": "GC=F"}
    
    # 1. Macro score = 3.0 (bullish for LONG)
    macro_bullish = {"usd_proxy_score": 3.0}
    res_bullish = compute_factor_scores(
        d1, h4, h1, pair, [], [], [], 1.0,
        macro_context=macro_bullish
    )
    
    # 2. Macro score = -3.0 (bearish for LONG)
    macro_bearish = {"usd_proxy_score": -3.0}
    res_bearish = compute_factor_scores(
        d1, h4, h1, pair, [], [], [], 1.0,
        macro_context=macro_bearish
    )
    
    assert res_bullish["final_score"] > res_bearish["final_score"]
    # Check that they differ by the expected amount.
    # Base score * (1 + 0.02) vs Base score * (1 - 0.02)
    # The relative difference is ~4% of base score.
    diff_pct = (res_bullish["final_score"] - res_bearish["final_score"]) / res_bearish["final_score"]
    assert pytest.approx(diff_pct, abs=1e-2) == 0.04


def test_engine_a_group_adjustments_enabled_reads_config(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False)
    assert factor_scoring._engine_a_group_adjustments_enabled() is False
    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    assert factor_scoring._engine_a_group_adjustments_enabled() is True


def test_adx_thresholds_use_explicit_score_group_keys(monkeypatch):
    """Score-group ADX keys in config.yaml resolve before asset_type fallback."""
    monkeypatch.setitem(
        CONFIG,
        "ADX_TREND_MIN_CLASS",
        {"commodity": 25, "copper": 26},
    )
    monkeypatch.setitem(
        CONFIG,
        "FACTOR_ADX_HARD_FAIL_CLASS",
        {"commodity": 10, "copper": 10},
    )
    trend_min, hard_fail = factor_scoring._resolve_adx_thresholds("commodity", "copper")
    assert trend_min == pytest.approx(26.0)
    assert hard_fail == pytest.approx(10.0)


def test_cot_coverage_status_unsupported_and_no_coverage(monkeypatch):
    assert factor_scoring._cot_coverage_status("SOL/USDT") == "unsupported"

    monkeypatch.setattr(factor_scoring, "_cot_formula_supported", lambda _d: True)
    monkeypatch.setattr(
        "cot_feed.get_cot_net",
        lambda _d: {"_cot_coverage": "no_coverage"},
    )
    assert factor_scoring._cot_coverage_status("Cocoa") == "no_coverage"


def test_cot_stock_uses_macro_proxy_formula():
    from cot_feed import _PAIR_FORMULA

    assert _PAIR_FORMULA.get("AAPL") == [(1.0, "SP500")]
    assert _PAIR_FORMULA.get("NVDA") == [(1.0, "NQ100")]


def test_engine_a_cot_policy_present_in_config():
    policy = CONFIG.get("ENGINE_A_COT_POLICY") or {}
    assert policy.get("altcoin_unsupported") is True
    assert policy.get("stock_macro_proxy") is True


def test_resolve_funding_stats_builds_rolling_mean_std(monkeypatch):
    factor_scoring._funding_stats_cache.clear()
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_USE_ZSCORE", True)
    monkeypatch.setitem(CONFIG, "FACTOR_FUNDING_ZSCORE_WINDOW", 5)
    rates = [{"rate": 0.0001}, {"rate": 0.0002}, {"rate": 0.0003}, {"rate": 0.0004}, {"rate": 0.0005}]
    monkeypatch.setattr(
        "data_feeds._fetch_bybit_funding_history",
        lambda *a, **k: {"list": rates},
    )
    monkeypatch.setattr(
        "data_feeds._fetch_binance_funding_history",
        lambda *a, **k: {"list": []},
    )
    stats = factor_scoring._resolve_funding_stats({"symbol": "BTCUSDT", "type": "crypto"})
    assert stats is not None
    assert stats["n"] == 5
    assert stats["mean"] == pytest.approx(0.0003)
    assert stats["std"] > 0
