"""Commodity catalog vs execution identity (futures proxy ≠ spot CFD).

Athena historically stores Yahoo continuous-futures tickers (SI=F, GC=F, CL=F)
on commodity pair ``symbol`` fields while live pricing, levels, and MT5
execution use broker spot CFDs (XAGUSD, XAUUSD, SpotCrude).

Those series correlate but are not the same instrument: contango premium,
session calendar, and feed semantics differ. This module keeps that boundary
explicit so chart/score fallbacks cannot silently price futures as spot.
"""

from __future__ import annotations

from typing import Any, Callable


def is_continuous_futures_ticker(symbol: str | None) -> bool:
    """Yahoo continuous futures tickers end with =F (GC=F, SI=F, CL=F, …)."""
    return str(symbol or "").strip().upper().endswith("=F")


def resolve_yfinance_pricing_symbol(
    pair: dict[str, Any] | None,
    *,
    vendor_yfinance: str | None = None,
) -> str | None:
    """Return a yfinance ticker for *pricing* history only.

    Continuous futures are never returned for MT5-primary commodity CFDs unless
    the pair explicitly opts in via ``yfinanceSymbol`` / vendor ``yfinance``.
    Empty-string overrides block yfinance entirely.
    """
    pair = pair if isinstance(pair, dict) else {}
    if "yfinanceSymbol" in pair:
        override = pair.get("yfinanceSymbol")
    else:
        override = vendor_yfinance

    if override is not None and str(override).strip() == "":
        return None
    if override:
        return str(override)

    sym = pair.get("symbol")
    if (
        pair.get("type") in {"stock", "etf", "etf_bond"}
        and isinstance(sym, str)
        and sym.endswith(".US")
    ):
        return sym[:-3]

    # MT5 commodity CFDs: catalog may still say SI=F/GC=F/CL=F for legacy
    # aliases, but those are continuous futures — not the live price series.
    if (
        str(pair.get("source") or "").lower() == "mt5"
        and str(pair.get("type") or "").lower() == "commodity"
        and is_continuous_futures_ticker(sym)
    ):
        return None

    return sym if isinstance(sym, str) and sym else None


def commodity_instrument_identity(
    pair: dict[str, Any] | None,
    *,
    mt5_map_symbol: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Provenance for commodity catalog vs live execution price series."""
    pair = pair if isinstance(pair, dict) else {}
    display = str(pair.get("display") or pair.get("symbol") or "").strip()
    catalog = str(pair.get("symbol") or "").strip()
    futures_proxy = str(
        pair.get("futures_proxy")
        or pair.get("futuresProxy")
        or (catalog if is_continuous_futures_ticker(catalog) else "")
        or ""
    ).strip() or None
    kind = str(
        pair.get("price_series_kind")
        or pair.get("priceSeriesKind")
        or ("spot_cfd" if str(pair.get("source") or "").lower() == "mt5" else "")
        or "unknown"
    ).strip()
    execution = None
    if mt5_map_symbol is not None and display:
        try:
            execution = mt5_map_symbol(display)
        except Exception:
            execution = None
    elif display:
        try:
            from mt5_executor import mt5_map_symbol as _map

            execution = _map(display)
        except Exception:
            execution = None

    note = None
    if (
        futures_proxy
        and execution
        and futures_proxy.upper() != str(execution).upper()
    ):
        note = (
            f"{futures_proxy} is continuous futures proxy (COT/research only); "
            f"live OHLC/levels use {execution}"
        )
    return {
        "display": display or None,
        "catalog_symbol": catalog or None,
        "execution_symbol": execution,
        "futures_proxy": futures_proxy,
        "price_series_kind": kind or "unknown",
        "catalog_is_continuous_futures": is_continuous_futures_ticker(catalog),
        "note": note,
    }
