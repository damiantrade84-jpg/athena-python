"""Engine B trigger/setup TF calibration (M5/M15/M30 only)."""

from __future__ import annotations

import pytest

from config import CONFIG
from market_structure import (
    NakedEngine,
    engine_b_trigger_atr_period,
    resolve_engine_b_trigger_tf_calibration,
)


def _make_candles(n: int = 80, start: float = 100.0) -> list[dict]:
    candles = []
    price = start
    for i in range(n):
        price += 0.08 if i % 5 else -0.05
        candles.append(
            {
                "open": price - 0.02,
                "high": price + 0.6,
                "low": price - 0.6,
                "close": price,
                "volume": 1000 + i,
            }
        )
    return candles


_WICK_CANDLES = [
    {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2},
    {"open": 100.2, "high": 100.4, "low": 99.8, "close": 100.0},
    # body=1, lower_wick=1.4 — fails majors 1.5, passes legacy 1.2
    {"open": 100.0, "high": 100.2, "low": 98.6, "close": 101.0},
]


@pytest.mark.parametrize(
    "group,asset,expected_atr,expected_wick,expected_engulf,expected_close",
    [
        ("forex_majors", "forex", 10, 1.30, 0.18, 0.68),
        ("forex_crosses", "forex", 11, 1.25, 0.18, 0.68),
        ("forex_exotics", "forex", 12, 1.2, 0.20, 0.65),
        ("crypto_btc", "crypto", 10, 1.0, 0.30, 0.75),
        ("crypto_other", "crypto", 11, 1.0, 0.35, 0.75),
        ("precious_trackers", "commodity", 11, 1.0, 0.30, 0.75),  # wick-heavy XAU like crypto (was 10/1.3/0.25)
        ("energy_oil", "commodity", 12, 1.2, 0.30, 0.70),
        ("bond_tlt", "stock", 10, 1.4, 0.22, 0.70),
        ("us_stock_single", "stock", 10, 1.4, 0.22, 0.70),
    ],
)
def test_trigger_calibration_resolves_per_score_group(
    group, asset, expected_atr, expected_wick, expected_engulf, expected_close
):
    cal = resolve_engine_b_trigger_tf_calibration(group, asset)
    assert cal["trigger_atr"] == expected_atr
    assert cal["rejection_wick_body"] == expected_wick
    assert cal["engulfing_body_atr"] == expected_engulf
    assert cal["strong_close_pct"] == expected_close
    assert "trigger_adx" not in cal
    assert engine_b_trigger_atr_period(group, asset, "M15") == expected_atr
    assert engine_b_trigger_atr_period(group, asset, "M30") == expected_atr
    assert engine_b_trigger_atr_period(group, asset, "M5") == expected_atr


def test_structural_tfs_keep_atr_period_14():
    for tf in ("D1", "H4", "H1"):
        assert engine_b_trigger_atr_period("forex_majors", "forex", tf) == 14


def test_missing_group_falls_back_to_hardcoded_defaults():
    cal = resolve_engine_b_trigger_tf_calibration("not_a_real_group", "forex")
    assert cal == {
        "trigger_atr": 14,
        "rejection_wick_body": 1.2,
        "engulfing_body_atr": 0.2,
        "strong_close_pct": 0.70,
    }
    assert engine_b_trigger_atr_period("not_a_real_group", "forex", "M15") == 14


def test_h1_struct_atr_period_stays_14_while_m15_resolves_calibrated():
    candles = _make_candles(120)
    engine = NakedEngine()
    assert engine_b_trigger_atr_period("forex_majors", "forex", "H1") == 14
    assert engine_b_trigger_atr_period("forex_majors", "forex", "M15") == 10
    h1_atr = engine._compute_atr_from_candles(candles, period=14)
    m15_atr = engine._compute_atr_from_candles(candles, period=10)
    assert h1_atr > 0 and m15_atr > 0


def test_h1_trigger_keeps_legacy_thresholds_for_forex_majors():
    """Regression: H1 must NOT receive class wick 1.5 even for forex_majors."""
    engine = NakedEngine()
    h1 = engine._price_action_trigger(
        _WICK_CANDLES,
        direction="LONG",
        atr=1.0,
        zone_hit=False,
        bos_confirmed=False,
        asset_type="forex",
        score_group="forex_majors",
        trigger_tf="H1",
    )
    assert h1["rejection"] is True


