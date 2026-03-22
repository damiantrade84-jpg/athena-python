"""
engine_c.py — Consensus Engine

Combines Engine A (quantitative factor scoring) and Engine B (naked price action)
into a unified trading signal with single direction, SL, TP, and conviction-based
position sizing.

Architecture:
  Layer 1: Normalise Engine A and B scores to 0-1 scale
  Layer 2: Direction gate + regime-weighted conviction scoring
  Layer 3: SL/TP resolution + position sizing multiplier

AI Vision is NOT a voter — it's a validation/override layer applied manually.

Sources:
  - ENGINE_C_AB_WEIGHTS: backtest evidence (A stronger in trends, B in ranges); not CONFIG REGIME_WEIGHTS
  - SL: structural → ATR clamp (2.5x) → tighter candidate; RR enforced in resolve_tp / consensus
  - TP: Engine B structural if RR ≥ 1.5, else Engine A ATR
  - Vision modes: Confirm / Weaken / Contradict / Override (AVOID)
"""

import logging
from typing import Optional

log = logging.getLogger("sentinel")


# ── Regime-conditional engine weights ─────────────────────────────────────────
# Backtest evidence: Engine A dominates in trends (COT/momentum factors),
# Engine B dominates in ranges (BOS/CHoCH/zones more meaningful at structure).
# Blend weights for Engine A vs Engine B (NOT config.yaml REGIME_WEIGHTS, which adjusts factor weights inside A).
ENGINE_C_AB_WEIGHTS = {
    "TRENDING":        {"A": 0.65, "B": 0.35},
    "RANGING":         {"A": 0.35, "B": 0.65},
    "HIGH_VOLATILITY": {"A": 0.50, "B": 0.50},
    "LOW_VOLATILITY":  {"A": 0.45, "B": 0.55},
}

# ── Conviction tier thresholds ────────────────────────────────────────────────
CONVICTION_TIERS = {
    "HIGH":   {"min": 0.70, "sizing": 1.0},
    "MEDIUM": {"min": 0.50, "sizing": 0.65},
    "LOW":    {"min": 0.35, "sizing": 0.35},
    "SKIP":   {"min": 0.00, "sizing": 0.0},
}

# ── AI Vision verdict mapping ─────────────────────────────────────────────────
# Vision is NOT a voter. It modifies conviction after consensus is established.
VISION_MODIFIERS = {
    "STRONG":    {"action": "confirm",     "conviction_mult": 1.15},  # boost
    "MODERATE":  {"action": "confirm",     "conviction_mult": 1.0},   # neutral
    "WEAK":      {"action": "weaken",      "conviction_mult": 0.70},  # reduce
    "AVOID":     {"action": "override",    "conviction_mult": 0.0},   # hard veto
    "CONTRADICTS": {"action": "contradict", "conviction_mult": 0.0},  # hard veto
}


def normalise_engine_a(signal_a: dict) -> dict:
    """Normalise Engine A output to 0-1 scale.

    Engine A outputs:
      - confluenceScore: raw score (0 to ~3.0)
      - maxScore: maximum possible (dynamic, depends on active factors)
      - direction: LONG/SHORT
      - regime: dict with label, state
      - sl, tp1, tp2: ATR-based levels
      - confluencePct: already 0-100 percentage
    """
    score = float(signal_a.get("confluenceScore", 0))
    max_score = float(signal_a.get("maxScore", 3.0)) or 3.0
    pct = float(signal_a.get("confluencePct", 0))

    # Use percentage if available (already normalised 0-100), else compute
    if pct > 0:
        norm = min(1.0, pct / 100.0)
    else:
        norm = min(1.0, score / max_score) if max_score > 0 else 0.0

    regime_data = signal_a.get("regime", {})
    if isinstance(regime_data, dict):
        regime_label = regime_data.get("label", regime_data.get("regime", "RANGING"))
    elif isinstance(regime_data, str):
        regime_label = regime_data
    else:
        regime_label = "RANGING"

    # Forex (and any engine with maxScore ~1.0): slightly lower floor so marginal A-side signals still participate in C.
    _a_has_floor = 0.25 if max_score <= 1.01 else 0.30
    return {
        "score_norm": round(norm, 4),
        "direction": signal_a.get("direction"),
        "regime": regime_label,
        "sl": signal_a.get("sl"),
        "tp": signal_a.get("tp1"),
        "tp2": signal_a.get("tp2"),
        "rr": signal_a.get("rr1", 0),
        "raw_score": score,
        "max_score": max_score,
        "has_signal": norm > _a_has_floor
        and signal_a.get("direction") in ("LONG", "SHORT"),
        "cot_active": bool(signal_a.get("votes", {}).get("derivatives")),
        "carry_active": bool(signal_a.get("votes", {}).get("carry")),
        "style": signal_a.get("tradeStyle", "swing"),
    }


