from __future__ import annotations


VALID_STYLES = frozenset({"scalp", "intraday", "swing"})
_SWING_ONLY_AUTO_GROUPS = frozenset({"forex_exotics", "crypto_other"})

# 2026-07-28 ATFX additions.  Session-bound symbols that need intraday ladders
# even when their parent score group historically defaulted to swing.
#
# Keyed per symbol, not per score group: every group they sit in also holds
# pre-existing pairs (forex_exotics has USD/ZAR, commodity_other has Gasoline)
# whose swing behaviour must not change.
#
# Deliberately excluded: EUR/ZAR, GBP/ZAR and USD/HKD.  Restricted/pegged
# instruments stay on swing (stricter min_score/min_rr).
_INTRADAY_AUTO_SYMBOLS = frozenset({
    "EURHUF", "EURPLN", "USDCZK", "USDHUF", "USDPLN",   # CEE crosses -> M30
    "XAUZAR",                                            # cross metal  -> M30
    "CHI50",                                             # China A50    -> M30
    "ESP35", "FRA40", "IT40",                            # EU indices   -> M15
    "XAUUSD", "XAGUSD",                                  # liquid metals session
})
_INTRADAY_AUTO_DISPLAYS = frozenset({
    "CHINAA50", "SPAIN35", "FRANCE40", "ITALY40",
    "XAU/USD", "XAG/USD", "GLD", "SLV",
})

# Score groups that opt out of the swing asset-class default wholesale.
# Session equities/indices use H1 structure under the policy matrix; auto must
# select intraday so those rows are not dead code. Bonds/EM ETFs stay swing.
# stock_other: ATFX share CFDs — same session constraint.
_INTRADAY_AUTO_GROUPS = frozenset({
    "stock_other",
    "us_stock_single",
    "us_indices_trackers",
    "eu_indices",
    "asian_indices",
    "index_other",
    "precious_trackers",
})

def _symbol_key(value: object) -> str:
    """Normalize a display or broker symbol to a comparable key.

    Mirrors timeframe_policy._key (uppercase, drop the =X / .US / .s vendor
    suffixes, keep alphanumerics) without importing it, so this module stays
    dependency-free for the many surfaces that import it.
    """
    text = str(value or "").strip().upper()
    text = text.replace("=X", "").replace(".US", "")
    if text.endswith(".S"):
        text = text[:-2]
    return "".join(ch for ch in text if ch.isalnum())


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
    for candidate in (
        pair_data.get("display"),
        pair_data.get("pair"),
        pair_data.get("symbol"),
    ):
        key = _symbol_key(candidate)
        if key and (key in _INTRADAY_AUTO_SYMBOLS or key in _INTRADAY_AUTO_DISPLAYS):
            return "intraday"

    if group in _SWING_ONLY_AUTO_GROUPS:
        return "swing"
    if group in _INTRADAY_AUTO_GROUPS:
        return "intraday"

    ptype = str(
        asset_type
        or pair_data.get("type")
        or pair_data.get("asset_type")
        or ""
    ).strip().lower()
    if ptype in ("crypto", "forex"):
        return "intraday"
    # Session-bound asset classes: use the intraday role ladder (H1 structure
    # for indices/stocks) rather than the swing D1 overlay under auto.
    if ptype == "index":
        return "intraday"
    if ptype == "stock" and group not in ("bond_tlt", "smallcap_em_etf"):
        return "intraday"
    if ptype == "commodity" and group == "precious_trackers":
        return "intraday"
    return "swing"
