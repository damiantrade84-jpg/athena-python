"""
Crypto Engine B H4 target provenance audit.

Report-only diagnostics. This script can temporarily override config values
inside the audit process, but it does not write config.yaml, tune triggers,
lower RR, enable the crypto target model by default, or touch live feeds/UI.
"""

import argparse
import copy
import importlib.util
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

import requests

sys.path.insert(0, str(Path(__file__).parent))
import config as runtime_config

spec = importlib.util.spec_from_file_location("athena_module", "athena.py")
athena_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(athena_module)

CRYPTO_PAIRS = athena_module.CRYPTO_PAIRS
ENABLED_CRYPTO = [p for p in CRYPTO_PAIRS if p.get("enabled", True)]
API_BASE = "http://localhost:5000"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("crypto_gate_calibration")

FALLBACK_NOT_ALLOWED_GATE = "fallback_target_not_allowed"


def _rr_distribution(values):
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return {"count": 0}
    return {
        "count": len(vals),
        "min": vals[0],
        "p25": vals[int(len(vals) * 0.25)],
        "median": median(vals),
        "p75": vals[int(len(vals) * 0.75)],
        "max": vals[-1],
    }


def _failed_gate_names(detail):
    names = detail.get("debug_gate_failed_names")
    if isinstance(names, list):
        return [str(g) for g in names if g is not None]
    return []


def _gate_flags(failed_gates, final_passed):
    trigger_failed = "trigger" in failed_gates
    rr_failed = any(g.startswith("rr=") or g == "rr_gate" for g in failed_gates)
    passed_all_non_trigger = not any(g != "trigger" for g in failed_gates)
    passed_all_gates = final_passed and not failed_gates
    return passed_all_gates, passed_all_non_trigger, trigger_failed, rr_failed


def _legacy_direction_detail(debug_row, direction):
    failed_gates = list(debug_row.get("failed_gate_names") or [])
    final_passed = bool(debug_row.get("long_passed" if direction == "LONG" else "short_passed"))
    passed_all_gates, passed_all_non_trigger, trigger_failed, rr_failed = _gate_flags(
        failed_gates, final_passed
    )
    return {
        "direction": direction,
        "structural_verdict": debug_row.get(
            "long_structural_verdict" if direction == "LONG" else "short_structural_verdict"
        ),
        "confidence_score": debug_row.get(
            "long_confidence_score" if direction == "LONG" else "short_confidence_score"
        ),
        "threshold": None,
        "passed_all_gates": passed_all_gates,
        "passed_all_non_trigger_gates": passed_all_non_trigger,
        "final_engine_b_passed": final_passed,
        "trigger_failed": trigger_failed,
        "rr_failed": rr_failed,
        "production_pass_flag": final_passed,
        "debug_gate_failed_names": failed_gates,
        "final_reject_reason": debug_row.get("final_reject_reason"),
        "trigger_required": True,
        "trigger_timeframe": debug_row.get("entry_tf"),
        "trigger_detected": None,
        "trigger_type_checked": "not_available_in_legacy_debug_row",
        "trigger_type_found": None,
        "candle_pattern_state": None,
        "sweep_state": None,
        "bos_choch_trigger_state": None,
        "displacement_state": None,
        "volume_confirmation_state": None,
        "m15_trigger_state": None,
        "m5_trigger_state": None,
        "exact_trigger_reject_reason": "not_available_in_legacy_debug_row" if trigger_failed else None,
    }


