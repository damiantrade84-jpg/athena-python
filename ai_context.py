"""ai_context.py — Shared AI context builder and style resolver."""

from typing import Dict, Any, List, Optional

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
        "trendState": signal.get("trendState") or signal.get("regimeName") or signal.get("regime", {}).get("label"),
        "warnings": signal.get("warnings", []),
    }
    
    # 3. Engine B metrics
    engine_b_data = signal.get("engine_b") or signal.get("engine_b_overlay") or signal.get("naked_data") or {}
    if not isinstance(engine_b_data, dict):
        engine_b_data = {}
        
    engine_b = {
        "structural_verdict": engine_b_data.get("structural_verdict") or signal.get("engine_b_verdict"),
        "current_swing_sequence": engine_b_data.get("current_swing_sequence") or engine_b_data.get("swing_sequence"),
        "macro_swing_sequence": engine_b_data.get("macro_swing_sequence") or engine_b_data.get("macro_sequence"),
        "structure_score": engine_b_data.get("structure_score") or engine_b_data.get("score") or signal.get("engine_b_score"),
        "confidence_score": engine_b_data.get("confidence_score") or engine_b_data.get("confidence") or signal.get("engine_b_score"),
        "max_possible": engine_b_data.get("max_possible") or signal.get("engine_b_max"),
        "score_pct": engine_b_data.get("score_pct") or engine_b_data.get("pct"),
        "is_actionable": engine_b_data.get("is_actionable") or engine_b_data.get("passed") or engine_b_data.get("checklist_passed"),
        "zone_quality": engine_b_data.get("zone_quality") or engine_b_data.get("zone_ok"),
        "trigger_quality": engine_b_data.get("trigger_quality") or engine_b_data.get("trigger_ok"),
        "room_to_resistance": engine_b_data.get("room_to_resistance") or engine_b_data.get("distance_to_res"),
        "room_to_support": engine_b_data.get("room_to_support") or engine_b_data.get("distance_to_sup"),
        "recommended_sl": engine_b_data.get("recommended_sl") or engine_b_data.get("recommended_stop_loss"),
        "recommended_tp": engine_b_data.get("recommended_tp") or engine_b_data.get("recommended_take_profit"),
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
        "slPct": sl_pct,
        "atr": atr,
        "slDistance": abs(entry - sl) if entry > 0 and sl > 0 else 0,
        "MAX_SL_PCT": signal.get("MAX_SL_PCT"),
        "position_sizing": signal.get("position_sizing") or signal.get("sizing"),
        "risk_sizing_context": signal.get("risk_sizing_context"),
        "spread_fees_guard": signal.get("spread_fees_guard"),
    }
    
    # 7. Calibration notes
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
        "calibration_notes": calibration_notes,
    }

def build_ai_calibration_context_string(signal: Dict[str, Any], engine_source: str, explicit_style: str = "auto") -> str:
    """Format calibration context as a readable string for Marcus Reid prompts."""
    ctx = build_ai_calibration_context(signal, engine_source, explicit_style)
    identity = ctx["identity"]
    engine_a = ctx["engine_a"]
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
        
    lines.append(f"Entry: {trade_risk['entry']} | SL: {trade_risk['sl']} | TP1: {trade_risk['tp1']} | TP2: {trade_risk['tp2']}")
    lines.append(f"R:R = 1:{trade_risk['rr1']} / 1:{trade_risk['rr2']}")

    lines.append("Note: thresholdProgressPct is scanner readiness; rawScorePct is theoretical factor quality.")

    return "\n".join(lines)
