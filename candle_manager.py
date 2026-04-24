"""Candle manager facade.

Encapsulates candle retrieval and cache/WS entry points behind a dedicated
module boundary to reduce coupling to athena.py internals.
"""

from __future__ import annotations

from typing import Any

from athena_legacy import load as _load_legacy

from athena_app.services.market_state import get_tf_market_state, MarketState

def _backend():
    return _load_legacy()


def fetch_candles(pair: dict[str, Any], tf: str, limit: int) -> list[dict[str, Any]] | None:
    """Fetch candles via the active production data path."""
    return _backend().fetch_candles(pair, tf, limit)


def fetch_market_state(pair: dict[str, Any], tf: str, limit: int) -> MarketState:
    """Fetch structured market state (confirmed + forming) for a pair/timeframe.
    
    Use this when you need metadata about whether the latest bar is still 'open'.
    """
    candles = fetch_candles(pair, tf, limit)
    return get_tf_market_state(pair, tf, candles=candles or [])


def fetch_confirmed_candles(pair: dict[str, Any], tf: str, limit: int) -> list[dict[str, Any]]:
    """Fetch only confirmed (closed) candles for stable scoring when required."""
    state = fetch_market_state(pair, tf, limit)
    return state["confirmed"]


def fetch_eodhd(pair: dict[str, Any], tf: str, limit: int) -> list[dict[str, Any]] | None:
    """Fetch candles directly from EODHD when available."""
    fn = getattr(_backend(), "fetch_eodhd", None)
    if callable(fn):
        return fn(pair, tf, limit)
    return None


def merge_forex_forming_ws(
    candles: list[dict[str, Any]] | None,
    pair: dict[str, Any],
    tf: str,
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Merge forming WS bar for forex charts (display + tf match monolith signature)."""
    fn = getattr(_backend(), "_merge_forex_forming_ws", None)
    if not callable(fn):
        return candles, None
    display = pair.get("display") or pair.get("symbol") or ""
    return fn(candles, display, tf, limit if limit is not None else 250)


def seed_candle_builders() -> None:
    """Run startup seeding for candle builders if backend exposes it."""
    fn = getattr(_backend(), "_seed_candle_builders", None)
    if callable(fn):
        fn()

