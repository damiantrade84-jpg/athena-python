"""Marcus Reid text-review routing helpers for /api/analyze → run_ai."""

from __future__ import annotations

from typing import Any

from ai_playbooks import get_engine_a_playbook, get_engine_b_playbook, render_playbook_prompt_block
from ai_schemas import EngineAResponse, EngineBResponse, EngineCMarcusResponse

ENGINE_A_SOURCE = "engine_a"
ENGINE_B_SOURCE = "engine_b"
ENGINE_C_SOURCE = "engine_c"

_ENGINE_B_OUTPUT_ADDON = (
    "\n\nENGINE B PRIMARY REVIEW RULES:\n"
    "- Engine B naked market structure is the primary deterministic context.\n"
    "- Follow the ATHENA TRADE PLAYBOOKS block (Engine B entry models, mustRejectIf, invalidations).\n"
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
    "mustRejectIf rules, and invalidations. Apply them strictly (advisory only)."
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
    """Mirror chart-review playbook injection for Marcus text path."""
    playbooks = [get_engine_a_playbook()]
    if _has_engine_b_context(signal):
        playbooks.append(get_engine_b_playbook())
    return render_playbook_prompt_block(playbooks, compact=True)


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
