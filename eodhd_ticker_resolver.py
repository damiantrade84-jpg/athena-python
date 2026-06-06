"""Shared EODHD ticker resolution for news and sentiment paths."""

from __future__ import annotations

from typing import Any

from eodhd_volume_overlay import eodhd_commodity_ticker_for_pair

_LEGACY_COMMODITY_SYMBOL_MAP: dict[str, str] = {
    "GC=F": "GC.COMEX",
    "SI=F": "SI.COMEX",
    "CL=F": "CL.COMEX",
    "BZ=F": "BZ.COMEX",
    "NG.US": "NG.COMEX",
    "PL=F": "PL.COMEX",
    "PA=F": "PA.COMEX",
    "HG=F": "HG.COMEX",
}


def eodhd_ticker_for_pair(pair: dict[str, Any] | None) -> str | None:
    """Resolve an internal pair dict to an EODHD news/sentiment ticker."""
    if not isinstance(pair, dict):
        return None

    disp = str(pair.get("display") or "").strip()
    ptype = str(pair.get("type") or "").strip().lower()
    sym = str(pair.get("symbol") or "").strip()
    commodity_ticker = eodhd_commodity_ticker_for_pair(pair)

    if ptype == "commodity" and commodity_ticker:
        return commodity_ticker

    if ptype == "crypto":
        base = disp.split("/")[0] if "/" in disp else disp.replace("USDT", "")
        return f"{base}-USD.CC" if base else None

    if ptype == "forex":
        clean = disp.replace("=X", "").replace("/", "")
        return f"{clean}.FOREX" if clean else None

    if ptype == "commodity":
        if "/" in disp:
            return disp.replace("/", "") + ".FOREX"
        if sym.endswith((".FOREX", ".COMM")):
            return sym
        if commodity_ticker:
            return commodity_ticker
        return _LEGACY_COMMODITY_SYMBOL_MAP.get(sym) or _LEGACY_COMMODITY_SYMBOL_MAP.get(disp)

    if ptype in ("stock", "etf", "etf_bond"):
        clean = (sym or disp).replace(".US", "")
        return f"{clean}.US" if clean else None

    if ptype == "index":
        if sym and ".INDX" in sym:
            return sym
        base = sym.lstrip("^") if sym else disp.replace(" ", "")
        return f"{base}.INDX" if base else None

    return sym or None


def eodhd_ticker_from_signal(
    pair: str,
    asset_type: str,
    *,
    symbol: str | None = None,
) -> str | None:
    """Build a pair dict from auto-trade / gate string inputs."""
    display = str(pair or "").strip()
    if not display:
        return None
    return eodhd_ticker_for_pair(
        {
            "display": display,
            "symbol": str(symbol or display).strip(),
            "type": str(asset_type or "").strip().lower(),
        }
    )
