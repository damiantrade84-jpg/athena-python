"""Group-aware scoring profiles for OX Alpha.

Each canonical score group gets an explicit profile: how much each of the six
OX Alpha axes contributes, what the decision thresholds are, and which risk
shape fits the group's intraday behaviour. Unknown groups fail over to a
conservative default profile rather than inheriting another group's tuning.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OxProfile:
    group: str
    # Component weights — must sum to ~1.0.
    w_kinetic: float
    w_spring: float
    w_flow: float
    w_velocity: float
    w_magnet: float
    w_structure: float
    # Decision thresholds on the 0..100 composite.
    trade_threshold: float
    watch_threshold: float
    # Risk shape.
    min_rr: float
    sl_atr_min: float
    sl_atr_max: float
    tp_atr_fallback: float
    # Behaviour flags.
    session_mode: str = "all"          # all | day  (day = exchange-hours asset)
    volume_axis: str = "auto"          # auto | required | off
    direction_agreement: float = 0.5   # min weighted vote share to resolve side


def _p(
    group: str,
    wk: float,
    ws: float,
    wf: float,
    wv: float,
    wm: float,
    wst: float,
    trade: float,
    watch: float,
    min_rr: float,
    sl_min: float,
    sl_max: float,
    tp_fb: float,
    session_mode: str = "all",
    volume_axis: str = "auto",
    agreement: float = 0.5,
) -> OxProfile:
    return OxProfile(
        group=group,
        w_kinetic=wk,
        w_spring=ws,
        w_flow=wf,
        w_velocity=wv,
        w_magnet=wm,
        w_structure=wst,
        trade_threshold=trade,
        watch_threshold=watch,
        min_rr=min_rr,
        sl_atr_min=sl_min,
        sl_atr_max=sl_max,
        tp_atr_fallback=tp_fb,
        session_mode=session_mode,
        volume_axis=volume_axis,
        direction_agreement=agreement,
    )


PROFILES: dict[str, OxProfile] = {
    # ── Forex ──────────────────────────────────────────────────────────────
    "forex_majors": _p(
        "forex_majors", 0.20, 0.13, 0.13, 0.18, 0.20, 0.16,
        trade=62.0, watch=48.0, min_rr=1.4, sl_min=0.8, sl_max=2.6, tp_fb=2.2,
        session_mode="day", agreement=0.5,
    ),
    "forex_crosses": _p(
        "forex_crosses", 0.22, 0.13, 0.12, 0.15, 0.20, 0.18,
        trade=64.0, watch=50.0, min_rr=1.5, sl_min=0.9, sl_max=2.8, tp_fb=2.3,
        session_mode="day", agreement=0.52,
    ),
    "forex_exotics": _p(
        "forex_exotics", 0.20, 0.12, 0.10, 0.13, 0.23, 0.22,
        trade=68.0, watch=54.0, min_rr=1.7, sl_min=1.0, sl_max=3.2, tp_fb=2.6,
        session_mode="day", agreement=0.55,
    ),
    "forex_other": _p(
        "forex_other", 0.20, 0.12, 0.10, 0.13, 0.23, 0.22,
        trade=68.0, watch=54.0, min_rr=1.7, sl_min=1.0, sl_max=3.2, tp_fb=2.6,
        session_mode="day", agreement=0.55,
    ),
    # ── Crypto ─────────────────────────────────────────────────────────────
    "crypto_btc": _p(
        "crypto_btc", 0.24, 0.17, 0.19, 0.08, 0.16, 0.16,
        trade=60.0, watch=46.0, min_rr=1.4, sl_min=0.9, sl_max=3.0, tp_fb=2.4,
        session_mode="all", volume_axis="required",
    ),
    "crypto_eth": _p(
        "crypto_eth", 0.25, 0.16, 0.19, 0.08, 0.15, 0.17,
        trade=61.0, watch=47.0, min_rr=1.4, sl_min=0.9, sl_max=3.0, tp_fb=2.4,
        session_mode="all", volume_axis="required",
    ),
    "crypto_doge": _p(
        "crypto_doge", 0.28, 0.15, 0.21, 0.06, 0.13, 0.17,
        trade=66.0, watch=52.0, min_rr=1.6, sl_min=1.1, sl_max=3.4, tp_fb=2.8,
        session_mode="all", volume_axis="required", agreement=0.56,
    ),
    "crypto_alt_majors": _p(
        "crypto_alt_majors", 0.27, 0.15, 0.21, 0.06, 0.14, 0.17,
        trade=65.0, watch=51.0, min_rr=1.6, sl_min=1.0, sl_max=3.4, tp_fb=2.7,
        session_mode="all", volume_axis="required", agreement=0.55,
    ),
    "crypto_other": _p(
        "crypto_other", 0.26, 0.14, 0.20, 0.06, 0.15, 0.19,
        trade=68.0, watch=54.0, min_rr=1.7, sl_min=1.1, sl_max=3.6, tp_fb=2.9,
        session_mode="all", volume_axis="required", agreement=0.58,
    ),
    # ── Commodities ────────────────────────────────────────────────────────
    "precious_trackers": _p(
        "precious_trackers", 0.21, 0.14, 0.12, 0.16, 0.21, 0.16,
        trade=62.0, watch=48.0, min_rr=1.4, sl_min=0.9, sl_max=2.8, tp_fb=2.3,
        session_mode="day",
    ),
    "energy_oil": _p(
        "energy_oil", 0.22, 0.15, 0.12, 0.16, 0.19, 0.16,
        trade=63.0, watch=49.0, min_rr=1.5, sl_min=0.9, sl_max=3.0, tp_fb=2.4,
        session_mode="day",
    ),
    "nat_gas": _p(
        "nat_gas", 0.22, 0.17, 0.11, 0.15, 0.19, 0.16,
        trade=66.0, watch=52.0, min_rr=1.6, sl_min=1.1, sl_max=3.4, tp_fb=2.7,
        session_mode="day",
    ),
    "copper": _p(
        "copper", 0.21, 0.14, 0.12, 0.17, 0.19, 0.17,
        trade=64.0, watch=50.0, min_rr=1.5, sl_min=0.9, sl_max=3.0, tp_fb=2.4,
        session_mode="day",
    ),
    "pgm_metals": _p(
        "pgm_metals", 0.20, 0.14, 0.11, 0.17, 0.20, 0.18,
        trade=65.0, watch=51.0, min_rr=1.5, sl_min=1.0, sl_max=3.0, tp_fb=2.5,
        session_mode="day",
    ),
    "base_metals": _p(
        "base_metals", 0.21, 0.14, 0.12, 0.16, 0.19, 0.18,
        trade=64.0, watch=50.0, min_rr=1.5, sl_min=1.0, sl_max=3.0, tp_fb=2.5,
        session_mode="day",
    ),
    "softs": _p(
        "softs", 0.20, 0.15, 0.11, 0.16, 0.20, 0.18,
        trade=66.0, watch=52.0, min_rr=1.6, sl_min=1.0, sl_max=3.2, tp_fb=2.6,
        session_mode="day",
    ),
    "commodity_other": _p(
        "commodity_other", 0.21, 0.14, 0.12, 0.16, 0.19, 0.18,
        trade=65.0, watch=51.0, min_rr=1.5, sl_min=1.0, sl_max=3.2, tp_fb=2.5,
        session_mode="day",
    ),
    # ── Indices ────────────────────────────────────────────────────────────
    "us_indices_trackers": _p(
        "us_indices_trackers", 0.22, 0.13, 0.12, 0.14, 0.23, 0.16,
        trade=62.0, watch=48.0, min_rr=1.4, sl_min=0.9, sl_max=2.8, tp_fb=2.3,
        session_mode="day",
    ),
    "eu_indices": _p(
        "eu_indices", 0.22, 0.13, 0.11, 0.15, 0.22, 0.17,
        trade=63.0, watch=49.0, min_rr=1.4, sl_min=0.9, sl_max=2.8, tp_fb=2.3,
        session_mode="day",
    ),
    "asian_indices": _p(
        "asian_indices", 0.22, 0.13, 0.11, 0.15, 0.22, 0.17,
        trade=63.0, watch=49.0, min_rr=1.4, sl_min=0.9, sl_max=2.8, tp_fb=2.3,
        session_mode="day",
    ),
    "index_other": _p(
        "index_other", 0.22, 0.13, 0.11, 0.15, 0.22, 0.17,
        trade=64.0, watch=50.0, min_rr=1.5, sl_min=1.0, sl_max=3.0, tp_fb=2.4,
        session_mode="day",
    ),
    # ── Stocks / ETFs ──────────────────────────────────────────────────────
    "us_stock_single": _p(
        "us_stock_single", 0.22, 0.13, 0.12, 0.13, 0.22, 0.18,
        trade=65.0, watch=51.0, min_rr=1.5, sl_min=1.0, sl_max=3.0, tp_fb=2.5,
        session_mode="day",
    ),
    "bond_tlt": _p(
        "bond_tlt", 0.21, 0.14, 0.11, 0.15, 0.21, 0.18,
        trade=64.0, watch=50.0, min_rr=1.5, sl_min=1.0, sl_max=3.0, tp_fb=2.4,
        session_mode="day",
    ),
    "smallcap_em_etf": _p(
        "smallcap_em_etf", 0.22, 0.14, 0.12, 0.14, 0.20, 0.18,
        trade=65.0, watch=51.0, min_rr=1.5, sl_min=1.0, sl_max=3.2, tp_fb=2.5,
        session_mode="day",
    ),
    "stock_other": _p(
        "stock_other", 0.22, 0.13, 0.12, 0.13, 0.22, 0.18,
        trade=66.0, watch=52.0, min_rr=1.6, sl_min=1.0, sl_max=3.2, tp_fb=2.6,
        session_mode="day",
    ),
}

DEFAULT_PROFILE = _p(
    "default", 0.22, 0.14, 0.12, 0.14, 0.20, 0.18,
    trade=68.0, watch=54.0, min_rr=1.6, sl_min=1.0, sl_max=3.2, tp_fb=2.6,
    session_mode="day", agreement=0.55,
)


def profile_for_group(group: str | None) -> OxProfile:
    key = str(group or "").strip().lower()
    return PROFILES.get(key, DEFAULT_PROFILE)
