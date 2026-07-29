from __future__ import annotations


VALID_STYLES = frozenset({"scalp", "intraday", "swing"})
_SWING_ONLY_AUTO_GROUPS = frozenset({"forex_exotics", "crypto_other"})

# 2026-07-28 ATFX additions.  Auto sends every commodity, index and stock to
# swing, and swing is a deliberate full slow overlay (timeframe_policy
# _engine_template) that replaces the instrument's group ladder with a uniform
# D1/D1/H4/H1/H1.  These instruments were added with intraday-speed ladders, so
# they opt out of the asset-class default.
#
# Keyed per symbol, not per score group: every group they sit in also holds
# pre-existing pairs (forex_exotics has USD/ZAR, commodity_other has Gasoline,
# eu_indices has DAX 40 and UK100, asian_indices has ASX 200 / Hang Seng /
# Nikkei 225) whose swing behaviour must not change.
#
# Deliberately excluded: EUR/ZAR, GBP/ZAR and USD/HKD.  Their symbol overrides
# pin an H1 trigger under both styles, so intraday would buy no extra
# resolution while dropping Engine B's stricter swing min_score/min_rr — the
# wrong side to err on for restricted and pegged instruments.
_INTRADAY_AUTO_SYMBOLS = frozenset({
    "EURHUF", "EURPLN", "USDCZK", "USDHUF", "USDPLN",   # CEE crosses -> M30
    "XAUZAR",                                            # cross metal  -> M30
    "CHI50",                                             # China A50    -> M30
    "ESP35", "FRA40", "IT40",                            # EU indices   -> M15
})
_INTRADAY_AUTO_DISPLAYS = frozenset({
    "CHINAA50", "SPAIN35", "FRANCE40", "ITALY40",
})

# Score groups that opt out of the swing asset-class default wholesale.
#
# stock_other is the ATFX share CFDs (219 active) plus the 14 JSE pairs, and
# every JSE pair is enabled=False in athena.py — so no live instrument outside
# the ATFX set is affected. Their policy group (cash_equity_standard_dynamic)
# is a D1/D1/H1/M30/M15 intraday ladder with M5 disabled; leaving them on the
# swing overlay flattened all 219 to a uniform D1/D1/H4/H1/H1 and made that
# template inert. Re-enabling a JSE pair would move it onto this ladder too.
_INTRADAY_AUTO_GROUPS = frozenset({"stock_other"})


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
    return "swing"
