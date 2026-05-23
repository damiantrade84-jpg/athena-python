"""Scalp Workbench order-flow overlay assembly for chart display."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import CONFIG

log = logging.getLogger("sentinel.scalp_orderflow")


def _cfg(key: str, default: Any) -> Any:
    node = CONFIG.get("SCALP_ENGINE") or {}
    wb = node.get("WORKBENCH_ORDERFLOW") if isinstance(node, dict) else None
    if isinstance(wb, dict) and key in wb:
        return wb[key]
    return default


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_ts(value: float | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _level_context(price: float | None, profile: dict | None) -> str:
    if price is None or not profile:
        return "OTHER"
    poc = _float(profile.get("poc"))
    vah = _float(profile.get("vah"))
    val = _float(profile.get("val"))
    tol = max(abs(price) * 0.0005, 0.0001)
    if poc is not None and abs(price - poc) <= tol:
        return "POC"
    if vah is not None and abs(price - vah) <= tol:
        return "VAH"
    if val is not None and abs(price - val) <= tol:
        return "VAL"
    for level in profile.get("lvns") or []:
        lv = _float(level)
        if lv is not None and abs(price - lv) <= tol:
            return "LVN"
    sweep = _float(profile.get("sweepLevel"))
    if sweep is not None and abs(price - sweep) <= tol:
        return "SWEEP"
    return "OTHER"


def _percentile_threshold(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def _query_trade_buckets(symbol: str, from_ts: float | None, to_ts: float | None) -> tuple[list[dict], str]:
    try:
        from athena.microstructure.trade_bucket_store import query_session_buckets

        rows = query_session_buckets(
            symbol,
            min_last_ts=from_ts,
            max_last_ts=to_ts,
        )
        if rows:
            return rows, "REAL_AGGTRADE"
    except Exception as exc:
        log.debug("[ScalpOrderflow] trade buckets unavailable: %s", exc)
    return [], "UNAVAILABLE"


def _large_trade_events(rows: list[dict], profile: dict | None) -> list[dict]:
    if not rows:
        return []
    pct = float(_cfg("LARGE_TRADE_PERCENTILE", 95))
    min_notional = float(_cfg("MIN_NOTIONAL_USD", 10000))
    max_events = int(_cfg("MAX_EVENTS_PER_TYPE", 20))
    notionals = []
    for row in rows:
        px = _float(row.get("price_bucket"))
        vol = _float(row.get("total_volume")) or 0.0
        if px is None:
            continue
        notionals.append(px * vol)
    threshold = max(_percentile_threshold(notionals, pct), min_notional)
    events = []
    for row in rows:
        px = _float(row.get("price_bucket"))
        buy = _float(row.get("buy_volume")) or 0.0
        sell = _float(row.get("sell_volume")) or 0.0
        vol = _float(row.get("total_volume")) or 0.0
        if px is None or vol <= 0:
            continue
        notional = px * vol
        if notional < threshold:
            continue
        side = "BUY" if buy >= sell else "SELL"
        events.append(
            {
                "ts": _iso_ts(_float(row.get("last_ts"))),
                "price": px,
                "side": side,
                "size": vol,
                "notional": round(notional, 2),
                "rank": round(notional / threshold, 2) if threshold else None,
                "levelContext": _level_context(px, profile),
            }
        )
    events.sort(key=lambda e: e.get("notional") or 0, reverse=True)
    return events[:max_events]


def _cvd_series(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: _float(r.get("last_ts")) or 0.0)
    cumulative = 0.0
    out = []
    for row in ordered:
        delta = _float(row.get("delta")) or 0.0
        cumulative += delta
        out.append({"ts": _iso_ts(_float(row.get("last_ts"))), "value": round(cumulative, 4)})
    return out


def _absorption_events(rows: list[dict], profile: dict | None) -> list[dict]:
    events = []
    for row in rows:
        buy = _float(row.get("buy_volume")) or 0.0
        sell = _float(row.get("sell_volume")) or 0.0
        vol = buy + sell
        if vol <= 0:
            continue
        px = _float(row.get("price_bucket"))
        imbalance = abs(buy - sell) / vol
        if imbalance < 0.55 or vol < 1:
            continue
        side_absorbed = "SELL" if buy > sell else "BUY"
        events.append(
            {
                "ts": _iso_ts(_float(row.get("last_ts"))),
                "price": px,
                "sideAbsorbed": side_absorbed,
                "level": _level_context(px, profile),
                "effort": round(vol, 4),
                "result": "no_follow_through",
                "confidence": round(imbalance, 2),
            }
        )
    return events[: int(_cfg("MAX_EVENTS_PER_TYPE", 20))]


def _initiative_events(rows: list[dict], profile: dict | None) -> list[dict]:
    events = []
    for row in rows:
        buy = _float(row.get("buy_volume")) or 0.0
        sell = _float(row.get("sell_volume")) or 0.0
        vol = buy + sell
        if vol <= 0:
            continue
        px = _float(row.get("price_bucket"))
        imbalance = abs(buy - sell) / vol
        if imbalance < 0.72:
            continue
        side = "BUY" if buy > sell else "SELL"
        events.append(
            {
                "ts": _iso_ts(_float(row.get("last_ts"))),
                "price": px,
                "side": side,
                "level": _level_context(px, profile),
                "effort": round(vol, 4),
                "confidence": round(imbalance, 2),
            }
        )
    return events[: int(_cfg("MAX_EVENTS_PER_TYPE", 20))]


def _exhaustion_events(rows: list[dict]) -> list[dict]:
    if len(rows) < 3:
        return []
    ordered = sorted(rows, key=lambda r: _float(r.get("last_ts")) or 0.0)
    volumes = [_float(r.get("total_volume")) or 0.0 for r in ordered]
    avg = sum(volumes) / len(volumes) if volumes else 0.0
    events = []
    for row in ordered[-5:]:
        vol = _float(row.get("total_volume")) or 0.0
        if avg <= 0 or vol > avg * 0.35:
            continue
        events.append(
            {
                "ts": _iso_ts(_float(row.get("last_ts"))),
                "price": _float(row.get("price_bucket")),
                "reason": "volume_dried_up",
                "confidence": round(1.0 - (vol / avg), 2),
            }
        )
    return events


def build_scalp_orderflow_payload(
    *,
    symbol: str,
    timeframe: str,
    from_ts: float | None = None,
    to_ts: float | None = None,
    profile: dict | None = None,
    asset_type: str | None = None,
    venue_mismatch: bool = False,
) -> dict[str, Any]:
    warnings: list[str] = []
    source = "UNAVAILABLE"
    rows: list[dict] = []

    asset = str(asset_type or "").lower()
    if asset == "crypto" or symbol.upper().endswith("USDT"):
        rows, source = _query_trade_buckets(symbol, from_ts, to_ts)
        if not rows:
            warnings.append("no_trade_bucket_data")
            source = "CANDLE_PROXY"
    else:
        warnings.append("non_crypto_orderflow_proxy_only")
        source = "MT5_TICK_PROXY" if asset in ("forex", "commodity", "index", "stock") else "UNAVAILABLE"

    if venue_mismatch:
        warnings.append("data_venue_differs_from_execution_venue")
        if source == "REAL_AGGTRADE":
            source = "DELAYED_REAL"

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source": source,
        "largeTradeEvents": _large_trade_events(rows, profile),
        "absorptionEvents": _absorption_events(rows, profile) if rows else [],
        "initiativeEvents": _initiative_events(rows, profile) if rows else [],
        "exhaustionEvents": _exhaustion_events(rows) if rows else [],
        "cvd": _cvd_series(rows),
        "liquidationEventsAvailable": False,
        "warnings": warnings,
    }