def normalise_engine_b(signal_b: dict, confidence_b: dict = None) -> dict:
    """Normalise Engine B output to 0-1 scale.

    Engine B outputs:
      - structural_verdict: "CLEAR" or "ERROR"
      - confidence score/max_possible from calculate_confidence
      - direction: LONG/SHORT
      - recommended_stop_loss, recommended_take_profit
      - bos_confirmed, choch_confirmed, ob_at_zone, etc.
    """
    conf = confidence_b or {}
    score = float(conf.get("score", 0))
    max_possible = float(conf.get("max_possible", 5.0)) or 5.0
    pct = float(conf.get("pct", 0))

    if pct > 0:
        norm = min(1.0, pct / 100.0)
    else:
        norm = min(1.0, score / max_possible) if max_possible > 0 else 0.0

    verdict = signal_b.get("structural_verdict", "ERROR")
    direction = signal_b.get("direction")

    return {
        "score_norm": round(norm, 4),
        "direction": direction,
        "sl": signal_b.get("recommended_stop_loss"),
        "tp": signal_b.get("recommended_take_profit"),
        "rr": float(conf.get("rr", 0)),
        "raw_score": score,
        "max_possible": max_possible,
        "has_signal": verdict == "CLEAR" and norm > 0.2 and direction in ("LONG", "SHORT"),
        "bos_confirmed": bool(signal_b.get("bos_confirmed")),
        "bos_mtf": bool(signal_b.get("bos_mtf_confirmed")),
        "choch_confirmed": bool(signal_b.get("choch_confirmed")),
        "ob_at_zone": bool(signal_b.get("ob_at_zone")),
        "ob_strength": max(
            (ob.get("strength", 0) for ob in signal_b.get("order_blocks", [])),
            default=0,
        ),
        "fvg_overlap": bool(signal_b.get("fvg_overlap")),
        "swing_sequence": signal_b.get("current_swing_sequence", ""),
        "macro_sequence": signal_b.get("macro_swing_sequence", ""),
        "structure_ok": bool(conf.get("structure_ok")),
        "zone_ok": bool(conf.get("zone_ok")),
        "trigger_ok": bool(conf.get("trigger_ok")),
        "trigger_pattern": conf.get("trigger_pattern", "NONE"),
    }


