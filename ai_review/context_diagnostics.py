"""Deterministic context diagnostics for AI chart review responses."""

from __future__ import annotations

import copy
from typing import Any

from market_structure import build_engine_b_profile_vp_context


_EQUITY_GROUPS = {"equity", "equities", "stock", "stocks"}
_CRYPTO_GROUPS = {"crypto", "cryptocurrency", "perp", "perpetual"}


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _non_empty(value: Any) -> Any:
    return None if value in ("", [], {}) else value


def _first(*values: Any) -> Any:
    for value in values:
        if _non_empty(value) is not None:
            return value
    return None


def _text_blob(ai_review: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "visual_confirmation",
        "visual_contradiction",
        "engine_a_alignment",
        "atr_rr_assessment",
        "freshness_assessment",
        "entry_quality",
    ):
        value = ai_review.get(key)
        if value:
            parts.append(str(value))
    for key in ("supporting_reasons", "risks", "missing_context"):
        value = ai_review.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
    return " ".join(parts).lower()


def _detail(
    *,
    key: str,
    label: str,
    reason: str,
    impact: str,
    blocks_trade: bool,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "reason": reason,
        "impact": impact,
        "blocksTrade": blocks_trade,
    }


def _not_applicable(key: str, label: str, reason: str) -> dict[str, str]:
    return {"key": key, "label": label, "reason": reason}


def _asset_group(engine_a_ctx: dict[str, Any]) -> str:
    return str(engine_a_ctx.get("asset_group") or engine_a_ctx.get("asset_class") or "").lower()


def _is_crypto_asset(engine_a_ctx: dict[str, Any]) -> bool:
    group = _asset_group(engine_a_ctx)
    return group in _CRYPTO_GROUPS or group.startswith("crypto")


def _is_forex_asset(engine_a_ctx: dict[str, Any]) -> bool:
    return _asset_group(engine_a_ctx) == "forex"


