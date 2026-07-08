"""Engine A score-group constants and shared pair→group resolution.

Kept config-independent so boot-time config validation can enforce threshold
coverage without importing scoring.py while config.py is still loading.

The pair lists and ``resolve_score_group_by_type`` are the single canonical
type-based dispatch shared by the live V3 path (``engine_a_v3.routing.route_specialist``)
and the v2 threshold path (``scoring.get_pair_score_group``). Callers that honour
pair-field or config overrides (``pair['score_group']``, ``PAIR_PROFILES``) apply
those before delegating here.
"""
from __future__ import annotations

ENGINE_A_KNOWN_SCORE_GROUPS = frozenset({
    "forex_majors",
    "forex_crosses",
    "forex_exotics",
    "forex_other",
    "crypto_btc",
    "crypto_eth",
    "crypto_doge",
    "crypto_alt_majors",
    "crypto_other",
    "precious_trackers",
    "energy_oil",
    "nat_gas",
    "copper",
    "pgm_metals",
    "base_metals",
    "softs",
    "commodity_other",
    "us_indices_trackers",
    "eu_indices",
    "asian_indices",
    "index_other",
    "us_stock_single",
    "bond_tlt",
    "smallcap_em_etf",
    "stock_other",
    "unknown",
})

# ── Pair → score-group routing tables ─────────────────────────────────────────
# Display values are upper-cased before comparison so the dispatch is
# case-insensitive across both callers.
_MAJOR_FOREX = {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD", "USD/CAD", "USD/CHF"}
_FOREX_CROSSES = {
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "GBP/AUD",
    "EUR/CHF", "USD/SGD", "AUD/CHF", "AUD/NZD",
}
_FOREX_EXOTICS = {"USD/ZAR", "USD/MXN", "USD/BRL", "USD/INR"}
_ALTCOIN_MAJORS = {
    "SOL/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "POL/USDT", "BNB/USDT",
    "DOT/USDT", "LTC/USDT", "SUI/USDT", "NEAR/USDT", "APT/USDT", "INJ/USDT",
    "RENDER/USDT",
    # 2026-06-24: promoted from crypto_other — liquid alts that previously scored
    # against the stricter crypto_other 2.2 bar despite high liquidity. XRP is an
    # ENGINE_B_HOTLIST_CRYPTO_ANCHORS member. Effect: Engine A threshold 2.2->2.0,
    # min_directional 0.24->0.20, RSI bounds 75/25->80/20. Reversible (remove names).
    "XRP/USDT", "ARB/USDT", "OP/USDT", "UNI/USDT",
}
_PRECIOUS_TRACKERS = {"XAU/USD", "XAG/USD", "GLD", "SLV", "GDX"}
_ENERGY_OIL = {"WTI OIL", "BRENT OIL", "USO", "XLE"}
_BASE_METALS = {"ALUMINIUM", "LEAD", "NICKEL", "ZINC"}
_SOFTS = {"CATTLE", "COCOA", "COFFEE", "CORN", "COTTON", "SOYBEANS", "SUGAR", "WHEAT"}
_US_INDICES_TRACKERS = {"NASDAQ-100", "S&P 500", "DOW JONES", "SPY", "QQQ", "DIA", "SOXX"}
_EU_INDICES = {"DAX", "DAX 40", "UK100", "FTSE 100"}
_ASIAN_INDICES = {"ASX 200", "NIKKEI 225", "HANG SENG"}
_CURRENCY_INDICES = {"USDX", "EURX", "JPYX"}
_US_STOCKS = {
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG", "GOOGL", "AVGO",
    "JPM", "V", "XOM", "NFLX", "AMD", "CRM", "DIS", "BA", "COIN", "PYPL",
    "INTC", "UBER", "PLTR",
}


def resolve_score_group_by_type(pair: dict) -> str:
    """Map a pair dict to its ENGINE_A score_group using asset type + display.

    Pure type-based dispatch — no pair-field or config override handling.
    Returns the group string; for unrecognised asset types returns ``"unknown"``.
    Both ``display`` and ``symbol`` are upper-cased before comparison.
    """
    display = str(pair.get("display") or pair.get("pair") or pair.get("symbol") or "").strip().upper()
    symbol = str(pair.get("symbol") or "").upper()
    ptype = str(pair.get("type") or pair.get("asset_type") or "").lower()

    if ptype == "forex":
        if display in _MAJOR_FOREX:
            return "forex_majors"
        if display in _FOREX_CROSSES:
            return "forex_crosses"
        if display in _FOREX_EXOTICS:
            return "forex_exotics"
        return "forex_other"
    if ptype == "crypto":
        if display == "BTC/USDT":
            return "crypto_btc"
        if display == "ETH/USDT":
            return "crypto_eth"
        if display == "DOGE/USDT":
            return "crypto_doge"
        if display in _ALTCOIN_MAJORS:
            return "crypto_alt_majors"
        return "crypto_other"
    if ptype == "commodity":
        if display in _PRECIOUS_TRACKERS:
            return "precious_trackers"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display == "NAT GAS":
            return "nat_gas"
        if display == "COPPER":
            return "copper"
        if display in {"XPT/USD", "XPD/USD"}:
            return "pgm_metals"
        if display in _BASE_METALS:
            return "base_metals"
        if display in _SOFTS:
            return "softs"
        return "commodity_other"
    if ptype == "index":
        if display in _CURRENCY_INDICES or symbol in _CURRENCY_INDICES:
            return "forex_majors"
        if display in _US_INDICES_TRACKERS:
            return "us_indices_trackers"
        if display in _EU_INDICES:
            return "eu_indices"
        if display in _ASIAN_INDICES:
            return "asian_indices"
        return "index_other"
    if ptype in {"stock", "etf", "etf_bond"}:
        if display == "TLT" or ptype == "etf_bond":
            return "bond_tlt"
        if display in {"IWM", "EEM"}:
            return "smallcap_em_etf"
        if display in _PRECIOUS_TRACKERS:
            return "precious_trackers"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display in _US_INDICES_TRACKERS:
            return "us_indices_trackers"
        if display in _US_STOCKS or symbol.endswith(".US"):
            return "us_stock_single"
        return "stock_other"
    fallback = f"{ptype}_other" if ptype else "unknown"
    return fallback if fallback in ENGINE_A_KNOWN_SCORE_GROUPS else "unknown"
