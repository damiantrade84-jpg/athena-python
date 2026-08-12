from __future__ import annotations

from dataclasses import dataclass

from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS, resolve_score_group_by_type

# Re-exported for every caller that still imports the canonical group set from
# routing (engine_a_v3.audit, profile, workflow, evaluator, tests, etc.).
KNOWN_SCORE_GROUPS = ENGINE_A_KNOWN_SCORE_GROUPS


def intradaily_pullback_overlay_enabled(
    family: str, subclass: str | None = None
) -> bool:
    """Whether the intradaily path should also run the trend-pullback specialist.

    Forex/crypto already declare a pullback id. This gate adds the same
    overlay to commodity / index / equity and can disable it per family.
    Precious metals also honor ENGINE_A_V3_XAU_INTRADAY_PULLBACK_ENABLED.
    """
    family_key = str(family or "").strip().lower()
    subclass_key = str(subclass or "").strip().lower()
    try:
        from config import CONFIG

        if subclass_key in {"xau", "precious"} and not bool(
            CONFIG.get("ENGINE_A_V3_XAU_INTRADAY_PULLBACK_ENABLED", True)
        ):
            return False
        cfg = CONFIG.get("ENGINE_A_V3_INTRADAY_PULLBACK_OVERLAY") or {}
        if isinstance(cfg, dict):
            if "ENABLED" in cfg and not bool(cfg.get("ENABLED")):
                return False
            by_family = cfg.get("BY_FAMILY") or {}
            if isinstance(by_family, dict) and family_key in by_family:
                return bool(by_family.get(family_key))
        return True
    except Exception:
        return True


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
            if h != "intraday":
                return ("precious_h4_d1_continuation",)
            ids = ("precious_london_ny_breakout_retest",)
            if intradaily_pullback_overlay_enabled(self.family, self.subclass):
                ids = ids + ("precious_trend_pullback",)
            return ids
        if self.family == "commodity":
            if h != "intraday":
                return (f"{self.subclass}_trend_pullback",)
            ids = (f"{self.subclass}_breakout_retest",)
            if intradaily_pullback_overlay_enabled(self.family, self.subclass):
                ids = ids + (f"{self.subclass}_trend_pullback",)
            return ids
        if self.family in {"index", "equity_etf"}:
            if h != "intraday":
                return (f"{self.subclass}_relative_strength_breakout_pullback",)
            ids = (f"{self.subclass}_opening_range_gap_continuation",)
            if intradaily_pullback_overlay_enabled(self.family, self.subclass):
                ids = ids + (f"{self.subclass}_trend_pullback",)
            return ids
        return ("unsupported_specialist",)


def _explicit_group(pair: dict) -> str | None:
    value = pair.get("score_group") or pair.get("scoreGroup")
    value = str(value or "").strip()
    if value in KNOWN_SCORE_GROUPS:
        return value
    try:
        from scoring import get_pair_profile

        profile = get_pair_profile(pair) or {}
        override = str(profile.get("score_group") or "").strip()
        if override in KNOWN_SCORE_GROUPS:
            return override
    except Exception:
        pass
    return None


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
        if group == "precious_trackers" and upper not in {"XAU/USD", "XAG/USD"}:
            # GLD/SLV/GDX keep precious_trackers periods/threshold but must not
            # inherit the London/NY metal-session specialist.
            return SpecialistRoute(group, "equity_etf", "us_stock_single")
        if upper == "XAU/ZAR":
            # Cross-metal: stay on commodity_other score/TF (thin M30) but use
            # the metal session specialist instead of the generic commodity one.
            return SpecialistRoute(group, "commodity", "precious")
        subclass = "xau" if upper == "XAU/USD" else group.removesuffix("_trackers")
        return SpecialistRoute(group, "commodity", subclass)
    if group in {"us_indices_trackers", "eu_indices", "asian_indices", "index_other"}:
        return SpecialistRoute(group, "index", group.removesuffix("_trackers"))
    if group in {"us_stock_single", "bond_tlt", "smallcap_em_etf", "stock_other"}:
        subclass = "jse_equity" if symbol.endswith(".JO") else group
        return SpecialistRoute(group, "equity_etf", subclass)
    return SpecialistRoute("unknown", "unknown", "unknown")