def test_m15_nested_subrow_merges_when_present(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_B_TRIGGER_TF_CALIBRATION",
        {
            "forex_majors": {
                "trigger_atr": 10,
                "rejection_wick_body": 1.5,
                "engulfing_body_atr": 0.25,
                "strong_close_pct": 0.70,
                "M15": {
                    "trigger_atr": 9,
                    "rejection_wick_body": 1.35,
                },
            }
        },
    )
    cal_m15 = resolve_engine_b_trigger_tf_calibration("forex_majors", "forex", "M15")
    cal_m5 = resolve_engine_b_trigger_tf_calibration("forex_majors", "forex", "M5")
    assert cal_m15["trigger_atr"] == 9
    assert cal_m15["rejection_wick_body"] == pytest.approx(1.35)
    assert cal_m15["engulfing_body_atr"] == pytest.approx(0.25)  # inherited flat
    assert cal_m5["trigger_atr"] == 10  # no M5 sub-row
    assert engine_b_trigger_atr_period("forex_majors", "forex", "M15") == 9


def test_m15_trigger_uses_phase1_forex_majors_wick_130():
    engine = NakedEngine()
    m15 = engine._price_action_trigger(
        _WICK_CANDLES,
        direction="LONG",
        atr=1.0,
        zone_hit=False,
        bos_confirmed=False,
        asset_type="forex",
        score_group="forex_majors",
        trigger_tf="M15",
    )
    # 2026-08-05 Phase 1: wick/body=1.4 now clears the calibrated 1.30 floor.
    assert m15["rejection"] is True


def test_trending_cap_applies_after_class_lookup():
    engine = NakedEngine()
    candles = [
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2},
        {"open": 100.2, "high": 100.4, "low": 99.8, "close": 100.0},
        {"open": 100.0, "high": 100.2, "low": 98.9, "close": 101.0},
    ]
    without_trend = engine._price_action_trigger(
        candles,
        direction="LONG",
        atr=1.0,
        zone_hit=False,
        bos_confirmed=False,
        is_trending=False,
        asset_type="forex",
        score_group="forex_majors",
        trigger_tf="M15",
    )
    with_trend = engine._price_action_trigger(
        candles,
        direction="LONG",
        atr=1.0,
        zone_hit=False,
        bos_confirmed=False,
        is_trending=True,
        asset_type="forex",
        score_group="forex_majors",
        trigger_tf="M15",
    )
    assert without_trend["rejection"] is False
    assert with_trend["rejection"] is True


def test_config_block_present():
    keyed = CONFIG.get("ENGINE_B_TRIGGER_TF_CALIBRATION") or {}
    assert "forex_majors" in keyed
    assert "crypto_btc" in keyed
    assert keyed["forex_majors"]["trigger_atr"] == 10
    # 2026-08-05 Phase 1: lock the promoted config values at the resolver boundary.
    assert keyed["forex_majors"]["rejection_wick_body"] == pytest.approx(1.30)
    assert keyed["forex_majors"]["engulfing_body_atr"] == pytest.approx(0.18)
    assert keyed["forex_majors"]["strong_close_pct"] == pytest.approx(0.68)
    assert keyed["forex_crosses"]["rejection_wick_body"] == pytest.approx(1.25)
    assert keyed["forex_crosses"]["engulfing_body_atr"] == pytest.approx(0.18)
    assert keyed["forex_crosses"]["strong_close_pct"] == pytest.approx(0.68)
    assert "trigger_adx" not in keyed["forex_majors"]

    naked = CONFIG.get("NAKED_ENGINE") or {}
    assert naked["zone_touch_tolerance_atr_mult"] == pytest.approx(0.32)
    assert naked["zone_proximity_atr_mult"]["default"] == pytest.approx(0.75)
    assert naked["zone_proximity_atr_mult"]["forex_majors"] == pytest.approx(0.75)
    assert naked["ob_min_strength"] == pytest.approx(42)
    assert CONFIG["ENGINE_B_M5_PULLBACK_REFINEMENT_ENABLED"] is True
    assert CONFIG["ENGINE_B_OB_CONFLUENCE_DIRECTIONAL"] is True


def test_phase1_location_values_reach_zone_context():
    """2026-08-05 Phase 1: loaded proximity/touch values reach the live consumer."""
    local_engine = NakedEngine()
    zone = {"lower": 98.0, "upper": 99.0, "center": 98.5}
    ctx = local_engine._zone_context(
        zone,
        current_price=99.74,
        atr=1.0,
        direction="LONG",
        candles=[{"open": 99.6, "high": 99.8, "low": 99.31, "close": 99.5}],
        asset_type="forex",
        score_group="forex_majors",
    )
    assert ctx["near_zone"] is True
    assert ctx["zone_touched"] is True
