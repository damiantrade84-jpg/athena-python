"""Tests that the live Engine A factor core stays intentionally small."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from factor_scoring import compute_factor_scores


def _sample_factor_inputs():
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    d1 = {"ema21": 1.20, "ema200": 1.00, "adx": 30.0, "close": 1.25}
    h4 = {
        "ema21": 1.21,
        "ema50": 1.10,
        "rsi": 55.0,
        "macdHist": 0.01,
        "adx": 28.0,
        "close": 1.24,
        "atr": 0.01,
    }
    h1 = {"ema21": 1.22, "ema50": 1.15, "close": 1.24}
    return pair, d1, h4, h1


def test_pair_profile_weight_overrides_do_not_change_live_factor_engine_output():
    original_profiles = CONFIG.get("PAIR_PROFILES")
    try:
        CONFIG["PAIR_PROFILES"] = {
            "EUR/USD": {
                "weight_overrides": {
                    "h4_fib": 1.5,
                    "h1_bb": 0.5,
                }
            }
        }
        pair, d1, h4, h1 = _sample_factor_inputs()
        overridden = compute_factor_scores(d1, h4, h1, pair, [], [], [], 1.0)

        CONFIG["PAIR_PROFILES"] = {}
        baseline = compute_factor_scores(d1, h4, h1, pair, [], [], [], 1.0)

        assert overridden["final_score"] == baseline["final_score"]
        assert overridden["factor_scores"] == baseline["factor_scores"]
        assert overridden["weights"] == baseline["weights"]
    finally:
        CONFIG["PAIR_PROFILES"] = original_profiles


def test_score_group_metadata_does_not_change_output_when_flag_off():
    """With group adjustments disabled, score_group is pure metadata."""
    original = CONFIG.get("ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED")
    try:
        CONFIG["ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED"] = False
        pair, d1, h4, h1 = _sample_factor_inputs()
        baseline = compute_factor_scores(d1, h4, h1, pair, [], [], [], 1.0)

        pair_with_group = dict(pair)
        pair_with_group["score_group"] = "forex_exotics"
        grouped = compute_factor_scores(d1, h4, h1, pair_with_group, [], [], [], 1.0)

        assert grouped["final_score"] == baseline["final_score"]
        assert grouped["factor_scores"] == baseline["factor_scores"]
    finally:
        CONFIG["ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED"] = original


def test_score_group_differentiates_output_when_flag_on():
    """With group adjustments enabled, forex_exotics config diverges from forex."""
    original = CONFIG.get("ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED")
    try:
        CONFIG["ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED"] = True
        pair, d1, h4, h1 = _sample_factor_inputs()
        baseline = compute_factor_scores(d1, h4, h1, pair, [], [], [], 1.0)

        pair_with_group = dict(pair)
        pair_with_group["score_group"] = "forex_exotics"
        grouped = compute_factor_scores(d1, h4, h1, pair_with_group, [], [], [], 1.0)

        assert grouped["final_score"] != baseline["final_score"]
    finally:
        CONFIG["ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED"] = original
