"""Sleeve manifest + overlay helpers. Does not run a backtest."""
from __future__ import annotations

from athena_research.engine_a_sleeves.run import (
    VARIANT_OVERLAYS,
    _deep_merge,
    _sleeve_verdict,
    load_manifest,
)


def test_sleeve_manifest_has_the_five_point9_cohorts():
    manifest = load_manifest()
    assert set(manifest["sleeves"]) == {"F1", "F1b", "F2", "F3", "F4"}
    assert {p["display"] for p in manifest["sleeves"]["F1"]["pairs"]} >= {
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
    }
    assert "USDX" not in {p["display"] for p in manifest["sleeves"]["F1"]["pairs"]}
    assert manifest["sleeves"]["F3"]["pairs"] == [
        {"display": "XAU/USD", "symbol": "GC=F", "type": "commodity", "source": "mt5"}
    ]
    assert "BTC/USDT" not in {p["display"] for p in manifest["sleeves"]["F4"]["pairs"]}


def test_tuning_lab_tf_variant_forces_liquid_metals_h4_m30():
    overlay = VARIANT_OVERLAYS["tuning_lab_tf"]
    roles = overlay["ENGINE_TF_ROLE_OVERRIDES"]["BY_GROUP"]["liquid_metals"]["intraday"]
    assert roles == {
        "regime": "H4",
        "bias": "H4",
        "structure": "H4",
        "setup": "H4",
        "trigger": "M30",
    }


def test_deep_merge_keeps_unrelated_nested_keys():
    base = {"ENGINE_A_V3_SETUP_UPGRADE": {"ENABLED": True, "MIN_CONFLUENCE_FRAC": 0.75}}
    merged = _deep_merge(base, VARIANT_OVERLAYS["mr_floor_025"])
    assert merged["ENGINE_A_V3_SETUP_UPGRADE"]["ENABLED"] is True
    assert merged["ENGINE_A_V3_SETUP_UPGRADE"]["MR_MIN_CONFLUENCE_FRAC"] == 0.25
    assert merged["ENGINE_A_V3_SETUP_UPGRADE"]["MIN_CONFLUENCE_FRAC"] == 0.75


def test_sleeve_verdict_requires_holdout_n_and_sqn():
    short = [{"holdoutN": 10, "holdoutSqn": 3.0}]
    assert _sleeve_verdict(short, min_n=30, min_sqn=2.0) == "INSUFFICIENT_SAMPLE"
    mixed = [
        {"holdoutN": 20, "holdoutSqn": 2.5},
        {"holdoutN": 15, "holdoutSqn": 0.4},
    ]
    assert _sleeve_verdict(mixed, min_n=30, min_sqn=2.0) == "RESEARCH_ONLY"
    good = [
        {"holdoutN": 20, "holdoutSqn": 2.5},
        {"holdoutN": 15, "holdoutSqn": 2.1},
    ]
    assert _sleeve_verdict(good, min_n=30, min_sqn=2.0) == "PASS"
