"""Cheap Engine B discovery shortlist built from live prices + small candle windows.

This service is read-only and intentionally does NOT run full Engine B, AI, or
chart capture. It ranks a small cross-asset shortlist so higher-cost Engine B
or chart-review steps can be focused on a handful of symbols.
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable


DEFAULT_GROUPS = ("forex", "crypto", "commodity", "index")

# Sticky slot / hysteresis defaults. Overridable via config keys of the same name.
DEFAULT_SWITCH_MARGIN = 15.0
DEFAULT_SCOUT_MIN_SCORE = 55.0
DEFAULT_HOT_MIN_SCORE = 65.0
DEFAULT_PRIORITY_MIN_SCORE = 75.0
DEFAULT_WEAK_TTL_SEC = 900.0
DEFAULT_CRYPTO_ANCHORS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "ADAUSDT",
)


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _iso_now(time_now: float | None = None) -> str:
    ts = time.time() if time_now is None else float(time_now)
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _symbol_variants(value: Any) -> set[str]:
    raw = str(value or "").strip().upper()
    if not raw:
        return set()
    base = raw.split(":")[-1]
    no_yahoo = base.replace("=X", "")
    compact = re.sub(r"[^A-Z0-9.]", "", no_yahoo)
    compact_no_dot = compact.replace(".", "")
    no_sep = re.sub(r"[^A-Z0-9]", "", no_yahoo)
    return {v for v in (raw, base, no_yahoo, compact, compact_no_dot, no_sep) if v}


def _canonical_symbol(pair: dict[str, Any]) -> str:
    display = str(pair.get("display") or "").strip()
    symbol = str(pair.get("symbol") or "").strip()
    pair_type = str(pair.get("type") or "").lower()
    if pair_type == "forex" and display:
        token = re.sub(r"[^A-Z0-9]", "", display.upper())
        if token:
            return token
    if symbol.endswith("=X"):
        return symbol[:-2]
    return symbol or display


def _canonical_group(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"forex", "fx"}:
        return "forex"
    if text == "crypto":
        return "crypto"
    if text in {"commodity", "commodities"}:
        return "commodity"
    if text in {"index", "indices", "stock", "stocks", "etf", "etfs"}:
        return "index"
    return None


def _pair_group(pair: dict[str, Any]) -> str | None:
    pair_type = str(pair.get("type") or "").lower()
    return _canonical_group(pair_type)


def _index_live_prices(live_prices: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for key, value in (live_prices or {}).items():
        if not isinstance(value, dict):
            continue
        for variant in _symbol_variants(key):
            indexed.setdefault(variant, value)
    return indexed


def _live_entry_for_pair(pair: dict[str, Any], live_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for field in (pair.get("display"), pair.get("symbol"), _canonical_symbol(pair)):
        for variant in _symbol_variants(field):
            entry = live_index.get(variant)
            if isinstance(entry, dict):
                return entry
    return {}


def _epoch_seconds(value: Any) -> float | None:
    ts = _safe_float(value)
    if ts is None or ts <= 0:
        return None
    return ts / 1000.0 if ts > 1e12 else ts


def _freshness_component(price_entry: dict[str, Any], *, time_now: float | None = None) -> tuple[float, float | None, str]:
    latest_price = _safe_float(price_entry.get("price"))
    score = 0.0
    if latest_price is not None and latest_price > 0:
        score += 8.0
    ts = _epoch_seconds(price_entry.get("ts") or price_entry.get("timestamp"))
    if ts is None:
        return (-4.0 if latest_price is None else score, None, "missing")
    age = max(0.0, (time.time() if time_now is None else float(time_now)) - ts)
    if age <= 15:
        score += 12.0
        status = "fresh"
    elif age <= 60:
        score += 10.0
        status = "fresh"
    elif age <= 300:
        score += 7.0
        status = "delayed"
    elif age <= 1800:
        score += 3.0
        status = "delayed"
    else:
        score -= 4.0
        status = "stale"
    return score, round(age, 3), status


def _movement_component(change_pct: float | None) -> float:
    if change_pct is None:
        return 0.0
    abs_move = abs(change_pct)
    if abs_move >= 3.0:
        return 25.0
    if abs_move >= 2.0:
        return 22.0
    if abs_move >= 1.0:
        return 16.0
    if abs_move >= 0.5:
        return 10.0
    if abs_move >= 0.2:
        return 5.0
    return 1.0 if abs_move > 0 else 0.0


def _spread_component(spread: float | None, latest_price: float | None) -> float:
    if spread is None or latest_price is None or latest_price <= 0:
        return 0.0
    rel = abs(spread) / latest_price
    if rel <= 0.0001:
        return 0.0
    if rel <= 0.0005:
        return -1.0
    if rel <= 0.001:
        return -3.0
    if rel <= 0.002:
        return -6.0
    return -10.0


def _session_component(pair: dict[str, Any], *, time_now: float | None = None) -> float:
    now = datetime.fromtimestamp(
        time.time() if time_now is None else float(time_now),
        tz=timezone.utc,
    )
    weekday = now.weekday()
    hour = now.hour
    pair_type = str(pair.get("type") or "").lower()
    if pair_type == "crypto":
        return 5.0
    if pair_type == "forex":
        if weekday > 4:
            return 0.0
        if 12 <= hour <= 16:
            return 10.0
        if 7 <= hour <= 18:
            return 7.0
        if 0 <= hour <= 9:
            return 4.0
        return 2.0
    if pair_type in {"commodity", "index", "stock", "etf"}:
        if weekday > 4:
            return 0.0
        if 12 <= hour <= 17:
            return 10.0
        if 7 <= hour <= 21:
            return 6.0
        return 2.0
    return 0.0


def _extract_candle_ohlc(candle: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    return (
        _safe_float(candle.get("open") if "open" in candle else candle.get("o")),
        _safe_float(candle.get("high") if "high" in candle else candle.get("h")),
        _safe_float(candle.get("low") if "low" in candle else candle.get("l")),
        _safe_float(candle.get("close") if "close" in candle else candle.get("c")),
    )


def _candles_metrics(candles: list[dict[str, Any]]) -> dict[str, float | None]:
    if not candles:
        return {
            "latest_close": None,
            "change_pct": None,
            "range_expansion": 0.0,
            "activity": 0.0,
        }
    cleaned = [c for c in candles if isinstance(c, dict)]
    if not cleaned:
        return {
            "latest_close": None,
            "change_pct": None,
            "range_expansion": 0.0,
            "activity": 0.0,
        }
    latest = cleaned[-1]
    open_v, high_v, low_v, close_v = _extract_candle_ohlc(latest)
    latest_range = (
        abs(high_v - low_v)
        if high_v is not None and low_v is not None and high_v >= low_v
        else None
    )
    body_ratio = 0.0
    if latest_range and latest_range > 0 and open_v is not None and close_v is not None:
        body_ratio = min(1.0, abs(close_v - open_v) / latest_range)
    prior_ranges = []
    for candle in cleaned[-6:-1]:
        _open, _high, _low, _close = _extract_candle_ohlc(candle)
        if _high is None or _low is None or _high < _low:
            continue
        prior_ranges.append(abs(_high - _low))
    avg_prior_range = (
        sum(prior_ranges) / len(prior_ranges)
        if prior_ranges
        else latest_range
    )
    range_component = 0.0
    if latest_range and avg_prior_range and avg_prior_range > 0:
        ratio = latest_range / avg_prior_range
        if ratio >= 2.0:
            range_component = 20.0
        elif ratio >= 1.5:
            range_component = 15.0
        elif ratio >= 1.2:
            range_component = 10.0
        elif ratio >= 1.0:
            range_component = 5.0
    activity_component = round(body_ratio * 10.0, 3)
    change_pct = None
    if close_v is not None and len(cleaned) >= 2:
        prev_close = _extract_candle_ohlc(cleaned[-2])[3]
        if prev_close is not None and prev_close != 0:
            change_pct = ((close_v - prev_close) / prev_close) * 100.0
    return {
        "latest_close": close_v,
        "change_pct": change_pct,
        "range_expansion": range_component,
        "activity": activity_component,
    }


def _reason_parts(*, freshness_status: str, movement: float, range_expansion: float, activity: float, session: float, spread: float) -> str:
    parts: list[str] = []
    if freshness_status == "fresh":
        parts.append("fresh live price")
    elif freshness_status == "delayed":
        parts.append("slightly delayed live price")
    else:
        parts.append("stale or missing live price")
    if movement >= 16.0:
        parts.append("strong move")
    elif movement >= 5.0:
        parts.append("meaningful move")
    if range_expansion >= 10.0:
        parts.append("strong recent range expansion")
    if activity >= 6.0:
        parts.append("active latest candle")
    if session >= 8.0:
        parts.append("active session")
    if spread <= -6.0:
        parts.append("spread elevated")
    return ", ".join(parts[:4]) if parts else "limited live context"


def _candidate_from_pair(
    pair: dict[str, Any],
    *,
    live_index: dict[str, dict[str, Any]],
    time_now: float | None = None,
) -> dict[str, Any]:
    live_entry = _live_entry_for_pair(pair, live_index)
    latest_price = _safe_float(live_entry.get("price"))
    bid = _safe_float(live_entry.get("bid"))
    ask = _safe_float(live_entry.get("ask"))
    spread = _safe_float(live_entry.get("spread"))
    if spread is None and bid is not None and ask is not None:
        spread = round(ask - bid, 6)
    freshness, freshness_seconds, freshness_status = _freshness_component(
        live_entry,
        time_now=time_now,
    )
    change_pct = _safe_float(live_entry.get("change_pct"))
    movement = _movement_component(change_pct)
    session = _session_component(pair, time_now=time_now)
    spread_penalty = _spread_component(spread, latest_price)
    strength = freshness + movement + session + spread_penalty
    return {
        "symbol": _canonical_symbol(pair),
        "display": str(pair.get("display") or pair.get("symbol") or "").strip(),
        "asset_type": str(pair.get("type") or "").lower(),
        "source": str(live_entry.get("source") or pair.get("source") or "").lower(),
        "latest_price": latest_price,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "change_pct": change_pct,
        "strength_score": strength,
        "reason": _reason_parts(
            freshness_status=freshness_status,
            movement=movement,
            range_expansion=0.0,
            activity=0.0,
            session=session,
            spread=spread_penalty,
        ),
        "components": {
            "freshness": round(freshness, 3),
            "movement": round(movement, 3),
            "rangeExpansion": 0.0,
            "activity": 0.0,
            "spread": round(spread_penalty, 3),
            "session": round(session, 3),
        },
        "freshness_seconds": freshness_seconds,
        "freshness_status": freshness_status,
        "_pair": pair,
    }


def _with_candles(
    candidate: dict[str, Any],
    *,
    candle_fetch_fn: Callable[[dict[str, Any], str, int], Any] | None,
    timeframe: str,
    candle_limit: int,
) -> dict[str, Any]:
    if candle_fetch_fn is None:
        candidate.pop("_pair", None)
        candidate["strength_score"] = round(max(0.0, min(100.0, candidate["strength_score"])), 3)
        return candidate
    pair = candidate.get("_pair") or {}
    try:
        raw = candle_fetch_fn(pair, timeframe, candle_limit)
    except Exception:
        raw = []
    candles = raw if isinstance(raw, list) else []
    metrics = _candles_metrics(candles)
    latest_close = _safe_float(metrics.get("latest_close"))
    if candidate.get("latest_price") is None and latest_close is not None:
        candidate["latest_price"] = latest_close
    if candidate.get("change_pct") is None:
        candidate["change_pct"] = _safe_float(metrics.get("change_pct"))
    movement = _movement_component(_safe_float(candidate.get("change_pct")))
    range_expansion = _safe_float(metrics.get("range_expansion")) or 0.0
    activity = _safe_float(metrics.get("activity")) or 0.0
    candidate["components"]["movement"] = round(movement, 3)
    candidate["components"]["rangeExpansion"] = round(range_expansion, 3)
    candidate["components"]["activity"] = round(activity, 3)
    candidate["strength_score"] = round(
        max(
            0.0,
            min(
                100.0,
                float(candidate["components"]["freshness"])
                + movement
                + range_expansion
                + activity
                + float(candidate["components"]["session"])
                + float(candidate["components"]["spread"]),
            ),
        ),
        3,
    )
    candidate["reason"] = _reason_parts(
        freshness_status=str(candidate.get("freshness_status") or "missing"),
        movement=movement,
        range_expansion=range_expansion,
        activity=activity,
        session=float(candidate["components"]["session"]),
        spread=float(candidate["components"]["spread"]),
    )
    candidate.pop("_pair", None)
    return candidate


def _thresholds_from_config(config: dict[str, Any] | None) -> dict[str, float]:
    cfg = config or {}

    def _num(key: str, default: float) -> float:
        val = _safe_float(cfg.get(key))
        return val if val is not None else default

    return {
        "switch_margin": _num("ENGINE_B_HOTLIST_SWITCH_MARGIN", DEFAULT_SWITCH_MARGIN),
        "scout_min_score": _num("ENGINE_B_HOTLIST_SCOUT_MIN_SCORE", DEFAULT_SCOUT_MIN_SCORE),
        "hot_min_score": _num("ENGINE_B_HOTLIST_HOT_MIN_SCORE", DEFAULT_HOT_MIN_SCORE),
        "priority_min_score": _num("ENGINE_B_HOTLIST_PRIORITY_MIN_SCORE", DEFAULT_PRIORITY_MIN_SCORE),
        "weak_ttl_sec": _num("ENGINE_B_HOTLIST_WEAK_TTL_SEC", DEFAULT_WEAK_TTL_SEC),
    }


def _crypto_anchors_from_config(config: dict[str, Any] | None) -> set[str]:
    raw = (config or {}).get("ENGINE_B_HOTLIST_CRYPTO_ANCHORS")
    if not isinstance(raw, (list, tuple)) or not raw:
        raw = DEFAULT_CRYPTO_ANCHORS
    anchors: set[str] = set()
    for item in raw:
        for variant in _symbol_variants(item):
            anchors.add(variant)
    return anchors


def _candidate_data_status(candidate: dict[str, Any]) -> str:
    """Coarse data-quality bucket for a candidate: fresh | stale | data_missing."""
    if candidate.get("latest_price") is None:
        return "data_missing"
    status = str(candidate.get("freshness_status") or "missing")
    if status in {"fresh", "delayed"}:
        return "fresh"
    if status == "stale":
        return "stale"
    return "data_missing"


def _preferred_pick(
    ranked: list[dict[str, Any]],
    *,
    group: str,
    anchors: set[str],
    switch_margin: float,
) -> dict[str, Any] | None:
    """Natural winner for a group, applying the crypto anchor preference.

    For crypto we prefer the strongest high-liquidity anchor unless a non-anchor
    beats the best anchor by at least ``switch_margin``.
    """
    if not ranked:
        return None
    top = ranked[0]
    if group != "crypto" or not anchors:
        return top
    anchor_rows = [row for row in ranked if str(row.get("symbol") or "").upper() in anchors]
    if not anchor_rows:
        return top
    best_anchor = anchor_rows[0]
    top_is_anchor = str(top.get("symbol") or "").upper() in anchors
    if not top_is_anchor and float(top.get("strength_score") or 0.0) >= float(best_anchor.get("strength_score") or 0.0) + switch_margin:
        return top
    return best_anchor


def _slot_tier(score: float, thresholds: dict[str, float]) -> str:
    if score >= thresholds["priority_min_score"]:
        return "PRIORITY"
    if score >= thresholds["hot_min_score"]:
        return "HOT"
    if score >= thresholds["scout_min_score"]:
        return "SCOUT"
    return "WEAK"


def _select_slot(
    *,
    group: str,
    ranked: list[dict[str, Any]],
    prior: dict[str, Any] | None,
    action: dict[str, Any] | None,
    thresholds: dict[str, float],
    anchors: set[str],
    time_now: float | None,
) -> dict[str, Any]:
    """Apply sticky hysteresis to choose the displayed symbol for a group.

    Returns the updated slot state. The chosen candidate (if any) is referenced
    by ``slot["symbol"]`` and embedded as ``slot["candidate"]``.
    """
    by_symbol = {str(row.get("symbol") or "").upper(): row for row in ranked}
    switch_margin = thresholds["switch_margin"]
    scout_min = thresholds["scout_min_score"]
    weak_ttl = thresholds["weak_ttl_sec"]
    now = time.time() if time_now is None else float(time_now)
    now_iso = _iso_now(time_now)

    action = action or {}
    manual_symbol = None
    for variant in _symbol_variants(action.get("select")):
        if variant in by_symbol:
            manual_symbol = variant
            break

    locked = bool(prior.get("locked")) if prior else False
    if "lock" in action:
        locked = bool(action.get("lock"))

    prior_symbol = str(prior.get("symbol") or "").upper() if prior else ""
    current = by_symbol.get(prior_symbol) if prior_symbol else None
    preferred = _preferred_pick(ranked, group=group, anchors=anchors, switch_margin=switch_margin)

    chosen: dict[str, Any] | None
    if manual_symbol is not None:
        chosen = by_symbol[manual_symbol]
        cause = "manual_select"
    elif preferred is None:
        chosen = None
        cause = "no_candidates"
    elif prior is None or current is None:
        # No prior selection, or the previously selected symbol vanished from the
        # candidate set (data missing / invalidated) -> take the natural winner.
        chosen = preferred
        cause = "initial" if prior is None else "current_missing"
    elif locked:
        chosen = current
        cause = "locked"
    else:
        cur_status = _candidate_data_status(current)
        cur_score = float(current.get("strength_score") or 0.0)
        below_since = _safe_float(prior.get("below_since"))
        weak_ttl_expired = (
            cur_score < scout_min
            and below_since is not None
            and (now - below_since) >= weak_ttl
        )
        if cur_status in {"stale", "data_missing"}:
            chosen = preferred
            cause = f"current_{cur_status}"
        elif weak_ttl_expired:
            chosen = preferred
            cause = "weak_ttl"
        elif (
            str(preferred.get("symbol") or "").upper() != prior_symbol
            and float(preferred.get("strength_score") or 0.0) >= cur_score + switch_margin
        ):
            chosen = preferred
            cause = "switch_margin"
        else:
            chosen = current
            cause = "sticky"

    if chosen is None:
        return {
            "group": group,
            "symbol": None,
            "display": None,
            "asset_type": None,
            "selected_at": now_iso,
            "last_score": None,
            "status": "NO_HOT_SETUP",
            "data_status": "data_missing",
            "locked": locked,
            "below_since": None,
            "switched": bool(prior_symbol),
            "switch_cause": cause,
            "previous_symbol": prior_symbol or None,
            "eligible_for_prime": False,
            "ai_review_enabled": False,
            "reason": "market inactive — no candidate available",
            "candidate": None,
        }

    chosen_symbol = str(chosen.get("symbol") or "").upper()
    score = float(chosen.get("strength_score") or 0.0)
    data_status = _candidate_data_status(chosen)
    switched = chosen_symbol != prior_symbol

    tier = _slot_tier(score, thresholds)
    if tier == "WEAK":
        # Below scout threshold: keep the symbol on screen if it is still fresh
        # (WEAK_SCOUT), otherwise flag the group as having no hot setup.
        status = "WEAK_SCOUT" if data_status == "fresh" else "NO_HOT_SETUP"
        reason = "market active but no candidate above scout threshold"
        eligible_for_prime = False
        ai_review_enabled = False
    else:
        status = tier
        reason = str(chosen.get("reason") or "")
        eligible_for_prime = True
        ai_review_enabled = True

    # Track (as epoch seconds) how long the slot has been below the scout
    # threshold so the weak TTL can eventually free a stuck weak symbol.
    prior_below = _safe_float(prior.get("below_since")) if prior else None
    if score < scout_min and not switched and prior_below is not None:
        below_since = prior_below
    elif score < scout_min:
        below_since = now
    else:
        below_since = None

    selected_at = now_iso
    if not switched and prior and prior.get("selected_at"):
        selected_at = str(prior.get("selected_at"))

    return {
        "group": group,
        "symbol": chosen_symbol,
        "display": chosen.get("display"),
        "asset_type": chosen.get("asset_type"),
        "selected_at": selected_at,
        "last_score": round(score, 1),
        "status": status,
        "data_status": data_status,
        "locked": locked,
        "below_since": below_since,
        "switched": switched,
        "switch_cause": cause,
        "previous_symbol": prior_symbol or None if switched else None,
        "eligible_for_prime": eligible_for_prime,
        "ai_review_enabled": ai_review_enabled,
        "reason": reason,
        "candidate": chosen,
    }


def build_engine_b_hotlist(
    *,
    pairs_universe: list[dict[str, Any]] | None,
    live_prices: dict[str, Any] | None,
    candle_fetch_fn: Callable[[dict[str, Any], str, int], Any] | None,
    config: dict[str, Any] | None,
    symbols_override: list[str] | None = None,
    groups_override: list[str] | None = None,
    top_per_group: int = 1,
    timeframe: str = "H1",
    include_candidates: bool = True,
    time_now: float | None = None,
    selected_slots: dict[str, Any] | None = None,
    actions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pairs = [p for p in (pairs_universe or []) if isinstance(p, dict) and p.get("enabled", True)]
    requested_groups = []
    for raw in (groups_override or list(DEFAULT_GROUPS)):
        canonical = _canonical_group(raw)
        if canonical and canonical not in requested_groups:
            requested_groups.append(canonical)
    if not requested_groups:
        requested_groups = list(DEFAULT_GROUPS)

    symbol_filter: set[str] | None = None
    if symbols_override:
        collected: set[str] = set()
        for raw in symbols_override:
            collected.update(_symbol_variants(raw))
        symbol_filter = collected or None

    live_index = _index_live_prices(live_prices or {})
    grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in requested_groups}
    for pair in pairs:
        group = _pair_group(pair)
        if group not in grouped:
            continue
        if symbol_filter is not None:
            pair_variants = _symbol_variants(pair.get("symbol")) | _symbol_variants(pair.get("display")) | _symbol_variants(_canonical_symbol(pair))
            if not (pair_variants & symbol_filter):
                continue
        grouped[group].append(
            _candidate_from_pair(
                pair,
                live_index=live_index,
                time_now=time_now,
            )
        )

    top_n = max(1, int(top_per_group or 1))
    candle_limit = int((config or {}).get("ENGINE_B_HOTLIST_CANDLE_LIMIT") or 8)
    preselect_n = max(top_n, int((config or {}).get("ENGINE_B_HOTLIST_PREFETCH_PER_GROUP") or 5))
    thresholds = _thresholds_from_config(config)
    anchors = _crypto_anchors_from_config(config)
    prior_slots = selected_slots or {}
    actions = actions or {}
    groups_payload: dict[str, dict[str, Any]] = {}
    updated_slots: dict[str, Any] = dict(prior_slots)
    selected_symbols: list[str] = []

    for group in requested_groups:
        base_rows = sorted(
            grouped.get(group, []),
            key=lambda row: (-float(row.get("strength_score") or 0.0), str(row.get("symbol") or "")),
        )
        enriched: list[dict[str, Any]] = []
        for row in base_rows[:preselect_n]:
            enriched.append(
                _with_candles(
                    dict(row),
                    candle_fetch_fn=candle_fetch_fn,
                    timeframe=str(timeframe or "H1").upper(),
                    candle_limit=candle_limit,
                )
            )
        for row in base_rows[preselect_n:]:
            clean = dict(row)
            clean.pop("_pair", None)
            clean["strength_score"] = round(max(0.0, min(100.0, clean["strength_score"])), 3)
            enriched.append(clean)
        ranked = sorted(
            enriched,
            key=lambda row: (-float(row.get("strength_score") or 0.0), str(row.get("symbol") or "")),
        )
        for idx, row in enumerate(ranked, start=1):
            row["rank"] = idx
            row["strength_score"] = round(float(row.get("strength_score") or 0.0), 1)

        slot = _select_slot(
            group=group,
            ranked=ranked,
            prior=prior_slots.get(group),
            action=actions.get(group),
            thresholds=thresholds,
            anchors=anchors,
            time_now=time_now,
        )
        winner = slot.get("candidate")
        if winner:
            selected_symbols.append(str(winner.get("symbol")))

        # Top alternates are the next-best ranked candidates excluding the
        # currently selected symbol, so the user can switch deliberately.
        selected_symbol = str(slot.get("symbol") or "")
        alternates = [
            {
                "symbol": row.get("symbol"),
                "display": row.get("display"),
                "asset_type": row.get("asset_type"),
                "strength_score": row.get("strength_score"),
                "change_pct": row.get("change_pct"),
                "freshness_status": row.get("freshness_status"),
                "reason": row.get("reason"),
                "rank": row.get("rank"),
            }
            for row in ranked
            if str(row.get("symbol") or "") != selected_symbol
        ][:3]

        # Slot state stored across refreshes never embeds the heavy candidate.
        persisted_slot = {k: v for k, v in slot.items() if k != "candidate"}
        updated_slots[group] = persisted_slot

        groups_payload[group] = {
            "group": group,
            "winner": winner,
            "selected": slot,
            "alternates": alternates,
            "candidates": ranked[:top_n] if include_candidates else [],
        }

    return {
        "success": True,
        "payloadVersion": "engine-b-hotbench-v1",
        "generated_at": _iso_now(time_now),
        "groups": groups_payload,
        "selectedSymbols": selected_symbols,
        "selectedSlots": updated_slots,
        "thresholds": thresholds,
        "scoring": {
            "usesAi": False,
            "usesScreenshots": False,
            "usesFullEngineB": False,
        },
    }
