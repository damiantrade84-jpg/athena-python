"""Market-state feed for OX Alpha.

Loads candles from the platform candle cache via the canonical
confirmed/forming split, proves per-timeframe freshness with the canonical
diagnostic, and fails closed on any error. No bespoke bucket math.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

log = logging.getLogger("ox_alpha.feed")


def pair_dict(pair: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": pair.get("symbol") or pair.get("display"),
        "display": pair.get("display") or pair.get("symbol"),
        "type": pair.get("type"),
        "source": pair.get("source"),
        "score_group": pair.get("score_group") or pair.get("scoreGroup"),
    }


def mt5_broker_symbol(pair: Mapping[str, Any]) -> str | None:
    """Resolve the broker-tradable MT5 symbol for an MT5-sourced pair.

    The catalog ``symbol`` is a research/catalog id (``EURUSD=X``, ``^GSPC``,
    ``DIS.US``) and is never a broker symbol; the local broker's name comes
    only from the platform mapper, which overlays machine-local
    ``MT5_SYMBOL_OVERRIDES`` (e.g. ``GBPUSD.s``, ``#DIS``). No mapping ->
    None, and every caller must fail closed on that.
    """
    display = str(pair.get("display") or pair.get("symbol") or "").strip()
    if not display:
        return None
    try:
        from mt5_executor import mt5_map_symbol

        return mt5_map_symbol(display) or None
    except Exception as exc:
        log.debug("OX Alpha MT5 symbol mapping unavailable for %s: %s", display, exc)
        return None


def _sort_confirmed_ascending(state: dict[str, Any]) -> None:
    """Enforce oldest->newest order on confirmed bars.

    Every downstream consumer (engine indicators, split/freshness diagnostics)
    indexes [-1] as the most recent bar; an out-of-order source must never be
    scored as if its oldest bar were the newest.
    """
    try:
        from athena_app.services.market_state import candle_timestamp_epoch

        confirmed = list(state.get("confirmed") or [])
        confirmed.sort(key=lambda c: candle_timestamp_epoch(c) or 0)
        state["confirmed"] = confirmed
    except Exception:
        pass


def _severity_ok(severity: str | None, tf: str = "") -> bool:
    """Freshness bar for an INTRADAY engine.

    fresh / one-bucket lag only. The D1 calendar-gap pass is legal for D1
    alone (a daily bar legitimately spans weekends); a blanket policy_ok
    acceptance would let exchange-closed markets score trades.
    """
    s = str(severity or "")
    if tf.strip().upper() == "D1" and (
        s == "d1_calendar_gap_policy_ok" or "policy_ok" in s
    ):
        return True
    return s in {"fresh", "stale_1_bucket"}


def _state_from_production_routing(
    pair: Mapping[str, Any],
    tf: str,
    limit: int,
    *,
    time_now: float | None = None,
) -> dict[str, Any] | None:
    """Fetch a market state through the canonical candle_manager facade.

    This is the same production routing the main scanner uses (builder ->
    venue REST/MT5 fallbacks with TTL caching), so OX Alpha never depends on
    the in-memory CandleBuilder being fed for every pair/timeframe.
    """
    try:
        from candle_manager import fetch_market_state
    except Exception as exc:
        log.debug("OX Alpha candle_manager unavailable: %s", exc)
        return None
    try:
        state = fetch_market_state(pair_dict(pair), tf, int(limit))
    except Exception as exc:
        log.debug("OX Alpha candle_manager fetch failed %s %s: %s",
                  pair.get("display"), tf, exc)
        return None
    if not isinstance(state, dict):
        return None
    return state


def load_market_states(
    pair: Mapping[str, Any],
    timeframes: Sequence[str],
    limits: Mapping[str, int] | None = None,
    *,
    time_now: float | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, bool]]:
    """Return ({tf: market_state}, {tf: fresh_ok}) for the requested TFs.

    Primary source is the canonical candle_manager routing; the CandleBuilder
    split remains as fallback. Freshness is always re-proven with the canonical
    diagnostic and fails closed on any error.
    """
    limits = limits or {}
    states: dict[str, dict[str, Any]] = {}
    freshness: dict[str, bool] = {}
    try:
        from candle_feeds import fetch_candles_live
        from athena_app.services.market_state import (
            candle_freshness_diagnostic,
            market_state_offset_hours,
            split_market_state,
        )
    except Exception as exc:  # pragma: no cover - app-context only
        log.debug("OX Alpha feed imports unavailable: %s", exc)
        for tf in timeframes:
            states[tf] = {"confirmed": [], "forming": None, "stale": True}
            freshness[tf] = False
        return states, freshness

    pd = pair_dict(pair)
    display = str(pd["display"] or "")
    for tf in timeframes:
        limit = int(limits.get(tf) or (300 if tf == "M15" else 200))
        state: dict[str, Any] | None = _state_from_production_routing(
            pair, tf, limit, time_now=time_now
        )
        if state is None:
            try:
                res = fetch_candles_live(display, tf, limit)
                candles = list((res or {}).get("candles") or [])
            except Exception as exc:
                log.debug("OX Alpha candle fetch failed %s %s: %s", display, tf, exc)
                candles = []
            if not candles:
                states[tf] = {"confirmed": [], "forming": None, "stale": True}
                freshness[tf] = False
                continue
            try:
                offset = market_state_offset_hours(pd, tf)
                state = split_market_state(
                    candles,
                    tf,
                    display,
                    time_now=time_now,
                    offset_hours=offset,
                    provider=pd.get("source"),
                    provider_symbol=pd.get("symbol"),
                )
            except Exception as exc:
                log.debug("OX Alpha market-state split failed %s %s: %s", display, tf, exc)
                states[tf] = {"confirmed": [], "forming": None, "stale": True}
                freshness[tf] = False
                continue
            series = candles
        else:
            confirmed = list(state.get("confirmed") or [])
            forming = state.get("forming")
            series = confirmed + ([forming] if isinstance(forming, Mapping) else [])

        if not state.get("confirmed"):
            state["stalenessSeverity"] = state.get("stalenessSeverity") or "no_confirmed_candles"
            states[tf] = state
            freshness[tf] = False
            continue
        _sort_confirmed_ascending(state)
        try:
            diag = candle_freshness_diagnostic(
                pd, tf, series, time_now=time_now, source=str(pd.get("source") or "")
            )
            state["stalenessSeverity"] = diag.get("stalenessSeverity")
            state["candleFreshness"] = diag
        except Exception as exc:
            log.debug("OX Alpha freshness diagnostic failed %s %s: %s", display, tf, exc)
            states[tf] = {"confirmed": [], "forming": None, "stale": True}
            freshness[tf] = False
            continue
        states[tf] = state
        freshness[tf] = _severity_ok(state.get("stalenessSeverity"), tf)
    return states, freshness


def load_quote(
    pair: Mapping[str, Any],
    *,
    max_age_sec: float = 30.0,
    time_now: float | None = None,
) -> dict[str, Any] | None:
    """Live bid/ask for executable geometry. Missing/stale/malformed -> None."""
    import time as _time

    now = float(time_now if time_now is not None else _time.time())
    try:
        max_age = float(max_age_sec)
    except (TypeError, ValueError):
        return None
    if max_age <= 0:
        return None
    try:
        from config import CONFIG as _cfg

        age_map = _cfg.get("LIVE_PRICE_MAX_AGE_SEC") or {}
        if isinstance(age_map, dict):
            asset_key = str(pair.get("type") or "default").lower()
            platform_cap = age_map.get(asset_key, age_map.get("default"))
            if platform_cap is not None:
                max_age = min(max_age, float(platform_cap))
    except Exception:
        pass
    pd = pair_dict(pair)
    asset = str(pd.get("type") or "").lower()
    source = str(pd.get("source") or "").lower()
    symbol = str(pd.get("symbol") or pd.get("display") or "")
    quote: dict[str, Any] | None = None
    try:
        if asset == "crypto" or source == "bybit":
            from data_feeds import _fetch_bybit_ticker

            raw = _fetch_bybit_ticker(symbol)
            if isinstance(raw, dict):
                ts_raw = raw.get("ts")
                if ts_raw is None:
                    return None
                quote = {
                    "bid": raw.get("bid"),
                    "ask": raw.get("ask"),
                    "ts": float(ts_raw),
                    "source": str(raw.get("source") or "bybit"),
                }
        elif source == "mt5":
            import MetaTrader5 as mt5  # type: ignore
            from athena_app.services.mt5_time_alignment import (
                normalize_mt5_tick_epoch_utc,
            )
            from config import CONFIG as _mt5_cfg

            broker_symbol = mt5_broker_symbol(pd)
            if not broker_symbol:
                return None
            tick = mt5.symbol_info_tick(broker_symbol)
            if tick is None:
                mt5.symbol_select(broker_symbol, True)
                tick = mt5.symbol_info_tick(broker_symbol)
            if tick is not None:
                bid = float(getattr(tick, "bid", 0) or 0)
                ask = float(getattr(tick, "ask", 0) or 0)
                ts_msc = getattr(tick, "time_msc", None)
                raw_ts = (
                    float(ts_msc) / 1000.0
                    if ts_msc
                    else float(getattr(tick, "time", 0) or 0)
                )
                ts = normalize_mt5_tick_epoch_utc(
                    raw_ts if raw_ts > 0 else None,
                    now,
                    int(_mt5_cfg.get("MT5_BROKER_UTC_OFFSET", 3)),
                )
                if ts is None:
                    return None
                quote = {
                    "bid": bid if bid > 0 else None,
                    "ask": ask if ask > 0 else None,
                    "ts": float(ts),
                    "source": "mt5_tick",
                }
    except Exception as exc:
        log.debug("OX Alpha quote fetch failed %s: %s", pd.get("display"), exc)
        return None
    if not quote:
        return None
    ts = float(quote.get("ts") or 0)
    if ts <= 0 or (now - ts) > max_age:
        return None
    try:
        bid_f = float(quote.get("bid"))
        ask_f = float(quote.get("ask"))
    except (TypeError, ValueError):
        return None
    if bid_f <= 0 or ask_f < bid_f:
        return None
    quote["ageSec"] = round(max(0.0, now - ts), 3)
    return quote


def freshness_ok_for_exec(
    pair: Mapping[str, Any],
    exec_dict: dict[str, Any],
    timeframes: Sequence[str] = ("M15", "H1"),
    *,
    time_now: float | None = None,
) -> tuple[bool, str]:
    """Execute-time freshness proof — re-reads the cache and fails closed."""
    states, fresh = load_market_states(pair, timeframes, time_now=time_now)
    bad = sorted(tf for tf, ok in fresh.items() if not ok)
    if bad:
        return False, f"stale_timeframes:{','.join(bad)}"
    stamped: dict[str, Any] = {}
    for tf in timeframes:
        state = states.get(tf) if isinstance(states.get(tf), dict) else {}
        diag = state.get("candleFreshness") if isinstance(state.get("candleFreshness"), dict) else {}
        row = dict(diag)
        if not row.get("stalenessSeverity"):
            row["stalenessSeverity"] = state.get("stalenessSeverity")
        if not row.get("stalenessSeverity"):
            return False, f"freshness_evidence_missing:{tf}"
        stamped[str(tf)] = row
    exec_dict["candleFreshness"] = stamped
    return True, "ok"
