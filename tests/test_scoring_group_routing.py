"""Tests for subgroup routing and threshold resolution."""

import os
import sys
import importlib.util
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from scoring import (
    ENGINE_A_KNOWN_SCORE_GROUPS,
    _classify_signal,
    calc_confluence,
    get_backtest_min_score_threshold,
    get_min_confluence_threshold,
    get_pair_level_atr_class,
    get_pair_score_group,
    get_score_threshold,
    engine_a_regime_label_for_threshold,
)
from factor_scoring import _volatility_scaler


def _load_root_athena_module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_athena_root_for_tests", root / "athena.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_volatility_scaler_uses_score_group_override_for_precious_trackers(monkeypatch):
    """GLD-as-stock should use precious_trackers bands, not stock bands."""
    monkeypatch.setitem(
        CONFIG,
        "VOLATILITY_SCALER_BANDS",
        {
            "stock": {"low": 0.05, "high": 0.20},
            "precious_trackers": {"low": 0.001, "high": 0.004},
        },
    )
    monkeypatch.setitem(CONFIG, "VOLATILITY_SCALER_MULT_LOW", 1.15)
    monkeypatch.setitem(CONFIG, "VOLATILITY_SCALER_MULT_HIGH", 0.85)
    # ATR% = 0.002 — inside precious band → interpolated; below stock low → would be mult_low
    v_precious = _volatility_scaler(2.0, 1000.0, "stock", "precious_trackers")
    v_stock_only = _volatility_scaler(2.0, 1000.0, "stock", "us_stock_single")
    assert v_precious != v_stock_only
    assert abs(v_precious - 1.0) < abs(v_stock_only - 1.0)


def test_engine_a_score_group_thresholds_cover_all_known_groups():
    """Each score_group must have its own floor — not legacy 3-tier or asset_type fallback."""
    thresholds = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {}
    missing = sorted(g for g in ENGINE_A_KNOWN_SCORE_GROUPS if g not in thresholds)
    assert not missing, f"ENGINE_A_SCORE_GROUP_THRESHOLDS missing: {missing}"


def test_expand_engine_a_threshold_updates_maps_asset_class_to_score_groups():
    from scoring import expand_engine_a_threshold_updates

    expanded = expand_engine_a_threshold_updates({"forex": 2.55, "forex_majors": 2.2})
    assert expanded["forex_majors"] == 2.2
    assert expanded["forex_crosses"] == 2.55
    assert expanded["forex_exotics"] == 2.55
    assert expanded["forex_other"] == 2.55


def test_engine_a_threshold_resolves_by_score_group_not_asset_type(monkeypatch):
    """Broad asset_type keys must not override score_group-specific floors."""
    monkeypatch.setitem(CONFIG, "PAIR_PROFILES", {})
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_SCORE_GROUP_THRESHOLDS",
        {
            "default": 9.9,
            "crypto": 9.8,
            "crypto_btc": 2.0,
            "us_stock_single": 1.5,
        },
    )
    assert get_score_threshold({"display": "BTC/USDT", "type": "crypto"}) == 2.0
    assert get_score_threshold({"display": "AAPL", "type": "stock"}) == 1.5


def test_pair_score_group_mapping_examples():
    assert get_pair_score_group({"display": "EUR/USD", "type": "forex"}) == "forex_majors"
    assert get_pair_score_group({"display": "GBP/JPY", "type": "forex"}) == "forex_crosses"
    assert get_pair_score_group({"display": "USD/ZAR", "type": "forex"}) == "forex_exotics"
    assert get_pair_score_group({"display": "BTC/USDT", "type": "crypto"}) == "crypto_btc"
    assert get_pair_score_group({"display": "ETH/USDT", "type": "crypto"}) == "crypto_eth"
    assert get_pair_score_group({"display": "DOGE/USDT", "type": "crypto"}) == "crypto_doge"
    assert get_pair_score_group({"display": "TLT", "type": "stock"}) == "bond_tlt"
    assert (
        get_pair_score_group({"display": "ASX 200", "type": "index"}) == "asian_indices"
    )