def _non_visual_context(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    raw = engine_a_ctx.get("non_visual_context") or engine_a_ctx.get("engine_a_non_visual_context")
    return raw if isinstance(raw, dict) else {}


def _score_attribution(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    raw = engine_a_ctx.get("score_attribution") or engine_a_ctx.get("scoreAttribution")
    return raw if isinstance(raw, dict) else {}


def _engine_b_in_review(engine_a_ctx: dict[str, Any]) -> bool:
    overlays = engine_a_ctx.get("screenshot_overlays") or []
    if isinstance(overlays, list) and "engine_b" in overlays:
        return True
    struct = engine_a_ctx.get("structure_context") or engine_a_ctx.get("engine_b") or {}
    return isinstance(struct, dict) and bool(
        struct.get("structural_verdict")
        or struct.get("nearest_support_zone")
        or struct.get("nearest_resistance_zone")
    )


def _engine_snapshot_not_applicable(engine_key: str, engine_a_ctx: dict[str, Any]) -> bool:
    if engine_key == "engineB":
        return not _engine_b_in_review(engine_a_ctx)
    if engine_key in ("engineC", "engineD"):
        return True
    return False


def _classify_engine_snapshot_missing(
    item: str,
    engine_a_ctx: dict[str, Any],
    not_applicable: list[dict[str, str]],
) -> bool:
    lower = item.lower().replace(" ", "")
    for engine_key in ("engineB", "engineC", "engineD"):
        prefix = engine_key.lower()
        if lower.startswith(f"{prefix}.") or lower.startswith(prefix):
            if _engine_snapshot_not_applicable(engine_key, engine_a_ctx):
                _append_unique(
                    not_applicable,
                    _not_applicable(
                        f"{engine_key.lower()}_snapshot",
                        f"{engine_key} snapshot",
                        "Not part of this chart review surface",
                    ),
                )
                return True
    if "engineb" in lower or "engine b" in item.lower():
        if _engine_snapshot_not_applicable("engineB", engine_a_ctx):
            _append_unique(
                not_applicable,
                _not_applicable(
                    "engine_b_snapshot",
                    "Engine B snapshot",
                    "Engine B overlays were not included in this review",
                ),
            )
            return True
    return False


def _append_unique(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    if not any(existing.get("key") == item.get("key") for existing in items):
        items.append(item)


def _metadata(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "chartCapturedAt": engine_a_ctx.get("chart_captured_at"),
        "scanTimestamp": engine_a_ctx.get("scan_timestamp"),
        "latestCandleTimestamp": engine_a_ctx.get("latest_candle_ts"),
        "chartProvider": engine_a_ctx.get("chart_provider_hint"),
        "engineProvider": engine_a_ctx.get("engine_a_provider"),
        "providerMismatch": engine_a_ctx.get("provider_mismatch")
        if engine_a_ctx.get("provider_mismatch") is None
        else bool(engine_a_ctx.get("provider_mismatch")),
    }


def _funding_oi(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    raw = engine_a_ctx.get("funding_oi")
    if not isinstance(raw, dict):
        raw = {}
    return {
        "fundingRate": _to_float(_first(raw.get("fundingRate"), raw.get("funding_rate"))),
        "fundingRateZ": _to_float(_first(raw.get("fundingRateZ"), raw.get("funding_rate_z"))),
        "openInterest": _to_float(
            _first(raw.get("openInterest"), raw.get("open_interest"), raw.get("oi"))
        ),
        "openInterestDelta": _to_float(
            _first(raw.get("openInterestDelta"), raw.get("open_interest_delta"))
        ),
        "openInterestDeltaPct": _to_float(
            _first(
                raw.get("openInterestDeltaPct"),
                raw.get("open_interest_delta_pct"),
                raw.get("oiChange"),
                raw.get("oi_change_pct"),
            )
        ),
        "source": raw.get("source"),
        "timestamp": _first(raw.get("timestamp"), raw.get("ts"), raw.get("time")),
    }


def _atr_diagnostics(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    atr = engine_a_ctx.get("atr") or {}
    timeframe = str(engine_a_ctx.get("timeframe") or "").upper()
    atr_tf = str(atr.get("atr_tf") or atr.get("atrTimeframe") or "").upper()
    atr_value = _to_float(atr.get("atr_value") or atr.get("atrValue"))
    atr_h4 = _to_float(atr.get("atrH4") or atr.get("atr_h4"))
    atr_d1 = _to_float(atr.get("atrD1") or atr.get("atr_d1"))
    if atr_h4 is None and atr_tf == "H4":
        atr_h4 = atr_value
    if atr_d1 is None and atr_tf == "D1":
        atr_d1 = atr_value
    chart_tf_value = _to_float(atr.get("atrChartTf") or atr.get("atr_chart_tf"))
    if chart_tf_value is None and timeframe and timeframe == atr_tf:
        chart_tf_value = atr_value
    return {
        "atrD1": atr_d1,
        "atrH4": atr_h4,
        "atrChartTf": chart_tf_value,
        "atrSource": atr.get("atr_source") or atr.get("atrSource"),
        "atrTimeframe": atr_tf or None,
        "atrAgeSeconds": _to_float(atr.get("atr_age_seconds") or atr.get("atrAgeSeconds")),
        "atrConfirmedOnly": atr.get("atr_confirmed_only")
        if atr.get("atr_confirmed_only") is None
        else bool(atr.get("atr_confirmed_only")),
        "atrCandleLastTs": atr.get("atr_candle_last_ts") or atr.get("atrCandleLastTs"),
    }


def _zone_price(zone: Any, direction: str) -> float | None:
    if not isinstance(zone, dict):
        return None
    if direction == "SHORT":
        return _to_float(zone.get("upper") or zone.get("price"))
    return _to_float(zone.get("lower") or zone.get("price"))


def _ema_levels(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    levels = engine_a_ctx.get("ema_levels")
    if not isinstance(levels, dict):
        levels = {}
    fd = engine_a_ctx.get("factor_diagnostics") or {}
    trend = fd.get("trendCoherence") or fd.get("trend_coherence") or {}
    if not isinstance(trend, dict):
        trend = {}
    return {
        "ema50": _to_float(
            levels.get("ema50")
            or levels.get("ema50_value")
            or trend.get("ema50")
            or trend.get("ema50_value")
        ),
        "ema200": _to_float(
            levels.get("ema200")
            or levels.get("ema200_value")
            or trend.get("ema200")
            or trend.get("ema200_value")
        ),
        "dema200": _to_float(levels.get("dema200") or levels.get("dema200_value")),
    }


def _profile_levels_trusted(engine_a_ctx: dict[str, Any], structure: dict[str, Any]) -> bool:
    vp_ctx = structure.get("profile_vp_context")
    if isinstance(vp_ctx, dict) and "enabled" in vp_ctx:
        return bool(vp_ctx.get("enabled"))
    asset = str(engine_a_ctx.get("asset_class") or engine_a_ctx.get("asset_group") or "")
    return bool(build_engine_b_profile_vp_context(asset).get("enabled"))


def _resistance_map(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    structure = engine_a_ctx.get("structure_context")
    if not isinstance(structure, dict):
        structure = engine_a_ctx.get("engine_b") if isinstance(engine_a_ctx.get("engine_b"), dict) else {}
    if not isinstance(structure, dict):
        structure = {}
    profile_trusted = _profile_levels_trusted(engine_a_ctx, structure)
    direction = str(engine_a_ctx.get("direction") or "").upper()
    nearest_zone = structure.get("nearest_resistance_zone")
    nearest = _zone_price(nearest_zone, direction)
    current_price = _to_float((engine_a_ctx.get("geometry") or {}).get("current_price"))
    distance = _to_float(structure.get("distance_to_res"))
    if distance is None and nearest is not None and current_price is not None:
        distance = nearest - current_price
    tp = _to_float(
        (engine_a_ctx.get("geometry") or {}).get("take_profit")
        or structure.get("recommended_take_profit")
        or structure.get("nearest_target_price")
    )
    if nearest is None:
        tp_clears = None
    elif tp is None:
        tp_clears = None
    elif direction == "SHORT":
        tp_clears = tp <= nearest
    else:
        tp_clears = tp >= nearest

    highs = []
    for value in engine_a_ctx.get("htf_swing_highs") or []:
        parsed = _to_float(value)
        if parsed is not None:
            highs.append(parsed)

    profile_levels = (
        {
            "poc": _to_float(structure.get("prev_session_poc")),
            "vah": _to_float(structure.get("prev_session_vah")),
            "val": _to_float(structure.get("prev_session_val")),
        }
        if profile_trusted
        else {"poc": None, "vah": None, "val": None}
    )

    return {
        "nearestResistance": nearest,
        "distanceToNearestResistance": distance,
        "tp": tp,
        "tpClearsResistance": tp_clears,
        "htfSwingHighs": highs,
        "profileLevels": profile_levels,
        "profileLevelsTrusted": profile_trusted,
        "emaLevels": _ema_levels(engine_a_ctx),
        "supplyZones": list(
            structure.get("supply_zones")
            or structure.get("supplyZones")
            or structure.get("d1_order_blocks")
            or []
        ),
    }


def _funding_oi_complete(funding_oi: dict[str, Any]) -> bool:
    return any(
        funding_oi.get(key) is not None
        for key in (
            "fundingRate",
            "fundingRateZ",
            "openInterest",
            "openInterestDelta",
            "openInterestDeltaPct",
        )
    )


def _atr_available(atr: dict[str, Any]) -> bool:
    return any(atr.get(key) is not None for key in ("atrD1", "atrH4", "atrChartTf"))


def _resistance_available(resistance: dict[str, Any]) -> bool:
    return resistance.get("nearestResistance") is not None or bool(resistance.get("htfSwingHighs"))


def _classify_missing_items(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    funding_oi: dict[str, Any],
    atr: dict[str, Any],
    resistance: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    required: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []
    not_applicable: list[dict[str, str]] = []
    asset_group = _asset_group(engine_a_ctx)

    if not resistance.get("profileLevelsTrusted", True):
        _append_unique(
            not_applicable,
            _not_applicable(
                "engine_b_volume_profile_asset_class",
                "Engine B volume profile (POC/VAH/VAL)",
                f"Not used for asset group {asset_group or 'unknown'} — unreliable volume feed",
            ),
        )

    if not _is_crypto_asset(engine_a_ctx):
        _append_unique(
            not_applicable,
            _not_applicable(
                "funding_oi_asset_class",
                "Funding / open interest",
                f"Not used for asset group {asset_group or 'unknown'}",
            ),
        )

    text = _text_blob(ai_review)
    references_funding_oi = any(
        marker in text for marker in ("funding", "open interest", "open-interest", " oi ")
    )
    references_atr = "atr" in text
    references_resistance = any(marker in text for marker in ("resistance", "supply", "tp "))
    references_profile = any(
        marker in text for marker in (" poc", "poc ", "vah", "val", "value area", "volume profile")
    )

    for raw in ai_review.get("missing_context") or []:
        item = str(raw or "").strip()
        if not item:
            continue
        if _classify_engine_snapshot_missing(item, engine_a_ctx, not_applicable):
            continue
        lower = item.lower()
        if "chart captured" in lower or "captured timestamp" in lower or lower == "timestamp":
            continue
        if "equity_session" in lower or "equity session" in lower:
            if asset_group not in _EQUITY_GROUPS:
                _append_unique(
                    not_applicable,
                    _not_applicable(
                        "equity_session",
                        "Equity session multiplier",
                        f"asset_group {asset_group or 'unknown'} does not use equity session multiplier",
                    ),
                )
            else:
                _append_unique(
                    optional,
                    _detail(
                        key="equity_session",
                        label="Equity session multiplier",
                        reason=item,
                        impact="low",
                        blocks_trade=False,
                    ),
                )
            continue
        if "funding" in lower or "open interest" in lower or "oi" in lower:
            if not _is_crypto_asset(engine_a_ctx):
                _append_unique(
                    not_applicable,
                    _not_applicable(
                        "funding_oi_asset_class",
                        "Funding / open interest",
                        f"Not used for asset group {asset_group or 'unknown'}",
                    ),
                )
                continue
            if not _funding_oi_complete(funding_oi):
                _append_unique(
                    optional,
                    _detail(
                        key="funding_oi_numeric",
                        label="Funding/OI numeric values",
                        reason="AI review referenced funding/OI but numeric values were unavailable",
                        impact="medium",
                        blocks_trade=False,
                    ),
                )
            continue
        if "carry" in lower:
            if not _is_forex_asset(engine_a_ctx):
                _append_unique(
                    not_applicable,
                    _not_applicable(
                        "carry_asset_class",
                        "Carry add-on",
                        f"Not used for asset group {asset_group or 'unknown'}",
                    ),
                )
            else:
                _append_unique(
                    optional,
                    _detail(
                        key="carry_context",
                        label="Carry add-on context",
                        reason=item,
                        impact="medium",
                        blocks_trade=False,
                    ),
                )
            continue
        if "cot" in lower or "commitments of traders" in lower:
            addon = (_non_visual_context(engine_a_ctx).get("addonContext") or {})
            addon_type = str(addon.get("addonType") or "").lower()
            if addon_type not in {"cot", "cot_proxy"}:
                _append_unique(
                    not_applicable,
                    _not_applicable(
                        "cot_asset_class",
                        "COT add-on",
                        f"addonType {addon_type or 'unavailable'} is not cot/cot_proxy",
                    ),
                )
            else:
                _append_unique(
                    optional,
                    _detail(
                        key="cot_context",
                        label="COT add-on context",
                        reason=item,
                        impact="medium",
                        blocks_trade=False,
                    ),
                )
            continue
        if references_profile and not resistance.get("profileLevelsTrusted", True):
            if any(token in lower for token in ("poc", "vah", "val", "value area", "volume profile")):
                _append_unique(
                    not_applicable,
                    _not_applicable(
                        "engine_b_volume_profile_asset_class",
                        "Engine B volume profile (POC/VAH/VAL)",
                        f"Not used for asset group {asset_group or 'unknown'}",
                    ),
                )
                continue
        if "h4 atr" in lower or " atr" in f" {lower}":
            if not _atr_available(atr):
                _append_unique(
                    optional,
                    _detail(
                        key="atr_timeframe",
                        label="Timeframe-specific ATR",
                        reason=item,
                        impact="medium",
                        blocks_trade=False,
                    ),
                )
            continue
        if "resistance" in lower or "higher-tf" in lower or "higher tf" in lower:
            if not _resistance_available(resistance):
                _append_unique(
                    required,
                    _detail(
                        key="resistance_map",
                        label="Higher-timeframe resistance map",
                        reason=item,
                        impact="high",
                        blocks_trade=True,
                    ),
                )
            continue
        _append_unique(
            optional,
            _detail(
                key=f"model_missing_{len(optional) + len(required) + 1}",
                label=item[:80],
                reason=item,
                impact="medium",
                blocks_trade=False,
            ),
        )

    if references_funding_oi and not _funding_oi_complete(funding_oi) and _is_crypto_asset(engine_a_ctx):
        _append_unique(
            optional,
            _detail(
                key="funding_oi_numeric",
                label="Funding/OI numeric values",
                reason="AI review referenced funding/OI but numeric values were unavailable",
                impact="medium",
                blocks_trade=False,
            ),
        )
    if references_atr and not _atr_available(atr):
        _append_unique(
            optional,
            _detail(
                key="atr_timeframe",
                label="Timeframe-specific ATR",
                reason="AI review referenced ATR but timeframe-specific ATR diagnostics were unavailable",
                impact="medium",
                blocks_trade=False,
            ),
        )
    if references_resistance and not _resistance_available(resistance):
        key = "resistance_map"
        target = required if "validate" in text or "tp" in text else optional
        _append_unique(
            target,
            _detail(
                key=key,
                label="Higher-timeframe resistance map",
                reason="AI review referenced resistance/TP validation but structured resistance levels were unavailable",
                impact="high",
                blocks_trade=target is required,
            ),
        )

    return required, optional, not_applicable


def _score(required: list[dict[str, Any]], optional: list[dict[str, Any]]) -> int:
    score = 100
    score -= len(required) * 35
    for item in optional:
        impact = str(item.get("impact") or "").lower()
        if impact == "high":
            score -= 15
        elif impact == "medium":
            score -= 8
        else:
            score -= 4
    return max(0, min(100, score))


def build_context_diagnostics(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> dict[str, Any]:
    """Build structured context diagnostics without changing trading logic."""
    funding_oi = _funding_oi(engine_a_ctx)
    atr = _atr_diagnostics(engine_a_ctx)
    resistance = _resistance_map(engine_a_ctx)
    required, optional, not_applicable = _classify_missing_items(
        engine_a_ctx,
        ai_review,
        funding_oi,
        atr,
        resistance,
    )
    score = _score(required, optional)
    status = "insufficient" if required else "partial" if optional else "complete"
    return {
        "contextCompleteness": {
            "score": score,
            "status": status,
            "missingRequired": [item["label"] for item in required],
            "missingOptional": [item["label"] for item in optional],
            "notApplicable": [item["label"] for item in not_applicable],
            "metadata": _metadata(engine_a_ctx),
        },
        "missingContextDetailed": {
            "required": required,
            "optional": optional,
            "notApplicable": not_applicable,
        },
        "fundingOi": funding_oi,
        "nonVisualContext": _non_visual_context(engine_a_ctx),
        "engineANonVisualContext": _non_visual_context(engine_a_ctx),
        "scoreAttribution": _score_attribution(engine_a_ctx),
        "engineAScoreAttribution": _score_attribution(engine_a_ctx),
        "atrDiagnostics": atr,
        "resistanceMap": resistance,
    }


def sanitize_ai_review_missing_context(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> dict[str, Any]:
    """Return a copy with metadata/not-applicable items removed from missing_context."""
    clean = copy.deepcopy(ai_review)
    diagnostics = build_context_diagnostics(engine_a_ctx, clean)
    missing = (
        diagnostics["contextCompleteness"]["missingRequired"]
        + diagnostics["contextCompleteness"]["missingOptional"]
    )
    clean["missing_context"] = missing
    return clean


def context_tradeability_penalty(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
) -> float:
    diagnostics = build_context_diagnostics(engine_a_ctx, ai_review)
    penalty = 0.0
    for item in diagnostics["missingContextDetailed"]["required"]:
        penalty += 18.0 if item.get("impact") == "high" else 12.0
    for item in diagnostics["missingContextDetailed"]["optional"]:
        if item.get("impact") == "high":
            penalty += 8.0
    return penalty