def _target_integrity(direction_detail):
    final_passed = bool(direction_detail.get("final_engine_b_passed"))
    used_true = bool(direction_detail.get("crypto_target_used_true_h4_liquidity_target"))
    used_fallback = bool(direction_detail.get("crypto_target_used_fallback_projection"))
    return {
        "old_target": direction_detail.get("crypto_target_old_target"),
        "old_rr": direction_detail.get("crypto_target_old_rr"),
        "tp1": direction_detail.get("crypto_target_tp1"),
        "tp2": direction_detail.get("crypto_target_tp2"),
        "final_target": direction_detail.get("crypto_target_final_target"),
        "final_rr": direction_detail.get("crypto_target_final_rr"),
        "target_mode_used": direction_detail.get("crypto_target_mode_used"),
        "used_true_h4_liquidity_target": used_true,
        "used_fallback_projection": used_fallback,
        "fallback_reason": direction_detail.get("crypto_target_fallback_reason"),
        "tp2_reject_reason": direction_detail.get("crypto_target_tp2_reject_reason"),
        "h4_liquidity_target_count": direction_detail.get("crypto_target_h4_liquidity_target_count"),
        "selected_h4_liquidity_rank": direction_detail.get("crypto_target_selected_h4_liquidity_rank"),
        "selected_h4_liquidity_price": direction_detail.get("crypto_target_selected_h4_liquidity_price"),
        "target_atr_multiple": direction_detail.get("crypto_target_atr_multiple"),
        "min_target_atr_multiple": direction_detail.get("crypto_target_min_target_atr_multiple"),
        "max_target_atr_multiple": direction_detail.get("crypto_target_max_target_atr_multiple"),
        "path_clear_to_tp2": direction_detail.get("crypto_target_path_clear_to_tp2"),
        "path_block_reason": direction_detail.get("crypto_target_path_block_reason"),
        "candidate_targets": direction_detail.get("crypto_target_candidate_targets"),
        "candidate_targets_total": direction_detail.get("crypto_target_candidate_targets_total"),
        "candidate_targets_considered": direction_detail.get("crypto_target_candidate_targets_considered"),
        "candidate_targets_rejected": direction_detail.get("crypto_target_candidate_targets_rejected"),
        "candidate_reject_reasons_count": direction_detail.get("crypto_target_candidate_reject_reasons_count"),
        "selected_target_price": direction_detail.get("crypto_target_selected_target_price"),
        "selected_target_tf": direction_detail.get("crypto_target_selected_target_tf"),
        "selected_target_type": direction_detail.get("crypto_target_selected_target_type"),
        "selected_target_rank": direction_detail.get("crypto_target_selected_target_rank"),
        "selected_target_rr": direction_detail.get("crypto_target_selected_target_rr"),
        "selected_target_atr_multiple": direction_detail.get("crypto_target_selected_target_atr_multiple"),
        "selected_target_is_structural": direction_detail.get("crypto_target_selected_target_is_structural"),
        "fallback_projection_price": direction_detail.get("crypto_target_fallback_projection_price"),
        "fallback_projection_rr": direction_detail.get("crypto_target_fallback_projection_rr"),
        "fallback_used_for_diagnostics_only": direction_detail.get("crypto_target_fallback_used_for_diagnostics_only"),
        "target_v2_reject_reason": direction_detail.get("crypto_target_v2_reject_reason"),
        "passed_because_of_true_h4_target": final_passed and used_true,
        "passed_because_of_fallback": final_passed and used_fallback,
    }


def _trigger_audit(direction_detail):
    return {
        "trigger_required": direction_detail.get("trigger_required"),
        "trigger_timeframe": direction_detail.get("trigger_timeframe"),
        "trigger_detected": direction_detail.get("trigger_detected"),
        "trigger_type_checked": direction_detail.get("trigger_type_checked"),
        "trigger_type_found": direction_detail.get("trigger_type_found"),
        "candle_pattern_state": direction_detail.get("candle_pattern_state"),
        "sweep_state": direction_detail.get("sweep_state"),
        "bos_choch_trigger_state": direction_detail.get("bos_choch_trigger_state"),
        "displacement_state": direction_detail.get("displacement_state"),
        "volume_confirmation_state": direction_detail.get("volume_confirmation_state"),
        "m15_trigger_state": direction_detail.get("m15_trigger_state"),
        "m5_trigger_state": direction_detail.get("m5_trigger_state"),
        "exact_trigger_reject_reason": direction_detail.get("exact_trigger_reject_reason"),
    }


def _apply_fallback_pass_policy(detail, allow_fallback_target_for_pass):
    target = detail["target_integrity"]
    if allow_fallback_target_for_pass or not target["used_fallback_projection"]:
        return detail

    if not detail["final_engine_b_passed"]:
        return detail

    detail = copy.deepcopy(detail)
    failed_gates = list(detail["debug_gate_failed_names"])
    if FALLBACK_NOT_ALLOWED_GATE not in failed_gates:
        failed_gates.append(FALLBACK_NOT_ALLOWED_GATE)
    detail["debug_gate_failed_names"] = failed_gates
    detail["failed_gates"] = failed_gates
    detail["passed"] = False
    detail["passed_all_gates"] = False
    detail["passed_all_non_trigger_gates"] = False
    detail["final_engine_b_passed"] = False
    detail["production_pass_flag"] = False
    detail["final_reject_reason"] = FALLBACK_NOT_ALLOWED_GATE
    detail["target_integrity"]["passed_because_of_fallback"] = False
    detail["audit_policy_note"] = "fallback target reported but disallowed for final pass"
    return detail


