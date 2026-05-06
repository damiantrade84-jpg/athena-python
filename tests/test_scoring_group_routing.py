"""Tests for subgroup routing and threshold resolution."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from scoring import (
    get_backtest_min_score_threshold,
    get_min_confluence_threshold,
    get_pair_score_group,
)


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


def test_engine_b_runtime_groups_keep_non_forex_override_coverage():
    groups = ((CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {})
    # Verify representative non-forex groups carry score_group_overrides
    assert "crypto_btc" in groups
    assert "us_stock_single" in groups
    assert "energy_oil" in groups
    assert "nat_gas" in groups
    assert bool(CONFIG.get("ENGINE_B_PROFILE_SCORING_ENABLED")) is True


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


def test_min_confluence_threshold_tiers():
    """3-tier ladder: stable → 1.5; exotic → 1.7; volatile → 2.0.

    Pairs with PAIR_PROFILES overrides are intentionally excluded — those test
    the override path, not the tier resolver.
    """
    original_profiles = CONFIG.get("PAIR_PROFILES")
    try:
        CONFIG["PAIR_PROFILES"] = {}  # neutralise overrides for this test
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


def test_min_confluence_class_is_not_live_threshold_input():
    original = CONFIG.get("MIN_CONFLUENCE_CLASS")
    try:
        CONFIG["MIN_CONFLUENCE_CLASS"] = {"forex": 2.95}
        # Stable-tier forex sanity-check that legacy class config is ignored.
        pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
        assert get_min_confluence_threshold(pair) == 1.5
        assert get_backtest_min_score_threshold(pair) == 1.5
    finally:
        CONFIG["MIN_CONFLUENCE_CLASS"] = original


def test_pair_profile_can_override_score_group_and_threshold():
    original = CONFIG.get("PAIR_PROFILES")
    try:
        CONFIG["PAIR_PROFILES"] = {
            "BTC/USDT": {"score_group": "crypto_alt_majors", "min_confluence": 0.77}
        }
        pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
        assert get_pair_score_group(pair) == "crypto_alt_majors"
        assert get_min_confluence_threshold(pair) == 0.77
    finally:
        CONFIG["PAIR_PROFILES"] = original


def test_backtest_live_threshold_parity():
    """Stage 4.2: Backtest and live use identical 2-tier thresholds.

    BT_MIN / BACKTEST_USE_BT_MIN_THRESHOLDS deleted.
    Pair profile min_confluence is the only override.
    """
    original_profiles = CONFIG.get("PAIR_PROFILES")
    try:
        CONFIG["PAIR_PROFILES"] = {
            "BTC/USDT": {
                "score_group": "crypto_btc",
                "min_confluence": 0.99,
            }
        }
        pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
        assert get_min_confluence_threshold(pair) == 0.99
        assert get_backtest_min_score_threshold(pair) == 0.99  # same as live
    finally:
        CONFIG["PAIR_PROFILES"] = original_profiles


def test_backtest_ignores_legacy_bt_min_field():
    """Stage 4.2: pair profile bt_min is ignored; min_confluence wins."""
    original = CONFIG.get("PAIR_PROFILES")
    try:
        # bt_min in profile is dead code — only min_confluence matters
        CONFIG["PAIR_PROFILES"] = {"XAU/USD": {"bt_min": 1.58, "min_confluence": 1.25}}
        pair = {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
        assert get_backtest_min_score_threshold(pair) == 1.25
        assert get_min_confluence_threshold(pair) == 1.25
    finally:
        CONFIG["PAIR_PROFILES"] = original


def test_backtest_no_dual_threshold_flag():
    """Stage 4.2: BACKTEST_USE_BT_MIN_THRESHOLDS must not exist in CONFIG."""
    assert "BACKTEST_USE_BT_MIN_THRESHOLDS" not in CONFIG
    assert "BT_MIN" not in CONFIG
    assert "BT_MIN_GROUP" not in CONFIG


def test_divergence_monitor_replays_shared_factor_path_for_forex():
    src = Path(__file__).resolve().parents[1] / "divergence_monitor.py"
    text = src.read_text(encoding="utf-8")
    assert "from scoring import calc_confluence" in text
    assert "compute_forex_score" not in text


def test_analyze_pair_wires_divergence_monitor_and_candle_fetch_meta():
    src = Path(__file__).resolve().parents[1] / "athena.py"
    text = src.read_text(encoding="utf-8")
    assert "ENGINE_A_DIVERGENCE_MONITOR_ENABLED" in text
    assert "check_divergence(" in text
    assert 'signal["candleFetchMeta"] = _candle_fetch_meta' in text
