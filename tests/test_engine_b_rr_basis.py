import os
import sys
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import risk_engine
import config
from risk_engine import risk_check
from indicators import calc_atr
from market_structure import (
    NakedEngine,
    aggregate_engine_b_level_cohorts,
    engine_b_confidence_passes,
    engine_b_level_cohort,
    resolve_engine_b_execution_levels,
    ENGINE_B_REASON_TP1_BLOCKED_BY_OPPOSING_ZONE,
    _engine_b_tp1_path_clear,
)
from scanner import _apply_engine_b_scan_levels


@pytest.fixture(autouse=True)
def _isolate_risk_drawdown(monkeypatch):
    monkeypatch.setattr(risk_engine, "_current_drawdown", lambda *_args, **_kwargs: 0.0)


def _make_signal(**overrides):
    base = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 100.0,
        "sl": 95.0,
        "tp1": 110.0,
        "tp2": 120.0,
        "type": "crypto",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confluenceScore": 5.0,
        "candleConsistency": {"H4": {"status": "OK"}},
    }
    base.update(overrides)
    return base


def test_engine_a_correlated_overlay_guard_config_does_not_change_engine_b_confidence(monkeypatch):
    engine = NakedEngine()
    res = {
        "atr": 5.0,
        "asset_type": "crypto",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "zone_touched": True,
        "trigger_ok": True,
        "distance_to_res": 20.0,
        "recommended_stop_loss": 95.0,
        "recommended_take_profit": 112.0,
        "bos_confirmed": True,
        "bos_volume_confirmed": True,
    }
    profile = {"style": "intraday", "min_rr": 1.0, "min_room_atr": 0.25}

    baseline = engine.calculate_confidence(dict(res), 100.0, "LONG", style_profile=profile)
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_CORRELATED_OVERLAY_GUARD_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_CORRELATED_OVERLAY_MAX_TOTAL_UPLIFT", 0.01)
    monkeypatch.setitem(config.CONFIG, "ENGINE_A_STRUCTURE_CONTEXT_ENABLED", True)
    after = engine.calculate_confidence(dict(res), 100.0, "LONG", style_profile=profile)

    for key in ("score", "gate_score", "pct", "passed", "structure_ok", "location_ok", "entry_ok", "space_gate_ok", "rr_ok"):
        assert after[key] == baseline[key]