def resolve_sl(
    entry: float,
    sl_a: Optional[float],
    sl_b: Optional[float],
    direction: str,
    atr: float = 0.0,
) -> dict:
    """Resolve unified SL:
    1. Engine B structural level (has market meaning)
    2. Validate against ATR (wider than 2.5x ATR from entry is clamped when ATR known)
    3. Pick tighter of valid candidates (less risk per trade)

    Minimum RR is enforced later in ``resolve_tp`` and ``compute_consensus`` (not here).

    Returns dict with sl price and method used.
    """
    if not entry or entry <= 0:
        return {"sl": None, "method": "NO_ENTRY"}

    candidates = []
    max_sl_dist = atr * 2.5 if atr > 0 else float("inf")

    # Engine B structural SL
    if sl_b is not None and sl_b > 0:
        dist_b = abs(entry - sl_b)
        if dist_b <= max_sl_dist and dist_b > 0:
            candidates.append(("structural", sl_b, dist_b))
        elif dist_b > max_sl_dist and atr > 0:
            # Structural too wide — clamp to 2.5x ATR
            if direction == "LONG":
                clamped = entry - max_sl_dist
            else:
                clamped = entry + max_sl_dist
            candidates.append(("structural_clamped", clamped, max_sl_dist))

    # Engine A ATR-based SL
    if sl_a is not None and sl_a > 0:
        dist_a = abs(entry - sl_a)
        if dist_a > 0:
            candidates.append(("atr", sl_a, dist_a))

    if not candidates:
        # Fallback: 1.5x ATR
        if atr > 0:
            fallback = entry - (atr * 1.5) if direction == "LONG" else entry + (atr * 1.5)
            return {"sl": round(fallback, 6), "method": "atr_fallback"}
        return {"sl": None, "method": "NO_DATA"}

    # Pick tighter (less risk) — for LONG: highest SL, for SHORT: lowest SL
    if direction == "LONG":
        best = max(candidates, key=lambda c: c[1])
    else:
        best = min(candidates, key=lambda c: c[1])

    return {"sl": round(best[1], 6), "method": best[0], "distance": round(best[2], 6)}


def resolve_tp(
    entry: float,
    sl: float,
    tp_a: Optional[float],
    tp_b: Optional[float],
    direction: str,
) -> dict:
    """Resolve unified TP:
    - Engine B structural TP if RR ≥ 1.5 (placed at opposing zone)
    - Else Engine A ATR-based TP
    - Calculate actual RR from resolved levels
    """
    if not entry or not sl or entry <= 0:
        return {"tp": None, "rr": 0, "method": "NO_DATA"}

    risk = abs(entry - sl)
    if risk <= 0:
        return {"tp": None, "rr": 0, "method": "ZERO_RISK"}

    # Check Engine B structural TP
    if tp_b is not None and tp_b > 0:
        reward_b = abs(tp_b - entry)
        rr_b = reward_b / risk if risk > 0 else 0
        if rr_b >= 1.5:
            return {
                "tp": round(tp_b, 6),
                "rr": round(rr_b, 2),
                "method": "structural",
            }

    # Fallback to Engine A ATR-based TP
    if tp_a is not None and tp_a > 0:
        reward_a = abs(tp_a - entry)
        rr_a = reward_a / risk if risk > 0 else 0
        return {
            "tp": round(tp_a, 6),
            "rr": round(rr_a, 2),
            "method": "atr",
        }

    # Last resort: 2x risk
    if direction == "LONG":
        tp_fallback = entry + (risk * 2.0)
    else:
        tp_fallback = entry - (risk * 2.0)

    return {"tp": round(tp_fallback, 6), "rr": 2.0, "method": "fallback_2R"}


def classify_conviction(conviction: float) -> tuple:
    """Map conviction score to tier and sizing multiplier."""
    if conviction >= CONVICTION_TIERS["HIGH"]["min"]:
        return "HIGH", CONVICTION_TIERS["HIGH"]["sizing"]
    elif conviction >= CONVICTION_TIERS["MEDIUM"]["min"]:
        return "MEDIUM", CONVICTION_TIERS["MEDIUM"]["sizing"]
    elif conviction >= CONVICTION_TIERS["LOW"]["min"]:
        return "LOW", CONVICTION_TIERS["LOW"]["sizing"]
    return "SKIP", 0.0


