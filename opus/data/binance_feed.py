"""Binance public market data for OPUS. Read-only REST, no authentication."""

from __future__ import annotations

import numpy as np
import requests

from opus.data.feed import Feed, FeedError, venue_time
from opus.types import Candles, Quote

SOURCE = "BINANCE"
_BASE = "https://api.binance.com"
_TIMEOUT = 12.0

_INTERVAL = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "OPUS/1.0"})


def _request(path: str, params: dict):
    """(payload, venue timestamp). Binance publishes no timestamp on
    bookTicker, so the HTTP `Date` header is the venue clock available."""
    try:
        response = _session.get(f"{_BASE}{path}", params=params, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.json(), venue_time(None, response)
    except requests.RequestException as exc:
        raise FeedError(f"Binance request failed: {exc}") from exc
    except ValueError as exc:
        raise FeedError("Binance returned a non-JSON response") from exc


def _get(path: str, params: dict):
    return _request(path, params)[0]


class BinanceFeed(Feed):
    name = SOURCE

    def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        interval = _INTERVAL.get(timeframe.upper())
        if interval is None:
            raise FeedError(f"Binance: unsupported timeframe {timeframe}")

        wanted = int(count)
        rows: list = []
        end_ms: int | None = None
        # 1000 klines per request; page backwards until `count` is satisfied.
        while len(rows) < wanted:
            params = {
                "symbol": symbol, "interval": interval,
                "limit": min(1000, wanted - len(rows)),
            }
            if end_ms is not None:
                params["endTime"] = end_ms
            page = _get("/api/v3/klines", params)
            if not page:
                break
            rows = list(page) + rows
            end_ms = int(page[0][0]) - 1
            if len(page) < 2:
                break

        if not rows:
            raise FeedError(f"Binance: no {timeframe} candles for {symbol}")

        arr = np.array([[float(x) for x in row[:6]] for row in rows], dtype=float)
        order = np.argsort(arr[:, 0])
        arr = arr[order]
        _, unique = np.unique(arr[:, 0], return_index=True)
        arr = arr[np.sort(unique)]

        return Candles(
            symbol=symbol, timeframe=timeframe.upper(), ts=arr[:, 0] / 1000.0,
            open=arr[:, 1], high=arr[:, 2], low=arr[:, 3], close=arr[:, 4],
            volume=arr[:, 5], source=SOURCE, volume_is_tick_count=False,
        )

    def quote(self, symbol: str) -> Quote | None:
        payload, venue_ts = _request("/api/v3/ticker/bookTicker", {"symbol": symbol})
        try:
            bid = float(payload.get("bidPrice") or 0.0)
            ask = float(payload.get("askPrice") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        if venue_ts is None:
            # Without the venue's clock, `quote_age` cannot fail on this feed.
            return None
        return Quote(symbol=symbol, bid=bid, ask=ask, ts=venue_ts, source=SOURCE)

    def book(self, symbol: str, depth: int = 10) -> dict | None:
        try:
            payload = _get(
                "/api/v3/depth",
                {"symbol": symbol, "limit": max(5, min(int(depth), 100))},
            )
        except FeedError:
            return None
        bids = [[float(p), float(s)] for p, s in (payload.get("bids") or [])]
        asks = [[float(p), float(s)] for p, s in (payload.get("asks") or [])]
        if not bids or not asks:
            return None
        return {"bids": bids[:depth], "asks": asks[:depth], "source": SOURCE}


__all__ = ["BinanceFeed", "SOURCE"]
