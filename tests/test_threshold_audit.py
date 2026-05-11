import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_engine import risk_check
from threshold_audit import (
    REQUIRED_FIELDS,
    audit_enabled,
    build_signal_funnel_row,
    write_signal_funnel_rows,
)
from tools.threshold_audit_report import build_report


def _pair():
    return {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "enabled": True}


def _signal(score=2.0):
    return {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "direction": "LONG",
        "confluenceScore": score,
        "maxScore": 3.0,
        "scoreNorm": round(score / 3.0, 4),
        "price": 1.10,
        "sl": 1.09,
        "timestamp": "2026-04-24T10:00:00+00:00",
        "factorScores": {
            "trend": 2.4,
            "momentum": 0.6,
            "addon": 0.0,
            "adx_value": 18.0,
            "adx_multiplier": 0.65,
            "session_multiplier": 0.75,
        },
        "factorDiagnostics": {},
        "scanDiagnostics": [{"code": "low_confluence", "detail": "Below threshold"}],
        "_threshold_audit_b_res": {"structural_verdict": "CLEAR", "direction": "LONG"},
        "_threshold_audit_b_conf": {
            "score": 2.5,
            "max_possible": 5.0,
            "passed": False,
            "structure_ok": True,
            "location_ok": True,
            "entry_ok": False,
            "room_ok": True,
            "rr_ok": False,
            "engine_b_diagnostics": {"reason_codes": ["no_trigger_pattern"]},
        },
        "_threshold_audit_b_threshold": 3.0,
    }


def _crypto_pair():
    return {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True}


def _crypto_signal(score=2.12):
    sig = _signal(score=score)
    sig.update(
        {
            "pair": "BTC/USDT",
            "display": "BTC/USDT",
            "symbol": "BTCUSDT",
            "type": "crypto",
            "confluenceScore": score,
            "scoreNorm": round(score / 3.0, 4),
            "scanThresholdEffective": 2.0,
            "scanThreshold": 2.0,
        }
    )
    return sig


def _signal_with_engine_b_diagnostics():
    sig = _signal(score=1.9)
    sig["_threshold_audit_b_style_profile"] = {"min_rr": 1.5, "min_room_atr": 0.35}
    sig["_threshold_audit_b_res"] = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "current_price": 100.0,
        "recommended_stop_loss": 98.0,
        "recommended_take_profit": 104.0,
        "atr": 2.0,
        "tp_source": "fallback_rr",
        "tp_structural_limited": True,
        "nearest_target_type": None,
        "nearest_target_price": None,
        "structural_target_candidates": [
            {
                "target_type": "resistance_zone",
                "target_price": 100.4,
                "correct_side": True,
                "distance_to_target": 0.4,
                "atr_multiple_to_target": 0.2,
                "selected": False,
                "rejected_reason": "too_close_or_wrong_side",
            },
            {
                "target_type": "resistance_zone",
                "target_price": 104.0,
                "correct_side": True,
                "distance_to_target": 4.0,
                "atr_multiple_to_target": 2.0,
                "selected": True,
                "rejected_reason": None,
            },
        ],
        "trigger_ok": False,
        "trigger_pattern": "NONE",
        "bos_data": {"bos_bull": True, "bos_bear": False},
        "choch_data": {"choch_bull": False, "choch_bear": False},
        "order_blocks": [{"type": "bullish"}],
        "active_fvgs": [],
        "liquidity_sweep": False,
        "breaker_block": None,
        "breaker_blocks": [],
        "zone_touched": True,
        "near_active_zone": False,
        "rejection_candle": False,
        "strong_close": False,
        "bos_volume_confirmed": True,
        "trigger_timeframe": "H1",
        "latest_confirmed_trigger_candle_time": "2026-04-24T19:00:00+00:00",
        "d1_pd_array_conflict": True,
        "d1_bos_data": {"bos_bull": False, "bos_bear": False},
        "d1_conflict_metric_details": [
            {
                "zone_type": "bearish_OB",
                "zone_price_range": {"top": 106.0, "bottom": 105.0},
                "distance_to_conflict": 5.5,
                "distance_in_atr": 2.75,
                "conflict_side": "above_price",
                "entry_side_or_tp_side": "tp_side",
            }
        ],
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
    }
    sig["_threshold_audit_b_conf"] = {
        "score": 2.5,
        "max_possible": 5.0,
        "passed": False,
        "structure_ok": True,
        "location_ok": True,
        "entry_ok": False,
        "room_ok": False,
        "rr_ok": False,
        "engine_b_diagnostics": {
            "reason_codes": [
                "structural_tp_too_close",
                "no_trigger_pattern",
                "d1_pd_array_conflict",
            ]
        },
    }
    return sig


