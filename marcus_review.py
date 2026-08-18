"""Marcus Reid text-review routing helpers for /api/analyze → run_ai."""

from __future__ import annotations

from typing import Any

from ai_playbooks import get_engine_a_playbook, get_engine_b_playbook, render_playbook_prompt_block
from ai_schemas import EngineAResponse, EngineBResponse, EngineCMarcusResponse
from style_resolver import normalize_style

ENGINE_A_SOURCE = "engine_a"
ENGINE_B_SOURCE = "engine_b"
ENGINE_C_SOURCE = "engine_c"

_ENGINE_B_OUTPUT_ADDON = (
    "\n\nENGINE B PRIMARY REVIEW RULES:\n"
    "- Engine B naked market structure is the primary deterministic context.\n"
    "- Follow the ATHENA TRADE PLAYBOOKS block (Engine B entry models, mustRejectIf, invalidations).\n"
    "- Engine B is top-down and SEQUENTIAL: HTF bias (Daily-primary for intraday, "
    "Weekly+Daily for swing) -> MTF confirmation (structure/setup rungs) -> LTF entry "
    "(trigger rung). Check the sequence is coherent and name the weakest stage. A strong "
    "LTF trigger never repairs a missing, unclear, or opposing HTF bias.\n"
    "- Use the server-supplied bias timeframe role and the supplied HTF bias / hierarchy "
    "fields only. Never derive bias from the chart timeframe and never assume an H4 bias.\n"
    "- bias_mode is deterministic and already applied: legacy = diagnostics only (review "
    "with the standard gate/zone logic and do NOT report the hierarchy as missing data); "
    "hierarchical = multiplier already applied; strict = state machine already applied. "
    "Cite block reasons (htf_bias_unclear, htf_bias_conflicting, counter_htf_bias, "
    "mtf_confirmation_missing, ltf_entry_missing); never re-apply or reverse them.\n"
    "- The hierarchy is advisory to you and never replaces structure_ok, location_ok, "
    "entry_ok, space_gate_ok, trigger_timeframe_gate_ok, or RR gates.\n"
    "- Do NOT fabricate Engine A trend_score/structure_score/momentum_score/liquidity_score/"
    "risk_score/confirmation_score/total_score when Engine A factor diagnostics are absent.\n"
    "- Judge by structural_verdict, gate flags, score_pct, min_rr, rr_used_for_gate, and zones.\n"
    "- reviewSource MUST be \"engine_b_marcus\".\n"
    "Output strict JSON with top-level keys: reasoning, verdict, reviewSource, resolvedStyle, "
    "bestValidStyle, grade, edgeProbability, riskLevel, style_ratings, levelsVerdict, "
    "levelsReason, suggestedSL, suggestedTP, warnings.\n"
    "style_ratings must contain scalp, intraday, swing objects with grade, edgeProbability, riskLevel."
)

_ENGINE_C_OUTPUT_ADDON = (
    "\n\nENGINE C CONSENSUS REVIEW RULES:\n"
    "- ENGINE C CONSENSUS is the primary deterministic context.\n"
    "- Use decision_state, tier, conviction, sizing_override, and A/B components.\n"
    "- Engine A factor scores are child diagnostics only; do not require fabricated component scores.\n"
    "- reviewSource MUST be \"engine_c_marcus\".\n"
    "- Grade and edgeProbability from consensus evidence, not Engine A total_score alone."
)

_MARCUS_PLAYBOOK_AUTHORITY = (
    "\n\nPLAYBOOK AUTHORITY:\n"
    "The ATHENA TRADE PLAYBOOKS block in the user message is authoritative for entry models, "
    "mustRejectIf rules, and invalidations. Apply them strictly (advisory only).\n"
    "PLAYBOOK FIELD NAMES: the playbooks were authored against the chart-review payload and "
    "cite dotted object paths (engineAContext.*, engineBContext.*, riskGeometry.*, "
    "atrDiagnostics.*, indicatorParity, vwapExtended). This is the TEXT review surface: the "
    "same evidence arrives under the === SECTION === headings and snake_case field names in "
    "the user message. Apply the playbook LOGIC and resolve each field by meaning. A dotted "
    "playbook path that does not appear verbatim is a naming difference, NOT missing data — "
    "never report it as unavailable and never downgrade for it.\n"
    "The playbooks' requiredOutputFields apply to the chart surface only. On this surface, "
    "emit exactly the JSON contract given above."
)

_MARCUS_STAGE1_PROMPT = (
    "Return a compact JSON object with ONLY these keys: "
    "reviewSource, grade, edgeProbability, riskLevel, verdict, reason, narrative, "
    "warnings, levelsVerdict, levelsReason, resolvedStyle, tradeStyle, positionSizing. "
    "No style_ratings yet."
)