ENGINE_B_NON_FOREX_OVERRIDE_REPRESENTATIVES = (
    "crypto_btc",
    "us_stock_single",
    "energy_oil",
    "nat_gas",
)


def test_engine_b_runtime_groups_keep_non_forex_override_coverage():
    groups = ((CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {})
    # Verify representative non-forex groups carry score_group_overrides
    for grp in ENGINE_B_NON_FOREX_OVERRIDE_REPRESENTATIVES:
        assert grp in groups, f"NAKED_ENGINE.score_group_overrides missing {grp}"
    assert bool(CONFIG.get("ENGINE_B_PROFILE_SCORING_ENABLED")) is True


def test_engine_b_non_forex_representatives_have_intraday_min_rr_override():
    """Regression: representative Engine B non-forex groups expose min_rr overrides."""
    groups = ((CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {})
    for grp in ENGINE_B_NON_FOREX_OVERRIDE_REPRESENTATIVES:
        assert float(groups[grp]["intraday"]["min_rr"]) > 0.0


def test_engine_b_forex_uses_base_style_profile_when_group_override_absent():
    groups = ((CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {})
    # Forex groups may carry min_rr overrides but must NOT override min_score
    for grp in ("forex_majors", "forex_crosses", "forex_exotics"):
        if grp in groups:
            for style in ("scalp", "intraday", "swing"):
                style_override = groups[grp].get(style, {})
                assert "min_score" not in style_override, (
                    f"{grp}.{style} must not override min_score — forex uses base profile"
                )
    assert float(CONFIG["NAKED_ENGINE"]["style_profiles"]["intraday"]["min_rr"]) > 0


def test_engine_b_resolved_style_profiles_keep_configured_min_rr_contracts():
    _naked_scan_style_profile = _load_root_athena_module()._naked_scan_style_profile

    _, scalp = _naked_scan_style_profile("scalp", score_group="forex_majors", asset_type="forex")
    _, forex_major = _naked_scan_style_profile("intraday", score_group="forex_majors", asset_type="forex")
    _, forex_cross = _naked_scan_style_profile("intraday", score_group="forex_crosses", asset_type="forex")
    _, btc = _naked_scan_style_profile("intraday", score_group="crypto_btc", asset_type="crypto")
    _, eth = _naked_scan_style_profile("intraday", score_group="crypto_eth", asset_type="crypto")
    _, alt = _naked_scan_style_profile("intraday", score_group="crypto_alt_majors", asset_type="crypto")
    _, commodity = _naked_scan_style_profile("swing", score_group="commodity_other", asset_type="commodity")

    assert float(scalp["min_rr"]) == 1.5
    assert float(forex_major["min_rr"]) == 1.3
    assert float(forex_cross["min_rr"]) == 1.3
    assert float(btc["min_rr"]) == 1.3
    assert float(eth["min_rr"]) == 1.3
    assert float(alt["min_rr"]) == 1.4
    assert float(commodity["min_rr"]) == 1.8


def test_engine_b_base_style_profiles_keep_min_score_contract():
    _naked_scan_style_profile = _load_root_athena_module()._naked_scan_style_profile

    _, scalp = _naked_scan_style_profile("scalp", asset_type="forex")
    _, intraday = _naked_scan_style_profile("intraday", asset_type="forex")
    _, swing = _naked_scan_style_profile("swing", asset_type="forex")

    # B5: YAML base_thresholds (engine_b_config.yaml:9-19) is the source of truth.
    # scalp=4.0, intraday=4.5, swing=5.0. Previously asserted 4.0 for all three,
    # which masked the YAML drift (audit LOW #20).
    assert float(scalp["min_score"]) == 4.0
    assert float(intraday["min_score"]) == 4.5
    assert float(swing["min_score"]) == 5.0
    assert float(scalp["fallback_rr"]) == 1.8
    assert float(intraday["fallback_rr"]) == 1.8
    assert float(swing["fallback_rr"]) == 2.5


def test_engine_b_auto_matches_engine_a_pair_aware_style():
    from engine_b_snapshot import resolve_engine_b_style_profile
    from style_resolver import resolve_auto_style

    cases = [
        ({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}, "intraday"),
        ({"display": "USD/ZAR", "symbol": "USDZAR", "type": "forex"}, "swing"),
        ({"display": "AAVE/USDT", "symbol": "AAVEUSDT", "type": "crypto"}, "swing"),
        # XAU/ZAR and France 40 are per-symbol intraday opt-ins (see
        # style_resolver._INTRADAY_AUTO_SYMBOLS): they were added with
        # intraday-speed ladders that the swing overlay would flatten.
        ({"display": "XAU/ZAR", "symbol": "XAUZAR", "type": "commodity"}, "intraday"),
        ({"display": "Brent Oil", "symbol": "BRENT", "type": "commodity"}, "swing"),
        ({"display": "France 40", "symbol": "FRA40", "type": "index"}, "intraday"),
        ({"display": "MSFT", "symbol": "MSFT", "type": "stock"}, "swing"),
        # BRKb is an ATFX share CFD, so stock_other — which routes intraday onto
        # the cash_equity_standard_dynamic ladder. MSFT stays us_stock_single.
        ({"display": "BRKb", "symbol": "BRKb", "type": "stock"}, "intraday"),
    ]

    for pair, expected in cases:
        score_group = get_pair_score_group(pair)
        engine_a_style = resolve_auto_style(
            "auto",
            pair,
            score_group=score_group,
            asset_type=pair["type"],
        )
        engine_b_style, _profile = resolve_engine_b_style_profile(
            "auto",
            score_group=score_group,
            asset_type=pair["type"],
            symbol=pair["display"],
            config=CONFIG,
        )
        assert engine_a_style == expected
        assert engine_b_style == expected

    explicit_swing, swing_profile = resolve_engine_b_style_profile(
        "swing",
        score_group="us_stock_single",
        asset_type="stock",
        symbol="MSFT",
        config=CONFIG,
    )
    assert explicit_swing == "swing"
    assert swing_profile["entry_tf"] == "M15"


def test_engine_b_cache_uses_one_canonical_symbol_and_clears_analysis_only():
    athena_root = _load_root_athena_module()
    athena_root._engine_b_cache.clear()
    payload = {"pair": "EUR/USD"}

    athena_root._engine_b_cache_put("EUR/USD", payload)
    athena_root._engine_b_cache_put("EURUSD=X", payload)
    athena_root._engine_b_cache_put("EUR_USD_vision", {"grade": "A"})

    assert athena_root._engine_b_cache_get("EUR/USD") is payload
    assert athena_root._engine_b_cache_get("EURUSD=X") is payload
    assert len(athena_root._engine_b_cache) == 2

    athena_root._engine_b_cache_clear_analysis()
    assert athena_root._engine_b_cache_get("EUR/USD") is None
    assert athena_root._engine_b_cache_get("EUR_USD_vision") == {"grade": "A"}


def test_min_confluence_threshold_tiers():
    """3-tier ladder: stable → 1.5; exotic → 1.7; volatile → 2.0.

    Pairs with PAIR_PROFILES overrides are intentionally excluded — those test
    the override path, not the tier resolver.
    """
    original_profiles = CONFIG.get("PAIR_PROFILES")
    original_group_thresholds = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS")
    try:
        CONFIG["PAIR_PROFILES"] = {}  # neutralise overrides for this test
        CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"] = {}  # exercise fallback tiers only
        eur_usd = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
        usd_zar = {"display": "USD/ZAR", "symbol": "USDZAR=X", "type": "forex"}
        btc = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
        cocoa = {"display": "Cocoa", "symbol": "Cocoa", "type": "commodity"}
        aluminium = {"display": "Aluminium", "symbol": "Aluminium", "type": "commodity"}
        nat_gas = {"display": "Nat Gas", "symbol": "NatGas", "type": "commodity"}
        assert get_min_confluence_threshold(eur_usd) == 1.5     # stable
        assert get_min_confluence_threshold(usd_zar) == 1.7     # forex_exotics → exotic
        assert get_min_confluence_threshold(btc) == 2.0         # crypto class → volatile
        assert get_min_confluence_threshold(cocoa) == 1.7       # softs → exotic
        assert get_min_confluence_threshold(aluminium) == 1.5   # base_metals → stable
        assert get_min_confluence_threshold(nat_gas) == 2.0     # nat_gas → volatile
    finally:
        CONFIG["PAIR_PROFILES"] = original_profiles
        CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"] = original_group_thresholds


def test_min_confluence_threshold_uses_configured_score_group_thresholds(monkeypatch):
    monkeypatch.setitem(CONFIG, "PAIR_PROFILES", {})
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_SCORE_GROUP_THRESHOLDS",
        {
            "default": 1.11,
            "forex_exotics": 1.77,
            "crypto_btc": 2.22,
        },
    )

    assert get_min_confluence_threshold(
        {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    ) == 1.11
    assert get_min_confluence_threshold(
        {"display": "USD/ZAR", "symbol": "USDZAR=X", "type": "forex"}
    ) == 1.77
    assert get_min_confluence_threshold(
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
    ) == 2.22


def test_crypto_engine_a_scan_threshold_resolves_to_score_group_floor():
    """Each crypto pair uses its group p70 bar, not a flat asset-class floor."""
    cases = [
        ({"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}, "crypto_btc", 0.90),
        ({"display": "ETH/USDT", "symbol": "ETHUSDT", "type": "crypto"}, "crypto_eth", 0.90),
        ({"display": "SOL/USDT", "symbol": "SOLUSDT", "type": "crypto"}, "crypto_alt_majors", 1.45),
    ]
    thresholds = CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"]
    for pair, expected_group, expected_floor in cases:
        assert get_pair_score_group(pair) == expected_group
        assert get_score_threshold(pair) == expected_floor
        assert thresholds[expected_group] == expected_floor

    assert get_score_threshold(
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
    ) == CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"]["crypto_btc"]


def test_forex_engine_a_scan_thresholds_are_explicit_strict_floor():
    """Every forex pair resolves to an explicit group floor, never a default.

    Floors are per-group p70 bars on the V3 0-3 scale (2026-08-08 recalibration).
    """
    cases = [
        ({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}, "forex_majors", 1.40),
        ({"display": "EUR/CHF", "symbol": "EURCHF", "type": "forex"}, "forex_crosses", 1.30),
        ({"display": "AUD/CHF", "symbol": "AUDCHF", "type": "forex"}, "forex_crosses", 1.30),
        # Not in any routing table — exercises the forex_other fallback.
        ({"display": "EUR/NOK", "symbol": "EURNOK", "type": "forex"}, "forex_other", 1.40),
    ]

    thresholds = CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"]
    for pair, expected_group, expected_floor in cases:
        assert get_pair_score_group(pair) == expected_group, pair["display"]
        assert get_score_threshold(pair) == expected_floor, pair["display"]
        # Explicit means the group has its own entry, not a "default" fallback.
        assert thresholds[expected_group] == expected_floor


def test_equity_and_commodity_groups_use_explicit_stable_floor():
    examples = [
        ({"display": "AAPL", "type": "stock"}, "us_stock_single", 1.30),
        ({"display": "SPY", "type": "stock"}, "us_indices_trackers", 1.05),
        ({"display": "XAU/USD", "type": "commodity"}, "precious_trackers", 0.95),
        ({"display": "Copper", "type": "commodity"}, "copper", 1.00),
        ({"display": "DAX 40", "type": "index"}, "eu_indices", 1.40),
    ]
    for pair, expected_group, expected_threshold in examples:
        assert get_pair_score_group(pair) == expected_group
        assert get_score_threshold(pair) == expected_threshold


def test_etf_pairs_map_to_existing_score_groups(caplog):
    """ETF universe uses type=etf; must not resolve to unknown etf_other."""
    thresholds = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {}
    examples = [
        ({"display": "DIA", "type": "etf"}, "us_indices_trackers"),
        ({"display": "QQQ", "type": "etf"}, "us_indices_trackers"),
        ({"display": "SPY", "type": "etf"}, "us_indices_trackers"),
        ({"display": "SOXX", "type": "etf"}, "us_indices_trackers"),
        ({"display": "GLD", "type": "etf"}, "precious_trackers"),
        ({"display": "SLV", "type": "etf"}, "precious_trackers"),
        ({"display": "GDX", "type": "etf"}, "precious_trackers"),
        ({"display": "USO", "type": "etf"}, "energy_oil"),
        ({"display": "XLE", "type": "etf"}, "energy_oil"),
        ({"display": "EEM", "type": "etf"}, "smallcap_em_etf"),
        ({"display": "IWM", "type": "etf"}, "smallcap_em_etf"),
        ({"display": "TLT", "type": "etf_bond"}, "bond_tlt"),
    ]
    for pair, expected_group in examples:
        assert get_pair_score_group(pair) == expected_group
        expected_threshold = float(thresholds[expected_group])
        assert get_score_threshold(pair) == expected_threshold
    assert "etf_other" not in caplog.text


def test_auto_trade_min_score_does_not_override_engine_a_scan_threshold(monkeypatch):
    """AUTO_TRADE_MIN_SCORE is informational; Engine A scan uses score-group thresholds only."""
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
    expected = get_score_threshold(pair)
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_MIN_SCORE", {"crypto": 9.99})
    assert get_score_threshold(pair) == expected


def test_score_group_requirement_overrides_are_explicit_for_distinct_crypto_and_forex_groups():
    assert CONFIG["ADX_TREND_MIN_CLASS"]["crypto_other"] > CONFIG["ADX_TREND_MIN_CLASS"]["crypto_btc"]
    assert CONFIG["FACTOR_ADX_HARD_FAIL_CLASS"]["crypto_other"] > CONFIG["FACTOR_ADX_HARD_FAIL_CLASS"]["crypto_btc"]
    assert CONFIG["ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS"]["crypto_other"]["min_directional"] > (
        CONFIG["ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS"]["crypto_btc"]["min_directional"]
    )
    assert CONFIG["RSI_BOUNDS"]["crypto_other"] == {"ob": 75, "os": 25}
    assert CONFIG["ADX_TREND_MIN_CLASS"]["forex_exotics"] < CONFIG["ADX_TREND_MIN_CLASS"]["forex_majors"]
    assert CONFIG["ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS"]["forex_exotics"]["min_directional"] < (
        CONFIG["ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS"]["forex_majors"]["min_directional"]
    )
    assert CONFIG["ENGINE_A_CONVICTION_FLOOR_BY_CLASS"]["forex_majors"] == pytest.approx(0.60)
    assert CONFIG["ENGINE_A_ADX_SOURCE_BY_CLASS"]["forex_majors"] == "max"
    assert CONFIG["ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS"]["forex"]["opposed"] == pytest.approx(0.65)
    assert CONFIG["ENGINE_A_SCAN_A_ONLY_AUTO_GATE_ENABLED"] is False


def test_engine_a_regime_label_for_threshold_supports_live_result_shapes(monkeypatch):
    pair = {"display": "EUR/USD", "type": "forex"}
    base = get_score_threshold(pair)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_REGIME_DYNAMIC_THRESHOLDS",
        {"ENABLED": True, "TRENDING_MULTIPLIER": 0.90, "RANGING_MULTIPLIER": 1.10},
    )
    regime = engine_a_regime_label_for_threshold({"regime": {"label": "RANGING"}})
    assert get_score_threshold(pair, regime=regime) == pytest.approx(base * 1.10)


def test_python_defaults_match_runtime_yaml_for_audit_sensitive_gates():
    assert CONFIG["RANGING"]["crypto"] == {
        "dead": 18,
        "choppy": 23,
    }
    assert CONFIG["ADX_TREND_MIN_CLASS"]["crypto"] == 18
    assert CONFIG["ADX_TREND_MIN_CLASS"]["forex"] == 20
    assert CONFIG["AUTO_TRADE_MIN_SCORE"]["index"] == 1.8
    assert CONFIG["THRESHOLD_AUDIT"]["ENABLED"] is True
    assert CONFIG["FACTOR_MIN_DIRECTIONAL_CRYPTO"] == 0.20
    assert "ENGINE_A_COT_ADDON_ASSET_TYPES" in CONFIG


def test_etf_level_atr_class_is_separate_from_stock_identity(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_ATR_LEVEL_CLASS_BY_DISPLAY",
        {"SPY": "etf", "TLT": "etf_bond"},
    )

    assert get_pair_level_atr_class({"display": "SPY", "symbol": "SPY.US", "type": "stock"}) == "etf"
    assert get_pair_level_atr_class({"display": "TLT", "symbol": "TLT.US", "type": "stock"}) == "etf_bond"
    assert get_pair_level_atr_class({"display": "AAPL", "symbol": "AAPL.US", "type": "stock"}) == "stock"


def test_score_group_thresholds_is_active_config():
    original = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS")
    try:
        CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"] = {
            "forex_majors": 2.95,
            "default": 1.5,
        }
        pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
        assert get_min_confluence_threshold(pair) == 2.95
        assert get_backtest_min_score_threshold(pair) == 2.95
    finally:
        CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"] = original


def test_pair_profile_can_override_score_group_and_threshold():
    original = CONFIG.get("PAIR_PROFILES")
    try:
        CONFIG["PAIR_PROFILES"] = {
            "BTC/USDT": {
                "score_group": "crypto_alt_majors",
                "min_confluence": 0.77,
                "allow_lower_threshold": True,
            }
        }
        pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
        assert get_pair_score_group(pair) == "crypto_alt_majors"
        assert get_min_confluence_threshold(pair) == 0.77
    finally:
        CONFIG["PAIR_PROFILES"] = original


def test_backtest_live_threshold_parity():
    """Stage 4.2: Backtest and live use identical Engine A thresholds.

    BT_MIN / BACKTEST_USE_BT_MIN_THRESHOLDS deleted.
    Pair profile min_confluence is the only override.
    """
    original_profiles = CONFIG.get("PAIR_PROFILES")
    try:
        CONFIG["PAIR_PROFILES"] = {
            "BTC/USDT": {
                "score_group": "crypto_btc",
                "min_confluence": 0.99,
                "allow_lower_threshold": True,
            }
        }
        pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
        assert get_min_confluence_threshold(pair) == 0.99
        assert get_backtest_min_score_threshold(pair) == 0.99  # same as live
    finally:
        CONFIG["PAIR_PROFILES"] = original_profiles


def test_rsi_warning_uses_score_group_bounds(monkeypatch):
    def _fake_factor_scores(*_args, **_kwargs):
        return {
            "final_score": 1.5,
            "direction": "LONG",
            "directional_score": 1.0,
            "nondirectional_score": 0.5,
            "factor_scores": {"rsi": 0.5},
            "weights": {},
            "regime": "TRENDING",
        }

    monkeypatch.setattr("factor_scoring.compute_factor_scores", _fake_factor_scores)
    monkeypatch.setattr(
        "confidence_engine.compute_confidence",
        lambda **_kwargs: {"confidence": 0.5},
    )
    monkeypatch.setitem(
        CONFIG,
        "RSI_BOUNDS",
        {
            "stock": {"ob": 70, "os": 30},
            "precious_trackers": {"ob": 75, "os": 25},
        },
    )

    snap = {"rsi": 73, "adx": 30, "close": 100, "ema50": 99, "ema200": 98, "macdHist": 1}
    result = calc_confluence(
        {"snap": snap},
        {"snap": snap},
        {"snap": snap},
        vr=1.0,
        stoch=None,
        pair={"display": "GLD", "symbol": "GLD", "type": "stock"},
        btc_bias="neutral",
        bar_time="2026-05-08T12:00:00+00:00",
    )

    assert result["warnings"] == []


def test_backtest_ignores_legacy_bt_min_field():
    """Stage 4.2: pair profile bt_min is ignored; min_confluence wins."""
    original = CONFIG.get("PAIR_PROFILES")
    try:
        # bt_min in profile is dead code — only min_confluence matters
        CONFIG["PAIR_PROFILES"] = {"XAU/USD": {"bt_min": 1.58, "min_confluence": 1.75}}
        pair = {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
        assert get_backtest_min_score_threshold(pair) == 1.75
        assert get_min_confluence_threshold(pair) == 1.75
    finally:
        CONFIG["PAIR_PROFILES"] = original


def test_backtest_no_dual_threshold_flag():
    """Stage 4.2: BACKTEST_USE_BT_MIN_THRESHOLDS must not exist in CONFIG."""
    assert "BACKTEST_USE_BT_MIN_THRESHOLDS" not in CONFIG
    assert "BT_MIN" not in CONFIG
    assert "BT_MIN_GROUP" not in CONFIG
    assert "FACTOR_WEIGHTS" not in CONFIG


def test_auto_trade_min_score_does_not_change_trade_tier_reason(monkeypatch):
    monkeypatch.setitem(CONFIG, "AUTO_TRADE_MIN_SCORE", {"crypto": 2.4})
    monkeypatch.setitem(CONFIG, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED", False)
    signal = {
        "confluenceScore": 2.1,
        "scanThreshold": 2.0,
        "eventRisk": {"hardBlock": False},
        "macroEventRisk": {"blocked": False},
        "exchangeClosed": False,
        "scanDiagnostics": [],
    }
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True}

    tier, reason = _classify_signal(signal, pair)

    assert tier == "trade"
    assert reason == "Trade-ready"


# test_engine_a_trade_gate_has_backtest_integration_points removed with the
# legacy backtester (archive/backtest_legacy/): the v3 rebuild resolves trade
# eligibility inside evaluate_engine_a_v3 itself, not via backtest wiring.


def test_scanner_persists_tier_reason_field():
    src = Path(__file__).resolve().parents[1] / "scanner.py"
    text = src.read_text(encoding="utf-8")
    assert 'sig["signalTierReason"] = tier_reason' in text


def test_divergence_monitor_replays_shared_factor_path_for_forex():
    src = Path(__file__).resolve().parents[1] / "divergence_monitor.py"
    text = src.read_text(encoding="utf-8")
    assert "from scoring import calc_confluence" in text
    assert "compute_forex_score" not in text


def test_analyze_pair_wires_v3_candle_fetch_meta():
    src = Path(__file__).resolve().parents[1] / "athena.py"
    text = src.read_text(encoding="utf-8")
    assert "evaluate_engine_a_v3" in text
    assert '_v3_signal["candleFetchMeta"]' in text
    assert '"pairSource": pair.get("source")' in text


def test_currency_indices_route_to_forex_majors():
    assert get_pair_score_group({"display": "USDX", "type": "index"}) == "forex_majors"
    assert get_pair_score_group({"display": "EURX", "type": "index"}) == "forex_majors"
    assert get_pair_score_group({"display": "JPYX", "type": "index"}) == "forex_majors"
    assert get_pair_score_group({"display": "US2000", "type": "index"}) == "index_other"
