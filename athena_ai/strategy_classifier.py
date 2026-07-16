"""Deterministic strategy-playbook classifier.

Reads price-action facts, engine summaries, and the playbook universe; ranks
candidate playbooks and ALWAYS records which playbooks were rejected and why.
The classifier never approves trades, never mutates engine results, and never
silently suppresses a candidate just because Engine A is "hot".

Spec rules (see prompt §4):
  * failed breakout/reclaim must NOT be classified as breakout continuation
  * WICK_REJECTION_AT_KEY_LEVEL requires a meaningful level; mid-range wicks
    are rejected with an explicit reason
  * balance/chop around POC prefers BALANCE_CHOP_NO_TRADE
  * a high Engine A score does NOT override a missing setup model — but it
    raises a classification_warning so the AI can see the conflict
"""

from __future__ import annotations

from typing import Any

from athena_ai.strategy_playbook_loader import get_enabled_playbooks
from style_resolver import normalize_style

# Numeric tags used purely for ranking — not exposed as a "score".
_HINT_TO_PLAYBOOK_AFFINITY: dict[str, dict[str, int]] = {
    "failed_breakout_hint": {"FAILED_BREAKOUT_REVERSAL": 3},
    "breakout_continuation_hint": {"TREND_CONTINUATION_PULLBACK": 2},
    "wick_rejection_hint": {"WICK_REJECTION_AT_KEY_LEVEL": 3, "LIQUIDITY_SWEEP_REVERSAL": 1},
    "liquidity_sweep_hint": {"LIQUIDITY_SWEEP_REVERSAL": 3},
    "balance_chop_hint": {"BALANCE_CHOP_NO_TRADE": 3},
    "return_to_poc_hint": {"RETURN_TO_POC_MEAN_REVERSION": 3},
}


def _engine_a_score_pct(engine_a_summary: dict[str, Any] | None) -> float | None:
    if not isinstance(engine_a_summary, dict):
        return None
    score = engine_a_summary.get("confluence_score") or engine_a_summary.get("score")
    max_score = engine_a_summary.get("max_score_override") or engine_a_summary.get("max_score")
    try:
        s = float(score) if score is not None else None
        m = float(max_score) if max_score is not None else None
    except (TypeError, ValueError):
        return None
    if s is None or not m or m <= 0:
        return None
    return s / m


def _has_failed_breakout_in_direction(
    breakout_state: dict[str, Any], direction: str | None
) -> bool:
    if breakout_state.get("state") != "failed_breakout":
        return False
    side = breakout_state.get("side")
    if not direction:
        return True
    d = str(direction).upper()
    if d == "LONG" and side == "upper":
        return True
    if d == "SHORT" and side == "lower":
        return True
    return False


def _wick_has_named_level(
    profile_location: dict[str, Any],
    breakout_state: dict[str, Any],
) -> bool:
    """A wick is 'at a level' only if a level was actually identified.

    We accept POC/VAH/VAL proximity (profile_location with a non-unknown
    location and known POC) OR a breakout state with a known level.
    """
    if breakout_state.get("level") is not None and breakout_state.get("state") != "unknown":
        return True
    if profile_location.get("poc") is None:
        return False
    loc = profile_location.get("location")
    if loc in ("above_vah", "below_val", "near_poc"):
        return True
    return False


def _asset_group_matches(playbook_groups: list[str], asset_group: str | None) -> bool:
    if not asset_group:
        return True
    normalized = asset_group.strip().lower()
    for group in playbook_groups:
        if not isinstance(group, str):
            continue
        if group.strip() == "*":
            return True
        if group.strip().lower() == normalized:
            return True
    return False


