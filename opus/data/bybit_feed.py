"""Bybit v5 public market data for OPUS.

Read-only REST. Public market endpoints need no authentication, so this works
out of the box for crypto data even when no API key is configured.
"""

from __future__ import annotations

import time

import numpy as np
import requests

from opus.data.feed import Feed, FeedError
from opus.types import Candles, Quote

SOURCE = "BYBIT"
_BASE = "https://api.bybit.com"
_TIMEOUT = 12.0

# Bybit expresses intraday intervals in minutes as strings.
_INTERVAL = {
    "M1": "1", "M5": "5", "M15": "15", "M30": "30",
    "H1": "60", "H4": "240", "D1": "D",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "OPUS/1.0"})


def _get(path: str, params: dict) -> dict:
    try:
        response = _session.get(f"{_BASE}{path}", params=params, timeout=_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise FeedError(f"Bybit request failed: {exc}") from exc
    except ValueError as exc:
        raise FeedError("Bybit returned a non-JSON response") from exc

    if int(payload.get("retCode", -1)) != 0:
        raise FeedError(f"Bybit error {payload.get('retCode')}: {payload.get('retMsg')}")
    return payload.get("result") or {}


def _category(symbol: str) -> str:
    # USDT perpetuals are the liquid intraday instruments; spot is the fallback.
    return "linear" if symbol.upper().endswith("USDT") else "spot"


class BybitFeed(Feed):
    name = SOURCE

    def candles(self, symbol: str, timeframe: str, count: int) -> Candles:
        interval = _INTERVAL.get(timeframe.upper())
        if interval is None:
            raise FeedError(f"Bybit: unsupported timeframe {timeframe}")

        wanted = int(count)
        rows: list = []
        end: int | None = None
        # Bybit caps a single kline response at 1000 bars, so page backwards.
        while len(rows) < wanted:
            params = {
                "category": _category(symbol), "symbol": symbol,
                "interval": interval, "limit": str(min(1000, wanted - len(rows))),
            }
            if end is not None:
                params["end"] = str(end)
            result = _get("/v5/market/kline", params)
            page = result.get("list") or []
            if not page:
                break
            rows.extend(page)
            # Bybit returns newest-first; step back past the oldest returned.
            oldest = int(page[-1][0])
            if end is not None and oldest >= end:
                break
            end = oldest - 1
            if len(page) < 200:
                break

        if not rows:
            raise FeedError(f"Bybit: no {timeframe} candles for {symbol}")

        arr = np.array(
            [[float(x) for x in row[:6]] for row in rows], dtype=float
        )
        order = np.argsort(arr[:, 0])
        arr = arr[order]
        return Candles(
            symbol=symbol, timeframe=timeframe.upper(),
            ts=arr[:, 0] / 1000.0,
            open=arr[:, 1], high=arr[:, 2], low=arr[:, 3], close=arr[:, 4],
            volume=arr[:, 5], source=SOURCE, volume_is_tick_count=False,
        )

    def quote(self, symbol: str) -> Quote | None:
        result = _get(
            "/v5/market/tickers", {"category": _category(symbol), "symbol": symbol}
        )
        rows = result.get("list") or []
        if not rows:
            return None
        row = rows[0]
        try:
            bid = float(row.get("bid1Price") or 0.0)
            ask = float(row.get("ask1Price") or 0.0)
        except (TypeError, ValueError):
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        return Quote(symbol=symbol, bid=bid, ask=ask, ts=time.time(), source=SOURCE)

    def book(self, symbol: str, depth: int = 10) -> dict | None:
        try:
            result = _get(
                "/v5/market/orderbook",
                {"category": _category(symbol), "symbol": symbol,
                 "limit": str(max(1, min(depth, 50)))},
            )
        except FeedError:
            return None
        bids = [[float(p), float(s)] for p, s in (result.get("b") or [])]
        asks = [[float(p), float(s)] for p, s in (result.get("a") or [])]
        if not bids or not asks:
            return None
        return {"bids": bids[:depth], "asks": asks[:depth], "source": SOURCE}


__all__ = ["BybitFeed", "SOURCE"]
