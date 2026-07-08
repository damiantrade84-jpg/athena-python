"""Engine B canonical actionability reconciliation (diagnostic-first, config-gated).

Computes a single authoritative actionability state from structural gates, trigger,
RR quality, learning context, and legacy field conflicts. Does not change scoring
thresholds or execution paths unless ENFORCE_ROUTING is enabled in config.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Any

from market_structure import (
    ENGINE_B_REASON_NO_TRIGGER_PATTERN,
    ENGINE_B_REASON_RESISTANCE_TOO_CLOSE,
    ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE,
    ENGINE_B_REASON_SUPPORT_TOO_CLOSE,
)

STATUS_ACTIONABLE = "ACTIONABLE"
STATUS_WARNING_ONLY = "WARNING_ONLY"
STATUS_REJECT_NO_TRIGGER = "REJECT_NO_TRIGGER"
STATUS_REJECT_NO_ROOM = "REJECT_NO_ROOM"
STATUS_REJECT_STRUCTURAL_TP = "REJECT_STRUCTURAL_TP"
STATUS_REJECT_ENTRY_LOCATION = "REJECT_ENTRY_LOCATION"
STATUS_REJECT_RR_QUALITY = "REJECT_RR_QUALITY"
STATUS_REJECT_DATA = "REJECT_DATA"
STATUS_REJECT_CONFLICT = "REJECT_CONFLICT"
STATUS_REJECT_LEARNING = "REJECT_LEARNING_CONTEXT"
STATUS_DEBUG_ONLY = "DEBUG_ONLY"

STRUCTURE_PASS = "STRUCTURE_PASS"
STRUCTURE_WARN = "STRUCTURE_WARN"
STRUCTURE_REJECT = "STRUCTURE_REJECT"

REASON_CONFLICT = "ACTIONABILITY_CONFLICT_ENGINE_B_NO_AI_TRUE"
REASON_PARTIAL_RR = "PARTIAL_TARGET_QUALITY_FAIL"
REASON_LEARNING_NEGATIVE = "LEARNING_CONTEXT_NEGATIVE"

_REJECT_STATUSES = frozenset(
    {
        STATUS_REJECT_NO_TRIGGER,
        STATUS_REJECT_NO_ROOM,
        STATUS_REJECT_STRUCTURAL_TP,
        STATUS_REJECT_ENTRY_LOCATION,
        STATUS_REJECT_RR_QUALITY,
        STATUS_REJECT_DATA,
        STATUS_REJECT_CONFLICT,
        STATUS_REJECT_LEARNING,
        STATUS_DEBUG_ONLY,
    }
)

_STRUCTURE_AUDIT_COLUMNS = [
    "timestamp",
    "pair",
    "direction",
    "entry",
    "SL",
    "TP1",
    "nearest_support_low",
    "nearest_support_high",
    "nearest_resistance_low",
    "nearest_resistance_high",
    "room_ok",
    "support_too_close",
    "resistance_too_close",
    "structural_tp_too_close",
    "entry_inside_support",
    "entry_inside_resistance",
    "tp_inside_support",
    "tp_inside_resistance",
    "structural_status",
    "final_candidate_routing",
    "reached_ui",
    "reached_ai_review",
    "rejection_reason",
]

_RECONCILIATION_AUDIT_COLUMNS = [
    "timestamp",
    "pair",
    "direction",
    "entry",
    "sl",
    "tp1",
    "rr1",
    "rr2",
    "style_min_rr",
    "gate_rr_used",
    "engine_b_actionable_raw",
    "ai_calibration_actionable_raw",
    "engine_b_canonical_actionable",
    "engine_b_canonical_status",
    "trigger_ok",
    "room_ok",
    "support_too_close",
    "resistance_too_close",
    "structural_tp_too_close",
    "no_trigger_pattern",
    "entry_inside_support",
    "entry_inside_resistance",
    "tp1_inside_support",
    "tp1_inside_resistance",
    "candle_freshness_status",
    "pair_learning_wr",
    "pair_learning_avg_r",
    "pair_learning_trade_count",
    "class_learning_wr",
    "class_learning_avg_r",
    "class_learning_trade_count",
    "reached_ui",
    "reached_ai_review",
    "final_routing",
    "rejection_reasons",
]


def _cfg() -> dict[str, Any]:
    try:
        from config import CONFIG

        block = CONFIG.get("ENGINE_B_CANONICAL_ACTIONABILITY") or {}
        if isinstance(block, dict):
            return block
    except Exception:
        pass
    return {}


def _enabled() -> bool:
    return bool(_cfg().get("ENABLED", True))


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _price_in_zone(low: float | None, high: float | None, price: float | None) -> bool:
    if price is None or low is None or high is None:
        return False
    lo, hi = (low, high) if low <= high else (high, low)
    return lo <= price <= hi


def _zone_bounds(zone: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(zone, dict):
        return None, None
    lower = _float(zone.get("lower"))
    upper = _float(zone.get("upper"))
    if lower is None and upper is not None:
        lower = upper
    if upper is None and lower is not None:
        upper = lower
    return lower, upper


def _reason_codes(conf: dict[str, Any]) -> list[str]:
    diag = conf.get("engine_b_diagnostics")
    if isinstance(diag, dict):
        codes = diag.get("reason_codes")
        if isinstance(codes, (list, tuple)):
            return [str(c) for c in codes]
    raw = conf.get("reason_codes")
    if isinstance(raw, (list, tuple)):
        return [str(c) for c in raw]
    return []


def _code_present(codes: list[str], token: str) -> bool:
    return token in codes


def _legacy_ai_calibration_actionable(conf: dict[str, Any]) -> bool:
    """Pre-fix AI calibration derivation (audit only): is_actionable then passed."""
    if conf.get("is_actionable") is not None:
        return bool(conf.get("is_actionable"))
    if conf.get("passed") is not None:
        return bool(conf.get("passed"))
    if conf.get("checklist_passed") is not None:
        return bool(conf.get("checklist_passed"))
    return False


def _playbook_actionable_raw(conf: dict[str, Any]) -> bool:
    """Playbook line uses is_actionable only; unset reads as NO."""
    return conf.get("is_actionable") is True


def _actionability_conflict(conf: dict[str, Any]) -> bool:
    """Engine B Actionable=NO (unset/false) while legacy AI path reads True via passed."""
    if _playbook_actionable_raw(conf):
        return False
    inflated = bool(conf.get("passed")) or bool(conf.get("checklist_passed"))
    return inflated and conf.get("is_actionable") is not True


def _style_min_rr(style_profile: dict[str, Any] | None, conf: dict[str, Any]) -> float | None:
    if isinstance(style_profile, dict) and style_profile.get("min_rr") is not None:
        return _float(style_profile.get("min_rr"))
    return _float(conf.get("min_rr")) or _float(conf.get("rr_required"))


def _learning_fields(learning_ctx: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "pair_learning_wr": None,
        "pair_learning_avg_r": None,
        "pair_learning_trade_count": None,
        "class_learning_wr": None,
        "class_learning_avg_r": None,
        "class_learning_trade_count": None,
        "learning_context_negative": False,
    }
    if not isinstance(learning_ctx, dict):
        return out
    pair_s = learning_ctx.get("pair_stats") if isinstance(learning_ctx.get("pair_stats"), dict) else {}
    class_s = (
        learning_ctx.get("asset_type_stats")
        if isinstance(learning_ctx.get("asset_type_stats"), dict)
        else {}
    )
    if pair_s:
        out["pair_learning_wr"] = _float(pair_s.get("win_rate"))
        out["pair_learning_avg_r"] = _float(pair_s.get("avg_r"))
        out["pair_learning_trade_count"] = pair_s.get("total_trades")
    if class_s:
        out["class_learning_wr"] = _float(class_s.get("win_rate"))
        out["class_learning_avg_r"] = _float(class_s.get("avg_r"))
        out["class_learning_trade_count"] = class_s.get("total_trades")

    negative = False
    min_trades = 5
    try:
        from config import CONFIG

        min_trades = int(CONFIG.get("LEARNING_MIN_TRADES", 5))
    except Exception:
        pass
    pair_trades = int(out["pair_learning_trade_count"] or 0)
    class_trades = int(out["class_learning_trade_count"] or 0)
    if pair_trades >= min_trades:
        wr = out["pair_learning_wr"]
        avg_r = out["pair_learning_avg_r"]
        if (wr is not None and wr < 0.35) or (avg_r is not None and avg_r < 0):
            negative = True
    elif class_trades >= min_trades:
        wr = out["class_learning_wr"]
        avg_r = out["class_learning_avg_r"]
        if (wr is not None and wr < 0.35) or (avg_r is not None and avg_r < 0):
            negative = True
    out["learning_context_negative"] = negative
    return out


def _freshness_status(conf: dict[str, Any], res: dict[str, Any], signal: dict[str, Any] | None) -> str:
    for src in (signal or {}, conf, res):
        if not isinstance(src, dict):
            continue
        for key in ("freshness_status", "freshnessStatus", "candle_freshness_status"):
            val = src.get(key)
            if val:
                return str(val)
        cf = src.get("candleFreshness") or src.get("candle_freshness")
        if isinstance(cf, dict):
            stale = [k for k, v in cf.items() if isinstance(v, dict) and str(v.get("status", "")).lower() in {"stale", "missing", "error"}]
            if stale:
                return "stale"
            return "fresh"
    return "unknown"


def _data_stale(freshness: str) -> bool:
    return str(freshness or "").lower() in {"stale", "missing", "error", "failed"}


def _structural_status_alias(status: str) -> str:
    if status == STATUS_ACTIONABLE:
        return STRUCTURE_PASS
    if status == STATUS_WARNING_ONLY:
        return STRUCTURE_WARN
    return STRUCTURE_REJECT


def reconcile_engine_b_actionability(
    *,
    direction: str,
    entry: float | None,
    sl: float | None,
    tp1: float | None,
    conf: dict[str, Any],
    res: dict[str, Any] | None = None,
    style_profile: dict[str, Any] | None = None,
    learning_ctx: dict[str, Any] | None = None,
    signal: dict[str, Any] | None = None,
    candle_freshness_status: str | None = None,
    confluence_score: float | None = None,
    ai_confidence: float | None = None,
) -> dict[str, Any]:
    """Pure reconciliation — score/confidence inputs are ignored for pass upgrades."""
    _ = confluence_score
    _ = ai_confidence

    res = res if isinstance(res, dict) else {}
    conf = conf if isinstance(conf, dict) else {}
    direction = str(direction or conf.get("direction") or res.get("direction") or "").upper()
    codes = _reason_codes(conf)

    sup_lo, sup_hi = _zone_bounds(res.get("nearest_support_zone"))
    res_lo, res_hi = _zone_bounds(res.get("nearest_resistance_zone"))

    entry_f = _float(entry) or _float(res.get("current_price"))
    sl_f = _float(sl) or _float(conf.get("execution_sl"))
    tp1_f = _float(tp1) or _float(conf.get("execution_tp1")) or _float(conf.get("execution_tp"))

    trigger_ok = _bool(conf.get("trigger_ok"))
    room_ok = _bool(conf.get("room_ok"))
    support_too_close = _code_present(codes, ENGINE_B_REASON_SUPPORT_TOO_CLOSE)
    resistance_too_close = _code_present(codes, ENGINE_B_REASON_RESISTANCE_TOO_CLOSE)
    structural_tp_too_close = _code_present(codes, ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE) or bool(
        res.get("tp_structural_limited")
    )
    no_trigger_pattern = _code_present(codes, ENGINE_B_REASON_NO_TRIGGER_PATTERN)

    entry_inside_support = _price_in_zone(sup_lo, sup_hi, entry_f)
    entry_inside_resistance = _price_in_zone(res_lo, res_hi, entry_f)
    tp_inside_support = _price_in_zone(sup_lo, sup_hi, tp1_f)
    tp_inside_resistance = _price_in_zone(res_lo, res_hi, tp1_f)

    rr1 = _float(conf.get("execution_rr1")) or _float(conf.get("rr1")) or _float(conf.get("rr"))
    rr2 = _float(conf.get("execution_rr2")) or _float(conf.get("rr2")) or _float(conf.get("rr_used_for_gate"))
    gate_rr_used = _float(conf.get("rr_used_for_gate")) or rr2
    style_min_rr = _style_min_rr(style_profile, conf)

    playbook_raw = _playbook_actionable_raw(conf)
    ai_raw = _legacy_ai_calibration_actionable(conf)
    ai_pass_fallback_raw = bool(conf.get("passed")) or bool(conf.get("checklist_passed"))

    freshness = candle_freshness_status or _freshness_status(conf, res, signal)
    learning = _learning_fields(learning_ctx)

    reasons: list[str] = []
    primary_status: str | None = None

    def _set(status: str, reason: str | None = None) -> None:
        nonlocal primary_status
        if reason:
            reasons.append(reason)
        if primary_status is None:
            primary_status = status

    # Priority-ordered hard blockers
    if _actionability_conflict(conf):
        _set(STATUS_REJECT_CONFLICT, REASON_CONFLICT)

    if _data_stale(freshness):
        _set(STATUS_REJECT_DATA, "CANDLE_FRESHNESS_STALE")

    if trigger_ok is False or no_trigger_pattern:
        _set(STATUS_REJECT_NO_TRIGGER, "TRIGGER_NOT_OK" if trigger_ok is False else ENGINE_B_REASON_NO_TRIGGER_PATTERN)

    if direction == "SHORT" and entry_inside_support:
        _set(STATUS_REJECT_ENTRY_LOCATION, "SHORT_ENTRY_INSIDE_SUPPORT")
    if direction == "LONG" and entry_inside_resistance:
        _set(STATUS_REJECT_ENTRY_LOCATION, "LONG_ENTRY_INSIDE_RESISTANCE")

    if structural_tp_too_close:
        _set(STATUS_REJECT_STRUCTURAL_TP, ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE)
    if direction == "SHORT" and tp_inside_support:
        _set(STATUS_REJECT_STRUCTURAL_TP, "SHORT_TP1_INSIDE_SUPPORT")
    if direction == "LONG" and tp_inside_resistance:
        _set(STATUS_REJECT_STRUCTURAL_TP, "LONG_TP1_INSIDE_RESISTANCE")

    if room_ok is False:
        _set(STATUS_REJECT_NO_ROOM, "ROOM_OK_FALSE")
    if direction == "SHORT" and support_too_close:
        _set(STATUS_REJECT_NO_ROOM, ENGINE_B_REASON_SUPPORT_TOO_CLOSE)
    if direction == "LONG" and resistance_too_close:
        _set(STATUS_REJECT_NO_ROOM, ENGINE_B_REASON_RESISTANCE_TOO_CLOSE)

    allow_rr1_below = bool(_cfg().get("ALLOW_RR1_BELOW_MIN_IN_RESEARCH", False))
    partial_rr_fail = False
    if style_min_rr is not None and rr1 is not None and rr1 < style_min_rr and not allow_rr1_below:
        partial_rr_fail = True
        _set(STATUS_REJECT_RR_QUALITY, REASON_PARTIAL_RR)
    if partial_rr_fail and rr2 is not None and gate_rr_used is not None and rr2 >= gate_rr_used:
        if REASON_PARTIAL_RR not in reasons:
            reasons.append(REASON_PARTIAL_RR)

    if conf.get("is_actionable") is False:
        _set(STATUS_DEBUG_ONLY, "ENGINE_B_ACTIONABLE_EXPLICIT_NO")

    structural_blockers = primary_status is not None and primary_status not in {
        STATUS_REJECT_LEARNING,
        STATUS_WARNING_ONLY,
        STATUS_ACTIONABLE,
    }
    if learning["learning_context_negative"]:
        reasons.append(REASON_LEARNING_NEGATIVE)
        if not structural_blockers and primary_status is None:
            _set(STATUS_REJECT_LEARNING, REASON_LEARNING_NEGATIVE)

    if primary_status is None:
        primary_status = STATUS_ACTIONABLE

    canonical_actionable = primary_status == STATUS_ACTIONABLE
    structural_status = _structural_status_alias(primary_status)

    cfg = _cfg()
    routing = route_engine_b_candidate(primary_status, enforce=bool(cfg.get("ENFORCE_ROUTING", False)))

    return {
        "engine_b_canonical_actionable": canonical_actionable,
        "engine_b_canonical_status": primary_status,
        "engine_b_rejection_reasons": sorted(set(reasons)),
        "engine_b_actionable_raw": playbook_raw,
        "engine_b_playbook_actionable_raw": playbook_raw,
        "ai_calibration_actionable_raw": ai_raw,
        "ai_calibration_pass_fallback_raw": ai_pass_fallback_raw,
        "is_actionable": canonical_actionable,
        "structural_status": structural_status,
        "structural_rejection_reasons": sorted(set(reasons)),
        "entry_inside_support": entry_inside_support,
        "entry_inside_resistance": entry_inside_resistance,
        "tp_inside_support": tp_inside_support,
        "tp_inside_resistance": tp_inside_resistance,
        "tp1_inside_support": tp_inside_support,
        "tp1_inside_resistance": tp_inside_resistance,
        "support_too_close": support_too_close,
        "resistance_too_close": resistance_too_close,
        "structural_tp_too_close": structural_tp_too_close,
        "no_trigger_pattern": no_trigger_pattern,
        "partial_target_quality_fail": partial_rr_fail,
        "rr1": rr1,
        "rr2": rr2,
        "style_min_rr": style_min_rr,
        "gate_rr_used": gate_rr_used,
        "candle_freshness_status": freshness,
        "learning_context_negative": learning["learning_context_negative"],
        "pair_learning_wr": learning["pair_learning_wr"],
        "pair_learning_avg_r": learning["pair_learning_avg_r"],
        "pair_learning_trade_count": learning["pair_learning_trade_count"],
        "class_learning_wr": learning["class_learning_wr"],
        "class_learning_avg_r": learning["class_learning_avg_r"],
        "class_learning_trade_count": learning["class_learning_trade_count"],
        "final_candidate_routing": routing,
        "final_routing": routing,
    }


def route_engine_b_candidate(status: str, *, enforce: bool = False) -> str:
    if not enforce:
        return "normal" if status == STATUS_ACTIONABLE else "rejected_diagnostics"
    if status == STATUS_ACTIONABLE:
        return "normal"
    if status == STATUS_WARNING_ONLY and bool(_cfg().get("ALLOW_WARN_IN_RESEARCH", False)):
        return "research_warn"
    return "rejected_diagnostics"


def should_block_normal_routing(status: str) -> bool:
    if not bool(_cfg().get("ENFORCE_ROUTING", False)):
        return False
    if status == STATUS_ACTIONABLE:
        return False
    if status == STATUS_WARNING_ONLY and bool(_cfg().get("ALLOW_WARN_IN_RESEARCH", False)):
        return False
    return True


def should_include_in_ai_review(status: str, *, debug_mode: bool = False) -> bool:
    if status == STATUS_ACTIONABLE:
        return True
    if debug_mode:
        return True
    if bool(_cfg().get("INCLUDE_REJECT_IN_AI_REVIEW", False)):
        return True
    return False


def attach_canonical_actionability(
    conf: dict[str, Any],
    res: dict[str, Any] | None,
    *,
    direction: str,
    current_price: float | None = None,
    learning_ctx: dict[str, Any] | None = None,
    style_profile: dict[str, Any] | None = None,
    pair: str | None = None,
    signal: dict[str, Any] | None = None,
    reached_ui: bool | None = None,
    reached_ai_review: bool | None = None,
) -> dict[str, Any]:
    if not _enabled():
        return conf
    entry = current_price
    sl = conf.get("execution_sl")
    tp1 = conf.get("execution_tp1") or conf.get("execution_tp")
    merged = reconcile_engine_b_actionability(
        direction=direction,
        entry=entry,
        sl=sl,
        tp1=tp1,
        conf=conf,
        res=res,
        style_profile=style_profile,
        learning_ctx=learning_ctx,
        signal=signal,
    )
    conf.update(merged)
    _append_audit_rows(
        conf=conf,
        res=res or {},
        pair=pair or (res or {}).get("display") or (res or {}).get("pair"),
        direction=direction,
        entry=entry,
        sl=sl,
        tp1=tp1,
        reached_ui=reached_ui,
        reached_ai_review=reached_ai_review,
    )
    return conf


def _append_audit_rows(
    *,
    conf: dict[str, Any],
    res: dict[str, Any],
    pair: str | None,
    direction: str,
    entry: float | None,
    sl: Any,
    tp1: Any,
    reached_ui: bool | None,
    reached_ai_review: bool | None,
) -> None:
    cfg = _cfg()
    ts = datetime.now(timezone.utc).isoformat()
    sup_lo, sup_hi = _zone_bounds(res.get("nearest_support_zone"))
    res_lo, res_hi = _zone_bounds(res.get("nearest_resistance_zone"))
    reasons = "; ".join(conf.get("engine_b_rejection_reasons") or [])
    routing = conf.get("final_candidate_routing") or conf.get("final_routing") or ""

    structure_row = {
        "timestamp": ts,
        "pair": pair or "",
        "direction": direction,
        "entry": entry,
        "SL": sl,
        "TP1": tp1,
        "nearest_support_low": sup_lo,
        "nearest_support_high": sup_hi,
        "nearest_resistance_low": res_lo,
        "nearest_resistance_high": res_hi,
        "room_ok": conf.get("room_ok"),
        "support_too_close": conf.get("support_too_close"),
        "resistance_too_close": conf.get("resistance_too_close"),
        "structural_tp_too_close": conf.get("structural_tp_too_close"),
        "entry_inside_support": conf.get("entry_inside_support"),
        "entry_inside_resistance": conf.get("entry_inside_resistance"),
        "tp_inside_support": conf.get("tp_inside_support"),
        "tp_inside_resistance": conf.get("tp_inside_resistance"),
        "structural_status": conf.get("structural_status"),
        "final_candidate_routing": routing,
        "reached_ui": reached_ui,
        "reached_ai_review": reached_ai_review,
        "rejection_reason": reasons,
    }
    recon_row = {
        "timestamp": ts,
        "pair": pair or "",
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "rr1": conf.get("rr1"),
        "rr2": conf.get("rr2"),
        "style_min_rr": conf.get("style_min_rr"),
        "gate_rr_used": conf.get("gate_rr_used"),
        "engine_b_actionable_raw": conf.get("engine_b_actionable_raw"),
        "ai_calibration_actionable_raw": conf.get("ai_calibration_actionable_raw"),
        "engine_b_canonical_actionable": conf.get("engine_b_canonical_actionable"),
        "engine_b_canonical_status": conf.get("engine_b_canonical_status"),
        "trigger_ok": conf.get("trigger_ok"),
        "room_ok": conf.get("room_ok"),
        "support_too_close": conf.get("support_too_close"),
        "resistance_too_close": conf.get("resistance_too_close"),
        "structural_tp_too_close": conf.get("structural_tp_too_close"),
        "no_trigger_pattern": conf.get("no_trigger_pattern"),
        "entry_inside_support": conf.get("entry_inside_support"),
        "entry_inside_resistance": conf.get("entry_inside_resistance"),
        "tp1_inside_support": conf.get("tp1_inside_support"),
        "tp1_inside_resistance": conf.get("tp1_inside_resistance"),
        "candle_freshness_status": conf.get("candle_freshness_status"),
        "pair_learning_wr": conf.get("pair_learning_wr"),
        "pair_learning_avg_r": conf.get("pair_learning_avg_r"),
        "pair_learning_trade_count": conf.get("pair_learning_trade_count"),
        "class_learning_wr": conf.get("class_learning_wr"),
        "class_learning_avg_r": conf.get("class_learning_avg_r"),
        "class_learning_trade_count": conf.get("class_learning_trade_count"),
        "reached_ui": reached_ui,
        "reached_ai_review": reached_ai_review,
        "final_routing": routing,
        "rejection_reasons": reasons,
    }
    _append_csv(cfg.get("STRUCTURE_AUDIT_CSV", "reports/engine_b_structure_gate_audit.csv"), _STRUCTURE_AUDIT_COLUMNS, structure_row)
    _append_csv(
        cfg.get("RECONCILIATION_AUDIT_CSV", "reports/engine_b_actionability_reconciliation.csv"),
        _RECONCILIATION_AUDIT_COLUMNS,
        recon_row,
    )


def _append_csv(path: str, columns: list[str], row: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        write_header = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow({k: row.get(k) for k in columns})
    except OSError:
        pass


def apply_to_naked_signal(
    signal: dict[str, Any],
    *,
    learning_ctx: dict[str, Any] | None = None,
    reached_ui: bool | None = None,
    reached_ai_review: bool | None = None,
) -> tuple[dict[str, Any], bool]:
    """Reconcile canonical actionability on a naked scan signal; return (signal, blocked)."""
    if not _enabled() or not isinstance(signal, dict):
        return signal, False
    naked = signal.get("naked_data")
    if not isinstance(naked, dict):
        naked = {}
    conf = dict(naked)
    attach_canonical_actionability(
        conf,
        naked,
        direction=str(signal.get("direction") or conf.get("direction") or ""),
        current_price=_float(signal.get("price")),
        learning_ctx=learning_ctx,
        style_profile={"min_rr": signal.get("min_rr")},
        pair=signal.get("pair") or signal.get("display"),
        signal=signal,
        reached_ui=reached_ui if reached_ui is not None else (not blocked if bool(_cfg().get("ENFORCE_ROUTING", False)) else None),
        reached_ai_review=reached_ai_review,
    )
    signal["naked_data"] = {**naked, **conf}
    signal["is_actionable"] = conf.get("engine_b_canonical_actionable")
    signal["engine_b_canonical_actionable"] = conf.get("engine_b_canonical_actionable")
    signal["engine_b_canonical_status"] = conf.get("engine_b_canonical_status")
    signal["engine_b_rejection_reasons"] = conf.get("engine_b_rejection_reasons")
    blocked = should_block_normal_routing(str(conf.get("engine_b_canonical_status") or ""))
    return signal, blocked