def apply_vision(consensus: dict, vision_result: dict) -> dict:
    """Apply AI Vision verdict to modify consensus conviction.

    Vision modes (from ChatGPT architecture):
    - CONFIRM: boosts conviction (STRONG/MODERATE)
    - WEAKEN: reduces conviction (WEAK)
    - CONTRADICT: kills the trade
    - OVERRIDE/AVOID: hard veto regardless of engines

    Vision structured output (from Gemini):
    - rating: STRONG/MODERATE/WEAK/AVOID
    - confirms_direction: true/false
    - sl_flag: ok/too_tight/too_wide
    - tp_flag: ok/too_ambitious/too_conservative
    """
    if not vision_result:
        return consensus

    updated = dict(consensus)

    # Parse structured data if available
    structured = vision_result.get("structured") or {}
    rating = structured.get("rating", "").upper() if structured else ""
    confirms_dir = structured.get("confirms_direction", True) if structured else True
    sl_flag = structured.get("sl_flag", "ok") if structured else "ok"
    tp_flag = structured.get("tp_flag", "ok") if structured else "ok"

    # If no structured data, try to extract rating from text
    if not rating:
        text = (vision_result.get("analysis") or "").upper()
        for r in ["AVOID", "STRONG", "MODERATE", "WEAK", "CONTRADICTS"]:
            if r in text:
                rating = r
                break
        # Check for direction contradiction in text
        if "CONTRADICT" in text:
            confirms_dir = False
            rating = "CONTRADICTS"

    if not rating:
        rating = "MODERATE"  # default if unparseable

    # Handle explicit direction contradiction
    if not confirms_dir and rating not in ("AVOID", "CONTRADICTS"):
        rating = "CONTRADICTS"

    # Apply modifier
    modifier = VISION_MODIFIERS.get(rating, VISION_MODIFIERS["MODERATE"])
    action = modifier["action"]
    mult = modifier["conviction_mult"]

    old_conviction = updated["conviction"]
    new_conviction = min(1.0, old_conviction * mult)

    # Reclassify tier
    tier, sizing = classify_conviction(new_conviction)

    updated["conviction"] = round(new_conviction, 4)
    updated["tier"] = tier
    updated["sizing_override"] = sizing
    updated["vision_applied"] = True
    updated["vision_action"] = action
    updated["vision_rating"] = rating
    updated["vision_sl_flag"] = sl_flag
    updated["vision_tp_flag"] = tp_flag

    # If Vision flags SL as too tight, check if we can widen
    if sl_flag == "too_tight" and updated.get("sl_method") != "atr":
        # Switch to Engine A's wider ATR-based SL if available
        if updated.get("sl_a") and updated.get("sl"):
            a_dist = abs(updated.get("entry", 0) - updated["sl_a"])
            c_dist = abs(updated.get("entry", 0) - updated["sl"])
            if a_dist > c_dist:
                updated["sl"] = updated["sl_a"]
                updated["sl_method"] = "atr_vision_override"
                # Recalculate RR
                risk = abs(updated["entry"] - updated["sl"])
                if risk > 0 and updated.get("tp"):
                    reward = abs(updated["tp"] - updated["entry"])
                    updated["rr"] = round(reward / risk, 2)

    # If Vision confirms and conviction is above LOW threshold, allow trade
    if action == "confirm" and new_conviction >= 0.35:
        updated["trade"] = True

    if action in ("override", "contradict"):
        updated["trade"] = False
        updated["verdict"] = f"VISION_{action.upper()}"
        updated["tier"] = "SKIP"
        updated["sizing_override"] = 0.0

    log.warning(
        f"[ENGINE C] Vision {action}: {rating} → conviction {old_conviction:.2f} → {new_conviction:.2f} "
        f"(tier={tier}, sl_flag={sl_flag})"
    )

    return updated


