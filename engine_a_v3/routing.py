from __future__ import annotations

from dataclasses import dataclass

from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS, resolve_score_group_by_type

# Re-exported for every caller that still imports the canonical group set from
# routing (engine_a_v3.audit, profile, workflow, evaluator, tests, etc.).
KNOWN_SCORE_GROUPS = ENGINE_A_KNOWN_SCORE_GROUPS


@dataclass(frozen=True)
class SpecialistRoute:
    score_group: str
    family: str
    subclass: str

    def setup_ids(self, horizon: str) -> tuple[str, ...]:
        h = str(horizon).lower()
        if self.family == "forex":
            from config import CONFIG

            setup_mode = str(CONFIG.get("ENGINE_A_V3_FOREX_SETUP", "trend") or "trend").lower()
            if setup_mode == "london_open" and h == "intraday":
                return ("fx_london_open_breakout",)
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
    upper = str(pair.get("display") or pair.get("pair") or pair.get("symbol") or "").strip().upper()
    symbol = str(pair.get("symbol") or "").upper()
    group = _explicit_group(pair) or resolve_score_group_by_type(pair)

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
