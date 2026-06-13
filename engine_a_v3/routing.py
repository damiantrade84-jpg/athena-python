from __future__ import annotations

from dataclasses import dataclass


KNOWN_SCORE_GROUPS = frozenset(
    {
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
    }
)

_MAJOR_FOREX = {
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "NZD/USD",
    "USD/CAD",
    "USD/CHF",
}
_FOREX_CROSSES = {
    "EUR/GBP",
    "EUR/JPY",
    "GBP/JPY",
    "AUD/JPY",
    "EUR/AUD",
    "GBP/AUD",
    "EUR/CHF",
    "USD/SGD",
    "AUD/CHF",
    "AUD/NZD",
}
_FOREX_EXOTICS = {"USD/ZAR", "USD/MXN", "USD/BRL", "USD/INR"}
_ALT_MAJORS = {
    "SOL/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "POL/USDT",
    "BNB/USDT",
    "DOT/USDT",
    "LTC/USDT",
    "SUI/USDT",
    "NEAR/USDT",
    "APT/USDT",
    "INJ/USDT",
    "RENDER/USDT",
}
_PRECIOUS = {"XAU/USD", "XAG/USD", "GLD", "SLV", "GDX"}
_ENERGY = {"WTI OIL", "BRENT OIL", "USO", "XLE"}
_BASE_METALS = {"ALUMINIUM", "LEAD", "NICKEL", "ZINC"}
_SOFTS = {"CATTLE", "COCOA", "COFFEE", "CORN", "COTTON", "SOYBEANS", "SUGAR", "WHEAT"}
_US_INDICES = {"NASDAQ-100", "S&P 500", "DOW JONES", "SPY", "QQQ", "DIA", "SOXX"}
_EU_INDICES = {"DAX", "DAX 40", "UK100", "FTSE 100"}
_ASIAN_INDICES = {"ASX 200", "NIKKEI 225", "HANG SENG"}
_US_STOCKS = {
    "AAPL",
    "TSLA",
    "NVDA",
    "MSFT",
    "AMZN",
    "META",
    "GOOG",
    "GOOGL",
    "AVGO",
    "JPM",
    "V",
    "XOM",
    "NFLX",
    "AMD",
    "CRM",
    "DIS",
    "BA",
    "COIN",
    "PYPL",
    "INTC",
    "UBER",
    "PLTR",
}


@dataclass(frozen=True)
class SpecialistRoute:
    score_group: str
    family: str
    subclass: str

    def setup_ids(self, horizon: str) -> tuple[str, ...]:
        h = str(horizon).lower()
        if self.family == "forex":
            return (
                ("fx_session_breakout_retest", "fx_trend_pullback")
                if h == "intraday"
                else ("fx_trend_pullback_continuation",)
            )
        if self.family == "crypto":
            return (
                ("crypto_contraction_breakout_retest", "crypto_trend_pullback")
                if h == "intraday"
                else ("crypto_trend_pullback_continuation",)
            )
        if self.subclass in {"xau", "precious"}:
            return (
                ("precious_london_ny_breakout_retest",)
                if h == "intraday"
                else ("precious_h4_d1_continuation",)
            )
        if self.family == "commodity":
            return (
                (f"{self.subclass}_breakout_retest",)
                if h == "intraday"
                else (f"{self.subclass}_trend_pullback",)
            )
        if self.family in {"index", "equity_etf"}:
            return (
                (f"{self.subclass}_opening_range_gap_continuation",)
                if h == "intraday"
                else (f"{self.subclass}_relative_strength_breakout_pullback",)
            )
        return ("unsupported_specialist",)


def _explicit_group(pair: dict) -> str | None:
    value = pair.get("score_group") or pair.get("scoreGroup")
    value = str(value or "").strip()
    return value if value in KNOWN_SCORE_GROUPS else None


def route_specialist(pair: dict) -> SpecialistRoute:
    display = str(pair.get("display") or pair.get("pair") or pair.get("symbol") or "").strip()
    upper = display.upper()
    symbol = str(pair.get("symbol") or "").upper()
    ptype = str(pair.get("type") or pair.get("asset_type") or "").lower()
    group = _explicit_group(pair)

    if group is None and ptype == "forex":
        group = (
            "forex_majors"
            if display in _MAJOR_FOREX
            else "forex_crosses"
            if display in _FOREX_CROSSES
            else "forex_exotics"
            if display in _FOREX_EXOTICS
            else "forex_other"
        )
    elif group is None and ptype == "crypto":
        group = (
            "crypto_btc"
            if upper == "BTC/USDT"
            else "crypto_eth"
            if upper == "ETH/USDT"
            else "crypto_doge"
            if upper == "DOGE/USDT"
            else "crypto_alt_majors"
            if upper in _ALT_MAJORS
            else "crypto_other"
        )
    elif group is None and ptype == "commodity":
        group = (
            "precious_trackers"
            if upper in _PRECIOUS
            else "energy_oil"
            if upper in _ENERGY
            else "nat_gas"
            if upper == "NAT GAS"
            else "copper"
            if upper == "COPPER"
            else "pgm_metals"
            if upper in {"XPT/USD", "XPD/USD"}
            else "base_metals"
            if upper in _BASE_METALS
            else "softs"
            if upper in _SOFTS
            else "commodity_other"
        )
    elif group is None and ptype == "index":
        group = (
            "us_indices_trackers"
            if upper in _US_INDICES
            else "eu_indices"
            if upper in _EU_INDICES
            else "asian_indices"
            if upper in _ASIAN_INDICES
            else "index_other"
        )
    elif group is None and ptype in {"stock", "etf", "etf_bond"}:
        group = (
            "bond_tlt"
            if upper == "TLT" or ptype == "etf_bond"
            else "smallcap_em_etf"
            if upper in {"IWM", "EEM"}
            else "precious_trackers"
            if upper in _PRECIOUS
            else "energy_oil"
            if upper in _ENERGY
            else "us_indices_trackers"
            if upper in _US_INDICES
            else "us_stock_single"
            if upper in _US_STOCKS or symbol.endswith(".US")
            else "stock_other"
        )
    group = group or "unknown"

    if group.startswith("forex_"):
        return SpecialistRoute(group, "forex", group.removeprefix("forex_"))
    if group.startswith("crypto_"):
        return SpecialistRoute(group, "crypto", group.removeprefix("crypto_"))
    if group in {
        "precious_trackers",
        "energy_oil",
        "nat_gas",
        "copper",
        "pgm_metals",
        "base_metals",
        "softs",
        "commodity_other",
    }:
        subclass = "xau" if upper == "XAU/USD" else group.removesuffix("_trackers")
        return SpecialistRoute(group, "commodity", subclass)
    if group in {"us_indices_trackers", "eu_indices", "asian_indices", "index_other"}:
        return SpecialistRoute(group, "index", group.removesuffix("_trackers"))
    if group in {"us_stock_single", "bond_tlt", "smallcap_em_etf", "stock_other"}:
        subclass = "jse_equity" if symbol.endswith(".JO") else group
        return SpecialistRoute(group, "equity_etf", subclass)
    return SpecialistRoute("unknown", "unknown", "unknown")