_MARCUS_STAGE2_PROMPT = (
    "Using your prior analysis, return JSON with ONLY: "
    "style_ratings (scalp/intraday/swing each with grade, edgeProbability, riskLevel), "
    "and any missing Engine-A structured fields if applicable "
    "(trend_score, structure_score, momentum_score, liquidity_score, risk_score, "
    "confirmation_score, total_score, ai_action, blocking_reasons)."
)


def resolve_marcus_engine_source(signal: dict[str, Any]) -> str:
    """Return canonical engine source: engine_a | engine_b | engine_c."""
    if not isinstance(signal, dict):
        return ENGINE_A_SOURCE

    engine_hint = str(
        signal.get("engine_source")
        or signal.get("source_engine")
        or signal.get("engine")
        or signal.get("setup_engine")
        or ""
    ).lower()

    is_engine_c = (
        engine_hint in {"engine_c", "consensus", "c"}
        or (
            signal.get("confluenceScore") is None
            and any(
                key in signal
                for key in ("conviction", "combinedConviction", "decision_state", "engine_c")
            )
        )
        or isinstance(signal.get("engine_c"), dict)
    )
    if is_engine_c:
        return ENGINE_C_SOURCE

    is_engine_b = (
        engine_hint in {"engine_b", "naked", "b"}
        or (bool(signal.get("is_naked")) and not engine_hint)
    )
    if is_engine_b:
        return ENGINE_B_SOURCE

    return ENGINE_A_SOURCE


def resolve_marcus_score_group(signal: dict[str, Any]) -> str | None:
    if not isinstance(signal, dict):
        return None
    group = signal.get("scoreGroup") or signal.get("score_group")
    return str(group) if group else None


def resolve_marcus_asset_type(signal: dict[str, Any]) -> str:
    if not isinstance(signal, dict):
        return ""
    return str(signal.get("type") or signal.get("asset_class") or signal.get("asset_type") or "")


def resolve_marcus_indicator_periods(signal: dict[str, Any]) -> dict[str, int]:
    """Per-group EMA/RSI/ATR periods aligned with Engine A scoring."""
    from factor_scoring import _resolve_atr_adx_periods, _resolve_ema_periods, _resolve_rsi_period

    score_group = resolve_marcus_score_group(signal)
    asset_type = resolve_marcus_asset_type(signal)
    ema = _resolve_ema_periods(score_group, asset_type)
    atr_adx = _resolve_atr_adx_periods(score_group, asset_type)
    return {
        "ema_trend": int(ema.get("trend", 21)),
        "ema_momentum": int(ema.get("momentum", 50)),
        "ema_long": int(ema.get("long", 200)),
        "rsi": int(_resolve_rsi_period(score_group, asset_type)),
        "atr": int(atr_adx.get("atr", 14)),
        "adx": int(atr_adx.get("adx", 14)),
    }


def _has_engine_b_context(signal: dict[str, Any]) -> bool:
    if resolve_marcus_engine_source(signal) == ENGINE_B_SOURCE:
        return True
    naked = signal.get("naked_data")
    engine_b = signal.get("engine_b")
    return isinstance(naked, dict) and bool(naked) or isinstance(engine_b, dict) and bool(engine_b)


def build_marcus_playbook_block(signal: dict[str, Any]) -> str:
    """Inject engine-primary playbooks for Marcus text path.

    A-primary → Engine A playbook (+ B only when real B overlay context exists).
    B-primary → Engine B playbook only (do not push A confluence language).
    C-primary → A + B when B child context exists, else A.
    """
    source = resolve_marcus_engine_source(signal)
    if source == ENGINE_B_SOURCE:
        playbooks = [get_engine_b_playbook()]
    elif source == ENGINE_C_SOURCE:
        playbooks = [get_engine_a_playbook()]
        if _has_engine_b_context(signal):
            playbooks.append(get_engine_b_playbook())
    else:
        playbooks = [get_engine_a_playbook()]
        if _has_engine_b_context(signal):
            playbooks.append(get_engine_b_playbook())
    return render_playbook_prompt_block(playbooks, compact=True)