def test_signal_funnel_logging_writes_required_fields():
    row = build_signal_funnel_row(_pair(), _signal(), tier="skip", tier_reason="Below discovery threshold")
    out = Path("tests") / "_artifacts" / "threshold_audit_test.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    write_signal_funnel_rows([row], out)
    loaded = json.loads(out.read_text(encoding="utf-8").strip())
    missing = [field for field in REQUIRED_FIELDS if field not in loaded]
    assert missing == []
    assert loaded["risk_check_allowed"] is False
    assert loaded["risk_check_fail_reasons"] == ["not_evaluated_threshold_audit_report_only"]


def test_threshold_report_handles_empty_no_signal_scans():
    report = build_report([])
    assert "Total scanned symbols: 0" in report
    assert "do not change yet" in report


def test_near_miss_classification_works():
    sig = _signal(score=1.4)
    sig["scoreNorm"] = 0.70
    row = build_signal_funnel_row(_pair(), sig, tier="skip")
    assert row["final_scan_result"] == "A_NEAR_MISS"


def test_shadow_thresholds_do_not_affect_execution_decisions():
    sig = _signal(score=2.0)
    row = build_signal_funnel_row(_pair(), sig, tier="skip")
    before = sig.get("confluenceScore")
    assert row["shadow_thresholds"]["ENGINE_A"]["current_minus_10pct"] < row["thresholds"]["engine_a"]
    assert sig.get("confluenceScore") == before
    assert "shadow_thresholds" not in sig


def test_threshold_audit_prefers_signal_scan_threshold_effective_for_engine_a():
    row = build_signal_funnel_row(_crypto_pair(), _crypto_signal(), tier="trade")

    assert row["thresholds"]["engine_a"] == 2.0
    assert row["engine_a_passed"] is True


def test_threshold_audit_falls_back_to_configured_scan_threshold_when_signal_threshold_missing():
    sig = _crypto_signal()
    sig.pop("scanThresholdEffective")
    sig.pop("scanThreshold")

    row = build_signal_funnel_row(_crypto_pair(), sig, tier="trade")

    assert row["thresholds"]["engine_a"] == 2.0
    assert row["engine_a_passed"] is True


def test_fail_reason_counts_are_reported():
    rows = [build_signal_funnel_row(_pair(), _signal(score=1.4), tier="skip")]
    report = build_report(rows)
    assert "below_engine_a_threshold" in report
    assert "engine_b_confidence_passed_false" in report
    assert "no_trigger_pattern" in report


def test_threshold_audit_mode_does_not_change_freshness_or_risk_gates():
    row = build_signal_funnel_row(_pair(), _signal(score=2.0), tier="skip")
    assert row["risk_check_fail_reasons"] == ["not_evaluated_threshold_audit_report_only"]
    approval = risk_check(
        {
            "pair": "EUR/USD",
            "type": "forex",
            "direction": "LONG",
            "price": 1.10,
            "sl": 1.09,
            "timestamp": "2020-01-01T00:00:00+00:00",
            "confluenceScore": 2.4,
            "maxScore": 3.0,
        },
        account_balance=10000,
        account_equity=10000,
        open_positions=[],
        kill_switch=False,
    )
    assert approval.approved is False
    assert approval.reason in {"STALE_SIGNAL", "STALE_DATA_BLOCK", "MISSING_CANDLE_FRESHNESS"}


