"""Build the AI strategy-playbook layer that gets attached to chart-review payloads.

Read-only. Does not touch execution, does not mutate engine results, does not
change thresholds. Produces an ADDITIVE block (`strategy_layer`) that lives
alongside the existing AI chart-review contract.

Honoured FIXes (see project prompt):
  * FIX 1 - rejected playbooks flow downstream with rejection_reason
  * FIX 2 - bounded window of raw OHLCV bars is attached; a Vision routing TODO
            marker is placed for the team to wire Vision when budget allows
  * FIX 3 - price-action facts are self-describing (raw + confidence)
  * FIX 4 - schema_metadata advertises the loaded playbook IDs the AI may pick
  * FIX 5 - classifier_agreement enum is documented for the AI in the schema
"""

from __future__ import annotations

from typing import Any

from athena_ai.ai_review_schema import schema_metadata
from athena_ai.candle_understanding import derive_candle_understanding
from athena_ai.price_action_facts import derive_price_action_facts
from athena_ai.strategy_classifier import classify_strategy
from athena_ai.strategy_playbook_loader import (
    get_enabled_playbooks,
    loaded_playbook_ids,
)
from style_resolver import normalize_style, resolve_auto_style

_DEFAULT_OHLCV_LIMIT = 80
_VISION_TODO = (
    "vision_routing_pending: this payload includes a bounded OHLCV window but "
    "does NOT yet route bars through Vision; AI must reason from the screenshot "
    "and the OHLCV window only. Wire Vision when budget permits."
)


def _bounded_ohlcv(window: list[dict[str, Any]] | None, limit: int) -> list[dict[str, Any]]:
    """Return at most `limit` most-recent bars, copied and key-normalised."""
    if not window:
        return []
    tail = list(window)[-int(max(1, limit)):]
    cleaned: list[dict[str, Any]] = []
    for bar in tail:
        if not isinstance(bar, dict):
            continue
        cleaned.append({
            "time": bar.get("time") or bar.get("t"),
            "open": bar.get("open") or bar.get("o"),
            "high": bar.get("high") or bar.get("h"),
            "low": bar.get("low") or bar.get("l"),
            "close": bar.get("close") or bar.get("c"),
            "volume": bar.get("volume") or bar.get("v"),
        })
    return cleaned


def engine_a_summary_has_data(engine_a_ctx: dict[str, Any] | None) -> bool:
    if not isinstance(engine_a_ctx, dict) or not engine_a_ctx:
        return False
    return bool(
        engine_a_ctx.get("regime")
        or engine_a_ctx.get("confluence_score")
        or engine_a_ctx.get("structure_context")
        or engine_a_ctx.get("atr")
    )


