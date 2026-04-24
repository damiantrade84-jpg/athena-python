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
    "engine_b_structural_verdict",
    "engine_b_confidence_passed",
    "engine_b_checklist_components",
    "engine_b_fail_reasons",
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


def _engine_b_fail_reasons(conf_b: dict[str, Any] | None, res_b: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    if not isinstance(res_b, dict):
        return ["engine_b_not_evaluated"]
    if res_b.get("structural_verdict") != "CLEAR":
        reasons.append("engine_b_structural_verdict_not_clear")
    if not isinstance(conf_b, dict):
        return sorted(set(reasons + ["engine_b_confidence_not_evaluated"]))
    for key in ("structure_ok", "location_ok", "entry_ok", "room_ok", "rr_ok", "macro_ok"):
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
    a_threshold = get_score_threshold(pair, is_backtest=False)
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
        "engine_b_structural_verdict": (res_b or {}).get("structural_verdict"),
        "engine_b_confidence_passed": (conf_b or {}).get("passed") is True,
        "engine_b_checklist_components": _engine_b_components(conf_b),
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
