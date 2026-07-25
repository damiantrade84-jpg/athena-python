"""
engine_b_ai.py - AI integration for Engine B (Naked Structure Engine)

Adapts Engine A's AI design pattern for Engine B structural analysis.
"""

import logging
import time
from typing import Optional

from ai_schemas import EngineBResponse
from ai_utils import parse_json_object
from config import CONFIG, AITemperatureConfig, create_ai_client, get_ai_model, resolve_ai_review_runtime
from prompt_store import load_prompt
from style_resolver import resolve_auto_style

log = logging.getLogger("athena")
VALID_ENGINE_B_GRADES = {"A+", "A", "B", "C", "D", "F"}
VALID_ENGINE_B_RISK_LEVELS = {"Low", "Medium", "High"}
REQUIRED_STYLE_KEYS = {"scalp", "intraday", "swing"}

_ENGINE_B_AI_EXPERT_PREFIX_FALLBACK = """You are Marcus Reid, a market-structure and execution-quality specialist.

Evaluate only the supplied price action, confirmed swings, structural breaks, liquidity events, imbalance, support and resistance zones, trigger quality, invalidation placement, target accessibility, current-price alignment, and structural reward-to-risk. SMC and ICT terminology is supporting classification, not proof.

ENGINE INDEPENDENCE:
Evaluate this engine using its own methodology. Information from other engines may be displayed as context but cannot change this engine's raw score, direction, eligibility, card status, SL, TP, or conviction.

EVIDENCE DISCIPLINE:
Use only supplied data. Missing evidence is unavailable — never neutral, positive, confirmed, or implicitly supportive. A coherent trading story does not increase conviction unless measurable supplied evidence supports it.

ENGINE B EVIDENCE HIERARCHY (higher overrides lower):
1. Confirmed swing structure
2. Current price relative to support and resistance zones
3. Space to the nearest opposing zone
4. Invalidation integrity
5. Target accessibility
6. Achievable structural reward-to-risk
7. Trigger quality
8. Sweeps, displacement, FVGs, order blocks, and secondary structural labels
Lower-priority concepts must not override higher-priority structural facts. Examples: an FVG must not justify a long entry inside resistance; a liquidity sweep must not justify a short entry inside support; a BOS must not justify a TP that requires trading through a major opposing zone; a trigger pattern must not justify an SL inside normal price noise.

OBJECTIVE PATTERN DEFINITIONS:
Do not infer or invent BOS, CHOCH, Sweep, FVG, Order block, Liquidity objective, Displacement, or Mitigation unless the supplied data objectively meets the implementation's defined criteria. Prefer the deterministic engine's criteria and flags over rediscovering patterns narratively.

ENGINE B PROHIBITIONS:
- Do not force every trade into SMC or ICT terminology.
- Do not create liquidity narratives after the fact.
- Do not treat a missing preferred pattern as automatic rejection.
- Do not move an SL or TP without explaining the exact structural basis.
- Do not ignore support_too_close, resistance_too_close, structural_tp_too_close, or entry inside/beyond a structural zone.
- Do not present theoretical RR as achievable when opposing structure blocks the path.
- Do not convert informational RR guidance into a new hard gate.
- Do not silently recalculate deterministic raw score, direction, zones, SL, TP, RR, or eligibility.

DETERMINISTIC AUTHORITY:
The deterministic engine remains source of truth for raw score, direction, structural zones, SL, TP, RR, eligibility, and card generation. AI may explain, audit, challenge, or flag inconsistencies only.

CURRENT-PRICE ALIGNMENT & STALE DATA:
Distinguish historical context, last confirmed candle, active candle, current executable price, and proposed entry. If current executable price has materially moved from signal construction price, report displacement and effects on structure/RR/SL/TP/conviction without auto-rejecting the card. Report only supplied timestamps; never fabricate them. Flag possible stale signal context when evidence supports it.

EVIDENCE STATUS:
State evidenceStatus inside reasoning as one of SUPPORTED, MIXED, INSUFFICIENT_DATA, INTERNALLY_INCONSISTENT. Keep the review concise: verdict, evidence status, main support, main contradiction, staleness/current-price warning if any, execution concern, final assessment.

STYLE & LEVELS:
Evaluate using Resolved AI style and Asset type from AI CALIBRATION CONTEXT. Do NOT judge Scalp by Swing criteria (or vice versa). For scale-out plans: compare RR1 to Engine B TP1 minimum RR and RR2 / rrUsedForGate to `Style min RR (config)` only; do NOT compare RR1 to style min RR when scaleOutActive=true and RR1 passes tp1MinRr with tp1PathClear=true. Do NOT invent thresholds. RR/SL/TP are deterministic engine outputs already gated by Python; treat RR as informational, not the primary grade driver. Review SL/TP structurally: distinguish structural invalidation SL from ATR/mechanical execution SL (executionSlTighterThanStructural=true is the normal design, not a levels defect); output levelsVerdict accept/adjust/reject with levelsReason citing zones/ATR; suggestedSL/suggestedTP only when adjust/reject. Do not automatically penalize Crypto for wide SL unless it exceeds MAX_SL_PCT.

ZONE / ROOM / TRIGGER RULES:
Engine B is zone-retest: when locationOk=true and entryOk=true, retest at the active zone is valid; do not reflex-downgrade as inside resistance/support. spaceGateOk is the authoritative deterministic room gate; roomOk=false alone is not an automatic reject when spaceGateOk=true via an approved and geometrically valid substitution or scale-out plan. support_too_close / resistance_too_close are warnings when spaceGateOk=true, but hard blockers when spaceGateOk=false. Reject or wait when tp1PathClear=false; such signals are also deterministically blocked. When a TP1 would overshoot the opposing zone Engine B clamps it to the wall's front edge (tp1ClampedToOpposingZone=true); the emitted TP1 is reachable, so do not reject it for the pre-clamp overshoot. Judge zones on zoneTf and triggers on triggerTf from server-trusted context; these resolved roles override the canonical playbook matrix, including M15/M30 live trigger overrides. Require triggerTimeframeGateOk=true when present and never substitute H1. Cite gateScore separately from graded qualityScore/qualityComponents; normalize with score/maxScore, never gatePct. Macro swing is always H4. Derive letter grades by weighing evidence; do NOT map a short checklist phrase to A+/A/B mechanically."""

