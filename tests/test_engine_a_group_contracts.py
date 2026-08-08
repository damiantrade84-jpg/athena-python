"""Targeted contract tests for Engine A group coverage, thresholds, and profiles.

These tests exist to prevent regression of the per-group differentiation
that the 2026 calibration review and Engine A audit verified.

They must pass on every change that touches group routing or the
ENGINE_A_*_BY_CLASS maps.
"""

from __future__ import annotations

from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from engine_a_scoring_profile import resolve_engine_a_scoring_profile
from scoring import get_pair_score_group, get_score_threshold
from tools.engine_a_group_contracts import validate_engine_a_group_coverage


def test_all_known_groups_have_strict_map_entries():
    """Every group returned by the resolver must have explicit keys in the
    maps that were expanded for per-group control (thresholds + indicator periods).
    """
    report = validate_engine_a_group_coverage()
    assert report["ok"], f"Group coverage contract broken: {report['strict_missing']}"
    assert not report["strict_missing"]


def test_representative_thresholds_per_tier():
    """Spot-check p70-recalibrated group thresholds (2026-08-08 live V3 distribution)."""
    cases = [
        ("EUR/USD", "forex_majors", 1.40),
        ("USD/MXN", "forex_exotics", 1.40),
        ("BTC/USDT", "crypto_btc", 0.90),
        ("ETH/USDT", "crypto_eth", 0.90),
        ("XAU/USD", "precious_trackers", 0.95),
        ("Nat Gas", "nat_gas", 1.80),
        ("S&P 500", "us_indices_trackers", 1.05),
        ("AAPL", "us_stock_single", 1.30),
    ]
    for display, expected_group, expected_threshold in cases:
        pair = {
            "display": display,
            "symbol": display,
            "type": _guess_type(expected_group),
        }
        # Force the correct type for a few known tricky displays used in the audit
        if display == "S&P 500":
            pair["type"] = "index"
        assert get_pair_score_group(pair) == expected_group
        # get_score_threshold uses the live CONFIG + group resolver
        thr = get_score_threshold(pair)
        assert abs(thr - expected_threshold) < 0.01, f"{display} threshold {thr} != {expected_threshold}"


def test_scoring_profile_resolves_for_every_known_group():
    """No group should cause the profile resolver to blow up or lose the base contract."""
    for group in ENGINE_A_KNOWN_SCORE_GROUPS:
        asset = _guess_type(group)
        profile = resolve_engine_a_scoring_profile(
            score_group=group, asset_type=asset, style="swing"
        )
        assert isinstance(profile, dict)
        assert "trend_timeframes" in profile
        assert profile.get("enabled") is True or profile.get("enabled") is False  # must be present


def _guess_type(score_group: str) -> str:
    if score_group.startswith("forex"):
        return "forex"
    if score_group.startswith("crypto"):
        return "crypto"
    if "index" in score_group:
        return "index"
    if "stock" in score_group or score_group in ("bond_tlt", "smallcap_em_etf"):
        return "stock"
    if score_group in ("precious_trackers", "energy_oil", "nat_gas", "copper",
                       "pgm_metals", "base_metals", "softs", "commodity_other"):
        return "commodity"
    return "stock"  # safe fallback for the test data builder