def _build_pair_detail(debug_row, direction, direction_detail, allow_fallback_target_for_pass=True):
    failed_gates = _failed_gate_names(direction_detail)
    final_passed = bool(direction_detail.get("final_engine_b_passed"))
    passed_all_gates, passed_all_non_trigger, trigger_failed, rr_failed = _gate_flags(
        failed_gates, final_passed
    )
    target = _target_integrity(direction_detail)
    detail = {
        "symbol": debug_row.get("symbol"),
        "display": debug_row.get("pair"),
        "direction": direction,
        "entry": direction_detail.get("entry"),
        "stop": direction_detail.get("stop"),
        "confidence_score": direction_detail.get("confidence_score"),
        "threshold": direction_detail.get("threshold"),
        "structural_verdict": direction_detail.get("structural_verdict"),
        "passed_all_gates": passed_all_gates,
        "passed_all_non_trigger_gates": passed_all_non_trigger,
        "final_engine_b_passed": final_passed,
        "trigger_failed": trigger_failed,
        "rr_failed": rr_failed,
        "production_pass_flag": bool(direction_detail.get("production_pass_flag")),
        "debug_gate_failed_names": failed_gates,
        "passed": final_passed,
        "failed_gates": failed_gates,
        "final_reject_reason": direction_detail.get("final_reject_reason"),
        "rr_value": target.get("final_rr"),
        "atr_value": debug_row.get("atr_value"),
        "candle_counts": {
            "d1": debug_row.get("d1_count", 0),
            "zone": debug_row.get("zone_count", 0),
            "entry": debug_row.get("entry_count", 0),
        },
        "style_profile": debug_row.get("style"),
        "timeframes": {
            "zone_tf": debug_row.get("zone_tf"),
            "entry_tf": debug_row.get("entry_tf"),
            "atr_tf": debug_row.get("atr_tf"),
        },
        "target_integrity": target,
        "trigger_audit": _trigger_audit(direction_detail),
    }
    return _apply_fallback_pass_policy(detail, allow_fallback_target_for_pass)


def _per_direction_target_output(detail):
    target = detail["target_integrity"]
    return {
        "symbol": detail.get("symbol"),
        "direction": detail.get("direction"),
        "entry": detail.get("entry"),
        "stop": detail.get("stop"),
        "old_target": target.get("old_target"),
        "old_rr": target.get("old_rr"),
        "tp1": target.get("tp1"),
        "tp2": target.get("tp2"),
        "final_target": target.get("final_target"),
        "final_rr": target.get("final_rr"),
        "target_mode_used": target.get("target_mode_used"),
        "used_true_h4_liquidity_target": target.get("used_true_h4_liquidity_target"),
        "used_fallback_projection": target.get("used_fallback_projection"),
        "fallback_reason": target.get("fallback_reason"),
        "tp2_reject_reason": target.get("tp2_reject_reason"),
        "h4_liquidity_target_count": target.get("h4_liquidity_target_count"),
        "selected_h4_liquidity_rank": target.get("selected_h4_liquidity_rank"),
        "selected_h4_liquidity_price": target.get("selected_h4_liquidity_price"),
        "target_atr_multiple": target.get("target_atr_multiple"),
        "min_target_atr_multiple": target.get("min_target_atr_multiple"),
        "max_target_atr_multiple": target.get("max_target_atr_multiple"),
        "path_clear_to_tp2": target.get("path_clear_to_tp2"),
        "path_block_reason": target.get("path_block_reason"),
        "candidate_targets_total": target.get("candidate_targets_total"),
        "candidate_targets_considered": target.get("candidate_targets_considered"),
        "candidate_targets_rejected": target.get("candidate_targets_rejected"),
        "candidate_reject_reasons_count": target.get("candidate_reject_reasons_count"),
        "selected_target_price": target.get("selected_target_price"),
        "selected_target_tf": target.get("selected_target_tf"),
        "selected_target_type": target.get("selected_target_type"),
        "selected_target_rank": target.get("selected_target_rank"),
        "selected_target_rr": target.get("selected_target_rr"),
        "selected_target_atr_multiple": target.get("selected_target_atr_multiple"),
        "selected_target_is_structural": target.get("selected_target_is_structural"),
        "fallback_projection_price": target.get("fallback_projection_price"),
        "fallback_projection_rr": target.get("fallback_projection_rr"),
        "fallback_used_for_diagnostics_only": target.get("fallback_used_for_diagnostics_only"),
        "target_v2_reject_reason": target.get("target_v2_reject_reason"),
        "passed_all_gates": detail.get("passed_all_gates"),
        "final_engine_b_passed": detail.get("final_engine_b_passed"),
        "failed_gate_names": detail.get("debug_gate_failed_names"),
    }


