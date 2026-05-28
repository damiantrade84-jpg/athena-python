"""
engine_c_ai.py — Optional xAI advisory layer for Engine C: per-setup A vs B trust verdict.

Default-off via ENGINE_C_AI_WEIGHT_VERDICT_ENABLED. Optional conviction/weight tweak via
ENGINE_C_AI_WEIGHT_ADJUST_ENABLED (does not flip trade from False → True).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from ai_utils import parse_json_object
from config import (
    CONFIG,
    create_ai_client,
    get_ai_api_key,
    get_ai_model,
    get_ai_provider_label,
    resolve_ai_review_runtime,
)

log = logging.getLogger("sentinel")

VALID_TRUST = frozenset({"trust_a", "trust_b", "trust_both", "trust_neither"})

_ENGINE_C_AI_SYSTEM = """You are Marcus Reid, prop-desk risk head reviewing Engine A (factors) vs Engine B (structure).
Output ONLY valid JSON. No markdown.

ABSOLUTE RULES:
1. Every claim in reasoning MUST cite a specific field from the user packet (factor name, score, checklist flag).
2. NEVER use "will", "guaranteed", "definitely".

TASK:
- Decide trust_verdict: trust_a | trust_b | trust_both | trust_neither for THIS setup.
- weight_recommendation: {"A": x, "B": y} must sum to 1.0 (approx); each between 0.2 and 0.8 unless trust_neither (then near 0.5/0.5).
- conviction_modifier: float in [-0.15, 0.15] — edge only, not a full rescoring.

