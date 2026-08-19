"""Athena pair book → KIMI scan-universe names.

The picker and ``set_symbols()`` share one canonicalizer so broker suffixes
(``.s``), share prefixes (``#``), and catalog ids (``AAPL.US``, ``GC=F``)
never leak into the scan list — and so ATFX-mapped names are not dropped.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

# Shared with Engine.set_symbols. 2 chars covers BA/GM/WTI; 1-char tickers
# (C, V) stay out — they collide with too many broker prefixes.
SYMBOL_RE = re.compile(r"[A-Z0-9]{2,20}")
_COUNTRY_SUFFIX = {"US", "UK", "DE", "HK", "PA"}


def canonicalize_kimi_symbol(raw: str | None) -> str | None:
    """Turn a display, catalog id, or broker symbol into a KIMI name.

    ``EURUSD.s`` → ``EURUSD``, ``#AAPL`` → ``AAPL``, ``AAPL.US`` → ``AAPL``,
    ``BTC/USDT`` → ``BTCUSDT``. Returns None when the result is not a legal
    scan symbol.
    """
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s:
        return None
    if s.startswith("#"):
        s = s[1:]
    s = s.replace("/", "").replace(" ", "")
    if s.endswith(".S"):
        s = s[:-2]
    if "." in s:
        base, suf = s.rsplit(".", 1)
        if suf in _COUNTRY_SUFFIX and base.isalnum():
            s = base
    if s.endswith("=X") or s.endswith("=F"):
        s = s[:-2]
        # Yahoo roots like GC/SI are not feed symbols; EURUSD=X still is.
        if len(s) <= 2:
            return None
    if SYMBOL_RE.fullmatch(s):
        return s
    return None


def pair_to_kimi_symbol(
    pair: dict[str, Any],
    map_fn: Callable[[str], str | None] | None = None,
) -> str | None:
    """Map one Athena pair dict to a KIMI universe symbol, or None."""
    if not isinstance(pair, dict) or not pair.get("enabled", True):
        return None
    display = str(pair.get("display") or "").strip()
    symbol = str(pair.get("symbol") or "").strip()
    raws: list[str] = []
    if map_fn is not None and display:
        try:
            mapped = map_fn(display)
        except Exception:  # noqa: BLE001 — mapper is host-owned; skip on error
            mapped = None
        if mapped:
            raws.append(str(mapped))
    if display:
        raws.append(display.replace("-", ""))
    if symbol:
        raws.append(symbol.replace("-", ""))
    for raw in raws:
        hit = canonicalize_kimi_symbol(raw)
        if hit:
            return hit
    return None


def portfolio_symbols(
    pairs: Iterable[dict[str, Any]] | None,
    map_fn: Callable[[str], str | None] | None = None,
) -> list[str]:
    """Enabled Athena pairs, in KIMI names, de-duplicated, original order.

    This is the 'ALL ACTIVE' book — not the exchange catalog. Shares and
    FX/CFDs are included when they canonicalize to a legal scan symbol;
    catalog ids that cannot (``GC=F`` without a display, hyphenated ETF
    broker codes with no short display) are skipped.
    """
    out: list[str] = []
    seen: set[str] = set()
    for pair in pairs or []:
        sym = pair_to_kimi_symbol(pair, map_fn)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out
