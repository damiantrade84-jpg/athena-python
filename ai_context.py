"""ai_context.py — Shared AI context builder and style resolver."""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

try:
    from ai_signal_trace import ensure_trace_id
except Exception:  # pragma: no cover - import guard for legacy tooling
    ensure_trace_id = None

def resolve_ai_style(signal: Dict[str, Any], explicit_style: str = "auto") -> str:
    """Resolve trading style based on UI selection, signal content, or asset rules.
    
    Style resolution order:
    1. explicit UI style if not auto
    2. signal["style"] if available and not auto
    3. signal["signalClass"] and signal["entryMode"]
    4. style_levels if available
    5. asset type and RR profile
    6. fallback to raw score/maxScore only if nothing else exists
    """
    if explicit_style and explicit_style != "auto":
        return explicit_style.upper()
        
    sig_style = signal.get("style") or signal.get("tradeStyle") or signal.get("requested_style")
    if sig_style and sig_style != "auto":
        return str(sig_style).upper()
        
    # 3. signalClass and entryMode
    sig_class = str(signal.get("signalClass", "")).upper()
    entry_mode = str(signal.get("entryMode", "")).upper()
    if "SCALP" in sig_class or "SCALP" in entry_mode:
        return "SCALP"
    if "INTRADAY" in sig_class or "INTRADAY" in entry_mode:
        return "INTRADAY"
    if "SWING" in sig_class or "SWING" in entry_mode:
        return "SWING"
        
    # 4. style_levels
    levels = signal.get("style_levels") or {}
    if levels:
        if levels.get("scalp"): return "SCALP"
        if levels.get("intraday"): return "INTRADAY"
        if levels.get("swing"): return "SWING"
        
    # 5. asset type and RR profile
    asset_type = str(signal.get("type") or signal.get("asset_type", "")).lower()
    rr = float(signal.get("rr1") or signal.get("rr", 1.0))
    if asset_type == "crypto":
        if rr < 2.0: return "SCALP"
        return "INTRADAY"
    elif asset_type == "forex":
        if rr >= 3.0: return "SWING"
        return "INTRADAY"
        
    # 6. fallback to raw score/maxScore only if nothing else exists
    confluence_score = float(signal.get("confluenceScore", 0) or 0)
    max_score = float(signal.get("maxScore", 3.0) or 3.0)
    score_pct = (confluence_score / max_score * 100) if max_score > 0 else 0
    if score_pct >= 70:
        return "SWING"
    elif score_pct >= 50:
        return "INTRADAY"
    return "SCALP"

def resolve_ai_review_min_rr(signal: Dict[str, Any], resolved_style: str) -> float:
    """Resolve min RR for AI review context from signal payload or config."""
    direct = signal.get("min_rr")
    if direct is not None:
        try:
            return float(direct)
        except (TypeError, ValueError):
            pass
    style_key = str(resolved_style or "intraday").lower()
    defaults = {"scalp": 1.5, "intraday": 1.5, "swing": 2.0}
    try:
        from config import CONFIG

        by_style = CONFIG.get("AI_REVIEW_MIN_RR_BY_STYLE") or defaults
    except Exception:
        by_style = defaults
    try:
        return float(by_style.get(style_key, by_style.get("intraday", 1.5)))
    except (TypeError, ValueError):
        return float(defaults.get(style_key, 1.5))


