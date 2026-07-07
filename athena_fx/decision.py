"""Factor-first decision engine for the athena_fx lane (Phase 9)."""
from __future__ import annotations

import logging
from typing import Any

from config import CONFIG

from athena_fx.models import (
    ACTION_BUY,
    ACTION_FLATTEN,
    ACTION_NO_TRADE,
    ACTION_REDUCE,
    ACTION_SELL,
    FAMILY_CARRY,
    FAMILY_MOMENTUM,
    FAMILY_VALUE,
    GATE_FAIL,
    PRIMARY_FAMILIES,
    SCORE_CAP_CARRY,
    SCORE_CAP_COT_MODIFIER,
    SCORE_CAP_MOMENTUM,
    SCORE_CAP_VALUE,
    CotOverlayResult,
    Diagnostic,
    FamilyScore,
    FxDecision,
    GateResult,
)

logger = logging.getLogger("athena_fx.decision")

_FORBIDDEN_SUBKEYS = frozenset({"tsmom", "xs_momentum"})


def _normalize_family_score(raw: FamilyScore | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, FamilyScore):
        return raw.to_dict()
    return dict(raw)


def _normalize_cot(raw: CotOverlayResult | dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, CotOverlayResult):
        return raw.to_dict()
    return dict(raw)


def _family_points(family: str, score: float | None, direction: str) -> float:
    if score is None:
        return 0.0
    sign = 1.0 if direction == "bullish" else -1.0 if direction == "bearish" else 0.0
    if sign == 0.0:
        return 0.0

    if family == FAMILY_CARRY:
        full_scale = float(CONFIG.get("FX_FACTOR_CARRY_FULL_SCALE", 0.05) or 0.05)
        normalized = max(-1.0, min(1.0, score / full_scale))
        return sign * abs(normalized * SCORE_CAP_CARRY)
    if family == FAMILY_MOMENTUM:
        normalized = max(-1.0, min(1.0, score))
        return sign * abs(normalized * SCORE_CAP_MOMENTUM)
    if family == FAMILY_VALUE:
        full_scale = float(CONFIG.get("FX_FACTOR_VALUE_FULL_SCALE", 2.0) or 2.0)
        normalized = max(-1.0, min(1.0, score / full_scale))
        return sign * abs(normalized * SCORE_CAP_VALUE)
    return 0.0


def _factor_score_entry(family: str, normalized: dict[str, Any]) -> dict[str, Any]:
    score = normalized.get("score")
    direction = normalized.get("direction", "neutral")
    status = normalized.get("status", "ok")
    points = _family_points(family, score, direction) if status == "ok" else 0.0
    return {
        "score": score,
        "direction": direction,
        "points": points,
        "status": status,
    }


def _gate_action_for_failure(
    gate: GateResult, has_existing_position: bool
) -> tuple[str, str | None]:
    """Map a failing gate to decision action and direction placeholder."""
    action = gate.recommended_action
    if has_existing_position and action == "flatten":
        return ACTION_FLATTEN, None
    if has_existing_position and action == "reduce":
        return ACTION_REDUCE, None
    return ACTION_NO_TRADE, None