def _gate_counts(details):
    counts = Counter()
    for detail in details:
        for gate in detail["debug_gate_failed_names"]:
            if gate.startswith("rr="):
                counts["rr"] += 1
            else:
                counts[gate] += 1
    return dict(counts)


def _target_too_close_or_far_counts(details):
    counts = {"too_close": 0, "too_far": 0, "wrong_side": 0}
    for detail in details:
        reason = str(detail["target_integrity"].get("tp2_reject_reason") or "")
        if "too_close" in reason:
            counts["too_close"] += 1
        if "too_far" in reason:
            counts["too_far"] += 1
        if "wrong_direction" in reason:
            counts["wrong_side"] += 1
    return counts


def generate_aggregate_stats(pair_details):
    stats = {
        "total_directions": len(pair_details),
        "gate_failure_counts": {"loc": 0, "trigger": 0, "struct": 0, "rr": 0},
        "single_gate_failures": {"only_loc": 0, "only_trigger": 0, "only_struct": 0, "only_rr": 0},
        "passed_all_gates_count": 0,
        "passed_all_non_trigger_gates_count": 0,
        "final_engine_b_passed_count": 0,
        "production_pass_flag_count": 0,
        "score_above_threshold_but_failed": 0,
        "structural_clear_but_failed": 0,
        "rr_distribution": {},
        "score_distribution": {},
        "target_model_integrity": {},
        "true_h4_liquidity_analysis": {},
        "fallback_analysis": {},
        "passing_directions": [],
        "single_trigger_failure_directions": [],
        "consistency_errors": [],
    }

    rr_values = []
    score_values = []
    true_details = []
    fallback_details = []
    true_rr = []
    fallback_rr = []
    tp2_invalid_rejections = 0

    for detail in pair_details:
        failed_gates = detail["debug_gate_failed_names"]
        target = detail["target_integrity"]
        score = detail.get("confidence_score")
        final_rr = target.get("final_rr")

        if "loc" in failed_gates:
            stats["gate_failure_counts"]["loc"] += 1
        if "trigger" in failed_gates:
            stats["gate_failure_counts"]["trigger"] += 1
        if "struct" in failed_gates:
            stats["gate_failure_counts"]["struct"] += 1
        if any(g.startswith("rr=") or g == "rr_gate" for g in failed_gates):
            stats["gate_failure_counts"]["rr"] += 1

        if len(failed_gates) == 1:
            gate = failed_gates[0]
            if gate == "loc":
                stats["single_gate_failures"]["only_loc"] += 1
            elif gate == "trigger":
                stats["single_gate_failures"]["only_trigger"] += 1
                stats["single_trigger_failure_directions"].append(detail)
            elif gate == "struct":
                stats["single_gate_failures"]["only_struct"] += 1
            elif gate.startswith("rr=") or gate == "rr_gate":
                stats["single_gate_failures"]["only_rr"] += 1

        if detail["passed_all_gates"]:
            stats["passed_all_gates_count"] += 1
        if detail["passed_all_non_trigger_gates"]:
            stats["passed_all_non_trigger_gates_count"] += 1
        if detail["final_engine_b_passed"]:
            stats["final_engine_b_passed_count"] += 1
            stats["passing_directions"].append(detail)
        if detail["production_pass_flag"]:
            stats["production_pass_flag_count"] += 1

        threshold = detail.get("threshold")
        if score is not None:
            score_values.append(score)
            if threshold is not None and score >= threshold and not detail["final_engine_b_passed"]:
                stats["score_above_threshold_but_failed"] += 1
        if detail.get("structural_verdict") == "CLEAR" and not detail["final_engine_b_passed"]:
            stats["structural_clear_but_failed"] += 1
        if final_rr is not None:
            rr_values.append(final_rr)
        if target["used_true_h4_liquidity_target"]:
            true_details.append(detail)
            true_rr.append(final_rr)
        if target["used_fallback_projection"]:
            fallback_details.append(detail)
            fallback_rr.append(final_rr)
        if target.get("tp2_reject_reason"):
            tp2_invalid_rejections += 1

        if detail["passed_all_gates"] and failed_gates:
            stats["consistency_errors"].append(
                f"{detail['display']} {detail['direction']}: passed_all_gates with failures {failed_gates}"
            )
        if detail["final_engine_b_passed"] and failed_gates:
            stats["consistency_errors"].append(
                f"{detail['display']} {detail['direction']}: final pass with failures {failed_gates}"
            )
        if detail["trigger_failed"] and detail["passed_all_gates"]:
            stats["consistency_errors"].append(
                f"{detail['display']} {detail['direction']}: trigger_failed with passed_all_gates"
            )

    stats["rr_distribution"] = _rr_distribution(rr_values)
    stats["score_distribution"] = _rr_distribution(score_values)
    stats["target_model_integrity"] = {
        "true_h4_liquidity_tp2_count": len(true_details),
        "fallback_2r_projection_count": len(fallback_details),
        "structural_target_selected_count": sum(
            1 for d in pair_details if d["target_integrity"].get("selected_target_is_structural")
        ),
        "structural_target_passed_count": sum(
            1
            for d in pair_details
            if d["target_integrity"].get("selected_target_is_structural")
            and d["final_engine_b_passed"]
        ),
        "h4_structural_target_selected_count": sum(
            1
            for d in pair_details
            if d["target_integrity"].get("selected_target_is_structural")
            and d["target_integrity"].get("selected_target_tf") == "H4"
        ),
        "d1_structural_target_selected_count": sum(
            1
            for d in pair_details
            if d["target_integrity"].get("selected_target_is_structural")
            and d["target_integrity"].get("selected_target_tf") == "D1"
        ),
        "rejected_because_tp2_invalid_count": tp2_invalid_rejections,
        "passed_using_true_h4_liquidity_count": sum(
            1 for d in pair_details if d["target_integrity"]["passed_because_of_true_h4_target"]
        ),
        "passed_using_fallback_count": sum(
            1 for d in pair_details if d["target_integrity"]["passed_because_of_fallback"]
        ),
        "rr_distribution_true_h4_liquidity_only": _rr_distribution(true_rr),
        "rr_distribution_fallback_only": _rr_distribution(fallback_rr),
    }
    stats["true_h4_liquidity_analysis"] = {
        "count": len(true_details),
        "passed_count": sum(1 for d in true_details if d["final_engine_b_passed"]),
        "failed_count": sum(1 for d in true_details if not d["final_engine_b_passed"]),
        "failed_gate_counts": _gate_counts(true_details),
        "rr_distribution": _rr_distribution(true_rr),
        "target_bounds_reject_counts": _target_too_close_or_far_counts(true_details),
        "trigger_failed_count": sum(1 for d in true_details if "trigger" in d["debug_gate_failed_names"]),
        "struct_failed_count": sum(1 for d in true_details if "struct" in d["debug_gate_failed_names"]),
        "location_failed_count": sum(1 for d in true_details if "loc" in d["debug_gate_failed_names"]),
        "path_clarity_block_count": sum(
            1 for d in true_details if d["target_integrity"].get("path_block_reason")
        ),
        "directions": [_per_direction_target_output(d) for d in true_details],
    }
    fallback_reasons = Counter(
        d["target_integrity"].get("fallback_reason") or "not_reported"
        for d in fallback_details
    )
    tp2_reasons = Counter(
        d["target_integrity"].get("tp2_reject_reason") or "not_reported"
        for d in fallback_details
    )
    stats["fallback_analysis"] = {
        "count": len(fallback_details),
        "passed_count": sum(1 for d in fallback_details if d["final_engine_b_passed"]),
        "fallback_reason_counts": dict(fallback_reasons),
        "tp2_reject_reason_counts": dict(tp2_reasons),
        "no_h4_target_existed_count": sum(
            1
            for d in fallback_details
            if (d["target_integrity"].get("h4_liquidity_target_count") or 0) < 2
        ),
        "wrong_side_count": sum(
            1
            for d in fallback_details
            if d["target_integrity"].get("tp2_reject_reason") == "tp2_wrong_direction"
        ),
        "atr_bounds_rejected_count": sum(
            1
            for d in fallback_details
            if "too_close" in str(d["target_integrity"].get("tp2_reject_reason") or "")
            or "too_far" in str(d["target_integrity"].get("tp2_reject_reason") or "")
        ),
        "skip_first_second_target_invalid_count": sum(
            1
            for d in fallback_details
            if d["target_integrity"].get("selected_h4_liquidity_rank") == 2
            and d["target_integrity"].get("tp2_reject_reason")
        ),
        "path_clarity_rejected_count": sum(
            1 for d in fallback_details if d["target_integrity"].get("path_block_reason")
        ),
        "directions": [_per_direction_target_output(d) for d in fallback_details],
    }
    return stats