_ENGINE_B_AI_EXPERT_PREFIX, _ENGINE_B_AI_PREFIX_SOURCE, _ENGINE_B_AI_PREFIX_HASH = load_prompt(
    "engine_b_ai_expert_prefix",
    fallback=_ENGINE_B_AI_EXPERT_PREFIX_FALLBACK,
)


def _is_retryable_ai_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    markers = (
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "connection refused",
        "temporarily unavailable",
        "server error",
        "rate limit",
        "too many requests",
        "502",
        "503",
        "504",
    )
    return any(marker in text for marker in markers)


def _call_ai_with_retry(client, model: str, messages: list, temperature: float):
    """Call AI with timeout and exponential backoff retry on transient provider failures.

    Prefers Pydantic-validated structured output via beta.chat.completions.parse;
    falls back to legacy json_object response_format if structured-output is unavailable.
    Returns a tuple (parsed_dict_or_none, raw_text) — parsed_dict_or_none is set when
    structured-output succeeded; raw_text is set when only the legacy path returned.
    """
    timeout_sec = float(CONFIG.get("ENGINE_B_AI_TIMEOUT_SEC", 30.0) or 30.0)
    max_retries = int(CONFIG.get("ENGINE_B_AI_MAX_RETRIES", 2) or 2)
    backoff_base = float(CONFIG.get("ENGINE_B_AI_RETRY_BACKOFF_SEC", 1.0) or 1.0)
    use_structured = bool(CONFIG.get("ENGINE_B_AI_STRUCTURED_OUTPUT", True))
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            if use_structured:
                try:
                    completion = client.beta.chat.completions.parse(
                        model=model,
                        max_tokens=800,
                        temperature=temperature,
                        messages=messages,
                        response_format=EngineBResponse,
                        timeout=timeout_sec,
                    )
                    parsed = completion.choices[0].message.parsed
                    if parsed is not None:
                        return parsed.model_dump(), None
                except Exception as _so_err:
                    log.debug("[ENGINE_B_AI] structured output unavailable: %s", _so_err)
            completion = client.chat.completions.create(
                model=model,
                max_tokens=800,
                temperature=temperature,
                messages=messages,
                response_format={"type": "json_object"},
                timeout=timeout_sec,
            )
            return None, (completion.choices[0].message.content or "").strip()
        except Exception as e:
            last_exc = e
            if attempt >= max_retries or not _is_retryable_ai_error(e):
                break
            sleep_sec = backoff_base * (2 ** attempt)
            log.warning(
                "[ENGINE_B_AI] transient AI failure on attempt %s/%s: %s; retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                e,
                sleep_sec,
            )
            time.sleep(sleep_sec)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("engine_b_ai retry exhausted without exception")


def _validate_engine_b_ai_payload(parsed: dict, pair: str) -> dict:
    """Validate and sanitize AI response fields after normalization."""
    out = dict(parsed)

    # edgeProbability must be numeric 0-100
    ep = out.get("edgeProbability")
    try:
        ep_val = float(ep) if ep is not None else 50.0
        ep_val = max(0.0, min(100.0, ep_val))
    except (TypeError, ValueError):
        log.warning(f"[ENGINE_B_AI] {pair}: Invalid edgeProbability {ep!r}; using 50")
        ep_val = 50.0
    out["edgeProbability"] = ep_val

    # riskLevel must be valid
    rl = str(out.get("riskLevel", "Medium")).strip().title()
    if rl not in VALID_ENGINE_B_RISK_LEVELS:
        log.warning(f"[ENGINE_B_AI] {pair}: Invalid riskLevel {rl!r}; using Medium")
        rl = "Medium"
    out["riskLevel"] = rl

    # style_ratings must contain all three styles with valid fields
    style_ratings = out.get("style_ratings")
    if not isinstance(style_ratings, dict):
        style_ratings = {}
    for style in REQUIRED_STYLE_KEYS:
        if style not in style_ratings or not isinstance(style_ratings[style], dict):
            style_ratings[style] = {
                "grade": _normalise_engine_b_grade(out.get("grade", "C"), pair),
                "edgeProbability": ep_val,
                "riskLevel": rl,
            }
        else:
            sr = dict(style_ratings[style])
            sr["grade"] = _normalise_engine_b_grade(sr.get("grade", out.get("grade", "C")), pair)
            try:
                sr_ep = float(sr.get("edgeProbability", ep_val))
                sr_ep = max(0.0, min(100.0, sr_ep))
            except (TypeError, ValueError):
                sr_ep = ep_val
            sr["edgeProbability"] = sr_ep
            sr_rl = str(sr.get("riskLevel", rl)).strip().title()
            if sr_rl not in VALID_ENGINE_B_RISK_LEVELS:
                sr_rl = rl
            sr["riskLevel"] = sr_rl
            style_ratings[style] = sr
    out["style_ratings"] = style_ratings
    return out


