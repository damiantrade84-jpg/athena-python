"""Pure helpers for crypto WebSocket / microstructure subscription scope."""

from __future__ import annotations

from typing import Any, Optional


def enabled_crypto_micro_pairs(
    crypto_pairs: Optional[list[dict[str, Any]]],
    *,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Enabled crypto pairs with a non-empty futures symbol.

    When ``source`` is supplied, restrict to pairs whose configured source matches
    it. Passing ``None`` intentionally returns all enabled crypto symbols, used
    for venue-specific microstructure feeds such as Bybit publicTrade.
    """
    if not crypto_pairs:
        return []
    source_key = str(source or "").strip().lower()
    by_upper_sym: dict[str, dict[str, Any]] = {}
    for p in crypto_pairs:
        if not isinstance(p, dict):
            continue
        if not p.get("enabled", True):
            continue
        if str(p.get("type") or "").lower() != "crypto":
            continue
        if source_key and str(p.get("source") or "").lower() != source_key:
            continue
        raw = str(p.get("symbol") or "").strip()
        sym_u = raw.replace("/", "").upper()
        if not sym_u:
            continue
        by_upper_sym[sym_u] = p
    return sorted(by_upper_sym.values(), key=lambda d: str(d.get("symbol") or "").replace("/", "").upper())


def enabled_binance_micro_crypto_pairs(crypto_pairs: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Pairs that receive Binance futures micro feeds (aggTrade buckets, depth, trades)."""
    return enabled_crypto_micro_pairs(crypto_pairs, source="binance")


def binance_micro_symbol_strings(crypto_pairs: Optional[list[dict[str, Any]]]) -> list[str]:
    """Uppercase perpetual symbols e.g. XRPUSDT for merging kline/live WS scope."""
    return [
        str(p.get("symbol") or "").replace("/", "").strip().upper()
        for p in enabled_binance_micro_crypto_pairs(crypto_pairs)
    ]


def should_start_binance_micro_feeds(
    config: dict[str, Any] | None,
    *,
    has_binance_pairs: bool,
) -> bool:
    """Return whether Binance microstructure sockets should start.

    Bybit is the default trade-bucket venue. Binance microstructure is only
    started when Binance is primary or an explicit parallel-feed override is set.
    """
    if not has_binance_pairs:
        return False
    cfg = config if isinstance(config, dict) else {}
    scalp_cfg = cfg.get("SCALP_ENGINE") if isinstance(cfg.get("SCALP_ENGINE"), dict) else {}
    primary = str(
        scalp_cfg.get("TRADE_BUCKET_EXCHANGE")
        or cfg.get("ENGINE_B_CRYPTO_LEVELS_FEED")
        or "bybit"
    ).strip().lower()
    if primary not in {"bybit", "binance"}:
        primary = "bybit"
    if primary == "binance":
        return True
    return bool(cfg.get("MICROSTRUCTURE_BINANCE_FEEDS_ENABLED", False))