def compute_consensus(
    signal_a: dict,
    signal_b: dict,
    confidence_b: dict = None,
    ai_vision: dict = None,
    asset_type: str = "",
    regime: str = "RANGING",
    entry_price: float = 0.0,
    atr: float = 0.0,
) -> dict:
    """Main consensus function. Combines Engine A + B into unified signal.

    Returns dict with:
        trade: bool — whether to execute
        direction: str — LONG/SHORT
        conviction: float — 0.0 to 1.0
        tier: str — HIGH/MEDIUM/LOW/SKIP
        sizing_override: float — multiplier for risk_engine
        sl: float — resolved stop loss
        tp: float — resolved take profit
        rr: float — actual R:R
        verdict: str — ALIGNED/CONFLICT/A_ONLY/B_ONLY/etc
        engine_weights: dict — what weights were used
        components: dict — A_norm, B_norm for audit
    """
    # Step 1: Normalise both engines
    a = normalise_engine_a(signal_a)
    b = normalise_engine_b(signal_b, confidence_b)

    entry = entry_price or float(signal_a.get("price", 0))

    # Step 2: Determine signal availability
    a_has = a["has_signal"]
    b_has = b["has_signal"]

    # Neither engine has a signal
    if not a_has and not b_has:
        return _build_result(
            trade=False, verdict="NO_SIGNAL", direction=None,
            conviction=0.0, tier="SKIP", sizing=0.0,
            a_norm=a, b_norm=b, entry=entry,
        )

    # Only one engine has signal
    if a_has and not b_has:
        # Engine A only — allow at reduced conviction
        direction = a["direction"]
        conviction = a["score_norm"] * 0.6  # 60% of A score (no B confirmation)
        tier, sizing = classify_conviction(conviction)

        sl_resolved = resolve_sl(entry, a["sl"], None, direction, atr)
        tp_resolved = resolve_tp(entry, sl_resolved["sl"], a["tp"], None, direction)

        result = _build_result(
            trade=conviction >= CONVICTION_TIERS["LOW"]["min"],
            verdict="A_ONLY",
            direction=direction,
            conviction=conviction,
            tier=tier,
            sizing=sizing,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            rr=tp_resolved["rr"],
        )
        if ai_vision:
            result = apply_vision(result, ai_vision)
        return result

    if b_has and not a_has:
        # Engine B only — watchlist, no auto-execute
        direction = b["direction"]
        conviction = b["score_norm"] * 0.5  # 50% of B score (no A confirmation)
        tier, sizing = classify_conviction(conviction)

        sl_resolved = resolve_sl(entry, None, b["sl"], direction, atr)
        tp_resolved = resolve_tp(entry, sl_resolved["sl"], None, b["tp"], direction)

        result = _build_result(
            trade=False,  # B-only never auto-trades
            verdict="B_ONLY",
            direction=direction,
            conviction=conviction,
            tier="LOW",  # cap at LOW for B-only
            sizing=0.35,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            rr=tp_resolved["rr"],
        )
        if ai_vision:
            result = apply_vision(result, ai_vision)
            # Vision CONFIRMS on B-only can upgrade to tradeable
            if result.get("vision_action") == "confirm" and result["conviction"] >= 0.50:
                result["trade"] = True
                result["verdict"] = "B_ONLY_VISION_CONFIRMED"
        return result

    # Step 3: Both engines have signals — check direction agreement
    if a["direction"] != b["direction"]:
        # Both engines strongly disagree (high normalised scores, opposite directions) — not a proven regime change.
        opposing_high_confidence = a["score_norm"] > 0.70 and b["score_norm"] > 0.70

        return _build_result(
            trade=False,
            verdict="OPPOSING_HIGH_CONFIDENCE" if opposing_high_confidence else "DIRECTION_CONFLICT",
            direction=None,
            conviction=0.0,
            tier="SKIP",
            sizing=0.0,
            a_norm=a, b_norm=b, entry=entry,
            opposing_high_confidence=opposing_high_confidence,
        )

    # Step 4: Direction agrees — compute weighted conviction
    direction = a["direction"]

    # Get regime-conditional A/B blend (see ENGINE_C_AB_WEIGHTS; distinct from CONFIG REGIME_WEIGHTS).
    weights = ENGINE_C_AB_WEIGHTS.get(regime, {"A": 0.50, "B": 0.50})

    conviction = (a["score_norm"] * weights["A"]) + (b["score_norm"] * weights["B"])
    conviction = min(1.0, conviction)

    # Extra emphasis when B shows MTF BOS / strong OB. Note: calculate_confidence() may already
    # count these as extra checklist rows (raising B_norm); multipliers here are deliberate stacking.
    if b["bos_mtf"]:
        conviction = min(1.0, conviction * 1.08)  # +8% for multi-TF BOS
    if b["ob_at_zone"] and b["ob_strength"] >= 70:
        conviction = min(1.0, conviction * 1.05)  # +5% for strong OB at zone

    tier, sizing = classify_conviction(conviction)

    # Step 5: Resolve SL and TP
    sl_resolved = resolve_sl(entry, a["sl"], b["sl"], direction, atr)
    tp_resolved = resolve_tp(entry, sl_resolved["sl"], a["tp"], b["tp"], direction)

    # Validate minimum RR
    if tp_resolved["rr"] < 1.2:
        # RR too low after SL/TP resolution — try Engine A TP
        if a["tp"]:
            alt_tp = resolve_tp(entry, sl_resolved["sl"], a["tp"], None, direction)
            if alt_tp["rr"] > tp_resolved["rr"]:
                tp_resolved = alt_tp

    if tp_resolved["rr"] < 1.0:
        tier = "SKIP"
        sizing = 0.0

    result = _build_result(
        trade=tier != "SKIP",
        verdict="ALIGNED",
        direction=direction,
        conviction=conviction,
        tier=tier,
        sizing=sizing,
        a_norm=a, b_norm=b, entry=entry,
        sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
        tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
        rr=tp_resolved["rr"],
        weights=weights,
        regime=regime,
    )

    # Step 6: Apply Vision if available
    if ai_vision:
        result = apply_vision(result, ai_vision)

    log.warning(
        f"[ENGINE C] {asset_type} {direction}: A={a['score_norm']:.2f} B={b['score_norm']:.2f} "
        f"→ conviction={result['conviction']:.2f} tier={result['tier']} "
        f"SL={result.get('sl')} TP={result.get('tp')} RR={result.get('rr')} "
        f"weights={weights} verdict={result['verdict']}"
    )

    return result


