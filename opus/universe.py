"""Derive the OPUS universe from Athena's active portfolio.

OPUS still owns its own scoring, gates and thresholds; what this module does is
stop it owning its own *instrument list*. Keeping a second hand-maintained list
in opus.yaml meant that enabling or disabling a pair in the portfolio had no
effect on OPUS, and the two lists silently drifted apart.

The portfolio is injected, never imported: `set_portfolio_provider` is called by
the host at wiring time (see athena.py, where OPUS is mounted). With no provider
installed - tests, a bare `python -c` import, a tool script - `portfolio_specs`
returns nothing and `config.universe()` falls back to the static UNIVERSE list.
That keeps OPUS importable with no runtime attached and preserves the shipped
behaviour rather than failing closed to an empty universe.

Mapping a portfolio pair to an OPUS spec needs three things:

  symbol  the BROKER symbol, not Athena's catalog id. `pair["symbol"]` is a
          Yahoo/EODHD id ("GC=F", "EURUSD=X", "AAPL.US") and is useless to a
          feed. MT5 symbols come from the injected resolver; crypto symbols are
          the display with the separator removed.
  venue   which feed adapter reads it. A pair whose source has no OPUS adapter
          (eodhd, yfinance) is SKIPPED with a reason rather than admitted and
          left to raise on every scan.
  class   selects the timeframe ladder, cost model and calibration bucket. Metals
          are split out of `commodity` by symbol because their cost profile is
          nothing like energy or grains.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from opus import config

_lock = threading.RLock()
_pair_provider: Callable[[], list[dict]] | None = None
_symbol_resolver: Callable[[str], str | None] | None = None


def set_portfolio_provider(
    pair_provider: Callable[[], list[dict]] | None,
    *,
    symbol_resolver: Callable[[str], str | None] | None = None,
) -> None:
    """Install the host's active-pair provider and broker symbol resolver."""
    global _pair_provider, _symbol_resolver, _cache, _cache_stamp, _cache_signature
    with _lock:
        _pair_provider = pair_provider
        _symbol_resolver = symbol_resolver
        _cache = _cache_signature = None
        _cache_stamp = 0.0


def has_portfolio_provider() -> bool:
    with _lock:
        return _pair_provider is not None


def _mapping() -> dict:
    return dict(config.load().get("UNIVERSE_MAPPING") or {})


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _key(value: Any) -> str:
    """Comparison key: case- and separator-insensitive."""
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _resolve_symbol(venue: str, display: str) -> str | None:
    if venue == "mt5":
        resolver = _symbol_resolver
        if resolver is not None:
            try:
                resolved = _norm(resolver(display))
            except Exception:  # noqa: BLE001 - a resolver fault must not kill the scan
                resolved = ""
            if resolved:
                return resolved
        # No resolver: the plain stripped display is the best available guess.
        # MT5Feed.resolve_symbol still prefix-matches broker suffixes on top.
    stripped = display.replace("/", "").replace(" ", "").replace("-", "")
    return stripped.upper() or None


def _asset_class(pair: dict, mapping: dict) -> str:
    display = _norm(pair.get("display"))
    asset_type = _norm(pair.get("type")).lower()
    metals = {_key(s) for s in (mapping.get("metals_symbols") or [])}
    if _key(display) in metals:
        return "metals"
    by_type = mapping.get("class_by_type") or {}
    return _norm(by_type.get(asset_type)) or "default"


def build_specs(
    pairs: list[dict] | None,
) -> tuple[list[dict], list[dict]]:
    """Map portfolio pairs to OPUS universe specs.

    Returns (specs, skipped). `skipped` carries a reason per dropped pair so the
    status endpoint can show WHY a portfolio pair is absent from OPUS instead of
    the pair just vanishing.
    """
    mapping = _mapping()
    venue_by_source = {
        _norm(k).lower(): _norm(v).lower()
        for k, v in (mapping.get("venue_by_source") or {}).items()
    }
    overrides = {
        _key(k): dict(v or {})
        for k, v in (mapping.get("overrides") or {}).items()
    }
    excluded = {_key(s) for s in (mapping.get("exclude") or [])}

    specs: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        display = _norm(pair.get("display"))
        if not display:
            continue
        # The portfolio's own enabled flag is authoritative. A pair disabled in
        # the UI or by the MT5 availability pass must not be scanned here.
        if not pair.get("enabled", True):
            continue
        if _key(display) in excluded:
            skipped.append({"display": display, "reason": "excluded by UNIVERSE_MAPPING.exclude"})
            continue

        override = overrides.get(_key(display), {})
        source = _norm(pair.get("source")).lower()
        venue = _norm(override.get("venue")).lower() or venue_by_source.get(source, "")
        if not venue:
            skipped.append({
                "display": display,
                "reason": f"no OPUS feed adapter for source '{source or 'unknown'}'",
            })
            continue

        symbol = _norm(override.get("symbol")) or _resolve_symbol(venue, display)
        if not symbol:
            skipped.append({"display": display, "reason": "could not resolve a broker symbol"})
            continue

        asset_class = _norm(override.get("class")) or _asset_class(pair, mapping)

        dedupe = f"{venue}:{symbol.upper()}"
        if dedupe in seen:
            skipped.append({"display": display, "reason": f"duplicate of {dedupe}"})
            continue
        seen.add(dedupe)

        specs.append({
            "symbol": symbol,
            "display": display,
            "venue": venue,
            "class": asset_class,
            "assetType": _norm(pair.get("type")).lower() or "unknown",
        })

    specs.sort(key=lambda s: (s["class"], s["symbol"]))
    return specs, skipped


# universe() is called on every scan, every status poll and once per candidate
# in the correlation-cap check, so the mapping is memoised for a couple of
# seconds. The TTL is deliberately short: a pair toggled off in the UI must
# stop being scanned promptly, not on the next restart.
_CACHE_TTL_SEC = 2.0
_cache: tuple[list[dict], list[dict]] | None = None
_cache_stamp: float = 0.0
_cache_signature: tuple | None = None


def invalidate() -> None:
    """Drop the memoised mapping (used by tests and by provider swaps)."""
    global _cache, _cache_stamp, _cache_signature
    with _lock:
        _cache, _cache_stamp, _cache_signature = None, 0.0, None


def portfolio_specs() -> tuple[list[dict], list[dict]]:
    """(specs, skipped) from the installed provider; ([], []) when absent."""
    global _cache, _cache_stamp, _cache_signature
    with _lock:
        provider = _pair_provider
    if provider is None:
        return [], []
    try:
        pairs = list(provider() or [])
    except Exception:  # noqa: BLE001 - a provider fault falls back to static config
        return [], [{"display": "*", "reason": "portfolio provider raised"}]

    signature = tuple(
        (str(p.get("display")), bool(p.get("enabled", True)))
        for p in pairs if isinstance(p, dict)
    )
    now = time.monotonic()
    with _lock:
        if (
            _cache is not None
            and _cache_signature == signature
            and (now - _cache_stamp) < _CACHE_TTL_SEC
        ):
            return _cache

    built = build_specs(pairs)
    with _lock:
        _cache, _cache_stamp, _cache_signature = built, now, signature
    return built


__all__ = [
    "set_portfolio_provider",
    "has_portfolio_provider",
    "build_specs",
    "invalidate",
    "portfolio_specs",
]