def test_threshold_audit_env_override_enables_without_default_config_change(monkeypatch):
    monkeypatch.setenv("ATHENA_THRESHOLD_AUDIT", "1")
    assert audit_enabled() is True


def test_engine_b_checklist_audit_rows_include_required_fields():
    row = build_signal_funnel_row(_pair(), _signal_with_engine_b_diagnostics(), tier="skip")
    assert row["engine_b_structural_tp_diagnostics"]["entry"] == 100.0
    assert row["engine_b_trigger_diagnostics"]["trigger_timeframe"] == "H1"
    assert row["engine_b_d1_conflict_diagnostics"]["conflicts"][0]["zone_type"] == "bearish_OB"


def test_structural_tp_too_close_diagnostics_are_populated():
    row = build_signal_funnel_row(_pair(), _signal_with_engine_b_diagnostics(), tier="skip")
    diag = row["engine_b_structural_tp_diagnostics"]
    assert diag["fail_reason"] == "structural_tp_too_close"
    assert diag["target_correct_side_of_entry"] is True
    assert diag["rr"] == 2.0


def test_no_trigger_pattern_diagnostics_are_populated():
    row = build_signal_funnel_row(_pair(), _signal_with_engine_b_diagnostics(), tier="skip")
    diag = row["engine_b_trigger_diagnostics"]
    assert diag["classification"] in ("STRUCTURE_READY_NO_TRIGGER", "NO_STRUCTURE_NO_TRIGGER", "POSSIBLE_WATCHLIST_ENTRY_PENDING")
    assert "no_price_action_trigger" in diag["exact_missing_trigger_condition"]


def test_d1_pd_array_conflict_diagnostics_are_populated():
    row = build_signal_funnel_row(_pair(), _signal_with_engine_b_diagnostics(), tier="skip")
    conflicts = row["engine_b_d1_conflict_diagnostics"]["conflicts"]
    assert conflicts[0]["distance_in_atr"] == 2.75


def test_watchlist_only_simulations_do_not_create_execution_signals():
    row = build_signal_funnel_row(_pair(), _signal_with_engine_b_diagnostics(), tier="skip")
    shadow = row["engine_b_shadow_simulation"]
    assert shadow["watchlist_entry_pending"] is True
    assert shadow["d1_conflict_watchlist_candidate"] is True
    assert shadow["adds_execution"] is False
    assert shadow["added_watchlist_count"] > 0
    assert shadow["added_execution_count"] == 0
    assert len(shadow["watchlist_reasons"]) > 0


def test_next_structural_target_simulation_does_not_choose_wrong_side_tp():
    sig = _signal_with_engine_b_diagnostics()
    sig["_threshold_audit_b_res"]["structural_target_candidates"].append(
        {
            "target_type": "bad_resistance_zone",
            "target_price": 99.0,
            "correct_side": False,
            "rejected_reason": None,
        }
    )
    row = build_signal_funnel_row(_pair(), sig, tier="skip")
    assert row["engine_b_shadow_simulation"]["next_structural_target_valid"] is True
    assert all(
        c.get("correct_side") for c in sig["_threshold_audit_b_res"]["structural_target_candidates"] if c.get("selected")
    )


def test_passed_true_row_has_empty_hard_fail_reasons():
    """Test that a passed=True row has empty hard_fail_reasons."""
    from threshold_audit import _engine_b_hard_fail_reasons
    
    conf_b = {"passed": True, "structure_ok": True, "location_ok": True, "entry_ok": True, "room_ok": True, "rr_ok": True, "macro_ok": True}
    res_b = {"structural_verdict": "CLEAR"}
    hard_fails = _engine_b_hard_fail_reasons(conf_b, res_b)
    assert hard_fails == []