def _bind_engine_b_selected_style(parsed: dict, selected_style: str) -> dict:
    """Bind Engine B headline fields to the deterministic selected style."""
    selected = str(selected_style or "intraday").strip().lower()
    if selected not in REQUIRED_STYLE_KEYS:
        selected = "intraday"
    ratings = parsed.get("style_ratings") or {}
    selected_row = ratings.get(selected) if isinstance(ratings, dict) else None

    parsed["resolvedStyle"] = selected.upper()
    parsed["bestValidStyle"] = selected.upper()
    if isinstance(selected_row, dict):
        parsed["grade"] = selected_row.get("grade", parsed.get("grade", "C"))
        parsed["edgeProbability"] = selected_row.get(
            "edgeProbability", parsed.get("edgeProbability", 50.0)
        )
        parsed["riskLevel"] = selected_row.get(
            "riskLevel", parsed.get("riskLevel", "Medium")
        )
    elif isinstance(ratings, dict) and ratings:
        # Ratings for other styles are not evidence for the selected style.
        parsed["grade"] = "C"
        parsed["edgeProbability"] = 50.0
        parsed["riskLevel"] = "Medium"
    return parsed


def _get_present_value(payload: dict | None, *keys, default=None):
    if not isinstance(payload, dict):
        return default
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def _normalise_engine_b_ai_payload(parsed: dict) -> dict:
    """Accept common model key aliases before falling back to safe defaults."""
    if not isinstance(parsed, dict):
        return parsed

    out = dict(parsed)
    aliases = {
        "edge_probability": "edgeProbability",
        "edge_prob": "edgeProbability",
        "risk_level": "riskLevel",
        "summary": "verdict",
        "reasoning": "verdict",
        "analysis": "verdict",
    }
    for src, dst in aliases.items():
        if dst not in out and src in out:
            out[dst] = out[src]

    style_ratings = out.get("style_ratings")
    if isinstance(style_ratings, dict):
        normalised_styles = {}
        for style, row in style_ratings.items():
            if not isinstance(row, dict):
                continue
            item = dict(row)
            if "edgeProbability" not in item:
                item["edgeProbability"] = (
                    item.get("edge_probability")
                    or item.get("edge_prob")
                    or out.get("edgeProbability")
                )
            if "riskLevel" not in item:
                item["riskLevel"] = item.get("risk_level") or out.get("riskLevel")
            normalised_styles[str(style).lower()] = item
        out["style_ratings"] = normalised_styles

    return out


def _normalise_engine_b_grade(value, pair: str = "?") -> str:
    grade = str(value or "C").strip().upper()
    if grade in VALID_ENGINE_B_GRADES:
        return grade
    log.warning(f"[ENGINE_B_AI] {pair}: Invalid grade {value!r}; using C")
    return "C"