def classify_strategy(
    *,
    price_action_facts: dict[str, Any],
    engine_a_summary: dict[str, Any] | None,
    engine_b_summary: dict[str, Any] | None = None,
    engine_d_summary: dict[str, Any] | None = None,
    asset_group: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    style: str | None = None,
    direction: str | None = None,
    playbook_path: str | None = None,
) -> dict[str, Any]:
    """Return {candidate_models, rejected_models, classification_warnings}.

    Returns a flat opinion the AI may override. NEVER approves a trade.
    """
    facts = price_action_facts or {}
    setup_hints = (facts.get("setup_candidates") or {}).get("hints", [])
    wick = facts.get("wick_event") or {}
    breakout = facts.get("breakout_state") or {}
    profile_loc = facts.get("profile_location") or {}
    profile_vp_ctx = facts.get("profile_vp_context") or {}

    del symbol  # reserved for future per-symbol gating
    evaluation_style = normalize_style(style)
    if evaluation_style == "auto":
        evaluation_style = None
    evaluation_timeframe = str(timeframe or "").strip().upper() or None

    engines_present: set[str] = {"A"}
    if isinstance(engine_b_summary, dict) and engine_b_summary.get("available", True):
        engines_present.add("B")
    if isinstance(engine_d_summary, dict) and engine_d_summary.get("available", True):
        engines_present.add("D")

    # Universe is every enabled playbook matching the asset group.
    # Engine availability is enforced HERE as a rejection reason rather than a
    # silent universe filter, so the AI sees what the classifier considered.
    universe = [
        p for p in get_enabled_playbooks(path=playbook_path)
        if _asset_group_matches(p.get("asset_groups") or [], asset_group)
    ]

    rejected: list[dict[str, Any]] = []

    # Engine-availability rejection — surfaced as rejection_reason, not as a
    # silent universe filter, so the AI sees that the model was considered.
    universe_after_engine: list[dict[str, Any]] = []
    deferred_engine_warnings: list[str] = []
    for playbook in universe:
        declared = {str(e).strip().upper() for e in playbook.get("engines") or []}
        if declared and not (declared & engines_present):
            rejected.append({
                "id": playbook["id"],
                "rejection_reason": (
                    f"Playbook declares engines {sorted(declared)}; none of those "
                    "engine summaries were supplied to this review."
                ),
            })
            continue
        missing = declared - engines_present
        if declared and missing:
            deferred_engine_warnings.append(
                f"Playbook {playbook['id']} normally uses engines "
                f"{sorted(declared)}; only {sorted(declared & engines_present)} "
                "available - degraded engine context."
            )
        universe_after_engine.append(playbook)

    # Per-playbook affinity from setup hints. A model with zero hints can still
    # be a candidate at affinity=0 — the AI will weigh it from the playbook
    # rule text. We only reject when there's a hard contraindication.
    affinity: dict[str, int] = {p["id"]: 0 for p in universe_after_engine}
    for hint in setup_hints:
        for pid, weight in _HINT_TO_PLAYBOOK_AFFINITY.get(hint, {}).items():
            if pid in affinity:
                affinity[pid] += weight

    warnings: list[str] = list(deferred_engine_warnings)

    if not profile_vp_ctx.get("enabled", True):
        if "RETURN_TO_POC_MEAN_REVERSION" in affinity:
            rejected.append({
                "id": "RETURN_TO_POC_MEAN_REVERSION",
                "rejection_reason": (
                    "Engine B volume profile (POC/VAH/VAL) is disabled for this asset class "
                    "due to unreliable volume feeds."
                ),
            })
            affinity.pop("RETURN_TO_POC_MEAN_REVERSION", None)

    failed_in_direction = _has_failed_breakout_in_direction(breakout, direction)

    # Hard rule: failed breakout in trade direction kills any continuation
    if failed_in_direction and "TREND_CONTINUATION_PULLBACK" in affinity:
        rejected.append({
            "id": "TREND_CONTINUATION_PULLBACK",
            "rejection_reason": (
                "Most recent move on the chart timeframe is a failed breakout in the trade "
                f"direction (side={breakout.get('side')}, level={breakout.get('level')}); "
                "trend continuation is not valid after a failed-breakout close-back-inside."
            ),
        })
        affinity.pop("TREND_CONTINUATION_PULLBACK", None)

    # WICK_REJECTION_AT_KEY_LEVEL requires a NAMED level
    if "WICK_REJECTION_AT_KEY_LEVEL" in affinity:
        if wick.get("type") in ("upper_rejection", "lower_rejection"):
            if not _wick_has_named_level(profile_loc, breakout):
                rejected.append({
                    "id": "WICK_REJECTION_AT_KEY_LEVEL",
                    "rejection_reason": (
                        "Wick rejection detected but no named level (POC/VAH/VAL or "
                        "breakout level) is present within range — a mid-range wick "
                        "is not a key-level rejection."
                    ),
                })
                affinity.pop("WICK_REJECTION_AT_KEY_LEVEL", None)
        else:
            rejected.append({
                "id": "WICK_REJECTION_AT_KEY_LEVEL",
                "rejection_reason": (
                    f"No upper/lower rejection wick on the last candle "
                    f"(wick_event.type={wick.get('type')})."
                ),
            })
            affinity.pop("WICK_REJECTION_AT_KEY_LEVEL", None)

    # RETURN_TO_POC_MEAN_REVERSION not valid in strong trending regimes
    regime_label = (
        (engine_a_summary or {}).get("regime")
        or (engine_a_summary or {}).get("regime_name")
        or ""
    )
    if "RETURN_TO_POC_MEAN_REVERSION" in affinity:
        if "trend" in str(regime_label).lower():
            rejected.append({
                "id": "RETURN_TO_POC_MEAN_REVERSION",
                "rejection_reason": (
                    f"Engine A regime label='{regime_label}' indicates a trending market; "
                    "mean-reversion to POC is not the right model here."
                ),
            })
            affinity.pop("RETURN_TO_POC_MEAN_REVERSION", None)

    # BALANCE_CHOP_NO_TRADE prefers itself over any directional model when
    # price is near POC with no displacement — we DO surface this preference
    # as a warning, but we do NOT remove the directional candidates outright.
    near_poc = profile_loc.get("location") == "near_poc"
    no_displacement = (facts.get("volume_behavior") or {}).get("state") in ("contraction", "normal")
    if near_poc and no_displacement and "BALANCE_CHOP_NO_TRADE" in affinity:
        affinity["BALANCE_CHOP_NO_TRADE"] += 2
        warnings.append(
            "Price near POC with no volume expansion — prefer BALANCE_CHOP_NO_TRADE unless "
            "a clean edge break or sweep appears in the next candle."
        )

    # Engine A score warning — NEVER suppresses a candidate
    engine_a_pct = _engine_a_score_pct(engine_a_summary)
    if (
        engine_a_pct is not None
        and engine_a_pct >= 0.85
        and not setup_hints
    ):
        warnings.append(
            f"Engine A score is high (~{engine_a_pct:.0%}) but no price-action setup hints "
            "were derived from the bars — do not let the Engine A score force a directional "
            "playbook on chop."
        )

    # Build ranked candidates from surviving affinity entries.
    survivors = sorted(affinity.items(), key=lambda kv: kv[1], reverse=True)
    universe_by_id = {p["id"]: p for p in universe_after_engine}

    candidates: list[dict[str, Any]] = []
    for pid, weight in survivors:
        playbook = universe_by_id.get(pid)
        if not playbook:
            continue
        candidates.append({
            "id": pid,
            "affinity": weight,
            "evaluation_style": evaluation_style,
            "evaluation_timeframe": evaluation_timeframe,
            "supporting_hints": [
                h for h in setup_hints
                if pid in _HINT_TO_PLAYBOOK_AFFINITY.get(h, {})
            ],
            "playbook": playbook,
        })

    # Any playbook in the universe that has zero affinity AND is a directional
    # model gets surfaced as unsupported by the current bars unless we already
    # rejected it for a hard reason above.
    rejected_ids = {r["id"] for r in rejected}
    if not setup_hints:
        for pid, weight in survivors:
            if weight == 0 and pid not in ("BALANCE_CHOP_NO_TRADE",) and pid not in rejected_ids:
                rejected.append({
                    "id": pid,
                    "rejection_reason": (
                        "No price-action hint supports this playbook on the current bars; "
                        "AI may still adopt it with explicit visual justification."
                    ),
                })
                rejected_ids.add(pid)

    candidates = [
        candidate for candidate in candidates
        if candidate["id"] not in rejected_ids
    ]

    return {
        "candidate_models": candidates,
        "rejected_models": rejected,
        "classification_warnings": warnings,
        "engines_considered": sorted(engines_present),
        "evaluation_style": evaluation_style,
        "evaluation_timeframe": evaluation_timeframe,
    }
