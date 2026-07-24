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

import logging

_logger = logging.getLogger(__name__)

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


# ── Timeframe-policy group taxonomy (policy v4) ──────────────────────────────
# The scoring taxonomy above (ENGINE_A_KNOWN_SCORE_GROUPS) is unchanged: it is
# keyed by config.yaml threshold sections and its coverage is validated at
# boot.  The timeframe-policy layer uses the finer-grained taxonomy below
# (split crosses/exotics, fast/standard majors and indices, merged thin-asset
# groups).  Old policy-group names keep working as deprecated aliases.

ENGINE_A_TIMEFRAME_POLICY_GROUPS = frozenset({
    "forex_majors_standard",
    "forex_majors_fast",
    "forex_crosses_broad",
    "forex_crosses_liquid",
    "forex_exotics_liquid",
    "forex_exotics_restricted",
    "energy_oil",
    "nat_gas",
    "liquid_metals",
    "thin_metals_base_softs",
    "equity_index_fast",
    "equity_index_standard",
    "us_stock_single",
    "bond_tlt_smallcap_em_etf",
    "crypto_majors_fast",
    "crypto_alt_majors",
    "crypto_other_thin",
})

# Deprecated alias → v4 policy group.  Where an old name spans several new
# groups (e.g. scoring "forex_majors" covers standard and fast majors), the
# alias maps to the conservative default and symbol-level policy overrides
# carry the per-instrument distinctions.
TIMEFRAME_POLICY_GROUP_ALIASES = {
    "forex_majors": "forex_majors_standard",
    "forex_crosses": "forex_crosses_broad",
    "forex_exotics": "forex_exotics_liquid",
    "forex_other": "forex_crosses_broad",
    "crypto_btc": "crypto_majors_fast",
    "crypto_eth": "crypto_majors_fast",
    "crypto_doge": "crypto_other_thin",
    "crypto_other": "crypto_other_thin",
    "precious_trackers": "liquid_metals",
    "copper": "thin_metals_base_softs",
    "pgm_metals": "thin_metals_base_softs",
    "base_metals": "thin_metals_base_softs",
    "softs": "thin_metals_base_softs",
    "commodity_other": "thin_metals_base_softs",
    "us_indices_trackers": "equity_index_standard",
    "eu_indices": "equity_index_standard",
    "asian_indices": "equity_index_standard",
    "index_other": "equity_index_standard",
    "bond_tlt": "bond_tlt_smallcap_em_etf",
    "smallcap_em_etf": "bond_tlt_smallcap_em_etf",
    "stock_other": "us_stock_single",
}

_deprecation_logged: set[str] = set()


def normalize_timeframe_policy_group(name: str | None) -> str | None:
    """Map a deprecated policy-group alias to its v4 group (warning once each)."""
    group = str(name or "").strip().lower()
    if not group:
        return None
    mapped = TIMEFRAME_POLICY_GROUP_ALIASES.get(group)
    if mapped and group not in _deprecation_logged:
        _deprecation_logged.add(group)
        _logger.warning(
            "timeframe policy group %r is deprecated; use %r instead",
            group,
            mapped,
        )
    return mapped or group


# New-taxonomy display sets (upper-cased before comparison, same convention as
# the scoring routing tables above).
_TF_MAJORS_STANDARD = {"EUR/USD", "USD/CHF", "AUD/USD", "NZD/USD", "USD/CAD"}
_TF_MAJORS_FAST = {"GBP/USD", "USD/JPY"}
_TF_CROSSES_BROAD = {
    "EUR/GBP", "AUD/NZD", "EUR/CHF", "AUD/CHF", "EUR/AUD", "GBP/AUD", "USD/SGD",
}
_TF_CROSSES_LIQUID = {"EUR/JPY", "AUD/JPY", "GBP/JPY"}
_TF_EXOTICS_LIQUID = {"USD/ZAR", "USD/MXN"}
_TF_EXOTICS_RESTRICTED = {"USD/BRL", "USD/INR"}
_TF_CRYPTO_FAST = {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
_TF_LIQUID_METALS = {"XAU/USD", "XAG/USD", "GLD", "SLV", "GDX"}
_TF_INDEX_FAST = {"NASDAQ-100", "DOW JONES", "DAX", "DAX 40"}
_TF_BOND_SMALLCAP_EM = {"TLT", "IWM", "EEM"}


def resolve_timeframe_policy_group(pair: dict) -> str:
    """Map a pair dict to its v4 timeframe-policy group using asset type + display.

    Pure type-based dispatch for the policy-layer taxonomy.  This is separate
    from ``resolve_score_group_by_type`` (the Engine A scoring taxonomy, which
    is config-keyed and unchanged).  Returns ``"unknown"`` for unrecognised
    asset types.
    """
    display = str(pair.get("display") or pair.get("pair") or pair.get("symbol") or "").strip().upper()
    symbol = str(pair.get("symbol") or "").upper()
    ptype = str(pair.get("type") or pair.get("asset_type") or "").lower()

    if ptype == "forex":
        if display in _TF_MAJORS_STANDARD:
            return "forex_majors_standard"
        if display in _TF_MAJORS_FAST:
            return "forex_majors_fast"
        if display in _TF_CROSSES_LIQUID:
            return "forex_crosses_liquid"
        if display in _TF_CROSSES_BROAD:
            return "forex_crosses_broad"
        if display in _TF_EXOTICS_LIQUID:
            return "forex_exotics_liquid"
        if display in _TF_EXOTICS_RESTRICTED:
            return "forex_exotics_restricted"
        return "forex_crosses_broad"
    if ptype == "crypto":
        if display in _TF_CRYPTO_FAST:
            return "crypto_majors_fast"
        if display in _ALTCOIN_MAJORS:
            return "crypto_alt_majors"
        return "crypto_other_thin"
    if ptype == "commodity":
        if display in _TF_LIQUID_METALS:
            return "liquid_metals"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display == "NAT GAS":
            return "nat_gas"
        return "thin_metals_base_softs"
    if ptype == "index":
        if display in _CURRENCY_INDICES or symbol in _CURRENCY_INDICES:
            return "forex_majors_standard"
        if display in _TF_INDEX_FAST:
            return "equity_index_fast"
        return "equity_index_standard"
    if ptype in {"stock", "etf", "etf_bond"}:
        if ptype == "etf_bond" or display in _TF_BOND_SMALLCAP_EM:
            return "bond_tlt_smallcap_em_etf"
        if display in _TF_LIQUID_METALS:
            return "liquid_metals"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display in _TF_INDEX_FAST:
            return "equity_index_fast"
        if display == "SPY" or display in _US_STOCKS or symbol.endswith(".US"):
            return "us_stock_single"
        if display in _US_INDICES_TRACKERS:
            return "equity_index_standard"
        return "us_stock_single"
    return "unknown"
