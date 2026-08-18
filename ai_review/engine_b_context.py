"""Assemble server-trusted Engine B context for TV chart AI review.

Timeframe contract: the roles stamped on the signal (and re-resolved through
``resolve_timeframe_policy``) are authoritative — ``regimeTf``/``biasTf``/
``structureTf``/``setupTf``/``triggerTf``/``executionTf`` plus ``atrTf``. The
chart interval the reviewer is looking at is presentation only and must never
override, correct, or infer a role.

Engine B is top-down and sequential: HTF bias (Daily primary for intraday,
Weekly+Daily for swing) -> MTF confirmation on the structure/setup rungs -> LTF
entry on the trigger rung. Under ``ENGINE_B_HIERARCHICAL_TF`` the policy bias
rung is Daily and the payload carries ``hierarchicalBias`` (applied,
sequenceRequired, htfBiasTf, mtfConfirmationTf, ltfEntryTf); the scoring side of
the same model is ``ENGINE_B_BIAS_MODE`` with ``biasMode``/``htfBias``/
``hierarchy`` on the context. All of it is advisory review context — the
deterministic gates already applied whatever effect the mode had, and a legacy
bias mode (no hierarchy fields) is a fully supported configuration, not a gap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from market_structure import sanitize_engine_b_structure_profile_fields
from scoring import get_pair_score_group
from style_resolver import resolve_auto_style
from timeframe_policy import resolve_timeframe_policy, speed_state_from_policy_payload

from ai_review.engine_a_context import (
    build_engine_b_prompt_context,
    build_engine_b_summary_for_strategy,
    select_ohlcv_bars_for_chart,
)


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbol_keys(symbol: str) -> set[str]:
    raw = str(symbol or "").strip().upper()
    compact = raw.replace("/", "").replace("-", "").replace(" ", "")
    return {k for k in (raw, compact) if k}


def _row_matches_symbol(row: dict[str, Any], symbol: str) -> bool:
    keys = _symbol_keys(symbol)
    for field in ("symbol", "pair", "display"):
        val = str(row.get(field) or "").strip().upper()
        if not val:
            continue
        compact = val.replace("/", "").replace("-", "").replace(" ", "")
        if val in keys or compact in keys:
            return True
    return False


def _pick_best_engine_b_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    matches = [r for r in rows if isinstance(r, dict) and _row_matches_symbol(r, symbol)]
    if not matches:
        return None
    directional = [
        r
        for r in matches
        if str(r.get("direction") or "NONE").upper() in ("LONG", "SHORT")
    ]
    pool = directional or matches
    return max(pool, key=lambda r: _to_float(r.get("confluenceScore")) or 0.0)


def _resolve_style(
    screenshot_meta: dict[str, Any] | None,
    seed: dict[str, Any],
    pair: dict[str, Any],
) -> str:
    meta = screenshot_meta or {}
    for key in ("horizon", "style", "selectedStyle", "resolvedStyle"):
        raw = str(seed.get(key) or "").strip().lower()
        if raw in ("scalp", "intraday", "swing"):
            return raw
    for key in ("analyze_style", "signal_style", "style"):
        raw = str(meta.get(key) or "").strip().lower()
        if raw in ("scalp", "intraday", "swing"):
            return raw
    try:
        score_group = get_pair_score_group(pair)
    except Exception:
        score_group = None
    return resolve_auto_style(
        "auto",
        pair,
        score_group=score_group,
        asset_type=pair.get("type") or pair.get("asset_type"),
    )


def _resolve_direction(
    symbol: str,
    screenshot_meta: dict[str, Any] | None,
    seed: dict[str, Any] | None,
    *,
    last_engine_b_rows_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> str | None:
    meta = screenshot_meta or {}
    for key in ("candidate_direction", "direction"):
        direction = str(meta.get(key) or (seed or {}).get(key) or "").upper()
        if direction in ("LONG", "SHORT"):
            return direction
    if last_engine_b_rows_fn:
        try:
            rows = last_engine_b_rows_fn() or []
        except Exception:
            rows = []
        row = _pick_best_engine_b_row([r for r in rows if isinstance(r, dict)], symbol)
        if row:
            direction = str(row.get("direction") or "").upper()
            if direction in ("LONG", "SHORT"):
                return direction
    return None


def _engine_b_passed(res: dict[str, Any]) -> bool:
    if res.get("passed") is not None:
        return bool(res.get("passed"))
    if res.get("checklist_passed") is not None:
        return bool(res.get("checklist_passed"))
    return False


def build_engine_b_summary_from_context(engine_b_ctx: dict[str, Any]) -> dict[str, Any]:
    """Strategy-layer summary when Engine B is the primary review engine."""
    return build_engine_b_summary_for_strategy(engine_b_ctx)


def assemble_engine_b_context(
    symbol: str,
    timeframe: str,
    *,
    screenshot_meta: dict[str, Any] | None = None,
    resolve_pair_fn: Callable[[str], dict[str, Any] | None] | None = None,
    naked_analysis_fn: Callable[..., tuple[Any, Any, str | None]] | None = None,
    last_engine_b_rows_fn: Callable[[], list[dict[str, Any]]] | None = None,
    origin_signal: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not resolve_pair_fn or not naked_analysis_fn:
        return None

    pair = resolve_pair_fn(symbol)
    if not pair:
        return None

    seed_row = origin_signal if isinstance(origin_signal, dict) else None
    if seed_row is None and last_engine_b_rows_fn:
        try:
            rows = last_engine_b_rows_fn() or []
        except Exception:
            rows = []
        seed_row = _pick_best_engine_b_row([r for r in rows if isinstance(r, dict)], symbol)

    direction = None
    if seed_row:
        candidate_direction = str(seed_row.get("direction") or "").upper()
        if candidate_direction in ("LONG", "SHORT"):
            direction = candidate_direction
    if direction is None:
        direction = _resolve_direction(
            symbol,
            screenshot_meta,
            seed_row,
            last_engine_b_rows_fn=last_engine_b_rows_fn,
        )
    if direction is None:
        return None

    style = _resolve_style(screenshot_meta, seed_row or {}, pair)
    display = pair.get("display") or pair.get("symbol") or symbol
    seed_signal: dict[str, Any] = {
        "symbol": pair.get("symbol") or display,
        "pair": display,
        "display": display,
        "type": pair.get("type"),
        "direction": direction,
        "style": style,
        "is_naked": True,
        "engine": "B",
    }
    if seed_row:
        seed_signal["price"] = seed_row.get("entry") or seed_row.get("price")
        seed_signal["confluenceScore"] = seed_row.get("confluenceScore")

    res, pair_obj, err = naked_analysis_fn(seed_signal, overlay_only=True)
    if err or not isinstance(res, dict):
        return None

    asset_type = str(pair_obj.get("type") if pair_obj else pair.get("type") or "")
    asset_group = get_pair_score_group(pair_obj or pair)
    structure = sanitize_engine_b_structure_profile_fields(dict(res), asset_type)

    price = _to_float(res.get("current_price") or res.get("entry") or res.get("price"))
    origin_payload: dict[str, Any] = {}
    if seed_row:
        nested = seed_row.get("naked_data") or seed_row.get("engine_b")
        origin_payload = nested if isinstance(nested, dict) else seed_row
    origin_entry = _to_float((seed_row or {}).get("entry"))
    if origin_entry is None:
        origin_entry = _to_float((seed_row or {}).get("price"))
    if origin_entry is None:
        origin_entry = _to_float(
            origin_payload.get("entry")
            or origin_payload.get("price")
            or origin_payload.get("current_price")
        )
    sl = _to_float(res.get("final_stop_loss") or res.get("recommended_stop_loss"))
    tp = _to_float(res.get("final_take_profit") or res.get("recommended_take_profit"))
    rr = _to_float(res.get("rr_used_for_gate") or res.get("rr"))
    score = _to_float(res.get("score"))
    max_score = _to_float(res.get("max_possible"))
    threshold = _to_float(res.get("min_score_used"))
    passed = _engine_b_passed(res)
    review_timestamp = datetime.now(timezone.utc).isoformat()
    scan_timestamp = (
        (seed_row or {}).get("timestamp")
        or (seed_row or {}).get("decisionTime")
        or (seed_row or {}).get("scan_timestamp")
        or res.get("timestamp")
        or res.get("computed_at")
        or review_timestamp
    )
    displacement = (
        abs(price - origin_entry)
        if price is not None and origin_entry is not None
        else None
    )
    origin_score = _to_float(origin_payload.get("score"))
    stamped_policy_context = {
        "timeframePolicyVersion": res.get("timeframePolicyVersion"),
        "timeframePolicyHash": res.get("timeframePolicyHash"),
        "policyKey": res.get("policyKey"),
        "regimeTf": res.get("regimeTf") or res.get("regime_tf"),
        "biasTf": res.get("biasTf") or res.get("bias_tf"),
        "structureTf": res.get("structureTf") or res.get("structure_tf") or res.get("zone_tf"),
        "setupTf": res.get("setupTf") or res.get("setup_tf"),
        "triggerTf": res.get("triggerTf") or res.get("trigger_tf") or res.get("entry_tf"),
        "executionTf": res.get("executionTf") or res.get("execution_tf") or res.get("entry_tf"),
        "executionMode": res.get("executionMode") or res.get("execution_mode") or "live_quote",
        "m5Role": res.get("m5Role") or res.get("m5_role"),
        "m5Policy": res.get("m5Policy") or res.get("m5_policy"),
    }
    speed_host = origin_payload if origin_payload.get("liveSpeedClass") or origin_payload.get("currentSpeedClass") else seed_row
    resolved_policy_context = resolve_timeframe_policy(
        str(pair.get("display") or symbol or pair.get("symbol") or ""),
        asset_type,
        asset_group,
        style,
        speed_state_from_policy_payload(speed_host) if speed_host else None,
        engine_id="engine_b",
    ).payload()
    policy_context = {
        **resolved_policy_context,
        **{
            key: value
            for key, value in stamped_policy_context.items()
            if value not in (None, "")
        },
    }
    chart_provider = (screenshot_meta or {}).get("provider") or (screenshot_meta or {}).get("chart_provider")

    ctx: dict[str, Any] = {
        "primary_engine": "B",
        "review_type": "engine_b_chart",
        # The response symbol must remain the requested chart identity. A
        # commodity catalog may retain a continuous-futures proxy even though
        # the live chart/levels use the spot-CFD display identity.
        "symbol": str(symbol or "").strip() or display,
        "catalog_symbol": pair.get("symbol"),
        "timeframe": timeframe,
        "analyze_style": style,
        "chart_timeframe": (screenshot_meta or {}).get("chart_timeframe") or timeframe,
        "timeframe_policy": policy_context,
        "asset_class": asset_type,
        "asset_group": asset_group,
        "direction": direction,
        "regime": res.get("regime"),
        "scan_timestamp": scan_timestamp,
        "candidate_timestamp": scan_timestamp,
        "review_timestamp": review_timestamp,
        "review_mode": "candidate" if seed_row else "exploratory",
        "candidate_origin": {
            "candidate_id": (seed_row or {}).get("signalId") or (seed_row or {}).get("signal_id") or (seed_row or {}).get("id"),
            "revision": scan_timestamp,
            "direction": direction,
            "style": (seed_row or {}).get("horizon") or (seed_row or {}).get("style"),
            "entry": origin_entry,
            "stop_loss": _to_float(
                origin_payload.get("final_stop_loss")
                or origin_payload.get("recommended_stop_loss")
                or (seed_row or {}).get("sl")
            ),
            "take_profit": _to_float(
                origin_payload.get("final_take_profit")
                or origin_payload.get("recommended_take_profit")
                or (seed_row or {}).get("tp1")
                or (seed_row or {}).get("tp")
            ),
            "score": origin_score,
            "max_score": _to_float(origin_payload.get("max_possible") or origin_payload.get("maxScore")),
            "threshold": _to_float(origin_payload.get("min_score_used") or origin_payload.get("threshold")),
            "passed": _engine_b_passed(origin_payload),
        } if seed_row else None,
        "review_delta": {
            "entry_displacement": displacement,
            "score_delta": score - origin_score if score is not None and origin_score is not None else None,
            "direction_changed": (
                str((seed_row or {}).get("direction") or "NONE").upper() != direction
            ) if seed_row else None,
        },
        "latest_candle_ts": res.get("latest_confirmed_trigger_candle_time"),
        "engine_b_provider": pair.get("source"),
        "chart_provider_hint": chart_provider,
        "provider_mismatch": False,
        "confluence_score": score,
        "max_score_override": max_score,
        "threshold": threshold,
        "passed": passed,
        "structure_context": structure,
        "atr": {
            "atr_value": _to_float(res.get("atr") if isinstance(res.get("atr"), (int, float)) else None),
            "atr_tf": res.get("atr_tf") or style,
            "atr_source": res.get("atr_source"),
            "atr_age_seconds": None,
            "atr_confirmed_only": True,
            "atr_freshness_status": None,
            "max_expected_age_seconds": None,
        },
        "geometry": {
            "candidate_entry": origin_entry if origin_entry is not None else price,
            "current_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "risk_points": abs(price - sl) if price is not None and sl is not None else None,
            "reward_points": abs(tp - price) if price is not None and tp is not None else None,
            "rr": rr,
            "price_displacement_from_candidate_entry": displacement,
            "sl_tp_source": "engine_b_levels",
        },
        "freshness": {},
        "chart_captured_at": (screenshot_meta or {}).get("captured_at"),
        "screenshot_overlays": list((screenshot_meta or {}).get("overlays") or []),
        "chart_snapshot": dict((screenshot_meta or {}).get("chart_snapshot") or {})
        if isinstance((screenshot_meta or {}).get("chart_snapshot"), dict)
        else {},
        "mismatch_warnings": [],
        "engine_snapshots": {
            "engineB": {
                "score": score,
                "maxScore": max_score,
                "threshold": threshold,
                "normalizedScore": (
                    (score / max_score * 100.0)
                    if score is not None and max_score not in (None, 0)
                    else None
                ),
                "passed": passed,
                "direction": direction,
                "structuralVerdict": structure.get("structural_verdict"),
            },
            "engineA": None,
        },
        "review_style_diagnostic": {
            "review_analyze_style": style,
            "candidate_signal_style": (screenshot_meta or {}).get("signal_style"),
            "style_matches_candidate": True,
            "chart_timeframe": (screenshot_meta or {}).get("chart_timeframe") or timeframe,
            "note": None,
        },
        "signal": res,
        "engine_b_confidence": res,
    }
    # Prefer candles attached on the live naked-analysis result (AI-review path
    # stamps limited *Candles series). Fall back to seed_row only if present —
    # scan rows stay lean and usually have no OHLC, which previously left
    # candle_anatomy / price-action facts empty.
    candle_host: dict[str, Any] = dict(res) if isinstance(res, dict) else {}
    if isinstance(seed_row, dict):
        for key, val in seed_row.items():
            if str(key).endswith("Candles") and key not in candle_host and val:
                candle_host[key] = val
    ctx["ohlcv_bars"] = select_ohlcv_bars_for_chart(candle_host, timeframe, screenshot_meta)
    ctx["engine_b_prompt_context"] = build_engine_b_prompt_context(ctx)
    return ctx
