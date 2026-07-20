"""Confirmed-bars-only view over stored candles.

Delegates to the same market-state split the live scan and the old backtester
use, so a backtest never sees a bar that would still be "forming" at the
as-of time it is replaying.
"""

from __future__ import annotations

from athena_app.services.market_state import market_state_offset_hours, split_market_state


def confirmed_candles(pair: dict, tf: str, candles: list[dict], *, as_of_epoch: float) -> list[dict]:
    """Return only bars confirmed as of `as_of_epoch` (exclusive of the forming bar)."""
    state = split_market_state(
        list(candles or []),
        tf,
        pair.get("display") or pair.get("symbol") or "",
        time_now=as_of_epoch,
        offset_hours=market_state_offset_hours(pair, tf),
    )
    return list(state.get("confirmed") or [])