def decide(
    symbol: str,
    as_of_date: str,
    *,
    family_scores: dict[str, FamilyScore | dict],
    cot: CotOverlayResult | dict | None,
    gate_results: list[GateResult],
    has_existing_position: bool = False,
) -> FxDecision:
    """Produce an ``FxDecision`` from family scores, COT overlay, and hard gates."""
    forbidden = _FORBIDDEN_SUBKEYS.intersection(family_scores.keys())
    if forbidden:
        raise ValueError(
            f"momentum subcomponents cannot satisfy alignment: {sorted(forbidden)}"
        )

    cot_data = _normalize_cot(cot)
    vol_action = "allow"
    cost_diag: dict[str, Any] = {}
    gate_size_mult = 1.0

    normalized: dict[str, dict[str, Any]] = {}
    all_diagnostics: list[Diagnostic] = []
    for fam in PRIMARY_FAMILIES:
        raw = family_scores.get(fam)
        if raw is None:
            continue
        nd = _normalize_family_score(raw)
        normalized[fam] = nd
        for d in nd.get("diagnostics") or []:
            if isinstance(d, dict):
                all_diagnostics.append(
                    Diagnostic(
                        code=d.get("code", ""),
                        severity=d.get("severity", "info"),
                        message=d.get("message", ""),
                        context=d.get("context") or {},
                    )
                )
            elif isinstance(d, Diagnostic):
                all_diagnostics.append(d)

    aligned_long: list[str] = []
    aligned_short: list[str] = []
    blocked: list[str] = []

    for fam, nd in normalized.items():
        direction = nd.get("direction", "neutral")
        status = nd.get("status", "invalid")
        if status != "ok":
            blocked.append(fam)
            continue
        if direction == "bullish":
            aligned_long.append(fam)
        elif direction == "bearish":
            aligned_short.append(fam)
        else:
            blocked.append(fam)

    for gate in gate_results:
        if gate.gate_name == "volatility":
            vol_action = gate.recommended_action
        if gate.gate_name == "cost":
            cost_diag = dict(gate.numeric_inputs)

        if gate.result == GATE_FAIL:
            action, _ = _gate_action_for_failure(gate, has_existing_position)
            reason = f"gate failure: {gate.gate_name} ({gate.reason_code})"
            factor_scores = {
                fam: _factor_score_entry(fam, nd) for fam, nd in normalized.items()
            }
            gate_aligned: list[str] = []
            if len(aligned_long) >= 2 and len(aligned_short) == 0:
                gate_aligned = aligned_long
            elif len(aligned_short) >= 2 and len(aligned_long) == 0:
                gate_aligned = aligned_short
            gate_total = (
                sum(float(factor_scores[fam].get("points") or 0.0) for fam in gate_aligned)
                if gate_aligned
                else None
            )
            return FxDecision(
                symbol=symbol,
                as_of_date=as_of_date,
                action=action,
                direction=None,
                aligned_families=gate_aligned,
                blocked_families=sorted(set(blocked)),
                factor_scores=factor_scores,
                total_score=gate_total,
                cot_overlay=cot_data,
                gate_results=list(gate_results),
                volatility_action=vol_action,
                size_multiplier=0.0,
                cost_diagnostics=cost_diag,
                reason=reason,
                diagnostics=all_diagnostics,
            )
        if gate.result != GATE_FAIL:
            gate_size_mult *= gate.size_multiplier

    trade_direction: str | None = None
    aligned: list[str] = []
    if len(aligned_long) >= 2 and len(aligned_short) == 0:
        trade_direction = "LONG"
        aligned = aligned_long
    elif len(aligned_short) >= 2 and len(aligned_long) == 0:
        trade_direction = "SHORT"
        aligned = aligned_short
    else:
        if len(aligned_long) >= 2 and len(aligned_short) >= 2:
            blocked.extend(f for f in aligned_long + aligned_short if f not in blocked)
        reason = "insufficient_family_alignment"
        return FxDecision(
            symbol=symbol,
            as_of_date=as_of_date,
            action=ACTION_NO_TRADE,
            direction=None,
            aligned_families=[],
            blocked_families=sorted(set(blocked)),
            cot_overlay=cot_data,
            gate_results=list(gate_results),
            volatility_action=vol_action,
            size_multiplier=gate_size_mult,
            cost_diagnostics=cost_diag,
            reason=reason,
            diagnostics=all_diagnostics,
        )

    # COT veto blocks aligned direction only
    if cot_data and cot_data.get("action") == "veto":
        return FxDecision(
            symbol=symbol,
            as_of_date=as_of_date,
            action=ACTION_NO_TRADE,
            direction=None,
            aligned_families=aligned,
            blocked_families=sorted(set(blocked)),
            cot_overlay=cot_data,
            gate_results=list(gate_results),
            volatility_action=vol_action,
            size_multiplier=0.0,
            cost_diagnostics=cost_diag,
            reason="cot_double_crowded_veto",
            diagnostics=all_diagnostics,
        )

    factor_scores: dict[str, Any] = {}
    total = 0.0
    for fam in aligned:
        nd = normalized[fam]
        factor_scores[fam] = _factor_score_entry(fam, nd)
        total += float(factor_scores[fam].get("points") or 0.0)

    cot_modifier = 0.0
    size_mult = gate_size_mult
    if cot_data and cot_data.get("action") == "downweight":
        cot_modifier = float(cot_data.get("modifier") or 0.0)
        cot_modifier = max(
            -SCORE_CAP_COT_MODIFIER,
            min(SCORE_CAP_COT_MODIFIER, cot_modifier),
        )
        size_mult *= 0.5

    total += cot_modifier

    min_score = CONFIG.get("FX_FACTOR_MIN_TOTAL_SCORE", None)
    if min_score is not None and abs(total) < float(min_score):
        return FxDecision(
            symbol=symbol,
            as_of_date=as_of_date,
            action=ACTION_NO_TRADE,
            direction=None,
            aligned_families=aligned,
            blocked_families=sorted(set(blocked)),
            factor_scores=factor_scores,
            total_score=total,
            cot_overlay=cot_data,
            gate_results=list(gate_results),
            volatility_action=vol_action,
            size_multiplier=size_mult,
            cost_diagnostics=cost_diag,
            reason=f"total_score {total:.2f} below threshold {float(min_score):.2f}",
            diagnostics=all_diagnostics,
        )

    action = ACTION_BUY if trade_direction == "LONG" else ACTION_SELL
    dir_label = "long" if trade_direction == "LONG" else "short"
    reason = f"{len(aligned)} families aligned {dir_label}; total_score={total:.2f}"

    return FxDecision(
        symbol=symbol,
        as_of_date=as_of_date,
        action=action,
        direction=trade_direction,
        aligned_families=aligned,
        blocked_families=sorted(set(blocked)),
        factor_scores=factor_scores,
        total_score=total,
        cot_overlay=cot_data,
        gate_results=list(gate_results),
        volatility_action=vol_action,
        size_multiplier=size_mult,
        cost_diagnostics=cost_diag,
        reason=reason,
        diagnostics=all_diagnostics,
    )