def _fetch_debug_rows(use_local_app=True):
    if use_local_app:
        client = athena_module.app.test_client()
        response = client.post(
            "/api/scan-naked",
            json={"assetClass": "crypto", "style": "auto", "debug": True},
        )
        if response.status_code != 200:
            raise RuntimeError(f"local app returned {response.status_code}")
        return response.get_json().get("debugRows", [])

    response = requests.post(
        f"{API_BASE}/api/scan-naked",
        json={"assetClass": "crypto", "style": "auto", "debug": True},
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"API returned {response.status_code}")
    return response.json().get("debugRows", [])


def _set_audit_config(overrides):
    old_values = {key: runtime_config.CONFIG.get(key) for key in overrides}
    for key, value in overrides.items():
        runtime_config.CONFIG[key] = value
        athena_module.CONFIG[key] = value
    return old_values


def _restore_audit_config(old_values):
    for key, value in old_values.items():
        runtime_config.CONFIG[key] = value
        athena_module.CONFIG[key] = value


def _build_report_from_debug_rows(
    debug_rows,
    scenario_name,
    crypto_target_model_enabled,
    allow_fallback_target_for_pass,
    crypto_target_v2_enabled=None,
):
    report = {
        "audit_timestamp": datetime.utcnow().isoformat(),
        "scenario": scenario_name,
        "total_pairs": len(ENABLED_CRYPTO),
        "pair_details": [],
        "per_direction_target_provenance": [],
        "aggregate_stats": {},
        "audit_config_overrides": {
            "ENGINE_B_CRYPTO_TARGET_MODEL_ENABLED": crypto_target_model_enabled,
            "ENGINE_B_CRYPTO_TARGET_V2_ENABLED": crypto_target_v2_enabled,
            "ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS": allow_fallback_target_for_pass,
            "override_scope": "audit_process_only_not_written_to_config",
        },
        "report_contract": {
            "passed_all_gates": "No debug gate failures and final Engine B direction passed.",
            "passed_all_non_trigger_gates": "All reported gates except trigger passed.",
            "final_engine_b_passed": "Engine B confidence gate and final RR guard passed after audit fallback policy.",
            "production_pass_flag": "Direction would emit a scan signal after audit fallback policy.",
            "debug_gate_failed_names": "Per-direction gate failures captured from Engine B diagnostics plus audit-only fallback policy gates.",
        },
    }

    for debug_row in debug_rows:
        direction_details = debug_row.get("direction_details") or {}
        for direction in ("LONG", "SHORT"):
            direction_detail = direction_details.get(direction)
            if not isinstance(direction_detail, dict):
                direction_detail = _legacy_direction_detail(debug_row, direction)
            detail = _build_pair_detail(
                debug_row,
                direction,
                direction_detail,
                allow_fallback_target_for_pass=allow_fallback_target_for_pass,
            )
            report["pair_details"].append(detail)
            report["per_direction_target_provenance"].append(_per_direction_target_output(detail))

    report["aggregate_stats"] = generate_aggregate_stats(report["pair_details"])
    return report


