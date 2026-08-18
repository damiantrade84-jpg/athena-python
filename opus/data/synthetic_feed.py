"""Deterministic synthetic feed.

Exists so OPUS can be exercised end to end - engine, API and UI - without a
broker connection or an internet route. Every bar it produces is labelled
`source="SYNTHETIC"`, and the execution router refuses to place a live order
against a synthetic quote. Demo data must never be able to masquerade as
market data.

One master path per symbol, generated at M5 and AGGREGATED upward to M15/H1/H4,
with the quote taken from the same path's final close. Generating each
timeframe as its own independent random series - the obvious shortcut - makes
the rungs mutually inconsistent: the H4 close and the M5 close describe
different markets, session VWAP lands tens of sigma from the quote, and every
value-based reading becomes a measurement of the fixture. A real venue always
returns one coherent instrument, so the fixture must too.
"""

from __future__ import annotations

import hashlib
import threading
import time

import numpy as np

from opus.data.feed import Feed
from opus.indicators.base import TF_SECONDS
from opus.types import Candles, Quote

SOURCE = "SYNTHETIC"

# M5 bars in the master path: enough to aggregate 400 H4 bars (19 200 M5 bars).
_MASTER_BARS = 26_000
_MASTER_STEP = 300

_BASE_PRICE = {
    "EURUSD": 1.0850, "GBPUSD": 1.2700, "USDJPY": 152.40,
    "AUDUSD": 0.6600, "USDCAD": 1.3600, "XAUUSD": 2380.0,
    "XAGUSD": 28.50, "BTCUSDT": 68000.0, "ETHUSDT": 3500.0,
    "SOLUSDT": 165.0,
}

# Per-M5-bar volatility and half-spread as a fraction of price. Spreads are set
# to realistic retail levels so the cost model is exercised honestly rather
# than being flattered by a zero-cost fixture.
_PROFILE = {
    "fx":      {"vol": 0.00035, "half_spread": 0.000025},
    "jpy":     {"vol": 0.00035, "half_spread": 0.000030},
    "metals":  {"vol": 0.00075, "half_spread": 0.000110},
    "crypto":  {"vol": 0.00110, "half_spread": 0.000060},
}

_cache: dict[str, dict] = {}
_lock = threading.Lock()


def _seed_for(text: str) -> int:
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)


def _profile_for(symbol: str) -> dict:
    if symbol.endswith("USDT") or symbol.endswith("USD") and symbol[:3] in {"BTC", "ETH", "SOL"}:
        return _PROFILE["crypto"]
    if symbol.startswith("XA"):
        return _PROFILE["metals"]
    if "JPY" in symbol:
        return _PROFILE["jpy"]
    return _PROFILE["fx"]


def _base_price(symbol: str) -> float:
    if symbol in _BASE_PRICE:
        return _BASE_PRICE[symbol]
    return 100.0 + (_seed_for(symbol) % 400)


def _master(symbol: str) -> dict:
    """Build (once) the M5 master series for a symbol."""
    now_bucket = int(time.time() // _MASTER_STEP)
    with _lock:
        cached = _cache.get(symbol)
        if cached is not None and cached["bucket"] == now_bucket:
            return cached

        rng = np.random.default_rng(_seed_for(symbol))
        profile = _profile_for(symbol)
        n = _MASTER_BARS
        vol = float(profile["vol"])

        # A slow cycle blended into the walk so the series actually visits
        # different regimes; pure noise leaves the classifier in one quadrant.
        steps = rng.normal(0.0, vol, n)
        cycle = 0.45 * vol * np.sin(np.arange(n) / 420.0)
        close = _base_price(symbol) * np.exp(np.cumsum(steps + cycle))

        open_ = np.concatenate(([_base_price(symbol)], close[:-1]))
        up = np.abs(rng.normal(0.0, vol * 0.65, n))
        dn = np.abs(rng.normal(0.0, vol * 0.65, n))
        high = np.maximum(open_, close) * (1.0 + up)
        low = np.minimum(open_, close) * (1.0 - dn)
        volume = rng.lognormal(mean=8.0, sigma=0.4, size=n)

        # Anchor so the final master bar is the most recently CLOSED M5 bar.
        last_closed = (now_bucket - 1) * _MASTER_STEP
        ts = last_closed - np.arange(n - 1, -1, -1) * _MASTER_STEP

        built = {
            "bucket": now_bucket, "ts": ts.astype(float), "open": open_,
            "high": high, "low": low, "close": close, "volume": volume,
        }
        _cache[symbol] = built
        return built


def _aggregate(master: dict, factor: int, count: int) -> dict:
    """Aggregate the M5 master into `factor`-sized bars, newest `count` kept."""
    if factor <= 1:
        take = min(count, master["ts"].shape[0])
        return {k: master[k][-take:] for k in ("ts", "open", "high", "low", "close", "volume")}

    total = master["ts"].shape[0]
    groups = total // factor
    usable = groups * factor
    offset = total - usable

    def _reshape(key: str) -> np.ndarray:
        return master[key][offset:].reshape(groups, factor)

    ts = _reshape("ts")[:, 0]
    open_ = _reshape("open")[:, 0]
    high = _reshape("high").max(axis=1)
    low = _reshape("low").min(axis=1)
    close = _reshape("close")[:, -1]
    volume = _reshape("volume").sum(axis=1)

    take = min(count, groups)
    return {
        "ts": ts[-take:], "open": open_[-take:], "high": high[-take:],
        "low": low[-take:], "close": close[-take:], "volume": volume[-take:],
    }


class SyntheticFeed(Feed):
    name = SOURCE

    def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        tf = timeframe.upper()
        span = TF_SECONDS.get(tf, 300)
        factor = max(1, int(span // _MASTER_STEP))
        block = _aggregate(_master(symbol), factor, int(max(count, 60)))
        return Candles(
            symbol=symbol, timeframe=tf, ts=block["ts"],
            open=block["open"], high=block["high"], low=block["low"],
            close=block["close"], volume=block["volume"],
            source=SOURCE, volume_is_tick_count=False,
        )

    def quote(self, symbol: str) -> Quote | None:
        master = _master(symbol)
        mid = float(master["close"][-1])
        half = mid * float(_profile_for(symbol)["half_spread"])
        return Quote(
            symbol=symbol, bid=mid - half, ask=mid + half,
            ts=time.time(), source=SOURCE,
        )

    def book(self, symbol: str, depth: int = 10) -> dict | None:
        q = self.quote(symbol)
        if q is None:
            return None
        rng = np.random.default_rng(_seed_for(symbol) + 7)
        step = max(q.mid * 0.00004, 1e-9)
        bids = [[q.bid - i * step, float(rng.lognormal(3.0, 0.5))] for i in range(depth)]
        asks = [[q.ask + i * step, float(rng.lognormal(3.0, 0.5))] for i in range(depth)]
        return {"bids": bids, "asks": asks, "source": SOURCE}


__all__ = ["SyntheticFeed", "SOURCE"]