def test_passed_false_row_contains_at_least_one_hard_fail_reason():
    """Test that a passed=False row contains at least one hard_fail_reason."""
    from threshold_audit import _engine_b_hard_fail_reasons
    
    conf_b = {"passed": False, "structure_ok": True, "location_ok": True, "entry_ok": False, "room_ok": True, "rr_ok": True, "macro_ok": True}
    res_b = {"structural_verdict": "CLEAR"}
    hard_fails = _engine_b_hard_fail_reasons(conf_b, res_b)
    assert len(hard_fails) >= 1
    assert "engine_b_entry_ok_false" in hard_fails or "engine_b_confidence_passed_false" in hard_fails


def test_soft_warnings_do_not_imply_failed_checklist():
    """Test that soft warnings do not imply failed checklist."""
    from threshold_audit import _engine_b_soft_warnings
    
    # TP structural limitation is a soft warning when passed=True
    conf_b = {"passed": True, "structure_ok": True, "entry_ok": True}
    res_b = {"structural_verdict": "CLEAR", "tp_structural_limited": True, "trigger_ok": True}
    warnings = _engine_b_soft_warnings(conf_b, res_b)
    assert "structural_tp_too_close_fallback_to_rr" in warnings
    # But hard fails should be empty
    from threshold_audit import _engine_b_hard_fail_reasons
    hard_fails = _engine_b_hard_fail_reasons(conf_b, res_b)
    assert len(hard_fails) == 0


def test_report_separates_hard_fails_from_warnings():
    """Test that the report separates hard fails from warnings."""
    sig = _signal_with_engine_b_diagnostics()
    # Set passed=True to trigger soft warnings instead of hard fails
    sig["_threshold_audit_b_conf"]["passed"] = True
    sig["_threshold_audit_b_conf"]["entry_ok"] = True
    sig["_threshold_audit_b_conf"]["room_ok"] = True
    sig["_threshold_audit_b_conf"]["rr_ok"] = True
    row = build_signal_funnel_row(_pair(), sig, tier="skip")
    
    # With passed=True, structural_tp_too_close becomes a soft warning
    assert "structural_tp_too_close_fallback_to_rr" in row["engine_b_soft_warnings"]
    # And hard fails should be empty
    assert row["engine_b_hard_fail_reasons"] == []
    # Diagnostic notes should contain context
    assert len(row["engine_b_diagnostic_notes"]) > 0


def test_long_wrong_side_tp_detected():
    """Test that wrong-side TP for LONG is detected."""
    from threshold_audit import _engine_b_structural_tp_diagnostics
    
    pair = _pair()
    res_b = {
        "current_price": 100.0,
        "recommended_stop_loss": 98.0,
        "recommended_take_profit": 95.0,  # Wrong side for LONG
        "atr": 2.0,
        "direction": "LONG",
        "tp_source": "fallback_rr",
        "tp_structural_limited": False,
        "selected_structural_target": {},
        "structural_target_candidates": [],
    }
    conf_b = {"passed": False}
    style_profile = {"min_rr": 1.5}
    diag = _engine_b_structural_tp_diagnostics(pair, res_b, conf_b, style_profile)
    
    assert diag["target_side_ok"] is False
    assert diag["sl_side_ok"] is True  # SL is correct side for LONG


