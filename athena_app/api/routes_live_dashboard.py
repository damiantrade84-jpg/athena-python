"""Live Dashboard API route handlers and registration.

Behavior-neutral extraction from athena.py. Snapshot and diagnostics remain
operator surfaces; paper-execute stays paper-only and never places broker orders.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import jsonify, request

CONFIG: dict = {}
_AUDIT_DB = ""
ALL_PAIRS = []
ACTIVE_PAIRS = []
_live_prices = {}
_live_prices_lock = None
_live_dashboard_scalp_cache = {}
_live_dashboard_scalp_cache_lock = None
fetch_candles = None
scan_candle_limits = None
_get_candle_fetch_meta = None
get_candle_builder = None
_json_safe = lambda value: value
_mt5_connection_health = lambda: {}
_engine_b_cache_get = lambda _key: None
_resolve_pair_from_signal = lambda _sig: None
get_min_confluence_threshold = lambda _pair: 0.0
_last_scan_results_getter = lambda: {}
_binance_ws_getter = lambda: None
_kill_switch_getter = lambda: False
log = logging.getLogger(__name__)


def api_live_feed_diagnostics():
    """Read-only live candle diagnostics. Does not place trades."""
    payload = request.get_json(silent=True) if request.method == "POST" else {}
    payload = payload or {}
    raw_symbols = payload.get("symbols") or request.args.get("symbols")
    if isinstance(raw_symbols, str):
        wanted = {s.strip().upper() for s in raw_symbols.split(",") if s.strip()}
    elif isinstance(raw_symbols, list):
        wanted = {str(s).strip().upper() for s in raw_symbols if str(s).strip()}
    else:
        wanted = set()

    raw_tfs = payload.get("timeframes") or request.args.get("timeframes")
    if isinstance(raw_tfs, str):
        timeframes = [s.strip().upper() for s in raw_tfs.split(",") if s.strip()]
    elif isinstance(raw_tfs, list):
        timeframes = [str(s).strip().upper() for s in raw_tfs if str(s).strip()]
    else:
        timeframes = ["H1", "H4", "D1"]

    pairs = []
    for pair in ACTIVE_PAIRS:
        keys = {
            str(pair.get("display", "")).upper(),
            str(pair.get("symbol", "")).upper(),
        }
        if wanted and not (wanted & keys):
            continue
        pairs.append(pair)

    from athena_app.services.data_freshness import (
        build_live_feed_diagnostic,
        check_live_candle_consistency,
    )
    from athena_app.services.engine_b_market_state import engine_b_live_market_state
    from athena_app.services.market_state import get_tf_market_state, market_state_offset_hours

    def _last_n_candle_opens(ser, n: int = 5) -> list[str]:
        """Open-time strings for the last n candles (MT5 H4 bar grid visibility)."""
        if not ser or not isinstance(ser, list):
            return []
        from datetime import datetime, timezone

        out: list[str] = []
        for c in ser[-n:]:
            if not isinstance(c, dict):
                continue
            t = c.get("time", c.get("datetime"))
            if t is None:
                continue
            if isinstance(t, (int, float)):
                e = int(t / 1000) if t > 1e12 else int(t)
                out.append(
                    datetime.fromtimestamp(e, tz=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            else:
                out.append(str(t))
        return out

    rows = []
    generated_at = datetime.now(timezone.utc).isoformat()
    _off = float(CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 2.0) or 2.0)
    _tnow = time.time()
    limits = scan_candle_limits()
    for pair in pairs:
        for tf in timeframes:
            tf_u = str(tf or "").upper()
            limit = int(limits.get(tf_u, CONFIG.get(f"{tf_u}_CANDLES", 300)) or 300)
            started = time.perf_counter()
            candles = None
            provider_error = None
            try:
                candles = fetch_candles(pair, tf_u, limit)
            except Exception as exc:
                provider_error = str(exc)
            duration_ms = (time.perf_counter() - started) * 1000.0
            meta = _get_candle_fetch_meta(pair, tf_u, limit) or {}
            diag = build_live_feed_diagnostic(
                pair,
                tf_u,
                candles,
                fetch_meta=meta,
                source=meta.get("upstream") or pair.get("source"),
                fetch_duration_ms=duration_ms,
                provider_error=provider_error,
                time_now=_tnow,
            )

            state_a = get_tf_market_state(pair, tf_u, candles=candles or [])
            state_b = engine_b_live_market_state(
                pair,
                tf_u,
                len(candles or []),
                candles=candles or [],
            )
            if pair.get("source") == "mt5" and pair.get("type") == "forex":
                engine_a_input = list(state_a.get("confirmed") or [])
            else:
                engine_a_input = list(state_a.get("confirmed") or [])
                if state_a.get("forming"):
                    engine_a_input.append(state_a["forming"])
            engine_b_input = list(state_b.get("confirmed") or [])
            scanner_input = list(engine_a_input)
            diag["consistency"] = check_live_candle_consistency(
                pair,
                tf_u,
                {
                    "raw_provider": candles or [],
                    "cache": diag,
                    "market_state": state_a,
                    "engine_a": engine_a_input,
                    "engine_b": engine_b_input,
                    "scanner": scanner_input,
                    "compare": scanner_input,
                },
                time_now=_tnow,
            )
            if tf_u == "H4" and pair.get("source") == "mt5":
                diag["mt5H4Diagnostics"] = {
                    "forexH4ResampleOffsetHours": _off,
                    "marketStateOffsetHours": float(
                        market_state_offset_hours(pair, "H4")
                    ),
                    "fetchMetaOffsetHours": meta.get("offsetHours"),
                    "last5BarOpenIso": _last_n_candle_opens(candles or []),
                    "expectedCurrentBucketIso": diag.get("expectedCurrentBucketIso"),
                    "lastBarIso": diag.get("lastBarIso"),
                    "stalenessSeverity": diag.get("stalenessSeverity"),
                    "bucketLag": diag.get("bucketLag"),
                    "resolution": meta.get("resolution"),
                    "upstream": meta.get("upstream"),
                    "cacheBypass": bool(meta.get("cacheBypass") or meta.get("cacheWriteSkipped")),
                }
            rows.append(diag)

            if pair.get("type") == "crypto" and tf_u in {"H1", "H4", "D1"}:
                cb = get_candle_builder()
                ws_candles = None
                if cb is not None:
                    try:
                        ws_candles = cb.get_candles(pair.get("display", ""), tf_u, min(limit, 1001))
                    except Exception as exc:
                        ws_diag = build_live_feed_diagnostic(
                            pair,
                            tf_u,
                            [],
                            source="binance_ws",
                            provider_status="error",
                            provider_error=str(exc),
                        )
                        rows.append(ws_diag)
                        continue
                if ws_candles:
                    rows.append(
                        build_live_feed_diagnostic(
                            pair,
                            tf_u,
                            ws_candles,
                            source="binance_ws",
                            provider_status="ok",
                        )
                    )

    return jsonify(
        _json_safe(
            {
                "success": True,
                "generatedAt": generated_at,
                "count": len(rows),
                "diagnostics": rows,
                "tradesPlaced": 0,
                "forexH4ResampleOffsetHours": float(
                    CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 2.0) or 2.0
                ),
                "mt5H4OffsetNote": (
                    "FOREX_H4_RESAMPLE_OFFSET_HOURS drives MT5 H4 (non-stock) bar grid in "
                    "market_state; US stocks H4 use 3h offset. Prove grid with tools/probe_mt5_h4.py"
                ),
            }
        )
    )

def _ld_empty_engine_a() -> dict:
    return {
        "score": None, "maxScore": None, "threshold": None,
        "direction": None, "passed": False, "decision": None,
        "factorScores": {"trend": None, "momentum": None, "addon": None},
        "trendScore": None, "momentumScore": None, "addonScore": None,
        "adxValue": None, "adxGate": None, "sessionMultiplier": None, "conviction": None,
        "entry": None, "sl": None, "tp": None, "tp1": None, "tp2": None, "rr": None,
        "failReasons": [],
        "freshnessPolicyStatus": None,
    }

def _ld_empty_engine_b() -> dict:
    return {
        "score": None, "maxScore": None, "threshold": None,
        "gateScore": None, "gateMax": None, "totalScore": None, "bonusPoints": None,
        "direction": None, "structuralVerdict": None, "structuralDataValid": False,
        "confidencePassed": False,
        "structure_ok": False, "location_ok": False, "entry_ok": False,
        "room_rr_ok": False, "d1_conflict": None,
        "hardFailReasons": [], "softWarnings": [], "diagnosticNotes": [],
        "noTriggerClassification": None,
        "entry": None, "sl": None, "tp": None, "rr": None,
    }

def _ld_empty_engine_c() -> dict:
    return {
        "decisionState": "NO_SETUP", "consensusType": None,
        "conviction": None, "tier": None, "trade": False, "reason": None,
        "engineAContribution": None, "engineBContribution": None,
        "engineBChecklistPassed": False, "watchlistReason": None, "blockReason": None,
    }

def _ld_empty_engine_d() -> dict:
    return {
        "enabled": True, "gateResult": "DATA_MISSING",
        "grade": None, "score": None, "setupType": None,
        "spread": None, "rr": None, "direction": None,
        "failReasons": [], "softWarnings": [], "diagnosticNotes": [],
        "missingData": ["engine_d_cache_missing"],
        "vp": {"vah": None, "poc": None, "val": None},
        "nearPoc": None, "nearVah": None, "nearVal": None,
        "cvdAvailable": None, "cvdBias": None, "absorptionDetected": None,
    }

def _ld_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v)]
    if isinstance(value, tuple):
        return [str(v) for v in value if v is not None and str(v)]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _ld_first(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _ld_has_valid_levels(levels: dict | None) -> bool:
    if not isinstance(levels, dict):
        return False
    try:
        entry = float(levels.get("entry"))
        sl = float(levels.get("sl"))
        tp = float(levels.get("tp") if levels.get("tp") is not None else levels.get("tp1"))
        rr = float(levels.get("rr"))
    except (TypeError, ValueError):
        return False
    return bool(entry and sl and tp and rr > 0 and entry != sl)

def _ld_final_state(engine_c: dict, engine_d: dict, freshness: dict, levels: dict) -> tuple[str, str | None, str | None]:
    c_state = engine_c.get("decisionState") or "NO_SETUP"
    d_gate = engine_d.get("gateResult") or "DATA_MISSING"
    if freshness.get("gateDecision") != "ALLOW":
        return "BLOCKED", "Freshness gate blocked", freshness.get("blockReason") or freshness.get("consistencyStatus")
    if d_gate == "DATA_MISSING":
        missing = ", ".join(_ld_list(engine_d.get("missingData"))) or "Engine D data missing"
        return "BLOCKED", "Engine D data missing", missing
    if c_state == "BLOCKED" or d_gate == "BLOCKED":
        return "BLOCKED", engine_c.get("blockReason") or "Engine gate blocked", engine_c.get("blockReason") or (engine_d.get("failReasons") or [None])[0]
    if c_state in ("A_ONLY", "B_ONLY", "WATCHLIST") or d_gate == "WATCHLIST":
        return "WATCHLIST", engine_c.get("watchlistReason") or engine_c.get("reason") or "Watchlist only", None
    if c_state == "ALIGNED" and _ld_has_valid_levels(levels):
        return "PAPER CANDIDATE", "Aligned paper candidate", None
    if d_gate == "PASS" and _ld_has_valid_levels(levels):
        return "PAPER CANDIDATE", "Engine D paper candidate", None
    return "NO SETUP", engine_c.get("reason") or "No executable setup", None

def _ld_executable_state(symbol_row: dict, risk_reason: str | None = None) -> dict:
    paper_soak = CONFIG.get("PAPER_SOAK") or {}
    paper_enabled = bool(paper_soak.get("ENABLED", True))
    real_allowed = bool(CONFIG.get("REAL_ORDERS_ALLOWED", False))
    freshness = symbol_row.get("freshness") or {}
    engine_c = symbol_row.get("engineC") or {}
    engine_d = symbol_row.get("engineD") or {}
    levels = symbol_row.get("levels") or {}
    final_state = symbol_row.get("finalState") or "NO SETUP"

    disabled_reason = None
    if not paper_enabled:
        disabled_reason = "PAPER_SOAK.ENABLED_FALSE"
    elif real_allowed:
        disabled_reason = "REAL_ORDERS_ALLOWED_TRUE"
    elif freshness.get("gateDecision") != "ALLOW":
        disabled_reason = freshness.get("blockReason") or "FRESHNESS_BLOCK"
    elif engine_c.get("decisionState") in ("BLOCKED", "WATCHLIST"):
        disabled_reason = "ENGINE_C_" + str(engine_c.get("decisionState"))
    elif engine_d.get("gateResult") == "DATA_MISSING":
        disabled_reason = "ENGINE_D_DATA_MISSING: " + (", ".join(_ld_list(engine_d.get("missingData"))) or "missing_data")
    elif engine_d.get("gateResult") == "BLOCKED":
        disabled_reason = "ENGINE_D_BLOCKED"
    elif final_state in ("BLOCKED", "WATCHLIST", "NO SETUP"):
        disabled_reason = final_state.replace(" ", "_")
    elif not _ld_has_valid_levels(levels):
        disabled_reason = "INVALID_LEVELS"
    elif risk_reason and risk_reason != "OK":
        disabled_reason = "RISK_" + risk_reason

    return {
        "canPaperExecute": disabled_reason is None,
        "canRealExecute": False,
        "disabledReason": disabled_reason,
        "riskStatus": risk_reason or "NOT_CHECKED",
        "freshnessStatus": freshness.get("gateDecision") or "BLOCK",
        "paperMode": paper_enabled,
        "realOrdersAllowed": real_allowed,
    }

def _ld_is_engine_a_v3_signal(sig: dict) -> bool:
    return (
        str(sig.get("engine") or "").upper() == "ENGINE_A_V3"
        and str(sig.get("contractVersion") or "").startswith("3.")
    )


def _ld_build_engine_a_row(sig: dict) -> dict:
    """Extract Engine A fields from a cached scan signal dict (V2 + V3)."""
    if not sig:
        return _ld_empty_engine_a()
    score = sig.get("confluenceScore") or sig.get("score") or 0.0
    max_score = sig.get("maxScore") or 3.0
    direction = sig.get("direction")
    decision = sig.get("decision")
    is_v3 = _ld_is_engine_a_v3_signal(sig)
    threshold = (
        sig.get("confluenceThreshold")
        if is_v3
        else (
            sig.get("threshold")
            or sig.get("liveThreshold")
            or sig.get("scanThresholdEffective")
            or sig.get("scanThreshold")
        )
    )
    # Derive threshold from pair profile if not in signal
    if threshold is None:
        try:
            from scoring import get_min_confluence_threshold
            _pair_lookup = _resolve_pair_from_signal(sig) or {}
            if _pair_lookup:
                threshold = get_min_confluence_threshold(_pair_lookup)
        except Exception:
            pass
    if is_v3:
        passed = (
            str(decision or "").upper() == "TRADE"
            and sig.get("qualified") is True
        )
    elif threshold is not None:
        try:
            passed = float(score or 0) >= float(threshold)
        except (TypeError, ValueError):
            passed = False
    else:
        passed = False
    factor_scores = sig.get("factorScores") or {}
    fail_reasons = []
    _warns = sig.get("warnings") or []
    if isinstance(_warns, list):
        fail_reasons = [str(w) for w in _warns if w]
    return {
        "score": score,
        "maxScore": max_score,
        "threshold": threshold,
        "direction": direction,
        "passed": passed,
        "decision": decision if is_v3 else None,
        "factorScores": {
            "trend": factor_scores.get("trend"),
            "momentum": factor_scores.get("momentum"),
            "addon": factor_scores.get("addon"),
        },
        "trendScore": factor_scores.get("trend"),
        "momentumScore": factor_scores.get("momentum"),
        "addonScore": factor_scores.get("addon"),
        "adxValue": sig.get("adxValue"),
        "adxGate": sig.get("adxGate") or sig.get("adx_gate"),
        "sessionMultiplier": sig.get("sessionMultiplier"),
        "conviction": sig.get("conviction"),
        "entry": sig.get("entry"),
        "sl": sig.get("sl"),
        "tp": sig.get("tp") or sig.get("tp1"),
        "tp1": sig.get("tp1") or sig.get("tp"),
        "tp2": sig.get("tp2"),
        "rr": sig.get("rr"),
        "failReasons": fail_reasons,
        "freshnessPolicyStatus": (sig.get("dataFreshness") or {}).get("policyStatus"),
    }

def _ld_build_engine_b_row(sig_b: dict) -> dict:
    """Extract Engine B fields from a cached naked analysis dict."""
    if not sig_b:
        return _ld_empty_engine_b()
    conf = sig_b.get("confidence") or {}
    checklist = sig_b.get("checklist") or conf.get("checklist") or {}
    structural_verdict = sig_b.get("structural_verdict")
    gate_score = _ld_first(
        conf.get("gate_score"),
        sig_b.get("engine_b_gate_score"),
        sig_b.get("gate_score"),
    )
    gate_max = _ld_first(
        conf.get("gate_max_possible"),
        sig_b.get("engine_b_gate_max"),
        sig_b.get("gate_max_possible"),
    )
    score = _ld_first(
        sig_b.get("confidence_score"),
        conf.get("score"),
        sig_b.get("score"),
    )
    max_score = _ld_first(
        sig_b.get("confidence_max"),
        conf.get("max_possible"),
        conf.get("max_score"),
        sig_b.get("max_score"),
        sig_b.get("max_possible"),
    )
    bonus_points = _ld_first(conf.get("bonus_points"), conf.get("bonusPoints"))
    threshold = _ld_first(
        conf.get("min_score_scaled"),
        sig_b.get("min_score"),
        sig_b.get("min_score_used"),
        sig_b.get("threshold"),
    )
    confidence_passed = bool(
        _ld_first(conf.get("passed"), sig_b.get("passed"), sig_b.get("checklist_passed"))
    )
    canonical_trade_ok = bool(
        _ld_first(
            conf.get("canonical_trade_ok"),
            conf.get("engine_b_canonical_actionable"),
            sig_b.get("canonical_trade_ok"),
            sig_b.get("engine_b_canonical_actionable"),
        )
    )
    if confidence_passed and not canonical_trade_ok:
        confidence_display_label = "SCORE PASSED / GATE FAILED"
    elif confidence_passed:
        confidence_display_label = "CONFIDENCE PASSED"
    else:
        confidence_display_label = "CONFIDENCE FAILED"
    canonical_structure_ok = bool(
        _ld_first(conf.get("canonical_structure_ok"), sig_b.get("canonical_structure_ok"), True)
        if _ld_first(conf.get("canonical_structure_ok"), sig_b.get("canonical_structure_ok")) is not None
        else bool(checklist.get("structure_ok") or sig_b.get("structure_ok"))
    )
    canonical_location_ok = bool(
        _ld_first(conf.get("canonical_location_ok"), sig_b.get("canonical_location_ok"))
        if _ld_first(conf.get("canonical_location_ok"), sig_b.get("canonical_location_ok")) is not None
        else bool(checklist.get("location_ok") or sig_b.get("location_ok"))
    )
    canonical_trigger_ok = bool(
        _ld_first(conf.get("canonical_trigger_ok"), sig_b.get("canonical_trigger_ok"))
        if _ld_first(conf.get("canonical_trigger_ok"), sig_b.get("canonical_trigger_ok")) is not None
        else bool(
            checklist.get("entry_ok")
            or checklist.get("trigger_ok")
            or sig_b.get("entry_ok")
        )
    )
    canonical_room_ok = bool(
        _ld_first(conf.get("canonical_room_ok"), sig_b.get("canonical_room_ok"))
        if _ld_first(conf.get("canonical_room_ok"), sig_b.get("canonical_room_ok")) is not None
        else bool(
            checklist.get("room_rr_ok") or checklist.get("room_ok") or sig_b.get("room_rr_ok")
        )
    )
    canonical_rr_ok = bool(
        _ld_first(conf.get("canonical_rr_ok"), sig_b.get("canonical_rr_ok"), True)
        if _ld_first(conf.get("canonical_rr_ok"), sig_b.get("canonical_rr_ok")) is not None
        else True
    )
    structural_tp_too_close = bool(
        _ld_first(conf.get("structural_tp_too_close"), sig_b.get("structural_tp_too_close"))
    )
    canonical_room_rr_ok = bool(
        _ld_first(conf.get("canonical_room_rr_ok"), sig_b.get("canonical_room_rr_ok"))
        if _ld_first(conf.get("canonical_room_rr_ok"), sig_b.get("canonical_room_rr_ok")) is not None
        else (canonical_room_ok and canonical_rr_ok and not structural_tp_too_close)
    )
    _sdv = sig_b.get("structural_data_valid")
    if _sdv is not None:
        structural_data_valid = bool(_sdv)
    else:
        structural_data_valid = structural_verdict == "CLEAR"
    source_timeframes = {
        "zone_tf": _ld_first(sig_b.get("zone_tf"), sig_b.get("structure_tf")),
        "entry_tf": sig_b.get("entry_tf"),
        "trigger_tf": _ld_first(sig_b.get("trigger_tf"), sig_b.get("entry_tf")),
        "atr_tf": sig_b.get("atr_tf"),
    }
    return {
        "score": score,
        "maxScore": max_score,
        "threshold": threshold,
        "gateScore": gate_score,
        "gateMax": gate_max,
        "totalScore": score,
        "bonusPoints": bonus_points,
        "direction": sig_b.get("direction"),
        "structuralVerdict": structural_verdict,
        "structuralDataValid": structural_data_valid,
        "confidencePassed": confidence_passed,
        "confidenceDisplayLabel": confidence_display_label,
        "structure_ok": canonical_structure_ok,
        "location_ok": canonical_location_ok,
        "entry_ok": canonical_trigger_ok,
        "room_rr_ok": canonical_room_rr_ok,
        "canonicalStatus": _ld_first(
            conf.get("canonical_status"),
            conf.get("engine_b_canonical_status"),
            sig_b.get("canonical_status"),
            sig_b.get("engine_b_canonical_status"),
        ),
        "canonicalTradeOk": canonical_trade_ok,
        "canonicalStructureOk": canonical_structure_ok,
        "canonicalLocationOk": canonical_location_ok,
        "canonicalTriggerOk": canonical_trigger_ok,
        "canonicalRoomOk": canonical_room_ok,
        "canonicalRrOk": canonical_rr_ok,
        "canonicalRoomRrOk": canonical_room_rr_ok,
        "canonicalPrimaryRejectReason": _ld_first(
            conf.get("canonical_primary_reject_reason"),
            sig_b.get("canonical_primary_reject_reason"),
        ),
        "canonicalSecondaryRejectReasons": _ld_list(
            conf.get("canonical_secondary_reject_reasons")
            or sig_b.get("canonical_secondary_reject_reasons")
        ),
        "suggestedLevelsExecutable": bool(
            _ld_first(
                conf.get("suggested_levels_executable"),
                sig_b.get("suggested_levels_executable"),
                canonical_trade_ok,
            )
        ),
        "structuralTpTooClose": structural_tp_too_close,
        "engineBRejectionReasons": _ld_list(
            conf.get("engine_b_rejection_reasons") or sig_b.get("engine_b_rejection_reasons")
        ),
        "d1_conflict": checklist.get("d1_conflict") if "d1_conflict" in checklist else sig_b.get("d1_conflict"),
        "hardFailReasons": _ld_list(conf.get("hard_fail_reasons") or
                                    sig_b.get("hard_fail_reasons")),
        "softWarnings": _ld_list(conf.get("soft_warnings") or
                                 sig_b.get("soft_warnings")),
        "diagnosticNotes": _ld_list(conf.get("diagnostic_notes") or
                                    sig_b.get("diagnostic_notes")),
        "noTriggerClassification": sig_b.get("no_trigger_classification") or sig_b.get("no_trigger"),
        "entry": _ld_first(
            sig_b.get("entry"),
            sig_b.get("current_price"),
            sig_b.get("price"),
        ),
        "sl": _ld_first(
            sig_b.get("sl"),
            sig_b.get("final_stop_loss"),
            sig_b.get("execution_sl"),
            sig_b.get("recommended_stop_loss"),
            sig_b.get("stop_loss"),
        ),
        "tp": _ld_first(
            sig_b.get("tp"),
            sig_b.get("final_take_profit"),
            sig_b.get("execution_tp"),
            sig_b.get("recommended_take_profit"),
            sig_b.get("take_profit"),
        ),
        "rr": _ld_first(
            sig_b.get("rr"),
            sig_b.get("rr_used_for_gate"),
            sig_b.get("execution_rr"),
        ),
        "nearestSupportZone": (
            dict(sig_b.get("nearest_support_zone"))
            if isinstance(sig_b.get("nearest_support_zone"), dict)
            else None
        ),
        "nearestResistanceZone": (
            dict(sig_b.get("nearest_resistance_zone"))
            if isinstance(sig_b.get("nearest_resistance_zone"), dict)
            else None
        ),
        "sourceTimeframes": source_timeframes,
        "engineBStyle": sig_b.get("style"),
    }

def _ld_derive_engine_c_state(a_row: dict, b_row: dict) -> dict:
    """Derive Engine C decision state from Engine A/B rows (no scoring change)."""
    a_passed = bool(a_row.get("passed"))
    b_passed = bool(b_row.get("confidencePassed"))
    a_dir = a_row.get("direction")
    b_dir = b_row.get("direction")

    if not a_passed and not b_passed:
        state = "NO_SETUP"
    elif a_passed and b_passed:
        dirs_conflict = bool(a_dir and b_dir and a_dir != b_dir)
        state = "CONFLICT" if dirs_conflict else "ALIGNED"
    elif a_passed:
        state = "A_ONLY"
    else:
        state = "B_ONLY"

    conviction = a_row.get("conviction")
    try:
        conviction_f = float(conviction or 0.0)
    except (TypeError, ValueError):
        conviction_f = 0.0

    tier = None
    if state == "ALIGNED":
        if conviction_f >= 0.70:
            tier = "HIGH"
        elif conviction_f >= 0.50:
            tier = "MEDIUM"
        elif conviction_f >= 0.35:
            tier = "LOW"
        else:
            tier = "SKIP"
    elif state in ("A_ONLY", "B_ONLY"):
        tier = "WATCHLIST"
    elif state == "CONFLICT":
        tier = "WATCHLIST"

    direction = a_dir or b_dir
    return {
        "decisionState": state,
        "consensusType": ("A+B" if state == "ALIGNED"
                          else ("A" if state == "A_ONLY"
                                else ("B" if state == "B_ONLY" else None))),
        "conviction": conviction_f if state == "ALIGNED" else None,
        "tier": tier,
        "trade": False,  # Dashboard never creates execution permission
        "engineAContribution": a_row.get("score"),
        "engineBContribution": b_row.get("score"),
        "engineBChecklistPassed": bool(b_row.get("confidencePassed")),
        "watchlistReason": ("single_engine_only" if state in ("A_ONLY", "B_ONLY")
                            else ("engine_conflict" if state == "CONFLICT" else None)),
        "blockReason": None,
        "reason": (f"{direction} - {state}" if state != "NO_SETUP" else "no engine setup"),
    }

def _ld_build_engine_d_row(scalp_cached: dict, pair: dict) -> dict:
    """Build Engine D row from cached scalp result dict."""
    if not scalp_cached:
        return _ld_empty_engine_d()

    ts = scalp_cached.get("_ts")
    engine_d_ttl = float((CONFIG.get("LIVE_DASHBOARD") or {}).get("ENGINE_D_UPDATE_SEC", 300))
    is_stale = ts is None or (time.time() - ts) > max(engine_d_ttl, 60.0)

    asset_type = pair.get("type") or ""
    limited_data = asset_type not in ("crypto",)
    soft_warnings = ["DATA_LIMITED_NON_CRYPTO"] if limited_data else []
    soft_warnings.extend(_ld_list(scalp_cached.get("soft_warnings")))

    if is_stale:
        return {**_ld_empty_engine_d(), "softWarnings": soft_warnings,
                "missingData": ["engine_d_cache_stale_or_missing"]}

    if scalp_cached.get("_skipped"):
        reason_raw = scalp_cached.get("reason") or "BLOCKED"
        fail_reasons = [reason_raw] if isinstance(reason_raw, str) else list(reason_raw or [])
        gate_result = scalp_cached.get("gateResult") or scalp_cached.get("gate_result")
        if not gate_result:
            gate_result = "NO_SETUP" if str(reason_raw).startswith("no_setup:") else "BLOCKED"
        return {
            "enabled": True, "gateResult": gate_result,
            "grade": None, "score": None, "setupType": None,
            "spread": scalp_cached.get("spread"), "rr": scalp_cached.get("rr1") or scalp_cached.get("rr"),
            "direction": scalp_cached.get("direction"),
            "failReasons": fail_reasons, "softWarnings": soft_warnings,
            "diagnosticNotes": _ld_list(scalp_cached.get("diagnostic_notes")),
            "missingData": _ld_list(scalp_cached.get("missing_data")),
            "vp": {"vah": None, "poc": None, "val": None},
            "nearPoc": None, "nearVah": None, "nearVal": None,
            "cvdAvailable": False, "cvdBias": None, "absorptionDetected": False,
        }

    grade = scalp_cached.get("ai_grade")
    score = scalp_cached.get("ai_score")
    explicit_gate = scalp_cached.get("gate_result") or scalp_cached.get("gateResult")
    if explicit_gate:
        gate_result = explicit_gate
    elif grade in ("A", "B"):
        gate_result = "PASS"
    elif grade == "C":
        gate_result = "WATCHLIST"
    elif grade == "D":
        gate_result = "BLOCKED"
    else:
        gate_result = "NO_SETUP"

    return {
        "enabled": True,
        "gateResult": gate_result,
        "grade": grade,
        "score": score,
        "setupType": scalp_cached.get("zone_type") or scalp_cached.get("setup_type"),
        "spread": scalp_cached.get("spread"),
        "rr": scalp_cached.get("rr1") or scalp_cached.get("rr"),
        "direction": scalp_cached.get("direction"),
        "entry": scalp_cached.get("price") or scalp_cached.get("entry"),
        "sl": scalp_cached.get("sl"),
        "tp": scalp_cached.get("tp1") or scalp_cached.get("tp"),
        "tp1": scalp_cached.get("tp1") or scalp_cached.get("tp"),
        "tp2": scalp_cached.get("tp2"),
        "failReasons": _ld_list(scalp_cached.get("fail_reasons") or scalp_cached.get("ai_reasons")),
        "softWarnings": soft_warnings,
        "diagnosticNotes": _ld_list(scalp_cached.get("diagnostic_notes")),
        "missingData": _ld_list(scalp_cached.get("missing_data")),
        "vp": {
            "vah": scalp_cached.get("vp_vah"),
            "poc": scalp_cached.get("vp_poc"),
            "val": scalp_cached.get("vp_val"),
        },
        "nearPoc": bool(scalp_cached.get("near_poc")) if scalp_cached.get("near_poc") is not None else None,
        "nearVah": bool(scalp_cached.get("near_vah")) if scalp_cached.get("near_vah") is not None else None,
        "nearVal": bool(scalp_cached.get("near_val")) if scalp_cached.get("near_val") is not None else None,
        "cvdAvailable": scalp_cached.get("cvd_direction") is not None,
        "cvdBias": scalp_cached.get("cvd_direction"),
        "absorptionDetected": bool((scalp_cached.get("absorption_count") or 0) > 0),
    }

def _ld_freshness_row(pair: dict, tf: str, candles: list | None, provider_error: str | None) -> dict:
    """Build a policy-aware freshness summary row for the live dashboard."""
    if provider_error or not candles:
        return {
            "consistencyStatus": "PROVIDER_ERROR" if provider_error else "DATA_MISSING",
            "policyStatus": None,
            "gateDecision": "BLOCK",
            "blockReason": provider_error or "no_candles",
        }
    try:
        from athena_app.services.market_state import candle_freshness_diagnostic
        diag = candle_freshness_diagnostic(pair, tf, candles)
        severity = diag.get("stalenessSeverity") or "missing_current_bucket"

        # Policy mapping: MT5-sourced pairs use CONFIRMED_ONLY - stale_1_bucket is expected
        source = pair.get("source") or ""
        ptype = pair.get("type") or ""
        confirmed_only = source == "mt5" or ptype in ("forex", "commodity", "index", "stock")

        if severity == "fresh":
            consistency = "OK"
            policy_status = "POLICY_OK"
            gate = "ALLOW"
            block_reason = None
        elif severity == "stale_1_bucket" and confirmed_only:
            consistency = "CONFIRMED_ONLY_OK"
            policy_status = "POLICY_OK"
            gate = "ALLOW"
            block_reason = None
        elif severity == "stale_1_bucket":
            consistency = "TRUE_STALE_1_BUCKET"
            policy_status = "POLICY_LAG"
            gate = "BLOCK"
            block_reason = "stale_1_bucket"
        elif severity == "stale_multi_bucket":
            consistency = "TRUE_STALE_MULTI_BUCKET"
            policy_status = "POLICY_LAG"
            gate = "BLOCK"
            block_reason = "stale_multi_bucket"
        else:
            consistency = "PROVIDER_STALE"
            policy_status = None
            gate = "BLOCK"
            block_reason = severity

        return {
            "consistencyStatus": consistency,
            "policyStatus": policy_status,
            "gateDecision": gate,
            "blockReason": block_reason,
            "bucketLag": diag.get("bucketLag"),
            "lastBarIso": diag.get("lastBarIso"),
            "candleConsistency": diag,
        }
    except Exception as exc:
        return {
            "consistencyStatus": "PROVIDER_ERROR",
            "policyStatus": None,
            "gateDecision": "BLOCK",
            "blockReason": str(exc)[:120],
        }

def _ld_paper_position(display: str) -> dict:
    """Look up the latest open paper/demo position for a symbol from audit_log."""
    empty = {"hasOpenPaperPosition": False, "entry": None, "sl": None, "tp": None, "pnl": None}
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=5.0) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT entry_price, sl, tp, direction FROM audit_log "
                "WHERE pair=? AND grade LIKE 'PAPER%' AND exit_price IS NULL "
                "ORDER BY ts DESC LIMIT 1",
                (display,),
            ).fetchone()
        if row:
            return {
                "hasOpenPaperPosition": True,
                "entry": row["entry_price"],
                "sl": row["sl"],
                "tp": row["tp"],
                "pnl": None,
            }
    except Exception:
        pass
    return empty

def _ld_binance_ws_live() -> bool:
    """Check if the Binance live price WebSocket is running."""
    try:
        bws = _binance_ws_getter()
        if bws is None:
            return False
        if not getattr(bws, "_running", False):
            return False
        t = getattr(bws, "_thread", None)
        return bool(t is not None and t.is_alive())
    except Exception:
        return False

def _ld_open_paper_positions() -> list[dict]:
    rows: list[dict] = []
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=5.0) as con:
            con.row_factory = sqlite3.Row
            for row in con.execute(
                "SELECT pair, entry_price, sl, tp FROM audit_log "
                "WHERE grade LIKE 'LD-PAPER%' AND exit_price IS NULL"
            ).fetchall():
                entry = row["entry_price"]
                sl = row["sl"]
                risk_amount = 0.0
                try:
                    risk_amount = abs(float(entry or 0) - float(sl or 0))
                except (TypeError, ValueError):
                    risk_amount = 0.0
                rows.append({"pair": row["pair"], "entry": entry, "sl": sl, "tp": row["tp"], "risk_amount": risk_amount})
    except Exception:
        return []
    return rows

# --- Session heat (advisory, read-only) --------------------------------------
#
# Historical Engine A / Engine B performance by session bucket, derived from the
# recorded backtest_trades_v3 outcomes. Advisory only: it never gates, sizes,
# scores, or approves anything. The snapshot path never blocks on a build - see
# session_heat.get_session_heat(block=False).


def _ld_session_heat_settings() -> dict:
    from athena_app.services.session_heat import resolve_settings

    return resolve_settings(CONFIG)


def _ld_pair_lookup(symbol: str) -> dict | None:
    """Resolve a backtest symbol to its live pair dict so score groups match Engine A."""
    return _ld_resolve_pair(symbol)


def _ld_score_group(pair: dict | None) -> str | None:
    if not pair:
        return None
    try:
        from engine_a_groups import resolve_score_group_by_type

        override = pair.get("score_group")
        if override:
            return str(override)
        return str(resolve_score_group_by_type(pair) or "") or None
    except Exception:
        return None


def _ld_session_heat_for_row(
    heat: dict | None,
    session_key: str,
    pair: dict | None,
    engine_a_row: dict,
    engine_b_row: dict,
) -> dict:
    """Compact per-card session cue for both engines plus the one to show first."""
    from athena_app.services import session_heat as sh

    if not heat:
        return {"available": False, "reason": "warming", "session": session_key}

    score_group = _ld_score_group(pair)
    a_ind = sh.card_indicator(heat, "A", session_key, score_group=score_group)
    b_ind = sh.card_indicator(heat, "B", session_key, score_group=score_group)

    # Show the engine that actually produced the live setup; Engine B leads when
    # it passed or cleared structure, otherwise Engine A.
    b_active = bool(
        engine_b_row.get("confidencePassed") is True
        or engine_b_row.get("structuralVerdict") == "CLEAR"
    )
    primary_engine = "B" if b_active else "A"
    primary = b_ind if b_active else a_ind
    if primary.get("heat") == sh.HEAT_INSUFFICIENT:
        other = a_ind if b_active else b_ind
        if other.get("heat") != sh.HEAT_INSUFFICIENT:
            primary_engine = "A" if b_active else "B"
            primary = other

    return {
        "available": True,
        "session": session_key,
        "sessionLabel": primary.get("sessionLabel"),
        "scoreGroup": score_group,
        "primaryEngine": primary_engine,
        "primary": primary,
        "engineA": a_ind,
        "engineB": b_ind,
    }


def api_live_dashboard_session_heat():
    """Engine x session historical performance matrix (advisory, read-only).

    Query params:
      engine        A | B | all              (default all)
      lookbackDays  int, 0 = all history     (default from config)
      minSample     int                      (default from config)
      scoreGroup    filter to one score group, plus its engine-wide row
      refresh       1 to force a rebuild instead of serving the cache
    """
    from athena_app.services import session_heat as sh

    cfg_ld = CONFIG.get("LIVE_DASHBOARD") or {}
    if not cfg_ld.get("ENABLED", True):
        return jsonify({"error": "Live Dashboard disabled in config"}), 503

    settings = _ld_session_heat_settings()
    if not settings.get("ENABLED", True):
        return jsonify({
            "enabled": False,
            "status": "DISABLED",
            "reason": "LIVE_DASHBOARD.SESSION_HEAT.ENABLED is false",
            "sessions": sh.session_buckets_contract(),
            "currentSession": sh.current_session(),
            "matrix": [],
        })

    for param, key in (("lookbackDays", "LOOKBACK_DAYS"), ("minSample", "MIN_SAMPLE")):
        raw = request.args.get(param)
        if raw is None:
            continue
        try:
            settings[key] = int(raw)
        except (TypeError, ValueError):
            return jsonify({"error": f"invalid {param}"}), 400
    if settings["MIN_SAMPLE"] < 1:
        settings["MIN_SAMPLE"] = 1

    engine_arg = str(request.args.get("engine") or "all").strip().upper()
    engines = tuple(sh.ENGINES) if engine_arg in ("ALL", "") else (engine_arg,)
    if engine_arg not in ("ALL", "") and engine_arg not in sh.ENGINES:
        return jsonify({"error": f"unknown engine {engine_arg}"}), 400

    force = str(request.args.get("refresh") or "").strip().lower() in ("1", "true", "yes")
    try:
        if force:
            payload = sh.build_session_heat(
                _AUDIT_DB, settings=settings, pair_lookup=_ld_pair_lookup, engines=engines
            )
            cache_meta = {"state": "REBUILT"}
        else:
            payload, cache_meta = sh.get_session_heat(
                _AUDIT_DB, settings=settings, pair_lookup=_ld_pair_lookup, block=True
            )
    except Exception as exc:
        log.error("api_live_dashboard_session_heat: %s", exc)
        return jsonify({"error": str(exc)}), 500

    payload = dict(payload or {})
    matrix = payload.get("matrix") or []
    if engine_arg not in ("ALL", ""):
        matrix = [m for m in matrix if m.get("engine") == engine_arg]
    score_group = request.args.get("scoreGroup")
    if score_group:
        # Keep the engine-wide row alongside the group so the UI can show the
        # group against the population it is being compared to.
        matrix = [m for m in matrix if m.get("scoreGroup") in (None, score_group)]
    payload["matrix"] = matrix
    payload["enabled"] = True
    payload["cache"] = cache_meta
    payload["currentSession"] = sh.current_session()
    payload["requested"] = {
        "engine": engine_arg,
        "scoreGroup": score_group,
        "lookbackDays": settings["LOOKBACK_DAYS"],
        "minSample": settings["MIN_SAMPLE"],
    }
    return jsonify(_json_safe(payload))


def _ld_resolve_pair(symbol: str) -> dict | None:
    sym_upper = str(symbol or "").upper()
    for p in ALL_PAIRS:
        keys = {
            str(p.get("display", "")).upper(),
            str(p.get("symbol", "")).upper(),
            str(p.get("pair", "")).upper(),
        }
        if sym_upper in keys:
            return p
    return None

def api_live_dashboard_snapshot():
    """Read-only live dashboard snapshot for paper/demo monitoring.

    SAFETY CONTRACT:
    - Never places orders.
    - Never calls broker order APIs.
    - Never mutates account state.
    - PAPER_SOAK.ENABLED and REAL_ORDERS_ALLOWED are always re-read from CONFIG.
    - Returns per-symbol error rows on failure; never 500 for individual symbols.
    """
    cfg_ld = CONFIG.get("LIVE_DASHBOARD") or {}
    if not cfg_ld.get("ENABLED", True):
        return jsonify({"error": "Live Dashboard disabled in config"}), 503

    # Parse requested symbols
    raw_syms = request.args.get("symbols", "")
    tf = str(request.args.get("timeframe") or cfg_ld.get("DEFAULT_TIMEFRAME") or "H4").upper()
    if tf not in ("H1", "H4", "D1"):
        tf = "H4"

    if raw_syms:
        req_symbols = [s.strip() for s in raw_syms.split(",") if s.strip()]
    else:
        req_symbols = list(cfg_ld.get("DEFAULT_SYMBOLS") or [])

    max_syms = max(1, int(cfg_ld.get("MAX_SYMBOLS", 8)))
    truncated = len(req_symbols) > max_syms
    if truncated:
        log.warning("[LIVE-DASH] %d symbols requested; truncating to %d", len(req_symbols), max_syms)
    req_symbols = req_symbols[:max_syms]

    # Safety state - always read from CONFIG
    paper_soak = CONFIG.get("PAPER_SOAK") or {}
    paper_mode = {
        "enabled": bool(paper_soak.get("ENABLED", True)),
        "realOrdersAllowed": bool(CONFIG.get("REAL_ORDERS_ALLOWED", False)),
    }

    # Connection status
    mt5_health = _mt5_connection_health()
    binance_live = _ld_binance_ws_live()
    connections = {
        "mt5": ("connected" if mt5_health.get("connected")
                else ("error" if mt5_health.get("available") else "unknown")),
        "binanceWs": "live" if binance_live else "unknown",
    }

    # Build lookup from last full scan results (Engine A)
    _last_a_by_display: dict = {}
    for _sig in (_last_scan_results_getter().get("signals") or []):
        _disp = str(_sig.get("display") or _sig.get("pair") or "").upper()
        if _disp:
            _last_a_by_display[_disp] = _sig

    now = datetime.now(timezone.utc)

    # Session heat: cached aggregate only. block=False guarantees the snapshot
    # never pays for a rebuild - a cold cache warms in the background and the
    # feature simply reports itself unavailable for this one poll.
    from athena_app.services import session_heat as sh

    _heat_settings = _ld_session_heat_settings()
    _heat_enabled = bool(_heat_settings.get("ENABLED", True))
    _heat_payload = None
    _heat_cache_meta = {"state": "DISABLED"}
    _current_session = sh.current_session(now)
    if _heat_enabled:
        try:
            _heat_payload, _heat_cache_meta = sh.get_session_heat(
                _AUDIT_DB,
                settings=_heat_settings,
                pair_lookup=_ld_pair_lookup,
                block=False,
            )
        except Exception as exc:  # never let an advisory layer break the snapshot
            log.warning("[LIVE-DASH] session heat unavailable: %s", exc)
            _heat_payload, _heat_cache_meta = None, {"state": "ERROR", "error": str(exc)}
    if _heat_payload is not None and _heat_payload.get("status") not in ("OK", "INSUFFICIENT"):
        _heat_payload = None

    symbol_rows = []
    events = []
    freshness_all_ok = True

    for sym_req in req_symbols:
        sym_upper = sym_req.strip().upper()

        # Resolve pair dict from ALL_PAIRS
        pair = None
        for _p in ALL_PAIRS:
            _keys = {str(_p.get("display", "")).upper(), str(_p.get("symbol", "")).upper()}
            if sym_upper in _keys:
                pair = _p
                break

        if pair is None:
            symbol_rows.append({
                "symbol": sym_req, "asset_type": None, "source": None, "timeframe": tf,
                "error": "symbol_not_found",
                "latest_price": None, "bid": None, "ask": None, "spread": None, "change_pct": None,
                "chart": None,
                "freshness": {"consistencyStatus": "DATA_MISSING", "policyStatus": None,
                              "gateDecision": "BLOCK", "blockReason": "symbol_not_found"},
                "engineA": _ld_empty_engine_a(), "engineB": _ld_empty_engine_b(),
                "engineC": _ld_empty_engine_c(), "engineD": _ld_empty_engine_d(),
                "aiReview": {},
                "paperPosition": {"hasOpenPaperPosition": False, "entry": None, "sl": None, "tp": None, "pnl": None},
                "levels": {},
                "finalState": "BLOCKED",
                "mainReason": "symbol_not_found",
                "blockReason": "symbol_not_found",
                "executableState": {
                    "canPaperExecute": False, "canRealExecute": False,
                    "disabledReason": "symbol_not_found", "riskStatus": "NOT_CHECKED",
                    "freshnessStatus": "BLOCK",
                    "paperMode": bool((CONFIG.get("PAPER_SOAK") or {}).get("ENABLED", True)),
                    "realOrdersAllowed": bool(CONFIG.get("REAL_ORDERS_ALLOWED", False)),
                },
            })
            continue

        display = str(pair.get("display") or pair.get("symbol") or sym_req)

        # Live price
        with _live_prices_lock:
            lp_entry = dict(_live_prices.get(display) or {})
        latest_price = lp_entry.get("price")
        bid = lp_entry.get("bid")
        ask = lp_entry.get("ask")
        spread = None
        if bid is not None and ask is not None:
            try:
                spread = round(float(ask) - float(bid), 6)
            except (TypeError, ValueError):
                pass
        change_pct = lp_entry.get("change_pct")

        # H4 candles for chart (limit 65 to get 60 confirmed bars after forming-bar considerations)
        chart_candles = None
        freshness_result = {"consistencyStatus": "DATA_MISSING", "policyStatus": None,
                            "gateDecision": "BLOCK", "blockReason": "no_candles"}
        provider_error_str = None
        raw_candles = None
        try:
            raw_candles = fetch_candles(pair, tf, 65)
        except Exception as exc:
            provider_error_str = str(exc)[:120]

        freshness_result = _ld_freshness_row(pair, tf, raw_candles, provider_error_str)

        if raw_candles:
            display_candles = raw_candles[-60:] if len(raw_candles) > 60 else raw_candles
            chart_candles = []
            for _c in display_candles:
                if not isinstance(_c, dict):
                    continue
                chart_candles.append({
                    "time": _c.get("time") or _c.get("t") or _c.get("date"),
                    "open": _c.get("open") or _c.get("o"),
                    "high": _c.get("high") or _c.get("h"),
                    "low": _c.get("low") or _c.get("l"),
                    "close": _c.get("close") or _c.get("c"),
                    "volume": _c.get("volume") or _c.get("v"),
                })

        # H4 grid offset for display annotation
        h4_offset_hours: float | None = None
        if tf == "H4":
            src = pair.get("source") or ""
            ptype = pair.get("type") or ""
            if src == "binance":
                h4_offset_hours = 0.0
            elif ptype == "stock":
                h4_offset_hours = 3.0
            else:
                h4_offset_hours = 2.0

        chart_row = None
        if chart_candles:
            latest_iso = chart_candles[-1].get("time") if chart_candles else None
            chart_row = {
                "candles": chart_candles,
                "latestConfirmedCandleIso": latest_iso,
                "latestFormingCandleIso": None,
                "candlePolicy": "CONFIRMED_ONLY",
                "h4GridOffsetHours": h4_offset_hours,
            }

        # Engine A - from last scan cache (no re-run on snapshot)
        sig_a = _last_a_by_display.get(display.upper()) or {}
        engine_a_row = _ld_build_engine_a_row(sig_a)

        # Engine B - from in-process cache
        sig_b = _engine_b_cache_get(display) or _engine_b_cache_get(display.upper()) or {}
        engine_b_row = _ld_build_engine_b_row(sig_b)

        # Engine C - derived (no scoring change, no execution)
        engine_c_row = _ld_derive_engine_c_state(engine_a_row, engine_b_row)

        # Engine D - from scalp cache
        with _live_dashboard_scalp_cache_lock:
            scalp_cached = dict(_live_dashboard_scalp_cache.get(display.upper()) or {})
        engine_d_row = _ld_build_engine_d_row(scalp_cached, pair)

        # Paper position
        paper_row = _ld_paper_position(display)

        # Freshness aggregate
        if freshness_result.get("gateDecision") != "ALLOW":
            freshness_all_ok = False

        # Build event entries for notable state changes
        c_state = engine_c_row.get("decisionState")
        if c_state == "ALIGNED":
            events.append({
                "timestamp": now.isoformat(),
                "symbol": display,
                "severity": "pass",
                "message": f"Engine C ALIGNED [{engine_c_row.get('tier','?')}] - {engine_a_row.get('direction','?')}",
            })
        elif c_state == "WATCHLIST" or c_state in ("A_ONLY", "B_ONLY"):
            events.append({
                "timestamp": now.isoformat(),
                "symbol": display,
                "severity": "watch",
                "message": f"Engine C {c_state} - {engine_a_row.get('direction') or engine_b_row.get('direction') or '?'}",
            })
        if freshness_result.get("gateDecision") == "BLOCK":
            events.append({
                "timestamp": now.isoformat(),
                "symbol": display,
                "severity": "block",
                "message": f"Freshness BLOCK [{display}]: {freshness_result.get('consistencyStatus','?')}",
            })
        d_gate = engine_d_row.get("gateResult")
        if d_gate == "PASS":
            events.append({
                "timestamp": now.isoformat(),
                "symbol": display,
                "severity": "pass",
                "message": f"Engine D PASS grade={engine_d_row.get('grade','?')} - {engine_d_row.get('setupType','?')}",
            })
        elif d_gate == "WATCHLIST":
            events.append({
                "timestamp": now.isoformat(),
                "symbol": display,
                "severity": "watch",
                "message": f"Engine D WATCHLIST (grade C) - {engine_d_row.get('setupType','?')}",
            })

        # Derive levels from best available engine (B > A > D)
        _levels: dict = {}
        if engine_b_row.get("entry") is not None:
            _levels = {"entry": engine_b_row.get("entry"), "sl": engine_b_row.get("sl"),
                       "tp": engine_b_row.get("tp"), "tp1": engine_b_row.get("tp"),
                       "tp2": None, "rr": engine_b_row.get("rr"),
                       "source": "engineB"}
        elif sig_a.get("entry") is not None:
            _levels = {"entry": sig_a.get("entry"), "sl": sig_a.get("sl"),
                       "tp": sig_a.get("tp") or sig_a.get("tp1"),
                       "tp1": sig_a.get("tp1") or sig_a.get("tp"),
                       "tp2": sig_a.get("tp2"), "rr": sig_a.get("rr"),
                       "source": "engineA"}
        elif engine_d_row.get("entry") is not None:
            _levels = {"entry": engine_d_row.get("entry"), "sl": engine_d_row.get("sl"),
                       "tp": engine_d_row.get("tp") or engine_d_row.get("tp1"),
                       "tp1": engine_d_row.get("tp1") or engine_d_row.get("tp"),
                       "tp2": engine_d_row.get("tp2"), "rr": engine_d_row.get("rr"),
                       "source": "engineD"}

        if chart_row is not None:
            chart_row["levels"] = {
                "entry": _levels.get("entry"),
                "sl": _levels.get("sl"),
                "tp1": _levels.get("tp1") or _levels.get("tp"),
                "tp2": _levels.get("tp2"),
                "live": latest_price,
                "vah": (engine_d_row.get("vp") or {}).get("vah"),
                "poc": (engine_d_row.get("vp") or {}).get("poc"),
                "val": (engine_d_row.get("vp") or {}).get("val"),
            }

        final_state, main_reason, block_reason = _ld_final_state(
            engine_c_row, engine_d_row, freshness_result, _levels
        )
        _symbol_state = {
            "freshness": freshness_result,
            "engineC": engine_c_row,
            "engineD": engine_d_row,
            "levels": _levels,
            "finalState": final_state,
        }
        _executable_state_obj = _ld_executable_state(_symbol_state)
        _ai_confidence = None
        _ai_analysis = sig_a.get("aiAnalysis") or sig_a.get("analysis")
        if isinstance(_ai_analysis, dict):
            _ai_confidence = _ai_analysis.get("edgeProbability")  # 0-100 scale
        if _ai_confidence is None:
            _ai_confidence = sig_a.get("conviction")  # 0-1 scale fallback
            if _ai_confidence is not None:
                _ai_confidence = round(float(_ai_confidence) * 100)
        _ai_review = {
            "marcusReid": _ai_analysis,
            "engineBAI": (sig_b or {}).get("ai_review"),
            "signalDebate": sig_a.get("signalDebate") or sig_a.get("debate"),
            "chartVision": sig_a.get("chartVision") or sig_a.get("vision"),
            "reviewState": sig_a.get("aiReviewState") or "NOT_VERIFIED",
            "confidence": _ai_confidence,
            "contradictions": _ld_list(sig_a.get("aiContradictions")),
            "missingInformation": _ld_list(sig_a.get("aiMissingInformation")),
            "downgradeOnly": True,
            "affectedExecutionPermission": False,
        }

        # executableState: paper-only - never grants real execution
        _c_state = engine_c_row.get("decisionState", "NO_SETUP")
        _fresh_ok = freshness_result.get("gateDecision") == "ALLOW"
        _executable_state = (
            "PAPER_READY" if (_c_state in ("ALIGNED", "A_ONLY", "B_ONLY") and _fresh_ok)
            else "NOT_READY"
        )

        symbol_rows.append({
            "symbol": display,
            "traceId": sig_a.get("trace_id") or sig_b.get("trace_id") or scalp_cached.get("trace_id"),
            "asset_type": pair.get("type"),
            "source": pair.get("source"),
            "timeframe": tf,
            "latest_price": latest_price,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "change_pct": change_pct,
            "chart": chart_row,
            "freshness": freshness_result,
            "engineA": engine_a_row,
            "engineB": engine_b_row,
            "engineC": engine_c_row,
            "engineD": engine_d_row,
            "aiReview": _ai_review,
            "sessionHeat": (
                _ld_session_heat_for_row(
                    _heat_payload, _current_session["key"], pair, engine_a_row, engine_b_row
                )
                if _heat_enabled
                else {"available": False, "reason": "disabled", "session": _current_session["key"]}
            ),
        "paperPosition": paper_row,
            "levels": _levels,
            "finalState": final_state,
            "mainReason": main_reason,
            "blockReason": block_reason,
            "executableState": _executable_state_obj,
        })

    return jsonify(_json_safe({
        "payloadVersion": "live-dashboard-v3",
        "contract": {
            "engineA": "v2_factor_scoring",
            "engineB": "naked_structure",
            "engineC": "consensus",
            "engineD": "scalp_vp",
            "execution": "paper_only",
        },
        "generated_at": now.isoformat(),
        "paperMode": paper_mode,
        "connections": connections,
        "truncatedSymbols": truncated,
        "maxSymbols": max_syms,
        "freshnessAllOk": freshness_all_ok,
        "symbols": symbol_rows,
        "events": events[-50:],
        "sessionHeat": {
            "enabled": _heat_enabled,
            "available": _heat_payload is not None,
            "status": (_heat_payload or {}).get("status") or _heat_cache_meta.get("state"),
            "cache": _heat_cache_meta,
            "currentSession": _current_session,
            "source": sh.SESSION_HEAT_SOURCE,
            "version": sh.SESSION_HEAT_VERSION,
            "generatedAt": (_heat_payload or {}).get("generatedAt"),
            "settings": (_heat_payload or {}).get("settings"),
            "coverage": (_heat_payload or {}).get("coverage"),
            "sessions": sh.session_buckets_contract(),
            "advisoryOnly": True,
        },
    }))

def api_live_dashboard_paper_execute():
    """Paper-only execution logger for Live Dashboard v2.

    Never calls any broker API. Logs the paper entry to audit_log with
    grade='LD-PAPER-EXECUTE' for soak tracking. Enforces paper mode,
    freshness gate, and kill-switch - identical safety stack as live
    execution except no broker call is made.
    """
    # Safety: abort immediately if real orders are somehow enabled
    if CONFIG.get("REAL_ORDERS_ALLOWED", False):
        return jsonify({"ok": False, "error": "REAL_ORDERS_ALLOWED is true - paper endpoint blocked"}), 403

    paper_cfg = CONFIG.get("PAPER_SOAK") or {}
    if not paper_cfg.get("ENABLED", True):
        return jsonify({"ok": False, "error": "PAPER_SOAK.ENABLED is false - paper mode is off"}), 403

    data = request.get_json(force=True, silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    direction = (data.get("direction") or "").strip().upper()
    entry = data.get("entry")
    sl = data.get("sl")
    tp = data.get("tp")
    rr = data.get("rr")

    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    if direction not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": "direction must be LONG or SHORT"}), 400

    # Kill-switch check
    try:
        from risk_engine import is_kill_switch_active
        if is_kill_switch_active():
            return jsonify({"ok": False, "error": "Kill switch is active"}), 409
    except Exception:
        pass

    # Freshness re-check - find the pair config
    pair_cfg = _ld_resolve_pair(symbol)
    if pair_cfg is None:
        return jsonify({"ok": False, "allowed": False, "error": "symbol_not_found"}), 404

    if pair_cfg is not None:
        try:
            raw_c = fetch_candles(pair_cfg, "H4", 10)
            fresh = _ld_freshness_row(pair_cfg, "H4", raw_c, None)
            if fresh.get("gateDecision") == "BLOCK":
                return jsonify({
                    "ok": False,
                    "error": "Freshness gate BLOCK",
                    "freshnessStatus": fresh.get("consistencyStatus"),
                }), 409
        except Exception:
            return jsonify({"ok": False, "allowed": False, "error": "Freshness validation failed"}), 409

    display = str(pair_cfg.get("display") or pair_cfg.get("symbol") or symbol)
    sig_a = {}
    for _sig in (_last_scan_results_getter().get("signals") or []):
        _disp = str(_sig.get("display") or _sig.get("pair") or "").upper()
        if _disp == display.upper():
            sig_a = _sig
            break
    engine_a_row = _ld_build_engine_a_row(sig_a)
    sig_b = _engine_b_cache_get(display) or _engine_b_cache_get(display.upper()) or {}
    engine_b_row = _ld_build_engine_b_row(sig_b)
    engine_c_row = _ld_derive_engine_c_state(engine_a_row, engine_b_row)
    with _live_dashboard_scalp_cache_lock:
        scalp_cached = dict(_live_dashboard_scalp_cache.get(display.upper()) or {})
    engine_d_row = _ld_build_engine_d_row(scalp_cached, pair_cfg)

    if engine_b_row.get("entry") is not None:
        levels = {"entry": engine_b_row.get("entry"), "sl": engine_b_row.get("sl"),
                  "tp": engine_b_row.get("tp"), "tp1": engine_b_row.get("tp"),
                  "rr": engine_b_row.get("rr"), "source": "engineB"}
    elif engine_a_row.get("entry") is not None:
        levels = {"entry": engine_a_row.get("entry"), "sl": engine_a_row.get("sl"),
                  "tp": engine_a_row.get("tp"), "tp1": engine_a_row.get("tp1"),
                  "tp2": engine_a_row.get("tp2"), "rr": engine_a_row.get("rr"),
                  "source": "engineA"}
    elif engine_d_row.get("entry") is not None:
        levels = {"entry": engine_d_row.get("entry"), "sl": engine_d_row.get("sl"),
                  "tp": engine_d_row.get("tp") or engine_d_row.get("tp1"),
                  "tp1": engine_d_row.get("tp1") or engine_d_row.get("tp"),
                  "tp2": engine_d_row.get("tp2"), "rr": engine_d_row.get("rr"),
                  "source": "engineD"}
    else:
        levels = {"entry": entry, "sl": sl, "tp": tp, "tp1": tp, "rr": rr,
                  "source": "request_fallback"}

    final_state, main_reason, block_reason = _ld_final_state(engine_c_row, engine_d_row, fresh, levels)
    exec_state = _ld_executable_state({
        "freshness": fresh, "engineC": engine_c_row, "engineD": engine_d_row,
        "levels": levels, "finalState": final_state,
    })
    if not exec_state.get("canPaperExecute"):
        return jsonify({
            "ok": False,
            "allowed": False,
            "error": exec_state.get("disabledReason") or "PAPER_EXECUTE_BLOCKED",
            "finalState": final_state,
            "mainReason": main_reason,
            "blockReason": block_reason,
            "executableState": exec_state,
        }), 409

    direction = (
        engine_a_row.get("direction")
        or engine_b_row.get("direction")
        or engine_d_row.get("direction")
        or direction
    ).upper()
    signal_for_risk = {
        "pair": display,
        "type": pair_cfg.get("type"),
        "direction": direction,
        "price": levels.get("entry"),
        "sl": levels.get("sl"),
        "tp1": levels.get("tp1") or levels.get("tp"),
        "tp2": levels.get("tp2"),
        "rr": levels.get("rr"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision_state": engine_c_row.get("decisionState"),
        "tier": engine_c_row.get("tier"),
        "verdict": engine_c_row.get("decisionState"),
        "components": {"b_checklist_passed": bool(engine_b_row.get("confidencePassed"))},
        "engine_b_status": {"checklist_passed": bool(engine_b_row.get("confidencePassed"))},
        "dataFreshness": {"allowed": True, "reason": None},
    }
    try:
        from risk_engine import risk_check

        paper_balance = float(paper_cfg.get("ACCOUNT_BALANCE", 10000.0))
        approval = risk_check(
            signal=signal_for_risk,
            account_balance=paper_balance,
            account_equity=paper_balance,
            open_positions=_ld_open_paper_positions(),
            symbol_info=None,
            kill_switch=_kill_switch_getter(),
            sizing_override=1.0,
        )
    except Exception as exc:
        return jsonify({"ok": False, "allowed": False, "error": f"risk_check failed: {exc}"}), 409

    if not approval.approved:
        return jsonify({
            "ok": False,
            "allowed": False,
            "error": f"Risk engine rejected: {approval.reason}",
            "approval": approval.to_dict(),
            "executableState": _ld_executable_state({
                "freshness": fresh, "engineC": engine_c_row, "engineD": engine_d_row,
                "levels": levels, "finalState": final_state,
            }, approval.reason),
        }), 409

    now_ts = datetime.now(timezone.utc).isoformat()
    log_id = None
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            cur = con.execute(
                """INSERT INTO audit_log
                   (ts, pair, direction, grade, style, asset_class, entry_price, sl, tp)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (now_ts, display, direction, "LD-PAPER-EXECUTE", "paper",
                 pair_cfg.get("type") if pair_cfg else None,
                 float(levels.get("entry")) if levels.get("entry") is not None else None,
                 float(levels.get("sl")) if levels.get("sl") is not None else None,
                 float(levels.get("tp")) if levels.get("tp") is not None else None),
            )
            con.commit()
            log_id = cur.lastrowid
    except Exception as exc:
        return jsonify({"ok": False, "error": f"DB write failed: {exc}"}), 500

    return jsonify({
        "ok": True,
        "allowed": True,
        "log_id": log_id,
        "symbol": display,
        "direction": direction,
        "grade": "LD-PAPER-EXECUTE",
        "approval": approval.to_dict(),
        "timestamp": now_ts,
        "note": "Paper log only - no broker order placed",
    })

