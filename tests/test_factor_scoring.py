import pytest

from config import CONFIG
from factor_scoring import (
    _momentum_quality,
    _oi_addon,
    build_oi_context_for_factor_scoring,
    compute_factor_scores,
    make_regime_smoothing_context,
)


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
):
    return compute_factor_scores(
        d1_snap=d1 if d1 is not None else _snap("long"),
        h4_snap=h4 if h4 is not None else _snap("long"),
        h1_snap=h1 if h1 is not None else _snap("long"),
        pair=pair or {"type": "stock", "display": "TEST"},
        d1_candles=d1_candles or [],
        h4_candles=h4_candles or [],
        h1_candles=[],
        volume_ratio=volume_ratio,
        funding_rate=funding_rate,
        bar_time=bar_time,
        macro_context=macro_context,
        intermarket_context=intermarket_context,
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


def test_public_helpers_match_current_contract():
    ctx = make_regime_smoothing_context()
    assert set(ctx) == {"history", "committed", "lock"}

    oi_context = build_oi_context_for_factor_scoring(
        {"oiChange": 6.0},
        [{"close": 100.0}, {"close": 105.0}],
        {"close": 110.0},
    )
    assert oi_context == {
        "oi_change_pct": 6.0,
        "price_change_pct": pytest.approx(10.0),
    }


def test_adx_hard_fail_blocks_to_zero_score():
    hard_fail = float(CONFIG.get("FACTOR_ADX_HARD_FAIL", 15.0))

    result = _score(
        _snap("long", adx=hard_fail - 0.1),
        _snap("long", adx=hard_fail - 0.1),
        _snap("long", adx=hard_fail - 0.1),
    )

    assert result["final_score"] == 0.0
    assert result["adx_multiplier"] == 0.0
    assert result["abort_reason"] == "adx_hard_abort"


def test_missing_adx_uses_configured_soft_multiplier():
    result = _score(
        _snap("long", include_adx=False),
        _snap("long", include_adx=False),
        _snap("long", include_adx=False),
    )

    assert result["adx_source"] == "missing"
    assert result["adx_multiplier"] == pytest.approx(
        float(CONFIG.get("FACTOR_ADX_SOFT_MULT", 0.65))
    )
    assert result["final_score"] > 0.0


def test_full_bullish_alignment_is_stronger_than_partial_alignment():
    full = _score(_snap("long"), _snap("long"), _snap("long"))
    d1_only = _score(_snap("long"), {}, {})

    assert full["direction"] == "LONG"
    assert d1_only["direction"] == "LONG"
    assert full["final_score"] > d1_only["final_score"]
    assert full["factor_scores"]["trend"] > d1_only["factor_scores"]["trend"]


def test_full_bearish_alignment_is_stronger_than_partial_alignment():
    full = _score(_snap("short"), _snap("short"), _snap("short"))
    d1_only = _score(_snap("short"), {}, {})

    assert full["direction"] == "SHORT"
    assert d1_only["direction"] == "SHORT"
    assert full["final_score"] > d1_only["final_score"]
    assert abs(full["factor_scores"]["trend"]) > abs(d1_only["factor_scores"]["trend"])


def test_h4_h1_against_d1_do_not_create_full_strength_trend_score():
    full_short = _score(_snap("short"), _snap("short"), _snap("short"))
    conflicted = _score(_snap("long"), _snap("short"), _snap("short"))

    assert conflicted["direction"] == "SHORT"
    assert conflicted["final_score"] < full_short["final_score"]
    assert abs(conflicted["factor_scores"]["trend"]) < abs(full_short["factor_scores"]["trend"])
    assert conflicted["trend_coherence"]["coherence_ratio"] < 1.0


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
    assert _oi_addon({"oi_change_pct": -4.0, "price_change_pct": -2.0}, "SHORT") == pytest.approx(0.30)
    assert _oi_addon({"oi_change_pct": 4.0, "price_change_pct": -2.0}, "LONG") == pytest.approx(-0.15)
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


def test_crypto_addon_conviction_positive_zero_negative_ordering():
    pair = {"type": "crypto", "display": "BTC/USDT"}

    positive = _score(pair=pair, funding_rate=-0.0002)
    zero = _score(pair=pair, funding_rate=0.0001)
    negative = _score(pair=pair, funding_rate=0.0010)

    assert positive["addon_value"] == pytest.approx(0.30)
    assert zero["addon_value"] == pytest.approx(0.0)
    assert negative["addon_value"] == pytest.approx(-0.15)
    assert positive["conviction"] > zero["conviction"] > negative["conviction"]
    assert positive["final_score"] > zero["final_score"] > negative["final_score"]


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
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "GROUPS": {"forex_crosses": ["obv_divergence"]},
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "forex", "display": "EUR/AUD"},
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    assert result["research_lab_value"] == pytest.approx(0.15)
    assert result["factor_scores"]["research_lab"] == pytest.approx(0.15)
    assert result["research_lab_detail"]["score_group"] == "forex_crosses"
    assert result["research_lab_detail"]["components"]["obv_divergence"]["signal"] == "confirming"
    assert "research_lab" in result["active_nondirectional_factors"]