def run_gate_calibration_audit(
    crypto_target_model_enabled=None,
    allow_fallback_target_for_pass=True,
    crypto_target_v2_enabled=None,
    use_local_app=True,
    scenario_name="single",
    report_filename="crypto_engine_b_gate_calibration.json",
):
    log.info("Starting Crypto Engine B Gate Calibration Audit for %s enabled pairs", len(ENABLED_CRYPTO))
    overrides = {
        "ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS": bool(allow_fallback_target_for_pass)
    }
    if crypto_target_model_enabled is not None:
        overrides["ENGINE_B_CRYPTO_TARGET_MODEL_ENABLED"] = bool(crypto_target_model_enabled)
    if crypto_target_v2_enabled is not None:
        overrides["ENGINE_B_CRYPTO_TARGET_V2_ENABLED"] = bool(crypto_target_v2_enabled)
    old_values = _set_audit_config(overrides)
    try:
        debug_rows = _fetch_debug_rows(use_local_app=use_local_app)
        log.info("Received %s debug rows from API", len(debug_rows))
    except (requests.exceptions.RequestException, RuntimeError) as exc:
        log.error("API request failed: %s", exc)
        return {
            "audit_timestamp": datetime.utcnow().isoformat(),
            "scenario": scenario_name,
            "total_pairs": len(ENABLED_CRYPTO),
            "pair_details": [],
            "aggregate_stats": {},
            "error": str(exc),
        }
    finally:
        _restore_audit_config(old_values)

    report = _build_report_from_debug_rows(
        debug_rows,
        scenario_name=scenario_name,
        crypto_target_model_enabled=crypto_target_model_enabled,
        allow_fallback_target_for_pass=allow_fallback_target_for_pass,
        crypto_target_v2_enabled=crypto_target_v2_enabled,
    )

    report_path = Path(__file__).parent / "logs" / report_filename
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print_calibration_summary(report)
    log.info("Calibration report saved to %s", report_path)
    return report


