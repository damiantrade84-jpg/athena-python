"""Market-state feed for OX Alpha.

Loads candles from the platform candle cache via the canonical
confirmed/forming split, proves per-timeframe freshness with the canonical
diagnostic, and fails closed on any error. No bespoke bucket math.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

log = logging.getLogger("ox_alpha.feed")

_SEVERITY_PASS = {"fresh", "stale_1_bucket", "d1_calendar_gap_policy_ok"}


def pair_dict(pair: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": pair.get("symbol") or pair.get("display"),
        "display": pair.get("display") or pair.get("symbol"),
        "type": pair.get("type"),
        "source": pair.get("source"),
        "score_group": pair.get("score_group") or pair.get("scoreGroup"),
    }


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


def _severity_ok(severity: str | None) -> bool:
    s = str(severity or "")
    return s in _SEVERITY_PASS or "policy_ok" in s


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
        freshness[tf] = _severity_ok(state.get("stalenessSeverity"))
    return states, freshness


def freshness_ok_for_exec(
    pair: Mapping[str, Any],
    exec_dict: dict[str, Any],
    timeframes: Sequence[str] = ("M15", "H1"),
    *,
    time_now: float | None = None,
) -> tuple[bool, str]:
    """Execute-time freshness proof — re-reads the cache and fails closed."""
    _, fresh = load_market_states(pair, timeframes, time_now=time_now)
    bad = sorted(tf for tf, ok in fresh.items() if not ok)
    if bad:
        return False, f"stale_timeframes:{','.join(bad)}"
    exec_dict["candleFreshness"] = {
        tf: {"fresh": ok} for tf, ok in fresh.items()
    }
    return True, "ok"
