from __future__ import annotations


def resolve_auto_style(requested_style: str, pair: dict | None) -> str:
    requested = str(requested_style or "auto").lower()
    if requested != "auto":
        return requested

    ptype = str((pair or {}).get("type") or "").lower()
    if ptype in ("crypto", "forex"):
        return "intraday"
    return "swing"