def test_short_wrong_side_tp_detected():
    """Test that wrong-side TP for SHORT is detected."""
    from threshold_audit import _engine_b_structural_tp_diagnostics
    
    pair = _pair()
    res_b = {
        "current_price": 100.0,
        "recommended_stop_loss": 102.0,
        "recommended_take_profit": 105.0,  # Wrong side for SHORT
        "atr": 2.0,
        "direction": "SHORT",
        "tp_source": "fallback_rr",
        "tp_structural_limited": False,
        "selected_structural_target": {},
        "structural_target_candidates": [],
    }
    conf_b = {"passed": False}
    style_profile = {"min_rr": 1.5}
    diag = _engine_b_structural_tp_diagnostics(pair, res_b, conf_b, style_profile)
    
    assert diag["target_side_ok"] is False
    assert diag["sl_side_ok"] is True  # SL is correct side for SHORT


def test_rr_calculation_correct():
    """Test that RR calculation is correct."""
    from threshold_audit import _engine_b_structural_tp_diagnostics
    
    pair = _pair()
    res_b = {
        "current_price": 100.0,
        "recommended_stop_loss": 98.0,
        "recommended_take_profit": 104.0,
        "atr": 2.0,
        "direction": "LONG",
        "tp_source": "structural_zone",
        "tp_structural_limited": False,
        "selected_structural_target": {},
        "structural_target_candidates": [],
    }
    conf_b = {"passed": True}
    style_profile = {"min_rr": 1.5}
    diag = _engine_b_structural_tp_diagnostics(pair, res_b, conf_b, style_profile)
    
    # Distance to TP = 104 - 100 = 4
    # Distance to SL = 100 - 98 = 2
    # RR = 4 / 2 = 2.0
    assert diag["distance_to_tp"] == 4.0
    assert diag["distance_to_sl"] == 2.0
    assert diag["rr"] == 2.0


def test_next_valid_target_detected_report_only():
    """Test that next valid structural target is detected as report-only."""
    from threshold_audit import _engine_b_structural_tp_diagnostics
    
    pair = _pair()
    res_b = {
        "current_price": 100.0,
        "recommended_stop_loss": 98.0,
        "recommended_take_profit": 104.0,
        "atr": 2.0,
        "direction": "LONG",
        "tp_source": "fallback_rr",
        "tp_structural_limited": True,
        "selected_structural_target": {
            "target_type": "resistance_zone",
            "target_price": 102.0,
            "correct_side": True,
            "rejected_reason": "too_close_or_wrong_side",
            "zone": {"type": "resistance", "low": 101.5, "high": 102.5, "mitigated": False, "age_bars": 5},
        },
        "structural_target_candidates": [
            {
                "target_type": "resistance_zone",
                "target_price": 102.0,
                "correct_side": True,
                "rejected_reason": "too_close_or_wrong_side",
                "zone": {"type": "resistance", "low": 101.5, "high": 102.5, "mitigated": False, "age_bars": 5},
            },
            {
                "target_type": "resistance_zone",
                "target_price": 106.0,
                "correct_side": True,
                "rejected_reason": None,
                "zone": {"type": "resistance", "low": 105.5, "high": 106.5, "mitigated": False, "age_bars": 10},
            },
        ],
    }
    conf_b = {"passed": True}  # Passed despite TP limitation (fell back to RR)
    style_profile = {"min_rr": 1.5}
    diag = _engine_b_structural_tp_diagnostics(pair, res_b, conf_b, style_profile)
    
    # TP limitation is not a hard block when passed=True
    assert diag["fail_is_hard_block"] is False
    # Zone details should be populated
    assert diag["chosen_zone_type"] == "resistance"
    assert diag["chosen_zone_low"] == 101.5
    assert diag["chosen_zone_high"] == 102.5
    assert diag["chosen_zone_mitigated"] is False
    assert diag["chosen_zone_age_bars"] == 5


