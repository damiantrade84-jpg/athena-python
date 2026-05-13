"""Threshold audit helpers for scan-time diagnostics.

This module is report-only. It must not change scan, paper, live, risk, or
execution decisions.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CONFIG
from scoring import get_score_threshold

AUDIT_LOG_PATH = Path("logs") / "threshold_audit" / "signal_funnel.jsonl"
_WRITE_LOCK = threading.Lock()

REQUIRED_FIELDS = [
    "timestamp",
    "symbol",
    "asset_type",
    "timeframes_used",
    "data_freshness_status",
    "engine_a_raw_score",
    "engine_a_max_score",
    "engine_a_normalized_score",
    "engine_a_direction",
    "engine_a_passed",
    "engine_a_fail_reasons",
    "engine_a_adx_value",
    "engine_a_adx_gate_result",
    "engine_a_trend_score",
    "engine_a_momentum_score",
    "engine_a_addon_score",
    "engine_a_session_multiplier",
    "engine_b_raw_score",
    "engine_b_max_score",
    "engine_b_normalized_score",
    "engine_b_direction",
    "engine_b_structural_data_valid",
    "engine_b_structural_verdict",
    "engine_b_confidence_passed",
    "engine_b_checklist_components",
    "engine_b_structural_tp_diagnostics",
    "engine_b_trigger_diagnostics",
    "engine_b_d1_conflict_diagnostics",
    "engine_b_shadow_simulation",
    "engine_b_hard_fail_reasons",
    "engine_b_soft_warnings",
    "engine_b_diagnostic_notes",
    "engine_c_consensus_type",
    "engine_c_final_conviction",
    "engine_c_decision_state",
    "engine_c_block_watchlist_reason",
    "risk_check_allowed",
    "risk_check_fail_reasons",
    "final_scan_result",
    "thresholds",
    "shadow_thresholds",
]


def audit_enabled() -> bool:
    if os.environ.get("ATHENA_THRESHOLD_AUDIT"):
        return True
    cfg = CONFIG.get("THRESHOLD_AUDIT") or {}
    if isinstance(cfg, dict) and "ENABLED" in cfg:
        return bool(cfg.get("ENABLED"))
    return False


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except (TypeError, ValueError):
        return default


def _norm(score: float | None, max_score: float | None) -> float:
    score_f = _safe_float(score, 0.0) or 0.0
    max_f = _safe_float(max_score, 0.0) or 0.0
    if max_f <= 0:
        return 0.0
    return round(max(0.0, min(1.0, score_f / max_f)), 4)


def _diagnostic_codes(signal: dict[str, Any] | None) -> list[str]:
    if not isinstance(signal, dict):
        return []
    codes: list[str] = []
    for item in signal.get("scanDiagnostics") or []:
        if isinstance(item, dict) and item.get("code"):
            codes.append(str(item.get("code")))
    return codes


def _engine_a_fail_reasons(
    signal: dict[str, Any] | None,
    threshold: float,
    final_tier: str | None = None,
) -> list[str]:
    if not isinstance(signal, dict):
        return ["no_engine_a_signal"]
    reasons = _diagnostic_codes(signal)
    score = _safe_float(signal.get("confluenceScore"), 0.0) or 0.0
    if score < threshold and "below_engine_a_threshold" not in reasons:
        reasons.append("below_engine_a_threshold")
    if signal.get("direction") not in ("LONG", "SHORT"):
        reasons.append("engine_a_direction_missing")
    fd = signal.get("factorDiagnostics") or {}
    if fd.get("minDirectionalFailed"):
        reasons.append("engine_a_min_directional_failed")
    trend_state = str(signal.get("trendState") or "")
    if trend_state in {"DEAD RANGING", "DEVELOPING"}:
        reasons.append(f"trend_state_{trend_state.lower().replace(' ', '_')}")
    if final_tier == "watchlist":
        reasons.append("scan_watchlist")
    return sorted(set(reasons))


def _adx_gate_result(signal: dict[str, Any] | None) -> str:
    if not isinstance(signal, dict):
        return "not_evaluated"
    fd = signal.get("factorDiagnostics") or {}
    fs = signal.get("factorScores") or {}
    adx_mult = _safe_float(fs.get("adx_multiplier"), None)
    adx_source = fs.get("adx_source") or (fd.get("feedStatus") or {}).get("adx")
    if adx_mult == 0.0:
        return "hard_fail"
    if adx_source in ("missing", "parse_error"):
        return f"soft_penalty_{adx_source}"
    if adx_mult is not None and adx_mult < 1.0:
        return "soft_penalty"
    if adx_mult == 1.0:
        return "passed"
    return "unknown"


def _engine_b_hard_fail_reasons(conf_b: dict[str, Any] | None, res_b: dict[str, Any] | None) -> list[str]:
    """Reasons that directly cause confidence.passed=False."""
    reasons: list[str] = []
    if not isinstance(res_b, dict):
        return ["engine_b_not_evaluated"]
    if not isinstance(conf_b, dict):
        return ["engine_b_confidence_not_evaluated"]
    # Checklist components that directly block
    for key in ("structure_ok", "location_ok", "entry_ok", "room_ok", "rr_ok", "macro_ok"):
        if key == "room_ok" and conf_b.get("space_gate_ok") is True:
            continue
        if conf_b.get(key) is False:
            reasons.append(f"engine_b_{key}_false")
    # If passed is explicitly False, it's a hard fail
    if conf_b.get("passed") is False:
        reasons.append("engine_b_confidence_passed_false")
    # If structural_verdict is not CLEAR, it's a hard fail for the structural component
    if res_b.get("structural_verdict") != "CLEAR":
        reasons.append("engine_b_structural_verdict_not_clear")
    return sorted(set(reasons))


def _engine_b_soft_warnings(conf_b: dict[str, Any] | None, res_b: dict[str, Any] | None) -> list[str]:
    """Warnings that reduce confidence or are noteworthy but do not block."""
    warnings: list[str] = []
    if not isinstance(res_b, dict):
        return []
    # TP structural limitation is a warning if passed is still True (means it fell back to RR)
    if res_b.get("tp_structural_limited") and (conf_b or {}).get("passed") is True:
        warnings.append("structural_tp_too_close_fallback_to_rr")
    # D1 conflict is a warning if passed is still True (means it's a watchlist condition)
    if res_b.get("d1_pd_array_conflict") and (conf_b or {}).get("passed") is True:
        warnings.append("d1_pd_array_conflict_watchlist_candidate")
    # No trigger pattern when structure is clear is a warning (watchlist candidate)
    if res_b.get("structural_verdict") == "CLEAR" and not res_b.get("trigger_ok"):
        warnings.append("no_trigger_pattern_structure_ready")
    return sorted(set(warnings))


def _engine_b_diagnostic_notes(conf_b: dict[str, Any] | None, res_b: dict[str, Any] | None) -> list[str]:
    """Useful context only - does not imply failure or warning."""
    notes: list[str] = []
    if not isinstance(res_b, dict):
        return []
    # TP source information
    tp_source = res_b.get("tp_source")
    if tp_source:
        notes.append(f"tp_source_{tp_source}")
    # Trigger pattern
    trigger_pattern = res_b.get("trigger_pattern")
    if trigger_pattern:
        notes.append(f"trigger_pattern_{trigger_pattern}")
    # Lifecycle state
    lifecycle = res_b.get("lifecycle_state")
    if lifecycle:
        notes.append(f"lifecycle_{lifecycle}")
    # BOS/CHoCH status
    if res_b.get("bos_data"):
        notes.append("bos_present")
    if res_b.get("choch_data"):
        notes.append("choch_present")
    # Order blocks/FVGs
    if res_b.get("order_blocks"):
        notes.append("order_blocks_present")
    if res_b.get("active_fvgs"):
        notes.append("fvgs_present")
    return sorted(set(notes))


def _engine_b_fail_reasons(conf_b: dict[str, Any] | None, res_b: dict[str, Any] | None) -> list[str]:
    """Legacy function - returns combined reasons for backward compatibility."""
    reasons: list[str] = []
    if not isinstance(res_b, dict):
        return ["engine_b_not_evaluated"]
    if res_b.get("structural_verdict") != "CLEAR":
        reasons.append("engine_b_structural_verdict_not_clear")
    if not isinstance(conf_b, dict):
        return sorted(set(reasons + ["engine_b_confidence_not_evaluated"]))
    for key in ("structure_ok", "location_ok", "entry_ok", "room_ok", "rr_ok", "macro_ok"):
        if key == "room_ok" and conf_b.get("space_gate_ok") is True:
            continue
        if conf_b.get(key) is False:
            reasons.append(f"engine_b_{key}_false")
    if conf_b.get("passed") is not True:
        reasons.append("engine_b_confidence_passed_false")
    diag = (conf_b.get("engine_b_diagnostics") or {}).get("reason_codes") or []
    reasons.extend(str(x) for x in diag if x)
    return sorted(set(reasons))


def _engine_b_components(conf_b: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(conf_b, dict):
        return {}
    keys = [
        "structure_ok",
        "location_ok",
        "zone_ok",
        "trigger_ok",
        "entry_ok",
        "room_ok",
        "space_gate_ok",
        "rr_ok",
        "macro_ok",
        "breakout_ok",
        "tp_side_ok",
        "profile_ok",
        "trigger_pattern",
        "rr",
        "lifecycle_state",
    ]
    return {k: conf_b.get(k) for k in keys if k in conf_b}


def _pip_or_tick_size(symbol: str | None, asset_type: str | None, current_price: float | None) -> float | None:
    asset = str(asset_type or "").lower()
    sym = str(symbol or "").upper()
    if asset == "forex":
        return 0.01 if "JPY" in sym else 0.0001
    if asset == "crypto" and current_price and current_price > 0:
        return max(current_price * 0.0001, 1e-8)
    return None


def _distance_metrics(
    current_price: float | None,
    sl: float | None,
    tp: float | None,
    direction: str | None,
    atr: float | None,
) -> dict[str, Any]:
    distance_to_tp = None
    distance_to_sl = None
    rr = None
    target_correct_side = None
    if current_price is not None and tp is not None:
        target_correct_side = (tp > current_price) if direction == "LONG" else (tp < current_price)
        distance_to_tp = abs(tp - current_price) if target_correct_side else 0.0
    if current_price is not None and sl is not None:
        distance_to_sl = abs(current_price - sl)
    if distance_to_sl and distance_to_tp is not None:
        rr = distance_to_tp / distance_to_sl
    return {
        "distance_to_tp": round(distance_to_tp, 8) if distance_to_tp is not None else None,
        "distance_to_sl": round(distance_to_sl, 8) if distance_to_sl is not None else None,
        "rr": round(rr, 4) if rr is not None else None,
        "atr_multiple_to_tp": round(distance_to_tp / atr, 4) if distance_to_tp is not None and atr else None,
        "target_correct_side_of_entry": target_correct_side,
    }


def _engine_b_structural_tp_diagnostics(
    pair: dict[str, Any],
    res_b: dict[str, Any] | None,
    conf_b: dict[str, Any] | None,
    style_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(res_b, dict):
        return {}
    current_price = _safe_float(res_b.get("current_price"), _safe_float((pair or {}).get("price"), None))
    sl = _safe_float(res_b.get("recommended_stop_loss"), None)
    tp = _safe_float(res_b.get("recommended_take_profit"), None)
    atr = _safe_float(res_b.get("atr"), None)
    direction = res_b.get("direction") or (pair or {}).get("direction")
    metrics = _distance_metrics(current_price, sl, tp, direction, atr)
    nearest = res_b.get("selected_structural_target") or {}
    pip_tick = _pip_or_tick_size(pair.get("display") or pair.get("symbol"), pair.get("type"), current_price)
    
    # Calculate SL side correctness
    sl_side_ok = None
    if sl is not None and current_price is not None:
        if direction == "LONG":
            sl_side_ok = sl < current_price
        elif direction == "SHORT":
            sl_side_ok = sl > current_price
    
    # Extract zone details from selected target
    chosen_zone = nearest.get("zone", {})
    chosen_zone_type = chosen_zone.get("type") if chosen_zone else None
    chosen_zone_low = chosen_zone.get("low") if chosen_zone else None
    chosen_zone_high = chosen_zone.get("high") if chosen_zone else None
    chosen_zone_mitigated = chosen_zone.get("mitigated", False) if chosen_zone else None
    chosen_zone_age_bars = chosen_zone.get("age_bars") if chosen_zone else None
    
    # Determine if TP limitation is a hard block
    # If tp_structural_limited but passed is True, it's a soft warning (fell back to RR)
    # If tp_structural_limited and passed is False, it's a hard block
    tp_limited = bool(res_b.get("tp_structural_limited"))
    passed = (conf_b or {}).get("passed") is True
    fail_is_hard_block = tp_limited and not passed
    
    return {
        "symbol": pair.get("display") or pair.get("symbol"),
        "asset_type": pair.get("type"),
        "direction": direction,
        "entry": current_price,
        "structural_sl": sl,
        "structural_tp": tp,
        "nearest_target_type": res_b.get("nearest_target_type"),
        "nearest_target_price": res_b.get("nearest_target_price"),
        "target_source": res_b.get("tp_source"),
        "target_side_ok": metrics.get("target_correct_side_of_entry"),
        "sl_side_ok": sl_side_ok,
        "min_rr_required": (style_profile or {}).get("min_rr"),
        "atr": atr,
        "tick_size_or_pip_size": pip_tick,
        "spread": None,
        "chosen_zone_type": chosen_zone_type,
        "chosen_zone_low": chosen_zone_low,
        "chosen_zone_high": chosen_zone_high,
        "chosen_zone_mitigated": chosen_zone_mitigated,
        "chosen_zone_age_bars": chosen_zone_age_bars,
        "fail_is_hard_block": fail_is_hard_block,
        "current_price": current_price,
        "target_candidates": res_b.get("structural_target_candidates") or [],
        "tp_structural_limited": tp_limited,
        "fail_reason": "structural_tp_too_close" if tp_limited else "",
        **metrics,
    }


def _engine_b_trigger_diagnostics(res_b: dict[str, Any] | None, conf_b: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(res_b, dict):
        return {}
    missing = []
    if not res_b.get("trigger_ok"):
        missing.append("no_price_action_trigger")
    if conf_b and conf_b.get("entry_ok") is False:
        missing.append("entry_ok_false")
    
    # Determine classification
    classification = ""
    structural_clear = res_b.get("structural_verdict") == "CLEAR"
    structure_ok = (conf_b or {}).get("structure_ok") is True
    entry_ok = (conf_b or {}).get("entry_ok") is True
    trigger_ok = res_b.get("trigger_ok") is True
    
    if structural_clear and structure_ok and not trigger_ok:
        classification = "STRUCTURE_READY_NO_TRIGGER"
    elif not structural_clear or not structure_ok:
        classification = "NO_STRUCTURE_NO_TRIGGER"
    elif structural_clear and structure_ok and trigger_ok:
        classification = "TRIGGER_PRESENT"
    else:
        classification = "POSSIBLE_WATCHLIST_ENTRY_PENDING"
    
    # Check BOS/CHoCH presence
    bos_data = res_b.get("bos_data") or {}
    choch_data = res_b.get("choch_data") or {}
    bos_present = bool(bos_data.get("bos_bull") or bos_data.get("bos_bear"))
    choch_present = bool(choch_data.get("choch_bull") or choch_data.get("choch_bear"))
    
    # Determine trigger policy (default to standard)
    trigger_policy = "STANDARD"
    
    return {
        "symbol": None,  # Will be set at higher level
        "asset_type": None,  # Will be set at higher level
        "direction": res_b.get("direction"),
        "structural_verdict": res_b.get("structural_verdict"),
        "checklist_structure_ok": (conf_b or {}).get("structure_ok"),
        "bos_present": bos_present,
        "choch_present": choch_present,
        "ob_present": bool(res_b.get("order_blocks")),
        "fvg_present": bool(res_b.get("active_fvgs")),
        "sweep_present": bool(res_b.get("liquidity_sweep")),
        "breaker_present": bool(res_b.get("breaker_block") or res_b.get("breaker_blocks")),
        "zone_retest": bool(res_b.get("zone_touched") or res_b.get("near_active_zone")),
        "rejection_candle": bool(res_b.get("rejection_candle")),
        "displacement_candle": bool(res_b.get("strong_close")),
        "close_back_inside_outside_level": None,  # Not currently tracked
        "close_beyond_level": bool(res_b.get("inside_break_candle")),
        "volume_confirmation": res_b.get("bos_volume_confirmed"),
        "trigger_timeframe": res_b.get("trigger_timeframe"),
        "latest_confirmed_candle_time": res_b.get("latest_confirmed_trigger_candle_time"),
        "trigger_policy": trigger_policy,
        "exact_missing_trigger_condition": ", ".join(missing),
        "classification": classification,
        "trigger_pattern": res_b.get("trigger_pattern"),
    }


def _engine_b_d1_conflict_diagnostics(res_b: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(res_b, dict):
        return {}
    current_price = _safe_float(res_b.get("current_price"), None)
    atr = _safe_float(res_b.get("atr"), None)
    
    # Extract D1 swing details if available
    d1_swing_high = None
    d1_swing_low = None
    d1_equilibrium = None
    
    # Determine conflict hard block status
    # Conflict is hard block if it's within 3 ATRs (configurable) and on TP side (opposing direction)
    configured_hard_block_atr_distance = 3.0  # Default from market_structure.py
    conflicts = res_b.get("d1_conflict_metric_details") or []
    direction = res_b.get("direction")
    
    # Determine if any conflict is a hard block
    conflict_is_hard_block = False
    for conflict in conflicts:
        dist_atr = conflict.get("distance_in_atr", 0)
        if dist_atr is not None and dist_atr < configured_hard_block_atr_distance:
            # Check if conflict is on TP side (opposing direction)
            conflict_side = conflict.get("conflict_side")
            entry_side_or_tp_side = conflict.get("entry_side_or_tp_side")
            
            # If the data explicitly tells us it's on TP side, use that
            if entry_side_or_tp_side == "tp_side":
                conflict_is_hard_block = True
                break
            # Otherwise, infer from direction and conflict_side
            if direction == "LONG" and conflict_side == "above_price":
                # Above price for LONG is TP side
                conflict_is_hard_block = True
                break
            if direction == "SHORT" and conflict_side == "below_price":
                # Below price for SHORT is TP side
                conflict_is_hard_block = True
                break
    
    return {
        "symbol": None,  # Will be set at higher level
        "asset_type": None,  # Will be set at higher level
        "direction": res_b.get("direction"),
        "current_price": current_price,
        "d1_bias": res_b.get("d1_bos_data"),
        "d1_swing_high": d1_swing_high,
        "d1_swing_low": d1_swing_low,
        "d1_equilibrium": d1_equilibrium,
        "d1_premium_discount_location": "not_evaluated",
        "conflicting_zone_type": None,  # Will be set from first conflict if needed
        "conflicting_zone_low": None,
        "conflicting_zone_high": None,
        "conflict_side_relative_to_price": None,
        "conflict_side_relative_to_entry": None,
        "conflict_side_relative_to_tp": None,
        "distance_to_conflict": None,
        "atr": atr,
        "distance_to_conflict_atr": None,
        "configured_hard_block_atr_distance": configured_hard_block_atr_distance,
        "h4_bias": res_b.get("macro_swing_sequence"),
        "h1_bias": res_b.get("current_swing_sequence"),
        "conflict_is_hard_block": conflict_is_hard_block,
        "conflicts": conflicts,
        "h4_h1_agreement": {
            "h1_sequence": res_b.get("current_swing_sequence"),
            "h4_sequence": res_b.get("macro_swing_sequence"),
        },
    }


def _engine_b_shadow_simulation(conf_b: dict[str, Any] | None, res_b: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(conf_b, dict) or not isinstance(res_b, dict):
        return {
            "watchlist_entry_pending": False,
            "next_structural_target_valid": False,
            "d1_conflict_watchlist_candidate": False,
            "all_three_watchlist_only_candidate": False,
            "adds_execution": False,
            "added_watchlist_count": 0,
            "added_execution_count": 0,
            "watchlist_reasons": [],
            "execution_reasons": [],
        }
    
    # Check if structure is ready but trigger missing
    structural_clear = res_b.get("structural_verdict") == "CLEAR"
    structure_ok = conf_b.get("structure_ok") is True
    trigger_ok = res_b.get("trigger_ok") is True
    
    # Entry pending only if structure ready but trigger missing
    entry_pending = structural_clear and structure_ok and not trigger_ok
    
    # Count next valid targets only if side and RR valid
    candidates = res_b.get("structural_target_candidates") or []
    direction = res_b.get("direction")
    next_target_valid = False
    for c in candidates:
        correct_side = c.get("correct_side")
        rejected = c.get("rejected_reason")
        # Target must be on correct side and not rejected
        if correct_side and not rejected:
            # Check RR if distance available
            dist = c.get("distance_to_target")
            atr = res_b.get("atr")
            if dist is not None and atr is not None and atr > 0:
                rr = dist / atr
                if rr >= 1.0:  # Minimum RR threshold
                    next_target_valid = True
                    break
            elif dist is None:
                # If no distance, still count as valid if side correct
                next_target_valid = True
                break
    
    # Count D1 conflict watchlist only if distance available
    d1_conflict = bool(res_b.get("d1_pd_array_conflict"))
    d1_conflicts = res_b.get("d1_conflict_metric_details") or []
    d1_has_distance = any(c.get("distance_in_atr") is not None for c in d1_conflicts)
    d1_conflict_watchlist = d1_conflict and d1_has_distance
    
    # Determine if this is a watchlist-only candidate
    all_three_watchlist = entry_pending and next_target_valid and d1_conflict_watchlist
    
    # Build reason breakdown
    watchlist_reasons = []
    if entry_pending:
        watchlist_reasons.append("structure_ready_no_trigger")
    if next_target_valid:
        watchlist_reasons.append("valid_structural_target_available")
    if d1_conflict_watchlist:
        watchlist_reasons.append("d1_conflict_watchlist_candidate")
    
    # Execution additions remain zero unless all conditions pass
    # This is intentional - shadow simulation does not add executions
    adds_execution = False
    
    return {
        "watchlist_entry_pending": entry_pending,
        "next_structural_target_valid": next_target_valid,
        "d1_conflict_watchlist_candidate": d1_conflict_watchlist,
        "all_three_watchlist_only_candidate": all_three_watchlist,
        "adds_execution": adds_execution,
        "added_watchlist_count": 1 if (entry_pending or next_target_valid or d1_conflict_watchlist) else 0,
        "added_execution_count": 0,  # Shadow simulation never adds executions
        "watchlist_reasons": watchlist_reasons,
        "execution_reasons": [] if not adds_execution else ["shadow_simulation_execution"],
    }


def _classify_engine_c(
    signal: dict[str, Any] | None,
    res_b: dict[str, Any] | None,
    conf_b: dict[str, Any] | None,
) -> tuple[str, float, str, str]:
    if not isinstance(signal, dict):
        return "NO_SIGNAL", 0.0, "blocked", "engine_a_missing"
    a_dir = signal.get("direction")
    a_norm = _safe_float(signal.get("scoreNorm"), None)
    if a_norm is None:
        a_norm = _norm(signal.get("confluenceScore"), signal.get("maxScore"))
    a_has = bool(a_norm and a_norm > 0.30 and a_dir in ("LONG", "SHORT"))
    b_score = _safe_float((conf_b or {}).get("score"), 0.0) or 0.0
    b_max = _safe_float((conf_b or {}).get("max_possible"), 5.0) or 5.0
    b_norm = _norm(b_score, b_max)
    b_dir = (res_b or {}).get("direction")
    b_has = bool(
        isinstance(res_b, dict)
        and res_b.get("structural_verdict") == "CLEAR"
        and (conf_b or {}).get("passed") is True
        and b_norm > 0.20
        and b_dir in ("LONG", "SHORT")
    )
    if a_has and b_has and a_dir != b_dir:
        return "CONFLICT", 0.0, "blocked", "engine_direction_conflict"
    if a_has and b_has:
        conviction = round((a_norm * 0.4) + (b_norm * 0.6), 4)
        if conviction >= 0.65:
            return "ALIGNED", conviction, "execute", ""
        if conviction >= 0.50:
            return "ALIGNED", conviction, "reduced_risk", ""
        return "ALIGNED", conviction, "watchlist", "engine_c_conviction_below_execute"
    if a_has:
        conviction = round(a_norm * 0.6, 4)
        state = "watchlist" if conviction >= 0.40 else "blocked"
        return "A_ONLY", conviction, state, "engine_b_missing_or_failed"
    if b_has:
        conviction = round(b_norm * float(CONFIG.get("ENGINE_C_B_ONLY_MULT", 0.65)), 4)
        state = "watchlist" if conviction >= 0.40 else "blocked"
        return "B_ONLY", conviction, state, "engine_a_missing_or_below_floor"
    return "NO_SIGNAL", 0.0, "blocked", "both_engines_missing_or_below_floor"


def _final_scan_result(
    tier: str | None,
    signal: dict[str, Any] | None,
    a_threshold: float,
    b_threshold: float,
    c_type: str,
    c_state: str,
) -> str:
    if not isinstance(signal, dict):
        return "BLOCKED_DATA"
    if c_type == "CONFLICT":
        return "CONFLICT"
    if c_state == "blocked" and c_type in {"A_ONLY", "B_ONLY", "ALIGNED"}:
        return "BLOCKED_RISK"
    if c_type in {"A_ONLY", "B_ONLY", "ALIGNED"} and tier == "trade":
        return c_type
    if c_type in {"A_ONLY", "B_ONLY", "ALIGNED"} and tier == "watchlist":
        return f"{c_type}_WATCHLIST"
    a_score = _safe_float(signal.get("confluenceScore"), 0.0) or 0.0
    b_score = _safe_float((signal.get("_threshold_audit_b_conf") or {}).get("score"), 0.0) or 0.0
    if a_threshold > 0 and a_threshold * 0.85 <= a_score < a_threshold:
        return "A_NEAR_MISS"
    if b_threshold > 0 and b_threshold * 0.85 <= b_score < b_threshold:
        return "B_NEAR_MISS"
    return "NO_SETUP"


def shadow_thresholds(
    a_threshold: float,
    b_threshold: float,
    c_threshold: float = 0.65,
) -> dict[str, dict[str, float]]:
    return {
        "ENGINE_A": {
            "current": round(a_threshold, 6),
            "current_minus_5pct": round(a_threshold * 0.95, 6),
            "current_minus_10pct": round(a_threshold * 0.90, 6),
            "current_minus_15pct": round(a_threshold * 0.85, 6),
        },
        "ENGINE_B": {
            "current": round(b_threshold, 6),
            "current_minus_5pct": round(b_threshold * 0.95, 6),
            "current_minus_10pct": round(b_threshold * 0.90, 6),
            "current_minus_15pct": round(b_threshold * 0.85, 6),
        },
        "ENGINE_C": {
            "current": round(c_threshold, 6),
            "current_minus_5pct": round(c_threshold * 0.95, 6),
            "current_minus_10pct": round(c_threshold * 0.90, 6),
        },
    }


def build_signal_funnel_row(
    pair: dict[str, Any],
    signal: dict[str, Any] | None,
    tier: str | None = None,
    tier_reason: str | None = None,
    skipped_reason: str | None = None,
    style_profile_b: dict[str, Any] | None = None,
    engine_b_threshold: float | None = None,
) -> dict[str, Any]:
    a_threshold = (
        _safe_float((signal or {}).get("scanThresholdEffective"), None)
        or _safe_float((signal or {}).get("scanThreshold"), None)
        or get_score_threshold(pair, is_backtest=False)
    )
    res_b = signal.get("_threshold_audit_b_res") if isinstance(signal, dict) else None
    conf_b = signal.get("_threshold_audit_b_conf") if isinstance(signal, dict) else None
    b_threshold = (
        _safe_float(engine_b_threshold, None)
        or _safe_float((style_profile_b or {}).get("min_score"), 0.0)
        or 0.0
    )
    fd = (signal or {}).get("factorDiagnostics") or {}
    fs = (signal or {}).get("factorScores") or {}
    c_type, c_conv, c_state, c_reason = _classify_engine_c(signal, res_b, conf_b)
    final_result = _final_scan_result(tier, signal, a_threshold, b_threshold, c_type, c_state)
    a_score = _safe_float((signal or {}).get("confluenceScore"), 0.0) or 0.0
    a_max = _safe_float((signal or {}).get("maxScore"), 3.0) or 3.0
    b_score = _safe_float((conf_b or {}).get("score"), 0.0) or 0.0
    b_max = _safe_float((conf_b or {}).get("max_possible"), 5.0) or 5.0
    style_profile_b = style_profile_b or (
        signal.get("_threshold_audit_b_style_profile") if isinstance(signal, dict) else None
    )
    tp_diag = _engine_b_structural_tp_diagnostics(pair, res_b, conf_b, style_profile_b)
    trigger_diag = _engine_b_trigger_diagnostics(res_b, conf_b)
    # Set symbol and asset_type in trigger diagnostics at higher level
    if trigger_diag:
        trigger_diag["symbol"] = pair.get("display") or pair.get("symbol")
        trigger_diag["asset_type"] = pair.get("type")
    d1_diag = _engine_b_d1_conflict_diagnostics(res_b)
    # Set symbol and asset_type in D1 conflict diagnostics at higher level
    if d1_diag:
        d1_diag["symbol"] = pair.get("display") or pair.get("symbol")
        d1_diag["asset_type"] = pair.get("type")
    b_shadow = _engine_b_shadow_simulation(conf_b, res_b)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": pair.get("display") or pair.get("symbol"),
        "asset_type": pair.get("type"),
        "timeframes_used": ["D1", "H4", "H1"],
        "data_freshness_status": (signal or {}).get("dataFreshness") or (signal or {}).get("candleFreshness") or {},
        "engine_a_raw_score": round(a_score, 6),
        "engine_a_max_score": round(a_max, 6),
        "engine_a_normalized_score": _norm(a_score, a_max),
        "engine_a_direction": (signal or {}).get("direction"),
        "engine_a_passed": bool(isinstance(signal, dict) and a_score >= a_threshold and (signal or {}).get("direction") in ("LONG", "SHORT")),
        "engine_a_fail_reasons": _engine_a_fail_reasons(signal, a_threshold, tier),
        "engine_a_adx_value": fs.get("adx_value"),
        "engine_a_adx_gate_result": _adx_gate_result(signal),
        "engine_a_trend_score": fs.get("trend"),
        "engine_a_momentum_score": fs.get("momentum"),
        "engine_a_addon_score": fs.get("addon"),
        "engine_a_session_multiplier": fs.get("session_multiplier"),
        "engine_b_raw_score": round(b_score, 6),
        "engine_b_max_score": round(b_max, 6),
        "engine_b_normalized_score": _norm(b_score, b_max),
        "engine_b_direction": (res_b or {}).get("direction"),
        "engine_b_structural_data_valid": (res_b or {}).get("structural_verdict") == "CLEAR",
        "engine_b_structural_verdict": (res_b or {}).get("structural_verdict"),
        "engine_b_confidence_passed": (conf_b or {}).get("passed") is True,
        "engine_b_checklist_components": _engine_b_components(conf_b),
        "engine_b_structural_tp_diagnostics": tp_diag,
        "engine_b_trigger_diagnostics": trigger_diag,
        "engine_b_d1_conflict_diagnostics": d1_diag,
        "engine_b_shadow_simulation": b_shadow,
        "engine_b_hard_fail_reasons": _engine_b_hard_fail_reasons(conf_b, res_b),
        "engine_b_soft_warnings": _engine_b_soft_warnings(conf_b, res_b),
        "engine_b_diagnostic_notes": _engine_b_diagnostic_notes(conf_b, res_b),
        "engine_b_fail_reasons": _engine_b_fail_reasons(conf_b, res_b),
        "engine_c_consensus_type": c_type,
        "engine_c_final_conviction": c_conv,
        "engine_c_decision_state": c_state,
        "engine_c_block_watchlist_reason": c_reason or tier_reason or skipped_reason,
        "risk_check_allowed": False,
        "risk_check_fail_reasons": ["not_evaluated_threshold_audit_report_only"],
        "final_scan_result": final_result,
        "thresholds": {
            "engine_a": round(a_threshold, 6),
            "engine_b": round(b_threshold, 6),
            "engine_c_execute": 0.65,
            "engine_c_reduced_risk": 0.50,
            "engine_c_watchlist": 0.40,
        },
        "shadow_thresholds": shadow_thresholds(a_threshold, b_threshold, 0.65),
        "scan_tier": tier,
        "scan_tier_reason": tier_reason,
        "skipped_reason": skipped_reason,
        "factor_diagnostics": {
            "directionalScore": fd.get("directionalScore"),
            "nondirectionalScore": fd.get("nondirectionalScore"),
            "trendCoherence": fd.get("trendCoherence"),
        },
    }
    for field in REQUIRED_FIELDS:
        row.setdefault(field, None)
    return row


def write_signal_funnel_rows(rows: list[dict[str, Any]], path: Path | str = AUDIT_LOG_PATH) -> None:
    if not rows:
        return
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with out_path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def percentile(values: list[float], pct: float) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 6)
    k = (len(clean) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(clean) - 1)
    frac = k - lo
    return round(clean[lo] + (clean[hi] - clean[lo]) * frac, 6)


def distribution(values: list[float]) -> dict[str, float | None]:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {k: None for k in ("min", "p10", "p25", "median", "p75", "p90", "max")}
    return {
        "min": round(min(clean), 6),
        "p10": percentile(clean, 0.10),
        "p25": percentile(clean, 0.25),
        "median": percentile(clean, 0.50),
        "p75": percentile(clean, 0.75),
        "p90": percentile(clean, 0.90),
        "max": round(max(clean), 6),
    }


def count_reasons(rows: list[dict[str, Any]], field: str) -> Counter:
    c: Counter = Counter()
    for row in rows:
        for reason in row.get(field) or []:
            c[str(reason)] += 1
    return c
