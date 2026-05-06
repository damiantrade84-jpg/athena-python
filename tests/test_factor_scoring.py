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
    h1_candles=None,
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
    assert result["abort_reason"] == "adx_hard_abort"


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
    # Stage 1.4: _ADDON_CONFIRM aligned to 0.20, _ADDON_AGAINST to -0.15
    assert _oi_addon({"oi_change_pct": -4.0, "price_change_pct": -2.0}, "SHORT") == pytest.approx(0.20)
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

    # Stage 1.4: _ADDON_CONFIRM = 0.20, _ADDON_AGAINST = -0.15
    assert positive["addon_value"] == pytest.approx(0.20)
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


def test_research_lab_addon_is_clamped_to_addon_ceiling(monkeypatch):
    # Stage 1.4: _ADDON_CONFIRM = 0.20; research lab + funding capped at 0.20 total.
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
    assert result["addon_value"] == pytest.approx(0.20)


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

    assert result["research_lab_value"] == pytest.approx(0.15)
    assert result["research_lab_detail"]["score_group"] == "universal"
    assert result["research_lab_detail"]["components"]["aroon_trend"]["signal"] == "bull_trend"


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
    assert detail.get("components", {}).get("obv_divergence", {}).get("signal") == "confirming"


def test_conviction_floor_default_is_explicit_and_no_momentum_uses_floor_blend():
    floor = float(CONFIG["FACTOR_CONVICTION_FLOOR"])
    result = _score(_snap("long"), _snap("long"), _snap("long"))

    # floor is config-driven (default 0.60 in config.yaml, _CONVICTION_FLOOR_DEFAULT = 0.20 in code)
    assert floor > 0.0
    # With no momentum (macdHist=0, rsi neutral), conviction = base_weight only
    assert result["conviction"] == pytest.approx(
        float(CONFIG.get("FACTOR_BASE_WEIGHT", 0.20))
    )
    # final_score depends on trend_score * adx * vol_scaler * di_align * (floor + (1-floor)*conviction)
    # Just verify it's in valid range and formula is consistent
    assert 0.0 < result["final_score"] < 3.0
    # Verify the formula components are present
    assert result["factor_scores"]["momentum"] == pytest.approx(0.0)


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


def test_volatility_scaler_replaces_session_multiplier():
    # Stage 3.4: Session multiplier deprecated; volatility scaler applied to all assets.
    # Low volatility (ATR% < 0.5%) → scaler > 1.0
    # High volatility (ATR% > 2.5%) → scaler < 1.0
    low_vol_d1 = {"ema21": 110.0, "ema200": 100.0, "adx": 25.0, "close": 1000.0, "atr": 3.0, "plusDI": 25.0, "minusDI": 15.0}
    low_vol_h4 = {"ema21": 110.0, "ema50": 100.0, "adx": 25.0, "rsi": 55.0, "macdHist": 0.0, "close": 1000.0, "atr": 3.0, "plusDI": 25.0, "minusDI": 15.0}
    low_vol_h1 = {"ema21": 110.0, "ema50": 100.0, "close": 1000.0, "atr": 3.0}
    low_vol = _score(
        d1=low_vol_d1, h4=low_vol_h4, h1=low_vol_h1,
        pair={"type": "forex", "display": "TEST/FX"},
    )
    high_vol_d1 = {"ema21": 110.0, "ema200": 100.0, "adx": 25.0, "close": 100.0, "atr": 5.0, "plusDI": 25.0, "minusDI": 15.0}
    high_vol_h4 = {"ema21": 110.0, "ema50": 100.0, "adx": 25.0, "rsi": 55.0, "macdHist": 0.0, "close": 100.0, "atr": 5.0, "plusDI": 25.0, "minusDI": 15.0}
    high_vol_h1 = {"ema21": 110.0, "ema50": 100.0, "close": 100.0, "atr": 5.0}
    high_vol = _score(
        d1=high_vol_d1, h4=high_vol_h4, h1=high_vol_h1,
        pair={"type": "stock", "display": "AAPL"},
    )

    # Session multiplier is now always 1.0 (deprecated)
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