def test_invalid_mitigated_target_not_selected():
    """Test that invalid/mitigated target is not selected as primary target."""
    from threshold_audit import _engine_b_structural_tp_diagnostics
    
    pair = _pair()
    res_b = {
        "current_price": 100.0,
        "recommended_stop_loss": 98.0,
        "recommended_take_profit": 104.0,
        "atr": 2.0,
        "direction": "LONG",
        "tp_source": "structural_zone",
        "tp_structural_limited": False,
        "nearest_target_type": "resistance_zone",
        "nearest_target_price": 106.0,
        "selected_structural_target": {
            "target_type": "resistance_zone",
            "target_price": 106.0,
            "correct_side": True,
            "rejected_reason": None,
            "zone": {"type": "resistance", "low": 105.5, "high": 106.5, "mitigated": False, "age_bars": 10},
        },
        "structural_target_candidates": [
            {
                "target_type": "resistance_zone",
                "target_price": 102.0,
                "correct_side": True,
                "rejected_reason": "too_close_or_wrong_side",
                "zone": {"type": "resistance", "low": 101.5, "high": 102.5, "mitigated": True, "age_bars": 5},
            },
            {
                "target_type": "resistance_zone",
                "target_price": 106.0,
                "correct_side": True,
                "rejected_reason": None,
                "zone": {"type": "resistance", "low": 105.5, "high": 106.5, "mitigated": False, "age_bars": 10},
            },
        ],
    }
    conf_b = {"passed": True}
    style_profile = {"min_rr": 1.5}
    diag = _engine_b_structural_tp_diagnostics(pair, res_b, conf_b, style_profile)
    
    # The non-mitigated target should be selected
    assert diag["chosen_zone_mitigated"] is False
    assert diag["nearest_target_price"] == 106.0


def test_structure_ready_no_trigger_classification():
    """Test that structure ready but no trigger becomes STRUCTURE_READY_NO_TRIGGER."""
    from threshold_audit import _engine_b_trigger_diagnostics
    
    res_b = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "trigger_ok": False,
        "trigger_pattern": "NONE",
        "bos_data": {"bos_bull": True, "bos_bear": False},
        "choch_data": {"choch_bull": False, "choch_bear": False},
        "order_blocks": [],
        "active_fvgs": [],
        "liquidity_sweep": False,
        "breaker_block": None,
        "zone_touched": True,
        "near_active_zone": False,
        "rejection_candle": False,
        "strong_close": False,
        "inside_break_candle": False,
        "bos_volume_confirmed": True,
        "trigger_timeframe": "H1",
        "latest_confirmed_trigger_candle_time": "2026-04-24T19:00:00+00:00",
    }
    conf_b = {"structure_ok": True, "entry_ok": False}
    diag = _engine_b_trigger_diagnostics(res_b, conf_b)
    
    assert diag["classification"] == "STRUCTURE_READY_NO_TRIGGER"
    assert diag["bos_present"] is True
    assert diag["choch_present"] is False


def test_no_structure_no_trigger_classification():
    """Test that no structure and no trigger becomes NO_STRUCTURE_NO_TRIGGER."""
    from threshold_audit import _engine_b_trigger_diagnostics
    
    res_b = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "trigger_ok": False,
        "trigger_pattern": "NONE",
        "bos_data": {"bos_bull": False, "bos_bear": False},
        "choch_data": {"choch_bull": False, "choch_bear": False},
        "order_blocks": [],
        "active_fvgs": [],
        "liquidity_sweep": False,
        "breaker_block": None,
        "zone_touched": False,
        "near_active_zone": False,
        "rejection_candle": False,
        "strong_close": False,
        "inside_break_candle": False,
        "bos_volume_confirmed": True,
        "trigger_timeframe": "H1",
        "latest_confirmed_trigger_candle_time": "2026-04-24T19:00:00+00:00",
    }
    conf_b = {"structure_ok": False, "entry_ok": False}
    diag = _engine_b_trigger_diagnostics(res_b, conf_b)
    
    assert diag["classification"] == "NO_STRUCTURE_NO_TRIGGER"
    assert diag["bos_present"] is False
    assert diag["choch_present"] is False