def _engine_a_summary_from_ctx(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    """Extract the slice of Engine A the classifier needs.

    The classifier only reads `regime`, `confluence_score`, `max_score_override`
    etc. — never the whole context. Keeping this narrow makes it obvious which
    Engine A fields drive playbook ranking.
    """
    if not isinstance(engine_a_ctx, dict):
        return {}
    return {
        "regime": engine_a_ctx.get("regime")
        or engine_a_ctx.get("regime_name")
        or (engine_a_ctx.get("structure_context") or {}).get("regime"),
        "confluence_score": engine_a_ctx.get("confluence_score")
        or engine_a_ctx.get("score"),
        "max_score": engine_a_ctx.get("max_score"),
        "max_score_override": engine_a_ctx.get("max_score_override"),
        "passed": engine_a_ctx.get("passed"),
        "direction": engine_a_ctx.get("direction") or engine_a_ctx.get("bias"),
    }


def build_strategy_layer(
    *,
    engine_a_ctx: dict[str, Any],
    ohlcv_window: list[dict[str, Any]] | None = None,
    engine_b_summary: dict[str, Any] | None = None,
    engine_d_summary: dict[str, Any] | None = None,
    named_levels: dict[str, float] | None = None,
    liquidity_pools: list[float] | None = None,
    direction: str | None = None,
    asset_group: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    style: str | None = None,
    ohlcv_limit: int = _DEFAULT_OHLCV_LIMIT,
    playbook_path: str | None = None,
) -> dict[str, Any]:
    """Build the strategy-playbook layer attached to AI chart review payloads.

    The shape is additive — it does not replace existing chart-review keys and
    must never be interpreted as an execution decision.
    """

    resolved_direction = (
        direction
        or (engine_a_ctx or {}).get("direction")
        or (engine_a_ctx or {}).get("bias")
    )
    resolved_asset_group = asset_group or (engine_a_ctx or {}).get("asset_group")
    resolved_style = normalize_style(
        style
        or (engine_a_ctx or {}).get("analyze_style")
        or (engine_a_ctx or {}).get("scoring_style")
        or (engine_a_ctx or {}).get("style")
        or (engine_a_ctx or {}).get("horizon")
        or (engine_b_summary or {}).get("style")
    )
    if resolved_style == "auto":
        score_group = (
            (engine_a_ctx or {}).get("scoreGroup")
            or (engine_a_ctx or {}).get("score_group")
            or (engine_a_ctx or {}).get("asset_group")
            or (engine_b_summary or {}).get("scoreGroup")
            or (engine_b_summary or {}).get("score_group")
        )
        asset_type = (
            (engine_a_ctx or {}).get("type")
            or (engine_a_ctx or {}).get("asset_type")
            or (engine_a_ctx or {}).get("asset_class")
            or (engine_b_summary or {}).get("type")
            or (engine_b_summary or {}).get("asset_type")
            or resolved_asset_group
        )
        resolved_style = resolve_auto_style(
            "auto",
            {"type": asset_type},
            score_group=str(score_group) if score_group else None,
            asset_type=str(asset_type) if asset_type else None,
        )

    bounded_bars = _bounded_ohlcv(ohlcv_window, ohlcv_limit)

    facts = derive_price_action_facts(
        engine_a_ctx=engine_a_ctx or {},
        ohlcv_window=bounded_bars or None,
        direction=str(resolved_direction) if resolved_direction else None,
        named_levels=named_levels,
        liquidity_pools=liquidity_pools,
    )

    candle_understanding = facts.get("candle_understanding")
    if candle_understanding is None and bounded_bars:
        engine_label = "D" if engine_d_summary and not engine_a_summary_has_data(engine_a_ctx) else "A"
        candle_understanding = derive_candle_understanding(
            candles=bounded_bars,
            engine_ctx=engine_a_ctx or {},
            direction=str(resolved_direction) if resolved_direction else None,
            timeframe=timeframe,
            engine=engine_label,
            named_levels=named_levels,
            liquidity_pools=liquidity_pools,
        )

    engine_a_summary = _engine_a_summary_from_ctx(engine_a_ctx or {})

    classification = classify_strategy(
        price_action_facts=facts,
        engine_a_summary=engine_a_summary,
        engine_b_summary=engine_b_summary,
        engine_d_summary=engine_d_summary,
        asset_group=str(resolved_asset_group) if resolved_asset_group else None,
        symbol=symbol,
        timeframe=timeframe,
        style=resolved_style,
        direction=str(resolved_direction) if resolved_direction else None,
        playbook_path=playbook_path,
    )

    enabled_playbooks = get_enabled_playbooks(path=playbook_path)
    playbook_index = [
        {
            "id": p["id"],
            "description": (p.get("description") or "").strip(),
            "ai_review_rule": (p.get("ai_review_rule") or "").strip(),
            "engines": list(p.get("engines") or []),
            "asset_groups": list(p.get("asset_groups") or []),
            "invalid_if": list(p.get("invalid_if") or []),
            "confirmation": list(p.get("confirmation") or []),
            "invalidation_logic": (p.get("invalidation_logic") or "").strip(),
            "target_logic": (p.get("target_logic") or "").strip(),
        }
        for p in enabled_playbooks
    ]

    return {
        "schema_version": 1,
        "schema_metadata": schema_metadata(),
        "playbook_universe": playbook_index,
        "loaded_playbook_ids": list(loaded_playbook_ids(path=playbook_path)),
        "price_action_facts": facts,
        "candle_understanding": candle_understanding,
        "candidate_models": classification.get("candidate_models", []),
        "rejected_models": classification.get("rejected_models", []),
        "classification_warnings": classification.get("classification_warnings", []),
        "engines_considered": classification.get("engines_considered", []),
        "evaluation_style": classification.get("evaluation_style"),
        "evaluation_timeframe": classification.get("evaluation_timeframe"),
        "raw_ohlcv_window": {
            "bars": bounded_bars,
            "bar_count": len(bounded_bars),
            "limit": int(ohlcv_limit),
        },
        "vision_routing_todo": _VISION_TODO,
        "advisory_only_notice": (
            "This block is read-only context for the AI reviewer. It must NOT "
            "be used to approve, size, or execute trades, and it does not "
            "override Engine A/B/C/D scoring or thresholds."
        ),
    }


def render_strategy_block_for_prompt(strategy_layer: dict[str, Any]) -> str:
    """Render the strategy layer as a compact text block for prompt inlining.

    Kept small on purpose — the bulky data (full playbook bodies, full OHLCV
    window) is delivered through the JSON payload, not duplicated in the
    prompt text.
    """
    candidates = strategy_layer.get("candidate_models") or []
    rejected = strategy_layer.get("rejected_models") or []
    warnings = strategy_layer.get("classification_warnings") or []
    facts = strategy_layer.get("price_action_facts") or {}
    meta = strategy_layer.get("schema_metadata") or {}

    lines: list[str] = []
    lines.append("== STRATEGY PLAYBOOK LAYER (read-only, advisory) ==")
    lines.append(
        "The deterministic classifier has produced an OPINION. You MAY override "
        "it but must explain WHY in plain_english_reason and set "
        "classifier_agreement accordingly."
    )
    lines.append(
        "Evaluate playbooks for selected style/timeframe only: "
        f"style={strategy_layer.get('evaluation_style') or 'unresolved'} "
        f"timeframe={strategy_layer.get('evaluation_timeframe') or 'unresolved'}."
    )
    lines.append("")
    lines.append("Candidate playbooks (ranked):")
    if not candidates:
        lines.append("  (none — classifier found no positive playbook signal)")
    for c in candidates:
        lines.append(
            f"  - {c.get('id')} (affinity={c.get('affinity')}) "
            f"supporting_hints={c.get('supporting_hints') or []}"
        )

    lines.append("")
    lines.append("Rejected playbooks (with reasons - FIX 1):")
    if not rejected:
        lines.append("  (none rejected)")
    for r in rejected:
        lines.append(f"  - {r.get('id')}: {r.get('rejection_reason')}")

    if warnings:
        lines.append("")
        lines.append("Classification warnings:")
        for w in warnings:
            lines.append(f"  ! {w}")

    candle_u = strategy_layer.get("candle_understanding") or facts.get("candle_understanding") or {}
    lines.append("")
    lines.append("CANDLE UNDERSTANDING read order (mandatory — use structured facts, do not invent patterns):")
    lines.append("  1. Regime gate (trend/range/unknown, ADX, HA streak, allow_long/allow_short)")
    lines.append("  2. Location (POC/VAH/VAL, named levels, distance to liquidity pool)")
    lines.append("  3. Last 3 candle anatomy (body/wick ratios, rejection/displacement shapes)")
    lines.append("  4. Sweep / reclaim / BOS / CHoCH / FVG / OB context")
    lines.append("  5. Volume / effort-vs-result / absorption")
    lines.append("  6. Directional bias, entry quality, invalidation, RR — advisory only")
    lines.append("  Distinguish: confirmed sweep | possible sweep | clean BOS | random wick/noise | regime-suppressed candle")
    if candle_u:
        lines.append(f"  Structured candle_understanding: {candle_u}")

    lines.append("")
    lines.append("Price-action facts (self-describing - FIX 3):")
    for key, value in facts.items():
        if key == "candle_understanding":
            continue
        lines.append(f"  {key}: {value}")

    lines.append("")
    lines.append("Required verdict schema:")
    lines.append(
        "  Return an additional top-level key `strategyPlaybookVerdict` "
        "matching the StrategyPlaybookVerdict schema with required fields: "
        + ", ".join(meta.get("required_fields", []))
    )
    lines.append(
        "  matched_strategy_model MUST be one of: "
        + ", ".join(meta.get("matched_strategy_model_choices", []))
    )
    lines.append(
        "  classifier_agreement MUST be one of: "
        + ", ".join((meta.get("enums") or {}).get("classifier_agreement", []))
    )
    lines.append(
        f"  Vision: {strategy_layer.get('vision_routing_todo')}"
    )
    lines.append("")
    return "\n".join(lines)
