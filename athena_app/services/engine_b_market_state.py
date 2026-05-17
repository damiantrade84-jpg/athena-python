"""Shared Engine B live market-state helper.

This factors out the `athena.py` `_engine_b_live_market_state()` behavior so all call
sites use identical confirmed/forming candle discipline.
"""

from __future__ import annotations

from typing import Any, Callable


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
    - Else for MT5 pairs, fetch directly from MT5 and split via `split_market_state`.
    - Else fall back to the shared `fetch_market_state` path.
    """
    from athena_app.services.market_state import (
        market_state_offset_hours,
        split_market_state,
        trim_mt5_d1_broker_session_ahead_tail,
    )

    tf_u = str(tf or "").upper()
    display = pair.get("display") or pair.get("symbol") or ""
    offset_hours = market_state_offset_hours(pair, tf_u)

    if candles is not None:
        series, _ = trim_mt5_d1_broker_session_ahead_tail(
            pair,
            tf_u,
            list(candles or []),
            time_now=time_now,
        )
        return split_market_state(
            series,
            tf_u,
            display,
            time_now=time_now,
            offset_hours=offset_hours,
        )

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
            return split_market_state(
                series,
                tf_u,
                display,
                time_now=time_now,
                offset_hours=offset_hours,
            )
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
            **split_market_state(
                [],
                tf_u,
                display,
                time_now=time_now,
                offset_hours=offset_hours,
            ),
            "error": "market_state_unavailable",
            "reason": "no_fetch_market_state_and_no_candles",
        }

    result = fetch_market_state(pair, tf_u, int(limit))
    if not result or not result.get("confirmed"):
        result = dict(result or {})
        result.setdefault("error", "market_state_empty")
        result.setdefault("reason", "fetch_market_state_returned_empty")
    return result

