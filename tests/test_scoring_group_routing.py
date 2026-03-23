"""Tests for subgroup routing and threshold resolution."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from scoring import get_pair_score_group, get_min_confluence_threshold


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


def test_min_confluence_uses_group_threshold_when_no_pair_override():
    pair = {"display": "USD/ZAR", "symbol": "USDZAR=X", "type": "forex"}
    # forex_exotics threshold from config defaults is 0.65
    assert get_min_confluence_threshold(pair) == 0.65


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


def test_forex_scoring_source_exposes_score_group_adjustments():
    src = Path(__file__).resolve().parents[1] / "forex_scoring.py"
    text = src.read_text(encoding="utf-8")
    assert "score_group: Optional[str] = None" in text
    assert "score_group_adjustments" in text