REQUIRED JSON KEYS IN THIS EXACT ORDER:
reasoning, trust_verdict, weight_recommendation, conviction_modifier
"""


def normalize_engine_c_ai_weight_verdict(
    parsed: Optional[Mapping[str, Any]],
    *,
    w_min: float = 0.20,
    w_max: float = 0.80,
) -> dict:
    """Normalize AI JSON for Engine C weight verdict (runtime + tests)."""
    if isinstance(parsed, dict) and parsed.get("error"):
        return dict(parsed)
    if not isinstance(parsed, dict):
        return {
            "trust_verdict": None,
            "weight_recommendation": None,
            "conviction_modifier": 0.0,
            "reasoning": "",
            "error": "invalid_payload",
        }

    out = {k: v for k, v in parsed.items() if not str(k).startswith("_")}
    tv_raw = str(out.get("trust_verdict") or "").strip().lower().replace("-", "_").replace(" ", "_")
    tv = tv_raw if tv_raw in VALID_TRUST else None
    if tv is None and tv_raw:
        # tolerate TRUST_A style from model
        if tv_raw in ("a", "engine_a"):
            tv = "trust_a"
        elif tv_raw in ("b", "engine_b"):
            tv = "trust_b"
        elif tv_raw in ("both", "aligned"):
            tv = "trust_both"
        elif tv_raw in ("neither", "none", "no_confidence"):
            tv = "trust_neither"
    if tv is None:
        tv = "trust_neither"

    out["trust_verdict"] = tv
    out["reasoning"] = str(out.get("reasoning") or out.get("rationale") or "")[:8000]

    try:
        cm = float(out.get("conviction_modifier", 0.0))
    except (TypeError, ValueError):
        cm = 0.0
    out["conviction_modifier"] = max(-0.15, min(0.15, cm))

    wr = out.get("weight_recommendation")
    if isinstance(wr, dict):
        try:
            wa = float(wr.get("A", wr.get("a", 0.5)))
            wb = float(wr.get("B", wr.get("b", 0.5)))
        except (TypeError, ValueError):
            wa, wb = 0.5, 0.5
        wa = max(w_min, min(w_max, wa))
        wb = max(w_min, min(w_max, wb))
        s = wa + wb
        if s <= 0:
            wa, wb = 0.5, 0.5
            s = 1.0
        wa, wb = wa / s, wb / s
        out["weight_recommendation"] = {"A": round(wa, 4), "B": round(wb, 4)}
    else:
        out["weight_recommendation"] = None

    out.pop("error", None)
    return out


def _build_weight_verdict_user_message(
    signal_a: dict,
    signal_b: dict,
    confidence_b: Optional[dict],
    consensus_snapshot: dict,
    regime: str,
    asset_type: str,
    base_weights: dict,
) -> str:
    lines = [
        "=== ENGINE C CONSENSUS SNAPSHOT ===",
        f"regime={regime} asset_type={asset_type}",
        f"verdict={consensus_snapshot.get('verdict')} trade={consensus_snapshot.get('trade')} "
        f"conviction={consensus_snapshot.get('conviction')} tier={consensus_snapshot.get('tier')} "
        f"decision_state={consensus_snapshot.get('decision_state')}",
        f"applied_engine_weights={consensus_snapshot.get('engine_weights')}",
        f"rule_ENGINE_C_AB_WEIGHTS_for_regime={base_weights}",
        "",
        "=== ENGINE A (signal_a) ===",
        f"direction={signal_a.get('direction')} "
        f"confluenceScore={signal_a.get('confluenceScore')}/{signal_a.get('maxScore', 3.0)}",
    ]
    fs = signal_a.get("factorScores") or signal_a.get("factor_scores") or {}
    fw = signal_a.get("factorWeights") or signal_a.get("factor_weights") or {}
    if fs:
        for k, v in fs.items():
            lines.append(f"  factor {k}: {v} [w={fw.get(k, '?')}]")
    fd = signal_a.get("factorDiagnostics") or {}
    if fd:
        tc = fd.get("trendCoherence") or {}
        cr = tc.get("coherence_ratio") if isinstance(tc, dict) else None
        lines.append(
            f"directionalScore={fd.get('directionalScore')} "
            f"nondirectionalScore={fd.get('nondirectionalScore')} "
            f"directionalConfidenceMultiplier={fd.get('directionalConfidenceMultiplier')} "
            f"trendCoherence_ratio={cr}"
        )
    cd = signal_a.get("confidenceDetail") or {}
    if cd and cd.get("confidence") is not None:
        lines.append(
            f"confidenceDetail.confidence={cd.get('confidence')} "
            f"components={cd.get('components')}"
        )

    lines.append("")
    lines.append("=== ENGINE B (signal_b + confidence_b) ===")
    lines.append(f"structural_verdict={signal_b.get('structural_verdict')}")
    cb = confidence_b if isinstance(confidence_b, dict) else {}
    if cb:
        lines.append(
            f"checklist: passed={cb.get('passed')} structure_ok={cb.get('structure_ok')} "
            f"zone_ok={cb.get('zone_ok')} trigger_ok={cb.get('trigger_ok')} "
            f"room_ok={cb.get('room_ok')} rr_ok={cb.get('rr_ok')} "
            f"score={cb.get('score')}/{cb.get('max_possible')}"
        )
    hfr = signal_b.get("hard_fail_reasons")
    if hfr:
        lines.append(f"hard_fail_reasons={hfr}")
    if not cb and not hfr:
        lines.append("(limited Engine B payload)")

    return "\n".join(lines)


def _engine_c_ai_state(verdict: dict | None) -> str:
    tv = str((verdict or {}).get("trust_verdict") or "").lower()
    if tv in ("trust_a", "trust_b", "trust_both"):
        return "CAUTION"
    if tv == "trust_neither":
        return "REJECT"
    return "REVIEW_INCOMPLETE"


def _log_engine_c_ai_review(
    *,
    signal_a: dict,
    signal_b: dict,
    consensus_snapshot: dict,
    confidence_b: Optional[dict],
    asset_type: str,
    model: str,
    provider: str | None = None,
    user_msg: str,
    verdict: dict | None,
    parse_success: bool,
    schema_valid: bool,
) -> None:
    try:
        from ai_review_logger import (
            REVIEW_TYPE_ENGINE_C_AI,
            log_ai_review,
        )

        log_ai_review(
            symbol=str(
                signal_a.get("pair")
                or signal_a.get("symbol")
                or signal_b.get("pair")
                or signal_b.get("symbol")
                or "?"
            ),
            asset_type=asset_type or str(signal_a.get("type") or "?"),
            review_type=REVIEW_TYPE_ENGINE_C_AI,
            model=model,
            provider=provider or get_ai_provider_label(CONFIG),
            prompt_version="engine_c_ai_weight_v1",
            input_packet=user_msg,
            has_chart_image=False,
            candle_freshness_status=(
                (signal_a.get("dataFreshness") or {}).get("reason") or "unknown"
            ),
            engine_a_state=signal_a.get("confluenceScore"),
            engine_b_state=(
                (confidence_b or {}).get("score")
                or signal_b.get("score")
                or signal_b.get("structural_verdict")
            ),
            engine_c_state={
                "verdict": consensus_snapshot.get("verdict"),
                "trade": consensus_snapshot.get("trade"),
                "conviction": consensus_snapshot.get("conviction"),
            },
            engine_d_state=None,
            risk_state=None,
            ai_review_state=_engine_c_ai_state(verdict),
            ai_confidence=None,
            contradictions_count=0,
            missing_information_count=0 if parse_success else 1,
            parse_success=parse_success,
            schema_valid=schema_valid,
            execution_allowed_before_ai=bool(consensus_snapshot.get("trade")),
            execution_allowed_after_ai=bool(consensus_snapshot.get("trade")),
            final_action="advisory",
        )
    except Exception as _log_exc:
        log.debug("[ENGINE_C_AI] audit log failed: %s", _log_exc)


def get_engine_c_weight_verdict(
    signal_a: dict,
    signal_b: dict,
    consensus_snapshot: dict,
    confidence_b: Optional[dict],
    regime: str,
    asset_type: str,
    *,
    base_weights: Optional[dict] = None,
    xai_api_key: Optional[str] = None,
    xai_model: Optional[str] = None,
) -> dict:
    """
    Call xAI for a trust verdict. Returns dict (normalized) or {error: str}.
    """
    runtime = resolve_ai_review_runtime(CONFIG)
    active_provider = str(runtime.get("provider") or runtime.get("selectedProvider") or "grok")
    key = (xai_api_key or str(runtime.get("api_key") or "") or get_ai_api_key(CONFIG) or "").strip()
    if not key:
        return {
            "error": f"{runtime.get('selectedProvider') or active_provider} API key not configured",
            "trust_verdict": None,
            "provider": active_provider,
            "selectedProvider": runtime.get("selectedProvider") or active_provider,
            "fallbackUsed": False,
        }

    bw = base_weights or {"A": 0.5, "B": 0.5}
    user_msg = _build_weight_verdict_user_message(
        signal_a or {},
        signal_b or {},
        confidence_b,
        consensus_snapshot or {},
        regime,
        asset_type,
        bw,
    )

    try:
        from engine_b_ai import _call_ai_with_retry

        model_override = None if runtime.get("fallbackUsed") else xai_model
        model = str(model_override or get_ai_model(CONFIG, "AI_MODEL", provider=active_provider)).strip()
        client = create_ai_client(CONFIG, api_key=key, provider=active_provider)
        _temp = float(CONFIG.get("AI_TEMPERATURE", 0.3))
        parsed_dict, raw_text = _call_ai_with_retry(
            client,
            model,
            messages=[
                {"role": "system", "content": _ENGINE_C_AI_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=_temp,
        )
        parsed = parsed_dict if isinstance(parsed_dict, dict) else parse_json_object(raw_text or "")
        if parsed is None:
            log.warning("[ENGINE_C_AI] Failed to parse JSON from AI response")
            _log_engine_c_ai_review(
                signal_a=signal_a or {},
                signal_b=signal_b or {},
                consensus_snapshot=consensus_snapshot or {},
                confidence_b=confidence_b,
                asset_type=asset_type,
                model=model,
                provider=active_provider,
                user_msg=user_msg,
                verdict={"trust_verdict": None},
                parse_success=False,
                schema_valid=False,
            )
            return {"error": "Invalid AI response format", "trust_verdict": None}
        w_min = float(CONFIG.get("ENGINE_C_AI_WEIGHT_MIN", 0.20) or 0.20)
        w_max = float(CONFIG.get("ENGINE_C_AI_WEIGHT_MAX", 0.80) or 0.80)
        out = normalize_engine_c_ai_weight_verdict(parsed, w_min=w_min, w_max=w_max)
        out["reviewSource"] = "engine_c_marcus_weight"
        out.setdefault("provider", active_provider)
        out.setdefault("selectedProvider", runtime.get("selectedProvider") or active_provider)
        out.setdefault("model", model)
        out.setdefault("fallbackUsed", bool(runtime.get("fallbackUsed")))
        if runtime.get("providerFailure"):
            out.setdefault("providerFailure", runtime.get("providerFailure"))
        _log_engine_c_ai_review(
            signal_a=signal_a or {},
            signal_b=signal_b or {},
            consensus_snapshot=consensus_snapshot or {},
            confidence_b=confidence_b,
            asset_type=asset_type,
            model=model,
            provider=active_provider,
            user_msg=user_msg,
            verdict=out,
            parse_success=True,
            schema_valid=not bool(out.get("error")),
        )
        return out
    except Exception as exc:  # pragma: no cover - network
        log.warning("[ENGINE_C_AI] weight verdict failed: %s", exc)
        try:
            _log_engine_c_ai_review(
                signal_a=signal_a or {},
                signal_b=signal_b or {},
                consensus_snapshot=consensus_snapshot or {},
                confidence_b=confidence_b,
                asset_type=asset_type,
                model=str(xai_model or get_ai_model(CONFIG, "AI_MODEL", provider=active_provider)).strip(),
                provider=active_provider,
                user_msg=user_msg,
                verdict={"trust_verdict": None, "error": str(exc)},
                parse_success=False,
                schema_valid=False,
            )
        except Exception:
            pass
        return {"error": str(exc).strip() or exc.__class__.__name__, "trust_verdict": None}


def apply_engine_c_ai_weight_adjustment(result: dict, verdict: dict) -> dict:
    """
    Apply optional weight re-blend + additive conviction_modifier.
    Does not set trade=True when it was False (execution safety).
    """
    if verdict.get("error"):
        return result
    verdict = normalize_engine_c_ai_weight_verdict(
        verdict,
        w_min=float(CONFIG.get("ENGINE_C_AI_WEIGHT_MIN", 0.20) or 0.20),
        w_max=float(CONFIG.get("ENGINE_C_AI_WEIGHT_MAX", 0.80) or 0.80),
    )
    if verdict.get("error") or verdict.get("trust_verdict") is None:
        return result

    comps = result.get("components") or {}
    try:
        an = float(comps.get("a_norm") or 0.0)
    except (TypeError, ValueError):
        an = 0.0
    try:
        bn = float(comps.get("b_norm") or 0.0)
    except (TypeError, ValueError):
        bn = 0.0

    mod = float(verdict.get("conviction_modifier") or 0.0)
    wrec = verdict.get("weight_recommendation")
    c0 = float(result.get("conviction") or 0.0)

    if (
        wrec
        and an > 0
        and bn > 0
        and str(result.get("verdict") or "") == "ALIGNED"
    ):
        try:
            im = float(result.get("intermarket_multiplier") or 1.0)
        except (TypeError, ValueError):
            im = 1.0
        blended = (an * wrec["A"] + bn * wrec["B"]) * im
        c0 = max(0.0, min(1.0, blended))

    new_c = max(0.0, min(1.0, c0 + mod))
    result["conviction_pre_ai_weight_adjust"] = result.get("conviction")
    result["conviction"] = round(new_c, 4)

    if wrec and an > 0 and bn > 0 and str(result.get("verdict") or "") == "ALIGNED":
        result["engine_weights"] = dict(wrec)

    from engine_c import classify_conviction

    tier, sizing = classify_conviction(new_c)
    result["tier"] = tier
    result["sizing_override"] = round(sizing, 4)

    if tier == "SKIP":
        result["trade"] = False
        result["decision_state"] = "blocked"
        result["sizing_override"] = 0.0
        result["ai_weight_adjustment_applied"] = True
        return result

    if not result.get("trade"):
        if tier == "SKIP":
            result["decision_state"] = "blocked"
    result["ai_weight_adjustment_applied"] = True
    return result