def _scenario_summary(report):
    stats = report.get("aggregate_stats", {})
    integrity = stats.get("target_model_integrity", {})
    return {
        "scenario": report.get("scenario"),
        "total_directions": stats.get("total_directions"),
        "trigger_failed": stats.get("gate_failure_counts", {}).get("trigger"),
        "passed_all_gates": stats.get("passed_all_gates_count"),
        "final_engine_b_passed": stats.get("final_engine_b_passed_count"),
        "production_pass_flag": stats.get("production_pass_flag_count"),
        "true_h4_liquidity_tp2_used": integrity.get("true_h4_liquidity_tp2_count"),
        "fallback_projection_used": integrity.get("fallback_2r_projection_count"),
        "structural_target_selected": integrity.get("structural_target_selected_count"),
        "structural_target_passed": integrity.get("structural_target_passed_count"),
        "h4_structural_target_selected": integrity.get("h4_structural_target_selected_count"),
        "d1_structural_target_selected": integrity.get("d1_structural_target_selected_count"),
        "passed_using_true_h4_liquidity": integrity.get("passed_using_true_h4_liquidity_count"),
        "passed_using_fallback": integrity.get("passed_using_fallback_count"),
        "consistency_errors": stats.get("consistency_errors"),
    }


def run_h4_target_provenance_comparison(use_local_app=True):
    disabled = run_gate_calibration_audit(
        crypto_target_model_enabled=False,
        allow_fallback_target_for_pass=True,
        crypto_target_v2_enabled=False,
        use_local_app=use_local_app,
        scenario_name="A_target_model_disabled",
        report_filename="crypto_engine_b_gate_calibration_A_disabled.json",
    )
    enabled_allowed = run_gate_calibration_audit(
        crypto_target_model_enabled=True,
        allow_fallback_target_for_pass=True,
        crypto_target_v2_enabled=False,
        use_local_app=use_local_app,
        scenario_name="B_target_model_enabled_fallback_allowed",
        report_filename="crypto_engine_b_gate_calibration_B_fallback_allowed.json",
    )
    enabled_no_fallback = run_gate_calibration_audit(
        crypto_target_model_enabled=True,
        allow_fallback_target_for_pass=False,
        crypto_target_v2_enabled=False,
        use_local_app=use_local_app,
        scenario_name="C_target_model_enabled_fallback_not_allowed",
        report_filename="crypto_engine_b_gate_calibration_C_fallback_not_allowed.json",
    )
    target_v2 = run_gate_calibration_audit(
        crypto_target_model_enabled=True,
        allow_fallback_target_for_pass=False,
        crypto_target_v2_enabled=True,
        use_local_app=use_local_app,
        scenario_name="D_target_v2_fallback_not_allowed",
        report_filename="crypto_engine_b_gate_calibration_D_target_v2.json",
    )

    comparison = {
        "audit_timestamp": datetime.utcnow().isoformat(),
        "scope": "crypto_engine_b_h4_target_provenance_validation",
        "config_default_verified": {
            "ENGINE_B_CRYPTO_TARGET_MODEL_ENABLED": runtime_config.CONFIG.get(
                "ENGINE_B_CRYPTO_TARGET_MODEL_ENABLED"
            ),
            "ENGINE_B_CRYPTO_TARGET_V2_ENABLED": runtime_config.CONFIG.get(
                "ENGINE_B_CRYPTO_TARGET_V2_ENABLED"
            ),
            "ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS": runtime_config.CONFIG.get(
                "ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS"
            ),
        },
        "scenarios": {
            "A_target_model_disabled": _scenario_summary(disabled),
            "B_target_model_enabled_fallback_allowed": _scenario_summary(enabled_allowed),
            "C_target_model_enabled_fallback_not_allowed": _scenario_summary(enabled_no_fallback),
            "D_target_v2_fallback_not_allowed": _scenario_summary(target_v2),
        },
        "per_direction_target_provenance": target_v2["per_direction_target_provenance"],
        "target_v2_analysis": target_v2["aggregate_stats"],
        "true_h4_liquidity_analysis": target_v2["aggregate_stats"][
            "true_h4_liquidity_analysis"
        ],
        "fallback_analysis": target_v2["aggregate_stats"]["fallback_analysis"],
        "fallback_not_allowed_analysis": enabled_no_fallback["aggregate_stats"],
        "evidence_notes": [
            "Fallback projection is reported as diagnostic target provenance only in scenarios C and D.",
            "Scenario D enables target v2 and disallows fallback targets for final passes.",
            "No trigger logic or RR threshold is changed by this audit.",
        ],
    }
    out_path = Path(__file__).parent / "logs" / "crypto_h4_target_provenance_audit.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2)

    print("\n" + "=" * 80)
    print("CRYPTO ENGINE B H4 TARGET PROVENANCE COMPARISON")
    print("=" * 80)
    for name, summary in comparison["scenarios"].items():
        print(
            f"{name}: final_pass={summary['final_engine_b_passed']} "
            f"true_h4_pass={summary['passed_using_true_h4_liquidity']} "
            f"fallback_pass={summary['passed_using_fallback']} "
            f"trigger_failed={summary['trigger_failed']}"
        )
    true_h4 = comparison["true_h4_liquidity_analysis"]
    fallback = comparison["fallback_analysis"]
    print("\nTrue H4 liquidity:")
    print(f"  used={true_h4['count']} passed={true_h4['passed_count']} rr={true_h4['rr_distribution']}")
    print(f"  failed_gates={true_h4['failed_gate_counts']}")
    print("\nFallback:")
    print(f"  used={fallback['count']} passed={fallback['passed_count']}")
    print(f"  fallback_reason_counts={fallback['fallback_reason_counts']}")
    print(f"  tp2_reject_reason_counts={fallback['tp2_reject_reason_counts']}")
    print(f"\nProvenance report saved to: {out_path}")
    print("=" * 80)
    return comparison


