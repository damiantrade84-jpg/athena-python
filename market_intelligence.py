"""Read-only market intelligence context for advisory AI review.

Uses only existing ATHENA/local sources and returns explicit unavailable or
partial statuses when data is absent. This module never mutates trading config,
thresholds, signals, orders, or execution state.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from pair_context import build_pair_context

_CACHE: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config(key: str, default: Any) -> Any:
    try:
        from config import CONFIG

        return CONFIG.get(key, default)
    except BaseException:
        return default


def _status(status: str, source: str, timestamp: str | None = None) -> dict[str, Any]:
    return {"status": status, "timestamp": timestamp, "source": source}


def _event_context(symbol: str | None, asset_type: str | None) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not symbol:
        return [], _status("unavailable", "event_risk.check_event_risk"), ["symbol missing for event calendar context"]
    try:
        from event_risk import check_event_risk

        res = check_event_risk(symbol, asset_type or "unknown", lookahead_hours=72)
        events = res.get("events") if isinstance(res, dict) else []
        return list(events or []), _status("fresh" if events else "partial", "event_risk.check_event_risk", _now_iso()), warnings
    except Exception as exc:
        warnings.append(f"Event calendar unavailable: {exc}")
        return [], _status("unavailable", "event_risk.check_event_risk"), warnings


def _cot_context(symbol: str | None) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not symbol:
        return {}, _status("unavailable", "cot_feed.get_cot_net"), ["symbol missing for COT context"]
    try:
        from cot_feed import get_cot_net, get_cot_z

        detail = get_cot_net(symbol)
        if detail is None:
            return {}, _status("unavailable", "cot_feed.get_cot_net"), ["COT formula unavailable for symbol"]
        return {"z": get_cot_z(symbol), "detail": detail}, _status("partial", "cot_feed.get_cot_net", _now_iso()), warnings
    except Exception as exc:
        warnings.append(f"COT unavailable: {exc}")
        return {}, _status("unavailable", "cot_feed.get_cot_net"), warnings


def _funding_context(symbol: str | None, asset_type: str | None) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    warnings: list[str] = []
    if str(asset_type or "").lower() != "crypto":
        return {}, _status("unavailable", "data_feeds funding"), []
    if not symbol:
        return {}, _status("unavailable", "data_feeds funding"), ["symbol missing for funding context"]
    market_symbol = symbol.replace("/", "").upper()
    try:
        from data_feeds import _fetch_bybit_funding_rate, _fetch_funding_rate

        bybit = _fetch_bybit_funding_rate(market_symbol)
        binance = _fetch_funding_rate(market_symbol)
        return {"bybit": bybit, "binance": binance}, _status("partial", "data_feeds funding", _now_iso()), warnings
    except Exception as exc:
        warnings.append(f"Funding unavailable: {exc}")
        return {}, _status("unavailable", "data_feeds funding"), warnings


def _empty_cross_asset(name: str) -> dict[str, Any]:
    return {"symbol": name, "status": "unavailable", "value": None, "timestamp": None, "source": None}


def get_market_intelligence(
    symbol: str | None = None,
    asset_type: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    ttl = int(_config("AI_MARKET_INTELLIGENCE_TTL_SECONDS", 1800) or 1800)
    key = (str(symbol or "").upper(), str(asset_type or "").lower(), ttl)
    now = time.time()
    if not force_refresh and key in _CACHE and now - _CACHE[key][0] < ttl:
        return dict(_CACHE[key][1])

    generated_at = _now_iso()
    warnings: list[str] = []
    missing_fields: list[str] = []
    if not symbol:
        missing_fields.append("symbol")
    if not asset_type:
        missing_fields.append("asset_type")

    events, macro_status, event_warnings = _event_context(symbol, asset_type)
    warnings.extend(event_warnings)
    cot, cot_status, cot_warnings = _cot_context(symbol)
    warnings.extend(cot_warnings)
    funding, funding_status, funding_warnings = _funding_context(symbol, asset_type)
    warnings.extend(funding_warnings)

    pair_ctx = build_pair_context(symbol or "unknown", asset_type)
    warnings.extend(pair_ctx.get("warnings") or [])

    available_sources = sum(
        1 for s in (macro_status, cot_status, funding_status) if s.get("status") not in {"unavailable", "stale"}
    )
    freshness_status = "fresh" if available_sources >= 3 else "partial" if available_sources > 0 else "unavailable"
    if warnings and freshness_status == "fresh":
        freshness_status = "partial"

    out = {
        "schema_version": "market_intelligence.v1",
        "generated_at": generated_at,
        "ttl_seconds": ttl,
        "freshness_status": freshness_status,
        "macro_regime": {
            "risk_regime": "unknown",
            "fed_stance": "unknown",
            "days_to_next_major_event": None,
            "calendar_within_72h": events,
            "yield_curve": {},
            "dxy": _empty_cross_asset("DXY"),
            "vix": _empty_cross_asset("VIX"),
            "notes": ["Macro regime is not inferred without current local cross-asset data."],
        },
        "cross_asset_map": {
            "dxy": _empty_cross_asset("DXY"),
            "vix": _empty_cross_asset("VIX"),
            "spx": _empty_cross_asset("SPX"),
            "gold": _empty_cross_asset("XAU/USD"),
            "btc": _empty_cross_asset("BTC/USDT"),
            "us10y": _empty_cross_asset("US10Y"),
        },
        "positioning_extremes": {
            "cot": cot,
            "funding": funding,
            "sentiment": {},
        },
        "pair_context": pair_ctx,
        "source_status": {
            "macro": macro_status,
            "news": _status("unavailable", "news_sentiment_feed", None),
            "cot": cot_status,
            "funding": funding_status,
        },
        "missing_fields": missing_fields,
        "warnings": sorted(set(str(w) for w in warnings if w)),
    }
    _CACHE[key] = (now, dict(out))
    return out