def _build_result(
    trade: bool,
    verdict: str,
    direction: Optional[str],
    conviction: float,
    tier: str,
    sizing: float,
    a_norm: dict = None,
    b_norm: dict = None,
    entry: float = 0.0,
    sl: float = None,
    sl_method: str = "",
    tp: float = None,
    tp_method: str = "",
    rr: float = 0.0,
    weights: dict = None,
    regime: str = "",
    opposing_high_confidence: bool = False,
    **kwargs,
) -> dict:
    """Build standardised consensus result dict."""
    return {
        "trade": trade,
        "verdict": verdict,
        "direction": direction,
        "entry": round(entry, 6) if entry else 0.0,
        "sl": round(sl, 6) if sl else None,
        "sl_method": sl_method,
        "sl_a": a_norm.get("sl") if a_norm else None,
        "sl_b": b_norm.get("sl") if b_norm else None,
        "tp": round(tp, 6) if tp else None,
        "tp_method": tp_method,
        "rr": round(rr, 2),
        "conviction": round(conviction, 4),
        "tier": tier,
        "sizing_override": round(sizing, 4),
        "engine_weights": weights or {},
        "regime": regime,
        "opposing_high_confidence": opposing_high_confidence,
        "components": {
            "a_norm": round(a_norm["score_norm"], 4) if a_norm else 0.0,
            "a_direction": a_norm.get("direction") if a_norm else None,
            "a_has_signal": a_norm.get("has_signal", False) if a_norm else False,
            "a_cot_active": a_norm.get("cot_active", False) if a_norm else False,
            "b_norm": round(b_norm["score_norm"], 4) if b_norm else 0.0,
            "b_direction": b_norm.get("direction") if b_norm else None,
            "b_has_signal": b_norm.get("has_signal", False) if b_norm else False,
            "b_bos": b_norm.get("bos_confirmed", False) if b_norm else False,
            "b_ob_at_zone": b_norm.get("ob_at_zone", False) if b_norm else False,
            "b_sequence": b_norm.get("swing_sequence", "") if b_norm else "",
        },
        "vision_applied": False,
        "vision_action": None,
        "vision_rating": None,
        "vision_sl_flag": None,
        "vision_tp_flag": None,
    }
