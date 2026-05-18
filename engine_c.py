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

from calibration import predict_calibrated_prob
from config import CONFIG, AISafetyConstants
from intermarket import engine_c_conviction_multiplier
from meta_learner import apply_meta_policy, get_dynamic_engine_weights, get_engine_context
from stability_monitor import record_signal_event

log = logging.getLogger("sentinel")


def _engine_a_reliability(engine_a: dict, a_quality: float) -> float:
    """Blend Engine A confidence-detail score with meta quality; missing confidence → quality only."""
    c = engine_a.get("confidence")
    if c is None:
        return float(a_quality)
    return float(c) * 0.5 + float(a_quality) * 0.5


# ── Regime-conditional engine weights ─────────────────────────────────────────
# Backtest evidence: Engine A dominates in trends (COT/momentum factors),
# Engine B dominates in ranges (BOS/CHoCH/zones more meaningful at structure).
# Blend weights for Engine A vs Engine B (NOT config.yaml REGIME_WEIGHTS, which adjusts factor weights inside A).
ENGINE_C_AB_WEIGHTS = {
    # A weight reduced from 0.65 pending validation: Engine A directional hit-rate is
    # 42.3% (2026-04-18 audit). Giving A 65% weight in trends amplifies a noisy scorer.
    # Equalise until LONG/SHORT regime split confirms A adds directional value in trends.
    "TRENDING":        {"A": 0.40, "B": 0.60},
    "RANGING":         {"A": 0.35, "B": 0.65},
    "HIGH_VOLATILITY": {"A": 0.45, "B": 0.55},
    "LOW_VOLATILITY":  {"A": 0.40, "B": 0.60},
}
ENGINE_C_META_BLEND = 0.20  # Default; override with CONFIG["ENGINE_C_META_BLEND"] (0..1)