def build_engine_b_signal_message(
    pair: str,
    direction: str,
    current_price: float,
    structure_result: dict,
    confidence_result: dict,
    learning_ctx: Optional[dict] = None,
    engine_a_ctx: Optional[dict] = None,
    news_ctx: Optional[dict] = None,
    freshness_ctx: Optional[dict] = None,
    style: Optional[str] = None,
    asset_type: Optional[str] = None,
) -> str:
    """
    Build AI prompt message for Engine B structural signals.
    Follows Engine A pattern but emphasizes price action structure over indicators.

    Args:
        pair: Trading pair display name
        direction: LONG or SHORT
        current_price: Current market price
        structure_result: Output from NakedEngine.analyze_structure()
        confidence_result: Output from NakedEngine.calculate_confidence()
        learning_ctx: AI learning context from trade outcomes
        engine_a_ctx: Optional Engine A signal dict for cross-engine alignment check
        news_ctx: Optional news/event context for AI advisory narrative (never affects checklist)

    Returns:
        Formatted message string for AI analysis
    """
    lines = []

    resolved_style = resolve_auto_style(
        style
        or structure_result.get("style")
        or (engine_a_ctx or {}).get("style")
        or "auto",
        {
            "type": asset_type
            or structure_result.get("asset_type")
            or (engine_a_ctx or {}).get("type")
        },
        score_group=(
            structure_result.get("scoreGroup")
            or structure_result.get("score_group")
            or (engine_a_ctx or {}).get("scoreGroup")
            or (engine_a_ctx or {}).get("score_group")
        ),
        asset_type=(
            asset_type
            or structure_result.get("asset_type")
            or (engine_a_ctx or {}).get("type")
        ),
    )
    _engine_b_proxy = dict(structure_result)
    _engine_b_proxy["style"] = resolved_style
    _signal_proxy = dict(engine_a_ctx or {})
    _signal_proxy.update(
        {
            "pair": pair,
            "display": pair,
            "direction": direction,
            "price": current_price,
            "type": asset_type
            or structure_result.get("asset_type")
            or (engine_a_ctx or {}).get("type"),
            "engine": "engine_b",
            "is_naked": True,
            "style": resolved_style,
            "requested_style": resolved_style,
            "engine_b": _engine_b_proxy,
            "engine_b_overlay": _engine_b_proxy,
        }
    )
    _role_aliases = {
        "timeframePolicyVersion": ("timeframePolicyVersion", "timeframe_policy_version"),
        "regimeTf": ("regimeTf", "regime_tf"),
        "biasTf": ("biasTf", "bias_tf"),
        "structureTf": ("structureTf", "structure_tf", "zone_tf"),
        "setupTf": ("setupTf", "setup_tf"),
        "triggerTf": ("triggerTf", "trigger_tf", "trigger_timeframe_actual", "entry_tf"),
        "executionTf": ("executionTf", "execution_tf", "entry_tf"),
    }
    for target, aliases in _role_aliases.items():
        value = _get_present_value(structure_result, *aliases)
        if value is None:
            value = _get_present_value(confidence_result, *aliases)
        if value is not None:
            _signal_proxy[target] = value
            if target == "structureTf":
                _engine_b_proxy["struct_tf"] = value
            elif target == "setupTf":
                _engine_b_proxy["setup_tf"] = value
            elif target == "triggerTf":
                _engine_b_proxy["trigger_tf_actual"] = value
            elif target == "executionTf":
                _engine_b_proxy["execution_tf_actual"] = value

    try:
        from ai_context import build_ai_calibration_context_string
        lines.append(build_ai_calibration_context_string(_signal_proxy, engine_source="Engine B naked market structure"))
        lines.append("")
    except Exception:
        pass

    # Engine B playbook (B-only) — parity with chart/Marcus injection surfaces.
    try:
        from ai_playbooks import get_engine_b_playbook, render_playbook_prompt_block

        _pb = render_playbook_prompt_block([get_engine_b_playbook()], compact=True)
        if _pb:
            lines.append(_pb)
            lines.append("")
    except Exception:
        pass

    # === ENGINE A CROSS-CHECK (only when compare mode) ===
    if engine_a_ctx and isinstance(engine_a_ctx, dict):
        a_dir = engine_a_ctx.get("direction", "?")
        a_score = _get_present_value(engine_a_ctx, "confluenceScore", "score", default=0)
        a_max = _get_present_value(engine_a_ctx, "maxScore", "max_score", default=3.0)
        a_pct = _get_present_value(engine_a_ctx, "confluencePct")
        if a_pct is None:
            a_pct = round((a_score / a_max * 100) if a_max else 0)
        a_trend = engine_a_ctx.get("trendState") or engine_a_ctx.get("trend_state", "?")
        a_sl = engine_a_ctx.get("sl")
        a_tp = engine_a_ctx.get("tp1")
        a_entry = engine_a_ctx.get("price", current_price)
        agree = a_dir == direction
        lines.append("=== ENGINE A CROSS-CHECK ===")
        lines.append(
            f"Engine A Direction: {a_dir} | Agreement: {'YES ✓' if agree else 'NO — CONFLICT ✗'}"
        )
        lines.append(f"Engine A Confluence: {a_score:.2f} / {a_max} ({a_pct}%)")
        lines.append(f"Engine A Trend State: {a_trend}")
        lines.append(f"Engine A Entry: {a_entry} | SL: {a_sl} | TP1: {a_tp}")
        if not agree:
            lines.append(
                "WARNING: Engines disagree on direction — higher risk, comment explicitly."
            )
        lines.append("")

    # === SIGNAL ===
    conf_score = confidence_result.get("score", 0)
    gate_score = confidence_result.get("gate_score", conf_score)
    gate_max = confidence_result.get("gate_max_possible")
    gate_pct = confidence_result.get("gate_pct")
    if gate_max is not None and gate_pct is not None:
        try:
            gate_max_f = float(gate_max)
            gate_score_f = float(gate_score)
            score_pct = int(gate_pct)
            lines.append("=== ENGINE B SIGNAL (NAKED STRUCTURE) ===")
            lines.append(f"Pair: {pair} | Direction: {direction} | Price: {current_price}")
            lines.append(
                f"Confidence: {gate_score_f:.2f} / {gate_max_f:g} ({score_pct}% gates)"
            )
            max_score = confidence_result.get("max_possible")
            if max_score and float(conf_score) != gate_score_f:
                lines.append(
                    f"Quality score (bonuses/penalties): {float(conf_score):.2f} / {float(max_score):g}"
                )
        except (TypeError, ValueError):
            gate_max = None
    if gate_max is None or gate_pct is None:
        max_score = confidence_result.get("max_possible", 5.0)
        score_pct = round((conf_score / max_score * 100)) if max_score else 0
        lines.append("=== ENGINE B SIGNAL (NAKED STRUCTURE) ===")
        lines.append(f"Pair: {pair} | Direction: {direction} | Price: {current_price}")
        lines.append(f"Confidence: {conf_score:.2f} / {max_score} ({score_pct}%)")
    lines.append(f"Verdict: {structure_result.get('structural_verdict', 'UNCLEAR')}")
    canonical_trade_ok = bool(
        confidence_result.get("canonical_trade_ok", confidence_result.get("engine_b_canonical_actionable"))
    )
    confidence_passed = bool(confidence_result.get("passed") or confidence_result.get("confidence_passed"))
    if confidence_passed and not canonical_trade_ok:
        actionable_line = "SCORE PASSED / GATE FAILED (canonical_trade_ok=NO)"
    elif canonical_trade_ok:
        actionable_line = "YES (canonical_trade_ok)"
    else:
        actionable_line = "NO (canonical_trade_ok)"
    lines.append(f"Actionable: {actionable_line}")
    if confidence_result.get("engine_b_canonical_status"):
        lines.append(f"Canonical status: {confidence_result.get('engine_b_canonical_status')}")
    badge = confidence_result.get("canonical_badge_state")
    if isinstance(badge, dict):
        lines.append("")
        lines.append("=== CANONICAL GATES (authoritative) ===")
        lines.append(f"Structure: {badge.get('structure', 'n/a')}")
        lines.append(f"Location: {badge.get('location', 'n/a')}")
        lines.append(f"Trigger: {badge.get('trigger', 'n/a')}")
        lines.append(f"Room/RR: {badge.get('room_rr', 'n/a')}")
        lines.append(f"Confidence: {badge.get('confidence', 'n/a')}")
        lines.append(f"Trade: {badge.get('trade', 'n/a')}")
    primary_reject = confidence_result.get("canonical_primary_reject_reason")
    if primary_reject:
        lines.append(f"Primary reject: {primary_reject}")
    secondary = confidence_result.get("canonical_secondary_reject_reasons") or []
    if secondary:
        lines.append(f"Secondary rejects: {', '.join(str(x) for x in secondary)}")

    # === STRUCTURE ===
    lines.append("")
    lines.append("=== MARKET STRUCTURE ===")

    # Swing sequences. The structural sequence TF varies by style (H1 scalp,
    # H4 intraday, D1 swing) — label it accurately so the model does not judge
    # a D1 sequence as an H1 micro-structure read. Macro is always H4.
    _struct_tf = str(structure_result.get("structure_tf") or "H1").upper()
    lines.append(
        f"{_struct_tf} Structure Swing: {structure_result.get('current_swing_sequence', 'RANGING')}"
    )
    lines.append(f"H4 Macro Swing: {structure_result.get('macro_swing_sequence', 'RANGING')}")

    # BOS and sweeps
    bos = structure_result.get("bos_data", {})
    sweep = structure_result.get("sweep_data", {})
    lines.append(
        f"Break of Structure: Bull={bos.get('bos_bull', False)} Bear={bos.get('bos_bear', False)}"
    )
    lines.append(
        f"Liquidity Sweep: Bull={sweep.get('bull_sweep', False)} Bear={sweep.get('bear_sweep', False)}"
    )

    # FVG
    fvg_overlap = structure_result.get("fvg_overlap", False)
    lines.append(f"Fair Value Gap overlap at key zone: {fvg_overlap}")

    # === LEVELS ===
    lines.append("")
    lines.append("=== STRUCTURAL LEVELS ===")

    res_zone = structure_result.get("nearest_resistance_zone")
    sup_zone = structure_result.get("nearest_support_zone")

    if res_zone:
        lines.append(
            f"Resistance: {res_zone.get('lower', 0):.6f} - {res_zone.get('upper', 0):.6f}"
        )
    else:
        lines.append("Resistance: None detected")

    if sup_zone:
        lines.append(
            f"Support: {sup_zone.get('lower', 0):.6f} - {sup_zone.get('upper', 0):.6f}"
        )
    else:
        lines.append("Support: None detected")

    dist_res = structure_result.get("distance_to_res", 0)
    dist_sup = structure_result.get("distance_to_sup", 0)
    lines.append(f"Distance to Resistance: {float(dist_res or 0):.6f}")
    lines.append(f"Distance to Support: {float(dist_sup or 0):.6f}")

    # === TRADE PARAMETERS ===
    lines.append("")
    if canonical_trade_ok:
        lines.append("=== TRADE PARAMETERS (executable) ===")
    else:
        lines.append("=== REJECTED DIAGNOSTIC LEVELS (not executable) ===")
    sl = (
        confidence_result.get("execution_sl")
        or structure_result.get("recommended_stop_loss")
    )
    tp = (
        confidence_result.get("execution_tp1")
        or confidence_result.get("execution_tp")
        or structure_result.get("recommended_take_profit")
    )
    rr = 0.0
    if sl is not None and tp is not None:
        sl_dist = abs(float(current_price) - float(sl))
        tp_dist = abs(float(tp) - float(current_price))
        rr = (tp_dist / sl_dist) if sl_dist > 0 else 0.0

    if canonical_trade_ok:
        lines.append(f"Entry: {current_price}")
        lines.append(f"Stop Loss: {sl}")
        lines.append(f"Take Profit: {tp}")
        lines.append(f"Risk:Reward: 1:{rr:.2f}")
    else:
        lines.append("Entry: -")
        lines.append("Stop Loss: -")
        lines.append("Take Profit: -")
        lines.append("Risk:Reward: -")
        if sl is not None or tp is not None:
            lines.append(
                f"Diagnostic only — Entry: {current_price} | SL: {sl} | TP: {tp} | RR: 1:{rr:.2f}"
            )

    # === CONFIDENCE BREAKDOWN ===
    lines.append("")
    lines.append("=== CONFIDENCE BREAKDOWN ===")
    lines.append(f"Structure Score: {confidence_result.get('struct_points', 0):.2f}")
    lines.append(f"Room Score: {confidence_result.get('room_points', 0):.2f}")
    lines.append(f"RR Score: {confidence_result.get('rr_points', 0):.2f}")
    lines.append(f"Catalyst Bonus: {confidence_result.get('catalyst_bonus', 0):.2f}")
    _ai_adj = confidence_result.get("ai_adjustment")
    try:
        _ai_adj_f = float(_ai_adj) if _ai_adj is not None else 0.0
    except (TypeError, ValueError):
        _ai_adj_f = 0.0
    if _ai_adj_f:
        lines.append(f"AI Adjustment: {_ai_adj_f:.2f}")

    # === LEARNING CONTEXT ===
    if learning_ctx and learning_ctx.get("sample_size", 0) >= 5:
        lines.append("")
        lines.append("=== LEARNING CONTEXT (from live outcomes) ===")

        pair_stats = learning_ctx.get("pair_stats")
        if pair_stats:
            lines.append(
                f"This pair history: {pair_stats['win_rate'] * 100:.0f}% WR over "
                f"{pair_stats['total_trades']} trades (avg {pair_stats['avg_r']:+.2f}R)"
            )

        asset_stats = learning_ctx.get("asset_type_stats")
        if asset_stats:
            lines.append(
                f"Asset class: {asset_stats['win_rate'] * 100:.0f}% WR over "
                f"{asset_stats['total_trades']} trades"
            )

        # Recent failures
        recent_fails = learning_ctx.get("recent_failures", [])
        if recent_fails:
            fail_strs = [
                f"{f.get('pair', '?')} {f.get('grade', '?')} {f.get('r', 0):+.1f}R"
                if isinstance(f, dict)
                else str(f)
                for f in recent_fails[:3]
            ]
            lines.append(f"Recent failures: {', '.join(fail_strs)}")

    # === NEWS / EVENT CONTEXT (advisory only — does not affect pass/fail) ===
    if news_ctx and isinstance(news_ctx, dict):
        lines.append("")
        lines.append("=== NEWS / EVENT CONTEXT ===")
        lines.append("NOTE: This context is for your advisory review only.")
        lines.append("Engine B pass/fail is already decided by price-action checklist.")
        lines.append("Use this to add warnings, timing notes, or risk context to your narrative.")
        lines.append("")

        # Economic events (high-impact: NFP, CPI, FOMC, rate decisions)
        econ = news_ctx.get("economic_events") or news_ctx.get("events") or news_ctx.get("forexEvents") or []
        if econ:
            lines.append("Upcoming economic events:")
            for ev in econ[:5]:  # cap at 5 most relevant
                ev_name = ev.get("name") or ev.get("event") or "Unknown"
                ev_time = ev.get("time") or ev.get("date") or ""
                ev_impact = ev.get("impact") or ev.get("importance") or ""
                ev_currency = ev.get("currency") or ev.get("country") or ""
                lines.append(f"  - {ev_name} | {ev_time} | Impact: {ev_impact} | {ev_currency}")
            lines.append("")

        # Pair-specific sentiment
        sentiment = news_ctx.get("sentiment") or news_ctx.get("pair_sentiment")
        if not sentiment and news_ctx.get("pairSentiment"):
            pair_sent = news_ctx["pairSentiment"]
            sentiment = pair_sent.get(pair) if isinstance(pair_sent, dict) else None
        if sentiment:
            if isinstance(sentiment, dict):
                s_score = sentiment.get("score") or sentiment.get("value") or "N/A"
                s_label = sentiment.get("label") or sentiment.get("sentiment") or ""
                lines.append(f"Pair sentiment: {s_label} ({s_score})")
            elif isinstance(sentiment, (int, float)):
                lines.append(f"Pair sentiment score: {sentiment}")
            lines.append("")

        # Market headlines (top 3)
        headlines = (
            news_ctx.get("headlines")
            or news_ctx.get("news")
            or news_ctx.get("marketNews")
            or []
        )
        if headlines:
            lines.append("Recent market headlines:")
            for hl in headlines[:3]:
                if isinstance(hl, dict):
                    title = hl.get("title") or hl.get("headline") or str(hl)
                    lines.append(f"  - {title}")
                elif isinstance(hl, str):
                    lines.append(f"  - {hl}")
            lines.append("")

        # Crypto-specific news (only for crypto pairs)
        crypto_news = news_ctx.get("crypto_news") or news_ctx.get("crypto") or news_ctx.get("cryptoNews") or []
        if crypto_news:
            lines.append("Crypto-specific news:")
            for cn in crypto_news[:3]:
                if isinstance(cn, dict):
                    title = cn.get("title") or cn.get("headline") or str(cn)
                    lines.append(f"  - {title}")
                elif isinstance(cn, str):
                    lines.append(f"  - {cn}")
            lines.append("")

    # === CANDLE DATA FRESHNESS ===
    try:
        from ai_utils import build_freshness_ai_context as _build_freshness
        _signal_proxy = freshness_ctx or {}
        _freshness_str = _build_freshness(_signal_proxy)
        if _freshness_str:
            lines.append("")
            lines.append(_freshness_str)
    except Exception:
        pass

    return "\n".join(lines)


