"""Tests for factor-engine subgroup and pair-profile overrides."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from factor_scoring import _apply_pair_profile_weight_rules


def test_pair_profile_weight_overrides_affect_factor_weights():
    original_profiles = CONFIG.get("PAIR_PROFILES")
    try:
        CONFIG["PAIR_PROFILES"] = {
            "XAU/USD": {
                "weight_overrides": {
                    "h4_fib": 1.5,  # maps to structure
                    "h1_bb": 0.5,   # maps to volatility
                }
            }
        }
        base = {
            "trend": 2.0,
            "momentum": 1.0,
            "derivatives": 1.0,
            "microstructure": 1.0,
            "trend_strength": 1.0,
            "volatility": 1.0,
            "volume": 1.0,
            "structure": 1.0,
            "carry": 1.0,
        }
        out = _apply_pair_profile_weight_rules({"display": "XAU/USD"}, base)
        assert out["structure"] == 1.5
        assert out["volatility"] == 0.5
    finally:
        CONFIG["PAIR_PROFILES"] = original_profiles


def test_score_group_multipliers_apply_to_weights():
    base = {
        "trend": 2.0,
        "momentum": 1.0,
        "derivatives": 1.0,
        "microstructure": 1.0,
        "trend_strength": 1.0,
        "volatility": 1.0,
        "volume": 1.0,
        "structure": 1.0,
        "carry": 1.0,
    }
    out = _apply_pair_profile_weight_rules({"score_group": "crypto_doge"}, base)
    assert out["volatility"] > base["volatility"]
    # microstructure multiplier removed for crypto_doge (zeroed at FACTOR_WEIGHTS level)
    assert out["microstructure"] == base["microstructure"]