def bind_marcus_selected_style(result: dict[str, Any], style: str) -> dict[str, Any]:
    """Make Marcus headline fields represent the caller-selected style only."""
    selected = normalize_style(style)
    if selected == "auto":
        return result

    ratings = result.get("style_ratings")
    selected_row = None
    if isinstance(ratings, dict):
        for key, value in ratings.items():
            if str(key).strip().lower() == selected and isinstance(value, dict):
                selected_row = value
                break

    result["resolvedStyle"] = selected.upper()
    result["tradeStyle"] = selected.upper()
    result["bestValidStyle"] = selected.upper()
    if selected_row:
        if selected_row.get("grade") is not None:
            result["grade"] = selected_row["grade"]
            result["selectedStyleGrade"] = selected_row["grade"]
        if selected_row.get("edgeProbability") is not None:
            result["edgeProbability"] = selected_row["edgeProbability"]
        if selected_row.get("riskLevel") is not None:
            result["riskLevel"] = selected_row["riskLevel"]
    elif isinstance(ratings, dict) and ratings:
        # Never borrow a populated non-selected style's headline. The neutral
        # placeholder below is required to keep the response schema valid, so
        # flag it as a placeholder rather than letting it read as a real rating.
        result["grade"] = "C"
        result["edgeProbability"] = 50.0
        result["riskLevel"] = "Medium"
        result["selectedStyleGrade"] = "C"
        result["selectedStyleRatingAvailable"] = False
        result["selectedStyleRatingSource"] = "placeholder_no_rating_for_selected_style"
        if not result.get("evidenceStatus"):
            result["evidenceStatus"] = "INSUFFICIENT_DATA"
        _warnings = list(result.get("warnings") or [])
        if "SELECTED_STYLE_RATING_UNAVAILABLE" not in _warnings:
            _warnings.append("SELECTED_STYLE_RATING_UNAVAILABLE")
            result["warnings"] = _warnings
    else:
        result["selectedStyleGrade"] = result.get("grade")
    return result


def marcus_review_source(engine_source: str) -> str:
    mapping = {
        ENGINE_A_SOURCE: "engine_a_marcus",
        ENGINE_B_SOURCE: "engine_b_marcus",
        ENGINE_C_SOURCE: "engine_c_marcus",
    }
    return mapping.get(engine_source, "engine_a_marcus")


def marcus_response_model(engine_source: str):
    if engine_source == ENGINE_B_SOURCE:
        return EngineBResponse
    if engine_source == ENGINE_C_SOURCE:
        return EngineCMarcusResponse
    return EngineAResponse


def marcus_legacy_required_keys(engine_source: str) -> set[str]:
    base = {"grade", "edgeProbability", "riskLevel"}
    if engine_source == ENGINE_B_SOURCE:
        base.add("verdict")
    return base


def marcus_structured_required_keys(engine_source: str) -> set[str]:
    if engine_source == ENGINE_B_SOURCE:
        return {
            "grade",
            "edgeProbability",
            "riskLevel",
            "verdict",
            "reviewSource",
        }
    if engine_source == ENGINE_C_SOURCE:
        return {
            "grade",
            "edgeProbability",
            "riskLevel",
            "verdict",
            "reviewSource",
            "reason",
        }
    return {
        "symbol",
        "timeframe",
        "bias",
        "setup_type",
        "trend_score",
        "structure_score",
        "momentum_score",
        "liquidity_score",
        "risk_score",
        "confirmation_score",
        "total_score",
        "ai_action",
        "blocking_reasons",
        "reason",
    }


def build_marcus_system_prompt(base_prompt: str, engine_source: str) -> str:
    """Append engine-specific output contract to Marcus system prompt."""
    prompt = base_prompt + _MARCUS_PLAYBOOK_AUTHORITY
    if engine_source == ENGINE_B_SOURCE:
        prompt += _ENGINE_B_OUTPUT_ADDON
    elif engine_source == ENGINE_C_SOURCE:
        prompt += _ENGINE_C_OUTPUT_ADDON
    else:
        prompt += (
            '\nreviewSource: use "engine_a_marcus" for Engine A factor/confluence reviews.'
            "\nENGINE A SELECTION: Engine A V3 is a continuous quant quality score "
            "(trend / momentum / location / volume-flow). When a cross-sectional ranking "
            "block is supplied and applied, pairs were ranked inside their score_group / "
            "universe and only the top N or top percentile were promoted - a RELATIVE "
            "selection layer on top of the unchanged absolute threshold. Cite rank, "
            "cutoff, and group only from the supplied block; never re-rank, never infer a "
            "rank, and never let rank alone move the grade or edgeProbability. No block, "
            "or applied=false, means ranking was inactive: review on absolute thresholds "
            "and do not report its absence as missing data."
        )
    return prompt


def marcus_stage_prompt(stage: int) -> str:
    return _MARCUS_STAGE1_PROMPT if stage == 1 else _MARCUS_STAGE2_PROMPT


def marcus_reasoning_effort(
    configured_effort: str,
    *,
    has_playbook: bool,
    provider: str,
) -> str:
    """Bump low effort to medium when playbooks inflate output budget needs."""
    effort = str(configured_effort or "low").strip().lower()
    if (
        has_playbook
        and provider == "openai"
        and effort == "low"
    ):
        return "medium"
    return effort
