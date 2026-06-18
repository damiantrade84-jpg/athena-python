"""Assemble server-trusted Engine B context for TV chart AI review."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from market_structure import sanitize_engine_b_structure_profile_fields
from scoring import get_pair_score_group

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


def _resolve_style(screenshot_meta: dict[str, Any] | None, seed: dict[str, Any]) -> str:
    meta = screenshot_meta or {}
    for key in ("analyze_style", "signal_style", "style"):
        raw = str(meta.get(key) or seed.get(key) or "").strip().lower()
        if raw in ("scalp", "intraday", "swing"):
            return raw
    return "intraday"


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
) -> dict[str, Any] | None:
    if not resolve_pair_fn or not naked_analysis_fn:
        return None

    pair = resolve_pair_fn(symbol)
    if not pair:
        return None

    seed_row = None
    if last_engine_b_rows_fn:
        try:
            rows = last_engine_b_rows_fn() or []
        except Exception:
            rows = []
        seed_row = _pick_best_engine_b_row([r for r in rows if isinstance(r, dict)], symbol)

    direction = _resolve_direction(symbol, screenshot_meta, seed_row, last_engine_b_rows_fn=last_engine_b_rows_fn)
    if direction is None:
        return None

    style = _resolve_style(screenshot_meta, seed_row or {})
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

    price = _to_float(res.get("current_price"))
    sl = _to_float(res.get("final_stop_loss") or res.get("recommended_stop_loss"))
    tp = _to_float(res.get("final_take_profit") or res.get("recommended_take_profit"))
    rr = _to_float(res.get("rr_used_for_gate") or res.get("rr"))
    score = _to_float(res.get("score"))
    max_score = _to_float(res.get("max_possible"))
    threshold = _to_float(res.get("min_score_used"))
    passed = _engine_b_passed(res)
    scan_timestamp = datetime.now(timezone.utc).isoformat()
    chart_provider = (screenshot_meta or {}).get("provider") or (screenshot_meta or {}).get("chart_provider")

    ctx: dict[str, Any] = {
        "primary_engine": "B",
        "review_type": "engine_b_chart",
        "symbol": pair.get("symbol") or display,
        "timeframe": timeframe,
        "analyze_style": style,
        "chart_timeframe": (screenshot_meta or {}).get("chart_timeframe") or timeframe,
        "asset_class": asset_type,
        "asset_group": asset_group,
        "direction": direction,
        "regime": res.get("regime"),
        "scan_timestamp": scan_timestamp,
        "candidate_timestamp": scan_timestamp,
        "latest_candle_ts": None,
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
            "candidate_entry": price,
            "current_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "risk_points": abs(price - sl) if price is not None and sl is not None else None,
            "reward_points": abs(tp - price) if price is not None and tp is not None else None,
            "rr": rr,
            "price_displacement_from_candidate_entry": 0.0 if price is not None else None,
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
    }
    ctx["ohlcv_bars"] = select_ohlcv_bars_for_chart(seed_row or {}, timeframe, screenshot_meta)
    ctx["engine_b_prompt_context"] = build_engine_b_prompt_context(ctx)
    return ctx