def get_engine_b_ai_verdict(
    pair: str,
    direction: str,
    current_price: float,
    structure_result: dict,
    confidence_result: dict,
    learning_ctx: Optional[dict] = None,
    xai_api_key: Optional[str] = None,
    xai_model: Optional[str] = None,
    engine_a_ctx: Optional[dict] = None,
    news_ctx: Optional[dict] = None,
    freshness_ctx: Optional[dict] = None,
    asset_type: Optional[str] = None,
    style: Optional[str] = None,
) -> dict:
    """
    Get AI analysis for Engine B signal using the configured AI provider.

    Returns dict with:
        - grade: A+ to F
        - edgeProbability: 0-100
        - riskLevel: LOW/MEDIUM/HIGH
        - verdict: text analysis
        - error: if failed
    """
    runtime = resolve_ai_review_runtime(CONFIG)
    active_provider = str(runtime.get("provider") or runtime.get("selectedProvider") or "grok")
    api_key = str(xai_api_key or "").strip()
    if not api_key:
        log.info("[ENGINE_B_AI] AI API key not provided, skipping AI analysis")
        return {
            "error": "API key not configured",
            "provider": active_provider,
            "selectedProvider": runtime.get("selectedProvider") or active_provider,
            "fallbackUsed": False,
        }

    try:
        client = create_ai_client(CONFIG, api_key=api_key, provider=active_provider)
        model_override = None if runtime.get("fallbackUsed") else xai_model
        model = str(model_override or get_ai_model(CONFIG, "AI_MODEL", provider=active_provider)).strip()
        resolved_asset_type = (
            asset_type
            or structure_result.get("asset_type")
            or (engine_a_ctx or {}).get("type")
        )
        resolved_style = resolve_auto_style(
            style
            or structure_result.get("style")
            or (engine_a_ctx or {}).get("style")
            or "auto",
            {"type": resolved_asset_type},
            score_group=(
                structure_result.get("scoreGroup")
                or structure_result.get("score_group")
                or (engine_a_ctx or {}).get("scoreGroup")
                or (engine_a_ctx or {}).get("score_group")
            ),
            asset_type=resolved_asset_type,
        )

        message = build_engine_b_signal_message(
            pair,
            direction,
            current_price,
            structure_result,
            confidence_result,
            learning_ctx,
            engine_a_ctx=engine_a_ctx,
            news_ctx=news_ctx,
            freshness_ctx=freshness_ctx,
            style=resolved_style,
            asset_type=resolved_asset_type,
        )

        cross_engine_note = (
            (
                " When Engine A context is present: treat it as comparative context only. "
                "Note whether Engine A momentum quality appears consistent with this structural trade, "
                "but do not change Engine B raw score, direction, eligibility, card status, SL, TP, or conviction. "
                "Disagreement is not automatic rejection and must not suppress either engine."
            )
            if engine_a_ctx
            else ""
        )

        expert_prompt = (
            _ENGINE_B_AI_EXPERT_PREFIX
            + cross_engine_note
            + " Weigh: overall structural conviction; distance to boundary; "
            "trigger quality; multi-TF alignment (explicit Y/N with evidence from available swing sequences). "
            f"The deterministic selected style is {resolved_style.upper()}. "
            "Judge it only from the server-supplied policy roles and configured Style min RR; "
            "never apply another style's timeframe, holding-period, or RR rules. "
            "Top-level grade/edgeProbability/riskLevel and resolvedStyle must represent the selected style. "
            "Other style_ratings are comparison-only and must not replace the selected-style headline. "
            "Output strict JSON only with exactly these top-level keys in this precise order: "
            "reasoning, verdict, reviewSource, resolvedStyle, bestValidStyle, grade, edgeProbability, riskLevel, style_ratings. "
            "style_ratings must contain scalp, intraday, and swing objects with grade, edgeProbability, riskLevel."
        )
        _temp = float(AITemperatureConfig.get_temperature("engine_b_ai"))

        parsed_dict, raw_text = _call_ai_with_retry(
            client,
            model=model,
            messages=[
                {"role": "system", "content": expert_prompt},
                {"role": "user", "content": message},
            ],
            temperature=_temp,
        )

        if parsed_dict is not None:
            parsed = parsed_dict
        else:
            parsed = parse_json_object(raw_text or "")

        if parsed is None:
            log.error(f"[ENGINE_B_AI] {pair}: Failed to parse JSON from AI response")
            return {"error": "Invalid AI response format"}

        parsed = _normalise_engine_b_ai_payload(parsed)
        parsed.setdefault("provider", active_provider)
        parsed.setdefault("selectedProvider", runtime.get("selectedProvider") or active_provider)
        parsed.setdefault("model", model)
        parsed.setdefault("fallbackUsed", bool(runtime.get("fallbackUsed")))
        try:
            from ai_learning import learning_capability_metadata

            _learn_meta = learning_capability_metadata()
            parsed.setdefault("learningMode", _learn_meta.get("learningMode"))
            parsed.setdefault("selfLearning", False)
        except Exception:
            parsed.setdefault("learningMode", "observation_only")
            parsed.setdefault("selfLearning", False)
        if runtime.get("providerFailure"):
            parsed.setdefault("providerFailure", runtime.get("providerFailure"))

        # Validate required keys
        required = {"grade", "edgeProbability", "riskLevel", "verdict", "reviewSource"}
        missing = required - set(parsed.keys())
        if missing == required:
            log.error(f"[ENGINE_B_AI] {pair}: Empty AI response (all required keys missing)")
            return {"error": "empty AI response"}
        if missing:
            log.warning(f"[ENGINE_B_AI] {pair}: Missing keys {missing} in AI response")
            parsed.setdefault("grade", "C")
            parsed.setdefault("edgeProbability", 50)
            parsed.setdefault("riskLevel", "Medium")
            parsed.setdefault("reviewSource", "engine_b_marcus")
            parsed.setdefault("verdict", "Model response missing fields; using safe defaults.")


        # Bind before style completion so a missing selected-style row cannot be
        # synthesized from a headline that represented another style.
        parsed = _bind_engine_b_selected_style(parsed, resolved_style)
        parsed = _validate_engine_b_ai_payload(parsed, pair)
        parsed = _bind_engine_b_selected_style(parsed, resolved_style)
        parsed["grade"] = _normalise_engine_b_grade(parsed.get("grade"), pair)

        log.info(
            f"[ENGINE_B_AI] {pair} => Grade:{parsed.get('grade', '?')} "
            f"Prob:{parsed.get('edgeProbability', '?')}% Risk:{parsed.get('riskLevel', '?')}"
        )

        try:
            from ai_review_logger import (
                log_ai_review,
                map_engine_b_grade_to_ai_state,
                REVIEW_TYPE_ENGINE_B_AI,
            )
            from prompt_versions import get_prompt_version

            _freshness_reason = (
                (freshness_ctx or {}).get("dataFreshness", {}) or {}
            ).get("reason") or "unknown"
            log_ai_review(
                symbol=pair,
                asset_type=str(
                    asset_type
                    or (engine_a_ctx.get("type") if isinstance(engine_a_ctx, dict) else None)
                    or "unknown"
                ),
                review_type=REVIEW_TYPE_ENGINE_B_AI,
                model=model,
                provider=active_provider,
                prompt_version=get_prompt_version("engine_b_ai"),
                input_packet={
                    "pair": pair,
                    "direction": direction,
                    "system_prompt": expert_prompt,
                    "user_message": message,
                },
                has_chart_image=False,
                candle_freshness_status=_freshness_reason,
                engine_a_state=(engine_a_ctx or {}).get("confluenceScore") if isinstance(engine_a_ctx, dict) else None,
                engine_b_state=confidence_result.get("pct"),
                engine_c_state=None,
                engine_d_state=None,
                risk_state=None,
                ai_review_state=map_engine_b_grade_to_ai_state(parsed.get("grade", "")),
                ai_confidence=parsed.get("edgeProbability"),
                contradictions_count=0,
                missing_information_count=0,
                parse_success=True,
                schema_valid=not bool(missing),
                execution_allowed_before_ai=True,
                execution_allowed_after_ai=True,
                final_action="advisory",
                trace_id=(
                    (engine_a_ctx or {}).get("trace_id")
                    if isinstance(engine_a_ctx, dict)
                    else None
                ),
            )
        except Exception as _log_err:
            log.debug("[AI_AUDIT] Engine B AI audit log failed: %s", _log_err)

        return parsed

    except Exception as e:
        err_text = str(e).strip() or e.__class__.__name__
        if "timed out" in err_text.lower() or "timeout" in err_text.lower():
            err_text = "AI request timed out after retry attempts"
        log.error(f"[ENGINE_B_AI] {pair}: AI analysis failed - {err_text}")
        return {"error": err_text}