def api_shadow_signals():
    """Recent Engine C shadow ledger rows (requires SHADOW_LEDGER_ENABLED + scans)."""
    try:
        lim = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(lim, 200))
    try:
        with sqlite3.connect(_AUDIT_DB, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            q = con.execute(
                "SELECT * FROM shadow_signals ORDER BY id DESC LIMIT ?",
                (lim,),
            ).fetchall()
        return jsonify(_json_safe({"signals": [dict(r) for r in q]}))
    except Exception as e:
        log.error(f"api_shadow_signals: {e}")
        return jsonify({"error": str(e)}), 500


def register_live_dashboard_routes(app, runtime: SimpleNamespace) -> None:
    """Register live dashboard routes using runtime state supplied by athena.py."""
    global CONFIG, _AUDIT_DB, ALL_PAIRS, ACTIVE_PAIRS, log
    global _live_prices, _live_prices_lock, _live_dashboard_scalp_cache
    global _live_dashboard_scalp_cache_lock, fetch_candles, scan_candle_limits
    global _get_candle_fetch_meta, get_candle_builder, _json_safe
    global _mt5_connection_health, _engine_b_cache_get, _resolve_pair_from_signal
    global get_min_confluence_threshold, _last_scan_results_getter
    global _binance_ws_getter, _kill_switch_getter

    CONFIG = runtime.CONFIG
    _AUDIT_DB = runtime.AUDIT_DB
    ALL_PAIRS = runtime.ALL_PAIRS
    ACTIVE_PAIRS = runtime.ACTIVE_PAIRS
    _live_prices = runtime.live_prices
    _live_prices_lock = runtime.live_prices_lock
    _live_dashboard_scalp_cache = runtime.live_dashboard_scalp_cache
    _live_dashboard_scalp_cache_lock = runtime.live_dashboard_scalp_cache_lock
    fetch_candles = runtime.fetch_candles
    scan_candle_limits = runtime.scan_candle_limits
    _get_candle_fetch_meta = runtime.get_candle_fetch_meta
    get_candle_builder = runtime.get_candle_builder
    _json_safe = runtime.json_safe
    _mt5_connection_health = runtime.mt5_connection_health
    _engine_b_cache_get = runtime.engine_b_cache_get
    _resolve_pair_from_signal = runtime.resolve_pair_from_signal
    get_min_confluence_threshold = runtime.get_min_confluence_threshold
    _last_scan_results_getter = runtime.last_scan_results
    _binance_ws_getter = runtime.binance_ws
    _kill_switch_getter = runtime.kill_switch
    log = runtime.log

    app.add_url_rule(
        "/api/live-feed-diagnostics",
        "api_live_feed_diagnostics",
        api_live_feed_diagnostics,
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/api/live-dashboard/snapshot",
        "api_live_dashboard_snapshot",
        api_live_dashboard_snapshot,
    )
    app.add_url_rule(
        "/api/live-dashboard/paper-execute",
        "api_live_dashboard_paper_execute",
        api_live_dashboard_paper_execute,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/live-dashboard/session-heat",
        "api_live_dashboard_session_heat",
        api_live_dashboard_session_heat,
    )
    app.add_url_rule(
        "/api/shadow-signals",
        "api_shadow_signals",
        api_shadow_signals,
    )