def _default_engine_a_max_score(signal_a: dict) -> float:
    """Fallback maxScore when Engine A omits or zeroes it; optional per-type overrides in CONFIG."""
    by_type = CONFIG.get("ENGINE_C_A_DEFAULT_MAX_SCORE_BY_TYPE")
    if isinstance(by_type, dict):
        for key in (
            signal_a.get("type"),
            signal_a.get("asset_class"),
            signal_a.get("assetType"),
        ):
            if key is None:
                continue
            raw = by_type.get(str(key).lower())
            if raw is None:
                continue
            try:
                v = float(raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
    try:
        v = float(CONFIG.get("ENGINE_C_A_DEFAULT_MAX_SCORE", 3.0))
        return v if v > 0 else 3.0
    except (TypeError, ValueError):
        return 3.0


def _engine_c_meta_blend() -> float:
    """How much to tilt A/B weights toward meta-learner trust (bounded)."""
    try:
        raw = CONFIG.get("ENGINE_C_META_BLEND", ENGINE_C_META_BLEND)
        v = float(raw)
        return max(0.0, min(1.0, v))
    except (TypeError, ValueError):
        return ENGINE_C_META_BLEND

def _compute_b_reliability(b: dict, b_quality: float = 0.50) -> float:
    """Compute Engine B structural reliability from B signal metadata."""
    b_struct_rel = 0.5
    if b.get("structure_ok"):
        b_struct_rel += 0.2
    if b.get("zone_ok"):
        b_struct_rel += 0.15
    if b.get("trigger_ok"):
        b_struct_rel += 0.15
    return (b_struct_rel * 0.5) + (b_quality * 0.5)


# ── Conviction tier thresholds ────────────────────────────────────────────────
CONVICTION_TIERS = {
    "HIGH":   {"min": 0.70, "sizing": 1.0},
    "MEDIUM": {"min": 0.50, "sizing": 0.65},
    "LOW":    {"min": 0.35, "sizing": 0.35},
    "SKIP":   {"min": 0.00, "sizing": 0.0},
}

# ── AI Vision verdict mapping ─────────────────────────────────────────────────
# Vision is NOT a voter. It modifies conviction after consensus is established.
_DEFAULT_VISION_MODIFIERS = {
    "STRONG":    {"action": "confirm",     "conviction_mult": 1.30},  # boost
    "MODERATE":  {"action": "confirm",     "conviction_mult": 1.0},   # neutral
    "WEAK":      {"action": "weaken",      "conviction_mult": 0.70},  # reduce
    "AVOID":     {"action": "override",    "conviction_mult": 0.0},   # hard veto
    "CONTRADICTS": {"action": "contradict", "conviction_mult": 0.0},  # hard veto
}


def _vision_modifiers() -> dict:
    raw = CONFIG.get("VISION_MODIFIERS")
    if not isinstance(raw, dict):
        return _DEFAULT_VISION_MODIFIERS

    out = {}
    for key, default in _DEFAULT_VISION_MODIFIERS.items():
        entry = raw.get(key, default)
        if not isinstance(entry, dict):
            out[key] = default
            continue
        action = str(entry.get("action", default["action"]))
        try:
            conviction_mult = float(
                entry.get("conviction_mult", default["conviction_mult"])
            )
        except (TypeError, ValueError):
            conviction_mult = float(default["conviction_mult"])
        out[key] = {"action": action, "conviction_mult": conviction_mult}

    # Veto verdicts cannot be neutralized by a mis-set YAML action or multiplier.
    if "AVOID" in out:
        out["AVOID"] = {"action": "override", "conviction_mult": 0.0}
    if "CONTRADICTS" in out:
        out["CONTRADICTS"] = {"action": "contradict", "conviction_mult": 0.0}
    return out


def _build_disagreement_diagnosis(
    verdict: str,
    a: dict,
    b: dict,
    signal_a: dict,
    signal_b: dict,
    regime: str,
) -> dict:
    """
    Build a human-readable explanation of why Engine A and Engine B disagree.

    This is a diagnostic-only field — no scoring effect whatsoever.
    Surfaces in the UI and audit log to help the user understand conflicts.

    Returns:
        dict with:
          verdict_explanation: str   one-line summary of the verdict
          a_summary: str             Engine A status in plain English
          b_summary: str             Engine B status in plain English
          b_independent_direction: dict | None   Engine B's own opinion if available
          conflict_type: str         'genuine_direction' | 'inherited_direction' | 'missing_signal' | 'no_conflict'
          conflict_explanation: str  why the conflict occurred
    """
    # ── Engine A summary ─────────────────────────────────────────────────────
    a_dir = a.get("direction", "N/A")
    a_norm = round(a.get("score_norm", 0.0), 3)
    a_has  = bool(a.get("has_signal"))
    a_regime = a.get("regime", regime)

    # Top factor from signal_a if available
    _factor_scores  = signal_a.get("factor_scores", {}) or {}
    _top_factor     = max(_factor_scores, key=lambda k: abs(_factor_scores[k] or 0), default=None) if _factor_scores else None
    _top_factor_val = round(_factor_scores.get(_top_factor, 0) or 0, 3) if _top_factor else None
    _active_dir_factors = signal_a.get("active_directional_factors") or []
    a_signal_source = (
        f"top_factor={_top_factor}({_top_factor_val}), active={_active_dir_factors}"
        if _top_factor
        else "no factor detail available"
    )

    if a_has:
        a_summary = (
            f"A={a_dir} norm={a_norm} regime={a_regime} [{a_signal_source}]"
        )
    else:
        # Why does A not have a signal?
        _is_insuff    = signal_a.get("insufficient_factors", False)
        _is_indet     = signal_a.get("indeterminate_direction", False)
        _min_dir_fail = signal_a.get("min_directional_failed", False)
        _feed_status  = signal_a.get("feed_status", {}) or {}
        _failed_feeds = [k for k, v in _feed_status.items() if v not in ("ok", "unsupported", "disabled", "no_coverage")]
        _reasons = []
        if _is_insuff:
            _reasons.append("insufficient_directional_factors")
        if _is_indet:
            _reasons.append("indeterminate_direction")
        if _min_dir_fail:
            _reasons.append(f"min_directional_threshold_failed (norm={a_norm}<=0.30)")
        if _failed_feeds:
            _reasons.append(f"feed_errors={_failed_feeds}")
        a_summary = (
            f"A=NO_SIGNAL norm={a_norm} reasons={'|'.join(_reasons) or 'low_conviction'}"
        )

    # ── Engine B summary ─────────────────────────────────────────────────────
    b_dir        = b.get("direction", "N/A")
    b_norm_val   = round(b.get("score_norm", 0.0), 3)
    b_has        = bool(b.get("has_signal"))
    b_verdict_b  = signal_b.get("structural_verdict", "?")
    b_struct_ok  = bool(b.get("structure_ok"))
    b_zone_ok    = bool(b.get("zone_ok"))
    b_trigger_ok = bool(b.get("trigger_ok"))
    b_swing      = b.get("swing_sequence", "?")
    b_macro      = b.get("macro_sequence", "?")

    # Engine B independent direction (Phase 2 addition)
    b_ind_dir = signal_b.get("engine_b_independent_direction")

    if b_has:
        b_summary = (
            f"B={b_dir} norm={b_norm_val} verdict={b_verdict_b} "
            f"struct={b_struct_ok} zone={b_zone_ok} trigger={b_trigger_ok} "
            f"H1_seq={b_swing} H4_seq={b_macro}"
        )
    else:
        # Why does B not have a signal?
        _b_missing = []
        if b_verdict_b != "CLEAR":
            _b_missing.append(f"verdict={b_verdict_b}")
        if b_verdict_b == "CLEAR" and not b.get("passed"):
            _b_missing.append(b.get("signal_diagnostic") or "engine_b_checklist_failed")
        if not b_struct_ok:
            _b_diagnostics = signal_b.get("engine_b_diagnostics") or {}
            if not isinstance(_b_diagnostics, dict):
                log.debug("[ENGINE C] signal_b['engine_b_diagnostics'] is %s (expected dict), treating as empty", type(_b_diagnostics).__name__)
                _b_diagnostics = {}
            _b_reason_codes = _b_diagnostics.get("reason_codes", [])
            _b_missing.append(f"structure_failed (codes={_b_reason_codes})")
        if not b_zone_ok:
            _b_missing.append("no_zone_touch")
        if not b_trigger_ok:
            _b_missing.append("no_trigger")
        if b_norm_val <= 0.2:
            _b_missing.append(f"low_confidence (norm={b_norm_val}<=0.20)")
        b_summary = (
            f"B=NO_SIGNAL norm={b_norm_val} reasons={'|'.join(_b_missing) or 'low_confidence'}"
        )

    # ── Conflict classification ───────────────────────────────────────────────
    conflict_type = "no_conflict"
    conflict_explanation = ""

    if verdict == "ALIGNED":
        conflict_type = "no_conflict"
        conflict_explanation = (
            f"Both engines agree: A={a_dir} B={b_dir} regime={regime}"
        )

    elif verdict in ("A_ONLY", "B_ONLY", "B_ONLY_SCORED", "B_ONLY_VISION_CONFIRMED"):
        conflict_type = "missing_signal"
        missing_engine = "B" if verdict.startswith("A") else "A"
        conflict_explanation = (
            f"Only one engine has a signal. Engine {missing_engine} did not produce a signal. "
            f"{a_summary if verdict.startswith('A') else b_summary}"
        )

    elif verdict in ("DIRECTION_CONFLICT", "B_OVERRIDE_CONFLICT", "OPPOSING_HIGH_CONFIDENCE"):
        # Determine if the conflict is genuine (both engines independently point opposite ways)
        # vs inherited (B was run in the same direction context as A)
        b_own_dir = (b_ind_dir or {}).get("direction")
        b_own_conf = (b_ind_dir or {}).get("confidence", "NONE")

        if b_own_dir is not None and b_own_dir != a_dir:
            conflict_type = "genuine_direction"
            conflict_explanation = (
                f"GENUINE conflict: Engine A={a_dir} (indicators), "
                f"Engine B independent opinion={b_own_dir} (confidence={b_own_conf}, H4={b_macro}). "
                f"The engines independently read the market in opposite directions."
            )
        elif b_own_dir is None:
            conflict_type = "inherited_direction"
            conflict_explanation = (
                f"INHERITED conflict: Engine B returned direction={b_dir} from caller context, "
                f"but its own structural evidence is inconclusive (H4={b_macro}, H1={b_swing}). "
                f"Engine A favors {a_dir}. This may resolve once structure clarifies."
            )
        else:
            conflict_type = "inherited_direction"
            conflict_explanation = (
                f"INHERITED conflict: Engine B ran against {b_dir} but its own "
                f"structural opinion ({b_own_dir}, {b_own_conf}) is inconclusive or matches A. "
                f"Source of conflict likely candle window or zone detection boundary."
            )
        conflict_explanation += (
            f" | A: norm={a_norm} | B: norm={b_norm_val} "
            f"struct={b_struct_ok} zone={b_zone_ok} trigger={b_trigger_ok}"
        )

    elif verdict == "NO_SIGNAL":
        conflict_type = "missing_signal"
        conflict_explanation = "Neither engine produced a tradeable signal."

    return {
        "verdict_explanation": f"{verdict}: {conflict_explanation[:120]}" if conflict_explanation else verdict,
        "a_summary": a_summary,
        "b_summary": b_summary,
        "b_independent_direction": b_ind_dir,
        "conflict_type": conflict_type,
        "conflict_explanation": conflict_explanation,
    }


def normalise_engine_a(signal_a: dict) -> dict:
    """Normalise Engine A output to 0-1 scale.

    Engine A outputs:
      - confluenceScore: raw score (0 to ~3.0)
      - maxScore: maximum possible (dynamic, depends on active factors)
      - direction: LONG/SHORT
      - regime: dict with label, state
      - sl, tp1, tp2: ATR-based levels
      - confluencePct: threshold-relative display percentage (UI only)
    """
    score = float(signal_a.get("confluenceScore", 0))
    _ms_raw = signal_a.get("maxScore")
    if _ms_raw is None:
        max_score = _default_engine_a_max_score(signal_a)
    else:
        try:
            max_score = float(_ms_raw)
        except (TypeError, ValueError):
            max_score = _default_engine_a_max_score(signal_a)
        if max_score <= 0:
            max_score = _default_engine_a_max_score(signal_a)
    score_norm = signal_a.get("scoreNorm")
    try:
        norm = float(score_norm) if score_norm is not None else None
    except (TypeError, ValueError):
        norm = None
    if norm is None:
        norm = min(1.0, score / max_score) if max_score > 0 else 0.0
    norm = max(0.0, min(1.0, norm))

    # No forex-specific rescale. All asset classes (Engine A v2) use 0-3.0 scale.
    # norm = score / max_score keeps normalization simple and asset-consistent.

    regime_data = signal_a.get("regime", {})
    if isinstance(regime_data, dict):
        regime_label = regime_data.get("label", regime_data.get("regime", "RANGING"))
    elif isinstance(regime_data, str):
        regime_label = regime_data
    else:
        regime_label = "RANGING"

    votes = signal_a.get("votes", {})
    factor_scores = signal_a.get("factor_scores", {})
    cot_active = any([
        votes.get("FACTOR_DERIVATIVES"),
        votes.get("COT Boost"),
        factor_scores.get("derivatives"),
        factor_scores.get("cot_boost")
    ])
    carry_active = any([
        votes.get("FACTOR_CARRY"),
        votes.get("Carry Tilt"),
        factor_scores.get("carry"),
        factor_scores.get("carry_tilt")
    ])

    # Floor for signal participation: norm must exceed the floor to count as a full signal.
    # All asset classes use 0-3.0 scale (Engine A v2): score > 0.90 to participate.
    _a_has_floor = float(CONFIG.get("ENGINE_C_A_HAS_FLOOR", 0.30))
    _a_partial_floor = float(CONFIG.get("ENGINE_C_A_PARTIAL_FLOOR", 0.20))
    _dir_ok = signal_a.get("direction") in ("LONG", "SHORT")
    _is_full = norm > _a_has_floor and _dir_ok
    _is_partial = (not _is_full) and norm > _a_partial_floor and _dir_ok
    _cf = None
    _cdetail = signal_a.get("confidenceDetail")
    if isinstance(_cdetail, dict) and _cdetail.get("confidence") is not None:
        try:
            _cf = float(_cdetail["confidence"])
        except (TypeError, ValueError):
            _cf = None
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
        "has_signal": _is_full,
        "has_partial_signal": _is_partial,
        "cot_active": bool(cot_active),
        "carry_active": bool(carry_active),
        "style": signal_a.get("style", signal_a.get("tradeStyle", "swing")),
        "confidence": _cf,
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
    passed_raw = conf.get("passed")
    passed = passed_raw is True
    high_enough = norm > 0.2
    valid_direction = direction in ("LONG", "SHORT")
    diagnostic = ""
    if verdict == "CLEAR" and high_enough and valid_direction and not passed:
        diagnostic = (
            "engine_b_checklist_missing_passed"
            if "passed" not in conf
            else "engine_b_checklist_failed"
        )
    elif verdict != "CLEAR":
        diagnostic = "engine_b_verdict_not_clear"
    elif not valid_direction:
        diagnostic = "engine_b_direction_missing"
    elif not high_enough:
        diagnostic = "engine_b_confidence_below_threshold"

    return {
        "score_norm": round(norm, 4),
        "direction": direction,
        "sl": conf.get("execution_sl") or signal_b.get("recommended_stop_loss"),
        "tp": conf.get("execution_tp") or signal_b.get("recommended_take_profit"),
        "rr": float(conf.get("rr_used_for_gate") or conf.get("rr", 0)),
        "raw_score": score,
        "max_possible": max_possible,
        "has_signal": verdict == "CLEAR" and high_enough and valid_direction and passed,
        "passed": passed,
        "checklist_passed": passed,
        "signal_diagnostic": diagnostic,
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
        elif dist_b == 0:
            log.debug(
                "[ENGINE_C] resolve_sl: structural SL=%s equals entry=%s — dropped (zero distance)",
                sl_b, entry,
            )
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
    if not entry or entry <= 0:
        return {"tp": None, "rr": 0, "method": "NO_DATA"}
    if sl is None or sl <= 0:
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


def _blend_consensus_weights(base_weights: dict, meta_weights: dict | None) -> dict:
    """Conservatively tilt A/B consensus weights toward recent meta trust."""
    meta = meta_weights or {}
    a_meta = float(meta.get("engine_a", 0.0) or 0.0)
    b_meta = float(meta.get("engine_b", 0.0) or 0.0)
    total = a_meta + b_meta
    if total <= 0:
        return dict(base_weights)
    meta_ratio_a = a_meta / total
    meta_ratio_b = b_meta / total
    _mb = _engine_c_meta_blend()
    blended = {
        "A": (base_weights["A"] * (1.0 - _mb)) + (meta_ratio_a * _mb),
        "B": (base_weights["B"] * (1.0 - _mb)) + (meta_ratio_b * _mb),
    }
    total_blended = blended["A"] + blended["B"]
    return {
        "A": round(blended["A"] / total_blended, 4) if total_blended else round(base_weights["A"], 4),
        "B": round(blended["B"] / total_blended, 4) if total_blended else round(base_weights["B"], 4),
    }


def _parse_style_ratings_from_text(text: str) -> dict:
    """Extract per-style vision ratings from chart-analysis text.

    Looks for patterns like:
        SCALP RATING: STRONG
        INTRADAY RATING: MODERATE
        SWING RATING: AVOID
    """
    import re
    ratings = {}
    valid = {"STRONG", "MODERATE", "WEAK", "AVOID", "CONTRADICTS"}
    for style in ("SCALP", "INTRADAY", "SWING"):
        pattern = rf"{style}\s+RATING\s*:\s*(STRONG|MODERATE|WEAK|AVOID|CONTRADICTS?)"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1).upper()
            if val == "CONTRADICT":
                val = "CONTRADICTS"
            if val in valid:
                ratings[style.lower()] = val
    return ratings


def apply_vision(consensus: dict, vision_result: dict) -> dict:
    """Apply AI Vision verdict to modify consensus conviction.

    Vision modes:
    - CONFIRM: boosts conviction (STRONG/MODERATE)
    - WEAKEN: reduces conviction (WEAK)
    - CONTRADICT: kills the trade
    - OVERRIDE/AVOID: hard veto regardless of engines

    Per-style ratings (parsed from text):
    - SCALP RATING: STRONG/MODERATE/WEAK/AVOID/CONTRADICTS
    - INTRADAY RATING: ...
    - SWING RATING: ...
    Stored in vision_style_ratings for per-button gating.
    """
    if not vision_result:
        return consensus

    updated = dict(consensus)
    try:
        from ai_reconciliation import arbitrate_ai

        _ai_decision = arbitrate_ai({"vision": vision_result}, require_trace_id=False)
        updated["ai_arbitration"] = _ai_decision
        _vision_state = (_ai_decision.get("source_states") or {}).get("vision") or {}
        if _vision_state.get("category") in ("invalid", "failure"):
            updated["trade"] = False
            updated["verdict"] = _ai_decision.get("reason", "VISION_AI_BLOCK")
            updated["tier"] = "SKIP"
            updated["sizing_override"] = 0.0
            updated["decision_state"] = "blocked"
            updated["vision_applied"] = True
            updated["vision_action"] = "block"
            updated["vision_rating"] = _vision_state.get("raw") or "INVALID"
            return updated
    except Exception as _arb_err:
        log.warning("[AI_ARBITRATION] Vision arbitration failed (fail-closed): %s", _arb_err)
        updated["trade"] = False
        updated["verdict"] = "VISION_ARBITRATION_ERROR"
        updated["tier"] = "SKIP"
        updated["sizing_override"] = 0.0
        updated["decision_state"] = "blocked"
        updated["vision_applied"] = True
        updated["vision_action"] = "block"
        updated["vision_rating"] = "ARBITRATION_FAILED"
        return updated

    # Parse structured data if available
    structured = vision_result.get("structured") or {}
    rating = structured.get("rating", "").upper()
    confirms_dir = structured.get("confirms_direction", False)
    sl_flag = structured.get("sl_flag", "ok")
    tp_flag = structured.get("tp_flag", "ok")

    text = (vision_result.get("analysis") or "").upper()

    # Parse per-style ratings from Vision text
    style_ratings = _parse_style_ratings_from_text(text)

    # Dual-TF: if AI reports TF ALIGNMENT: CONFLICTED, treat as hard contradiction
    import re as _re
    tf_align_match = _re.search(r"TF\s+ALIGNMENT\s*:\s*(ALIGNED|CONFLICTED)", text)
    if tf_align_match and tf_align_match.group(1) == "CONFLICTED":
        confirms_dir = False
        rating = "CONTRADICTS"

    # If no structured rating, extract the overall rating from text using word boundaries
    if not rating:
        for r in ["AVOID", "STRONG", "MODERATE", "WEAK", "CONTRADICTS"]:
            if _re.search(rf"\b{r}\b", text):
                rating = r
                break
        if _re.search(r"\bCONTRADICT\b", text) or _re.search(r"\bCONFLICTED\b", text):
            confirms_dir = False
            rating = "CONTRADICTS"

    # If we have per-style ratings but no overall, derive overall from best style
    if not rating and style_ratings:
        priority = ["STRONG", "MODERATE", "WEAK", "AVOID", "CONTRADICTS"]
        for p in priority:
            if p in style_ratings.values():
                rating = p
                break

    if not rating:
        rating = "AVOID"

    # Handle explicit direction contradiction
    if not confirms_dir and rating not in ("AVOID", "CONTRADICTS"):
        rating = "CONTRADICTS"

    # Apply modifier
    modifiers = _vision_modifiers()
    modifier = modifiers.get(rating, modifiers["MODERATE"])
    action = str(modifier["action"]).lower()
    mult = float(modifier["conviction_mult"])

    # Hard veto ratings and actions — YAML cannot weaken these to a non-veto path.
    if rating in ("AVOID", "CONTRADICTS"):
        action = "override" if rating == "AVOID" else "contradict"
        mult = 0.0
    elif action in ("override", "contradict"):
        mult = 0.0

    old_conviction = updated.get("conviction", 0.0)
    if "conviction" not in updated:
        log.warning("[ENGINE_C] apply_vision called on consensus without conviction key; defaulting to 0.0")
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
    updated["vision_style_ratings"] = style_ratings or {
        "scalp": rating, "intraday": rating, "swing": rating,
    }
    updated["vision_level_suggestions"] = structured.get("level_suggestions") or {}

    # If Vision flags SL as too tight, check if we can widen
    if sl_flag == "too_tight" and updated.get("sl_method") != "atr":
        if updated.get("sl_a") and updated.get("sl"):
            a_dist = abs(updated.get("entry", 0) - updated["sl_a"])
            c_dist = abs(updated.get("entry", 0) - updated["sl"])
            if a_dist > c_dist:
                updated["sl"] = updated["sl_a"]
                updated["sl_method"] = "atr_vision_override"
                risk = abs(updated["entry"] - updated["sl"])
                if risk > 0 and updated.get("tp"):
                    reward = abs(updated["tp"] - updated["entry"])
                    updated["rr"] = round(reward / risk, 2)

    # Vision confirm path — compile-time kill switch overrides YAML (Audit CRIT-001).
    _vision_upgrade_min = float(CONFIG.get("ENGINE_C_VISION_UPGRADE_MIN_CONVICTION", 0.35))
    if action == "confirm" and new_conviction >= _vision_upgrade_min:
        _cfg_upgrade = bool(CONFIG.get("AI_VISION_CAN_UPGRADE_TRADE", False))
        if _cfg_upgrade and AISafetyConstants.DISABLE_AI_VISION_UPGRADE_PATH:
            log.critical(
                "[ENGINE_C] Config AI_VISION_CAN_UPGRADE_TRADE=True is OVERRIDDEN by "
                "AISafetyConstants.DISABLE_AI_VISION_UPGRADE_PATH — Chart Vision cannot "
                "upgrade trades (Audit CRIT-001)."
            )
        if (
            _cfg_upgrade
            and not AISafetyConstants.DISABLE_AI_VISION_UPGRADE_PATH
        ):
            # Sanctioned upgrade (requires DISABLE_AI_VISION_UPGRADE_PATH=False in source).
            updated["trade"] = True
        else:
            # Veto-only / downgrade-only: CONFIRM records support flags but cannot create trade=True.
            updated["ai_visual_confirmed"] = True
            updated["vision_supports_setup"] = True

    if action in ("override", "contradict"):
        updated["trade"] = False
        updated["verdict"] = f"VISION_{action.upper()}"
        updated["tier"] = "SKIP"
        updated["sizing_override"] = 0.0
        updated["decision_state"] = "blocked"

    log.warning(
        f"[ENGINE C] Vision {action}: {rating} → conviction {old_conviction:.2f} → {new_conviction:.2f} "
        f"(tier={tier}, sl_flag={sl_flag}, styles={style_ratings})"
    )

    return updated


def _finalize_consensus(result: dict, asset_type: str) -> dict:
    calibrated = predict_calibrated_prob(
        result.get("conviction"),
        engine="engine_c",
        asset_class=asset_type,
        style=result.get("style"),
        max_score=1.0,
    )
    result["calibratedProbability"] = calibrated.get("calibrated_prob")
    result["calibration"] = calibrated
    if isinstance(result.get("metaPolicy"), dict):
        result = apply_meta_policy(result, result.get("metaPolicy"))

    try:
        comps = result.get("components", {}) if isinstance(result, dict) else {}
        record_signal_event(
            engine="engine_c",
            score=result.get("conviction"),
            max_score=1.0,
            passed=bool(result.get("trade")),
            expected_prob=result.get("calibratedProbability", result.get("conviction")),
            feature_map={
                "a_norm": comps.get("a_norm"),
                "b_norm": comps.get("b_norm"),
                "a_has_signal": comps.get("a_has_signal"),
                "b_has_signal": comps.get("b_has_signal"),
                "b_bos": comps.get("b_bos"),
                "b_ob_at_zone": comps.get("b_ob_at_zone"),
                "rr": result.get("rr"),
            },
            meta={
                "asset_type": asset_type,
                "tier": result.get("tier"),
                "verdict": result.get("verdict"),
                "runtime_only_metric": False,
            },
        )
    except Exception as exc:
        log.debug(f"[SSI] Engine C sample skipped: {exc}")
    return result


def _maybe_apply_engine_c_ai_weight_verdict(
    result: dict,
    signal_a: dict,
    signal_b: dict,
    confidence_b: Optional[dict],
    regime: str,
    asset_type: str,
) -> dict:
    """Optional xAI trust verdict (advisory); optional conviction tweak if ADJUST enabled."""
    if not CONFIG.get("ENGINE_C_AI_WEIGHT_VERDICT_ENABLED", False):
        return result
    if not isinstance(result, dict):
        return result
    try:
        from engine_c_ai import (
            apply_engine_c_ai_weight_adjustment,
            get_engine_c_weight_verdict,
            normalize_engine_c_ai_weight_verdict,
        )

        base = ENGINE_C_AB_WEIGHTS.get(regime, {"A": 0.50, "B": 0.50})
        snap = {
            "verdict": result.get("verdict"),
            "trade": result.get("trade"),
            "conviction": result.get("conviction"),
            "tier": result.get("tier"),
            "decision_state": result.get("decision_state"),
            "engine_weights": result.get("engine_weights"),
        }
        wv = get_engine_c_weight_verdict(
            signal_a or {},
            signal_b or {},
            snap,
            confidence_b,
            regime,
            asset_type,
            base_weights=base,
        )
        if not wv.get("error"):
            w_min = float(CONFIG.get("ENGINE_C_AI_WEIGHT_MIN", 0.20) or 0.20)
            w_max = float(CONFIG.get("ENGINE_C_AI_WEIGHT_MAX", 0.80) or 0.80)
            wv = normalize_engine_c_ai_weight_verdict(wv, w_min=w_min, w_max=w_max)
        result["ai_weight_verdict"] = wv
        if wv.get("error"):
            return result
        if CONFIG.get("ENGINE_C_AI_WEIGHT_ADJUST_ENABLED", False):
            result = apply_engine_c_ai_weight_adjustment(result, wv)
    except Exception as exc:
        log.debug("[ENGINE_C_AI] weight verdict skipped: %s", exc)
    return result


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

    # Reliability / conviction thresholds (configurable)
    _REL_EXEC = float(CONFIG.get("ENGINE_C_REL_EXECUTE", 0.60))
    _CONV_EXEC = float(CONFIG.get("ENGINE_C_CONV_EXECUTE", 0.65))
    _REL_RR = float(CONFIG.get("ENGINE_C_REL_REDUCED_RISK", 0.45))
    _CONV_RR = float(CONFIG.get("ENGINE_C_CONV_REDUCED_RISK", 0.50))
    _CONV_WATCH = float(CONFIG.get("ENGINE_C_CONV_WATCHLIST", 0.40))
    _CONV_WATCH_CONS = float(CONFIG.get("ENGINE_C_CONV_WATCHLIST_CONSENSUS", 0.50))
    _WEAK_A_CONV_RR = float(CONFIG.get("ENGINE_C_WEAK_A_CONV_REDUCED_RISK", 0.65))
    _WEAK_A_CONV_RR2 = float(CONFIG.get("ENGINE_C_WEAK_A_CONV_REDUCED_RISK2", 0.50))
    _SIZING_DELTA_RR = float(CONFIG.get("ENGINE_C_SIZING_DELTA_REDUCED_RISK", 0.25))
    _SIZING_DELTA_RR_WEAK = float(CONFIG.get("ENGINE_C_SIZING_DELTA_REDUCED_RISK_WEAK", 0.35))

    # Step 2: Determine signal availability
    # When A has a partial signal (norm between partial_floor and has_floor) and
    # B also has a signal, promote A to participate at half weight so borderline
    # trend signals are not silently dropped from the consensus blend.
    a_has = a["has_signal"]
    a_partial = a.get("has_partial_signal", False) and not a_has
    b_has = b["has_signal"]
    if a_partial and b_has:
        a_has = True
        a = dict(a)
        a["score_norm"] = a["score_norm"] * 0.5  # half-weight for partial

    # Neither engine has a signal
    if not a_has and not b_has:
        return _finalize_consensus(_build_result(
            trade=False, verdict="NO_SIGNAL", direction=None,
            conviction=0.0, tier="SKIP", sizing=0.0,
            a_norm=a, b_norm=b, entry=entry,
            disagreement_diagnosis=_build_disagreement_diagnosis(
                "NO_SIGNAL", a, b, signal_a, signal_b, regime
            ),
        ), asset_type)

    # Only one engine has signal
    if a_has and not b_has:
        # Engine A only — allow at reduced conviction
        direction = a["direction"]
        conviction = a["score_norm"] * 0.6  # 60% of A score (no B confirmation)
        tier, sizing = classify_conviction(conviction)

        # C-2: If Engine B shows near-zero structural presence, A-only is a coin-flip
        # (Engine A directional hit-rate 42.3% — 2026-04-18 audit). Gate reduced_risk
        # (trade=True) behind a minimum B structural norm floor. Watchlist is still
        # allowed so the signal surfaces for manual review.
        b_partial_norm = b["score_norm"]
        _b_floor_met = b_partial_norm >= 0.10

        sl_resolved = resolve_sl(entry, a["sl"], None, direction, atr)
        tp_resolved = resolve_tp(entry, sl_resolved["sl"], a["tp"], None, direction)

        # Reliability Layer for A_ONLY
        a_quality = 0.50 # fallback
        a_reliability = _engine_a_reliability(a, a_quality)
        c_reliability = a_reliability # 100% allocation to A

        decision_state = "blocked"
        decision_state_reason = None
        if tier != "SKIP":
            if c_reliability >= _REL_EXEC and conviction >= _CONV_EXEC and _b_floor_met:
                decision_state = "execute"
            elif c_reliability >= _REL_RR and conviction >= _CONV_RR and _b_floor_met:
                decision_state = "reduced_risk"
                sizing = max(0.0, sizing - _SIZING_DELTA_RR)
            elif conviction >= _CONV_WATCH:
                decision_state = "watchlist"
                if not _b_floor_met:
                    decision_state_reason = f"b_partial_norm={b_partial_norm:.3f}<0.10"
            else:
                if not _b_floor_met:
                    decision_state_reason = f"b_partial_norm={b_partial_norm:.3f}<0.10"
                elif c_reliability < 0.45:
                    decision_state_reason = f"reliability={c_reliability:.3f}<0.45"
                elif conviction < 0.40:
                    decision_state_reason = f"conviction={conviction:.3f}<0.40"

        if not _b_floor_met and decision_state in ("execute", "reduced_risk"):
            decision_state = "watchlist"
            decision_state_reason = f"b_partial_norm={b_partial_norm:.3f}<0.10"

        if decision_state == "blocked":
            tier = "SKIP"
            sizing = 0.0
        elif decision_state == "watchlist":
            tier = "WATCHLIST"
            sizing = 0.0

        result = _build_result(
            trade=decision_state in ("execute", "reduced_risk"),
            verdict="A_ONLY",
            direction=direction,
            conviction=conviction,
            tier=tier,
            sizing=sizing,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            rr=tp_resolved["rr"],
            decision_state=decision_state,
            decision_state_reason=decision_state_reason,
            a_reliability=a_reliability,
            b_reliability=0.0,
            c_reliability=c_reliability,
            disagreement_diagnosis=_build_disagreement_diagnosis(
                "A_ONLY", a, b, signal_a, signal_b, regime
            ),
        )
        result = _maybe_apply_engine_c_ai_weight_verdict(
            result, signal_a, signal_b, confidence_b, regime, asset_type
        )
        if ai_vision:
            result = apply_vision(result, ai_vision)
        return _finalize_consensus(result, asset_type)

    if b_has and not a_has:
        direction = b["direction"]
        if b.get("sl") is None or b.get("tp") is None:
            return _build_result(
                trade=False,
                verdict="B_ONLY_LEVELS_MISSING",
                direction=direction,
                conviction=0.0,
                tier="SKIP",
                sizing=0.0,
                a_norm=a, b_norm=b, entry=entry,
                sl=None, sl_method="no_levels",
                tp=None, tp_method="no_levels",
                rr=0.0,
                weights={"A": 0.0, "B": 1.0},
                decision_state="blocked",
                decision_state_reason="engine_b_levels_missing",
                a_reliability=0.0,
                b_reliability=0.0,
                c_reliability=0.0,
                disagreement_diagnosis=_build_disagreement_diagnosis(
                    "B_ONLY", a, b, signal_a, signal_b, regime
                ),
            )
        b_only_mult = float(CONFIG.get("ENGINE_C_B_ONLY_MULT", 0.65))
        conviction = b["score_norm"] * b_only_mult
        tier, sizing = classify_conviction(conviction)

        sl_resolved = resolve_sl(entry, None, b["sl"], direction, atr)
        tp_resolved = resolve_tp(entry, sl_resolved["sl"], None, b["tp"], direction)
        if tp_resolved["rr"] < 1.0:
            tier = "SKIP"
            sizing = 0.0

        # Reliability Layer for B_ONLY
        b_quality = 0.50 # fallback
        b_reliability = _compute_b_reliability(b, b_quality)
        c_reliability = b_reliability
        
        decision_state = "blocked"
        if tier != "SKIP":
            if c_reliability >= _REL_EXEC and conviction >= _CONV_EXEC:
                decision_state = "execute"
            elif c_reliability >= _REL_RR and conviction >= _CONV_RR:
                decision_state = "reduced_risk"
                sizing = max(0.0, sizing - _SIZING_DELTA_RR)
            elif conviction >= 0.40:
                decision_state = "watchlist"
                
        if decision_state == "blocked":
            tier = "SKIP"
            sizing = 0.0
        elif decision_state == "watchlist":
            tier = "WATCHLIST"
            sizing = 0.0
            
        result = _build_result(
            trade=decision_state in ("execute", "reduced_risk"),
            verdict="B_ONLY_SCORED" if tier not in ("SKIP", "WATCHLIST") else "B_ONLY",
            direction=direction,
            conviction=conviction,
            tier=tier,
            sizing=sizing,
            a_norm=a, b_norm=b, entry=entry,
            sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
            tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
            weights={"A": 0.0, "B": 1.0},
            rr=tp_resolved["rr"],
            decision_state=decision_state,
            a_reliability=0.0,
            b_reliability=b_reliability,
            c_reliability=c_reliability,
            disagreement_diagnosis=_build_disagreement_diagnosis(
                "B_ONLY", a, b, signal_a, signal_b, regime
            ),
        )
        result = _maybe_apply_engine_c_ai_weight_verdict(
            result, signal_a, signal_b, confidence_b, regime, asset_type
        )
        if ai_vision:
            result = apply_vision(result, ai_vision)
            # Preserve explicit upgraded label when vision confirms an already-valid B-only setup.
            if result.get("vision_action") == "confirm" and result["trade"]:
                result["verdict"] = "B_ONLY_VISION_CONFIRMED"
        return _finalize_consensus(result, asset_type)

    # Step 3: Both engines have signals — check direction agreement
    if a["direction"] != b["direction"]:
        # Both engines strongly disagree (high normalised scores, opposite directions) — not a proven regime change.
        opposing_high_confidence = a["score_norm"] > 0.70 and b["score_norm"] > 0.70
        allow_b_override = bool(CONFIG.get("ENGINE_C_B_CONFLICT_OVERRIDE_ENABLED", True))
        b_override_min = float(CONFIG.get("ENGINE_C_B_CONFLICT_MIN_SCORE", 0.70))
        a_override_max = float(CONFIG.get("ENGINE_C_A_CONFLICT_MAX_SCORE", 0.45))
        b_override_penalty = float(CONFIG.get("ENGINE_C_B_CONFLICT_PENALTY", 0.85))

        # If B is clearly strong while A is weak/opposed, let Engine C score B with a penalty
        # instead of discarding to zero-conviction conflict.
        if (
            allow_b_override
            and not opposing_high_confidence
            and b["score_norm"] >= b_override_min
            and a["score_norm"] <= a_override_max
        ):
            direction = b["direction"]
            conviction = min(1.0, b["score_norm"] * b_override_penalty)
            tier, sizing = classify_conviction(conviction)

            sl_resolved = resolve_sl(entry, None, b["sl"], direction, atr)
            tp_resolved = resolve_tp(entry, sl_resolved["sl"], None, b["tp"], direction)
            if tp_resolved["rr"] < 1.0:
                tier = "SKIP"
                sizing = 0.0

            b_quality = 0.50
            b_reliability = _compute_b_reliability(b, b_quality)
            c_reliability = b_reliability

            decision_state = "blocked"
            if tier != "SKIP":
                if c_reliability >= _REL_EXEC and conviction >= _CONV_EXEC:
                    decision_state = "execute"
                elif c_reliability >= _REL_RR and conviction >= _CONV_RR:
                    decision_state = "reduced_risk"
                    sizing = max(0.0, sizing - _SIZING_DELTA_RR)
                elif conviction >= 0.40:
                    decision_state = "watchlist"
                    
            if decision_state == "blocked":
                tier = "SKIP"
                sizing = 0.0
            elif decision_state == "watchlist":
                tier = "WATCHLIST"
                sizing = 0.0

            result = _build_result(
                trade=decision_state in ("execute", "reduced_risk"),
                verdict="B_OVERRIDE_CONFLICT",
                direction=direction,
                conviction=conviction,
                tier=tier,
                sizing=sizing,
                a_norm=a, b_norm=b, entry=entry,
                sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
                tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
                rr=tp_resolved["rr"],
                weights={"A": 0.0, "B": 1.0},
                regime=regime,
                opposing_high_confidence=opposing_high_confidence,
                decision_state=decision_state,
                a_reliability=0.0,
                b_reliability=b_reliability,
                c_reliability=c_reliability,
                disagreement_diagnosis=_build_disagreement_diagnosis(
                    "B_OVERRIDE_CONFLICT", a, b, signal_a, signal_b, regime
                ),
            )
            result = _maybe_apply_engine_c_ai_weight_verdict(
                result, signal_a, signal_b, confidence_b, regime, asset_type
            )
            if ai_vision:
                result = apply_vision(result, ai_vision)
            return _finalize_consensus(result, asset_type)

        # Mirror: A-override when A is strong and B is weak.
        # Config-gated (default off) because Engine A directional hit-rate is 42.3%.
        allow_a_override = bool(CONFIG.get("ENGINE_C_A_CONFLICT_OVERRIDE_ENABLED", False))
        a_override_min = float(CONFIG.get("ENGINE_C_A_CONFLICT_MIN_SCORE", 0.70))
        b_override_max_for_a = float(CONFIG.get("ENGINE_C_B_CONFLICT_MAX_SCORE_FOR_A", 0.45))
        a_override_penalty = float(CONFIG.get("ENGINE_C_A_CONFLICT_PENALTY", 0.80))
        if (
            allow_a_override
            and not opposing_high_confidence
            and a["score_norm"] >= a_override_min
            and b["score_norm"] <= b_override_max_for_a
        ):
            direction = a["direction"]
            conviction = min(1.0, a["score_norm"] * a_override_penalty)
            tier, sizing = classify_conviction(conviction)

            sl_resolved = resolve_sl(entry, a["sl"], None, direction, atr)
            tp_resolved = resolve_tp(entry, sl_resolved["sl"], a["tp"], None, direction)
            if tp_resolved["rr"] < 1.0:
                tier = "SKIP"
                sizing = 0.0

            a_quality = 0.50
            a_reliability = _engine_a_reliability(a, a_quality)
            c_reliability = a_reliability

            decision_state = "blocked"
            if tier != "SKIP":
                if c_reliability >= _REL_EXEC and conviction >= _CONV_EXEC:
                    decision_state = "execute"
                elif c_reliability >= _REL_RR and conviction >= _CONV_RR:
                    decision_state = "reduced_risk"
                    sizing = max(0.0, sizing - _SIZING_DELTA_RR)
                elif conviction >= _CONV_WATCH:
                    decision_state = "watchlist"

            if decision_state == "blocked":
                tier = "SKIP"
                sizing = 0.0
            elif decision_state == "watchlist":
                tier = "WATCHLIST"
                sizing = 0.0

            result = _build_result(
                trade=decision_state in ("execute", "reduced_risk"),
                verdict="A_OVERRIDE_CONFLICT",
                direction=direction,
                conviction=conviction,
                tier=tier,
                sizing=sizing,
                a_norm=a, b_norm=b, entry=entry,
                sl=sl_resolved["sl"], sl_method=sl_resolved["method"],
                tp=tp_resolved["tp"], tp_method=tp_resolved["method"],
                rr=tp_resolved["rr"],
                weights={"A": 1.0, "B": 0.0},
                regime=regime,
                opposing_high_confidence=opposing_high_confidence,
                decision_state=decision_state,
                a_reliability=a_reliability,
                b_reliability=0.0,
                c_reliability=c_reliability,
                disagreement_diagnosis=_build_disagreement_diagnosis(
                    "A_OVERRIDE_CONFLICT", a, b, signal_a, signal_b, regime
                ),
            )
            result = _maybe_apply_engine_c_ai_weight_verdict(
                result, signal_a, signal_b, confidence_b, regime, asset_type
            )
            if ai_vision:
                result = apply_vision(result, ai_vision)
            return _finalize_consensus(result, asset_type)

        _conflict_result = _build_result(
            trade=False,
            verdict="OPPOSING_HIGH_CONFIDENCE" if opposing_high_confidence else "DIRECTION_CONFLICT",
            direction=None,
            conviction=0.0,
            tier="SKIP",
            sizing=0.0,
            a_norm=a, b_norm=b, entry=entry,
            opposing_high_confidence=opposing_high_confidence,
            disagreement_diagnosis=_build_disagreement_diagnosis(
                "OPPOSING_HIGH_CONFIDENCE" if opposing_high_confidence else "DIRECTION_CONFLICT",
                a, b, signal_a, signal_b, regime
            ),
        )
        _conflict_result = _maybe_apply_engine_c_ai_weight_verdict(
            _conflict_result, signal_a, signal_b, confidence_b, regime, asset_type
        )
        return _finalize_consensus(_conflict_result, asset_type)

    # Step 4: Direction agrees — compute weighted conviction
    direction = a["direction"]

    # Stage 3.1: Explicit Engine A × Engine B interaction rules.
    # Engine A directional hit-rate is 42.3%. If A is weak (score_norm < 0.30),
    # B cannot rescue it to a full execute — cap at reduced_risk or watchlist.
    # This prevents B's structural narrative from overriding A's weak quantitative
    # evidence and creating false-confidence trades.
    _a_weak_threshold = float(CONFIG.get("ENGINE_C_A_WEAK_THRESHOLD", 0.30))
    _a_is_weak = a["score_norm"] < _a_weak_threshold

    # Get regime-conditional A/B blend (see ENGINE_C_AB_WEIGHTS; distinct from CONFIG REGIME_WEIGHTS).
    base_weights = ENGINE_C_AB_WEIGHTS.get(regime, {"A": 0.50, "B": 0.50})
    meta_context = get_engine_context(
        {
            "engine": "engine_c",
            "type": asset_type,
            "style": a.get("style"),
            "regime": regime,
            "direction": direction,
            "volume_momentum_spread": signal_a.get("volume_momentum_spread"),
        }
    )
    meta_policy = {
        "context": {
            "signal_engine": meta_context.get("signal_engine"),
            "asset_class": meta_context.get("asset_class"),
            "style": meta_context.get("style"),
            "regime": meta_context.get("regime"),
            "system_ssi": (meta_context.get("signal_stability_index") or {}).get("system"),
            "spread_slippage_percentile": meta_context.get("spread_slippage_percentile"),
            "calibration_quality": meta_context.get("calibration_quality"),
            "vms_state": meta_context.get("vms_state"),
        },
        **get_dynamic_engine_weights(meta_context),
    }
    weights = _blend_consensus_weights(base_weights, meta_policy.get("weights"))

    conviction = (a["score_norm"] * weights["A"]) + (b["score_norm"] * weights["B"])
    conviction = min(1.0, conviction)
    _intermarket_confirmation = signal_a.get("intermarketConfirmation") or {}
    _intermarket_mult = engine_c_conviction_multiplier(
        _intermarket_confirmation,
        config=CONFIG,
    )
    conviction = max(0.0, min(1.0, conviction * _intermarket_mult))
    _intermarket_confirmation_payload = (
        dict(_intermarket_confirmation)
        if isinstance(_intermarket_confirmation, dict)
        else {}
    )
    if _intermarket_confirmation_payload:
        _intermarket_confirmation_payload["engineCMultiplier"] = round(
            float(_intermarket_mult),
            6,
        )

    # Extra emphasis when B shows MTF BOS / strong OB. Note: calculate_confidence() may already
    # count these as extra checklist rows (raising B_norm); multipliers here are deliberate stacking.
    if b["bos_mtf"]:
        conviction = min(1.0, conviction * 1.08)  # +8% for multi-TF BOS
    if b["ob_at_zone"] and b["ob_strength"] >= 70:
        conviction = min(1.0, conviction * 1.05)  # +5% for strong OB at zone

    tier, sizing = classify_conviction(conviction)

    # Step 5: Resolve SL and TP
    # When Engine B checklist passed, trust execution_sl/execution_tp — they already
    # satisfied rr_ok in market_structure.calculate_confidence (avoids resolve_tp's
    # structural_min_rr=1.5 and SL/TP blend changing gated RR).
    used_b_execution_bundle = False
    conf_b_al = confidence_b if isinstance(confidence_b, dict) else {}
    if bool(conf_b_al.get("passed")):
        try:
            _ex_sl = float(conf_b_al["execution_sl"])
            _ex_tp = float(conf_b_al["execution_tp"])
        except (KeyError, TypeError, ValueError):
            _ex_sl = _ex_tp = None
        if (
            _ex_sl is not None
            and _ex_tp is not None
            and _ex_sl > 0
            and _ex_tp > 0
            and entry > 0
        ):
            if direction == "LONG":
                _side_ok = _ex_sl < entry < _ex_tp
            else:
                _side_ok = _ex_sl > entry > _ex_tp
            if _side_ok:
                _risk_al = abs(entry - _ex_sl)
                if _risk_al > 0:
                    _rr_al = abs(_ex_tp - entry) / _risk_al
                    sl_resolved = {"sl": round(_ex_sl, 6), "method": "engine_b_execution"}
                    tp_resolved = {
                        "tp": round(_ex_tp, 6),
                        "rr": round(_rr_al, 2),
                        "method": "engine_b_execution",
                    }
                    used_b_execution_bundle = True

    if not used_b_execution_bundle:
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

    # --- Reliability Layer ---
    a_quality = meta_policy.get("engineQualities", {}).get("engine_a", {}).get("quality_score", 0.50)
    b_quality = meta_policy.get("engineQualities", {}).get("engine_b", {}).get("quality_score", 0.50)

    a_reliability = _engine_a_reliability(a, a_quality)
    b_reliability = _compute_b_reliability(b, b_quality)
    
    c_reliability = (a_reliability * weights["A"]) + (b_reliability * weights["B"])

    decision_state = "blocked"
    if tier != "SKIP":
        _intermarket_blocks = bool(
            CONFIG.get("INTERMARKET_CONFIRMATION", {}).get(
                "severe_contradiction_blocks", False
            )
        ) and (_intermarket_confirmation.get("contradictionFlags") or {}).get(
            "severeContradiction"
        )
        if _intermarket_blocks:
            decision_state = "blocked"
        # Stage 3.1: If A is weak, B cannot rescue to execute.
        elif _a_is_weak and conviction >= _WEAK_A_CONV_RR:
            # A weak but total conviction high → reduced_risk only
            decision_state = "reduced_risk"
            sizing = max(0.0, sizing - _SIZING_DELTA_RR_WEAK)
        elif _a_is_weak and conviction >= _WEAK_A_CONV_RR2:
            decision_state = "reduced_risk"
            sizing = max(0.0, sizing - 0.25)
        elif c_reliability >= _REL_EXEC and conviction >= _CONV_EXEC:
            decision_state = "execute"
        elif c_reliability >= 0.45 and conviction >= 0.50:
            decision_state = "reduced_risk"
            sizing = max(0.0, sizing - 0.25)
        elif conviction >= _CONV_WATCH_CONS:
            decision_state = "watchlist"

    if decision_state == "blocked":
        tier = "SKIP"
        sizing = 0.0
    elif decision_state == "watchlist":
        tier = "WATCHLIST"
        sizing = 0.0

    result = _build_result(
        trade=decision_state in ("execute", "reduced_risk"),
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
        meta_policy=meta_policy,
        meta_base_weights=base_weights,
        decision_state=decision_state,
        a_reliability=a_reliability,
        b_reliability=b_reliability,
        c_reliability=c_reliability,
        intermarket_confirmation=_intermarket_confirmation_payload,
        intermarket_multiplier=_intermarket_mult,
        a_is_weak=_a_is_weak,
        disagreement_diagnosis=_build_disagreement_diagnosis(
            "ALIGNED", a, b, signal_a, signal_b, regime
        ),
    )

    try:
        from ai_context import build_ai_calibration_context
        result["aiCalibrationContext"] = build_ai_calibration_context(result, engine_source="Engine C")
    except Exception as _ctx_err:
        log.debug("[ENGINE_C] Failed to generate AI calibration context: %s", _ctx_err)

    result = _maybe_apply_engine_c_ai_weight_verdict(
        result, signal_a, signal_b, confidence_b, regime, asset_type
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

    return _finalize_consensus(result, asset_type)


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
    decision_state: str = "blocked",
    a_reliability: float = 0.0,
    b_reliability: float = 0.0,
    c_reliability: float = 0.0,
    **kwargs,
) -> dict:
    """Build standardised consensus result dict."""
    # Derive signalTier and watchlistReason for UI compatibility
    signal_tier = tier.lower() if tier else "skip"
    watchlist_reason = ""
    if decision_state == "watchlist":
        signal_tier = "watchlist"
        watchlist_reason = f"conviction {round(conviction, 2)} below execute threshold"
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
        "signalTier": signal_tier,
        "watchlistReason": watchlist_reason,
        "decision_state": decision_state,
        "decision_state_reason": kwargs.get("decision_state_reason") or None,
        "sizing_override": round(sizing, 4),
        "engine_weights": weights or {},
        "engine_base_weights": kwargs.get("meta_base_weights") or {},
        "regime": regime,
        "style": a_norm.get("style") if a_norm else None,
        "opposing_high_confidence": opposing_high_confidence,
        "metaPolicy": kwargs.get("meta_policy"),
        "components": {
            "a_norm": round(a_norm["score_norm"], 4) if a_norm else 0.0,
            "a_direction": a_norm.get("direction") if a_norm else None,
            "a_has_signal": a_norm.get("has_signal", False) if a_norm else False,
            "a_cot_active": a_norm.get("cot_active", False) if a_norm else False,
            "b_norm": round(b_norm["score_norm"], 4) if b_norm else 0.0,
            "b_direction": b_norm.get("direction") if b_norm else None,
            "b_has_signal": b_norm.get("has_signal", False) if b_norm else False,
            "b_checklist_passed": b_norm.get("passed", False) if b_norm else False,
            "b_signal_diagnostic": b_norm.get("signal_diagnostic", "") if b_norm else "",
            "b_bos": b_norm.get("bos_confirmed", False) if b_norm else False,
            "b_ob_at_zone": b_norm.get("ob_at_zone", False) if b_norm else False,
            "b_sequence": b_norm.get("swing_sequence", "") if b_norm else "",
        },
        "vision_applied": False,
        "vision_action": None,
        "vision_rating": None,
        "vision_sl_flag": None,
        "vision_tp_flag": None,
        "intermarket_confirmation": kwargs.get("intermarket_confirmation") or {},
        "intermarket_multiplier": round(
            float(kwargs.get("intermarket_multiplier", 1.0) or 1.0),
            6,
        ),
        # Phase 3: Engine C disagreement diagnostics (display-only, no scoring effect)
        "disagreement_diagnosis": kwargs.get("disagreement_diagnosis") or {},
        "a_is_weak": kwargs.get("a_is_weak"),
    }
