"""Shared Engine B live market-state helper.

This factors out the `athena.py` `_engine_b_live_market_state()` behavior so all call
sites use identical confirmed/forming candle discipline.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping


def fresh_live_gate_quote(
    pair: dict[str, Any],
    live_prices: Mapping[str, Any] | None,
    config: dict[str, Any] | None,
    *,
    time_now: float | None = None,
) -> tuple[float, dict[str, Any]]:
    """Return a fresh live quote for Engine B gate/level geometry.

    Confirmed candles remain authoritative for structure and indicators.  This
    quote is a separate, fail-closed executable-price input: missing timestamps,
    stale quotes, and invalid prices are never replaced by a candle close.
    """
    def _key(value: Any) -> str:
        return "".join(ch for ch in str(value or "").upper() if ch.isalnum())

    wanted = {_key(pair.get("display")), _key(pair.get("symbol")), _key(pair.get("pair"))}
    wanted.discard("")
    entry: dict[str, Any] | None = None
    matched_key: str | None = None
    for raw_key, raw_entry in (live_prices or {}).items():
        if _key(raw_key) in wanted and isinstance(raw_entry, dict):
            entry = dict(raw_entry)
            matched_key = str(raw_key)
            break
    if entry is None:
        raise ValueError("ENGINE_B_LIVE_QUOTE_MISSING")

    try:
        price = float(entry.get("price"))
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        raise ValueError("ENGINE_B_LIVE_QUOTE_INVALID_PRICE")

    quote_source = str(entry.get("source") or "").strip().lower()
    # MT5 polling/fetching can repeatedly receive the broker's last known tick
    # while a market is closed. ``ts`` is the local receipt time, so using it
    # would make that frozen quote appear fresh on every poll. MT5 entries carry
    # the normalized broker tick time separately; fail closed when it is absent.
    timestamp_key = "broker_ts" if quote_source == "mt5" else "ts"
    try:
        timestamp = float(entry.get(timestamp_key))
        if timestamp > 1e12:
            timestamp /= 1000.0
    except (TypeError, ValueError):
        timestamp = 0.0
    if timestamp <= 0:
        raise ValueError("ENGINE_B_LIVE_QUOTE_AGE_UNKNOWN")

    now_s = float(time_now if time_now is not None else time.time())
    age_sec = max(0.0, now_s - timestamp)
    asset_type = str(pair.get("type") or "").strip().lower()
    age_cfg = (config or {}).get("LIVE_PRICE_MAX_AGE_SEC") or {}
    threshold_type = "etf" if asset_type == "etf_bond" else asset_type
    try:
        max_age_sec = float(age_cfg.get(threshold_type))
    except (AttributeError, TypeError, ValueError):
        max_age_sec = 0.0
    if max_age_sec <= 0:
        raise ValueError(f"ENGINE_B_LIVE_QUOTE_MAX_AGE_UNCONFIGURED:{asset_type or 'unknown'}")
    if age_sec > max_age_sec:
        raise ValueError(f"ENGINE_B_LIVE_QUOTE_STALE:{age_sec:.3f}s>{max_age_sec:.3f}s")

    return price, {
        "source": entry.get("source"),
        "matchedKey": matched_key,
        "timestamp": timestamp,
        "timestampSource": timestamp_key,
        "ageSec": round(age_sec, 3),
        "maxAgeSec": max_age_sec,
        "thresholdAssetType": threshold_type,
        "fresh": True,
    }


# Backward-compatible Engine B name; Engine A uses the same quote contract.
engine_b_live_gate_quote = fresh_live_gate_quote


def engine_b_live_market_state(
    pair: dict,
    tf: str,
    limit: int,
    *,
    candles: list[dict[str, Any]] | None = None,
    fetch_mt5: Callable[[dict, str, int], Any] | None = None,
    fetch_market_state: Callable[[dict, str, int], dict] | None = None,
    extract_candles: Callable[[Any], list[dict[str, Any]] | None] | None = None,
    log: Any | None = None,
    time_now: float | None = None,
) -> dict:
    """Return market state dict: {confirmed, forming, is_live, ...}.

    Behavior matches `athena.py` `_engine_b_live_market_state()`:
    - If `candles` are provided, they are split into confirmed/forming directly.
      (Used by scan overlays that already fetched raw candles.)
    - Else for MT5 pairs, fetch directly from MT5 and normalize via
      ``get_tf_market_state`` (same stale/calendar-gap rules as Engine A).
    - Else fall back to the shared `fetch_market_state` path.

    Important: do not return raw ``split_market_state`` for live Engine B.
    Its age>tf_seconds ``stale`` flag ignores D1 weekend calendar-gap grace and
    false-blocks execute with ``stale_required_closed_timeframes:D1``.
    """
    from athena_app.services.market_state import (
        get_tf_market_state,
        trim_mt5_d1_broker_session_ahead_tail,
    )

    tf_u = str(tf or "").upper()
    display = pair.get("display") or pair.get("symbol") or ""

    def _state_from_series(series: list[dict[str, Any]]) -> dict:
        return get_tf_market_state(
            pair,
            tf_u,
            candles=series,
            time_now=time_now,
        )

    if candles is not None:
        series, _ = trim_mt5_d1_broker_session_ahead_tail(
            pair,
            tf_u,
            list(candles or []),
            time_now=time_now,
        )
        return _state_from_series(series)

    if pair.get("source") == "mt5" and fetch_mt5 is not None:
        try:
            raw = fetch_mt5(pair, tf_u, int(limit))
            series: list[dict[str, Any]] = []
            if extract_candles is not None:
                series = extract_candles(raw) or []
            elif isinstance(raw, list):
                series = raw
            elif isinstance(raw, dict):
                series = (raw.get("candles") or []) if isinstance(raw.get("candles"), list) else []
            series, _ = trim_mt5_d1_broker_session_ahead_tail(
                pair,
                tf_u,
                series,
                time_now=time_now,
            )
            return _state_from_series(series)
        except Exception as e:
            if log is not None:
                try:
                    log.debug(
                        "[ENGINE B LIVE] %s %s: MT5 direct state failed, falling back to shared market state: %s",
                        display,
                        tf_u,
                        e,
                    )
                except Exception:
                    pass

    if fetch_market_state is None:
        # Best-effort fallback: no market state available.
        return {
            **_state_from_series([]),
            "error": "market_state_unavailable",
            "reason": "no_fetch_market_state_and_no_candles",
        }

    result = fetch_market_state(pair, tf_u, int(limit))
    if not result or not result.get("confirmed"):
        result = dict(result or {})
        result.setdefault("error", "market_state_empty")
        result.setdefault("reason", "fetch_market_state_returned_empty")
    return result


def engine_b_confirmed_candles_from_raw(
    pair: dict,
    tf: str,
    raw_candles: list[dict[str, Any]] | None,
    *,
    time_now: float | None = None,
) -> list[dict[str, Any]]:
    """Return confirmed-only candles for Engine B (matches scanner discipline)."""
    from datetime import datetime, timezone

    from athena_app.services.market_state import (
        market_state_offset_hours,
        split_market_state,
    )

    raw_list = list(raw_candles or [])
    tf_u = str(tf or "").upper()

    if pair.get("source") == "mt5":
        state = engine_b_live_market_state(
            pair,
            tf_u,
            len(raw_list),
            candles=raw_list,
            time_now=time_now,
        )
        return list(state.get("confirmed") or [])

    now_dt = (
        datetime.fromtimestamp(time_now, tz=timezone.utc)
        if time_now is not None
        else datetime.now(timezone.utc)
    )

    def _should_drop_last(bars: list[dict[str, Any]]) -> bool:
        if not bars or len(bars) <= 1:
            return False
        last_t = bars[-1].get("time") or bars[-1].get("datetime")
        if not last_t:
            return True
        try:
            ts_str = str(last_t).replace("Z", "+00:00")
            bar_dt = datetime.fromisoformat(ts_str)
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=timezone.utc)
            return bar_dt > now_dt
        except Exception:
            return True

    work = list(raw_list)
    if _should_drop_last(work):
        work = work[:-1]

    state = split_market_state(
        work,
        tf_u,
        pair.get("display") or pair.get("symbol") or "",
        time_now=time_now,
        offset_hours=market_state_offset_hours(pair, tf_u),
    )
    return list(state.get("confirmed") or [])


def engine_b_gate_current_price(
    *,
    explicit_price: Any = None,
    structure_entry_candles: list[dict[str, Any]] | None = None,
    trigger_entry_candles: list[dict[str, Any]] | None = None,
) -> float:
    """Resolve Engine B gate ``current_price`` (room/location/RR distance maths).

    Matches the full scan path (``scanner.py``): last close on the structure entry
    TF (confirmed-only when ``ENGINE_B_STRIP_FORMING_STRUCT`` is true). Trigger
    series may include the live forming bar for follow-through bonuses only — never
    for gate pricing when structure candles are confirmed-only.
    """
    if explicit_price is not None:
        try:
            px = float(explicit_price)
            if px > 0:
                return px
        except (TypeError, ValueError):
            pass
    confirmed = list(structure_entry_candles or [])
    if confirmed:
        return float(confirmed[-1]["close"])
    trigger = list(trigger_entry_candles or [])
    if trigger:
        return float(trigger[-1]["close"])
    raise ValueError("engine_b_gate_current_price: no candle series for price")