def derive_engine_b_score_pct(engine_b: Dict[str, Any] | None) -> float:
    """Return Engine B graded score percent (total score / max headroom).

    gate_pct cannot serve as the headline: a signal is only emitted when every
    mandatory gate passes, so gate_pct saturates at 100 for all emitted
    signals. Prefer the graded total, then provided pct fields, with gate_pct
    only as a last resort.
    """

    def _num(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    data = engine_b if isinstance(engine_b, dict) else {}
    score = _num(data.get("score"))
    max_score = _num(data.get("max_possible")) or _num(data.get("maxScore"))
    provided_pct = _num(data.get("score_pct"))
    if provided_pct is None:
        provided_pct = _num(data.get("pct"))
    if score is not None and max_score and max_score > 0:
        derived = score / max_score * 100.0
        if provided_pct is None or abs(provided_pct - derived) > 1.0:
            return derived
        return provided_pct
    if provided_pct is not None:
        return provided_pct
    gate_pct = _num(data.get("gate_pct"))
    return gate_pct if gate_pct is not None else 0.0


def classify_rr_by_style(asset_type: str, style: str, rr: float) -> str:
    """Classify Risk:Reward ratios based on style rules.
    
    SCALP: RR >= 1.5 acceptable.
    INTRADAY: RR >= 2.0 preferred.
    SWING: RR >= 3.0 preferred.
    """
    style = style.upper()
    if style == "SCALP":
        if rr >= 1.5:
            return "Acceptable"
        return "Poor"
    elif style == "INTRADAY":
        if rr >= 2.0:
            return "Acceptable"
        return "Poor"
    elif style == "SWING":
        if rr >= 3.0:
            return "Acceptable"
        return "Poor"
    return "Acceptable" if rr >= 2.0 else "Poor"

def classify_sl_by_asset_style(asset_type: str, style: str, sl_pct: float, atr: float, entry: float, max_sl_pct: Optional[float] = None) -> str:
    """Classify Stop Loss risk.
    
    - Judge SL by asset type, style, ATR distance, MAX_SL_PCT, and account risk.
    - For crypto alts, SL > 2% is not automatically quarter size.
    - For forex, SL% above normal range can downgrade if ATR/risk confirms it is wide.
    - If SL exceeds configured MAX_SL_PCT, call it execution-blocking or invalid.
    - If SL is inside MAX_SL_PCT but wide for style, call it elevated risk, not automatic reject.
    """
    asset_type = asset_type.lower()
    style = style.upper()
    
    if max_sl_pct is not None and sl_pct > max_sl_pct:
        return "Execution-blocking"
        
    if asset_type == "forex":
        if sl_pct > 1.5:
            return "Wide (Elevated Risk)"
        return "Normal"
    elif asset_type == "crypto":
        if sl_pct > 5.0:
            return "Wide (Elevated Risk)"
        return "Normal"
    else:
        if sl_pct > 2.0:
            return "Wide (Elevated Risk)"
        return "Normal"

def build_ai_calibration_context(signal: Dict[str, Any], engine_source: str, explicit_style: str = "auto") -> Dict[str, Any]:
    """Build a unified dictionary context across all engines for LLM prompts."""
    asset_type = str(signal.get("type") or signal.get("asset_type", "unknown")).lower()
    resolved_style = resolve_ai_style(signal, explicit_style)
    style_min_rr = resolve_ai_review_min_rr(signal, resolved_style)

    # 1. Identity/context
    identity = {
        "pair": signal.get("display") or signal.get("pair", "?"),
        "symbol": signal.get("symbol", "?"),
        "asset_type": asset_type,
        "scoreGroup": signal.get("scoreGroup") or signal.get("score_group"),
        "engine_source": engine_source,
        "requested_style": explicit_style,
        "resolved_ai_style": resolved_style,
        "session": signal.get("session") or signal.get("current_session"),
        "timestamp": signal.get("timestamp"),
        "data_freshness": signal.get("dataFreshness") or signal.get("candleFetchMeta"),
    }
    
    # 2. Engine A metrics
    confluence_score = float(signal.get("confluenceScore", 0) or 0)
    max_score = float(signal.get("maxScore", 3.0) or 3.0)
    raw_score_pct = (confluence_score / max_score * 100) if max_score > 0 else 0
    
    engine_a = {
        "confluenceScore": confluence_score,
        "maxScore": max_score,
        "rawScorePct": raw_score_pct,
        "liveThreshold": signal.get("liveThreshold"),
        "thresholdProgressPct": float(signal.get("thresholdProgressPct") or signal.get("confluencePct") or 0),
        "scoreNorm": signal.get("scoreNorm"),
        "factorScores": signal.get("factorScores") or signal.get("factor_scores"),
        "factorWeights": signal.get("factorWeights") or signal.get("factor_weights"),
        "factorDiagnostics": signal.get("factorDiagnostics") or signal.get("factor_diagnostics"),
        "confidenceDetail": signal.get("confidenceDetail") or signal.get("confidence_detail"),
        "trendState": signal.get("trendState") or signal.get("regimeName") or (signal.get("regime", {}).get("label") if isinstance(signal.get("regime", {}), dict) else signal.get("regime")),
        "warnings": signal.get("warnings", []),
        "addonStatus": (signal.get("factorDiagnostics") or signal.get("factor_diagnostics") or {}).get("addon_status"),
    }
    
    # 3. Engine B metrics
    engine_b_data = signal.get("engine_b") or signal.get("engine_b_overlay") or signal.get("naked_data") or {}
    if not isinstance(engine_b_data, dict):
        engine_b_data = {}

    def _first_not_none(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None
        
    engine_b = {
        "structural_verdict": _first_not_none(engine_b_data.get("structural_verdict"), signal.get("engine_b_verdict")),
        "current_swing_sequence": _first_not_none(engine_b_data.get("current_swing_sequence"), engine_b_data.get("swing_sequence")),
        "macro_swing_sequence": _first_not_none(engine_b_data.get("macro_swing_sequence"), engine_b_data.get("macro_sequence")),
        "structure_score": _first_not_none(engine_b_data.get("structure_score"), engine_b_data.get("score"), signal.get("engine_b_score")),
        "confidence_score": _first_not_none(engine_b_data.get("confidence_score"), engine_b_data.get("confidence"), signal.get("engine_b_score")),
        "max_possible": _first_not_none(engine_b_data.get("max_possible"), signal.get("engine_b_max")),
        "score_pct": _first_not_none(engine_b_data.get("score_pct"), engine_b_data.get("pct")),
        "is_actionable": engine_b_data.get("engine_b_canonical_actionable"),
        "engine_b_canonical_actionable": engine_b_data.get("engine_b_canonical_actionable"),
        "engine_b_canonical_status": engine_b_data.get("engine_b_canonical_status"),
        "engine_b_rejection_reasons": engine_b_data.get("engine_b_rejection_reasons") or [],
        "canonical_structure_ok": engine_b_data.get("canonical_structure_ok"),
        "canonical_location_ok": engine_b_data.get("canonical_location_ok"),
        "canonical_trigger_ok": engine_b_data.get("canonical_trigger_ok"),
        "canonical_room_ok": engine_b_data.get("canonical_room_ok"),
        "canonical_rr_ok": engine_b_data.get("canonical_rr_ok"),
        "canonical_trade_ok": engine_b_data.get("canonical_trade_ok"),
        "canonical_status": engine_b_data.get("canonical_status"),
        "canonical_primary_reject_reason": engine_b_data.get("canonical_primary_reject_reason"),
        "canonical_secondary_reject_reasons": engine_b_data.get("canonical_secondary_reject_reasons") or [],
        "canonical_badge_state": engine_b_data.get("canonical_badge_state"),
        "confidence_passed": engine_b_data.get("confidence_passed"),
        "suggested_levels_executable": engine_b_data.get("suggested_levels_executable"),
        "ai_calibration_actionable_raw": engine_b_data.get("ai_calibration_actionable_raw"),
        "engine_b_actionable_raw": engine_b_data.get("engine_b_actionable_raw"),
        "zone_quality": _first_not_none(engine_b_data.get("zone_quality"), engine_b_data.get("zone_ok")),
        "trigger_quality": _first_not_none(engine_b_data.get("trigger_quality"), engine_b_data.get("trigger_ok")),
        "room_to_resistance": _first_not_none(engine_b_data.get("room_to_resistance"), engine_b_data.get("distance_to_res")),
        "room_to_support": _first_not_none(engine_b_data.get("room_to_support"), engine_b_data.get("distance_to_sup")),
        "recommended_sl": _first_not_none(engine_b_data.get("recommended_sl"), engine_b_data.get("recommended_stop_loss")),
        "recommended_tp": _first_not_none(engine_b_data.get("recommended_tp"), engine_b_data.get("recommended_take_profit")),
        "rr_used_for_gate": _first_not_none(
            engine_b_data.get("rr_used_for_gate"),
            engine_b_data.get("execution_rr"),
            engine_b_data.get("rr"),
        ),
        "room_ok": _first_not_none(engine_b_data.get("room_ok"), engine_b_data.get("room_rr_ok")),
        "d1_adx": engine_b_data.get("d1_adx"),
        "h4_adx": engine_b_data.get("h4_adx"),
        "adx_derived_regime": _first_not_none(
            engine_b_data.get("_adx_derived_regime"),
            engine_b_data.get("adx_derived_regime"),
        ),
        "reason_codes": engine_b_data.get("reason_codes") or engine_b_data.get("engine_b_diagnostics", {}).get("reason_codes", []),
        "independent_direction": engine_b_data.get("independent_direction") or engine_b_data.get("engine_b_independent_direction"),
    }
    
    # 4. Engine C metrics
    engine_c_data = signal.get("engine_c") or {}
    if not isinstance(engine_c_data, dict):
        engine_c_data = {}
        
    engine_c = {
        "verdict": engine_c_data.get("verdict") or signal.get("verdict"),
        "trade": engine_c_data.get("trade") if "trade" in engine_c_data else signal.get("trade"),
        "conviction": engine_c_data.get("conviction") or signal.get("combinedConviction"),
        "tier": engine_c_data.get("tier") or signal.get("tier"),
        "decision_state": engine_c_data.get("decision_state") or signal.get("decision_state"),
        "sizing_override": engine_c_data.get("sizing_override") or signal.get("sizing_override"),
        "components": engine_c_data.get("components") or signal.get("components"),
        "disagreement_diagnosis": engine_c_data.get("disagreement_diagnosis") or signal.get("disagreement_diagnosis"),
        "calibrated_probability": engine_c_data.get("calibrated_probability") or signal.get("calibratedProbability"),
    }
    
    # 5. AI Vision metrics
    vision_data = signal.get("vision") or signal.get("ai_vision") or {}
    if not isinstance(vision_data, dict):
        vision_data = {}
        
    vision = {
        "vision_rating": vision_data.get("vision_rating") or signal.get("vision_rating"),
        "vision_action": vision_data.get("vision_action") or signal.get("vision_action"),
        "vision_applied": vision_data.get("vision_applied") or signal.get("vision_applied"),
        "vision_style_ratings": vision_data.get("vision_style_ratings") or signal.get("vision_style_ratings"),
        "right_edge_status": vision_data.get("right_edge_status") or vision_data.get("structured", {}).get("right_edge_status"),
        "tf_alignment": vision_data.get("tf_alignment") or vision_data.get("structured", {}).get("tf_alignment"),
        "confirms_direction": vision_data.get("confirms_direction") if "confirms_direction" in vision_data else vision_data.get("structured", {}).get("confirms_direction"),
        "level_suggestions": vision_data.get("level_suggestions") or vision_data.get("structured", {}).get("level_suggestions"),
        "vision_sl_flag": vision_data.get("vision_sl_flag") or signal.get("vision_sl_flag"),
        "vision_tp_flag": vision_data.get("vision_tp_flag") or signal.get("vision_tp_flag"),
    }
    
    # 6. Trade/risk metrics
    entry = float(signal.get("price") or 0)
    sl = float(signal.get("sl") or 0)
    sl_pct = (abs(entry - sl) / entry * 100) if entry > 0 and sl > 0 else 0
    atr = float(signal.get("atr") or 0)
    
    trade_risk = {
        "direction": signal.get("direction"),
        "entry": entry,
        "sl": sl,
        "tp1": signal.get("tp1") or signal.get("tp"),
        "tp2": signal.get("tp2"),
        "rr1": signal.get("rr1") or signal.get("rr"),
        "rr2": signal.get("rr2"),
        "style_min_rr": style_min_rr,
        "slPct": sl_pct,
        "atr": atr,
        "slDistance": abs(entry - sl) if entry > 0 and sl > 0 else 0,
        "MAX_SL_PCT": signal.get("MAX_SL_PCT"),
        "position_sizing": signal.get("position_sizing") or signal.get("sizing"),
        "risk_sizing_context": signal.get("risk_sizing_context"),
        "spread_fees_guard": signal.get("spread_fees_guard"),
    }
    
    # 7. Data quality flags
    candle_meta = signal.get("candleFetchMeta") or {}
    data_quality = {
        "forming_bar_included": candle_meta.get("forming_bar_included"),
        "freshness_status": signal.get("freshnessStatus") or signal.get("freshness_status"),
        "data_freshness": signal.get("dataFreshness") or signal.get("data_freshness"),
    }

    # 8. Calibration notes
    calibration_notes = {
        "rawScorePct": "Theoretical score quality.",
        "thresholdProgressPct": "Scanner readiness versus actual live threshold.",
        "combinedConviction": "Execution gate proxy.",
        "AI_instruction": "AI must not treat these as the same metric.",
    }
    
    return {
        "identity": identity,
        "engine_a": engine_a,
        "engine_b": engine_b,
        "engine_c": engine_c,
        "vision": vision,
        "trade_risk": trade_risk,
        "data_quality": data_quality,
        "calibration_notes": calibration_notes,
    }

def build_ai_calibration_context_string(signal: Dict[str, Any], engine_source: str, explicit_style: str = "auto") -> str:
    """Format calibration context as a readable string for Marcus Reid prompts."""
    ctx = build_ai_calibration_context(signal, engine_source, explicit_style)
    identity = ctx["identity"]
    engine_a = ctx["engine_a"]
    engine_b = ctx["engine_b"]
    trade_risk = ctx["trade_risk"]
    
    lines = [
        "=== AI CALIBRATION CONTEXT ===",
        f"Engine source: {identity['engine_source']}",
        f"Asset type: {identity['asset_type'].upper()}",
        f"Score group: {identity['scoreGroup']}",
        f"Requested style: {identity['requested_style']}",
        f"Resolved AI style: {identity['resolved_ai_style']}",
        f"Raw score: {engine_a['confluenceScore']} / {engine_a['maxScore']}",
        f"Raw score pct: {engine_a['rawScorePct']:.1f}%",
        f"Live threshold: {engine_a['liveThreshold']}",
        f"Threshold progress pct: {engine_a['thresholdProgressPct']:.1f}%",
    ]
    
    if signal.get("dashboard_confluence_label"):
        lines.append(f"Dashboard confluence label: {signal['dashboard_confluence_label']}")
    elif engine_a["thresholdProgressPct"] >= 80:
        lines.append("Dashboard confluence label: Strong")
    elif engine_a["thresholdProgressPct"] >= 50:
        lines.append("Dashboard confluence label: Medium")
    else:
        lines.append("Dashboard confluence label: Weak")
        
    lines.append(f"ScoreNorm: {engine_a['scoreNorm']}")
    if ctx["engine_c"].get("conviction") is not None:
        lines.append(f"CombinedConviction: {ctx['engine_c']['conviction']}")
    is_engine_b_source = str(identity.get("engine_source") or "").lower().startswith("engine b") or bool(signal.get("is_naked"))
    if is_engine_b_source:
        score = engine_b.get("structure_score")
        max_possible = engine_b.get("max_possible")
        score_pct = engine_b.get("score_pct")
        try:
            if score_pct is None and score is not None and max_possible:
                score_pct = float(score) / float(max_possible) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            score_pct = None
        lines.append(f"Engine B structural verdict: {engine_b.get('structural_verdict')}")
        lines.append(
            "Engine B swing sequence: "
            f"{engine_b.get('current_swing_sequence')} | Macro: {engine_b.get('macro_swing_sequence')}"
        )
        if score is not None or max_possible is not None:
            pct_text = f"{float(score_pct):.1f}%" if score_pct is not None else "n/a"
            lines.append(f"Engine B score: {score} / {max_possible} ({pct_text})")
        lines.append(
            "Engine B actionable: "
            f"{engine_b.get('is_actionable')} | RR gate: {engine_b.get('rr_used_for_gate')} "
            f"| room_ok: {engine_b.get('room_ok')}"
        )
        if engine_b.get("engine_b_canonical_status"):
            lines.append(f"Engine B canonical status: {engine_b.get('engine_b_canonical_status')}")
        if engine_b.get("canonical_primary_reject_reason"):
            lines.append(
                f"Engine B primary reject: {engine_b.get('canonical_primary_reject_reason')}"
            )
        secondary = engine_b.get("canonical_secondary_reject_reasons") or []
        if secondary:
            lines.append(
                f"Engine B secondary rejects: {', '.join(str(x) for x in secondary)}"
            )
        badge = engine_b.get("canonical_badge_state")
        if isinstance(badge, dict):
            lines.append(
                "Engine B canonical gates: "
                f"structure={badge.get('structure')} "
                f"location={badge.get('location')} "
                f"trigger={badge.get('trigger')} "
                f"room_rr={badge.get('room_rr')} "
                f"confidence={badge.get('confidence')} "
                f"trade={badge.get('trade')}"
            )
        rej = engine_b.get("engine_b_rejection_reasons") or []
        if rej:
            lines.append(f"Engine B rejection reasons: {', '.join(str(x) for x in rej)}")
        if engine_b.get("ai_calibration_actionable_raw") is not None:
            lines.append(
                f"Engine B legacy AI actionable (audit): {engine_b.get('ai_calibration_actionable_raw')}"
            )
        if (
            engine_b.get("d1_adx") is not None
            or engine_b.get("h4_adx") is not None
            or engine_b.get("adx_derived_regime") is not None
        ):
            lines.append(
                "Engine B ADX: "
                f"D1={engine_b.get('d1_adx')} H4={engine_b.get('h4_adx')} "
                f"regime={engine_b.get('adx_derived_regime')}"
            )
        reason_codes = engine_b.get("reason_codes") or []
        if reason_codes:
            if isinstance(reason_codes, (list, tuple)):
                reason_text = ", ".join(str(x) for x in reason_codes)
            else:
                reason_text = str(reason_codes)
            lines.append(f"Engine B reason codes: {reason_text}")
        lines.append(
            "Engine B primary note: Engine A factor diagnostics are optional child context, "
            "not missing Engine B evidence."
        )
    if str(identity.get("engine_source") or "").lower().startswith("engine c") or any(
        ctx["engine_c"].get(k) is not None
        for k in ("decision_state", "tier", "sizing_override", "components")
    ):
        lines.append(f"Engine C decision_state: {ctx['engine_c'].get('decision_state')}")
        lines.append(f"Engine C tier: {ctx['engine_c'].get('tier')}")
        lines.append(f"Engine C sizing_override: {ctx['engine_c'].get('sizing_override')}")
        components = ctx["engine_c"].get("components")
        if isinstance(components, dict):
            lines.append(
                "Engine C components: "
                f"a_norm={components.get('a_norm')} "
                f"b_norm={components.get('b_norm')} "
                f"a_has_signal={components.get('a_has_signal')} "
                f"b_has_signal={components.get('b_has_signal')}"
            )
        
    lines.append(f"Entry: {trade_risk['entry']} | SL: {trade_risk['sl']} | TP1: {trade_risk['tp1']} | TP2: {trade_risk['tp2']}")
    lines.append(f"R:R = 1:{trade_risk['rr1']} / 1:{trade_risk['rr2']}")
    lines.append(f"Style min RR (config): {trade_risk.get('style_min_rr', '?')}")
    lines.append(
        "Note: TP1/RR1 is partial exit target; TP2/RR2 is runner. "
        "Levels are deterministic engine outputs already gated by Python."
    )
    lines.append("Note: thresholdProgressPct is scanner readiness; rawScorePct is theoretical factor quality.")

    dq = ctx.get("data_quality") or {}
    if dq.get("forming_bar_included") is not None:
        lines.append(f"Forming bar included: {dq['forming_bar_included']}")
    if dq.get("freshness_status"):
        lines.append(f"Freshness status: {dq['freshness_status']}")

    return "\n".join(lines)


_MISSING = object()


def _present(value: Any) -> bool:
    return value is not _MISSING and value is not None


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    if value is None or value is _MISSING:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _to_text(value: Any) -> str | None:
    if value is None or value is _MISSING:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("name", "label", "session", "value"):
            text = _to_text(value.get(key))
            if text:
                return text
        return None
    return str(value)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not _MISSING and value is not None:
            return value
    return None


def _pick(source: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return _MISSING


def _pick_nested(signal: Dict[str, Any], nested: Dict[str, Any], *keys: str) -> Any:
    return _first_present(_pick(nested, *keys), _pick(signal, *keys))


def _pick_engine_nested(nested: Dict[str, Any], *keys: str) -> Any:
    return _pick(nested, *keys)


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value is _MISSING or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact_missing(data: Dict[str, Any], required_fields: List[str]) -> List[str]:
    return [
        field
        for field in required_fields
        if data.get(field, _MISSING) is None or data.get(field, _MISSING) is _MISSING
    ]


def _section_complete(data: Dict[str, Any], required_fields: List[str]) -> float:
    if not required_fields:
        return 1.0
    missing = set(data.get("missing_fields") or [])
    complete = sum(1 for field in required_fields if field not in missing)
    return round(complete / len(required_fields), 4)


def _freshness_status(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("allowed") is False:
            reason = value.get("reason") or value.get("status") or value.get("freshness_status") or "blocked"
            return f"allowed_false:{reason}"
        return (
            value.get("status")
            or value.get("freshness_status")
            or value.get("reason")
            or ("fresh" if value.get("allowed") is True else None)
        )
    return value


def _signal_symbol(signal: Dict[str, Any]) -> str:
    return str(
        signal.get("symbol")
        or signal.get("pair")
        or signal.get("display")
        or signal.get("ticker")
        or "unknown"
    )


def _engine_dict(signal: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for key in keys:
        value = signal.get(key)
        if isinstance(value, dict):
            return value
    return {}


def build_engine_d_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Extract canonical Engine D scalp context from live/backtest signal payloads."""
    signal = signal if isinstance(signal, dict) else {}
    engine_d = _engine_dict(signal, "engine_d", "scalp", "engineD")
    fee_guard = _pick_nested(signal, engine_d, "fee_guard", "feeGuard")
    if fee_guard is _MISSING:
        fee_guard = _pick(signal, "spread_fees_guard")

    data = {
        "setup_type": _pick_nested(signal, engine_d, "setup_type", "zone_type", "zoneType"),
        "gate_result": _pick_nested(signal, engine_d, "gate_result", "gateResult"),
        "executable": _pick_nested(signal, engine_d, "executable"),
        "candidate_status": _pick_nested(signal, engine_d, "candidate_status", "candidateStatus"),
        "quality_score": _pick_nested(signal, engine_d, "quality_score", "ai_score", "score"),
        "quality_grade": _pick_nested(signal, engine_d, "quality_grade", "ai_grade", "grade"),
        "quality_reasons": _as_list(_pick_nested(signal, engine_d, "quality_reasons", "ai_reasons", "reasons")),
        "market_state": _pick_nested(signal, engine_d, "market_state", "marketState"),
        "session": _to_text(_pick_nested(signal, engine_d, "session")),
        "execution_tf": _pick_nested(signal, engine_d, "execution_tf", "executionTf"),
        "structure_tf": _pick_nested(signal, engine_d, "structure_tf", "structureTf"),
        "context_tf": _pick_nested(signal, engine_d, "context_tf", "contextTf"),
        "vp_poc": _pick_nested(signal, engine_d, "vp_poc", "poc"),
        "vp_vah": _pick_nested(signal, engine_d, "vp_vah", "vah"),
        "vp_val": _pick_nested(signal, engine_d, "vp_val", "val"),
        "vp_lvn_count": _pick_nested(signal, engine_d, "vp_lvn_count", "lvn_count"),
        "vp_volume_source": _pick_nested(signal, engine_d, "vp_volume_source", "volume_source"),
        "vp_fidelity": _pick_nested(signal, engine_d, "vp_fidelity"),
        "vp_is_proxy": _pick_nested(signal, engine_d, "vp_is_proxy"),
        "vp_uses_real_trade_buckets": _pick_nested(signal, engine_d, "vp_uses_real_trade_buckets"),
        "profile_anchor_mode": _pick_nested(signal, engine_d, "profile_anchor_mode"),
        "profile_anchor_start": _pick_nested(signal, engine_d, "profile_anchor_start"),
        "profile_anchor_end": _pick_nested(signal, engine_d, "profile_anchor_end"),
        "vwap": _pick_nested(signal, engine_d, "vwap"),
        "absorption_count": _pick_nested(signal, engine_d, "absorption_count"),
        "absorption_source": _pick_nested(signal, engine_d, "absorption_source"),
        "absorption_fidelity": _pick_nested(signal, engine_d, "absorption_fidelity"),
        "absorption_is_proxy": _pick_nested(signal, engine_d, "absorption_is_proxy"),
        "cvd_direction": _pick_nested(signal, engine_d, "cvd_direction"),
        "cvd_slope": _pick_nested(signal, engine_d, "cvd_slope"),
        "cvd_source": _pick_nested(signal, engine_d, "cvd_source"),
        "cvd_bucket_count": _pick_nested(signal, engine_d, "cvd_bucket_count"),
        "cvd_fidelity": _pick_nested(signal, engine_d, "cvd_fidelity"),
        "cvd_is_proxy": _pick_nested(signal, engine_d, "cvd_is_proxy"),
        "aaa_complete": _pick_nested(signal, engine_d, "aaa_complete"),
        "aggression_source": _pick_nested(signal, engine_d, "aggression_source"),
        "aggression_confirmed": _pick_nested(signal, engine_d, "aggression_confirmed"),
        "aggression_uses_real_order_flow": _pick_nested(signal, engine_d, "aggression_uses_real_order_flow"),
        "aggression_source_is_proxy": _pick_nested(signal, engine_d, "aggression_source_is_proxy"),
        "rr1": _pick_nested(signal, engine_d, "rr1", "rr"),
        "spread_pips": _pick_nested(signal, engine_d, "spread_pips", "spread"),
        "fee_guard": fee_guard if _present(fee_guard) else None,
        "sl_method": _pick_nested(signal, engine_d, "sl_method"),
        "sl_distance": _pick_nested(signal, engine_d, "sl_distance", "slDistance"),
        "rr_ok": _pick_nested(signal, engine_d, "rr_ok"),
        "strict_fabio_pass": _pick_nested(signal, engine_d, "strict_fabio_pass"),
        "strict_fabio_reason": _pick_nested(signal, engine_d, "strict_fabio_reason"),
        "strict_fabio_missing_pillars": _as_list(_pick_nested(signal, engine_d, "strict_fabio_missing_pillars")),
        "strict_fabio_pillars": _pick_nested(signal, engine_d, "strict_fabio_pillars"),
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    required = [
        "setup_type", "gate_result", "executable", "candidate_status", "quality_score",
        "quality_grade", "market_state", "session", "execution_tf", "vp_poc", "vp_vah",
        "vp_val", "vp_volume_source", "vp_fidelity", "vp_is_proxy",
        "vp_uses_real_trade_buckets", "vwap", "absorption_count", "absorption_source",
        "cvd_direction", "cvd_source", "aaa_complete", "aggression_source",
        "aggression_confirmed", "aggression_uses_real_order_flow", "rr1", "spread_pips",
        "fee_guard", "sl_method", "sl_distance", "rr_ok", "strict_fabio_pass",
        "strict_fabio_reason", "strict_fabio_missing_pillars", "strict_fabio_pillars",
    ]
    data["missing_fields"] = _compact_missing(data, required)
    return data


def _build_engine_a_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    engine_a = _engine_dict(signal, "engine_a", "engineA")
    score = _to_float(_pick_nested(signal, engine_a, "score", "confluenceScore", "final_score"))
    max_score = _to_float(_pick_nested(signal, engine_a, "max_score", "maxScore"))
    score_pct = _to_float(_pick_nested(signal, engine_a, "score_pct", "rawScorePct", "confluencePct"))
    if score_pct is None and score is not None and max_score:
        score_pct = score / max_score * 100.0
    data = {
        "score": score,
        "max_score": max_score,
        "score_pct": score_pct,
        "direction": _pick_nested(signal, engine_a, "direction"),
        "regime": _pick_nested(signal, engine_a, "regime", "trendState", "regimeName"),
        "trend_score": _pick_nested(signal, engine_a, "trend_score", "trendScore"),
        "momentum_score": _pick_nested(signal, engine_a, "momentum_score", "mom_quality", "momentumScore"),
        "adx": _pick_nested(signal, engine_a, "adx"),
        "volatility_state": _pick_nested(signal, engine_a, "volatility_state", "volatilityState"),
        "factor_diagnostics": _pick_nested(signal, engine_a, "factor_diagnostics", "factorDiagnostics"),
        "threshold_progress": _pick_nested(signal, engine_a, "threshold_progress", "thresholdProgressPct"),
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    required = [
        "score", "max_score", "score_pct", "direction", "regime", "trend_score",
        "momentum_score", "adx", "volatility_state", "factor_diagnostics",
        "threshold_progress",
    ]
    data["missing_fields"] = _compact_missing(data, required)
    return data


def _build_engine_b_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    engine_b = _engine_dict(signal, "engine_b", "engine_b_overlay", "naked_data", "engineB")
    data = {
        "verdict": _pick_engine_nested(engine_b, "verdict", "structural_verdict", "engine_b_verdict"),
        "confidence": _pick_engine_nested(engine_b, "confidence", "confidence_score", "score", "engine_b_score"),
        "direction": _pick_engine_nested(engine_b, "direction", "independent_direction", "engine_b_independent_direction"),
        "bos": _pick_engine_nested(engine_b, "bos", "break_of_structure"),
        "choch": _pick_engine_nested(engine_b, "choch"),
        "order_block": _pick_engine_nested(engine_b, "order_block", "ob"),
        "fvg": _pick_engine_nested(engine_b, "fvg"),
        "liquidity_sweep": _pick_engine_nested(engine_b, "liquidity_sweep", "sweep"),
        "zone_context": _pick_engine_nested(engine_b, "zone_context", "zone_quality", "zone_ok"),
        "rr": _pick_engine_nested(engine_b, "rr", "rr1"),
        "style": _pick_engine_nested(engine_b, "style"),
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    required = [
        "verdict", "confidence", "direction", "bos", "choch", "order_block",
        "fvg", "liquidity_sweep", "zone_context", "rr", "style",
    ]
    data["missing_fields"] = _compact_missing(data, required)
    return data


def _build_engine_c_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    engine_c = _engine_dict(signal, "engine_c", "engineC")
    data = {
        "decision_state": _pick_nested(signal, engine_c, "decision_state"),
        "combined_conviction": _pick_nested(signal, engine_c, "combined_conviction", "combinedConviction", "conviction"),
        "a_norm": _pick_nested(signal, engine_c, "a_norm", "engine_a_norm"),
        "b_norm": _pick_nested(signal, engine_c, "b_norm", "engine_b_norm"),
        "blend_case": _pick_nested(signal, engine_c, "blend_case"),
        "watchlist_reason": _pick_nested(signal, engine_c, "watchlist_reason"),
        "reliability_flags": _as_list(_pick_nested(signal, engine_c, "reliability_flags")),
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    required = [
        "decision_state", "combined_conviction", "a_norm", "b_norm", "blend_case",
        "watchlist_reason", "reliability_flags",
    ]
    data["missing_fields"] = _compact_missing(data, required)
    return data


def _build_vision_context(signal: Dict[str, Any], vision: Dict[str, Any] | None = None) -> Dict[str, Any]:
    vision_data = _as_dict(vision) or _engine_dict(signal, "vision", "ai_vision")
    structured = _as_dict(vision_data.get("structured"))
    structured_trade_read = _as_dict(vision_data.get("structured_trade_read") or structured.get("structured_trade_read"))
    data = {
        "right_edge_status": _first_present(_pick(vision_data, "right_edge_status"), _pick(structured, "right_edge_status")),
        "tf_alignment": _first_present(_pick(vision_data, "tf_alignment"), _pick(structured, "tf_alignment")),
        "confirms_direction": _first_present(_pick(vision_data, "confirms_direction"), _pick(structured, "confirms_direction")),
        "style_ratings": _first_present(_pick(vision_data, "style_ratings", "vision_style_ratings"), _pick(structured, "style_ratings")),
        "last_candle_read": _first_present(_pick(vision_data, "last_candle_read"), _pick(structured, "last_candle_read")),
        "last_3_5_candles": _first_present(_pick(vision_data, "last_3_5_candles"), _pick(structured, "last_3_5_candles")),
        "visible_obstacles": _first_present(_pick(vision_data, "visible_obstacles"), _pick(structured, "visible_obstacles")),
        "freshness_status": _first_present(_pick(vision_data, "freshness_status"), _pick(structured, "freshness_status")),
        "chart_timestamp": _first_present(_pick(vision_data, "chart_timestamp"), _pick(structured, "chart_timestamp")),
        "latest_candle_ts": _first_present(_pick(vision_data, "latest_candle_ts"), _pick(structured, "latest_candle_ts")),
        "image_hash": _first_present(_pick(vision_data, "image_hash"), _pick(signal, "image_hash")),
        "structured_trade_read": structured_trade_read or None,
        "allowed_for_execution_context": _first_present(
            _pick(vision_data, "allowed_for_execution_context"),
            _pick(structured_trade_read, "allowed_for_execution_context"),
        ),
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    required = [
        "right_edge_status", "tf_alignment", "confirms_direction", "style_ratings",
        "last_candle_read", "last_3_5_candles", "visible_obstacles", "freshness_status",
        "chart_timestamp", "latest_candle_ts", "image_hash",
    ]
    data["missing_fields"] = _compact_missing(data, required)
    return data


def _build_risk_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    risk = _engine_dict(signal, "risk", "risk_state", "riskState")
    fee_guard = _pick(signal, "fee_guard", "spread_fees_guard")
    data = {
        "rr": _first_present(_pick(risk, "rr"), _pick(signal, "rr1", "rr")),
        "min_rr": _first_present(_pick(risk, "min_rr", "minRR"), _pick(signal, "min_rr", "minRR")),
        "sl": _first_present(_pick(risk, "sl", "stop_loss"), _pick(signal, "sl", "stopLoss")),
        "tp": _first_present(_pick(risk, "tp", "take_profit"), _pick(signal, "tp", "tp1", "takeProfit")),
        "entry": _first_present(_pick(risk, "entry"), _pick(signal, "entry", "price")),
        "position_size": _first_present(_pick(risk, "position_size"), _pick(signal, "position_size")),
        "spread": _first_present(_pick(risk, "spread"), _pick(signal, "spread", "spread_pips")),
        "fees": _first_present(_pick(risk, "fees"), fee_guard),
        "slippage": _first_present(_pick(risk, "slippage"), _pick(signal, "slippage")),
        "guardian_status": _first_present(_pick(risk, "guardian_status"), _pick(signal, "guardian_status")),
        "kill_switch_status": _first_present(_pick(risk, "kill_switch_status"), _pick(signal, "kill_switch_status")),
        "event_risk": _first_present(_pick(risk, "event_risk"), _pick(signal, "event_risk")),
        "data_freshness_status": _first_present(
            _pick(risk, "data_freshness_status"),
            _freshness_status(_pick(signal, "dataFreshness", "data_freshness", "freshnessStatus", "freshness_status")),
        ),
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    required = [
        "rr", "min_rr", "sl", "tp", "entry", "position_size", "spread", "fees",
        "slippage", "guardian_status", "kill_switch_status", "event_risk",
        "data_freshness_status",
    ]
    data["missing_fields"] = _compact_missing(data, required)
    return data


def _build_data_quality_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    candle_meta = _as_dict(signal.get("candleFetchMeta") or signal.get("candle_fetch_meta"))
    data_freshness = _pick(signal, "dataFreshness", "data_freshness")
    if data_freshness is _MISSING:
        data_freshness = None
    data_freshness_status = _freshness_status(data_freshness)
    direct_freshness_status = _pick(signal, "freshnessStatus", "freshness_status")
    if isinstance(data_freshness_status, str) and data_freshness_status.upper().startswith("ALLOWED_FALSE"):
        freshness_status = data_freshness_status
    else:
        freshness_status = _first_present(direct_freshness_status, data_freshness_status)
    stale_flags = _as_list(signal.get("stale_flags") or candle_meta.get("stale_flags"))
    provider_flags = _as_list(signal.get("provider_flags") or candle_meta.get("provider_flags"))
    data = {
        "candle_source": _first_present(_pick(candle_meta, "source"), _pick(signal, "candle_source", "source")),
        "volume_source": _first_present(_pick(signal, "volume_source"), _pick(signal, "vp_volume_source")),
        "freshness_status": freshness_status,
        "stale_flags": stale_flags,
        "provider_flags": provider_flags,
        "confirmed_only": _first_present(_pick(candle_meta, "confirmed_only"), _pick(signal, "confirmed_only")),
        "forming_bar_policy": _first_present(_pick(candle_meta, "forming_bar_policy"), _pick(signal, "forming_bar_policy")),
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    required = [
        "candle_source", "volume_source", "freshness_status", "confirmed_only",
        "forming_bar_policy",
    ]
    data["missing_fields"] = _compact_missing(data, required)
    return data


def _build_deterministic_gate_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    engine_c = _engine_dict(signal, "engine_c", "engineC")
    gate_detail = _as_dict(signal.get("engineATradeGate"))
    data = {
        "trade": _first_present(_pick(signal, "trade"), _pick(engine_c, "trade")),
        "advisory_rule_trade_allowed": _pick(signal, "advisory_rule_trade_allowed"),
        "execution_allowed": _pick(signal, "execution_allowed", "executionAllowed"),
        "risk_allowed": _pick(signal, "risk_allowed", "riskAllowed"),
        "freshness_allowed": _pick(signal, "freshness_allowed", "freshnessAllowed"),
        "signal_class": _pick(signal, "signalClass", "signal_class"),
        "signal_tier": _pick(signal, "signalTier", "signal_tier", "scan_tier"),
        "final_state": _pick(signal, "finalState", "final_state"),
        "block_reason": _pick(signal, "blockReason", "block_reason"),
        "engine_c_decision_state": _first_present(_pick(engine_c, "decision_state"), _pick(signal, "decision_state")),
        "engine_a_trade_enabled": _pick(signal, "engineATradeEnabled", "engine_a_trade_enabled"),
        "engine_a_trade_gate": gate_detail or None,
        "engine_a_trade_gate_reason": gate_detail.get("reason") if gate_detail else None,
    }
    for key, value in list(data.items()):
        if value is _MISSING:
            data[key] = None
    return data


def build_ai_review_packet(
    signal: Dict[str, Any],
    user_question: str | None = None,
    vision: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a canonical advisory-only AI review packet without changing legacy callers."""
    signal = dict(signal or {})
    if ensure_trace_id:
        try:
            ensure_trace_id(signal)
        except Exception:
            pass
    asset_type = str(signal.get("type") or signal.get("asset_type") or "unknown").lower()
    requested_style = resolve_ai_style(signal, str(signal.get("requested_style") or signal.get("style") or "auto"))
    engine_source = str(
        signal.get("engine_source")
        or signal.get("source_engine")
        or signal.get("engine")
        or signal.get("setup_engine")
        or "unknown"
    )
    engine_a = _build_engine_a_context(signal)
    engine_b = _build_engine_b_context(signal)
    engine_c = _build_engine_c_context(signal)
    engine_d = build_engine_d_context(signal)
    vision_ctx = _build_vision_context(signal, vision)
    risk = _build_risk_context(signal)
    data_quality = _build_data_quality_context(signal)
    deterministic_gates = _build_deterministic_gate_context(signal)
    try:
        from cross_engine_context import build_cross_engine_context

        cross_engine_payload = build_cross_engine_context(signal)
    except Exception:
        cross_engine_payload = {
            "engine_a_native_context": {},
            "engine_b_native_context": {},
            "cross_engine_context": {},
            "risk_context": risk,
            "learning_context": {},
        }
    similar = _as_dict(signal.get("similar_outcomes")) or {
        "examples": [],
        "aggregate_sample_size": 0,
        "win_rate": None,
        "avg_r": None,
        "profit_factor": None,
        "oos_decay": None,
        "reliability": "unavailable",
        "warning": None,
        "missing_fields": [],
    }
    similar.setdefault("missing_fields", [])
    market_context = {
        "pair": signal.get("pair") or signal.get("display") or signal.get("symbol"),
        "session": signal.get("session") or signal.get("current_session"),
        "timeframe": signal.get("timeframe") or signal.get("tf"),
        "timestamp": signal.get("timestamp") or signal.get("ts"),
        "regime": signal.get("regime") or signal.get("regimeName"),
        "data_freshness": signal.get("dataFreshness") or signal.get("data_freshness"),
    }
    market_intelligence = _as_dict(signal.get("market_intelligence"))
    try:
        from config import CONFIG as _CONFIG

        mi_enabled = bool(_CONFIG.get("AI_MARKET_INTELLIGENCE_ENABLED", True))
    except BaseException:
        mi_enabled = True
    if not market_intelligence and mi_enabled:
        try:
            from market_intelligence import get_market_intelligence

            market_intelligence = get_market_intelligence(market_context.get("pair") or _signal_symbol(signal), asset_type)
        except Exception as exc:
            market_intelligence = {
                "schema_version": "market_intelligence.v1",
                "freshness_status": "unavailable",
                "warnings": [f"Market intelligence unavailable: {exc}"],
                "missing_fields": [],
            }
    completeness = {
        "engine_a_complete": _section_complete(engine_a, [k for k in engine_a if k != "missing_fields"]),
        "engine_b_complete": _section_complete(engine_b, [k for k in engine_b if k != "missing_fields"]),
        "engine_c_complete": _section_complete(engine_c, [k for k in engine_c if k != "missing_fields"]),
        "engine_d_complete": _section_complete(engine_d, [k for k in engine_d if k != "missing_fields"]),
        "vision_complete": _section_complete(vision_ctx, [k for k in vision_ctx if k != "missing_fields"]),
        "risk_complete": _section_complete(risk, [k for k in risk if k != "missing_fields"]),
        "data_quality_complete": _section_complete(data_quality, [k for k in data_quality if k != "missing_fields"]),
    }
    completeness["overall_complete"] = round(sum(completeness.values()) / max(len(completeness), 1), 4)
    return {
        "schema_version": "1.0",
        "trace_id": str(signal.get("trace_id") or ""),
        "symbol": _signal_symbol(signal),
        "asset_type": asset_type,
        "direction": signal.get("direction"),
        "requested_style": requested_style,
        "engine_source": engine_source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "market_context": market_context,
        "market_intelligence": market_intelligence,
        "engine_a": engine_a,
        "engine_b": engine_b,
        "engine_a_native_context": cross_engine_payload.get("engine_a_native_context") or {},
        "engine_b_native_context": cross_engine_payload.get("engine_b_native_context") or {},
        "cross_engine_context": cross_engine_payload.get("cross_engine_context") or {},
        "risk_context": cross_engine_payload.get("risk_context") or risk,
        "learning_context": cross_engine_payload.get("learning_context") or {},
        "engine_c": engine_c,
        "engine_d": engine_d,
        "vision": vision_ctx,
        "risk": risk,
        "data_quality": data_quality,
        "similar_outcomes": similar,
        "user_question": user_question,
        "context_completeness": completeness,
        "deterministic_gates": deterministic_gates,
    }