def test_research_lab_addon_is_clamped_to_addon_ceiling(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.20,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "GROUPS": {"crypto_majors": ["obv_divergence"]},
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "crypto", "display": "BTC/USDT"},
        funding_rate=-0.0002,
        d1_candles=_candles(trend=0.2, volume_trend=10.0),
    )

    assert result["research_lab_value"] == pytest.approx(0.20)
    assert result["addon_value"] == pytest.approx(0.30)


def test_weights_report_effective_values_when_addon_is_unsupported():
    result = _score(pair={"type": "stock", "display": "TEST"})

    assert result["addon_unsupported"] is True
    assert result["weights"]["addon"] == pytest.approx(0.0)
    assert "base" in result["weights"]


def test_research_lab_factor_supports_commodity_group_candidates(monkeypatch):
    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "GROUPS": {"metals": ["aroon_trend"]},
    }
    monkeypatch.setitem(CONFIG, "ENGINE_A_RESEARCH_LAB_FACTORS", cfg)

    result = _score(
        pair={"type": "commodity", "display": "XAG/USD"},
        d1_candles=_candles(trend=0.2, volume_trend=0.0),
    )

    assert result["research_lab_value"] == pytest.approx(0.15)
    assert result["research_lab_detail"]["score_group"] == "metals"
    assert result["research_lab_detail"]["components"]["aroon_trend"]["signal"] == "bull_trend"


def test_calc_confluence_factor_diagnostics_includes_research_lab(monkeypatch):
    """research_lab_* from compute_factor_scores must appear on API-bound factorDiagnostics."""
    from scoring import calc_confluence

    cfg = {
        "ENABLED": True,
        "BONUS": 0.15,
        "PENALTY": -0.10,
        "MAX_ABS": 0.20,
        "GROUPS": {"forex_crosses": ["obv_divergence"]},
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
    assert detail.get("score_group") == "forex_crosses"
    assert detail.get("components", {}).get("obv_divergence", {}).get("signal") == "confirming"


def test_conviction_floor_default_is_explicit_and_no_momentum_uses_floor_blend():
    floor = float(CONFIG["FACTOR_CONVICTION_FLOOR"])
    result = _score(_snap("long"), _snap("long"), _snap("long"))

    assert floor == pytest.approx(0.60)
    assert result["conviction"] == pytest.approx(
        float(CONFIG.get("FACTOR_BASE_WEIGHT", 0.20))
    )
    expected = 3.0 * (floor + (1.0 - floor) * result["conviction"])
    assert result["final_score"] == pytest.approx(expected)
    assert result["final_score"] < 3.0


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


def test_forex_session_multiplier_does_not_penalize_crypto():
    off_session = "2026-04-24T02:00:00+00:00"

    forex = _score(
        pair={"type": "forex", "display": "TEST/FX"},
        bar_time=off_session,
    )
    crypto = _score(
        pair={"type": "crypto", "display": "BTC/USDT"},
        bar_time=off_session,
        funding_rate=0.0001,
    )

    assert forex["session_multiplier"] < 1.0
    assert crypto["session_multiplier"] == pytest.approx(1.0)
    assert forex["final_score"] < crypto["final_score"]


def test_volume_macro_and_intermarket_context_do_not_affect_current_score():
    baseline = _score(pair={"type": "stock", "display": "AAPL"})
    with_unused_context = _score(
        pair={"type": "stock", "display": "AAPL"},
        volume_ratio=999.0,
        macro_context={"usd_proxy_score": -3.0},
        intermarket_context={"drivers": [{"driver": "DXY", "summary": {"current": 1}}]},
    )

    assert with_unused_context["final_score"] == baseline["final_score"]
    assert with_unused_context["factor_scores"] == baseline["factor_scores"]
    assert with_unused_context["intermarket_engine_a_delta"] == 0.0
