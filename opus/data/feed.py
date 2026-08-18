"""Market data access for OPUS.

One interface, several venues. Each adapter is responsible for returning
CONFIRMED bars only - the currently-forming bar is dropped at the source, not
somewhere downstream. A forming bar has a moving high, low and close, so any
indicator that sees it produces a reading that silently rewrites itself as the
bar develops, and any backtest built on it is looking at the future.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

import opus.config as config
from opus.types import Candles, Quote


class FeedError(RuntimeError):
    """Raised when a venue cannot supply usable data."""


@dataclass
class DataBundle:
    """Everything one symbol needs for a full evaluation."""

    symbol: str
    display: str
    asset_class: str
    venue: str
    context: Candles
    structure: Candles
    trigger: Candles
    quote: Quote | None = None
    book: dict | None = None
    timeframes: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    fetched_ts: float = 0.0

    @property
    def usable(self) -> bool:
        bars = config.load()["BARS"]
        return (
            len(self.context) >= int(bars["context_min"])
            and len(self.structure) >= int(bars["structure_min"])
            and len(self.trigger) >= int(bars["trigger_min"])
        )


def drop_forming_bar(candles: Candles, timeframe: str, now: float | None = None) -> Candles:
    """Remove the final bar when its period has not yet elapsed."""
    from opus.indicators.base import TF_SECONDS

    n = len(candles)
    if n == 0:
        return candles
    span = TF_SECONDS.get(timeframe.upper(), 0)
    if span <= 0:
        return candles
    now = float(now if now is not None else time.time())
    if float(candles.ts[-1]) + span > now:
        return candles.tail(n - 1) if n > 1 else Candles(
            symbol=candles.symbol, timeframe=candles.timeframe,
            ts=np.zeros(0), open=np.zeros(0), high=np.zeros(0),
            low=np.zeros(0), close=np.zeros(0), volume=np.zeros(0),
            source=candles.source,
            volume_is_tick_count=candles.volume_is_tick_count,
        )
    return candles


def sanitise(candles: Candles) -> Candles:
    """Drop malformed bars and enforce strictly ascending timestamps.

    Real feeds deliver duplicate bars, out-of-order bars after a reconnect, and
    occasional rows where high < low. Every one of those quietly corrupts a
    sigma estimate or a pivot, so they are removed here rather than defended
    against in nine different indicators.
    """
    n = len(candles)
    if n == 0:
        return candles

    ok = (
        np.isfinite(candles.ts) & np.isfinite(candles.open)
        & np.isfinite(candles.high) & np.isfinite(candles.low)
        & np.isfinite(candles.close)
        & (candles.high >= candles.low)
        & (candles.high >= candles.open) & (candles.high >= candles.close)
        & (candles.low <= candles.open) & (candles.low <= candles.close)
        & (candles.close > 0)
    )
    idx = np.flatnonzero(ok)
    if idx.size == 0:
        raise FeedError(f"{candles.symbol} {candles.timeframe}: no valid bars")

    ts = candles.ts[idx]
    order = np.argsort(ts, kind="stable")
    idx = idx[order]
    ts = candles.ts[idx]
    keep = np.concatenate(([True], np.diff(ts) > 0))
    idx = idx[keep]

    volume = candles.volume[idx]
    volume = np.where(np.isfinite(volume) & (volume >= 0), volume, 0.0)

    return Candles(
        symbol=candles.symbol,
        timeframe=candles.timeframe,
        ts=candles.ts[idx],
        open=candles.open[idx],
        high=candles.high[idx],
        low=candles.low[idx],
        close=candles.close[idx],
        volume=volume,
        source=candles.source,
        volume_is_tick_count=candles.volume_is_tick_count,
    )


class Feed:
    """Base class for a venue adapter."""

    name = "base"

    def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        raise NotImplementedError

    def quote(self, symbol: str) -> Quote | None:
        return None

    def book(self, symbol: str, depth: int = 10) -> dict | None:
        return None

    def close(self) -> None:
        return None


_REGISTRY: dict[str, Feed] = {}


def register(name: str, feed: Feed) -> None:
    _REGISTRY[name] = feed


def get_feed(venue: str) -> Feed | None:
    if venue in _REGISTRY:
        return _REGISTRY[venue]
    feed = _build_feed(venue)
    if feed is not None:
        _REGISTRY[venue] = feed
    return feed


def _build_feed(venue: str) -> Feed | None:
    venue = (venue or "").strip().lower()
    try:
        if venue == "mt5":
            from opus.data.mt5_feed import MT5Feed
            return MT5Feed()
        if venue == "bybit":
            from opus.data.bybit_feed import BybitFeed
            return BybitFeed()
        if venue == "binance":
            from opus.data.binance_feed import BinanceFeed
            return BinanceFeed()
        if venue == "synthetic":
            from opus.data.synthetic_feed import SyntheticFeed
            return SyntheticFeed()
    except Exception:  # noqa: BLE001 - an unavailable venue is not fatal
        return None
    return None


def fetch_bundle(spec: dict, *, now: float | None = None) -> DataBundle:
    """Fetch all three rungs plus the live quote for one universe entry."""
    now = float(now if now is not None else time.time())
    symbol = str(spec["symbol"])
    venue = str(spec["venue"])
    asset_class = str(spec["class"])
    display = str(spec.get("display") or symbol)

    tfs = config.timeframes_for(asset_class)
    bars = config.load()["BARS"]
    errors: list[str] = []

    feed = get_feed(venue)
    if feed is None:
        raise FeedError(f"{symbol}: no feed adapter available for venue '{venue}'")

    def _get(role: str, count_key: str) -> Candles:
        timeframe = str(tfs[role])
        raw = feed.candles(symbol, timeframe, int(bars[count_key]))
        cleaned = sanitise(raw)
        return drop_forming_bar(cleaned, timeframe, now)

    context = _get("context", "context_fetch")
    structure = _get("structure", "structure_fetch")
    trigger = _get("trigger", "trigger_fetch")

    quote = None
    try:
        quote = feed.quote(symbol)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"quote: {exc}")

    book = None
    try:
        book = feed.book(symbol, int(config.indicator("OFP")["book_levels"]))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"book: {exc}")

    return DataBundle(
        symbol=symbol,
        display=display,
        asset_class=asset_class,
        venue=venue,
        context=context,
        structure=structure,
        trigger=trigger,
        quote=quote,
        book=book,
        timeframes=dict(tfs),
        errors=errors,
        fetched_ts=now,
    )


__all__ = [
    "FeedError", "DataBundle", "Feed", "register", "get_feed", "fetch_bundle",
    "sanitise", "drop_forming_bar",
]