def test_trigger_present_classification():
    """Test that trigger present becomes TRIGGER_PRESENT classification."""
    from threshold_audit import _engine_b_trigger_diagnostics
    
    res_b = {
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "trigger_ok": True,
        "trigger_pattern": "ENGULFING",
        "bos_data": {"bos_bull": True, "bos_bear": False},
        "choch_data": {"choch_bull": False, "choch_bear": False},
        "order_blocks": [],
        "active_fvgs": [],
        "liquidity_sweep": False,
        "breaker_block": None,
        "zone_touched": True,
        "near_active_zone": False,
        "rejection_candle": True,
        "strong_close": True,
        "inside_break_candle": False,
        "bos_volume_confirmed": True,
        "trigger_timeframe": "H1",
        "latest_confirmed_trigger_candle_time": "2026-04-24T19:00:00+00:00",
    }
    conf_b = {"structure_ok": True, "entry_ok": True}
    diag = _engine_b_trigger_diagnostics(res_b, conf_b)
    
    assert diag["classification"] == "TRIGGER_PRESENT"
    assert diag["bos_present"] is True


def test_distant_d1_conflict_not_hard_block():
    """Test that distant D1 conflict is not hard block if outside configured ATR distance."""
    from threshold_audit import _engine_b_d1_conflict_diagnostics
    
    res_b = {
        "direction": "LONG",
        "current_price": 100.0,
        "atr": 2.0,
        "d1_bos_data": {"bos_bull": False, "bos_bear": False},
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "d1_conflict_metric_details": [
            {
                "zone_type": "bearish_OB",
                "zone_price_range": {"top": 110.0, "bottom": 109.0},
                "distance_to_conflict": 9.5,
                "distance_in_atr": 4.75,  # Beyond 3 ATR threshold
                "conflict_side": "above_price",
                "entry_side_or_tp_side": "tp_side",
            }
        ],
    }
    diag = _engine_b_d1_conflict_diagnostics(res_b)
    
    assert diag["conflict_is_hard_block"] is False
    assert diag["configured_hard_block_atr_distance"] == 3.0


def test_nearby_d1_conflict_is_hard_block():
    """Test that nearby opposing D1 conflict is hard block."""
    from threshold_audit import _engine_b_d1_conflict_diagnostics
    
    res_b = {
        "direction": "LONG",
        "current_price": 100.0,
        "atr": 2.0,
        "d1_bos_data": {"bos_bull": False, "bos_bear": False},
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "d1_conflict_metric_details": [
            {
                "zone_type": "bearish_OB",
                "zone_price_range": {"top": 105.0, "bottom": 104.0},
                "distance_to_conflict": 4.5,
                "distance_in_atr": 2.25,  # Within 3 ATR threshold
                "conflict_side": "above_price",
                "entry_side_or_tp_side": "tp_side",
            }
        ],
    }
    diag = _engine_b_d1_conflict_diagnostics(res_b)
    
    assert diag["conflict_is_hard_block"] is True
    assert diag["configured_hard_block_atr_distance"] == 3.0


def test_wrong_side_conflict_not_hard_block():
    """Test that wrong-side conflict is not hard block (entry side instead of TP side)."""
    from threshold_audit import _engine_b_d1_conflict_diagnostics
    
    res_b = {
        "direction": "LONG",
        "current_price": 100.0,
        "atr": 2.0,
        "d1_bos_data": {"bos_bull": False, "bos_bear": False},
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "d1_conflict_metric_details": [
            {
                "zone_type": "bullish_OB",
                "zone_price_range": {"top": 99.0, "bottom": 98.0},
                "distance_to_conflict": 1.5,
                "distance_in_atr": 0.75,
                "conflict_side": "below_price",  # Entry side, not TP side
                "entry_side_or_tp_side": "entry_side",
            }
        ],
    }
    diag = _engine_b_d1_conflict_diagnostics(res_b)
    
    # Below price for LONG is entry side, not TP side - should not be hard block
    assert diag["conflict_is_hard_block"] is False