def print_calibration_summary(report):
    stats = report["aggregate_stats"]
    print("\n" + "=" * 80)
    print(f"CRYPTO ENGINE B GATE CALIBRATION AUDIT SUMMARY: {report.get('scenario')}")
    print("=" * 80)
    print(f"Total directions analyzed: {stats['total_directions']}")
    print(f"Passed all gates: {stats['passed_all_gates_count']}")
    print(f"Passed all non-trigger gates: {stats['passed_all_non_trigger_gates_count']}")
    print(f"Final Engine B passed: {stats['final_engine_b_passed_count']}")
    print(f"Production pass flag: {stats['production_pass_flag_count']}")

    print("\n--- Gate Failure Counts ---")
    for gate, count in stats["gate_failure_counts"].items():
        print(f"  {gate:10s}: {count}")

    print("\n--- Target Model Integrity ---")
    integrity = stats["target_model_integrity"]
    print(f"  true H4 liquidity TP2: {integrity['true_h4_liquidity_tp2_count']}")
    print(f"  fallback 2R projection: {integrity['fallback_2r_projection_count']}")
    print(f"  TP2 invalid rejection: {integrity['rejected_because_tp2_invalid_count']}")
    print(f"  passed using true H4 liquidity: {integrity['passed_using_true_h4_liquidity_count']}")
    print(f"  passed using fallback: {integrity['passed_using_fallback_count']}")

    if stats["consistency_errors"]:
        print("\n--- Consistency Errors ---")
        for err in stats["consistency_errors"]:
            print(f"  {err}")
    else:
        print("\nConsistency check: OK")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--crypto-target-model-enabled",
        action="store_true",
        help="Audit-only override; does not write config.yaml or enable the model by default.",
    )
    parser.add_argument(
        "--fallback-not-allowed",
        action="store_true",
        help="Audit-only validation: fallback targets are reported but cannot create final passes.",
    )
    parser.add_argument(
        "--target-v2",
        action="store_true",
        help="Audit-only override: enable Crypto Target Selection v2.",
    )
    parser.add_argument(
        "--comparison",
        action="store_true",
        help="Run A/B/C H4 target provenance comparison.",
    )
    parser.add_argument(
        "--localhost-api",
        action="store_true",
        help="Call the running localhost API instead of the current code via Flask test_client.",
    )
    args = parser.parse_args()
    if args.comparison:
        run_h4_target_provenance_comparison(use_local_app=not args.localhost_api)
    else:
        run_gate_calibration_audit(
            crypto_target_model_enabled=True if args.crypto_target_model_enabled else None,
            allow_fallback_target_for_pass=not args.fallback_not_allowed,
            crypto_target_v2_enabled=True if args.target_v2 else None,
            use_local_app=not args.localhost_api,
        )
