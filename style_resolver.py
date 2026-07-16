from __future__ import annotations


VALID_STYLES = frozenset({"scalp", "intraday", "swing"})
_SWING_ONLY_AUTO_GROUPS = frozenset({"forex_exotics", "crypto_other"})


def normalize_style(requested_style: str | None) -> str:
    """Return a supported style token, preserving ``auto`` for resolution."""
    requested = str(requested_style or "auto").strip().lower()
    return requested if requested in VALID_STYLES or requested == "auto" else "auto"


def resolve_auto_style(
    requested_style: str | None,
    pair: dict | None = None,
    *,
    score_group: str | None = None,
    asset_type: str | None = None,
) -> str:
    """Resolve the shared pair-aware style used by every Engine A/B surface.

    Explicit styles are returned unchanged.  Auto keeps the established routing:
    slow/thin FX and long-tail crypto use swing, other FX/crypto use intraday,
    and the remaining asset classes use swing.
    """
    requested = normalize_style(requested_style)
    if requested != "auto":
        return requested

    pair_data = pair or {}
    group = str(
        score_group
        or pair_data.get("scoreGroup")
        or pair_data.get("score_group")
        or ""
    ).strip().lower()
    if group in _SWING_ONLY_AUTO_GROUPS:
        return "swing"

    ptype = str(
        asset_type
        or pair_data.get("type")
        or pair_data.get("asset_type")
        or ""
    ).strip().lower()
    if ptype in ("crypto", "forex"):
        return "intraday"
    return "swing"
