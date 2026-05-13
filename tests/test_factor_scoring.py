import pytest

import factor_scoring
from config import CONFIG
from factor_scoring import (
    _coherent_trend_score,
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
    monkeypatch.setitem(CONFIG, "INSIDER_TRADING_ENABLED", True)
    monkeypatch.setitem(CONFIG, "FUNDAMENTALS_ENABLED", True)

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


def test_stock_index_cot_proxy_formulas_are_config_gated(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity", "index", "stock"])
    monkeypatch.setattr("factor_scoring._cot_addon", lambda *_args, **_kwargs: 0.20)

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

    assert result["feed_status"]["abort_reason"] == "DI_ALIGNMENT_CONFLICT"
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
    forex_base = _score(pair=forex_pair, h4=_snap("long", momentum="bullish"))
    crypto_base = _score(pair=crypto_pair, h4=_snap("long", momentum="bullish"), funding_rate=0.0001)

    monkeypatch.setitem(CONFIG, "ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", True)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_FACTOR_WEIGHTS_BY_CLASS",
        {"us_stock_single": {"momentum": 0.05, "addon": 0.0, "base": 0.95}},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS", {"stock": {"min_directional": 0.9, "soft_span": 0.1}})

    forex_grouped = _score(pair=forex_pair, h4=_snap("long", momentum="bullish"))
    crypto_grouped = _score(pair=crypto_pair, h4=_snap("long", momentum="bullish"), funding_rate=0.0001)

    assert forex_grouped["final_score"] == forex_base["final_score"]
    assert crypto_grouped["final_score"] == crypto_base["final_score"]


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
