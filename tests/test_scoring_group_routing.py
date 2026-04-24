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


def test_min_confluence_uses_class_threshold_when_live_group_config_absent():
    pair = {"display": "USD/ZAR", "symbol": "USDZAR=X", "type": "forex"}
    assert get_min_confluence_threshold(pair) == CONFIG["MIN_CONFLUENCE_CLASS"]["forex"]


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


def test_backtest_min_score_falls_back_to_live_chain_when_no_bt_override():
    """Engine A backtest should mirror live threshold routing unless bt_min is explicit."""
    original_profiles = CONFIG.get("PAIR_PROFILES")
    original_bt = dict(CONFIG.get("BT_MIN") or {})
    try:
        CONFIG["PAIR_PROFILES"] = {
            "BTC/USDT": {
                "score_group": "crypto_btc",
                "min_confluence": 0.99,
            }
        }
        CONFIG["BT_MIN"] = {**original_bt, "crypto": 0.12}
        pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
        assert get_min_confluence_threshold(pair) == 0.99
        assert get_backtest_min_score_threshold(pair) == 0.99
    finally:
        CONFIG["PAIR_PROFILES"] = original_profiles
        CONFIG["BT_MIN"] = original_bt


def test_backtest_min_score_pair_profile_bt_min_override():
    original = CONFIG.get("PAIR_PROFILES")
    original_bt = dict(CONFIG.get("BT_MIN") or {})
    original_rm = CONFIG.get("RESEARCH_MODE", False)
    original_btf = CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
    try:
        CONFIG["PAIR_PROFILES"] = {"XAU/USD": {"bt_min": 1.58, "min_confluence": 5.8}}
        CONFIG["BT_MIN"] = {**original_bt, "commodity": 0.70}
        CONFIG["RESEARCH_MODE"] = True
        CONFIG["BACKTEST_USE_BT_MIN_THRESHOLDS"] = False
        pair = {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
        assert get_backtest_min_score_threshold(pair) == 1.58
    finally:
        CONFIG["PAIR_PROFILES"] = original
        CONFIG["BT_MIN"] = original_bt
        CONFIG["RESEARCH_MODE"] = original_rm
        CONFIG["BACKTEST_USE_BT_MIN_THRESHOLDS"] = original_btf


def test_backtest_min_score_uses_bt_chain_when_backtest_flag_only():
    """BACKTEST_USE_BT_MIN_THRESHOLDS enables BT path without RESEARCH_MODE."""
    original = CONFIG.get("PAIR_PROFILES")
    original_bt = dict(CONFIG.get("BT_MIN") or {})
    original_rm = CONFIG.get("RESEARCH_MODE", False)
    original_btf = CONFIG.get("BACKTEST_USE_BT_MIN_THRESHOLDS", False)
    try:
        CONFIG["PAIR_PROFILES"] = {"XAU/USD": {"bt_min": 1.58, "min_confluence": 5.8}}
        CONFIG["BT_MIN"] = {**original_bt, "commodity": 0.70}
        CONFIG["RESEARCH_MODE"] = False
        CONFIG["BACKTEST_USE_BT_MIN_THRESHOLDS"] = True
        pair = {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
        assert get_backtest_min_score_threshold(pair) == 1.58
        assert get_min_confluence_threshold(pair) == 5.8
    finally:
        CONFIG["PAIR_PROFILES"] = original
        CONFIG["BT_MIN"] = original_bt
        CONFIG["RESEARCH_MODE"] = original_rm
        CONFIG["BACKTEST_USE_BT_MIN_THRESHOLDS"] = original_btf


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
