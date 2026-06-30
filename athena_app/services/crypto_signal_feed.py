"""Config-gated crypto signal candle source selection for Engine A/B experiments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


SUPPORTED_CRYPTO_SIGNAL_FEEDS = {"binance", "bybit"}


@dataclass(frozen=True)
class CryptoSignalCandles:
    candles: list[dict] | None
    meta: dict[str, Any]


def _normalize_feed(value: Any) -> str:
    feed = str(value or "binance").strip().lower()
    return feed if feed in SUPPORTED_CRYPTO_SIGNAL_FEEDS else "binance"


def resolve_crypto_signal_feed(engine: str, config: dict | None) -> str:
    """Resolve the configured Engine A/B crypto OHLCV signal feed."""
    cfg = config or {}
    eng = str(engine or "AB").strip().upper()
    engine_key = f"ENGINE_{eng}_CRYPTO_SIGNAL_FEED"
    value = cfg.get(engine_key)
    if value is None:
        # B1: default to "bybit" to match config.yaml and config.py. Previously
        # "binance", which silently produced a different venue than the loaded
        # config.yaml when callers read CONFIG without the YAML layer (audit MED #11).
        value = cfg.get("ENGINE_AB_CRYPTO_SIGNAL_FEED", "bybit")
    return _normalize_feed(value)


def crypto_signal_feed_fallback_enabled(engine: str, config: dict | None) -> bool:
    cfg = config or {}
    eng = str(engine or "AB").strip().upper()
    engine_key = f"ENGINE_{eng}_CRYPTO_SIGNAL_FEED_FALLBACK"
    if engine_key in cfg:
        return bool(cfg.get(engine_key))
    return bool(cfg.get("ENGINE_AB_CRYPTO_SIGNAL_FEED_FALLBACK", False))


def _default_meta(pair: dict, tf: str, limit: int, feed: str) -> dict[str, Any]:
    return {
        "symbol": pair.get("symbol", pair.get("display")),
        "display": pair.get("display", pair.get("symbol")),
        "tf": str(tf or "").upper(),
        "limit": int(limit or 0),
        "signalFeed": feed,
        "actualPriceVolume": True,
        "fallback": None,
        "error": False,
        "detail": None,
    }


def _symbol(pair: dict) -> str:
    return str(pair.get("symbol") or pair.get("display") or "").replace("/", "").upper()


def fetch_crypto_signal_candles(
    pair: dict,
    tf: str,
    limit: int,
    *,
    engine: str,
    config: dict | None,
    default_fetch: Callable[[dict, str, int], list[dict] | None],
    bybit_fetch: Callable[[str, str, int], list[dict] | None] | None = None,
    bybit_paginated_fetch: Callable[[str, str, int], list[dict] | None] | None = None,
) -> CryptoSignalCandles:
    """Fetch Engine A/B crypto signal candles from Binance or Bybit.

    Non-crypto callers should continue using their normal source-specific fetcher.
    This helper exists so crypto experiments can switch OHLCV source without
    mutating scoring formulas or execution gates.
    """
    feed = resolve_crypto_signal_feed(engine, config)
    meta = _default_meta(pair, tf, limit, feed)
    tf_u = str(tf or "").upper()
    limit_i = int(limit or 0)

    if str(pair.get("type") or "").lower() != "crypto" or feed == "binance":
        candles = default_fetch(pair, tf_u, limit_i)
        meta.update(
            {
                "upstream": "binance_futures"
                if str(pair.get("source") or "").lower() == "binance"
                else str(pair.get("source") or "default"),
                "bars": len(candles or []),
            }
        )
        return CryptoSignalCandles(candles, meta)

    if feed == "bybit":
        paginated = limit_i > 1000 and callable(bybit_paginated_fetch)
        estimated_requests = max(1, (limit_i + 999) // 1000)
        fetcher = (
            bybit_paginated_fetch
            if paginated
            else bybit_fetch
        )
        candles = fetcher(_symbol(pair), tf_u, limit_i) if callable(fetcher) else None
        if candles:
            meta.update(
                {
                    "upstream": "bybit_linear_kline",
                    "bars": len(candles),
                    "paginated": paginated,
                    "estimatedRequests": estimated_requests if paginated else 1,
                }
            )
            return CryptoSignalCandles(candles, meta)

        if crypto_signal_feed_fallback_enabled(engine, config):
            fallback = default_fetch(pair, tf_u, limit_i)
            meta.update(
                {
                    "upstream": "binance_futures"
                    if str(pair.get("source") or "").lower() == "binance"
                    else str(pair.get("source") or "default"),
                    "fallback": "binance"
                    if str(pair.get("source") or "").lower() == "binance"
                    else "default",
                    "bars": len(fallback or []),
                    "detail": "bybit_signal_feed_unavailable",
                }
            )
            return CryptoSignalCandles(fallback, meta)

        meta.update(
            {
                "upstream": "bybit_linear_kline",
                "bars": 0,
                "error": True,
                "detail": "bybit_signal_feed_unavailable",
                "paginated": paginated,
                "estimatedRequests": estimated_requests if paginated else 1,
            }
        )
        return CryptoSignalCandles(None, meta)

    candles = default_fetch(pair, tf_u, limit_i)
    meta.update({"upstream": str(pair.get("source") or "default"), "bars": len(candles or [])})
    return CryptoSignalCandles(candles, meta)