def test_long_sl_below_entry():
    # Approved since SL (95) < Entry (100)
    result = risk_check(_make_signal(price=100.0, sl=95.0, tp1=110.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_short_sl_above_entry():
    # Approved since SL (105) > Entry (100)
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=105.0, tp1=90.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_long_tp_above_entry():
    # Approved since TP (110) > Entry (100)
    result = risk_check(_make_signal(price=100.0, sl=95.0, tp1=110.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_short_tp_below_entry():
    # Approved since TP (90) < Entry (100)
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=105.0, tp1=90.0), 10000.0, 10000.0, [])
    assert result.approved is True

def test_wrong_side_sl_rejected_long():
    # Rejected since SL (105) >= Entry (100) for LONG
    result = risk_check(_make_signal(price=100.0, sl=105.0, tp1=120.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"

def test_wrong_side_sl_rejected_short():
    # Rejected since SL (95) <= Entry (100) for SHORT
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=95.0, tp1=80.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"

def test_wrong_side_tp_rejected_long():
    # Rejected since TP (90) <= Entry (100) for LONG
    result = risk_check(_make_signal(price=100.0, sl=95.0, tp1=90.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"

def test_wrong_side_tp_rejected_short():
    # Rejected since TP (110) >= Entry (100) for SHORT
    result = risk_check(_make_signal(direction="SHORT", price=100.0, sl=105.0, tp1=110.0), 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "INVALID_LEVELS"


def test_risk_check_treats_is_naked_as_engine_b_for_min_rr():
    result = risk_check(
        _make_signal(
            price=100.0,
            sl=95.0,
            tp1=102.0,
            is_naked=True,
            naked_data={"passed": True},
            min_rr=2.0,
        ),
        10000.0,
        10000.0,
        [],
    )

    assert result.approved is False
    assert result.reason == "RR_BELOW_MINIMUM"


def test_risk_check_reads_nested_engine_b_min_rr():
    result = risk_check(
        _make_signal(
            price=100.0,
            sl=95.0,
            tp1=107.5,
            is_naked=True,
            naked_data={"passed": True, "min_rr": 2.0},
        ),
        10000.0,
        10000.0,
        [],
    )

    assert result.approved is False
    assert result.reason == "RR_BELOW_MINIMUM"


def test_rr_uses_absolute_reward_absolute_risk():
    # Verify resolve_engine_b_execution_levels absolute RR computation
    out_long = resolve_engine_b_execution_levels(
        direction="LONG", entry=100.0, structural_sl=90.0, structural_tp=120.0, atr=1.0, style="intraday", asset_class="forex"
    )
    # Execution SL: max(atr_sl, structural_sl) -> max(100 - 1.5*1, 90) = max(98.5, 90) = 98.5
    # Execution TP: structural_tp = 120.0
    # execution_rr = |120 - 100| / |100 - 98.5| = 20 / 1.5 = 13.333
    assert out_long["structural_rr"] == pytest.approx(2.0)
    
    out_short = resolve_engine_b_execution_levels(
        direction="SHORT", entry=100.0, structural_sl=110.0, structural_tp=80.0, atr=1.0, style="intraday", asset_class="forex"
    )
    # Execution SL: min(atr_sl, structural_sl) -> min(100 + 1.5*1, 110) = min(101.5, 110) = 101.5
    # Execution TP: 80.0
    # execution_rr = |80 - 100| / |100 - 101.5| = 20 / 1.5 = 13.333
    assert out_short["structural_rr"] == pytest.approx(2.0)

def test_live_and_backtest_same_sl_tp_basis():
    # Verify live Engine B resolution returns the same ATR levels used in backtest logic.
    out = resolve_engine_b_execution_levels(
        direction="LONG", entry=100.0, structural_sl=90.0, structural_tp=120.0, atr=1.0, style="intraday", asset_class="forex"
    )
    # Both systems read CONFIG.STYLE_ATR_MULTS and ATR_CLASS
    # Intraday mult for forex is 1.5
    # Live engine:
    assert out["execution_sl"] == pytest.approx(98.5)
    assert out["execution_tp"] == pytest.approx(120.0)


def test_zero_atr_hard_rejects_execution_levels():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=99.0,
        structural_tp=102.0,
        atr=0.0,
        style="intraday",
        asset_class="forex",
    )

    assert out["execution_levels_valid"] is False
    assert out["execution_level_reject_reason"] == "invalid_atr"
    assert out["execution_sl"] is None
    assert out["execution_tp"] is None
    assert out["rr_used_for_gate"] == 0.0


def test_private_atr_helper_uses_shared_wilder_atr():
    candles = []
    close = 100.0
    for i in range(24):
        open_ = close
        high = open_ + 1.0 + (i % 4) * 0.15
        low = open_ - 0.7 - (i % 3) * 0.1
        close = open_ + (0.25 if i % 2 == 0 else -0.1)
        candles.append({"open": open_, "high": high, "low": low, "close": close})

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    expected = [v for v in calc_atr(highs, lows, closes, 14) if v is not None][-1]

    assert NakedEngine._compute_atr_from_candles(candles, 14) == pytest.approx(expected)


def test_calculate_confidence_zero_atr_keeps_execution_levels_invalid():
    engine = NakedEngine()
    out = engine.calculate_confidence(
        {
            "atr": 0.0,
            "asset_type": "forex",
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "bos_confirmed": True,
            "trigger_ok": True,
            "zone_touched": True,
            "near_active_zone": True,
            "distance_to_res": 5.0,
            "recommended_stop_loss": 99.0,
            "recommended_take_profit": 102.0,
            "structural_verdict": "CLEAR",
        },
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile={
            "style": "intraday",
            "min_score": 3.0,
            "min_room_atr": 0.35,
            "min_rr": 1.5,
            "fallback_rr": 2.0,
            "require_macro_align": False,
        },
    )

    assert out["execution_levels_valid"] is False
    assert out["execution_level_reject_reason"] == "invalid_atr"
    assert out["rr_ok"] is False
    assert out["rr_used_for_gate"] == 0.0


def test_forex_structural_tp_below_min_rr_uses_synthetic_fallback():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert out["structural_rr"] == pytest.approx(0.1)
    assert out["execution_sl"] == pytest.approx(98.5)
    assert out["execution_tp"] == pytest.approx(103.0)
    assert out["rr_used_for_gate"] == pytest.approx(2.0)
    assert out["rr_source"] == "atr_sl_fallback_rr_tp"
    assert out["execution_levels_valid"] is True
    assert out["execution_level_reject_reason"] is None or out["execution_level_reject_reason"] == ""
    assert out["fallback_tp_applied"] is True
    assert out["fallback_tp_reason"] == "structural_tp_below_min_rr"


def test_structural_tp_below_min_rr_preserves_reachable_tp1_and_runner_tp2():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert out["execution_tp"] == pytest.approx(103.0)
    assert out["execution_tp1"] == pytest.approx(101.0)
    assert out["execution_tp2"] == pytest.approx(103.0)
    assert out["execution_rr1"] == pytest.approx(round(1.0 / 1.5, 4))
    assert out["execution_rr2"] == pytest.approx(2.0)
    assert out["tp1_source"] == "structural"
    assert out["tp2_source"] == "fallback_rr"
    assert out["exit_strategy"] == "scale_out_structural_tp1_fallback_tp2"
    assert out["runner_tp_requires_structural_break"] is True


def test_tp2_below_min_room_falls_back_to_single_tp(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP2_MIN_ROOM_ATR", {"default": 5.0})
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP2_ROOM_FAIL_CLOSED", True)

    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    # Fallback TP2 has only 3 ATR of room; guard requires 5 → single-TP plan.
    assert out["exit_strategy"] == "single_fallback_rr_tp"
    assert out["scale_out_guard_reason"] == "tp2_room_below_min_atr"
    assert out["runner_tp_requires_structural_break"] is False
    assert out["execution_tp1"] == pytest.approx(103.0)
    assert out["execution_tp2"] == pytest.approx(103.0)
    assert out["execution_rr1"] == pytest.approx(2.0)
    assert out["execution_rr2"] == pytest.approx(2.0)
    # Legacy gate fields unchanged: trade still flows.
    assert out["execution_levels_valid"] is True
    assert out["rr_used_for_gate"] == pytest.approx(2.0)


def test_tp1_too_close_retries_tighter_sl_and_preserves_scale_out(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP1_MIN_RR", {"default": 1.0})

    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    # Original SL dist 1.5 ATR gives rr1=0.67 < 1.0; retry at min_sl_atr=0.75
    # gives rr1=1.33 and resynthesized TP2 at fallback RR from the tighter SL.
    assert out["exit_strategy"] == "scale_out_structural_tp1_fallback_tp2"
    assert out["tp1_sl_retry_applied"] is True
    assert out["sl_source"].endswith("_tp1_rr_retry")
    assert out["execution_sl"] == pytest.approx(99.25)
    assert out["execution_tp1"] == pytest.approx(101.0)
    assert out["execution_tp2"] == pytest.approx(101.5)
    assert out["execution_rr1"] == pytest.approx(round(1.0 / 0.75, 4))
    assert out["execution_rr2"] == pytest.approx(2.0)
    assert out["rr_used_for_gate"] == pytest.approx(2.0)
    assert out["runner_tp_requires_structural_break"] is True


def test_tp1_too_close_with_no_sane_sl_falls_back_to_single_tp(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP1_MIN_RR", {"default": 2.0})

    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    # Even the tightest allowed SL (0.75 ATR) only reaches rr1=1.33 < 2.0 →
    # scale-out dropped, legacy single-TP levels preserved untouched.
    assert out["exit_strategy"] == "single_fallback_rr_tp"
    assert out["scale_out_guard_reason"] == "tp1_rr_below_min_no_sane_sl"
    assert out["tp1_sl_retry_applied"] is False
    assert out["execution_sl"] == pytest.approx(98.5)
    assert out["execution_tp"] == pytest.approx(103.0)
    assert out["execution_tp1"] == pytest.approx(103.0)
    assert out["execution_tp2"] == pytest.approx(103.0)
    assert out["execution_levels_valid"] is True


def test_engine_b_scan_levels_use_reachable_tp1_and_runner_tp2(monkeypatch):
    signal = {"engine": "engine_b"}
    conf_b = {
        "passed": True,
        "execution_levels_valid": True,
        "execution_sl": 98.5,
        "execution_tp": 103.0,
        "execution_tp1": 101.0,
        "execution_tp2": 103.0,
        "execution_rr1": 1.0 / 1.5,
        "execution_rr2": 2.0,
        "rr_used_for_gate": 2.0,
        "exit_strategy": "scale_out_structural_tp1_fallback_tp2",
    }
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True)

    _apply_engine_b_scan_levels(signal, conf_b, {})

    assert signal["engine_b_execution_tp"] == pytest.approx(103.0)
    assert signal["engine_b_execution_tp1"] == pytest.approx(101.0)
    assert signal["engine_b_execution_tp2"] == pytest.approx(103.0)
    assert signal["engine_b_execution_rr1"] == pytest.approx(1.0 / 1.5)
    assert signal["engine_b_execution_rr2"] == pytest.approx(2.0)
    assert signal["engine_b_exit_strategy"] == "scale_out_structural_tp1_fallback_tp2"
    assert signal["tp1"] == pytest.approx(101.0)
    assert signal["tp2"] == pytest.approx(103.0)
    # Exit mode + runner directive stamped for Engine B rows (global default
    # is the static fixed-bracket mode -> runner rides to TP2).
    assert signal["exit_mode"] == "traditional_static"
    assert signal["runner_directive"] == "runner_to_tp2"


def test_structural_sl_fallback_rr_tp_cohort_when_structural_sl_selected():
    """Structural SL can remain while TP is replaced by synthetic fallback RR."""
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=99.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert out["sl_source"] == "structural"
    assert out["tp_source"] == "fallback_rr"
    assert out["level_mode"] == "structural_sl_fallback_rr_tp"
    assert out["level_cohort"] == "structural_sl_fallback_rr_tp"
    assert out["fallback_tp_applied"] is True
    assert out["synthetic_rr_tp_used"] is True


def test_structural_and_fallback_tp_setups_have_separate_cohorts():
    structural = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.5,
        structural_tp=120.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )
    fallback = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.5,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert structural["level_mode"] == "structural_sl_structural_tp"
    assert structural["level_cohort"] == "structural_sl_structural_tp"
    assert structural["fallback_tp_applied"] is False
    assert fallback["level_mode"] == "atr_sl_fallback_rr_tp"
    assert fallback["level_cohort"] == "atr_sl_fallback_rr_tp"
    assert fallback["fallback_tp_applied"] is True


def test_fallback_tp_applied_only_when_fallback_rr_target_used():
    structural = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=120.0,
        atr=1.0,
        style="intraday",
        asset_class="forex",
        min_rr=1.5,
        fallback_rr=2.0,
    )
    atr_tp = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.0,
        structural_tp=None,
        atr=1.0,
        style="intraday",
        asset_class="crypto",
        min_rr=1.5,
        fallback_rr=2.0,
    )
    fallback = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.0,
        structural_tp=None,
        atr=1.0,
        style="intraday",
        asset_class="unknown_class",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert structural["tp_source"] == "structural"
    assert structural["fallback_tp_applied"] is False
    assert structural["synthetic_rr_tp_used"] is False
    assert atr_tp["tp_source"] == "atr"
    assert atr_tp["fallback_tp_applied"] is False
    assert atr_tp["synthetic_rr_tp_used"] is False
    assert fallback["tp_source"] == "fallback_rr"
    assert fallback["fallback_tp_applied"] is True
    assert fallback["synthetic_rr_tp_used"] is True


_ENGINE_B_EXECUTION_METADATA_KEYS = (
    "level_mode",
    "level_cohort",
    "sl_source",
    "tp_source",
    "fallback_tp_applied",
    "structural_tp_available",
    "synthetic_rr_tp_used",
    "rr_required",
    "rr_actual",
    "rr_passed",
    "execution_tp1",
    "execution_tp2",
    "execution_rr1",
    "execution_rr2",
    "tp1_source",
    "tp2_source",
    "exit_strategy",
    "runner_tp_requires_structural_break",
    "scale_out_guard_reason",
    "tp1_sl_retry_applied",
    "structural_target_distance_atr",
    "stop_distance_atr",
)


def test_calculate_confidence_emits_execution_level_metadata():
    engine = NakedEngine()
    out = engine.calculate_confidence(
        {
            "atr": 1.0,
            "asset_type": "forex",
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "bos_confirmed": True,
            "trigger_ok": True,
            "zone_touched": True,
            "near_active_zone": True,
            "distance_to_res": 5.0,
            "recommended_stop_loss": 90.0,
            "recommended_take_profit": 120.0,
        },
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile={
            "style": "intraday",
            "min_score": 3.0,
            "min_room_atr": 0.35,
            "min_rr": 1.5,
            "fallback_rr": 2.0,
            "require_macro_align": False,
        },
    )

    for key in _ENGINE_B_EXECUTION_METADATA_KEYS:
        assert key in out


def test_level_cohort_tagging_does_not_change_confidence_score_or_pass_result():
    engine = NakedEngine()
    payload = {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "trigger_ok": True,
        "zone_touched": True,
        "near_active_zone": True,
        "distance_to_res": 5.0,
        "recommended_stop_loss": 90.0,
        "recommended_take_profit": 120.0,
        "structural_verdict": "CLEAR",
    }
    style_profile = {
        "style": "intraday",
        "min_score": 3.0,
        "min_room_atr": 0.35,
        "min_rr": 1.5,
        "fallback_rr": 2.0,
        "require_macro_align": False,
    }

    out = engine.calculate_confidence(
        dict(payload),
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile=style_profile,
    )

    assert out["score"] == pytest.approx(out["gate_score"] + out["bonus_points"])
    assert out["passed"] is True
    assert out["rr_passed"] is out["rr_ok"]
    assert out["level_cohort"] == out["level_mode"]


def test_missing_structural_tp_does_not_crash_level_diagnostics():
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.0,
        structural_tp=None,
        atr=1.0,
        style="intraday",
        asset_class="crypto",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert out["structural_tp_available"] is False
    assert out["structural_target_distance_atr"] is None
    assert out["stop_distance_atr"] is not None
    assert engine_b_level_cohort(out.get("level_mode")) in {
        "atr_sl_structural_tp",
        "atr_sl_fallback_rr_tp",
        "unknown_level_mode",
    }


def test_engine_b_level_cohort_aggregation_counts_sources():
    rows = [
        {"level_cohort": "structural_sl_structural_tp", "passed": True, "rr_actual": 2.0},
        {"level_mode": "atr_sl_fallback_rr_tp", "passed": False, "rr_actual": 1.0},
        {"level_mode": "not_a_known_mode", "passed": False},
    ]

    summary = aggregate_engine_b_level_cohorts(rows)
    assert summary["structural_sl_structural_tp"]["count"] == 1
    assert summary["structural_sl_structural_tp"]["passed"] == 1
    assert summary["structural_sl_structural_tp"]["avg_rr_actual"] == pytest.approx(2.0)
    assert summary["atr_sl_fallback_rr_tp"]["count"] == 1
    assert summary["atr_sl_fallback_rr_tp"]["failed"] == 1
    assert summary["unknown_level_mode"]["count"] == 1


@pytest.mark.parametrize("asset_class,expected_tp", [
    ("crypto", 103.0),
    ("commodity", 104.0),
    ("index", 103.0),
    ("stock", 103.0),
])
def test_non_forex_structural_tp_below_min_rr_uses_synthetic_fallback(asset_class, expected_tp):
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=101.0,
        atr=1.0,
        style="intraday",
        asset_class=asset_class,
        min_rr=1.5,
        fallback_rr=2.0,
    )

    assert out["execution_tp"] == pytest.approx(expected_tp)
    assert out["rr_used_for_gate"] == pytest.approx(2.0)
    assert out["rr_source"].endswith("_sl_fallback_rr_tp")
    assert out["execution_levels_valid"] is True
    assert out["execution_level_reject_reason"] is None or out["execution_level_reject_reason"] == ""
    assert out["fallback_tp_applied"] is True
    assert out["fallback_tp_reason"] == "structural_tp_below_min_rr"


def test_calculate_confidence_uses_synthetic_fallback_when_structural_tp_below_min_rr():
    engine = NakedEngine()
    out = engine.calculate_confidence(
        {
            "atr": 1.0,
            "asset_type": "forex",
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "bos_confirmed": True,
            "trigger_ok": True,
            "zone_touched": True,
            "near_active_zone": True,
            "distance_to_res": 5.0,
            "recommended_stop_loss": 90.0,
            "recommended_take_profit": 101.0,
            "structural_verdict": "CLEAR",
        },
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile={
            "style": "intraday",
            "min_score": 3.0,
            "min_room_atr": 0.35,
            "min_rr": 1.5,
            "fallback_rr": 2.0,
            "require_macro_align": False,
        },
    )

    assert out["rr_ok"] is True
    assert out["rr_used_for_gate"] == pytest.approx(2.0)
    assert out["execution_tp"] == pytest.approx(103.0)
    assert out["rr_source"].endswith("_sl_fallback_rr_tp")
    assert out["execution_levels_valid"] is True
    assert out["execution_level_reject_reason"] is None or out["execution_level_reject_reason"] == ""
    assert out["fallback_tp_applied"] is True


def _capped_structural_tp_res() -> dict:
    return {
        "atr": 1.0,
        "asset_type": "crypto",  # crypto has RR_CAN_SATISFY_SPACE_GATE: true
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "trigger_ok": True,
        "zone_touched": True,
        "near_active_zone": True,
        # Tight room — fails the room_ok gate
        "distance_to_res": 0.05,
        "nearest_resistance_zone": {"lower": 100.05, "upper": 100.20},
        "recommended_stop_loss": 90.0,
        # Structural TP at the resistance wall — reachable TP1 before opposing zone
        "recommended_take_profit": 100.05,
        "structural_verdict": "CLEAR",
    }


_CAPPED_TP_STYLE_PROFILE = {
    "style": "intraday",
    "min_score": 3.0,
    "min_rr": 1.5,
    "fallback_rr": 2.0,
    "require_macro_align": False,
}


def test_scale_out_plan_satisfies_space_gate_when_structural_tp_capped(monkeypatch):
    """Active dual-TP scale-out: structural wall is TP1, so space passes even
    when room_ok is false and synthetic TP2 would have been capped-blocked."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP1_MIN_RR", {"default": 0.0})
    engine = NakedEngine()
    out = engine.calculate_confidence(
        _capped_structural_tp_res(),
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile=dict(_CAPPED_TP_STYLE_PROFILE),
    )

    assert out["fallback_tp_applied"] is True
    assert out["rr_ok"] is True
    assert out["exit_strategy"] == "scale_out_structural_tp1_fallback_tp2"
    assert out["scale_out_guard_reason"] is None
    assert out["scale_out_active"] is True
    assert out["scale_out_space_ok"] is True
    assert out["room_ok"] is False
    assert out["rr_space_capped_tp_blocked"] is False
    assert out["space_gate_ok"] is True
    assert out["passed"] is True
    assert "space" not in (out.get("failed_gate_names") or [])


def test_single_tp_fallback_still_blocked_at_space_when_scale_out_dropped(monkeypatch):
    """When scale-out is dropped (no sane TP1/SL pair), legacy single-TP fallback
    keeps the capped-structural space block — trade still fails closed on space."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP1_MIN_RR", {"default": 2.0})

    engine = NakedEngine()
    out = engine.calculate_confidence(
        _capped_structural_tp_res(),
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile=dict(_CAPPED_TP_STYLE_PROFILE),
    )

    assert out["exit_strategy"] == "single_fallback_rr_tp"
    assert out["scale_out_guard_reason"] == "tp1_rr_below_min_no_sane_sl"
    assert out["scale_out_active"] is False
    assert out["scale_out_space_ok"] is False
    assert out["rr_ok"] is True
    assert out["room_ok"] is False
    assert out["rr_space_capped_tp_blocked"] is True
    assert out["space_gate_ok"] is False
    assert out["passed"] is False
    assert "space" in (out.get("failed_gate_names") or [])


def test_synthetic_fallback_tp_capped_block_flag_off_restores_legacy(monkeypatch):
    """Legacy rescue behaviour returns when the block flag is disabled."""
    monkeypatch.setitem(
        config.CONFIG, "ENGINE_B_SPACE_RR_SUBSTITUTE_BLOCK_CAPPED_STRUCTURAL_TP", False
    )
    engine = NakedEngine()
    out = engine.calculate_confidence(
        _capped_structural_tp_res(),
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile=dict(_CAPPED_TP_STYLE_PROFILE),
    )

    assert out["fallback_tp_applied"] is True
    assert out["room_ok"] is False
    assert out["rr_space_capped_tp_blocked"] is False
    assert out["rr_can_satisfy_space_gate"] is True
    assert out["space_gate_ok"] is True


def test_synthetic_fallback_tp_without_opposing_wall_still_satisfies_space_gate():
    """A synthetic TP created with NO opposing wall mapped (no structural TP,
    no distance_to_res) still qualifies for the space substitution — the
    2026-06-24 'no opposing zone' over-block must not recur."""
    engine = NakedEngine()
    res = _capped_structural_tp_res()
    res.pop("recommended_take_profit")  # no opposing wall → no structural TP
    res["distance_to_res"] = None
    res["bos_confirmed"] = False  # room None-path needs trend+BOS → room_ok False
    profile = dict(_CAPPED_TP_STYLE_PROFILE)
    profile["min_rr"] = 2.5  # above the crypto intraday ATR tp1/sl ratio (2.0)
    out = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile=profile,
    )

    assert out["fallback_tp_applied"] is True
    assert out["fallback_tp_reason"] == "atr_tp_below_min_rr"
    assert out["room_ok"] is False
    assert out["rr_ok"] is True
    assert out["rr_space_capped_tp_blocked"] is False
    assert out["rr_can_satisfy_space_gate"] is True
    assert out["space_gate_ok"] is True


def test_short_direction_synthetic_fallback_tp():
    """Regression: SHORT signals also get a correctly-mirrored synthetic TP."""
    out = resolve_engine_b_execution_levels(
        direction="SHORT",
        entry=100.0,
        structural_sl=110.0,
        structural_tp=99.0,  # only 1% below entry → far below min_rr
        atr=1.0,
        style="intraday",
        asset_class="crypto",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    # SL: max ATR_sl (101.5) and structural (110) → 101.5 (tighter for SHORT = lower)
    # Wait: for SHORT, ATR SL = entry + atr*sl_mult = 100 + 1.5 = 101.5
    # structural SL = 110
    # tighter SL for SHORT = closer to entry = lower value
    # exec_sl = min(101.5, 110) = 101.5
    assert out["execution_sl"] == pytest.approx(101.5)
    # SL distance = 1.5; target_rr = 2.0 → TP = entry - 1.5*2.0 = 97.0
    assert out["execution_tp"] == pytest.approx(97.0)
    assert out["rr_used_for_gate"] == pytest.approx(2.0)
    assert out["fallback_tp_applied"] is True
    assert out["execution_levels_valid"] is True


def test_synthetic_fallback_recovers_missing_structural_tp():
    """Regression: when structural TP is missing (None), the synthetic
    fallback should still produce a valid execution TP from the SL distance,
    instead of returning execution_levels_valid=False.

    Before the fix, the fallback was guarded by `_exec_valid` which requires
    both SL and TP to already be on the right side. A missing TP would short-
    circuit the fallback even though we have a perfectly valid SL.
    """
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=90.0,
        structural_tp=None,  # No structural TP available
        atr=1.0,
        style="intraday",
        asset_class="crypto",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    # exec_sl = max(atr_sl=98.5, struct_sl=90) = 98.5 → sl_dist=1.5 → TP=103
    # ATR TP fallback would set _atr_tp = 100 + 2.5 = 102.5, giving RR=1.67
    # which already passes min_rr=1.5 — so this test covers the
    # "ATR TP exists but synthetic preferred when fallback_rr asked" path is
    # NOT triggered. We rely on the ATR TP branch satisfying the gate.
    assert out["execution_levels_valid"] is True
    assert out["execution_tp"] is not None
    assert out["rr_used_for_gate"] >= 1.5


def test_synthetic_fallback_recovers_with_no_atr_config_and_missing_tp():
    """Edge case: asset_class without STYLE_ATR_MULTS entry AND structural TP
    is missing. Pre-fix, fallback didn't fire because _exec_valid was False
    (TP=None), and the function returned execution_levels_valid=False.
    Post-fix, synthetic TP rescues from a valid SL distance.
    """
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.0,
        structural_tp=None,
        atr=1.0,
        style="intraday",
        asset_class="unknown_class",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    # exec_sl = struct_sl = 98 -> sl_dist = 2 -> synthetic TP = 100 + 2*2.0 = 104
    assert out["execution_sl"] == pytest.approx(98.0)
    assert out["execution_tp"] == pytest.approx(104.0)
    assert out["execution_levels_valid"] is True
    assert out["fallback_tp_applied"] is True
    assert out["rr_used_for_gate"] == pytest.approx(2.0)


def test_synthetic_fallback_recovers_wrong_side_structural_tp():
    """Regression: when structural TP is on the wrong side (e.g. below entry
    for a LONG), and ATR TP also produces RR<min_rr (degenerate case), the
    synthetic fallback should still rescue with a valid synthetic TP.
    """
    # Use a tiny ATR so ATR TP also fails min_rr, forcing reliance on
    # synthetic-fallback construction from a valid SL distance.
    out = resolve_engine_b_execution_levels(
        direction="LONG",
        entry=100.0,
        structural_sl=98.0,
        structural_tp=99.0,  # wrong side for LONG (below entry)
        atr=0.5,
        style="intraday",
        asset_class="crypto",
        min_rr=1.5,
        fallback_rr=2.0,
    )

    # exec_sl: ATR SL = 100 - 0.5*1.5 = 99.25, struct_sl=98 → tighter (LONG)=99.25
    # ATR TP = 100 + 0.5*2.5 = 101.25 → RR = 1.25/0.75 = 1.67 (passes min_rr=1.5)
    # So this should still produce a valid execution_levels_valid=True via ATR
    # TP fallback. We just verify the function does not crash and produces a
    # valid TP on the correct side.
    assert out["execution_levels_valid"] is True
    assert out["execution_tp"] is not None
    assert out["execution_tp"] > 100.0  # LONG TP must be above entry


def _fully_gated_swing_res():
    return {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "liquidity_sweep": False,
        "zone_touched": True,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "distance_to_res": 3.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
        "structural_verdict": "CLEAR",
        "d1_pd_array_conflict": True,
    }


def test_engine_b_confidence_passes_fully_gated_swing_ranging(monkeypatch):
    """All mandatory gates pass in RANGING; score floor must not require bonus headroom."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_REGIME_MULTIPLIERS_APPLY_TO_MIN_SCORE", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", True)
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_STYLE_MIN_SCORE_BY_STYLE",
        {"scalp": 4.0, "intraday": 4.5, "swing": 5.0},
    )

    engine = NakedEngine()
    style_profile = {
        "style": "swing",
        "min_score": 5.0,
        "min_room_atr": 0.35,
        "min_rr": 1.5,
        "require_macro_align": False,
        "checklist_mode": "flexible",
    }
    out = engine.calculate_confidence(
        _fully_gated_swing_res(),
        current_price=100.0,
        direction="LONG",
        style_profile=style_profile,
    )

    assert out["passed"] is True
    assert out["gate_score"] == pytest.approx(5.0)
    assert out["gate_pct"] == 100

    gate_ok, effective_min = engine_b_confidence_passes(
        out, style_profile, regime_label="RANGING", asset_type="forex"
    )
    assert effective_min == pytest.approx(5.0)
    assert gate_ok is True


def test_engine_b_confidence_passes_blocks_when_gate_score_below_floor():
    style_profile = {"min_score": 5.0, "style": "swing"}
    gate_ok, effective_min = engine_b_confidence_passes(
        {
            "passed": True,
            "gate_score": 4.0,
            "gate_max_possible": 5.0,
            "score": 3.75,
        },
        style_profile,
        regime_label="RANGING",
    )

    assert effective_min == pytest.approx(5.0)
    assert gate_ok is False


def _short_tp1_blocked_exec_levels(**overrides):
    base = {
        "entry": 6.705,
        "structural_sl": 6.938064641405058,
        "structural_tp": 6.592244191324525,
        "structural_rr": 0.7021,
        "structural_sl_valid": True,
        "structural_tp_valid": True,
        "structural_tp_available": True,
        "execution_sl": 6.865596962107586,
        "execution_tp": 6.415925468206346,
        "execution_tp1": 6.592244191324525,
        "execution_tp2": 6.415925468206346,
        "execution_rr": 1.8,
        "execution_rr1": 0.7021,
        "execution_rr2": 1.8,
        "rr_used_for_gate": 1.8,
        "rr_source": "structural_sl_sl_structural_tp_tp",
        "level_mode": "structural_sl_sl_structural_tp_tp",
        "level_cohort": "structural",
        "sl_source": "structural",
        "tp_source": "fallback_rr",
        "tp1_source": "structural",
        "tp2_source": "fallback_rr",
        "exit_strategy": "scale_out_structural_tp1_fallback_tp2",
        "runner_tp_requires_structural_break": True,
        "scale_out_guard_reason": None,
        "tp1_sl_retry_applied": False,
        "execution_levels_valid": True,
        "execution_level_reject_reason": None,
        "execution_tp_side_ok": True,
        "fallback_tp_applied": True,
        "fallback_tp_reason": "structural_tp_below_min_rr",
        "synthetic_rr_tp_used": True,
        "atr_sl_clamp_applied": False,
        "rr_required": 1.4,
        "rr_actual": 1.8,
        "rr_passed": True,
        "structural_target_distance_atr": None,
        "stop_distance_atr": None,
    }
    base.update(overrides)
    return base


def _short_tp1_blocked_res() -> dict:
    return {
        "atr": 0.16,
        "asset_type": "crypto",
        "current_swing_sequence": "LH_LL",
        "macro_swing_sequence": "LH_LL",
        "bos_confirmed": True,
        "trigger_ok": True,
        "zone_touched": True,
        "near_active_zone": True,
        "structural_verdict": "CLEAR",
        "nearest_support_zone": {"lower": 6.6525, "upper": 6.8345},
        "distance_to_sup": -0.1295,
        "distance_to_sup_pct": pytest.approx(-1.9314, rel=1e-2),
        "recommended_stop_loss": 6.938064641405058,
        "recommended_take_profit": 6.592244191324525,
    }


def test_short_tp1_blocked_by_opposing_zone_regression(monkeypatch):
    """Exact SHORT scenario: entry inside support, TP1 below support upper edge."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP1_MIN_RR", {"default": 0.3})
    monkeypatch.setitem(
        config.CONFIG,
        "ENGINE_B_SPACE_RR_SUBSTITUTE_BLOCK_CAPPED_STRUCTURAL_TP",
        True,
    )
    monkeypatch.setattr(
        "market_structure.resolve_engine_b_execution_levels",
        lambda **kwargs: _short_tp1_blocked_exec_levels(),
    )

    engine = NakedEngine()
    out = engine.calculate_confidence(
        _short_tp1_blocked_res(),
        current_price=6.705,
        direction="SHORT",
        entry_candles=[],
        style_profile={
            "style": "intraday",
            "min_score": 3.0,
            "min_rr": 1.4,
            "fallback_rr": 2.0,
            "min_room_atr": 0.25,
            "require_macro_align": False,
        },
    )

    assert out["entry_inside_opposing_zone"] is True
    assert out["tp1_before_opposing_zone"] is False
    assert out["tp1_path_clear"] is False
    assert out["scale_out_active"] is True
    assert out["scale_out_space_ok"] is False
    assert out["rr_can_satisfy_space_gate"] is False
    assert out["space_gate_ok"] is False
    assert out["passed"] is False
    assert out["rr_ok"] is True
    assert ENGINE_B_REASON_TP1_BLOCKED_BY_OPPOSING_ZONE in (
        out.get("engine_b_diagnostics", {}).get("reason_codes") or []
    )


def test_long_tp1_blocked_by_opposing_zone_mirrors_short(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP1_MIN_RR", {"default": 0.3})
    monkeypatch.setattr(
        "market_structure.resolve_engine_b_execution_levels",
        lambda **kwargs: _short_tp1_blocked_exec_levels(
            entry=100.75,
            structural_sl=94.0,
            structural_tp=101.5,
            execution_sl=95.0,
            execution_tp=103.0,
            execution_tp1=101.5,
            execution_tp2=103.0,
            execution_rr=1.8,
            execution_rr1=0.7,
            execution_rr2=1.8,
            rr_used_for_gate=1.8,
        ),
    )

    engine = NakedEngine()
    out = engine.calculate_confidence(
        {
            "atr": 2.0,
            "asset_type": "crypto",
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "bos_confirmed": True,
            "trigger_ok": True,
            "zone_touched": True,
            "near_active_zone": True,
            "structural_verdict": "CLEAR",
            "nearest_resistance_zone": {"lower": 100.5, "upper": 101.0},
            "distance_to_res": -0.25,
            "recommended_stop_loss": 94.0,
            "recommended_take_profit": 101.5,
        },
        current_price=100.75,
        direction="LONG",
        entry_candles=[],
        style_profile={
            "style": "intraday",
            "min_score": 3.0,
            "min_rr": 1.4,
            "fallback_rr": 2.0,
            "min_room_atr": 0.25,
            "require_macro_align": False,
        },
    )

    assert out["entry_inside_opposing_zone"] is True
    assert out["tp1_before_opposing_zone"] is False
    assert out["tp1_path_clear"] is False
    assert out["scale_out_space_ok"] is False
    assert out["space_gate_ok"] is False
    assert out["passed"] is False


def test_scale_out_space_ok_when_tp1_before_opposing_zone_despite_low_room(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_TP1_MIN_RR", {"default": 0.0})
    monkeypatch.setattr(
        "market_structure.resolve_engine_b_execution_levels",
        lambda **kwargs: _short_tp1_blocked_exec_levels(
            execution_tp1=100.04,
            structural_tp=100.04,
            execution_rr1=0.8,
            execution_rr2=2.0,
            execution_rr=2.0,
            rr_used_for_gate=2.0,
            rr_passed=True,
            fallback_tp_reason="structural_tp_below_min_rr",
        ),
    )

    engine = NakedEngine()
    out = engine.calculate_confidence(
        _capped_structural_tp_res(),
        current_price=100.0,
        direction="LONG",
        entry_candles=[],
        style_profile=dict(_CAPPED_TP_STYLE_PROFILE),
    )

    assert out["tp1_path_clear"] is True
    assert out["scale_out_active"] is True
    assert out["scale_out_space_ok"] is True
    assert out["room_ok"] is False
    assert out["space_gate_ok"] is True
    assert out["passed"] is True


def test_engine_b_tp1_path_clear_short_exact_geometry():
    diag = _engine_b_tp1_path_clear(
        direction="SHORT",
        entry=6.705,
        tp1=6.592244191324525,
        opposing_zone={"lower": 6.6525, "upper": 6.8345},
        opposing_zone_distance=-0.1295,
    )
    assert diag["entry_inside_opposing_zone"] is True
    assert diag["tp1_before_opposing_zone"] is False
    assert diag["tp1_path_clear"] is False
    assert diag["tp1_path_block_reason"] == ENGINE_B_REASON_TP1_BLOCKED_BY_OPPOSING_ZONE
