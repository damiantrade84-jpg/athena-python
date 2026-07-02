import pandas as pd
import numpy as np
import math
from scipy.signal import find_peaks
import logging
import threading
import config
from indicators import calc_atr
from zone_registry import get_zone_registry

log = logging.getLogger(__name__)


# FIX 2: Corrected risk-inverted regime multipliers.
# TRENDING/LOW_VOL = slightly easier; RANGING/HIGH_VOL = stricter.
ENGINE_B_REGIME_GATE_DEFAULTS = {
    "TRENDING": 0.95,
    "RANGING": 1.10,
    "HIGH_VOLATILITY": 1.10,
    "LOW_VOLATILITY": 0.90,
}


def _naked_engine_atr_mult(key: str, default: float, asset_type: str | None = "") -> float:
    """Resolve a NAKED_ENGINE ATR multiplier that may be a scalar or a per-asset
    dict ({"crypto": 1.8, "forex": 1.2, "default": 1.5}). Falls back to ``default``
    on missing/invalid config. Used for zone prominence / merge / touch tolerances
    that were previously hardcoded literals.
    """
    raw = config.CONFIG.get("NAKED_ENGINE", {}).get(key, default)
    if isinstance(raw, dict):
        raw = raw.get(str(asset_type or "").lower(), raw.get("default", default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


# Forex pairs whose peak liquidity lies inside Tokyo/Sydney hours. Skipping the
# Asian session on these pairs removes their primary trading window. The skip
# stays in effect for European/North-American pairs (EUR/USD, GBP/USD, USD/CHF,
# USD/CAD, EUR/GBP, etc.) where Asian hours are genuinely thin.
_ASIAN_ACTIVE_CURRENCIES = ("JPY", "AUD", "NZD", "CNH", "SGD", "HKD")


def _pair_has_asian_active_currency(display: str | None) -> bool:
    if not display:
        return False
    sym = str(display).upper()
    return any(ccy in sym for ccy in _ASIAN_ACTIVE_CURRENCIES)


def engine_b_forex_asian_session_blocks_bar(
    entry_candles: list | None, asset_type: str, display: str | None = None
) -> bool:
    """Return True when Engine B should skip this bar for forex (Asian session UTC).

    Gated by ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED (default True for historical
    backtest parity). Live scan uses the same helper so BT and discovery align.

    Pairs containing an Asian-active currency (JPY/AUD/NZD/CNH/SGD/HKD) are
    exempt — Tokyo/Sydney is their primary session, not thin time.
    """
    if str(asset_type or "").lower() != "forex":
        return False
    if not bool(
        config.CONFIG.get("ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED", True)
    ):
        return False
    if _pair_has_asian_active_currency(display):
        return False
    if not entry_candles:
        return False
    try:
        from datetime import datetime, timezone

        last = entry_candles[-1]
        _lt = last.get("time") or last.get("datetime")
        if not _lt:
            return False
        _bar_dt = datetime.fromisoformat(str(_lt).replace("Z", "+00:00"))
        if _bar_dt.tzinfo is None:
            _bar_dt = _bar_dt.replace(tzinfo=timezone.utc)
        h = _bar_dt.hour
        if bool(config.CONFIG.get("ENGINE_B_FOREX_PRE_ASIAN_SESSION_SKIP_ENABLED", True)) and h == 23:
            return True
        return h >= 22 or h < 7
    except Exception:
        return False


def _parse_utc_candle_time(value):
    try:
        from datetime import datetime, timezone

        if not value:
            return None
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _forex_session_bucket(candle_time, *, symbol: str = "") -> dict:
    if config.CONFIG.get("SESSION_QUALITY_ENABLED", False):
        try:
            from session_contract import classify_session, session_quality_to_legacy
            dt = _parse_utc_candle_time(candle_time)
            sc = classify_session(symbol=symbol, asset_class="forex", timestamp_utc=dt or candle_time)
            q = session_quality_to_legacy(sc.session_quality)
            active = []
            n = sc.session_name
            if "london" in n and "ny" in n:
                active = ["london", "new_york"]
            elif "london" in n or "pre_london" in n:
                active = ["london"]
            elif "ny" in n or "new_york" in n:
                active = ["new_york"]
            elif "asian" in n or "asia" in n:
                active = ["asia"]
            else:
                active = ["asia"] if q == "low" else []
            return {
                "session": sc.session_name,
                "session_quality": q,
                "sessions_active": active,
                "utc_hour": dt.hour if dt else None,
            }
        except Exception:
            pass

    dt = _parse_utc_candle_time(candle_time)
    if dt is None:
        return {
            "session": "unknown",
            "session_quality": "unknown",
            "sessions_active": [],
            "utc_hour": None,
        }
    # Audit #5: these static UTC bands are the FALLBACK only — the primary path above
    # (session_contract.classify_session) is the DST-aware source of truth. The bands
    # do not auto-shift for broker DST, so expose a manual offset
    # (ENGINE_B_SESSION_FALLBACK_DST_OFFSET_HOURS, default 0) operators can set to +1
    # during summer DST when this fallback is in use. A full DST calendar is left to
    # the primary path deliberately.
    try:
        _dst_offset = int(config.CONFIG.get("ENGINE_B_SESSION_FALLBACK_DST_OFFSET_HOURS", 0) or 0)
    except (TypeError, ValueError):
        _dst_offset = 0
    hour = (dt.hour - _dst_offset) % 24
    if 12 <= hour < 16:
        session = "london_ny_overlap"
        quality = "high"
        active = ["london", "new_york"]
    elif 7 <= hour < 10:
        session = "london_open"
        quality = "high"
        active = ["london"]
    elif 10 <= hour < 12:
        session = "london"
        quality = "medium"
        active = ["london"]
    elif 16 <= hour < 21:
        session = "new_york"
        quality = "medium"
        active = ["new_york"]
    else:
        session = "asian_off_hours"
        quality = "low"
        active = ["asia"]
    return {
        "session": session,
        "session_quality": quality,
        "sessions_active": active,
        "utc_hour": dt.hour,
    }


def _engine_b_forex_session_structure_context(
    *,
    asset_type: str,
    candle_time,
    zone_context: bool,
    ob_at_zone: bool,
    fvg_overlap: bool,
    liquidity_sweep: bool,
) -> dict:
    """Forex-only session diagnostics for structure quality.

    London and New York tend to give cleaner institutional structure than the
    Asian/off-hours window. This helper is diagnostics-first and only produces a
    score bonus when the explicit config flag is enabled.
    """
    session_cfg = config.CONFIG.get("ENGINE_B_FOREX_SESSION_STRUCTURE_WEIGHTING", {}) or {}
    if not isinstance(session_cfg, dict):
        session_cfg = {}
    enabled = bool(session_cfg.get("ENABLED", False))
    bucket = _forex_session_bucket(candle_time)
    is_forex = str(asset_type or "").lower() == "forex"
    confluence_count = sum(
        1 for item in (zone_context, ob_at_zone, fvg_overlap, liquidity_sweep) if item
    )
    active_quality = bucket["session_quality"] in {"high", "medium"}
    score_bonus = 0.0
    if enabled and is_forex and bucket["session_quality"] != "unknown" and confluence_count > 0:
        if bucket["session_quality"] == "high":
            score_bonus += _float_value_from_mapping(
                session_cfg, "HIGH_QUALITY_BONUS", 0.02
            )
        elif bucket["session_quality"] == "medium":
            score_bonus += _float_value_from_mapping(
                session_cfg, "MEDIUM_QUALITY_BONUS", 0.01
            )
        elif bucket["session_quality"] == "low":
            score_bonus += _float_value_from_mapping(
                session_cfg, "LOW_QUALITY_PENALTY", -0.02
            )
        if bucket["session"] == "london_ny_overlap":
            score_bonus += _float_value_from_mapping(
                session_cfg, "LONDON_NY_OVERLAP_BONUS", 0.01
            )
        if liquidity_sweep and active_quality:
            score_bonus += _float_value_from_mapping(
                session_cfg, "LIQUIDITY_SWEEP_ACTIVE_SESSION_BONUS", 0.01
            )
        max_abs = abs(
            _float_value_from_mapping(session_cfg, "MAX_ABS_SCORE_BONUS", 0.04)
        )
        score_bonus = max(-max_abs, min(max_abs, score_bonus))

    return {
        "enabled": enabled,
        "session": bucket["session"],
        "session_quality": bucket["session_quality"],
        "sessions_active": bucket["sessions_active"],
        "utc_hour": bucket["utc_hour"],
        "confluence_count": confluence_count,
        "active_session_structure": bool(active_quality and confluence_count > 0),
        "liquidity_sweep_active_session": bool(liquidity_sweep and active_quality),
        "score_bonus": round(score_bonus, 6),
        "score_influence_enabled": bool(
            session_cfg.get("SCORE_INFLUENCE_ENABLED", False)
        ),
    }


def _equity_session_bucket(candle_time, *, asset_class: str = "stock", symbol: str = "", venue: str | None = None) -> dict:
    if config.CONFIG.get("SESSION_QUALITY_ENABLED", False):
        try:
            from session_contract import classify_session, session_quality_to_legacy
            dt = _parse_utc_candle_time(candle_time)
            sc = classify_session(
                symbol=symbol, asset_class=asset_class,
                timestamp_utc=dt or candle_time, venue=venue,
            )
            q = session_quality_to_legacy(sc.session_quality)
            active = ["us_cash"] if q in ("high", "medium") else []
            decimal_hour = round(dt.hour + (dt.minute / 60.0), 2) if dt else None
            return {
                "session": sc.session_name,
                "session_quality": q,
                "sessions_active": active,
                "utc_hour": decimal_hour,
            }
        except Exception:
            pass

    dt = _parse_utc_candle_time(candle_time)
    if dt is None:
        return {
            "session": "unknown",
            "session_quality": "unknown",
            "sessions_active": [],
            "utc_hour": None,
        }
    decimal_hour = dt.hour + (dt.minute / 60.0)
    # UTC approximation of US cash hours; configurable scoring remains optional.
    if 14.5 <= decimal_hour < 16.0:
        session = "us_cash_open"
        quality = "high"
    elif 16.0 <= decimal_hour < 20.5:
        session = "us_regular"
        quality = "medium"
    elif 20.5 <= decimal_hour < 21.0:
        session = "us_cash_close"
        quality = "high"
    else:
        session = "off_hours"
        quality = "low"
    return {
        "session": session,
        "session_quality": quality,
        "sessions_active": ["us_cash"] if session != "off_hours" else [],
        "utc_hour": round(decimal_hour, 2),
    }


def _engine_b_equity_session_structure_context(
    *,
    asset_class: str,
    candle_time,
    zone_context: bool,
    ob_at_zone: bool,
    fvg_overlap: bool,
    liquidity_sweep: bool,
) -> dict:
    """Diagnostics-first US session context for stocks, indices, and ETFs."""
    session_cfg = config.CONFIG.get("ENGINE_B_EQUITY_SESSION_STRUCTURE_WEIGHTING", {}) or {}
    if not isinstance(session_cfg, dict):
        session_cfg = {}
    enabled = bool(session_cfg.get("ENABLED", False))
    bucket = _equity_session_bucket(candle_time)
    class_key = str(asset_class or "").lower()
    applicable = class_key in {"stock", "index", "etf", "etf_bond"}
    confluence_count = sum(
        1 for item in (zone_context, ob_at_zone, fvg_overlap, liquidity_sweep) if item
    )
    active_quality = bucket["session_quality"] in {"high", "medium"}
    score_bonus = 0.0
    if enabled and applicable and bucket["session_quality"] != "unknown" and confluence_count > 0:
        if bucket["session_quality"] == "high":
            score_bonus += _float_value_from_mapping(
                session_cfg, "HIGH_QUALITY_BONUS", 0.015
            )
        elif bucket["session_quality"] == "medium":
            score_bonus += _float_value_from_mapping(
                session_cfg, "MEDIUM_QUALITY_BONUS", 0.008
            )
        elif bucket["session_quality"] == "low":
            score_bonus += _float_value_from_mapping(
                session_cfg, "OFF_HOURS_PENALTY", -0.015
            )
        if liquidity_sweep and active_quality:
            score_bonus += _float_value_from_mapping(
                session_cfg, "LIQUIDITY_SWEEP_ACTIVE_SESSION_BONUS", 0.008
            )
        max_abs = abs(
            _float_value_from_mapping(session_cfg, "MAX_ABS_SCORE_BONUS", 0.03)
        )
        score_bonus = max(-max_abs, min(max_abs, score_bonus))

    return {
        "enabled": enabled,
        "asset_class": class_key,
        "session": bucket["session"],
        "session_quality": bucket["session_quality"],
        "sessions_active": bucket["sessions_active"],
        "utc_hour": bucket["utc_hour"],
        "confluence_count": confluence_count,
        "active_session_structure": bool(active_quality and confluence_count > 0),
        "liquidity_sweep_active_session": bool(liquidity_sweep and active_quality),
        "score_bonus": round(score_bonus, 6),
        "score_influence_enabled": bool(
            session_cfg.get("SCORE_INFLUENCE_ENABLED", False)
        ),
    }


def _float_value_from_mapping(mapping: dict, key: str, default: float) -> float:
    try:
        return float((mapping or {}).get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _engine_b_confirmed_only_struct_candles(
    struct_candles: list,
    structure_tf: str,
    *,
    pair: dict | None,
    diagnostics: dict | None = None,
) -> list:
    """Use confirmed (closed) bars only for structural TF when pair context is available."""
    def _reason(code: str, **extra) -> None:
        if isinstance(diagnostics, dict):
            diagnostics.clear()
            diagnostics.update({"reason": code, **extra})

    if not bool(config.CONFIG.get("ENGINE_B_STRIP_FORMING_STRUCT", True)):
        _reason("disabled")
        return struct_candles
    if not struct_candles or len(struct_candles) < 2:
        _reason("insufficient_input", candle_count=len(struct_candles or []))
        return []
    if not pair or not isinstance(pair, dict):
        _reason("missing_pair_context")
        return []
    try:
        import time
        from athena_app.services.market_state import (
            market_state_offset_hours,
            split_market_state,
        )

        tf_u = str(structure_tf or "").upper()
        display = str(pair.get("display") or pair.get("symbol") or "")
        st = split_market_state(
            list(struct_candles),
            tf_u,
            display,
            time_now=time.time(),
            offset_hours=market_state_offset_hours(pair, tf_u),
        )
        confirmed = list(st.get("confirmed") or [])
        min_bars = int(config.CONFIG.get("ENGINE_B_STRUCT_CONFIRMED_MIN_BARS", 20) or 20)
        if len(confirmed) >= min_bars:
            _reason(
                "confirmed",
                confirmed_count=len(confirmed),
                min_bars=min_bars,
            )
            return confirmed
        _reason(
            "insufficient_confirmed_bars",
            confirmed_count=len(confirmed),
            min_bars=min_bars,
        )
    except Exception as exc:
        _reason("split_market_state_error", error=str(exc))
        return []
    return []


# Engine B timeframe matrix — single source of truth for TF selection across
# live, execution, scan, and backtest. Maps (asset_type, style) to the four
# roles: struct (BOS/CHoCH detection), zone (S/R clusters), trigger (entry
# pattern), atr (SL/TP scale). Callers pre-resolve candles per role.
_ENGINE_B_TF_MATRIX = {
    "forex":     {"scalp": ("H1", "H4", "H1", "H1"),
                  "intraday": ("H4", "H4", "H1", "H4"),
                  "swing": ("D1", "D1", "H4", "D1")},
    "crypto":    {"scalp": ("H1", "H4", "H1", "H1"),
                  "intraday": ("H4", "H4", "H1", "H4"),
                  "swing": ("D1", "D1", "H4", "D1")},
    "commodity": {"scalp": ("H1", "H4", "H1", "H1"),
                  "intraday": ("H4", "H4", "H1", "H4"),
                  "swing": ("D1", "D1", "H4", "D1")},
    "index":     {"scalp": ("H1", "H4", "H1", "H1"),
                  "intraday": ("H4", "H4", "H1", "H4"),
                  "swing": ("D1", "D1", "H4", "D1")},
    "stock":     {"scalp": ("H1", "H4", "H1", "H1"),
                  "intraday": ("H4", "H4", "H1", "H4"),
                  "swing": ("D1", "D1", "H4", "D1")},
}


# Score groups that are equity ETFs (route through STYLE_ATR_MULTS["etf"] so we
# can use tighter SL/TP than single stocks). Bond ETFs (TLT) get an even tighter
# class. All others fall back to the asset_type lookup.
_ETF_SCORE_GROUPS = frozenset({
    "smallcap_em_etf",       # IWM, EEM
    "us_indices_trackers",   # SPY, QQQ, DIA (when type=stock)
    "energy_oil",            # USO, XLE (stock-typed energy ETFs)
    "precious_trackers",     # GLD, SLV, GDX (stock-typed precious-metal ETFs)
})
_ETF_BOND_SCORE_GROUPS = frozenset({"bond_tlt"})


def resolve_engine_b_asset_class(asset_type: str, score_group: str | None = None) -> str:
    """Map (asset_type, score_group) to the asset class used for STYLE_ATR_MULTS /
    ATR_CLASS lookup. Stock-typed ETFs route to ``etf`` (or ``etf_bond`` for TLT)
    so they pick up tighter SL/TP multipliers than single stocks.
    """
    a = (asset_type or "").lower()
    g = (score_group or "").lower()
    if a == "stock":
        if g in _ETF_BOND_SCORE_GROUPS:
            return "etf_bond"
        if g in _ETF_SCORE_GROUPS:
            return "etf"
    return a or "forex"


def resolve_engine_b_tfs(asset_type: str, style: str) -> dict:
    """Resolve struct/zone/trigger/atr timeframes for an (asset_type, style) pair.

    Single source of truth used by athena, execution, scanner, and backtest_runner.
    Replaces the legacy ``ENGINE_B_FOREX_STRUCTURE_TF`` flag and the per-style
    hardcoded TFs in ``_naked_scan_style_profile``.
    """
    a = (asset_type or "forex").lower()
    s = (style or "intraday").lower()
    if s == "auto":
        s = "intraday"
    asset_table = _ENGINE_B_TF_MATRIX.get(a) or _ENGINE_B_TF_MATRIX["forex"]
    struct_tf, zone_tf, trigger_tf, atr_tf = asset_table.get(
        s, asset_table["intraday"]
    )
    return {
        "struct": struct_tf,
        "zone": zone_tf,
        "trigger": trigger_tf,
        "atr": atr_tf,
    }

# Observability only — stable codes for logs/diagnostics (no scoring side effects).
ENGINE_B_REASON_RESISTANCE_TOO_CLOSE = "resistance_too_close"
ENGINE_B_REASON_SUPPORT_TOO_CLOSE = "support_too_close"
ENGINE_B_REASON_ADVERSE_DXY = "adverse_dxy_correlation"
ENGINE_B_REASON_STRUCTURAL_SL_HARD_CAP = "structural_sl_rejected_hard_cap"
# Intentionally never emitted. ADX no longer gates Engine B structure (FIX 6:
# ADX drives regime classification only). Kept as a stable identifier so the
# negative assertion in tests/test_engine_b_diagnostics.py::
# test_calculate_confidence_forex_adx_derives_regime_not_blocks_structure
# pins removal of the legacy gate (audit BUG-B-1 / BUG-B-7).
ENGINE_B_REASON_FOREX_ADX_LOW = "forex_adx_below_min"
ENGINE_B_REASON_TP_WRONG_SIDE = "tp_wrong_side"
ENGINE_B_REASON_SL_WRONG_SIDE = "sl_wrong_side"
ENGINE_B_REASON_SEQUENCE_COUNTER_TREND = "sequence_counter_trend"
ENGINE_B_REASON_NO_TRIGGER_PATTERN = "no_trigger_pattern"
ENGINE_B_REASON_BOS_WITHOUT_VOLUME = "bos_without_volume"
ENGINE_B_REASON_D1_PD_ARRAY_CONFLICT = "d1_pd_array_conflict"
ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE = "structural_tp_too_close"
ENGINE_B_REASON_AGGTRADE_REQUIRED = "aggtrade_required_for_crypto"
ENGINE_B_REASON_AGGTRADE_CVD_OPPOSED = "aggtrade_cvd_opposed"


def _float_cfg(key: str, default: float) -> float:
    try:
        return float(config.CONFIG.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _crypto_structure_adjustment(asset_type: str, candles: list, atr: float) -> dict:
    """Return default-off crypto structure tuning and diagnostics.

    Crypto spends more time in wick-heavy, high-ATR regimes than forex. When the
    explicit flag is enabled, only elevated/high crypto volatility widens swing
    prominence and requires a minimum close-through for BOS/CHoCH.
    """
    is_crypto = str(asset_type or "").lower() == "crypto"
    enabled = bool(config.CONFIG.get("ENGINE_B_CRYPTO_VOLATILITY_ADJUSTMENT_ENABLED", False))
    detail = {
        "asset_type": str(asset_type or "").lower(),
        "enabled": enabled,
        "applied": False,
        "volatility_regime": "not_crypto" if not is_crypto else "unknown",
        "atr_pct": None,
        "wick_dominance": None,
        "structure_mult": 1.0,
        "swing_prominence_mult": 1.0,
        "bos_min_break_atr": 0.0,
    }
    if not is_crypto:
        return detail

    try:
        close = float((candles[-1] or {}).get("close", 0.0)) if candles else 0.0
    except (TypeError, ValueError, AttributeError):
        close = 0.0
    try:
        atr_val = float(atr or 0.0)
    except (TypeError, ValueError):
        atr_val = 0.0
    atr_pct = (atr_val / abs(close)) if close else None

    bands = (config.CONFIG.get("VOLATILITY_SCALER_BANDS", {}) or {}).get("crypto", {}) or {}
    try:
        low_band = float(bands.get("low", 0.010))
    except (TypeError, ValueError):
        low_band = 0.010
    try:
        high_band = float(bands.get("high", 0.040))
    except (TypeError, ValueError):
        high_band = 0.040
    if high_band < low_band:
        low_band, high_band = high_band, low_band

    if atr_pct is None:
        regime = "unknown"
    elif atr_pct >= high_band:
        regime = "high_volatility"
    elif atr_pct >= low_band:
        regime = "elevated_volatility"
    else:
        regime = "normal"

    wick_ratios: list[float] = []
    for c in (candles or [])[-5:]:
        try:
            high = float(c.get("high"))
            low = float(c.get("low"))
            open_v = float(c.get("open"))
            close_v = float(c.get("close"))
            candle_range = high - low
            if candle_range > 0:
                body = abs(close_v - open_v)
                wick_ratios.append(max(0.0, min(1.0, (candle_range - body) / candle_range)))
        except (AttributeError, TypeError, ValueError):
            continue
    wick_dominance = round(float(np.mean(wick_ratios)), 4) if wick_ratios else None

    cfg_mult = max(1.0, _float_cfg("ENGINE_B_CRYPTO_STRUCTURE_MULT", 1.25))
    applied_mult = 1.0
    if enabled and regime == "high_volatility":
        applied_mult = cfg_mult
    elif enabled and regime == "elevated_volatility":
        applied_mult = 1.0 + ((cfg_mult - 1.0) * 0.5)

    min_break = 0.0
    if applied_mult > 1.0:
        min_break = max(0.0, _float_cfg("ENGINE_B_CRYPTO_BOS_MIN_BREAK_ATR", 0.10)) * applied_mult

    detail.update({
        "volatility_regime": regime,
        "atr_pct": round(atr_pct, 6) if atr_pct is not None else None,
        "wick_dominance": wick_dominance,
        "structure_mult": round(applied_mult, 4),
        "swing_prominence_mult": round(applied_mult, 4),
        "bos_min_break_atr": round(min_break, 4),
        "applied": applied_mult > 1.0,
    })
    return detail


def _asset_class_structure_adjustment(
    asset_type: str,
    score_group: str | None,
    candles: list,
    atr: float,
) -> dict:
    """Default-off non-forex/non-crypto structure tuning.

    Reuses Engine B's existing swing prominence and close-through hooks so asset
    classes can be tested without duplicating BOS/CHoCH detection logic.
    """
    asset_class = resolve_engine_b_asset_class(asset_type, score_group)
    enabled = bool(config.CONFIG.get("ENGINE_B_ASSET_CLASS_ADJUSTMENTS_ENABLED", False))
    vol_aware = bool(
        config.CONFIG.get("ENGINE_B_ASSET_CLASS_VOLATILITY_AWARE_ENABLED", False)
    )
    applicable = asset_class in {"forex", "stock", "index", "commodity", "etf", "etf_bond"}
    detail = {
        "asset_type": str(asset_type or "").lower(),
        "asset_class": asset_class,
        "score_group": score_group,
        "enabled": enabled,
        "volatility_aware": vol_aware,
        "applied": False,
        "volatility_regime": "not_applicable" if not applicable else "unknown",
        "atr_pct": None,
        "structure_mult": 1.0,
        "swing_prominence_mult": 1.0,
        "bos_min_break_atr": 0.0,
    }
    if not applicable:
        return detail

    try:
        close = float((candles[-1] or {}).get("close", 0.0)) if candles else 0.0
    except (TypeError, ValueError, AttributeError):
        close = 0.0
    try:
        atr_val = float(atr or 0.0)
    except (TypeError, ValueError):
        atr_val = 0.0
    atr_pct = (atr_val / abs(close)) if close else None

    bands_all = config.CONFIG.get("VOLATILITY_SCALER_BANDS", {}) or {}
    bands = bands_all.get(str(score_group or "").lower()) or bands_all.get(asset_class) or {}
    try:
        low_band = float(bands.get("low", 0.005))
    except (TypeError, ValueError):
        low_band = 0.005
    try:
        high_band = float(bands.get("high", 0.020))
    except (TypeError, ValueError):
        high_band = 0.020
    if high_band < low_band:
        low_band, high_band = high_band, low_band

    if atr_pct is None:
        regime = "unknown"
    elif atr_pct >= high_band:
        regime = "high_volatility"
    elif atr_pct >= low_band:
        regime = "elevated_volatility"
    else:
        regime = "normal"

    mult_cfg = config.CONFIG.get("ENGINE_B_ASSET_CLASS_STRUCTURE_MULT", {}) or {}
    break_cfg = config.CONFIG.get("ENGINE_B_ASSET_CLASS_BOS_MIN_BREAK_ATR", {}) or {}
    if not isinstance(mult_cfg, dict):
        mult_cfg = {}
    if not isinstance(break_cfg, dict):
        break_cfg = {}
    cfg_mult = max(1.0, _float_value_from_mapping(mult_cfg, asset_class, 1.0))
    if cfg_mult == 1.0 and score_group:
        cfg_mult = max(
            1.0, _float_value_from_mapping(mult_cfg, str(score_group).lower(), 1.0)
        )

    applied_mult = 1.0
    if enabled:
        if vol_aware:
            if regime == "high_volatility":
                applied_mult = cfg_mult
            elif regime == "elevated_volatility":
                applied_mult = 1.0 + ((cfg_mult - 1.0) * 0.5)
        else:
            applied_mult = cfg_mult

    min_break_base = _float_value_from_mapping(break_cfg, asset_class, 0.0)
    if min_break_base == 0.0 and score_group:
        min_break_base = _float_value_from_mapping(
            break_cfg, str(score_group).lower(), 0.0
        )
    min_break = max(0.0, min_break_base) * applied_mult if enabled else 0.0

    detail.update({
        "volatility_regime": regime,
        "atr_pct": round(atr_pct, 6) if atr_pct is not None else None,
        "structure_mult": round(applied_mult, 4),
        "swing_prominence_mult": round(applied_mult, 4),
        "bos_min_break_atr": round(min_break, 4),
        "applied": applied_mult > 1.0 or min_break > 0.0,
    })
    return detail


def _crypto_structure_quality_score(
    bos_data: dict, choch_data: dict, sweep_data: dict, macro_sequence: str, direction: str, wick_dominance
) -> float:
    score = 0.0
    if bool(bos_data.get("bos_bull") or bos_data.get("bos_bear")):
        score += 0.35
    if bool(choch_data.get("choch_bull") or choch_data.get("choch_bear")):
        score += 0.20
    if bool(sweep_data.get("bull_sweep") or sweep_data.get("bear_sweep")):
        score += 0.15
    if (direction == "LONG" and macro_sequence == "HH_HL") or (
        direction == "SHORT" and macro_sequence == "LH_LL"
    ):
        score += 0.15
    if bool(bos_data.get("bos_volume_confirmed")):
        score += 0.15
    try:
        if wick_dominance is not None and float(wick_dominance) >= 0.65:
            score -= 0.10
    except (TypeError, ValueError):
        pass
    return round(max(0.0, min(1.0, score)), 4)


def _engine_b_structural_tp_buffer_atr_mult() -> float:
    """Dedicated opposing-zone TP buffer, separate from stop-loss zone width."""
    naked_cfg = config.CONFIG.get("NAKED_ENGINE", {}) or {}
    try:
        mult = float(naked_cfg.get("structural_tp_buffer_atr_mult", 0.25))
    except (TypeError, ValueError):
        mult = 0.25
    return max(0.0, mult)


def _engine_b_d1_conflict_window_atr_mult() -> float:
    naked_cfg = config.CONFIG.get("NAKED_ENGINE", {}) or {}
    try:
        mult = float(naked_cfg.get("d1_pd_array_conflict_window_atr_mult", 1.5))
    except (TypeError, ValueError):
        mult = 1.5
    return max(0.0, mult)


def _engine_b_rejection_wick_body_ratio() -> float:
    naked_cfg = config.CONFIG.get("NAKED_ENGINE", {}) or {}
    try:
        ratio = float(naked_cfg.get("rejection_wick_body_ratio", 1.2))
    except (TypeError, ValueError):
        ratio = 1.2
    return max(0.0, ratio)


def _engine_b_structural_target_price(
    zone: dict, direction: str, atr: float, buffer_mult: float
) -> float:
    if direction == "LONG":
        return float(zone["lower"]) - (float(atr) * buffer_mult)
    return float(zone["upper"]) + (float(atr) * buffer_mult)


def _adx_from_indicator_snap(snap: dict | None) -> float | None:
    """Read ADX from calc_indicators / calc_indicators_with_normalized snap (Engine A parity)."""
    if not isinstance(snap, dict):
        return None
    v = snap.get("adx")
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _engine_b_regime_gate(regime_label: str | None, asset_type: str = "") -> float:
    """Return the regime multiplier applied to Engine B ``min_score``.

    - ``ENGINE_B_REGIME_MULTIPLIERS_ENABLED: true`` — use
      ``ENGINE_B_REGIME_MULTIPLIERS`` (per-asset nested dict supported) with
      ``ENGINE_B_REGIME_GATE_DEFAULTS`` fallback; unknown regimes return ``1.0``.
    - ``ENGINE_B_REGIME_MULTIPLIERS_ENABLED: false`` — return ``1.0`` (base
      ``min_score`` is not adjusted by regime).
    """
    if not bool(config.CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS_ENABLED", True)):
        return 1.0
    regime_key = str(regime_label or "").upper()
    cfg_gate = config.CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS", {}) or {}
    # Try per-asset-class first (nested dict)
    asset_cfg = cfg_gate.get(asset_type.lower(), None) if asset_type else None
    if isinstance(asset_cfg, dict):
        try:
            return float(
                asset_cfg.get(
                    regime_key, ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0)
                )
            )
        except (TypeError, ValueError):
            log.warning("Regime multiplier config malformed for regime=%s asset=%s, using default", regime_key, asset_type)
            return float(ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0))
    # Fallback to flat config (backward compatible)
    try:
        return float(
            cfg_gate.get(regime_key, ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0))
        )
    except (TypeError, ValueError):
        log.warning("Regime multiplier config malformed for regime=%s, using default", regime_key)
        return float(ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0))


def engine_b_profile_trusted_asset_types(cfg: dict | None = None) -> frozenset[str]:
    """Asset types allowed to use Engine B previous-session volume profile."""
    raw_cfg = cfg if isinstance(cfg, dict) else config.CONFIG
    raw = raw_cfg.get("ENGINE_B_PROFILE_TRUSTED_ASSET_TYPES")
    if raw is None:
        raw = ["crypto", "stock"]
    if not isinstance(raw, (list, tuple)):
        return frozenset({"crypto", "stock"})
    return frozenset(str(item).strip().lower() for item in raw if str(item or "").strip())


def engine_b_profile_trusted_score_groups(cfg: dict | None = None) -> frozenset[str]:
    """Score groups allowed to use Engine B previous-session volume profile."""
    raw_cfg = cfg if isinstance(cfg, dict) else config.CONFIG
    raw = raw_cfg.get("ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS")
    if raw is None:
        raw = [
            "crypto_btc",
            "crypto_eth",
            "crypto_alt_majors",
            "crypto_doge",
            "crypto_other",
            "us_stock_single",
            "stock_other",
            "smallcap_em_etf",
        ]
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(str(item).strip().lower() for item in raw if str(item or "").strip())


def engine_b_profile_context_enabled(
    asset_type: str, cfg: dict | None = None, score_group: str | None = None,
    profile_trusted_override: bool | None = None,
) -> bool:
    """True when Engine B may compute/score previous-session VP for this asset type."""
    raw_cfg = cfg if isinstance(cfg, dict) else config.CONFIG
    if not bool(raw_cfg.get("ENGINE_B_PROFILE_SCORING_ENABLED", False)):
        return False
    # Per-(group,style) YAML override: profile_trusted in score_group_overrides
    # replaces the global trust list for this scoring call. Wired so
    # engine_b_config.yaml group_overrides rows can extend the global
    # ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS list per (group, style) — audit HIGH #2.
    if profile_trusted_override is not None:
        return bool(profile_trusted_override)
    trust_mode = str(raw_cfg.get("ENGINE_B_PROFILE_TRUST_MODE", "asset_type")).lower()
    if trust_mode == "score_group":
        group_key = str(score_group or "").strip().lower()
        return group_key in engine_b_profile_trusted_score_groups(raw_cfg)
    asset_key = str(asset_type or "").strip().lower()
    return asset_key in engine_b_profile_trusted_asset_types(raw_cfg)


def _engine_b_resolve_profile_trusted_override(
    score_group: str | None, style: str | None, cfg: dict | None = None,
) -> bool | None:
    """Look up per-(group,style) profile_trusted from score_group_overrides.

    Returns True/False when the override row sets profile_trusted, None when no
    override exists (caller falls back to the global trust list).
    """
    raw_cfg = cfg if isinstance(cfg, dict) else config.CONFIG
    group_key = str(score_group or "").strip().lower()
    style_key = str(style or "").strip().lower()
    if not group_key or not style_key:
        return None
    group_overrides = (raw_cfg.get("NAKED_ENGINE", {}) or {}).get("score_group_overrides", {}) or {}
    if not isinstance(group_overrides, dict):
        return None
    group_entry = group_overrides.get(group_key, {})
    if not isinstance(group_entry, dict):
        return None
    style_overrides = group_entry.get(style_key, {})
    if not isinstance(style_overrides, dict):
        return None
    if "profile_trusted" not in style_overrides:
        return None
    return bool(style_overrides["profile_trusted"])


def build_engine_b_profile_vp_context(
    asset_type: str, cfg: dict | None = None, score_group: str | None = None,
    profile_trusted_override: bool | None = None,
) -> dict:
    """Metadata for UI/AI: whether Engine B VP context is active for this asset."""
    raw_cfg = cfg if isinstance(cfg, dict) else config.CONFIG
    scoring_on = bool(raw_cfg.get("ENGINE_B_PROFILE_SCORING_ENABLED", False))
    trusted = engine_b_profile_context_enabled(
        asset_type, raw_cfg, score_group,
        profile_trusted_override=profile_trusted_override,
    )
    trust_mode = str(raw_cfg.get("ENGINE_B_PROFILE_TRUST_MODE", "asset_type")).lower()
    reason = None
    if not scoring_on:
        reason = "profile_scoring_disabled"
    elif not trusted:
        reason = (
            "score_group_untrusted_for_volume_profile"
            if trust_mode == "score_group"
            else "asset_type_untrusted_for_volume_profile"
        )
    return {
        "enabled": bool(scoring_on and trusted),
        "trusted": bool(trusted),
        "trust_mode": trust_mode,
        "score_group": score_group,
        "reason": reason,
    }


def _engine_b_crypto_aggtrade_context(
    asset_type: str,
    pair: dict | None,
    *,
    reference_ts=None,
    require_fresh: bool = True,
) -> dict:
    """Read-only crypto aggTrade VP/CVD context for Engine B."""
    asset_key = str(asset_type or "").lower()
    enabled = asset_key == "crypto" and bool(
        config.CONFIG.get("ENGINE_B_CRYPTO_AGGTRADE_ENABLED", True)
    )
    required = enabled and bool(
        config.CONFIG.get("ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", True)
    )
    base_fidelity = {
        "vp_source": "disabled" if not enabled else "unavailable",
        "vp_fidelity": "vp_unavailable",
        "vp_uses_real_trade_buckets": False,
        "vp_is_proxy": True,
        "cvd_source": "disabled" if not enabled else "unavailable",
        "cvd_fidelity": "cvd_unavailable",
        "cvd_uses_real_trade_buckets": False,
        "cvd_is_proxy": True,
    }
    out = {
        "aggtrade_enabled": enabled,
        "aggtrade_required": required,
        "aggtrade_available": False,
        "aggtrade_reason": None if enabled else "aggtrade_disabled",
        "aggtrade_profile": None,
        "aggtrade_profile_source_tf": None,
        "aggtrade_cvd_direction": None,
        "aggtrade_cvd_source": None,
        "aggtrade_cvd_slope": None,
        "aggtrade_cvd_bucket_count": None,
        "engine_b_data_fidelity": base_fidelity,
    }
    if not enabled:
        return out

    display = (
        (pair or {}).get("display")
        or (pair or {}).get("symbol")
        or (pair or {}).get("pair")
    )
    if not display:
        out["aggtrade_reason"] = "missing_symbol"
        return out

    try:
        from athena.microstructure.aggtrade_volume import (
            build_trade_bucket_volume_profile,
            check_trade_bucket_cvd,
            source_fidelity,
        )

        scalp_cfg = config.CONFIG.get("SCALP_ENGINE", {}) or {}
        vp = build_trade_bucket_volume_profile(
            str(display),
            scalp_cfg,
            reference_ts=reference_ts,
            require_fresh=require_fresh,
        )
        cvd = check_trade_bucket_cvd(
            str(display),
            scalp_cfg,
            reference_ts=reference_ts,
            require_fresh=require_fresh,
        )
        vp_fidelity = source_fidelity(
            vp.get("volume_source") if vp.get("valid") else vp.get("volume_source") or vp.get("source") or "unavailable",
            domain="vp",
        )
        cvd_fidelity = source_fidelity(cvd.get("source") or "unavailable", domain="cvd")
        vp_ok = bool(vp.get("valid") and vp_fidelity.get("uses_real_trade_buckets"))
        cvd_ok = bool(cvd_fidelity.get("uses_real_trade_buckets") and cvd.get("bucket_count"))
        reason = None
        if not vp_ok:
            reason = str(vp.get("reason") or "aggtrade_vp_unavailable")
        elif not cvd_ok:
            reason = str(cvd.get("reason") or "aggtrade_cvd_unavailable")
        out.update(
            {
                "aggtrade_available": bool(vp_ok and cvd_ok),
                "aggtrade_reason": reason,
                "aggtrade_profile": vp if vp_ok else None,
                "aggtrade_profile_source_tf": "aggtrade_session" if vp_ok else None,
                "aggtrade_cvd_direction": cvd.get("direction"),
                "aggtrade_cvd_source": cvd.get("source"),
                "aggtrade_cvd_slope": cvd.get("cvd_slope"),
                "aggtrade_cvd_bucket_count": cvd.get("bucket_count"),
                "engine_b_data_fidelity": {
                    "vp_source": vp_fidelity.get("source"),
                    "vp_fidelity": vp_fidelity.get("fidelity"),
                    "vp_uses_real_trade_buckets": bool(vp_fidelity.get("uses_real_trade_buckets")),
                    "vp_is_proxy": bool(vp_fidelity.get("is_proxy")),
                    "vp_bucket_count": vp.get("bucket_count"),
                    "cvd_source": cvd_fidelity.get("source"),
                    "cvd_fidelity": cvd_fidelity.get("fidelity"),
                    "cvd_uses_real_trade_buckets": bool(cvd_fidelity.get("uses_real_trade_buckets")),
                    "cvd_is_proxy": bool(cvd_fidelity.get("is_proxy")),
                    "cvd_bucket_count": cvd.get("bucket_count"),
                },
            }
        )
        return out
    except Exception as exc:
        log.debug("[ENGINE_B] aggTrade context unavailable for %s: %s", display, exc)
        out["aggtrade_reason"] = "trade_bucket_error"
        return out


def sanitize_engine_b_structure_profile_fields(
    structure: dict | None, asset_type: str, cfg: dict | None = None
) -> dict:
    """Remove untrusted VP levels from Engine B structure payloads (AI review path)."""
    out = dict(structure) if isinstance(structure, dict) else {}
    vp_ctx = build_engine_b_profile_vp_context(asset_type, cfg)
    out["profile_vp_context"] = vp_ctx
    if vp_ctx.get("enabled"):
        return out
    for key in (
        "prev_session_profile_valid",
        "prev_session_poc",
        "prev_session_vah",
        "prev_session_val",
        "prev_session_profile_high",
        "prev_session_profile_low",
        "prev_session_total_volume",
        "prev_session_start",
        "prev_session_end",
        "prev_session_profile_source_tf",
        "profile_in_play",
        "profile_level_in_play",
        "inside_prev_value_area",
        "above_prev_value_area",
        "below_prev_value_area",
        "touched_poc",
        "touched_vah",
        "touched_val",
        "rejected_from_poc",
        "rejected_from_vah",
        "rejected_from_val",
        "accepted_at_poc",
        "accepted_inside_value",
        "returned_to_value",
        "failed_return_to_value",
        "profile_bias",
        "profile_reaction_strength",
        "profile_notes",
    ):
        if key.endswith("_valid"):
            out[key] = False
        elif key in {"profile_in_play", "inside_prev_value_area", "above_prev_value_area",
                     "below_prev_value_area", "touched_poc", "touched_vah", "touched_val",
                     "rejected_from_poc", "rejected_from_vah", "rejected_from_val",
                     "accepted_at_poc", "accepted_inside_value", "returned_to_value",
                     "failed_return_to_value"}:
            out[key] = False
        elif key in {"profile_reaction_strength"}:
            out[key] = 0.0
        elif key in {"profile_bias"}:
            out[key] = "neutral"
        elif key in {"profile_notes"}:
            out[key] = ""
        else:
            out[key] = None
    return out


def _engine_b_resolve_min_score_override(
    score_group: str | None, style: str | None, cfg: dict | None = None,
) -> float | None:
    """Look up per-(group,style) min_score from score_group_overrides.

    Returns the override value when set, None when no override exists (caller
    falls back to the global style floor).
    """
    raw_cfg = cfg if isinstance(cfg, dict) else config.CONFIG
    group_key = str(score_group or "").strip().lower()
    style_key = str(style or "").strip().lower()
    if not group_key or not style_key:
        return None
    group_overrides = (raw_cfg.get("NAKED_ENGINE", {}) or {}).get("score_group_overrides", {}) or {}
    if not isinstance(group_overrides, dict):
        return None
    group_entry = group_overrides.get(group_key, {})
    if not isinstance(group_entry, dict):
        return None
    style_overrides = group_entry.get(style_key, {})
    if not isinstance(style_overrides, dict):
        return None
    if "min_score" not in style_overrides:
        return None
    try:
        return float(style_overrides["min_score"])
    except (TypeError, ValueError):
        return None


def engine_b_min_score_diagnostics(
    style_profile: dict | None, regime_label: str | None, asset_type: str = ""
) -> dict:
    """Return report-only Engine B min-score scaling diagnostics.

    Fields: ``base_min_score``, ``regime_multiplier``, ``scaled_min_score``,
    ``multipliers_enabled`` (mirrors ``ENGINE_B_REGIME_MULTIPLIERS_ENABLED``).
    """
    profile = style_profile if isinstance(style_profile, dict) else {}
    base_min = float(profile.get("min_score", 0.0) or 0.0)
    if bool(config.CONFIG.get("ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED", False)):
        style = str(profile.get("style") or "").lower()
        style_floors = config.CONFIG.get("ENGINE_B_STYLE_MIN_SCORE_BY_STYLE", {}) or {}
        try:
            style_floor = float(style_floors.get(style, base_min))
        except (TypeError, ValueError):
            style_floor = base_min
        # C3: per-(group,style) min_score override in score_group_overrides wins
        # over the global style floor when set. Without this, the style floor
        # silently overrode group-level min_score overrides (e.g. nat_gas,
        # crypto_doge) — audit MED #6.
        _group_override = _engine_b_resolve_min_score_override(
            profile.get("score_group"), style
        )
        base_min = _group_override if _group_override is not None else style_floor
    multipliers_enabled = bool(config.CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS_ENABLED", True))
    regime_multiplier = _engine_b_regime_gate(regime_label, asset_type)
    scaled = base_min * regime_multiplier
    if scaled <= 0:
        scaled_min = 0.0
    else:
        scaled_min = float(math.ceil((scaled * 10.0) - 1e-12) / 10.0)
    return {
        "base_min_score": base_min,
        "regime_multiplier": float(regime_multiplier),
        "scaled_min_score": scaled_min,
        "multipliers_enabled": multipliers_enabled,
    }


def engine_b_min_score_threshold(
    style_profile: dict | None, regime_label: str | None, asset_type: str = ""
) -> float:
    """Return the scaled Engine B score floor for a style/regime combination."""
    return float(
        engine_b_min_score_diagnostics(
            style_profile, regime_label, asset_type
        )["scaled_min_score"]
    )


# FIX 10: Per-gate failure histogram (module-level)
_engine_b_gate_failures: dict[str, int] = {
    "structure_ok": 0,
    "location_ok": 0,
    "entry_ok": 0,
    "room_ok": 0,
    "rr_ok": 0,
    "macro_ok": 0,
}


def _log_gate_failure(gate_name: str, reason: str = "") -> None:
    """Log a gate failure for monitoring (FIX 10)."""
    _engine_b_gate_failures[gate_name] = _engine_b_gate_failures.get(gate_name, 0) + 1
    log.debug("Gate failure: %s — %s", gate_name, reason)


def _get_engine_b_gate_failure_summary() -> dict[str, int]:
    """Return current gate failure counts."""
    return dict(_engine_b_gate_failures)


def _reset_engine_b_gate_failures() -> None:
    """Reset gate failure counters (call at start of each scan cycle)."""
    for k in _engine_b_gate_failures:
        _engine_b_gate_failures[k] = 0


def _find_breakout_bar_index(
    candles: list,
    direction: str,
    level: float | None,
    lookback: int = 20,
) -> int | None:
    """Locate the most recent bar whose close crossed beyond ``level``.

    A breakout bar is one that closed beyond the broken structural level while
    the previous bar's close was still inside it. Needed because
    ``_breakout_follow_through`` evaluates the bars *after* the breakout — the
    last bar has no follow-through window by definition.
    """
    if level is None or not candles:
        return None
    try:
        _level = float(level)
    except (TypeError, ValueError):
        return None
    n = len(candles)
    start = max(1, n - max(1, int(lookback)))
    for i in range(n - 1, start - 1, -1):
        try:
            close_i = float(candles[i].get("close", 0.0))
            close_prev = float(candles[i - 1].get("close", 0.0))
        except (AttributeError, TypeError, ValueError):
            continue
        if direction == "LONG" and close_i > _level >= close_prev:
            return i
        if direction == "SHORT" and close_i < _level <= close_prev:
            return i
    return None


def _breakout_follow_through(
    candles: list,
    direction: str,
    atr: float,
    breakout_bar_index: int | None = None,
) -> dict:
    """Evaluate breakout quality by checking follow-through over next 2-3 bars.

    SMC traders frequently get caught in false breakouts where price breaks a
    level but immediately reverses. This factor scores the *quality* of the
    breakout by measuring post-break continuation.

    Scoring:
      +1.5  Strong follow-through — 3+ bars all aligned, close beyond breakout
      +1.0  Moderate — 2 bars aligned, close near breakout level
       0.0  Neutral — mixed or insufficient data
      -0.5  Trap — immediate reversal within 1-2 bars, long wick, close back inside

    Returns dict with score, confidence, and diagnostic details.
    """
    if not candles or len(candles) < 5:
        return {"score": 0.0, "confidence": "insufficient_data", "bars_checked": 0}

    # Default to last bar if no breakout index provided
    idx = breakout_bar_index if breakout_bar_index is not None else len(candles) - 1
    if idx < 0 or idx >= len(candles):
        idx = len(candles) - 1

    breakout = candles[idx]
    _open = float(breakout.get("open", 0.0))
    _high = float(breakout.get("high", 0.0))
    _low = float(breakout.get("low", 0.0))
    _close = float(breakout.get("close", 0.0))
    _range = max(_high - _low, 1e-12)
    _body = abs(_close - _open)

    # Wick analysis on breakout bar
    upper_wick = _high - max(_open, _close)
    lower_wick = min(_open, _close) - _low

    is_long = direction == "LONG"

    # Wick ratio: long opposing wick = potential trap
    _wick_ratio = 0.0
    if is_long:
        _wick_ratio = upper_wick / _range if _range > 0 else 0.0
    else:
        _wick_ratio = lower_wick / _range if _range > 0 else 0.0

    # Check next 3 bars for follow-through
    _max_check = min(3, len(candles) - idx - 1)
    _aligned_bars = 0
    _reversal_bars = 0
    _close_beyond_count = 0
    _avg_close_pos = 0.0

    for i in range(1, _max_check + 1):
        bar = candles[idx + i]
        c_open = float(bar.get("open", 0.0))
        c_high = float(bar.get("high", 0.0))
        c_low = float(bar.get("low", 0.0))
        c_close = float(bar.get("close", 0.0))

        if is_long:
            # Bar aligned if close > breakout close
            if c_close > _close:
                _aligned_bars += 1
                if c_close > _high:
                    _close_beyond_count += 1
            # Reversal if close drops below breakout open
            if c_close < _open:
                _reversal_bars += 1
            _avg_close_pos += (c_close - _low) / max(c_high - c_low, 1e-12)
        else:
            if c_close < _close:
                _aligned_bars += 1
                if c_close < _low:
                    _close_beyond_count += 1
            if c_close > _open:
                _reversal_bars += 1
            _avg_close_pos += (c_high - c_close) / max(c_high - c_low, 1e-12)

    if _max_check > 0:
        _avg_close_pos /= _max_check

    # Score determination
    _score = 0.0
    _confidence = "neutral"

    if _max_check == 0:
        _score = 0.0
        _confidence = "insufficient_data"
    elif _reversal_bars >= 2 and _aligned_bars == 0:
        # Immediate full reversal — trap
        _score = -0.5
        _confidence = "trap"
    elif _wick_ratio > 0.6 and _aligned_bars < 2:
        # Long opposing wick + weak follow-through = likely trap
        _score = -0.5
        _confidence = "trap"
    elif _aligned_bars >= 3 and _close_beyond_count >= 2:
        # Strong continuation across all checked bars
        _score = 1.5
        _confidence = "strong"
    elif _aligned_bars >= 2 and _close_beyond_count >= 1:
        # Moderate continuation
        _score = 1.0
        _confidence = "moderate"
    elif _aligned_bars >= 1 and _reversal_bars == 0:
        # Weak but present continuation
        _score = 0.5
        _confidence = "weak"
    elif _reversal_bars >= 1 and _aligned_bars >= 1:
        # Mixed — neutral
        _score = 0.0
        _confidence = "mixed"
    else:
        _score = 0.0
        _confidence = "neutral"

    return {
        "score": round(_score, 2),
        "confidence": _confidence,
        "bars_checked": _max_check,
        # M-4: distinguish "too recent to evaluate" (0 bars after the breakout, score
        # is a non-signal neutral) from a genuine no-follow-through verdict.
        "follow_through_evaluable": _max_check > 0,
        "aligned_bars": _aligned_bars,
        "reversal_bars": _reversal_bars,
        "close_beyond_count": _close_beyond_count,
        "wick_ratio": round(_wick_ratio, 4),
        "avg_close_position": round(_avg_close_pos, 4),
    }


def engine_b_confidence_passes(
    conf_data: dict | None,
    style_profile: dict | None,
    regime_label: str | None,
    asset_type: str = "",
    diagnostics_context: dict | None = None,
) -> tuple[bool, float]:
    """Return final Engine B gate status including the style/regime score floor."""
    min_score_scaled = engine_b_min_score_threshold(
        style_profile, regime_label, asset_type
    )
    conf = conf_data if isinstance(conf_data, dict) else {}
    try:
        score = float(conf.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    score_floor_ok = min_score_scaled <= 0 or score >= min_score_scaled
    passed = bool(conf.get("passed", False)) and score_floor_ok
    try:
        from calibration_diagnostics import (
            build_engine_b_calibration_row,
            record_calibration_diagnostic,
        )

        record_calibration_diagnostic(
            build_engine_b_calibration_row(
                conf=conf,
                style_profile=style_profile,
                regime_label=regime_label,
                asset_type=asset_type,
                scaled_min_score=min_score_scaled,
                passed=passed,
                diagnostics_context=diagnostics_context,
            )
        )
    except Exception as exc:
        log.debug("[CALIBRATION-DIAG] Engine B diagnostic skipped: %s", exc)
    return passed, min_score_scaled


def _normalise_research_candles(candles: list | None) -> list:
    rows = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        row = dict(c)
        if row.get("vol") is None and row.get("volume") is not None:
            row["vol"] = row.get("volume")
        rows.append(row)
    return rows


# get_pair_score_group() uses e.g. "precious_trackers" for XAU/XAG; ENGINE_B_RESEARCH_LAB_FACTORS
# GROUPS uses "metals" per CLAUDE.md. Map known aliases so config keys stay stable.
_ENGINE_B_RESEARCH_LAB_GROUP_ALIASES = {
    "precious_trackers": "metals",
    "pgm_metals": "metals",
}


def _infer_engine_b_research_group(res: dict, profile: dict) -> str:
    score_group = str(profile.get("score_group") or "").strip()
    if score_group:
        return _ENGINE_B_RESEARCH_LAB_GROUP_ALIASES.get(score_group, score_group)
    asset_type = str(res.get("asset_type") or "").lower()
    return asset_type or "unknown"


def _engine_b_micro_breakout_value(direction: str, candles: list, range_bars: int) -> dict:
    if len(candles) < range_bars + 2:
        return {"passed": False, "signal": "insufficient_candles"}
    window = candles[-(range_bars + 2):-2]
    prior_high = max(float(c.get("high", 0.0)) for c in window)
    prior_low = min(float(c.get("low", 0.0)) for c in window)
    prev_close = float(candles[-2].get("close", 0.0))
    close = float(candles[-1].get("close", 0.0))
    if direction == "LONG":
        passed = prev_close <= prior_high and close > prior_high
        level = prior_high
    else:
        passed = prev_close >= prior_low and close < prior_low
        level = prior_low
    return {
        "passed": bool(passed),
        "signal": "micro_breakout" if passed else "no_break",
        "level": round(level, 8),
        "close": round(close, 8),
    }


def _engine_b_vwap_reclaim_value(direction: str, candles: list, atr_val: float, band_std: float) -> dict:
    if len(candles) < 20:
        return {"passed": False, "signal": "insufficient_candles"}
    try:
        from indicators import calc_vwap

        vwap = calc_vwap(candles, band_mult=band_std)
        values = vwap.get("vwap") or []
        if len(values) < 2 or values[-1] is None or values[-2] is None:
            return {"passed": False, "signal": "missing_vwap"}
        band = max(float(atr_val), 1e-10) * band_std
        prev_close = float(candles[-2].get("close", 0.0))
        close = float(candles[-1].get("close", 0.0))
        prev_vwap = float(values[-2])
        last_vwap = float(values[-1])
        if direction == "LONG":
            trigger = prev_close <= prev_vwap + band and close > last_vwap + band
        else:
            trigger = prev_close >= prev_vwap - band and close < last_vwap - band
        return {
            "passed": bool(trigger),
            "signal": "vwap_reclaim" if trigger else "no_reclaim",
            "vwap": round(last_vwap, 8),
            "close": round(close, 8),
        }
    except Exception as exc:
        # L-5: fail closed but make the indicator-import/compute failure observable.
        log.debug("[ENGINE_B] vwap_reclaim value failed: %s", exc)
        return {"passed": False, "signal": "error", "error": str(exc)}


def _engine_b_cvd_momentum_value(direction: str, candles: list) -> dict:
    if len(candles) < 20:
        return {"passed": False, "signal": "insufficient_candles"}
    try:
        from indicators import calc_cvd, calc_vwap

        cvd = calc_cvd(candles, smooth_period=5)
        smoothed = cvd.get("smoothed_delta") or []
        vwap_values = (calc_vwap(candles).get("vwap") or [])
        if len(smoothed) < 2 or not vwap_values or vwap_values[-1] is None:
            return {"passed": False, "signal": "missing_cvd_or_vwap"}
        prev_delta = float(smoothed[-2] or 0.0)
        last_delta = float(smoothed[-1] or 0.0)
        close = float(candles[-1].get("close", 0.0))
        last_vwap = float(vwap_values[-1])
        if direction == "LONG":
            passed = last_delta > prev_delta and close > last_vwap
        else:
            passed = last_delta < prev_delta and close < last_vwap
        return {
            "passed": bool(passed),
            "signal": "cvd_momentum" if passed else "no_momentum",
            "delta": round(last_delta, 4),
            "prev_delta": round(prev_delta, 4),
            "vwap": round(last_vwap, 8),
        }
    except Exception as exc:
        # L-5: fail closed but make the indicator-import/compute failure observable.
        log.debug("[ENGINE_B] cvd_momentum value failed: %s", exc)
        return {"passed": False, "signal": "error", "error": str(exc)}


def _engine_b_vwap_deviation_value(direction: str, candles: list, std_threshold: float) -> dict:
    if len(candles) < 25:
        return {"passed": False, "signal": "insufficient_candles"}
    try:
        from indicators import calc_vwap

        closes = pd.Series([float(c.get("close", 0.0)) for c in candles])
        std = float(closes.rolling(20).std().iloc[-1] or 0.0)
        vwap_values = calc_vwap(candles).get("vwap") or []
        if not vwap_values or vwap_values[-1] is None or std <= 0:
            return {"passed": False, "signal": "missing_vwap_or_std"}
        close = float(closes.iloc[-1])
        last_vwap = float(vwap_values[-1])
        upper = last_vwap + std_threshold * std
        lower = last_vwap - std_threshold * std
        if direction == "LONG":
            passed = close <= lower
        else:
            passed = close >= upper
        return {
            "passed": bool(passed),
            "signal": "value_deviation" if passed else "inside_vwap_band",
            "vwap": round(last_vwap, 8),
            "close": round(close, 8),
            "upper": round(upper, 8),
            "lower": round(lower, 8),
        }
    except Exception as exc:
        return {"passed": False, "signal": "error", "error": str(exc)}


def _engine_b_research_lab_candidate_gates(
    res: dict,
    direction: str,
    entry_candles: list | None,
    current_price: float,
    atr_val: float,
    profile: dict,
) -> dict:
    cfg = config.CONFIG.get("ENGINE_B_RESEARCH_LAB_FACTORS", {}) or {}
    if not cfg.get("ENABLED", False):
        return {"enabled": False, "entry_ok": False, "location_ok": False}
    score_group = _infer_engine_b_research_group(res, profile)
    group_cfg = cfg.get("GROUPS", {}) or {}
    allowed = group_cfg.get(score_group, group_cfg.get(str(res.get("asset_type") or "").lower(), []))
    if not allowed:
        return {
            "enabled": True,
            "score_group": score_group,
            "allowed": [],
            "entry_ok": False,
            "location_ok": False,
            "reason": "no_group_factors",
        }
    candles = _normalise_research_candles(entry_candles)
    if not candles:
        candles = _normalise_research_candles(res.get("entry_candles"))
    if not candles or len(candles) < 10:
        return {
            "enabled": True,
            "score_group": score_group,
            "allowed": list(allowed),
            "entry_ok": False,
            "location_ok": False,
            "reason": "insufficient_candles",
        }

    range_bars = int(cfg.get("MICRO_BREAKOUT_RANGE_BARS", 6) or 6)
    band_std = float(cfg.get("VWAP_RECLAIM_BAND_ATR", 1.0) or 1.0)
    deviation_std = float(cfg.get("VWAP_DEVIATION_STD", 2.0) or 2.0)
    components: dict[str, dict] = {}
    entry_ok = False
    location_ok = False
    for name in allowed:
        name = str(name)
        if name == "micro_breakout":
            detail = _engine_b_micro_breakout_value(direction, candles, range_bars)
            entry_ok = entry_ok or bool(detail.get("passed"))
        elif name == "vwap_reclaim":
            detail = _engine_b_vwap_reclaim_value(direction, candles, atr_val, band_std)
            entry_ok = entry_ok or bool(detail.get("passed"))
        elif name == "cvd_momentum":
            detail = _engine_b_cvd_momentum_value(direction, candles)
            entry_ok = entry_ok or bool(detail.get("passed"))
        elif name == "vwap_deviation":
            detail = _engine_b_vwap_deviation_value(direction, candles, deviation_std)
            location_ok = location_ok or bool(detail.get("passed"))
        else:
            detail = {"passed": False, "signal": "unknown_candidate"}
        components[name] = detail

    allow_gate_upgrade = bool(cfg.get("ALLOW_GATE_UPGRADE", False))
    return {
        "enabled": True,
        "score_group": score_group,
        "allowed": list(allowed),
        "allow_gate_upgrade": allow_gate_upgrade,
        "entry_ok": bool(entry_ok and allow_gate_upgrade),
        "location_ok": bool(location_ok and allow_gate_upgrade),
        "raw_entry_ok": bool(entry_ok),
        "raw_location_ok": bool(location_ok),
        "components": components,
        "current_price": round(float(current_price), 8),
    }


ENGINE_B_LEVEL_COHORTS = {
    "structural_sl_structural_tp",
    "atr_sl_structural_tp",
    "atr_sl_fallback_rr_tp",
    "structural_sl_fallback_rr_tp",
}


def _engine_b_analyze_structure_error_result(
    precompute: dict,
    *,
    direction: str | None = None,
) -> dict:
    """Stable diagnostic payload when structure precompute cannot run."""
    conflict_window = _engine_b_d1_conflict_window_atr_mult()
    return {
        "structural_verdict": "ERROR",
        "reason": str(precompute.get("_error") or "unknown"),
        "direction": direction,
        "asset_type": precompute.get("asset_type", ""),
        "d1_adx": None,
        "h4_adx": None,
        "d1_pd_array_conflict": False,
        "d1_conflict_details": [],
        "d1_conflict_metric_details": [],
        "d1_pd_array_conflict_window_atr_mult": conflict_window,
        "recommended_stop_loss": None,
        "recommended_take_profit": None,
        "tp_source": None,
        "tp_structural_limited": False,
        "current_price": None,
        "structural_target_candidates": [],
        "selected_structural_target": None,
        "nearest_target_type": None,
        "nearest_target_price": None,
        "fallback_tp_applied": False,
        "fallback_tp_reason": None,
        "level_mode": "error",
        "level_cohort": "unknown_level_mode",
        "atr": None,
        "d1_atr": None,
        "struct_atr": None,
        "zone_atr": None,
        "forming_strip_diagnostics": precompute.get("forming_strip_diagnostics"),
    }


def engine_b_level_cohort(level_mode: str | None) -> str:
    """Normalize execution-level source modes into stable reporting cohorts."""
    mode = str(level_mode or "")
    return mode if mode in ENGINE_B_LEVEL_COHORTS else "unknown_level_mode"


def aggregate_engine_b_level_cohorts(rows: list[dict] | tuple[dict, ...]) -> dict[str, dict]:
    """Aggregate report-only Engine B performance diagnostics by level cohort."""
    summary: dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cohort = engine_b_level_cohort(row.get("level_cohort") or row.get("level_mode"))
        bucket = summary.setdefault(
            cohort,
            {"count": 0, "passed": 0, "failed": 0, "rr_actual_sum": 0.0, "rr_actual_count": 0},
        )
        bucket["count"] += 1
        if row.get("passed") is True or row.get("rr_passed") is True:
            bucket["passed"] += 1
        elif row.get("passed") is False or row.get("rr_passed") is False:
            bucket["failed"] += 1
        try:
            rr_actual = float(row.get("rr_actual"))
        except (TypeError, ValueError):
            rr_actual = None
        if rr_actual is not None:
            bucket["rr_actual_sum"] += rr_actual
            bucket["rr_actual_count"] += 1
    for bucket in summary.values():
        count = int(bucket.get("rr_actual_count") or 0)
        bucket["avg_rr_actual"] = round(bucket["rr_actual_sum"] / count, 4) if count else None
    return summary


def _engine_b_sl_distance_pct(entry: float, sl: float | None) -> float | None:
    try:
        _entry = float(entry)
        _sl = float(sl)
    except (TypeError, ValueError):
        return None
    if _entry <= 0 or _sl is None:
        return None
    return abs(_entry - _sl) / abs(_entry)


def _engine_b_resolve_max_sl_pct(
    asset_class: str, pair: str | None = None, score_group: str | None = None
) -> tuple[float, str]:
    from risk_engine import resolve_max_sl_pct

    _asset = str(asset_class or "forex").lower()
    sig_ctx: dict = {"type": _asset, "asset_type": _asset}
    if pair:
        sig_ctx["pair"] = pair
        sig_ctx["display"] = pair
    if score_group:
        sig_ctx["score_group"] = score_group
    return resolve_max_sl_pct(sig_ctx, _asset, config.CONFIG)


def _engine_b_sl_within_max_sl_pct(
    entry: float,
    sl: float | None,
    max_sl_pct: float | None,
) -> bool:
    if max_sl_pct is None or sl is None:
        return True
    pct = _engine_b_sl_distance_pct(entry, sl)
    if pct is None:
        return False
    return pct <= float(max_sl_pct) + 1e-12


def _engine_b_clamp_sl_to_max_pct(
    entry: float,
    direction: str,
    max_sl_pct: float,
) -> float:
    """Tightest allowed SL at the max SL width cap (risk_engine aligned)."""
    max_dist = float(entry) * float(max_sl_pct)
    if direction == "LONG":
        return float(entry) - max_dist
    return float(entry) + max_dist


def _synthesize_tp_from_sl(
    entry: float,
    exec_sl: float,
    direction: str,
    fallback_rr,
    min_rr,
) -> tuple[float | None, float]:
    """Compute synthetic TP from SL distance * target RR.

    Returns (tp, target_rr) on success, (None, 0.0) when inputs are invalid.
    """
    try:
        _fb = float(fallback_rr)
    except (TypeError, ValueError):
        return None, 0.0
    if _fb <= 0:
        return None, 0.0
    try:
        _mr = float(min_rr) if min_rr is not None else _fb
    except (TypeError, ValueError):
        _mr = _fb
    _sl_dist = abs(entry - exec_sl)
    if _sl_dist <= 0:
        return None, 0.0
    _target_rr = max(_fb, _mr)
    tp = entry + (_sl_dist * _target_rr) if direction == "LONG" else entry - (_sl_dist * _target_rr)
    return tp, _target_rr


def resolve_engine_b_execution_levels(
    *,
    direction: str,
    entry: float,
    structural_sl,
    structural_tp,
    atr: float,
    style: str = "intraday",
    asset_class: str = "forex",
    min_rr: float | None = None,
    fallback_rr: float | None = None,
    pair: str | None = None,
    score_group: str | None = None,
) -> dict:
    """Resolve final Engine B execution SL/TP for RR gating.

    Priority:
    - SL : ATR-based execution SL (fallback: structural SL).
    - TP : structural TP when valid (preserves structural targets including crypto);
           fallback: ATR TP when structural is missing or invalid.

    Using ATR SL + structural TP accurately reflects execution reality:
    - mechanical stop placement at ATR distance
    - profit-target at the structural level price action identified

    Returns dict with structural, execution, and gate fields.
    """
    _style = (style or "intraday").lower()
    _asset = (asset_class or "forex").lower()

    def _safe_float(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    try:
        _entry = float(entry) if entry is not None else 0.0
    except (TypeError, ValueError):
        _entry = 0.0
    try:
        _atr = float(atr) if atr is not None else 0.0
    except (TypeError, ValueError):
        _atr = 0.0

    _struct_sl = _safe_float(structural_sl)
    _struct_tp = _safe_float(structural_tp)

    _struct_sl_valid = bool(
        _struct_sl is not None and _entry > 0
        and ((direction == "LONG" and _struct_sl < _entry)
             or (direction == "SHORT" and _struct_sl > _entry))
    )
    _struct_tp_valid = bool(
        _struct_tp is not None and _entry > 0
        and ((direction == "LONG" and _struct_tp > _entry)
             or (direction == "SHORT" and _struct_tp < _entry))
    )

    _structural_rr = 0.0
    if _struct_sl_valid and _struct_tp_valid:
        _sd = abs(_entry - _struct_sl)
        _td = abs(_struct_tp - _entry)
        _structural_rr = _td / _sd if _sd > 0 else 0.0

    if _entry <= 0 or _atr <= 0:
        _reject = "invalid_entry" if _entry <= 0 else "invalid_atr"
        _structural_target_distance_atr = (
            round(abs(_struct_tp - _entry) / _atr, 4)
            if _struct_tp is not None and _entry > 0 and _atr > 0
            else None
        )
        return {
            "entry": _entry,
            "structural_sl": _struct_sl,
            "structural_tp": _struct_tp,
            "structural_rr": round(_structural_rr, 4),
            "structural_sl_valid": _struct_sl_valid,
            "structural_tp_valid": _struct_tp_valid,
            "structural_tp_available": _struct_tp is not None,
            "execution_sl": None,
            "execution_tp": None,
            "execution_tp1": None,
            "execution_tp2": None,
            "execution_rr": 0.0,
            "execution_rr1": 0.0,
            "execution_rr2": 0.0,
            "rr_used_for_gate": 0.0,
            "rr_source": _reject,
            "level_mode": _reject,
            "level_cohort": "unknown_level_mode",
            "sl_source": None,
            "tp_source": None,
            "tp1_source": None,
            "tp2_source": None,
            "exit_strategy": "invalid_levels",
            "runner_tp_requires_structural_break": False,
            "execution_levels_valid": False,
            "execution_level_reject_reason": _reject,
            "execution_tp_side_ok": False,
            "fallback_tp_applied": False,
            "fallback_tp_reason": None,
            "synthetic_rr_tp_used": False,
            "rr_required": _safe_float(min_rr),
            "rr_actual": 0.0,
            "rr_passed": False,
            "structural_target_distance_atr": _structural_target_distance_atr,
            "stop_distance_atr": None,
        }

    # ATR execution SL
    _style_mults = (config.CONFIG.get("STYLE_ATR_MULTS") or {}).get(_style, {})
    _m = _style_mults.get(_asset) or (config.CONFIG.get("ATR_CLASS") or {}).get(_asset, {})

    _atr_sl = None
    _atr_tp = None
    _atr_sl_valid = False
    _atr_reject = None

    if _m and _atr > 0 and _entry > 0:
        _sl_mult = float(_m.get("sl", 1.5))
        _tp_mult = float(_m.get("tp1", 2.5))
        if direction == "LONG":
            _atr_sl = _entry - _atr * _sl_mult
            _atr_tp = _entry + _atr * _tp_mult
        else:
            _atr_sl = _entry + _atr * _sl_mult
            _atr_tp = _entry - _atr * _tp_mult

        _atr_sl_valid = (direction == "LONG" and _atr_sl < _entry) or (direction == "SHORT" and _atr_sl > _entry)
        if not _atr_sl_valid:
            _atr_reject = "atr_sl_wrong_side"
    else:
        _atr_reject = "no_atr_config_or_zero_atr"

    _enforce_max_sl = bool(config.CONFIG.get("ENGINE_B_ENFORCE_MAX_SL_PCT", True))
    _max_sl_pct = None
    _max_sl_source = None
    if _enforce_max_sl and _entry > 0:
        _max_sl_pct, _max_sl_source = _engine_b_resolve_max_sl_pct(
            _asset, pair=pair, score_group=score_group
        )

    # Select execution SL.
    # Preference order:
    #   1. Keep structural SL when structural levels already satisfy min_rr.
    #      This preserves structural protection (swing-anchored stops) instead
    #      of compressing the stop to ATR distance unnecessarily.
    #   2. Otherwise pick the tighter of ATR SL and structural SL — the
    #      original "wide structural SL → poor RR" fix, applied only when it
    #      is actually needed to clear the RR gate.
    # Tighter = closer to entry (LONG: higher value; SHORT: lower value).
    _keep_structural_sl = (
        _struct_sl_valid
        and _struct_tp_valid
        and min_rr is not None
        and _structural_rr >= float(min_rr)
        and _engine_b_sl_within_max_sl_pct(_entry, _struct_sl, _max_sl_pct)
    )
    if _keep_structural_sl:
        _exec_sl = _struct_sl
        _sl_source = "structural"
    elif _atr_sl_valid and _struct_sl_valid:
        if direction == "LONG":
            _exec_sl = max(_atr_sl, _struct_sl)
        else:
            _exec_sl = min(_atr_sl, _struct_sl)
        _sl_source = "atr" if _exec_sl == _atr_sl else "structural"
    elif _atr_sl_valid:
        _exec_sl = _atr_sl
        _sl_source = "atr"
    elif _struct_sl_valid:
        _exec_sl = _struct_sl
        _sl_source = "structural"
    else:
        _exec_sl = _struct_sl
        _sl_source = "structural_invalid"

    _atr_sl_clamp_applied = None
    if (
        bool(config.CONFIG.get("ENGINE_B_ATR_SL_CLAMPS_ENABLED", True))
        and _exec_sl is not None
        and _entry > 0
        and _atr > 0
    ):
        _group_clamps = (
            (config.CONFIG.get("ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES") or {})
            .get(str(score_group or "").lower(), {})
        )
        try:
            _min_sl_atr = float(
                _group_clamps.get(
                    "min_sl_atr",
                    config.CONFIG.get("ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75),
                )
            )
        except (TypeError, ValueError):
            _min_sl_atr = 0.75
        try:
            _max_sl_atr = float(
                _group_clamps.get(
                    "max_sl_atr",
                    config.CONFIG.get("ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0),
                )
            )
        except (TypeError, ValueError):
            _max_sl_atr = 3.0
        _min_sl_atr = max(0.0, _min_sl_atr)
        _max_sl_atr = max(_min_sl_atr, _max_sl_atr)
        _dist_atr = abs(_entry - _exec_sl) / _atr
        if _dist_atr < _min_sl_atr or _dist_atr > _max_sl_atr:
            _target_dist_atr = min(max(_dist_atr, _min_sl_atr), _max_sl_atr)
            _exec_sl = (
                _entry - (_atr * _target_dist_atr)
                if direction == "LONG"
                else _entry + (_atr * _target_dist_atr)
            )
            _sl_source = f"{_sl_source}_atr_clamp"
            _level_mode = f"{_sl_source}_sl_structural_tp"
            _rr_source = _level_mode
            _atr_sl_clamp_applied = {
                "before_atr": round(_dist_atr, 4),
                "after_atr": round(_target_dist_atr, 4),
                "min_sl_atr": round(_min_sl_atr, 4),
                "max_sl_atr": round(_max_sl_atr, 4),
            }

    # Select execution TP: structural when valid, ATR fallback
    if _struct_tp_valid:
        _exec_tp = _struct_tp
        _tp_source = "structural"
    elif _atr_tp is not None:
        _exec_tp = _atr_tp
        _tp_source = "atr"
    else:
        _exec_tp = _struct_tp
        _tp_source = "structural_missing"

    _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
    _rr_source = _level_mode

    def _compute_exec_rr(_sl, _tp):
        _rr = 0.0
        _valid = False
        _reject = None
        _tp_side_ok = False
        if _sl is not None and _tp is not None and _entry > 0:
            _sl_ok = (direction == "LONG" and _sl < _entry) or (direction == "SHORT" and _sl > _entry)
            _tp_ok = (direction == "LONG" and _tp > _entry) or (direction == "SHORT" and _tp < _entry)
            _tp_side_ok = _tp_ok
            if _sl_ok and _tp_ok:
                _sd = abs(_entry - _sl)
                _td = abs(_tp - _entry)
                _rr = _td / _sd if _sd > 0 else 0.0
                _valid = True
            else:
                _reject = "sl_wrong_side" if not _sl_ok else "tp_wrong_side"
        else:
            _reject = "levels_missing"
        return _rr, _valid, _reject, _tp_side_ok

    # Compute execution RR
    _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(_exec_sl, _exec_tp)

    _fallback_applied = False
    _fallback_reason = None
    _structural_tp_below_min_rr = False
    # Synthetic fallback eligibility: a valid SL (correct side, non-zero distance)
    # is required to construct a TP from sl_dist * fallback_rr. We allow the
    # fallback to fire in two cases:
    #   1. exec is valid but RR below min_rr (legacy path)
    #   2. exec is invalid because of TP-only problems (missing or wrong-side TP)
    # Wrong-side SL is a fundamental direction error and is never recovered.
    _sl_valid_for_synthetic = (
        _exec_sl is not None
        and _entry > 0
        and (
            (direction == "LONG" and _exec_sl < _entry)
            or (direction == "SHORT" and _exec_sl > _entry)
        )
    )
    _tp_only_failure = (not _exec_valid) and _exec_reject in (
        "tp_wrong_side",
        "levels_missing",
    )
    if fallback_rr is not None and _sl_valid_for_synthetic and (
        _exec_valid or _tp_only_failure
    ):
        try:
            _min_rr = float(min_rr) if min_rr is not None else None
        except (TypeError, ValueError):
            _min_rr = None
        try:
            _fallback_rr = float(fallback_rr)
        except (TypeError, ValueError):
            _fallback_rr = 0.0
        _need_synthetic = (
            _min_rr is not None
            and _fallback_rr > 0
            and (
                (_exec_valid and _exec_rr < _min_rr)
                or _tp_only_failure
            )
        )
        if _need_synthetic:
            if _tp_only_failure:
                _fallback_reason = (
                    "missing_tp_synthesized"
                    if _exec_reject == "levels_missing"
                    else "wrong_side_tp_synthesized"
                )
            else:
                _fallback_reason = (
                    "structural_tp_below_min_rr"
                    if _tp_source == "structural"
                    else f"{_tp_source}_tp_below_min_rr"
                )
                _structural_tp_below_min_rr = _tp_source == "structural"
            if bool(config.CONFIG.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False)):
                _synth_tp, _ = _synthesize_tp_from_sl(_entry, _exec_sl, direction, _fallback_rr, _min_rr)
                if _synth_tp is not None:
                    _exec_tp = _synth_tp
                    _tp_source = "fallback_rr"
                    _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                    _rr_source = _level_mode
                    _fallback_applied = True
                    _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(
                        _exec_sl, _exec_tp
                    )
            elif _exec_valid:
                # Flag off and original exec was valid: keep legacy "force invalid" semantics.
                # For TP-only failures with flag off, leave the original reject reason.
                _exec_valid = False
                _exec_reject = _fallback_reason

    # MAX_SL enforcement — align Engine B execution SL with risk_engine gate.
    if (
        _enforce_max_sl
        and _max_sl_pct is not None
        and _exec_sl is not None
        and _entry > 0
    ):
        _cur_sl_pct = _engine_b_sl_distance_pct(_entry, _exec_sl)
        if _cur_sl_pct is not None and _cur_sl_pct > float(_max_sl_pct) + 1e-12:
            if _atr_sl_valid and _engine_b_sl_within_max_sl_pct(_entry, _atr_sl, _max_sl_pct):
                _exec_sl = _atr_sl
                _sl_source = "atr_max_sl_clamp"
                _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                _rr_source = _level_mode
                _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(
                    _exec_sl, _exec_tp
                )
                if not _exec_tp_side_ok:
                    _exec_valid = False
                    _exec_reject = "tp_wrong_side"
                try:
                    _min_rr_after = float(min_rr) if min_rr is not None else None
                except (TypeError, ValueError):
                    _min_rr_after = None
                if (
                    fallback_rr is not None
                    and _sl_valid_for_synthetic
                    and bool(config.CONFIG.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False))
                    and _exec_valid
                    and _min_rr_after is not None
                    and _exec_rr + 1e-12 < _min_rr_after
                ):
                    _synth_tp, _ = _synthesize_tp_from_sl(_entry, _exec_sl, direction, fallback_rr, min_rr)
                    if _synth_tp is not None:
                        _exec_tp = _synth_tp
                        _tp_source = "fallback_rr"
                        _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                        _rr_source = _level_mode
                        _fallback_applied = True
                        _fallback_reason = "max_sl_clamp_tp_resynthesized"
                        _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(
                            _exec_sl, _exec_tp
                        )
            elif (
                _fallback_applied
                and bool(config.CONFIG.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False))
                and _max_sl_pct is not None
            ):
                # Preserve fallback-RR path: clamp SL to cap instead of nulling both levels.
                _exec_sl = _engine_b_clamp_sl_to_max_pct(_entry, direction, float(_max_sl_pct))
                _sl_source = "max_sl_clamp"
                _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                _rr_source = _level_mode
                _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(
                    _exec_sl, _exec_tp
                )
                if fallback_rr is not None and _exec_sl is not None:
                    _synth_tp, _ = _synthesize_tp_from_sl(_entry, _exec_sl, direction, fallback_rr, min_rr)
                    if _synth_tp is not None:
                        _exec_tp = _synth_tp
                        _tp_source = "fallback_rr"
                        _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                        _rr_source = _level_mode
                        _fallback_reason = "max_sl_clamp_tp_resynthesized"
                        _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(
                            _exec_sl, _exec_tp
                        )
            else:
                _exec_sl = None
                _exec_tp = None
                _exec_valid = False
                _exec_reject = "max_sl_exceeded"
                _exec_rr = 0.0
                _level_mode = "max_sl_exceeded"
                _rr_source = "max_sl_exceeded"

    _asset_class_lower = str(asset_class or "").lower()
    if (
        _exec_valid
        and _exec_tp is not None
        and _entry > 0
        and _asset_class_lower
    ):
        try:
            from risk_engine import validate_tp_exchange_bounds

            _bounds_ok, _bounds_err = validate_tp_exchange_bounds(
                _entry, _exec_tp, _asset_class_lower, config.CONFIG
            )
        except Exception:
            _bounds_ok = True
            _bounds_err = None
        if not _bounds_ok:
            _synth_attempted = False
            if (
                fallback_rr is not None
                and _sl_valid_for_synthetic
                and bool(config.CONFIG.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False))
                and _exec_sl is not None
            ):
                _synth_tp, _ = _synthesize_tp_from_sl(_entry, _exec_sl, direction, fallback_rr, min_rr)
                if _synth_tp is not None:
                    _exec_tp = _synth_tp
                    _tp_source = "fallback_rr"
                    _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                    _rr_source = _level_mode
                    _fallback_applied = True
                    _fallback_reason = "tp_exchange_bounds_synthesized"
                    _synth_attempted = True
                    _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(
                        _exec_sl, _exec_tp
                    )
                    _bounds_ok, _bounds_err = validate_tp_exchange_bounds(
                        _entry, _exec_tp, _asset_class_lower, config.CONFIG
                    )
            if not _bounds_ok:
                _exec_valid = False
                _exec_reject = "tp_exchange_bounds"
                _exec_tp = None

    _execution_tp1 = _exec_tp
    _execution_tp2 = _exec_tp
    _execution_rr1 = _exec_rr
    _execution_rr2 = _exec_rr
    _tp1_source = _tp_source
    _tp2_source = _tp_source
    _exit_strategy = (
        f"single_{_tp_source}_tp"
        if _exec_valid and _exec_tp is not None and _tp_source
        else "invalid_levels"
    )
    _runner_tp_requires_structural_break = False
    if (
        _fallback_applied
        and _structural_tp_below_min_rr
        and _struct_tp_valid
        and _exec_sl is not None
        and _exec_tp is not None
    ):
        _tp1_rr, _tp1_valid, _, _ = _compute_exec_rr(_exec_sl, _struct_tp)
        if _tp1_valid:
            _execution_tp1 = _struct_tp
            _execution_tp2 = _exec_tp
            _execution_rr1 = _tp1_rr
            _execution_rr2 = _exec_rr
            _tp1_source = "structural"
            _tp2_source = "fallback_rr"
            _exit_strategy = "scale_out_structural_tp1_fallback_tp2"
            _runner_tp_requires_structural_break = True

    _structural_target_distance_atr = (
        round(abs(_struct_tp - _entry) / _atr, 4)
        if _struct_tp is not None and _entry > 0 and _atr > 0
        else None
    )
    _stop_distance_atr = (
        round(abs(_entry - _exec_sl) / _atr, 4)
        if _exec_sl is not None and _entry > 0 and _atr > 0
        else None
    )
    _rr_required = _safe_float(min_rr)
    _rr_passed = bool(_exec_valid and (_rr_required is None or _exec_rr >= _rr_required))
    _level_cohort = engine_b_level_cohort(_level_mode)

    return {
        "entry": _entry,
        "structural_sl": _struct_sl,
        "structural_tp": _struct_tp,
        "structural_rr": round(_structural_rr, 4),
        "structural_sl_valid": _struct_sl_valid,
        "structural_tp_valid": _struct_tp_valid,
        "structural_tp_available": _struct_tp is not None,
        "execution_sl": _exec_sl,
        "execution_tp": _exec_tp,
        "execution_tp1": _execution_tp1,
        "execution_tp2": _execution_tp2,
        "execution_rr": round(_exec_rr, 4),
        "execution_rr1": round(_execution_rr1, 4),
        "execution_rr2": round(_execution_rr2, 4),
        "rr_used_for_gate": round(_exec_rr, 4),
        "rr_source": _rr_source,
        "level_mode": _level_mode,
        "level_cohort": _level_cohort,
        "sl_source": _sl_source,
        "tp_source": _tp_source,
        "tp1_source": _tp1_source,
        "tp2_source": _tp2_source,
        "exit_strategy": _exit_strategy,
        "runner_tp_requires_structural_break": _runner_tp_requires_structural_break,
        "execution_levels_valid": _exec_valid,
        "execution_level_reject_reason": _exec_reject,
        "execution_tp_side_ok": _exec_tp_side_ok,
        "fallback_tp_applied": _fallback_applied,
        "fallback_tp_reason": _fallback_reason,
        "synthetic_rr_tp_used": bool(_tp_source == "fallback_rr" and _fallback_applied),
        "atr_sl_clamp_applied": _atr_sl_clamp_applied,
        "rr_required": _rr_required,
        "rr_actual": round(_exec_rr, 4),
        "rr_passed": _rr_passed,
        "structural_target_distance_atr": _structural_target_distance_atr,
        "stop_distance_atr": _stop_distance_atr,
    }


class NakedEngine:
    def __init__(self):
        self._registry_context = threading.local()

    @staticmethod
    def _compute_atr_from_candles(candles: list, period: int = 14, fallback: float | None = None) -> float:
        if not candles or len(candles) < 2:
            return float(fallback or 0.0)
        try:
            highs = [float(c["high"]) for c in candles]
            lows = [float(c["low"]) for c in candles]
            closes = [float(c["close"]) for c in candles]
            atr_series = calc_atr(highs, lows, closes, period)
        except (KeyError, TypeError, ValueError):
            return float(fallback or 0.0)
        valid = [float(v) for v in atr_series if v is not None and float(v) > 0]
        atr = valid[-1] if valid else 0.0
        return atr if atr > 0 else float(fallback or 0.0)

    @staticmethod
    def _swing_cache(
        highs: np.ndarray,
        lows: np.ndarray,
        atr: float,
        distance: int = 3,
        prominence_mult: float = 1.0,
    ) -> dict:
        if atr <= 0:
            return {"peak_idx": np.array([], dtype=int), "trough_idx": np.array([], dtype=int)}
        try:
            prominence_mult = max(1.0, float(prominence_mult or 1.0))
        except (TypeError, ValueError):
            prominence_mult = 1.0
        prominence = atr * 0.8 * prominence_mult
        peak_idx, _ = find_peaks(highs, prominence=prominence, distance=distance)
        trough_idx, _ = find_peaks(-lows, prominence=prominence, distance=distance)
        return {"peak_idx": peak_idx, "trough_idx": trough_idx}

    def set_registry_context(self, symbol: str | None):
        self._registry_context.symbol = (
            str(symbol).upper() if symbol is not None and str(symbol).strip() else None
        )
        return self

    def _consume_registry_symbol(self) -> str | None:
        symbol = getattr(self._registry_context, "symbol", None)
        self._registry_context.symbol = None
        return symbol

    def _registry_order_blocks(self, entries: list[dict]) -> list[dict]:
        obs = []
        for entry in entries:
            if entry.get("type") != "OB":
                continue
            obs.append({
                "type": entry.get("direction"),
                "top": float(entry.get("top", 0.0)),
                "bottom": float(entry.get("bottom", 0.0)),
                "strength": int(round(float(entry.get("strength", 0.0)))),
                "mitigated": bool(entry.get("mitigated", False)),
                "created_at": entry.get("created_at"),
                "mitigated_at": entry.get("mitigated_at"),
                "scan_count": int(entry.get("scan_count", 1)),
            })
        return obs

    def _registry_fvgs(self, entries: list[dict]) -> list[dict]:
        fvgs = []
        for entry in entries:
            if entry.get("type") != "FVG":
                continue
            top = float(entry.get("top", 0.0))
            bottom = float(entry.get("bottom", 0.0))
            fvgs.append({
                "type": entry.get("direction"),
                "top": top,
                "bottom": bottom,
                "size": round(abs(top - bottom), 6),
                "strength": float(entry.get("strength", abs(top - bottom))),
                "mitigated": bool(entry.get("mitigated", False)),
                "created_at": entry.get("created_at"),
                "mitigated_at": entry.get("mitigated_at"),
                "scan_count": int(entry.get("scan_count", 1)),
            })
        return fvgs

    def _find_zones(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        atr: float,
        regime: str,
        candles: list,
        structure_tf: str = "H4",
        asset_type: str = "",
    ):
        # Find resistance peaks
        # Prominence ensures we only get significant peaks, relative to ATR.
        # #3: the 1.5 multiplier is configurable (scalar or per-asset dict) via
        # NAKED_ENGINE.zone_prominence_atr_mult so volatile crypto vs. tight index
        # universes can be tuned independently.
        prominence_threshold = atr * _naked_engine_atr_mult(
            "zone_prominence_atr_mult", 1.5, asset_type
        )

        _zone_peak_dist = int(
            (config.CONFIG.get("ENGINE_B_ZONE_PEAK_DISTANCE", {}) or {})
            .get(structure_tf, 5)
        )
        peak_idx, _ = find_peaks(highs, prominence=prominence_threshold, distance=_zone_peak_dist)
        trough_idx, _ = find_peaks(-lows, prominence=prominence_threshold, distance=_zone_peak_dist)

        multipliers = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_multipliers", {})
        buf = multipliers.get(
            regime.upper(),
            multipliers.get("RANGING", {"upper": 0.5, "lower": 1.2, "sl": 1.0}),
        )

        # Calculate recent average volume for normalisation
        vols = np.array([float(c.get("vol", 0)) for c in candles], dtype=float)
        avg_volume_20 = (
            np.mean(vols[-20:]) if len(vols) >= 20 else (np.mean(vols) if len(vols) > 0 else 1.0)
        )
        if avg_volume_20 <= 0:
            avg_volume_20 = 1.0
        zone_vol_means = (
            pd.Series(vols).rolling(window=3, center=False, min_periods=1).mean().to_numpy()
            if len(vols) > 0
            else np.array([], dtype=float)
        )

        res_zones = []
        for idx in peak_idx:
            peak_price = highs[idx]

            # Average vol of 3 bars around the peak
            zone_vol = float(zone_vol_means[idx]) if idx < len(zone_vol_means) else 0.0
            vol_strength = min(1.0, zone_vol / avg_volume_20)

            # Zone expands below the peak (ceiling)
            res_zones.append(
                {
                    "upper": peak_price
                    + (atr * buf.get("upper", 0.5)),  # slight overshoot tolerance
                    "lower": peak_price
                    - (atr * buf.get("lower", 1.2)),  # buffer zone thickness
                    "center": peak_price,
                    "volume_strength": vol_strength,
                    # M-6: bars since the zone-forming pivot, so consumers can
                    # downweight stale zones. 0 = most recent bar.
                    "age_bars": int(len(highs) - 1 - idx),
                }
            )

        sup_zones = []
        for idx in trough_idx:
            trough_price = lows[idx]

            # Average vol of 3 bars around the trough
            zone_vol = float(zone_vol_means[idx]) if idx < len(zone_vol_means) else 0.0
            vol_strength = min(1.0, zone_vol / avg_volume_20)

            # Zone expands above the trough (floor)
            sup_zones.append(
                {
                    "lower": trough_price - (atr * buf.get("upper", 0.5)),
                    "upper": trough_price + (atr * buf.get("lower", 1.2)),
                    "center": trough_price,
                    "volume_strength": vol_strength,
                    # M-6: bars since the zone-forming pivot (0 = most recent bar).
                    "age_bars": int(len(lows) - 1 - idx),
                }
            )

        return res_zones, sup_zones

    def _determine_sequence(
        self, highs: np.ndarray, lows: np.ndarray, atr: float, direction: str, swings: dict | None = None
    ) -> dict:
        """Finds the most recent swings to determine HH/HL or LH/LL sequence."""
        swings = swings if isinstance(swings, dict) else self._swing_cache(highs, lows, atr)
        peak_idx = swings.get("peak_idx", [])
        trough_idx = swings.get("trough_idx", [])

        last_peaks = [highs[i] for i in peak_idx[-3:]] if len(peak_idx) > 0 else []
        last_troughs = [lows[i] for i in trough_idx[-3:]] if len(trough_idx) > 0 else []

        sequence = "RANGING"
        if len(last_peaks) >= 2 and len(last_troughs) >= 2:
            if last_peaks[-1] > last_peaks[-2] and last_troughs[-1] > last_troughs[-2]:
                sequence = "HH_HL"  # Uptrend structure
            elif last_peaks[-1] < last_peaks[-2] and last_troughs[-1] < last_troughs[-2]:
                sequence = "LH_LL"  # Downtrend structure
            elif (
                last_peaks[-1] < last_peaks[-2] and last_troughs[-1] > last_troughs[-2]
            ):
                sequence = "CONTRACTION"
            elif (
                last_peaks[-1] > last_peaks[-2] and last_troughs[-1] < last_troughs[-2]
            ):
                sequence = "EXPANSION"

        # Most recent extrema for standard tight SL
        recent_swing_high = last_peaks[-1] if last_peaks else np.max(highs)
        recent_swing_low = last_troughs[-1] if last_troughs else np.min(lows)

        # Check for Double Tops / Bottoms indicating a liquidity sweep
        equal_highs = (
            len(last_peaks) >= 2 and abs(last_peaks[-1] - last_peaks[-2]) < atr * 0.3
        )
        equal_lows = (
            len(last_troughs) >= 2
            and abs(last_troughs[-1] - last_troughs[-2]) < atr * 0.3
        )

        has_equal_extrema = False
        if equal_highs and direction == "SHORT":
            has_equal_extrema = True
        elif equal_lows and direction == "LONG":
            has_equal_extrema = True
 
        return {
            "state": sequence,
            "recent_high": recent_swing_high,
            "recent_low": recent_swing_low,
            "has_equal_extrema": has_equal_extrema,
            # Direction-agnostic raw flags so direction-dependent consumers can
            # resolve per side (precompute calls this once with "LONG").
            "equal_highs": bool(equal_highs),
            "equal_lows": bool(equal_lows),
        }

    def _detect_bos(self, highs: np.ndarray, lows: np.ndarray, atr: float,
                    volumes: np.ndarray = None, closes: np.ndarray = None,
                    swings: dict | None = None,
                    min_break_atr: float = 0.0) -> dict:
        """Detect Break of Structure (BOS) using close-based breakout of the
        prior structural swing.

        Scans the last ``ENGINE_B_BOS_LOOKBACK_BARS`` bars (default 5) for a
        close that breached the prior swing AND has not been invalidated since
        (no subsequent close back across the level). This captures BOS-then-
        retest entries that the legacy last-bar-only logic missed.

        volumes: numpy array of bar volumes. Volume gate now applies only when
        volumes are real (Binance contract volume). MT5 tick-volume gating is
        handled by the caller — see analyze_structure().
        closes: numpy array of bar closes; falls back to highs/lows when absent.
        """
        try:
            swings = swings if isinstance(swings, dict) else self._swing_cache(highs, lows, atr)
            peak_idx = swings.get("peak_idx", [])
            trough_idx = swings.get("trough_idx", [])

            last_peaks = [highs[i] for i in peak_idx[-3:]]
            last_troughs = [lows[i] for i in trough_idx[-3:]]

            if len(last_peaks) < 2 or len(last_troughs) < 2:
                return {
                    "bos_bull": False, "bos_bear": False,
                    "last_broken_high": None, "last_broken_low": None,
                    "bos_reference_high": None, "bos_reference_low": None,
                    "bos_volume_confirmed": False,
                    "bos_bull_bar_index": None, "bos_bear_bar_index": None,
                }

            prior_high = float(last_peaks[-2])
            prior_low = float(last_troughs[-2])
            prior_high_idx = int(peak_idx[-2])
            prior_low_idx = int(trough_idx[-2])
            try:
                min_break_abs = max(0.0, float(min_break_atr or 0.0)) * float(atr)
            except (TypeError, ValueError):
                min_break_abs = 0.0
            _has_close = closes is not None and len(closes) > 0
            if not _has_close:
                # H-1: BOS must be confirmed by a close through the level. Highs/lows
                # permit wick-only breaches without conviction, so when closes are
                # unavailable we fail closed instead of emitting a weaker proxy BOS.
                return {
                    "bos_bull": False, "bos_bear": False,
                    "last_broken_high": None, "last_broken_low": None,
                    "bos_reference_high": prior_high, "bos_reference_low": prior_low,
                    "bos_reference_high_index": prior_high_idx,
                    "bos_reference_low_index": prior_low_idx,
                    "bos_volume_confirmed": False,
                    "bos_bull_bar_index": None, "bos_bear_bar_index": None,
                    "bos_close_unavailable": True,
                }
            n = len(closes) if _has_close else len(highs)

            lookback = int(
                config.CONFIG.get("ENGINE_B_BOS_LOOKBACK_BARS", 5) or 5
            )
            lookback = max(1, min(lookback, n))

            bos_bull = False; bos_bull_idx = None; bos_bull_vol_ref = None
            bos_bear = False; bos_bear_idx = None; bos_bear_vol_ref = None
            for k in range(1, lookback + 1):
                idx = n - k
                close_v = float(closes[idx]) if _has_close else float(highs[idx])
                # Bull BOS: close above prior swing high, no subsequent close back below
                if not bos_bull and close_v > prior_high + min_break_abs:
                    invalidated = False
                    for j in range(idx + 1, n):
                        if (float(closes[j]) if _has_close else float(lows[j])) < prior_high:
                            invalidated = True
                            break
                    if not invalidated:
                        bos_bull, bos_bull_idx, bos_bull_vol_ref = True, idx, idx
                close_v_bear = float(closes[idx]) if _has_close else float(lows[idx])
                if not bos_bear and close_v_bear < prior_low - min_break_abs:
                    invalidated = False
                    for j in range(idx + 1, n):
                        if (float(closes[j]) if _has_close else float(highs[j])) > prior_low:
                            invalidated = True
                            break
                    if not invalidated:
                        bos_bear, bos_bear_idx, bos_bear_vol_ref = True, idx, idx
                if bos_bull and bos_bear:
                    break

            bos_volume_confirmed = False
            if volumes is not None and len(volumes) >= 20 and (bos_bull or bos_bear):
                positive_vols = [float(v) for v in volumes[-20:] if float(v) > 0]
                avg_vol_20 = float(np.mean(positive_vols)) if positive_vols else 0.0
                ref_idx = bos_bull_vol_ref if bos_bull else bos_bear_vol_ref
                # C-3: when forming bars are NOT stripped from the structure TF, the
                # last bar can be a forming (unconfirmed) candle whose volume is
                # incomplete; do not gate BOS on partial volume. Default config strips
                # forming bars (ENGINE_B_STRIP_FORMING_STRUCT=True) so this is a no-op
                # there — the gate still uses the confirmed last bar.
                _strip_forming = bool(config.CONFIG.get("ENGINE_B_STRIP_FORMING_STRUCT", True))
                _bos_on_last_bar = ref_idx is None or ref_idx >= n - 1
                if _bos_on_last_bar and not _strip_forming:
                    bos_volume_confirmed = False
                elif avg_vol_20 > 0:
                    bar_vol = float(volumes[ref_idx]) if ref_idx is not None else float(volumes[-1])
                    multiplier = float(
                        config.CONFIG.get("NAKED_ENGINE", {}).get("bos_volume_multiplier", 1.3)
                    )
                    bos_volume_confirmed = bar_vol >= avg_vol_20 * multiplier
                else:
                    bos_volume_confirmed = False

            return {
                "bos_bull": bos_bull,
                "bos_bear": bos_bear,
                "last_broken_high": prior_high if bos_bull else None,
                "last_broken_low": prior_low if bos_bear else None,
                "bos_reference_high": prior_high,
                "bos_reference_low": prior_low,
                "bos_reference_high_index": prior_high_idx,
                "bos_reference_low_index": prior_low_idx,
                "bos_volume_confirmed": bos_volume_confirmed,
                "bos_bull_bar_index": bos_bull_idx,
                "bos_bear_bar_index": bos_bear_idx,
            }

        except Exception as exc:
            log.warning("[ENGINE_B] _detect_bos failed: %s", exc, exc_info=True)
            return {
                "bos_bull": False, "bos_bear": False,
                "last_broken_high": None, "last_broken_low": None,
                "bos_reference_high": None, "bos_reference_low": None,
                "bos_volume_confirmed": False,
                "bos_bull_bar_index": None, "bos_bear_bar_index": None,
                "_detection_error": str(exc),
            }

    def _detect_choch(
        self, highs: np.ndarray, lows: np.ndarray, atr: float,
        closes: np.ndarray = None, swings: dict | None = None,
        bos_data: dict | None = None,
        min_break_atr: float = 0.0,
    ) -> dict:
        """Detect Change of Character (CHoCH) — structural reversal signal.

        CHoCH occurs when price breaks the swing that produced the last BOS,
        indicating the trend structure has changed direction. The trend-context
        check (was_bearish / was_bullish) uses a 2-of-3 swing rule: at least
        two of the three most-recent swing highs trended in the prior
        direction, and at least two of the three swing lows did the same.
        Real downtrends rarely produce three strictly-monotonic swings due to
        corrective bars; the strict 3-of-3 logic suppressed almost every
        valid CHoCH.

        Returns:
            choch_bull: True if bearish structure broke bullish (reversal up)
            choch_bear: True if bullish structure broke bearish (reversal down)
            choch_level: The price level that was broken
        """
        try:
            swings = swings if isinstance(swings, dict) else self._swing_cache(highs, lows, atr)
            peak_idx = swings.get("peak_idx", [])
            trough_idx = swings.get("trough_idx", [])

            last_peaks = [highs[i] for i in peak_idx[-3:]]
            last_troughs = [lows[i] for i in trough_idx[-3:]]

            if len(last_peaks) < 3 or len(last_troughs) < 3:
                return {"choch_bull": False, "choch_bear": False, "choch_level": None, "choch_events": []}

            last_close = float(closes[-1]) if closes is not None and len(closes) > 0 else None
            try:
                min_break_abs = max(0.0, float(min_break_atr or 0.0)) * float(atr)
            except (TypeError, ValueError):
                min_break_abs = 0.0
            if isinstance(bos_data, dict) and last_close is not None:
                choch_events = []
                choch_level = None
                choch_bull = False
                choch_bear = False
                if bos_data.get("bos_bear") and bos_data.get("bos_reference_high") is not None:
                    ref_high = float(bos_data["bos_reference_high"])
                    choch_bull = bool(last_close > ref_high + min_break_abs)
                    if choch_bull:
                        choch_level = ref_high
                        choch_events.append({"type": "bullish", "level": ref_high})
                if bos_data.get("bos_bull") and bos_data.get("bos_reference_low") is not None:
                    ref_low = float(bos_data["bos_reference_low"])
                    choch_bear = bool(last_close < ref_low - min_break_abs)
                    if choch_bear:
                        choch_level = ref_low
                        choch_events.append({"type": "bearish", "level": ref_low})
                # Dual BOS in lookback can set both flags; keep CHoCH for the more recent BOS only.
                if choch_bull and choch_bear:
                    def _bos_bar_idx(key: str) -> int:
                        try:
                            raw = bos_data.get(key)
                            return int(raw) if raw is not None else -1
                        except (TypeError, ValueError):
                            return -1

                    _bear_bos_idx = _bos_bar_idx("bos_bear_bar_index")
                    _bull_bos_idx = _bos_bar_idx("bos_bull_bar_index")
                    if _bull_bos_idx > _bear_bos_idx:
                        choch_bull = False
                        choch_events = [e for e in choch_events if e.get("type") != "bullish"]
                    else:
                        choch_bear = False
                        choch_events = [e for e in choch_events if e.get("type") != "bearish"]
                    choch_level = choch_events[-1]["level"] if choch_events else None
                if bos_data.get("bos_bull") or bos_data.get("bos_bear"):
                    return {
                        "choch_bull": choch_bull,
                        "choch_bear": choch_bear,
                        "choch_level": choch_level,
                        "choch_events": choch_events,
                    }

            # M-3: standalone CHoCH path. Reached only when _detect_bos reported
            # neither bull nor bear BOS (the early return above fires otherwise).
            # It detects a reversal that broke prior swing structure without a BOS
            # in the lookback window — a narrow but real case (e.g. a sharp reversal
            # off equal highs/lows). Kept (not dead); exercised by the no-BOS branch.
            def _trend_count(seq, descending: bool) -> int:
                # Count adjacent transitions matching the trend direction.
                if descending:
                    return sum(1 for a, b in zip(seq, seq[1:]) if b < a)
                return sum(1 for a, b in zip(seq, seq[1:]) if b > a)

            strict = bool(config.CONFIG.get("ENGINE_B_CHOCH_STRICT", False))
            # 2-of-3 = at least 1 of 2 transitions in trend direction.
            # strict mode = require both transitions (legacy behaviour).
            min_trend_transitions = 2 if strict else 1
            was_bearish = (
                _trend_count(last_peaks, descending=True) >= min_trend_transitions
                and _trend_count(last_troughs, descending=True) >= min_trend_transitions
            )
            bull_break = closes[-1] if closes is not None and len(closes) > 0 else highs[-1]
            choch_bull = bool(was_bearish and bull_break > last_peaks[-1] + min_break_abs)

            was_bullish = (
                _trend_count(last_peaks, descending=False) >= min_trend_transitions
                and _trend_count(last_troughs, descending=False) >= min_trend_transitions
            )
            bear_break = closes[-1] if closes is not None and len(closes) > 0 else lows[-1]
            choch_bear = bool(was_bullish and bear_break < last_troughs[-1] - min_break_abs)

            choch_level = None
            choch_events = []
            if choch_bull:
                choch_level = float(last_peaks[-1])
                choch_events.append({"type": "bullish", "level": choch_level})
            elif choch_bear:
                choch_level = float(last_troughs[-1])
                choch_events.append({"type": "bearish", "level": choch_level})

            return {
                "choch_bull": choch_bull,
                "choch_bear": choch_bear,
                "choch_level": choch_level,
                "choch_events": choch_events,
            }
        except Exception as exc:
            log.warning("[ENGINE_B] _detect_choch failed: %s", exc, exc_info=True)
            return {
                "choch_bull": False,
                "choch_bear": False,
                "choch_level": None,
                "choch_events": [],
                "_detection_error": str(exc),
            }

    def _detect_order_blocks(self, candles: list, bos_data: dict, atr: float,
                              structure_tf: str = "H4") -> list:
        """
        Detect Order Blocks — the last opposing candle before a Break of Structure.

        Bullish OB: last bearish candle before a bullish BOS break.
        Bearish OB: last bullish candle before a bearish BOS break.

        Returns list of OB dicts with top, bottom, type, volume, displacement score.
        """
        obs = []
        if not candles or len(candles) < 10:
            return obs

        # L-3: backward OB scan span is configurable per structure TF via
        # NAKED_ENGINE.ob_lookback_bars (default 20) so slow swing markets can look
        # further back for the originating order block.
        _ob_lookback = int(
            (config.CONFIG.get("NAKED_ENGINE", {}).get("ob_lookback_bars") or {})
            .get(structure_tf, 20)
        )

        try:
            # Bullish OB: find last bearish candle before the bullish BOS
            if bos_data.get("bos_bull") and bos_data.get("last_broken_high") is not None:
                broken_high = float(bos_data["last_broken_high"])
                # Prefer the precomputed BOS bar index from _detect_bos when available;
                # falls back to scanning back from the last bar (legacy behaviour).
                _precomputed_idx = bos_data.get("bos_bull_bar_index")
                if _precomputed_idx is not None and 0 <= _precomputed_idx < len(candles):
                    bos_index = int(_precomputed_idx)
                else:
                    bos_index = len(candles) - 1
                    for j in range(len(candles) - 1, max(0, len(candles) - _ob_lookback), -1):
                        if float(candles[j]["close"]) > broken_high:
                            bos_index = j
                        else:
                            break
                
                # Scan backward from bos_index - 1 for the last opposite candle
                for i in range(bos_index - 1, max(0, bos_index - _ob_lookback), -1):
                    c = candles[i]
                    if float(c["close"]) < float(c["open"]):  # bearish candle
                        ob_top = float(c["open"])
                        ob_bottom = float(c["close"])  # body only — wick excluded per SMC
                        # Displacement: how far price moved after this candle (in ATRs)
                        if i + 1 < len(candles):
                            _ob_lookforward = int(
                                (config.CONFIG.get("NAKED_ENGINE", {}).get("ob_displacement_bars") or {})
                                .get(structure_tf, 6)
                            )
                            max_after = max(float(candles[j]["high"]) for j in range(i + 1, min(i + _ob_lookforward, len(candles))))
                            displacement = (max_after - ob_top) / atr if atr > 0 else 0
                        else:
                            displacement = 0
                        # Volume strength. Missing/zero volume must not grant the
                        # 40% volume contribution used for exchange-volume assets.
                        vol = float(c.get("vol", 0))
                        avg_vol = np.mean([float(candles[k].get("vol", 0)) for k in range(max(0, i - 20), i)]) if i > 0 else 0.0
                        volume_available = bool(vol > 0 and avg_vol > 0)
                        vol_ratio = vol / avg_vol if volume_available else 0.0
                        # Strength score: 60% displacement, 40% volume (capped 0-100)
                        strength = min(100, int((min(displacement / 2.0, 1.0) * 60) + (min(vol_ratio / 2.0, 1.0) * 40)))
                        obs.append({
                            "type": "bullish",
                            "top": ob_top,
                            "bottom": ob_bottom,
                            "displacement": round(displacement, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "volume_available": volume_available,
                            "strength": strength,
                            "mitigated": False,
                        })
                        break

            # Bearish OB: find last bullish candle before the bearish BOS
            if bos_data.get("bos_bear") and bos_data.get("last_broken_low") is not None:
                broken_low = float(bos_data["last_broken_low"])
                _precomputed_idx = bos_data.get("bos_bear_bar_index")
                if _precomputed_idx is not None and 0 <= _precomputed_idx < len(candles):
                    bos_index = int(_precomputed_idx)
                else:
                    bos_index = len(candles) - 1
                    for j in range(len(candles) - 1, max(0, len(candles) - _ob_lookback), -1):
                        if float(candles[j]["close"]) < broken_low:
                            bos_index = j
                        else:
                            break
                
                # Scan backward from bos_index - 1 for the last opposite candle
                for i in range(bos_index - 1, max(0, bos_index - _ob_lookback), -1):
                    c = candles[i]
                    if float(c["close"]) > float(c["open"]):  # bullish candle
                        ob_top = float(c["close"])  # body only — wick excluded per SMC
                        ob_bottom = float(c["open"])
                        # Displacement
                        if i + 1 < len(candles):
                            _ob_lookforward = int(
                                (config.CONFIG.get("NAKED_ENGINE", {}).get("ob_displacement_bars") or {})
                                .get(structure_tf, 6)
                            )
                            min_after = min(float(candles[j]["low"]) for j in range(i + 1, min(i + _ob_lookforward, len(candles))))
                            displacement = (ob_top - min_after) / atr if atr > 0 else 0
                        else:
                            displacement = 0
                        # Volume strength. Missing/zero volume must not grant the
                        # 40% volume contribution used for exchange-volume assets.
                        vol = float(c.get("vol", 0))
                        avg_vol = np.mean([float(candles[k].get("vol", 0)) for k in range(max(0, i - 20), i)]) if i > 0 else 0.0
                        volume_available = bool(vol > 0 and avg_vol > 0)
                        vol_ratio = vol / avg_vol if volume_available else 0.0
                        strength = min(100, int((min(displacement / 2.0, 1.0) * 60) + (min(vol_ratio / 2.0, 1.0) * 40)))
                        obs.append({
                            "type": "bearish",
                            "top": ob_top,
                            "bottom": ob_bottom,
                            "displacement": round(displacement, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "volume_available": volume_available,
                            "strength": strength,
                            "mitigated": False,
                        })
                        break
        except Exception as _ob_err:
            import logging
            logging.getLogger(__name__).debug(
                f"[OB-DETECT] exception during detection: {_ob_err}"
            )

        return obs

    def _detect_sweep(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, atr: float,
        swing_high: float = None, swing_low: float = None,
    ) -> dict:
        """
        Detect liquidity sweep patterns in the configured recent candle window.
        Uses swing highs/lows (from find_peaks) as reference levels where
        stop losses cluster per SMC methodology.
        
        B4 FIX: Improved fallback logic - instead of using closes[-6] which can
        produce false positives in choppy markets, we now compute local min/max
        of the lookback window as a more robust reference level.
        """
        _empty = {
            "bull_sweep": False,
            "bear_sweep": False,
            "sweep_low": None,
            "sweep_high": None,
        }
        if len(closes) < 8:
            return _empty

        try:
            # B4 FIX: Improved fallback - use local extremes from lookback window
            # instead of arbitrary closes[-6] which can be misleading in choppy markets
            if swing_low is not None:
                ref_low = swing_low
            else:
                # Use the minimum low from bars 6-15 as reference (avoids recent noise)
                lookback_lows = lows[-15:-5] if len(lows) >= 15 else lows[:-5]
                ref_low = float(np.min(lookback_lows)) if len(lookback_lows) > 0 else float(lows[-6])
            
            if swing_high is not None:
                ref_high = swing_high
            else:
                # Use the maximum high from bars 6-15 as reference
                lookback_highs = highs[-15:-5] if len(highs) >= 15 else highs[:-5]
                ref_high = float(np.max(lookback_highs)) if len(lookback_highs) > 0 else float(highs[-6])

            try:
                sweep_lookback = int(config.CONFIG.get("ENGINE_B_SWEEP_LOOKBACK_BARS", 5) or 5)
            except (TypeError, ValueError):
                sweep_lookback = 5
            sweep_lookback = max(1, min(sweep_lookback, len(closes)))

            recent_highs = highs[-sweep_lookback:]
            recent_lows = lows[-sweep_lookback:]
            recent_closes = closes[-sweep_lookback:]

            bull_sweep = False
            bear_sweep = False
            sweep_low = None
            sweep_high = None

            for i in range(sweep_lookback):
                high = recent_highs[i]
                low = recent_lows[i]
                close = recent_closes[i]

                # Bullish sweep: wick below swing low, close above it (stop hunt below → reversal up)
                if (
                    low < ref_low - 0.3 * atr
                    and close > ref_low
                ):
                    bull_sweep = True
                    sweep_low = low

                # Bearish sweep: wick above swing high, close below it (stop hunt above → reversal down)
                if (
                    high > ref_high + 0.3 * atr
                    and close < ref_high
                ):
                    bear_sweep = True
                    sweep_high = high

            return {
                "bull_sweep": bull_sweep,
                "bear_sweep": bear_sweep,
                "sweep_low": sweep_low,
                "sweep_high": sweep_high,
            }

        except Exception:
            return _empty

    def _merge_fvgs(self, raw_fvgs: list) -> list:
        if len(raw_fvgs) < 2:
            return raw_fvgs

        merged = [raw_fvgs[0]]
        for fvg in raw_fvgs[1:]:
            prev = merged[-1]
            if fvg["type"] == prev["type"] and abs(fvg["bar_index"] - prev["bar_index"]) <= 2:
                prev["top"] = max(prev["top"], fvg["top"])
                prev["bottom"] = min(prev["bottom"], fvg["bottom"])
                prev["size"] = round(prev["top"] - prev["bottom"], 6)
                prev["mitigated"] = prev["mitigated"] and fvg["mitigated"]
            else:
                merged.append(fvg)

        return merged

    def _detect_fvg_legacy(self, candles: list) -> list:
        raw_fvgs = []
        for i in range(1, len(candles) - 1):
            prev_high = float(candles[i - 1]["high"])
            prev_low = float(candles[i - 1]["low"])
            next_high = float(candles[i + 1]["high"])
            next_low = float(candles[i + 1]["low"])

            # Bearish FVG — gap down: prev candle low above next candle high
            if prev_low > next_high:
                gap_top = prev_low
                gap_bottom = next_high
                gap_size = gap_top - gap_bottom
                # Mitigation: price rallies back UP into the gap (high >= midpoint)
                midpoint = gap_bottom + (gap_size * 0.5)
                mitigated = False
                for j in range(i + 2, len(candles)):
                    if float(candles[j]["high"]) >= midpoint:
                        mitigated = True
                        break
                raw_fvgs.append({
                    "type": "bearish", "top": gap_top, "bottom": gap_bottom,
                    "size": round(gap_size, 6), "mitigated": mitigated, "bar_index": i,
                })

            # Bullish FVG — gap up: prev candle high below next candle low
            if prev_high < next_low:
                gap_top = next_low
                gap_bottom = prev_high
                gap_size = gap_top - gap_bottom
                # Mitigation: price retraces DOWN into the gap (low <= midpoint)
                midpoint = gap_top - (gap_size * 0.5)
                mitigated = False
                for j in range(i + 2, len(candles)):
                    if float(candles[j]["low"]) <= midpoint:
                        mitigated = True
                        break
                raw_fvgs.append({
                    "type": "bullish", "top": gap_top, "bottom": gap_bottom,
                    "size": round(gap_size, 6), "mitigated": mitigated, "bar_index": i,
                })

        return self._merge_fvgs(raw_fvgs)

    def _detect_fvg_fast(self, candles: list) -> list:
        n = len(candles)
        if n < 3:
            return []

        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]

        raw_fvgs = []
        for i in range(1, n - 1):
            prev_high = highs[i - 1]
            prev_low = lows[i - 1]
            next_high = highs[i + 1]
            next_low = lows[i + 1]

            if prev_low > next_high:
                gap_top = prev_low
                gap_bottom = next_high
                gap_size = gap_top - gap_bottom
                midpoint = gap_bottom + (gap_size * 0.5)
                mitigated = False
                for j in range(i + 2, n):
                    if highs[j] >= midpoint:
                        mitigated = True
                        break
                raw_fvgs.append({
                    "type": "bearish", "top": gap_top, "bottom": gap_bottom,
                    "size": round(gap_size, 6), "mitigated": mitigated, "bar_index": i,
                })

            if prev_high < next_low:
                gap_top = next_low
                gap_bottom = prev_high
                gap_size = gap_top - gap_bottom
                midpoint = gap_top - (gap_size * 0.5)
                mitigated = False
                for j in range(i + 2, n):
                    if lows[j] <= midpoint:
                        mitigated = True
                        break
                raw_fvgs.append({
                    "type": "bullish", "top": gap_top, "bottom": gap_bottom,
                    "size": round(gap_size, 6), "mitigated": mitigated, "bar_index": i,
                })

        return self._merge_fvgs(raw_fvgs)

    def _detect_fvg(self, candles: list) -> list:
        """
        Detect Fair Value Gaps with mitigation tracking and consecutive merging.

        A bearish FVG: candle[i-1].low > candle[i+1].high (gap down)
        A bullish FVG: candle[i-1].high < candle[i+1].low (gap up)

        Mitigation: FVG is considered mitigated when price has retraced
        through 50%+ of the gap (consequent encroachment).

        Consecutive merging: adjacent FVGs of same type merge into one
        using the widest boundaries.
        """
        if not bool(config.CONFIG.get("ENGINE_B_FAST_FVG_DETECTION", True)):
            return self._detect_fvg_legacy(candles)
        try:
            return self._detect_fvg_fast(candles)
        except Exception as _fvg_err:
            log.warning(f"[FVG-DETECT] fast path fallback: {_fvg_err}")
            return self._detect_fvg_legacy(candles)

    def _zone_context(
        self,
        zone: dict | None,
        current_price: float,
        atr: float,
        direction: str,
        candles: list,
        *,
        asset_type: str | None = None,
        score_group: str | None = None,
    ) -> dict:
        atr_val = atr if atr and atr > 0 else 0.0001
        if not zone:
            return {"distance": None, "near_zone": False, "zone_touched": False}

        lower = zone.get("lower")
        upper = zone.get("upper")
        center = zone.get("center")

        if lower is not None and upper is not None:
            if lower <= current_price <= upper:
                distance = 0.0
            elif current_price < lower:
                distance = lower - current_price
            else:
                distance = current_price - upper
        else:
            distance = abs(current_price - center) if center is not None else None

        _proximity_cfg = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_proximity_atr_mult") or {}
        try:
            from scoring import _resolve_class_keyed

            _proximity_mult = float(
                _resolve_class_keyed(
                    _proximity_cfg if isinstance(_proximity_cfg, dict) else {"default": _proximity_cfg},
                    score_group,
                    str(asset_type or "").lower(),
                    0.5,
                )
            )
        except Exception:
            _proximity_mult = 0.5
            if isinstance(_proximity_cfg, dict):
                _proximity_mult = float(_proximity_cfg.get("default", 0.5))
            else:
                _proximity_mult = float(_proximity_cfg)
        near_zone = distance is not None and distance <= atr_val * _proximity_mult
        zone_touched = False

        if candles:
            # C-1: zone-touch tolerance was a hardcoded 0.1 ATR — far tighter than the
            # near_zone proximity above and too strict on wide-ATR instruments. Make it
            # configurable (NAKED_ENGINE.zone_touch_tolerance_atr_mult, default 0.2).
            _touch_tol = _naked_engine_atr_mult(
                "zone_touch_tolerance_atr_mult", 0.2, asset_type
            )
            last = candles[-1]
            last_high = float(last["high"])
            last_low = float(last["low"])
            last_close = float(last["close"])
            if direction == "LONG":
                threshold = upper if upper is not None else center
                floor = lower if lower is not None else center
                if threshold is not None and last_low <= threshold + (atr_val * _touch_tol):
                    zone_touched = floor is None or last_close >= floor - (atr_val * _touch_tol)
            else:
                threshold = lower if lower is not None else center
                ceiling = upper if upper is not None else center
                if threshold is not None and last_high >= threshold - (atr_val * _touch_tol):
                    zone_touched = ceiling is None or last_close <= ceiling + (atr_val * _touch_tol)

        return {
            "distance": distance,
            "near_zone": near_zone,
            "zone_touched": zone_touched,
        }

    def _price_action_trigger(
        self,
        candles: list,
        direction: str,
        atr: float,
        zone_hit: bool,
        bos_confirmed: bool,
        is_trending: bool = False,
        asset_type: str | None = "",
    ) -> dict:
        if len(candles) < 3:
            return {
                "pattern": "NONE",
                "trigger_ok": False,
                "rejection": False,
                "engulfing": False,
                "inside_break": False,
                "strong_close": False,
            }

        last = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]

        open_ = float(last["open"])
        high = float(last["high"])
        low = float(last["low"])
        close = float(last["close"])

        prev_open = float(prev["open"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])
        prev_close = float(prev["close"])

        prev2_high = float(prev2["high"])
        prev2_low = float(prev2["low"])

        _current_price = float(candles[-1]["close"]) if candles else 0.0
        _min_range = max(atr * 0.05, _current_price * 1e-6 if _current_price > 0 else 1e-9, 1e-12)
        range_ = max(high - low, _min_range)
        body = abs(close - open_)
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low

        rejection_ratio = _engine_b_rejection_wick_body_ratio()
        # In trending regimes, candles have larger bodies and smaller wicks —
        # reduce the wick/body requirement so rejection patterns still fire.
        if is_trending:
            rejection_ratio = min(rejection_ratio, 1.0)
        bull_rejection = lower_wick >= max(body * rejection_ratio, atr * 0.08) and close >= low + (range_ * 0.6)
        bear_rejection = upper_wick >= max(body * rejection_ratio, atr * 0.08) and close <= high - (range_ * 0.6)

        # Engulfing displacement floor: a real engulfing trigger needs a body
        # that means something at this timeframe's volatility. Without it,
        # doji-engulfs-doji noise in quiet regimes fires the entry gate.
        # NAKED_ENGINE.engulfing_min_body_atr_mult (scalar or per-asset dict);
        # set 0.0 to restore legacy body-agnostic behaviour. Inert when atr<=0.
        _engulf_min_body = atr * _naked_engine_atr_mult(
            "engulfing_min_body_atr_mult", 0.2, asset_type
        ) if atr and atr > 0 else 0.0
        bull_engulf = (
            close > open_
            and prev_close < prev_open
            and close >= prev_open
            and open_ <= prev_close
            and body >= _engulf_min_body
        )
        bear_engulf = (
            close < open_
            and prev_close > prev_open
            and close <= prev_open
            and open_ >= prev_close
            and body >= _engulf_min_body
        )

        inside_bar = prev_high < prev2_high and prev_low > prev2_low
        bull_inside_break = inside_bar and close > prev_high
        bear_inside_break = inside_bar and close < prev_low

        bull_strong_close = close > open_ and close >= low + (range_ * 0.7)
        bear_strong_close = close < open_ and close <= high - (range_ * 0.7)

        if direction == "LONG":
            trigger_ok = bull_rejection or bull_engulf or bull_inside_break or (
                bull_strong_close and (zone_hit or bos_confirmed)
            )
            pattern = (
                "REJECTION"
                if bull_rejection
                else "ENGULFING"
                if bull_engulf
                else "INSIDE_BREAK"
                if bull_inside_break
                else "STRONG_CLOSE"
                if bull_strong_close and (zone_hit or bos_confirmed)
                else "NONE"
            )
            rejection = bull_rejection
            engulfing = bull_engulf
            inside_break = bull_inside_break
            strong_close = bull_strong_close
        else:
            trigger_ok = bear_rejection or bear_engulf or bear_inside_break or (
                bear_strong_close and (zone_hit or bos_confirmed)
            )
            pattern = (
                "REJECTION"
                if bear_rejection
                else "ENGULFING"
                if bear_engulf
                else "INSIDE_BREAK"
                if bear_inside_break
                else "STRONG_CLOSE"
                if bear_strong_close and (zone_hit or bos_confirmed)
                else "NONE"
            )
            rejection = bear_rejection
            engulfing = bear_engulf
            inside_break = bear_inside_break
            strong_close = bear_strong_close

        return {
            "pattern": pattern,
            "trigger_ok": trigger_ok,
            "rejection": rejection,
            "engulfing": engulfing,
            "inside_break": inside_break,
            "strong_close": strong_close,
        }

    @staticmethod
    def _float_or_none(value):
        try:
            if value is None:
                return None
            out = float(value)
            if out != out:
                return None
            return out
        except (TypeError, ValueError):
            return None

    @classmethod
    def _candle_value(cls, candle: dict, *keys: str, default: float = 0.0) -> float:
        if not isinstance(candle, dict):
            return default
        for key in keys:
            value = cls._float_or_none(candle.get(key))
            if value is not None:
                return value
        return default

    def _determine_independent_direction(
        self,
        h1_sequence: str,
        h4_sequence: str,
        bos_data: dict,
        d1_bos: dict,
        choch_data: dict,
        sweep_data: dict,
    ) -> dict:
        """
        Determine Engine B's own directional opinion from pure price-action evidence.

        This is advisory only — it does NOT affect scoring, checklist pass/fail,
        or the direction parameter passed into analyze_structure from the caller.
        It lets Engine C distinguish genuine direction conflicts from inherited ones.

        Returns:
            dict with:
              direction: 'LONG' | 'SHORT' | None  (None = no structural opinion)
              confidence: 'HIGH' | 'MEDIUM' | 'LOW'
              reason: str  human-readable rationale
              votes: dict  evidence map used to derive the opinion
        """
        votes: dict[str, int] = {}  # +1 = bullish, -1 = bearish, 0 = neutral

        # --- H1 micro swing sequence ---
        if h1_sequence == "HH_HL":
            votes["h1_swing"] = 1
        elif h1_sequence == "LH_LL":
            votes["h1_swing"] = -1
        else:
            votes["h1_swing"] = 0

        # --- H4 macro swing sequence ---
        if h4_sequence == "HH_HL":
            votes["h4_swing"] = 1
        elif h4_sequence == "LH_LL":
            votes["h4_swing"] = -1
        else:
            votes["h4_swing"] = 0

        # --- BOS direction (diagnostic only; primary checklist already gates on BOS) ---
        bos_bull = bool(bos_data.get("bos_bull"))
        bos_bear = bool(bos_data.get("bos_bear"))
        diagnostic_bos = {"bos_bull": bos_bull, "bos_bear": bos_bear}
        # M-2: BOS is one of the strongest structural signals — it now contributes a
        # low-weight vote to Engine B's *independent* directional opinion (weight 1
        # below). It remains separately gated by the primary checklist.
        if bos_bull and not bos_bear:
            votes["bos"] = 1
        elif bos_bear and not bos_bull:
            votes["bos"] = -1
        else:
            votes["bos"] = 0

        # --- D1 BOS (diagnostic only; MTF BOS is tracked separately) ---
        d1_bull = bool(d1_bos.get("bos_bull"))
        d1_bear = bool(d1_bos.get("bos_bear"))
        diagnostic_bos.update({"d1_bull": d1_bull, "d1_bear": d1_bear})

        # --- CHoCH (early reversal signal, lower weight) ---
        if choch_data.get("choch_bull") and not choch_data.get("choch_bear"):
            votes["choch"] = 1
        elif choch_data.get("choch_bear") and not choch_data.get("choch_bull"):
            votes["choch"] = -1
        else:
            votes["choch"] = 0

        # --- Liquidity sweep direction (sweep of lows → bullish, highs → bearish) ---
        if sweep_data.get("bull_sweep"):
            votes["sweep"] = 1   # sweep of lows = bullish reversal context
        elif sweep_data.get("bear_sweep"):
            votes["sweep"] = -1  # sweep of highs = bearish reversal context
        else:
            votes["sweep"] = 0

        # --- Weight the votes ---
        # H4 swing carries more weight than H1, CHoCH, or sweep.
        weights = {
            "h4_swing": 2,
            "h1_swing": 1,
            "choch":    1,
            "sweep":    1,
            "bos":      1,
        }
        total_weight = sum(weights.values())
        weighted_score = sum(
            votes.get(k, 0) * w for k, w in weights.items()
        )
        score_ratio = weighted_score / total_weight  # -1.0 to +1.0

        # Confidence: how strongly the evidence agrees
        active_votes = [v for v in votes.values() if v != 0]
        if not active_votes:
            return {
                "direction": None,
                "confidence": "NONE",
                "reason": "No structural evidence available",
                "votes": votes,
                "diagnostic_bos": diagnostic_bos,
                "weighted_score": 0.0,
            }

        positive = sum(1 for v in active_votes if v > 0)
        negative = sum(1 for v in active_votes if v < 0)
        agreement_ratio = max(positive, negative) / len(active_votes)

        if agreement_ratio >= 0.80:
            confidence = "HIGH"
        elif agreement_ratio >= 0.60:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Direction from weighted score
        if score_ratio > 0.15:
            direction = "LONG"
            reason = (
                f"Structural evidence bullish: {positive}/{len(active_votes)} indicators agree "
                f"(h4={h4_sequence}, choch={votes.get('choch', 0)}, sweep={votes.get('sweep', 0)})"
            )
        elif score_ratio < -0.15:
            direction = "SHORT"
            reason = (
                f"Structural evidence bearish: {negative}/{len(active_votes)} indicators agree "
                f"(h4={h4_sequence}, choch={votes.get('choch', 0)}, sweep={votes.get('sweep', 0)})"
            )
        else:
            direction = None
            confidence = "LOW"
            reason = (
                f"Structural evidence mixed or ranging: score={score_ratio:.2f} "
                f"(pos={positive}, neg={negative}, h4={h4_sequence})"
            )

        return {
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "votes": votes,
            "diagnostic_bos": diagnostic_bos,
            "weighted_score": round(score_ratio, 4),
        }

    def precompute_structure_data(
        self,
        d1_candles: list,
        h4_candles: list,
        h1_candles: list,
        current_price: float,
        atr: float,
        regime: str = "RANGING",
        fallback_rr: float = 2.0,
        asset_type: str = "",
        enable_zone_registry: bool = True,
        enable_profile_context: bool = True,
        d1_snap: dict | None = None,
        h4_snap: dict | None = None,
        style: str = "intraday",
        pair: dict | None = None,
        trade_bucket_reference_ts=None,
        trade_bucket_require_fresh: bool = True,
    ) -> dict:
        """Precompute all direction-independent structure analysis data.

        Returns a precompute context dict that can be passed to
        ``analyze_structure_direction()``. Call this once per bar, then
        iterate over directions with the lightweight direction-dependent step.

        Backward-compatible: the original ``analyze_structure()`` calls this
        internally.
        """
        if not d1_candles or not h4_candles or not h1_candles or atr <= 0:
            return {"_error": "Missing data or valid ATR"}

        registry_symbol = self._consume_registry_symbol() if enable_zone_registry else None

        _tfs = resolve_engine_b_tfs(asset_type, style)
        structure_tf = _tfs["struct"]
        if structure_tf == "D1":
            struct_candles = d1_candles
        elif structure_tf == "H4":
            struct_candles = h4_candles
        else:
            struct_candles = h1_candles
        forming_strip_diag: dict = {}
        struct_candles = _engine_b_confirmed_only_struct_candles(
            struct_candles,
            structure_tf,
            pair=pair,
            diagnostics=forming_strip_diag,
        )
        _strip_reason = str(forming_strip_diag.get("reason") or "")
        if _strip_reason == "missing_pair_context":
            return {
                "_error": "missing_pair_context",
                "forming_strip_diagnostics": forming_strip_diag,
            }
        if _strip_reason == "insufficient_confirmed_bars":
            return {
                "_error": "insufficient_confirmed_bars",
                "forming_strip_diagnostics": forming_strip_diag,
            }
        if _strip_reason in ("split_market_state_error", "insufficient_input"):
            return {
                "_error": _strip_reason,
                "forming_strip_diagnostics": forming_strip_diag,
            }
        if not struct_candles:
            return {
                "_error": _strip_reason or "struct_candles_empty",
                "forming_strip_diagnostics": forming_strip_diag,
            }
        trigger_candles = h4_candles if _tfs["trigger"] == "H4" else h1_candles
        _zone_fvg_candles = struct_candles if structure_tf == "H4" else h4_candles

        h4_highs = np.array([float(c["high"]) for c in h4_candles])
        h4_lows = np.array([float(c["low"]) for c in h4_candles])

        struct_highs = np.array([float(c["high"]) for c in struct_candles])
        struct_lows = np.array([float(c["low"]) for c in struct_candles])
        struct_closes = np.array([float(c["close"]) for c in struct_candles])
        struct_atr = self._compute_atr_from_candles(struct_candles, fallback=atr)
        zone_atr = self._compute_atr_from_candles(_zone_fvg_candles, fallback=atr)
        _score_group = (
            (pair or {}).get("score_group")
            or (pair or {}).get("group")
            or (pair or {}).get("asset_group")
        )
        asset_struct_diag = _asset_class_structure_adjustment(asset_type, _score_group, struct_candles, struct_atr)
        crypto_struct_diag = _crypto_structure_adjustment(asset_type, struct_candles, struct_atr)
        structure_prominence_mult = max(
            float(crypto_struct_diag.get("swing_prominence_mult", 1.0) or 1.0),
            float(asset_struct_diag.get("swing_prominence_mult", 1.0) or 1.0),
        )
        structure_min_break_atr = max(
            float(crypto_struct_diag.get("bos_min_break_atr", 0.0) or 0.0),
            float(asset_struct_diag.get("bos_min_break_atr", 0.0) or 0.0),
        )
        struct_swings = self._swing_cache(struct_highs, struct_lows, struct_atr, prominence_mult=structure_prominence_mult)

        _zf_highs = np.array([float(c["high"]) for c in _zone_fvg_candles])
        _zf_lows = np.array([float(c["low"]) for c in _zone_fvg_candles])
        res_zones, sup_zones = self._find_zones(_zf_highs, _zf_lows, zone_atr, regime, _zone_fvg_candles, structure_tf=structure_tf, asset_type=asset_type)

        fvgs = self._detect_fvg(_zone_fvg_candles)
        active_fvgs = [f for f in fvgs if not f.get("mitigated", False)]

        h4_swings = self._swing_cache(h4_highs, h4_lows, zone_atr)
        sequence_data = self._determine_sequence(struct_highs, struct_lows, struct_atr, "LONG", swings=struct_swings)
        macro_seq_data = self._determine_sequence(h4_highs, h4_lows, zone_atr, "LONG", swings=h4_swings)

        struct_volumes = None
        _allow_tick_vol_gate = bool(config.CONFIG.get("ENGINE_B_BOS_VOLUME_FOR_TICKVOL", False))
        _crypto_src = str((pair or {}).get("source") or "").lower()
        if (asset_type or "").lower() == "crypto":
            _vol_gate_eligible = not (_crypto_src == "mt5") or bool(_allow_tick_vol_gate)
        else:
            _vol_gate_eligible = bool(_allow_tick_vol_gate)
        if _vol_gate_eligible:
            _has_vol = any(float(c.get("vol", 0)) > 0 for c in struct_candles[-5:])
            if _has_vol:
                struct_volumes = np.array([float(c.get("vol", 0)) for c in struct_candles])

        bos_data = self._detect_bos(struct_highs, struct_lows, struct_atr, volumes=struct_volumes, closes=struct_closes, swings=struct_swings, min_break_atr=structure_min_break_atr)

        _sweep_swing_high = None
        _sweep_swing_low = None
        try:
            _pk_idx = struct_swings.get("peak_idx", [])
            _tr_idx = struct_swings.get("trough_idx", [])
            if len(_pk_idx) >= 1:
                _sweep_swing_high = float(struct_highs[_pk_idx[-1]])
            if len(_tr_idx) >= 1:
                _sweep_swing_low = float(struct_lows[_tr_idx[-1]])
        except Exception:
            pass
        sweep_data = self._detect_sweep(struct_highs, struct_lows, struct_closes, struct_atr, swing_high=_sweep_swing_high, swing_low=_sweep_swing_low)

        d1_highs = np.array([float(c["high"]) for c in d1_candles])
        d1_lows = np.array([float(c["low"]) for c in d1_candles])
        d1_closes = np.array([float(c["close"]) for c in d1_candles])
        d1_atr = self._compute_atr_from_candles(d1_candles, fallback=atr)
        d1_crypto_diag = _crypto_structure_adjustment(asset_type, d1_candles, d1_atr)
        d1_asset_diag = _asset_class_structure_adjustment(asset_type, _score_group, d1_candles, d1_atr)
        d1_prominence_mult = max(float(d1_crypto_diag.get("swing_prominence_mult", 1.0) or 1.0), float(d1_asset_diag.get("swing_prominence_mult", 1.0) or 1.0))
        d1_min_break_atr = max(float(d1_crypto_diag.get("bos_min_break_atr", 0.0) or 0.0), float(d1_asset_diag.get("bos_min_break_atr", 0.0) or 0.0))
        d1_swings = self._swing_cache(d1_highs, d1_lows, d1_atr, prominence_mult=d1_prominence_mult)
        d1_bos = self._detect_bos(d1_highs, d1_lows, d1_atr, closes=d1_closes, swings=d1_swings, min_break_atr=d1_min_break_atr)

        d1_order_blocks_raw: list = []
        d1_fvgs_raw: list = []
        try:
            d1_order_blocks_raw = self._detect_order_blocks(d1_candles, d1_bos, d1_atr, structure_tf="D1")
            d1_fvgs_raw = [f for f in self._detect_fvg(d1_candles) if not f.get("mitigated")]
        except Exception:
            pass
        d1_conflict_window = d1_atr * _engine_b_d1_conflict_window_atr_mult()

        bos_mtf_confirmed = bool((bos_data.get("bos_bull") and d1_bos.get("bos_bull")) or (bos_data.get("bos_bear") and d1_bos.get("bos_bear")))

        order_blocks = self._detect_order_blocks(
            struct_candles, bos_data, struct_atr, structure_tf=structure_tf
        )
        if registry_symbol:
            zone_registry = get_zone_registry()
            zone_registry.upsert_zones(
                registry_symbol, structure_tf, order_blocks, [], atr=struct_atr, asset_type=asset_type
            )
            zone_registry.upsert_zones(
                registry_symbol, "H4", [], fvgs, atr=struct_atr, asset_type=asset_type
            )
            zone_registry.mark_mitigated(registry_symbol, structure_tf, current_price, atr)
            zone_registry.mark_mitigated(registry_symbol, "H4", current_price, atr)
            zone_registry.prune_old_zones()
            if zone_registry.has_zones(registry_symbol, structure_tf):
                order_blocks = self._registry_order_blocks(zone_registry.get_active_zones(registry_symbol, structure_tf))
            if zone_registry.has_zones(registry_symbol, "H4"):
                active_fvgs = self._registry_fvgs(zone_registry.get_active_zones(registry_symbol, "H4"))

        for zone in res_zones + sup_zones:
            overlapping_fvgs = [fvg for fvg in active_fvgs if not (zone["upper"] < fvg["bottom"] or zone["lower"] > fvg["top"])]
            zone["fvg_overlap"] = len(overlapping_fvgs) > 0
            if overlapping_fvgs and zone_atr > 0:
                largest_fvg = max(overlapping_fvgs, key=lambda f: abs(f["top"] - f["bottom"]))
                zone["fvg_size_atr"] = round(abs(largest_fvg["top"] - largest_fvg["bottom"]) / zone_atr, 2)
            else:
                zone["fvg_size_atr"] = 0.0

        choch_data = self._detect_choch(struct_highs, struct_lows, struct_atr, closes=struct_closes, swings=struct_swings, bos_data=bos_data, min_break_atr=structure_min_break_atr)
        if str(asset_type or "").lower() == "crypto":
            crypto_struct_diag["structure_quality_score"] = _crypto_structure_quality_score(bos_data, choch_data, sweep_data, macro_seq_data["state"], "LONG", crypto_struct_diag.get("wick_dominance"))
            crypto_struct_diag["d1"] = {"volatility_regime": d1_crypto_diag.get("volatility_regime"), "applied": d1_crypto_diag.get("applied"), "structure_mult": d1_crypto_diag.get("structure_mult"), "bos_min_break_atr": d1_crypto_diag.get("bos_min_break_atr")}
        if asset_struct_diag.get("asset_class") in {"stock", "index", "commodity", "etf", "etf_bond"}:
            asset_struct_diag["d1_structure"] = {"volatility_regime": d1_asset_diag.get("volatility_regime"), "applied": d1_asset_diag.get("applied"), "structure_mult": d1_asset_diag.get("structure_mult"), "bos_min_break_atr": d1_asset_diag.get("bos_min_break_atr")}

        _breaker_atr_mult = float(config.CONFIG.get("NAKED_ENGINE", {}).get("breaker_width_atr_mult", 0.3))
        breaker_blocks = []
        if choch_data.get("choch_bull") and choch_data.get("choch_level") is not None:
            breaker_block = {"type": "bullish_breaker", "level": choch_data["choch_level"], "upper": choch_data["choch_level"] + (struct_atr * _breaker_atr_mult), "lower": choch_data["choch_level"] - (struct_atr * _breaker_atr_mult)}
            sup_zones.append({"upper": breaker_block["upper"], "lower": breaker_block["lower"], "center": breaker_block["level"], "volume_strength": 0.8, "fvg_overlap": False, "fvg_size_atr": 0.0, "is_breaker": True})
            breaker_blocks.append(breaker_block)
        elif choch_data.get("choch_bear") and choch_data.get("choch_level") is not None:
            breaker_block = {"type": "bearish_breaker", "level": choch_data["choch_level"], "upper": choch_data["choch_level"] + (struct_atr * _breaker_atr_mult), "lower": choch_data["choch_level"] - (struct_atr * _breaker_atr_mult)}
            res_zones.append({"upper": breaker_block["upper"], "lower": breaker_block["lower"], "center": breaker_block["level"], "volume_strength": 0.8, "fvg_overlap": False, "fvg_size_atr": 0.0, "is_breaker": True})
            breaker_blocks.append(breaker_block)
        for event in choch_data.get("choch_events") or []:
            level = event.get("level")
            if level is None or level == choch_data.get("choch_level"):
                continue
            level = float(level)
            if event.get("type") == "bullish":
                breaker = {"type": "bullish_breaker", "level": level, "upper": level + (struct_atr * _breaker_atr_mult), "lower": level - (struct_atr * _breaker_atr_mult)}
                sup_zones.append({"upper": breaker["upper"], "lower": breaker["lower"], "center": breaker["level"], "volume_strength": 0.8, "fvg_overlap": False, "fvg_size_atr": 0.0, "is_breaker": True})
                breaker_blocks.append(breaker)
            elif event.get("type") == "bearish":
                breaker = {"type": "bearish_breaker", "level": level, "upper": level + (struct_atr * _breaker_atr_mult), "lower": level - (struct_atr * _breaker_atr_mult)}
                res_zones.append({"upper": breaker["upper"], "lower": breaker["lower"], "center": breaker["level"], "volume_strength": 0.8, "fvg_overlap": False, "fvg_size_atr": 0.0, "is_breaker": True})
                breaker_blocks.append(breaker)
        breaker_block = breaker_blocks[-1] if breaker_blocks else None

        def _merge_zones(zones, merge_dist):
            if not zones:
                return zones
            sorted_z = sorted(zones, key=lambda z: z["center"])
            merged = [sorted_z[0]]
            anchors = [float(sorted_z[0]["center"])]
            for z in sorted_z[1:]:
                prev = merged[-1]
                if abs(z["center"] - anchors[-1]) <= merge_dist:
                    prev["upper"] = max(prev["upper"], z["upper"])
                    prev["lower"] = min(prev["lower"], z["lower"])
                    # H-5: keep the center of the strongest (highest-volume) zone
                    # rather than recomputing the geometric midpoint, which drifts the
                    # level away from the real peak/trough and skews nearest-zone
                    # sorting and distance calcs.
                    if z.get("volume_strength", 0) > prev.get("volume_strength", 0):
                        prev["center"] = z["center"]
                    prev["volume_strength"] = max(prev.get("volume_strength", 0), z.get("volume_strength", 0))
                    if z.get("fvg_overlap"):
                        prev["fvg_overlap"] = True
                        prev["fvg_size_atr"] = max(prev.get("fvg_size_atr", 0), z.get("fvg_size_atr", 0))
                else:
                    merged.append(z)
                    anchors.append(float(z["center"]))
            return merged

        # #4: merge distance configurable (scalar or per-asset dict) via
        # NAKED_ENGINE.zone_merge_atr_mult so distinct levels are not over-merged on
        # volatile instruments nor left duplicated on quiet ones.
        _merge_dist = zone_atr * _naked_engine_atr_mult("zone_merge_atr_mult", 0.5, asset_type)
        res_zones = _merge_zones(res_zones, _merge_dist)
        sup_zones = _merge_zones(sup_zones, _merge_dist)

        # Zone filtering is now direction-dependent in analyze_structure_direction()
        # via valid_res/valid_sup filters. Removed direction-independent filtering
        # that incorrectly removed zones relevant for the opposite direction.

        d1_adx_val = _adx_from_indicator_snap(d1_snap)
        h4_adx_val = _adx_from_indicator_snap(h4_snap)
        try:
            from indicators import calc_adx

            if d1_adx_val is None:
                _d1_hi = [float(c["high"]) for c in d1_candles]
                _d1_lo = [float(c["low"]) for c in d1_candles]
                _d1_cl = [float(c["close"]) for c in d1_candles]
                for _v in reversed((calc_adx(_d1_hi, _d1_lo, _d1_cl, 14).get("adx") or [])):
                    if _v is not None:
                        d1_adx_val = float(_v)
                        break
            if h4_adx_val is None:
                _h4_hi = [float(c["high"]) for c in h4_candles]
                _h4_lo = [float(c["low"]) for c in h4_candles]
                _h4_cl = [float(c["close"]) for c in h4_candles]
                for _v in reversed((calc_adx(_h4_hi, _h4_lo, _h4_cl, 14).get("adx") or [])):
                    if _v is not None:
                        h4_adx_val = float(_v)
                        break
        except Exception:
            pass

        _structure_quality_score = None
        if str(asset_type or "").lower() == "crypto":
            _structure_quality_score = crypto_struct_diag.get("structure_quality_score")

        enable_profile_context = bool(enable_profile_context) and engine_b_profile_context_enabled(
            asset_type,
            score_group=_score_group,
            profile_trusted_override=_engine_b_resolve_profile_trusted_override(
                _score_group, style
            ),
        )

        # Volume profile: precompute profile candles and VP once (direction-independent part)
        _vp_profile = None
        _vp_source_tf = None
        _vp_sessions = None
        _aggtrade_context = _engine_b_crypto_aggtrade_context(
            asset_type,
            pair,
            reference_ts=trade_bucket_reference_ts,
            require_fresh=trade_bucket_require_fresh,
        )
        if enable_profile_context:
            if _aggtrade_context.get("aggtrade_profile"):
                _vp_profile = _aggtrade_context.get("aggtrade_profile")
                _vp_source_tf = _aggtrade_context.get("aggtrade_profile_source_tf")
            elif not (
                str(asset_type or "").lower() == "crypto"
                and bool(_aggtrade_context.get("aggtrade_required"))
            ):
                try:
                    from volume_profile import compute_fixed_range_volume_profile, split_completed_sessions
                    _vp_candles = h1_candles if len(h1_candles or []) >= 24 else h4_candles
                    _vp_source_tf = "H1" if len(h1_candles or []) >= 24 else "H4"
                    _vp_sessions = split_completed_sessions(_vp_candles or [], asset_type)
                    _vp_prev = _vp_sessions.get("prev_session_candles", [])
                    if _vp_prev:
                        _vp_profile = compute_fixed_range_volume_profile(_vp_prev)
                except Exception:
                    pass

        return {
            "_error": None,
            "_registry_symbol": registry_symbol,
            "_tfs": _tfs,
            "_structure_tf": structure_tf,
            "_trigger_candles": trigger_candles,
            "_struct_candles": struct_candles,
            "_score_group": _score_group,
            "asset_type": asset_type,
            "atr": atr,
            "d1_atr": d1_atr,
            "struct_atr": struct_atr,
            "zone_atr": zone_atr,
            "struct_highs": struct_highs,
            "struct_lows": struct_lows,
            "struct_closes": struct_closes,
            "h4_highs": h4_highs,
            "h4_lows": h4_lows,
            "d1_highs": d1_highs,
            "d1_lows": d1_lows,
            "d1_closes": d1_closes,
            "res_zones": res_zones,
            "sup_zones": sup_zones,
            "active_fvgs": active_fvgs,
            "sequence_data": sequence_data,
            "macro_seq_data": macro_seq_data,
            "bos_data": bos_data,
            "d1_bos": d1_bos,
            "sweep_data": sweep_data,
            "bos_mtf_confirmed": bos_mtf_confirmed,
            "order_blocks": order_blocks,
            "choch_data": choch_data,
            "breaker_block": breaker_block,
            "breaker_blocks": breaker_blocks,
            "asset_struct_diag": asset_struct_diag,
            "crypto_struct_diag": crypto_struct_diag,
            "d1_crypto_diag": d1_crypto_diag,
            "d1_asset_diag": d1_asset_diag,
            "d1_order_blocks_raw": d1_order_blocks_raw,
            "d1_fvgs_raw": d1_fvgs_raw,
            "d1_conflict_window": d1_conflict_window,
            "d1_adx": d1_adx_val,
            "h4_adx": h4_adx_val,
            "_structure_quality_score": _structure_quality_score,
            # Volume profile precompute
            "_vp_profile": _vp_profile,
            "_vp_source_tf": _vp_source_tf,
            "_vp_sessions": _vp_sessions,
            "aggtrade_enabled": _aggtrade_context.get("aggtrade_enabled"),
            "aggtrade_required": _aggtrade_context.get("aggtrade_required"),
            "aggtrade_available": _aggtrade_context.get("aggtrade_available"),
            "aggtrade_reason": _aggtrade_context.get("aggtrade_reason"),
            "aggtrade_cvd_direction": _aggtrade_context.get("aggtrade_cvd_direction"),
            "aggtrade_cvd_source": _aggtrade_context.get("aggtrade_cvd_source"),
            "aggtrade_cvd_slope": _aggtrade_context.get("aggtrade_cvd_slope"),
            "aggtrade_cvd_bucket_count": _aggtrade_context.get("aggtrade_cvd_bucket_count"),
            "engine_b_data_fidelity": _aggtrade_context.get("engine_b_data_fidelity"),
            "structure_tf": structure_tf,
            "regime": regime,
            "fallback_rr": fallback_rr,
            "style": style,
            "pair": pair,
            "enable_profile_context": enable_profile_context,
            "forming_strip_diagnostics": forming_strip_diag,
        }

    def analyze_structure_direction(
        self,
        precompute: dict,
        current_price: float,
        direction: str,
    ) -> dict:
        """Apply direction to precomputed structure data (see ``precompute_structure_data``).

        Returns the same result dict as ``analyze_structure()`` but uses the
        precomputed context to avoid duplicating expensive direction-independent
        work (zone finding, FVG detection, BOS, CHoCH, swing detection, etc.).
        """
        if precompute.get("_error"):
            return _engine_b_analyze_structure_error_result(
                precompute,
                direction=direction,
            )

        # Unpack precomputed data
        atr = precompute["atr"]
        d1_atr = precompute["d1_atr"]
        struct_atr = precompute["struct_atr"]
        zone_atr = precompute["zone_atr"]
        res_zones = precompute["res_zones"]
        sup_zones = precompute["sup_zones"]
        active_fvgs = precompute["active_fvgs"]
        sequence_data = precompute["sequence_data"]
        macro_seq_data = precompute["macro_seq_data"]
        bos_data = precompute["bos_data"]
        d1_bos = precompute["d1_bos"]
        sweep_data = precompute["sweep_data"]
        choch_data = precompute["choch_data"]
        order_blocks = precompute["order_blocks"]
        breaker_block = precompute["breaker_block"]
        breaker_blocks = precompute["breaker_blocks"]
        asset_struct_diag = precompute["asset_struct_diag"]
        crypto_struct_diag = precompute["crypto_struct_diag"]
        d1_order_blocks_raw = precompute["d1_order_blocks_raw"]
        d1_fvgs_raw = precompute["d1_fvgs_raw"]
        d1_conflict_window = precompute["d1_conflict_window"]
        trigger_candles = precompute["_trigger_candles"]
        struct_candles = precompute["_struct_candles"]
        tfs = precompute["_tfs"]
        structure_tf = precompute["_structure_tf"]
        bos_mtf_confirmed = precompute["bos_mtf_confirmed"]
        asset_type = precompute["asset_type"]
        regime = precompute["regime"]
        fallback_rr = precompute["fallback_rr"]
        style = precompute["style"]
        pair = precompute.get("pair")
        enable_profile_context = precompute.get("enable_profile_context", True)
        registry_symbol = precompute.get("_registry_symbol")
        d1_adx_val = precompute.get("d1_adx")
        h4_adx_val = precompute.get("h4_adx")

        # H-4: ATR <= 0 makes _swing_cache return empty peak/trough arrays, which
        # cascades into _determine_sequence using the full-series max/min as the
        # "recent" swing — producing an absurdly wide SL that fails the RR gate with
        # no explanation. Fail closed with a clear reason instead.
        try:
            _atr_ok = float(atr) > 0 and float(struct_atr) > 0
        except (TypeError, ValueError):
            _atr_ok = False
        if not _atr_ok:
            return _engine_b_analyze_structure_error_result(
                {**precompute, "_error": "ENGINE_B_REASON_ATR_ZERO"},
                direction=direction,
            )

        # D1 PD-array: direction-dependent filtering
        d1_pd_array_conflict: bool = False
        d1_conflict_details: list = []
        d1_conflict_metric_details: list = []
        try:
            for ob in d1_order_blocks_raw:
                ob_mid = (ob["top"] + ob["bottom"]) / 2.0
                ob_dist = abs(current_price - ob_mid)
                if ob_dist > d1_conflict_window:
                    continue
                if direction == "LONG" and ob["type"] == "bearish" and ob_mid > current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bearish_OB@{ob_mid:.5f} (str={ob['strength']})")
                    d1_conflict_metric_details.append({"zone_type": "bearish_OB", "zone_price_range": {"top": ob["top"], "bottom": ob["bottom"]}, "zone_mid": ob_mid, "distance_to_conflict": ob_dist, "distance_in_atr": (ob_dist / d1_atr) if d1_atr > 0 else None, "conflict_side": "above_price", "entry_side_or_tp_side": "tp_side", "strength": ob.get("strength")})
                elif direction == "SHORT" and ob["type"] == "bullish" and ob_mid < current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bullish_OB@{ob_mid:.5f} (str={ob['strength']})")
                    d1_conflict_metric_details.append({"zone_type": "bullish_OB", "zone_price_range": {"top": ob["top"], "bottom": ob["bottom"]}, "zone_mid": ob_mid, "distance_to_conflict": ob_dist, "distance_in_atr": (ob_dist / d1_atr) if d1_atr > 0 else None, "conflict_side": "below_price", "entry_side_or_tp_side": "tp_side", "strength": ob.get("strength")})
            for fvg in d1_fvgs_raw:
                fvg_mid = (fvg["top"] + fvg["bottom"]) / 2.0
                fvg_dist = abs(current_price - fvg_mid)
                if fvg_dist > d1_conflict_window:
                    continue
                if direction == "LONG" and fvg["type"] == "bearish" and fvg_mid > current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bearish_FVG@{fvg_mid:.5f}")
                    d1_conflict_metric_details.append({"zone_type": "bearish_FVG", "zone_price_range": {"top": fvg["top"], "bottom": fvg["bottom"]}, "zone_mid": fvg_mid, "distance_to_conflict": fvg_dist, "distance_in_atr": (fvg_dist / d1_atr) if d1_atr > 0 else None, "conflict_side": "above_price", "entry_side_or_tp_side": "tp_side"})
                elif direction == "SHORT" and fvg["type"] == "bullish" and fvg_mid < current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bullish_FVG@{fvg_mid:.5f}")
                    d1_conflict_metric_details.append({"zone_type": "bullish_FVG", "zone_price_range": {"top": fvg["top"], "bottom": fvg["bottom"]}, "zone_mid": fvg_mid, "distance_to_conflict": fvg_dist, "distance_in_atr": (fvg_dist / d1_atr) if d1_atr > 0 else None, "conflict_side": "below_price", "entry_side_or_tp_side": "tp_side"})
        except Exception as exc:
            # H-2: surface malformed D1 OB/FVG data so the -0.25 conflict penalty
            # being skipped is observable, instead of failing silently.
            log.warning("[ENGINE_B] D1 PD-array conflict check failed: %s", exc)

        # Nearest resistance above price
        valid_res = [z for z in res_zones if z["upper"] >= current_price]
        nearest_res = (
            min(valid_res, key=lambda x: 0.0 if x["lower"] <= current_price <= x["upper"] else max(0.0, x["lower"] - current_price))
            if valid_res else None
        )

        # Nearest support below price
        valid_sup = [z for z in sup_zones if z["lower"] <= current_price]
        nearest_sup = (
            min(valid_sup, key=lambda x: 0.0 if x["lower"] <= current_price <= x["upper"] else max(0.0, current_price - x["upper"]))
            if valid_sup else None
        )

        anchored_low = min(current_price, sequence_data["recent_low"])
        anchored_high = max(current_price, sequence_data["recent_high"])

        multipliers = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_multipliers", {})
        buf = multipliers.get(regime.upper(), multipliers.get("RANGING", {"upper": 0.5, "lower": 1.2, "sl": 1.0}))
        sl_mult = float(buf.get("sl", 1.0))
        if bool(config.CONFIG.get("ENGINE_B_STRUCTURAL_SL_USE_STYLE_ATR_MULTS", False)):
            _style_mults = (config.CONFIG.get("STYLE_ATR_MULTS") or {}).get(style.lower() or "intraday", {}) or {}
            _asset_mults = _style_mults.get(asset_type.lower(), None)
            if isinstance(_asset_mults, dict):
                _style_sl = float(_asset_mults.get("sl", sl_mult))
                _regime_scale = {"TRENDING": 1.00, "RANGING": 0.90, "HIGH_VOLATILITY": 1.20, "LOW_VOLATILITY": 0.85}.get(regime.upper(), 1.0)
                sl_mult = _style_sl * _regime_scale
        structural_tp_buffer_mult = _engine_b_structural_tp_buffer_atr_mult()

        # SL with sweep override
        sl = (anchored_low - (atr * sl_mult) if direction == "LONG" else anchored_high + (atr * sl_mult))
        if direction == "LONG" and sweep_data["bull_sweep"] and sweep_data["sweep_low"] is not None:
            sl = sweep_data["sweep_low"] - (atr * sl_mult)
        elif direction == "SHORT" and sweep_data["bear_sweep"] and sweep_data["sweep_high"] is not None:
            sl = sweep_data["sweep_high"] + (atr * sl_mult)

        # TP from structural zones
        tp = None
        tp_source = "fallback_rr"
        tp_structural_limited = False
        structural_target_candidates = []

        if direction == "LONG" and valid_res:
            for zone in sorted(valid_res, key=lambda z: z["lower"]):
                structural_tp = _engine_b_structural_target_price(zone, direction, atr, structural_tp_buffer_mult)
                target_distance = structural_tp - current_price
                structural_target_candidates.append({"target_type": "resistance_zone", "target_price": structural_tp, "zone": dict(zone), "correct_side": structural_tp > current_price, "distance_to_target": target_distance, "atr_multiple_to_target": (target_distance / atr) if atr > 0 else None, "selected": False, "rejected_reason": "too_close_or_wrong_side" if structural_tp <= current_price + (atr * 0.5) else None})
                if structural_tp <= current_price + (atr * 0.5):
                    tp_structural_limited = True
                    continue
                tp = structural_tp
                tp_source = "structural_zone"
                structural_target_candidates[-1]["selected"] = True
                break
        elif direction == "SHORT" and valid_sup:
            for zone in sorted(valid_sup, key=lambda z: z["upper"], reverse=True):
                structural_tp = _engine_b_structural_target_price(zone, direction, atr, structural_tp_buffer_mult)
                target_distance = current_price - structural_tp
                structural_target_candidates.append({"target_type": "support_zone", "target_price": structural_tp, "zone": dict(zone), "correct_side": structural_tp < current_price, "distance_to_target": target_distance, "atr_multiple_to_target": (target_distance / atr) if atr > 0 else None, "selected": False, "rejected_reason": "too_close_or_wrong_side" if structural_tp >= current_price - (atr * 0.5) else None})
                if structural_tp >= current_price - (atr * 0.5):
                    tp_structural_limited = True
                    continue
                tp = structural_tp
                tp_source = "structural_zone"
                structural_target_candidates[-1]["selected"] = True
                break

        if tp is None:
            sl_dist = abs(current_price - sl) if (sl is not None) else (atr * sl_mult)
            if sl_dist == 0:
                sl_dist = atr * sl_mult
            tp = current_price + (sl_dist * fallback_rr) if direction == "LONG" else current_price - (sl_dist * fallback_rr)

        selected_structural_target = next((c for c in structural_target_candidates if c.get("selected")), None)

        bos_confirmed = (direction == "LONG" and bos_data["bos_bull"]) or (direction == "SHORT" and bos_data["bos_bear"])
        choch_confirmed = (direction == "LONG" and choch_data["choch_bull"]) or (direction == "SHORT" and choch_data["choch_bear"])

        fvg_overlap = False
        if direction == "LONG" and nearest_sup:
            fvg_overlap = nearest_sup.get("fvg_overlap", False)
        elif direction == "SHORT" and nearest_res:
            fvg_overlap = nearest_res.get("fvg_overlap", False)

        active_zone = nearest_sup if direction == "LONG" else nearest_res
        _zone_score_group = (
            (pair or {}).get("score_group")
            or (pair or {}).get("group")
            or (pair or {}).get("asset_group")
        )
        zone_ctx = self._zone_context(
            active_zone,
            current_price,
            atr,
            direction,
            trigger_candles,
            asset_type=asset_type,
            score_group=_zone_score_group,
        )

        _ob_min_strength = config.CONFIG.get("NAKED_ENGINE", {}).get("ob_min_strength", 50)
        _ob_at_zone = False
        if active_zone and order_blocks:
            az_lower = active_zone.get("lower", 0)
            az_upper = active_zone.get("upper", 0)
            for ob in order_blocks:
                if not (ob["top"] < az_lower or ob["bottom"] > az_upper):
                    if ob.get("strength", 0) >= _ob_min_strength:
                        _ob_at_zone = True
                        break
        trigger_ctx = self._price_action_trigger(
            trigger_candles, direction, atr,
            zone_ctx["zone_touched"] or zone_ctx["near_zone"],
            bos_confirmed,
            is_trending=(
                (direction == "LONG" and sequence_data["state"] == "HH_HL" and macro_seq_data["state"] == "HH_HL")
                or (direction == "SHORT" and sequence_data["state"] == "LH_LL" and macro_seq_data["state"] == "LH_LL")
            ),
            asset_type=asset_type,
        )

        latest_trigger_candle_time = None
        if trigger_candles:
            _last_tc = trigger_candles[-1]
            latest_trigger_candle_time = _last_tc.get("time") or _last_tc.get("timestamp") or _last_tc.get("datetime") or _last_tc.get("date")

        # Volume profile interaction (direction-dependent part)
        _profile_result = {
            "prev_session_profile_valid": False, "prev_session_profile_source_tf": None,
            "prev_session_poc": None, "prev_session_vah": None, "prev_session_val": None,
            "prev_session_profile_high": None, "prev_session_profile_low": None,
            "prev_session_total_volume": None, "prev_session_start": None, "prev_session_end": None,
            "profile_in_play": False, "profile_level_in_play": None,
            "inside_prev_value_area": False, "above_prev_value_area": False, "below_prev_value_area": False,
            "touched_poc": False, "touched_vah": False, "touched_val": False,
            "rejected_from_poc": False, "rejected_from_vah": False, "rejected_from_val": False,
            "accepted_at_poc": False, "accepted_inside_value": False,
            "returned_to_value": False, "failed_return_to_value": False,
            "profile_bias": "neutral", "profile_reaction_strength": 0.0, "profile_notes": "",
        }
        _vp_profile = precompute.get("_vp_profile")
        _vp_source_tf = precompute.get("_vp_source_tf")
        if enable_profile_context and _vp_profile and _vp_profile.get("profile_valid"):
            try:
                from volume_profile import classify_profile_interaction
                _interaction = classify_profile_interaction(
                    current_price=current_price,
                    recent_candles=(trigger_candles or struct_candles or [])[-10:],
                    direction=direction,
                    poc=_vp_profile["poc"],
                    vah=_vp_profile["vah"],
                    val=_vp_profile["val"],
                    atr=atr,
                )
                _profile_result.update({
                    "prev_session_profile_valid": True,
                    "prev_session_profile_source_tf": _vp_source_tf,
                    "prev_session_poc": _vp_profile["poc"],
                    "prev_session_vah": _vp_profile["vah"],
                    "prev_session_val": _vp_profile["val"],
                    "prev_session_profile_high": _vp_profile["session_high"],
                    "prev_session_profile_low": _vp_profile["session_low"],
                    "prev_session_total_volume": _vp_profile["total_volume"],
                    "prev_session_start": _vp_profile["session_start"],
                    "prev_session_end": _vp_profile["session_end"],
                    **_interaction,
                })
            except Exception:
                pass

        is_sweep_event = (direction == "LONG" and sweep_data["bull_sweep"]) or (direction == "SHORT" and sweep_data["bear_sweep"])

        forex_session_structure = None
        if str(asset_type or "").lower() == "forex":
            forex_session_structure = _engine_b_forex_session_structure_context(
                asset_type=asset_type, candle_time=latest_trigger_candle_time,
                zone_context=bool(zone_ctx["zone_touched"] or zone_ctx["near_zone"]),
                ob_at_zone=bool(_ob_at_zone), fvg_overlap=bool(fvg_overlap),
                liquidity_sweep=bool(is_sweep_event),
            )
        equity_session_structure = None
        if asset_struct_diag.get("asset_class") in {"stock", "index", "etf", "etf_bond"}:
            equity_session_structure = _engine_b_equity_session_structure_context(
                asset_class=asset_struct_diag.get("asset_class"), candle_time=latest_trigger_candle_time,
                zone_context=bool(zone_ctx["zone_touched"] or zone_ctx["near_zone"]),
                ob_at_zone=bool(_ob_at_zone), fvg_overlap=bool(fvg_overlap),
                liquidity_sweep=bool(is_sweep_event),
            )

        return {
            "structural_verdict": "CLEAR",
            "direction": direction,
            "nearest_resistance_zone": nearest_res,
            "nearest_support_zone": nearest_sup,
            "current_swing_sequence": sequence_data["state"],
            "macro_swing_sequence": macro_seq_data["state"],
            "recommended_stop_loss": sl,
            "recommended_take_profit": tp,
            "tp_source": tp_source,
            "tp_structural_limited": tp_structural_limited,
            "current_price": current_price,
            "structural_target_candidates": structural_target_candidates,
            "selected_structural_target": selected_structural_target,
            "nearest_target_type": (selected_structural_target or {}).get("target_type"),
            "nearest_target_price": (selected_structural_target or {}).get("target_price"),
            "distance_to_res": (nearest_res["lower"] - current_price) if nearest_res else None,
            "distance_to_sup": (current_price - nearest_sup["upper"]) if nearest_sup else None,
            "atr": atr,
            "d1_atr": d1_atr,
            "struct_atr": struct_atr,
            "zone_atr": zone_atr,
            "bos_confirmed": bos_confirmed,
            "bos_mtf_confirmed": bos_mtf_confirmed,
            "bos_volume_confirmed": bos_data.get("bos_volume_confirmed", False),
            "bos_data": bos_data,
            "d1_bos_data": d1_bos,
            "sweep_data": sweep_data,
            "choch_data": choch_data,
            "choch_confirmed": choch_confirmed,
            "breaker_block": breaker_block,
            "breaker_blocks": breaker_blocks,
            "order_blocks": order_blocks,
            "ob_at_zone": _ob_at_zone,
            "fvg_overlap": fvg_overlap,
            "active_fvgs": active_fvgs,
            "liquidity_sweep": is_sweep_event,
            # Equal extrema relative to the scanned direction: equal lows are the
            # liquidity pool for LONG sweeps, equal highs for SHORT. The precompute
            # sequence pass runs direction-independent, so resolve per side here.
            "has_equal_extrema": bool(
                sequence_data.get("equal_lows", False)
                if direction == "LONG"
                else sequence_data.get("equal_highs", False)
            )
            if ("equal_lows" in sequence_data or "equal_highs" in sequence_data)
            else sequence_data.get("has_equal_extrema", False),
            "active_zone_distance": zone_ctx["distance"],
            "near_active_zone": zone_ctx["near_zone"],
            "zone_touched": zone_ctx["zone_touched"],
            "trigger_pattern": trigger_ctx["pattern"],
            "trigger_ok": trigger_ctx["trigger_ok"],
            "rejection_candle": trigger_ctx["rejection"],
            "engulfing_candle": trigger_ctx["engulfing"],
            "inside_break_candle": trigger_ctx["inside_break"],
            "strong_close": trigger_ctx["strong_close"],
            "trigger_timeframe": tfs["trigger"],
            "latest_confirmed_trigger_candle_time": latest_trigger_candle_time,
            "structure_tf": structure_tf,
            "asset_type": asset_type,
            "pair_display": (pair or {}).get("display") or (pair or {}).get("symbol"),
            "aggtrade_enabled": precompute.get("aggtrade_enabled"),
            "aggtrade_required": precompute.get("aggtrade_required"),
            "aggtrade_available": precompute.get("aggtrade_available"),
            "aggtrade_reason": precompute.get("aggtrade_reason"),
            "aggtrade_cvd_direction": precompute.get("aggtrade_cvd_direction"),
            "aggtrade_cvd_source": precompute.get("aggtrade_cvd_source"),
            "aggtrade_cvd_slope": precompute.get("aggtrade_cvd_slope"),
            "aggtrade_cvd_bucket_count": precompute.get("aggtrade_cvd_bucket_count"),
            "engine_b_data_fidelity": precompute.get("engine_b_data_fidelity"),
            **({"forex_session_structure": forex_session_structure} if str(asset_type or "").lower() == "forex" else {}),
            **({"asset_class_structure_diagnostics": asset_struct_diag} if asset_struct_diag.get("asset_class") in {"stock", "index", "commodity", "etf", "etf_bond"} else {}),
            **({"equity_session_structure": equity_session_structure} if equity_session_structure is not None else {}),
            **({"crypto_structure_diagnostics": crypto_struct_diag} if str(asset_type or "").lower() == "crypto" else {}),
            "d1_adx": d1_adx_val,
            "h4_adx": h4_adx_val,
            "engine_b_independent_direction": self._determine_independent_direction(
                h1_sequence=sequence_data["state"], h4_sequence=macro_seq_data["state"],
                bos_data=bos_data, d1_bos=d1_bos, choch_data=choch_data, sweep_data=sweep_data,
            ),
            "d1_order_blocks": d1_order_blocks_raw,
            "d1_fvgs": d1_fvgs_raw,
            "d1_pd_array_conflict": d1_pd_array_conflict,
            "d1_conflict_details": d1_conflict_details,
            "d1_conflict_metric_details": d1_conflict_metric_details,
            "d1_pd_array_conflict_window_atr_mult": _engine_b_d1_conflict_window_atr_mult(),
            **_profile_result,
        }

    def analyze_structure(
        self,
        d1_candles: list,
        h4_candles: list,
        h1_candles: list,
        current_price: float,
        direction: str,
        atr: float,
        regime: str = "RANGING",
        fallback_rr: float = 2.0,
        asset_type: str = "",
        enable_zone_registry: bool = True,
        enable_profile_context: bool = True,
        d1_snap: dict | None = None,
        h4_snap: dict | None = None,
        style: str = "intraday",
        pair: dict | None = None,
        trade_bucket_reference_ts=None,
        trade_bucket_require_fresh: bool = True,
    ) -> dict:
        """
        Analyzes raw candle data to find Support/Resistance zones and trend sequence.
        Returns structural verdict used by the Comparator in athena.py.

        Delegates to ``precompute_structure_data()`` + ``analyze_structure_direction()``
        for backward compatibility. For optimal performance (especially during backtesting),
        call ``precompute_structure_data()`` once per bar and iterate directions with
        ``analyze_structure_direction()``.
        """
        pre = self.precompute_structure_data(
            d1_candles, h4_candles, h1_candles, current_price, atr,
            regime=regime, fallback_rr=fallback_rr, asset_type=asset_type,
            enable_zone_registry=enable_zone_registry,
            enable_profile_context=enable_profile_context,
            d1_snap=d1_snap, h4_snap=h4_snap, style=style, pair=pair,
            trade_bucket_reference_ts=trade_bucket_reference_ts,
            trade_bucket_require_fresh=trade_bucket_require_fresh,
        )
        return self.analyze_structure_direction(pre, current_price, direction)

    def _determine_lifecycle_state(self, res: dict, current_price: float, direction: str, trigger_ok: bool) -> tuple[str, str]:
        bos_confirmed = res.get("bos_confirmed", False)
        choch_confirmed = res.get("choch_confirmed", False)
        sweep = res.get("liquidity_sweep", False)

        zone_touched = res.get("zone_touched", False)
        near_zone = res.get("near_active_zone", False)

        sl = res.get("recommended_stop_loss")
        tp = res.get("recommended_take_profit")

        # 1. Invalidated checking
        if sl is not None:
            if direction == "LONG" and current_price < sl:
                return "invalidated", "Price breached Stop Loss level"
            elif direction == "SHORT" and current_price > sl:
                return "invalidated", "Price breached Stop Loss level"

        # 2. Expired checking
        if tp is not None:
            if direction == "LONG" and current_price >= tp:
                return "expired", "Target level already reached prior to entry"
            elif direction == "SHORT" and current_price <= tp:
                return "expired", "Target level already reached prior to entry"

        structural_setup = bos_confirmed or choch_confirmed or sweep

        # 3. Candidate
        if not structural_setup:
            return "candidate", "Awaiting structural confirmation (BOS/CHoCH/Sweep)"

        # 4. Triggered
        if trigger_ok and (zone_touched or near_zone):
            return "triggered", "Valid trigger pattern printed at the key entry zone"

        # 5. Armed
        if zone_touched or near_zone:
            return "armed", "Price is actively testing the key structural entry zone"

        # 6. Retracing (Pullback)
        nearest_sup = res.get("nearest_support_zone")
        nearest_res = res.get("nearest_resistance_zone")
        
        if direction == "LONG" and nearest_sup:
            center = nearest_sup.get("center", nearest_sup.get("upper", current_price))
            if current_price > center:
                return "retracing", "Price pulling back towards support zone"
        elif direction == "SHORT" and nearest_res:
            center = nearest_res.get("center", nearest_res.get("lower", current_price))
            if current_price < center:
                return "retracing", "Price pulling back towards resistance zone"

        # 7. Confirmed default
        return "confirmed", "Structural break confirmed, awaiting retracement to zone"


    def calculate_confidence(
        self,
        res: dict,
        current_price: float,
        direction: str,
        learning_ctx: dict = None,
        entry_candles: list | None = None,
        style_profile: dict | None = None,
    ) -> dict:
        """Calculate Engine B confidence score and gate verdict.

        Stage 3.7: Explicit gate definitions for Engine B.
        The 6 mandatory gates (flexible mode) are:
          1. structure_ok  — not hard_counter_trend; micro/macro aligned OR BOS/sweep
          2. location_ok   — zone_touched OR near_zone OR ob_at_zone OR breakout_ok
          3. entry_ok      — trigger_ok OR (breakout + volume) OR (sweep/CHoCH + zone)
          4. room_ok       — distance to next structural level >= min_room_atr × ATR
          5. rr_ok         — execution RR >= min_rr (style/asset dependent)
          6. macro_ok      — macro_aligned (only enforced when require_macro_align=True)

        Bonus points (add to score, not required for pass):
          +1  bos_mtf_confirmed  — multi-TF BOS alignment
          +1  volume_ok          — volume confirmation on breakout
        """
        try:
            atr = float(res.get("atr", 0.0) or 0.0)
        except (TypeError, ValueError):
            atr = 0.0
        atr_val = atr if atr > 0 else 0.0001
        h1_seq = res.get("current_swing_sequence", "")
        h4_seq = res.get("macro_swing_sequence", "")
        profile = style_profile if isinstance(style_profile, dict) else {}
        exec_style = str(profile.get("style", "intraday"))
        min_room_atr = float(profile.get("min_room_atr", 0.35))
        min_rr = float(profile.get("min_rr", 1.0))
        asset_type_lower = str(res.get("asset_type") or "").lower()
        _rma_cfg = profile.get("require_macro_align", False)
        if isinstance(_rma_cfg, dict):
            require_macro_align = bool(_rma_cfg.get(asset_type_lower, False))
        else:
            require_macro_align = bool(_rma_cfg)
        checklist_mode = str(profile.get("checklist_mode", "flexible")).lower()
        allow_breakout_entry = bool(
            profile.get("allow_breakout_entry", not require_macro_align)
        )

        micro_aligned = (direction == "LONG" and h1_seq == "HH_HL") or (
            direction == "SHORT" and h1_seq == "LH_LL"
        )
        macro_aligned = (direction == "LONG" and h4_seq == "HH_HL") or (
            direction == "SHORT" and h4_seq == "LH_LL"
        )
        hard_counter = (direction == "LONG" and h1_seq == "LH_LL" and h4_seq == "LH_LL") or (
            direction == "SHORT" and h1_seq == "HH_HL" and h4_seq == "HH_HL"
        )
        # hard_counter softening: default is a soft score penalty rather than
        # a structure_ok veto. Legacy veto mode remains available via the
        # NAKED_ENGINE.hard_counter_mode config key. Mirrors Engine A's soft
        # DI-conflict penalty from commit 3d2c3eed (factor_scoring.py:1939-1957).
        _naked_engine_cfg = config.CONFIG.get("NAKED_ENGINE", {}) or {}
        _hard_counter_mode = str(_naked_engine_cfg.get("hard_counter_mode", "penalty")).lower()
        try:
            _hard_counter_penalty_cfg = float(_naked_engine_cfg.get("hard_counter_penalty", 1.0))
        except (TypeError, ValueError):
            _hard_counter_penalty_cfg = 1.0
        _hard_counter_veto = hard_counter and _hard_counter_mode == "veto"
        bos_mtf = bool(res.get("bos_mtf_confirmed", False))
        structure_ok = (not _hard_counter_veto) and (
            micro_aligned
            or macro_aligned
            or res.get("bos_confirmed", False)
            or res.get("liquidity_sweep", False)
        )
        structure_gate_original_ok = bool(structure_ok)
        structure_gate_disabled = bool(profile.get("disable_structure_gate", False))
        if structure_gate_disabled:
            structure_ok = True

        # Forex ADX is exposed as a diagnostic (engine_b_overlay / Marcus Reid /
        # tests) but no longer gates structure_ok. Consumers can use it as a
        # soft conviction signal.
        _diag_codes: list[str] = []
        _engine_b_data_fidelity = (
            res.get("engine_b_data_fidelity")
            if isinstance(res.get("engine_b_data_fidelity"), dict)
            else {}
        )
        _aggtrade_enabled = asset_type_lower == "crypto" and bool(
            config.CONFIG.get("ENGINE_B_CRYPTO_AGGTRADE_ENABLED", True)
        )
        _aggtrade_mode = str(
            config.CONFIG.get(
                "ENGINE_B_CRYPTO_AGGTRADE_MODE",
                "required" if config.CONFIG.get("ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", True) else "preferred",
            )
        ).lower()
        if _aggtrade_mode not in {"required", "preferred", "degraded"}:
            _aggtrade_mode = "required"
        _aggtrade_required_raw = res.get("aggtrade_required")
        _aggtrade_required = bool(
            _aggtrade_enabled
            and (
                bool(_aggtrade_required_raw)
                if _aggtrade_required_raw is not None
                else bool(config.CONFIG.get("ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", True))
            )
        )
        _aggtrade_hard_required = bool(_aggtrade_required and _aggtrade_mode == "required")
        _aggtrade_available = bool(
            _aggtrade_enabled
            and res.get("aggtrade_available")
            and _engine_b_data_fidelity.get("vp_uses_real_trade_buckets")
            and _engine_b_data_fidelity.get("cvd_uses_real_trade_buckets")
        )
        _aggtrade_reason = res.get("aggtrade_reason")
        _aggtrade_cvd_direction = str(res.get("aggtrade_cvd_direction") or "").upper() or None
        _aggtrade_cvd_aligned = _aggtrade_cvd_direction == direction
        _aggtrade_cvd_opposed = (
            _aggtrade_cvd_direction in ("LONG", "SHORT")
            and _aggtrade_cvd_direction != direction
        )
        _engine_b_adx_val = None
        if str(res.get("asset_type") or "").lower() == "forex":
            _adx_val = res.get("d1_adx")
            if _adx_val is None:
                _adx_val = res.get("h4_adx")
            if _adx_val is not None:
                try:
                    _adx_val = float(_adx_val)
                except (TypeError, ValueError):
                    _adx_val = 0.0
                _engine_b_adx_val = _adx_val
                if _adx_val >= 30:
                    res["_adx_derived_regime"] = "TRENDING"
                elif _adx_val >= 20:
                    res["_adx_derived_regime"] = "NORMAL"
                else:
                    res["_adx_derived_regime"] = "RANGING"

        # Stage 2.3 (commit 8c26a078): commodity swing requires macro alignment.
        # Default True for back-compat / safety — commodities are macro-driven.
        # Set NAKED_ENGINE.commodity_swing_force_macro_align: false to trust YAML
        # (NAKED_ENGINE.style_profiles.swing.require_macro_align.commodity).
        _score_group_lower = str(profile.get("score_group") or "").lower()
        _commodity_macro_groups = {
            "nat_gas",
            "energy_oil",
            "precious_trackers",
            "commodity_other",
            "copper",
            "pgm_metals",
        }
        _force_commodity_macro = bool(
            _naked_engine_cfg.get("commodity_swing_force_macro_align", False)
        )
        _commodity_swing_macro_required = (
            _force_commodity_macro
            and exec_style == "swing"
            and (
                asset_type_lower in ("commodity", "nat_gas", "energy_oil", "precious_trackers")
                or _score_group_lower in _commodity_macro_groups
            )
        )
        if _commodity_swing_macro_required:
            require_macro_align = True

        macro_ok = macro_aligned or not require_macro_align
        zone_ok = bool(res.get("zone_touched") or res.get("near_active_zone"))
        trigger_ok = bool(res.get("trigger_ok"))
        breakout_ok = bool(res.get("bos_confirmed")) and bool(
            res.get("strong_close")
            or res.get("inside_break_candle")
            or res.get("engulfing_candle")
        )
        ob_at_zone = bool(res.get("ob_at_zone"))

        # Trend-continuation path: BOS/CHoCH/sweep confirmed with trigger pattern
        # even if price is not at a zone. Valid pullback entry in trending regimes
        # where price has moved away from structural levels.
        # C-2: this path must still require *some* structural location — either
        # proximity to an active zone or proximity to the broken BOS level — so a
        # BOS+trigger anywhere on the chart can no longer satisfy location_ok with
        # zero structural evidence.
        _bos_level = (
            (res.get("bos_data") or {}).get("last_broken_high")
            if direction == "LONG"
            else (res.get("bos_data") or {}).get("last_broken_low")
        )
        _near_bos_level = False
        if _bos_level is not None:
            try:
                _bos_prox = _naked_engine_atr_mult(
                    "trend_pullback_bos_proximity_atr_mult", 1.0, asset_type_lower
                )
                _near_bos_level = abs(
                    float(res.get("current_price")) - float(_bos_level)
                ) <= atr_val * _bos_prox
            except (TypeError, ValueError):
                _near_bos_level = False
        _trend_pullback = (
            (bool(res.get("bos_confirmed"))
             or bool(res.get("choch_confirmed"))
             or bool(res.get("liquidity_sweep")))
            and trigger_ok
            and (zone_ok or _near_bos_level)
        )

        location_ok = zone_ok or ob_at_zone or (allow_breakout_entry and breakout_ok) or _trend_pullback
        location_mode = (
            "trend_pullback"
            if _trend_pullback and not zone_ok and not ob_at_zone
            else "normal" if location_ok else "none"
        )
        location_distance_atr = None
        if res.get("active_zone_distance") is not None:
            try:
                location_distance_atr = abs(float(res.get("active_zone_distance"))) / atr_val
            except (TypeError, ValueError):
                location_distance_atr = None
        room_dist = res.get("distance_to_res") if direction == "LONG" else res.get("distance_to_sup")

        # Contextual Room Gate helper (called after rr is known).
        # Crypto and indices trade with tighter swings → smaller min_room.
        def _get_min_room_atr(rr_val, bos_confirmed, asset_class, style):
            if profile.get("min_room_atr") is not None:
                return min_room_atr
            if asset_class == "crypto":
                if style == "swing":
                    return 0.40
                if style == "intraday":
                    return 0.25
                return 0.15
            if asset_class == "index":
                return 0.20
            if style == "scalp":
                return 0.20
            if rr_val >= 2.0:
                return 0.20
            if bos_confirmed:
                return 0.25
            # Catch-all fallback: aligned with crypto intraday (0.25) so non-BOS
            # sub-2RR setups on forex/commodity/stock/ETF are not held to a
            # stricter standard than crypto. Was 0.35; lowered as part of the
            # cross-asset strictness fix.
            return 0.25

        # Structural levels from analyze_structure
        _struct_sl = res.get("recommended_stop_loss")
        _struct_tp = res.get("recommended_take_profit")

        # Resolve final execution levels for RR gating.
        # ETF score groups (bond_tlt / smallcap_em_etf / sector ETFs etc.) route to a
        # narrower ATR multiplier class so equity ETFs don't share single-stock stops.
        _exec_asset_class = resolve_engine_b_asset_class(
            asset_type_lower, profile.get("score_group")
        )
        _exec_lvl = resolve_engine_b_execution_levels(
            direction=direction,
            entry=current_price,
            structural_sl=_struct_sl,
            structural_tp=_struct_tp,
            atr=atr,
            style=exec_style,
            asset_class=_exec_asset_class,
            min_rr=min_rr,
            fallback_rr=profile.get("fallback_rr"),
            pair=res.get("pair_display") or res.get("pair") or res.get("display") or res.get("symbol"),
            score_group=profile.get("score_group"),
        )
        # Structural TP side check — kept for diagnostics / ENGINE_B_REASON_TP_WRONG_SIDE
        sl = _exec_lvl["execution_sl"]
        tp = _exec_lvl["execution_tp"]
        rr = _exec_lvl["rr_used_for_gate"]
        rr_ok = bool(_exec_lvl["execution_levels_valid"]) and rr >= min_rr

        # FIX 7: Apply contextual room gate after rr is computed
        _effective_min_room_atr = _get_min_room_atr(rr, bool(res.get("bos_confirmed")), asset_type_lower, exec_style)
        if config.CONFIG.get("ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE", True):
            if room_dist is None:
                # In strong trends with confirmed BOS, no opposing zone means
                # the trend has room to run — pass the room gate.
                _h1_trend = (direction == "LONG" and h1_seq == "HH_HL") or (
                    direction == "SHORT" and h1_seq == "LH_LL"
                )
                _h4_trend = (direction == "LONG" and h4_seq == "HH_HL") or (
                    direction == "SHORT" and h4_seq == "LH_LL"
                )
                _bos_ok = bool(res.get("bos_confirmed"))
                room_ok = (_h1_trend or _h4_trend) and _bos_ok
            else:
                room_ok = room_dist >= atr_val * _effective_min_room_atr
        else:
            room_ok = room_dist is None or room_dist >= atr_val * _effective_min_room_atr
        # tp_side_ok reflects structural TP for diagnostic purposes
        tp_side_ok = _exec_lvl["structural_tp_valid"]

        stop_valid = bool(
            sl is not None and (
                (direction == "LONG" and sl < current_price)
                or (direction == "SHORT" and sl > current_price)
            )
        )

        original_trigger_ok = trigger_ok
        original_location_ok = location_ok
        research_lab_detail = _engine_b_research_lab_candidate_gates(
            res=res,
            direction=direction,
            entry_candles=entry_candles,
            current_price=current_price,
            atr_val=atr_val,
            profile=profile,
        )
        research_entry_ok = bool(research_lab_detail.get("entry_ok"))
        research_location_ok = bool(research_lab_detail.get("location_ok"))
        if research_location_ok and not location_ok:
            location_ok = True
            location_mode = "research_lab"
        if research_entry_ok and not trigger_ok:
            trigger_ok = True

        # entry_ok: trigger OR breakout-with-volume OR sweep-at-zone OR choch-at-zone.
        # The absorption_confirmed mean-reversion branch was removed (audit LOW #19):
        # analyze_structure_direction never set absorption_confirmed or
        # location_at_extreme, so the branch was dead. Only scalp_engine.py sets
        # absorption_confirmed. Re-add when Engine B gains absorption detection.
        entry_ok = (
            trigger_ok
            or (breakout_ok and bool(res.get("bos_volume_confirmed", False)))
            or (bool(res.get("liquidity_sweep")) and zone_ok)
            or (bool(res.get("choch_confirmed")) and zone_ok)
        )

        # Accept both structural TPs and synthetic-fallback TPs as a "meaningful"
        # target that can substitute for room. Raw ATR TPs do not qualify
        # because they carry no structural justification.
        _level_mode_str = str(_exec_lvl.get("level_mode") or "")
        _rr_space_tp_qualified = _level_mode_str.endswith("_structural_tp") or _level_mode_str.endswith(
            "_fallback_rr_tp"
        )
        # A synthetic fallback TP that replaced a structural TP capped by a nearby
        # opposing zone must not substitute for room: the wall that made the
        # structural target fail min_rr is still in front of price, and the
        # synthetic target sits beyond it. Substitution stays valid for structural
        # TPs and for synthetic TPs created because no structural target existed
        # (missing/wrong-side TP — no opposing wall was mapped).
        _rr_space_capped_tp_blocked = False
        if (
            _rr_space_tp_qualified
            and _level_mode_str.endswith("_fallback_rr_tp")
            and _exec_lvl.get("fallback_tp_reason") == "structural_tp_below_min_rr"
            and bool(
                config.CONFIG.get(
                    "ENGINE_B_SPACE_RR_SUBSTITUTE_BLOCK_CAPPED_STRUCTURAL_TP", True
                )
            )
        ):
            _rr_space_tp_qualified = False
            _rr_space_capped_tp_blocked = True
        _rr_space_floor_enabled = bool(
            config.CONFIG.get("ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED", True)
        )
        try:
            _rr_space_floor_atr = float(
                config.CONFIG.get("ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR", 0.5)
            )
        except (TypeError, ValueError):
            _rr_space_floor_atr = 0.5
        _room_distance_atr = None
        if room_dist is not None:
            try:
                _room_distance_atr = float(room_dist) / atr_val
            except (TypeError, ValueError):
                _room_distance_atr = None
        _rr_space_floor_ok = (
            not _rr_space_floor_enabled
            or (_room_distance_atr is not None and _room_distance_atr >= max(0.0, _rr_space_floor_atr))
        )
        rr_can_satisfy_space = bool(rr_ok and _rr_space_tp_qualified and _rr_space_floor_ok)
        space_ok = room_ok or rr_can_satisfy_space
        # Some asset classes often have a nearby structural level while the
        # execution RR model still has valid protective distance. Keep
        # historical behavior unless the explicit asset-scoped flag is enabled.
        _rr_space_cfg = config.CONFIG.get("ENGINE_B_RR_CAN_SATISFY_SPACE_GATE", False)
        if isinstance(_rr_space_cfg, dict):
            rr_space_gate_enabled = bool(
                _rr_space_cfg.get(asset_type_lower, _rr_space_cfg.get("default", False))
            )
        else:
            rr_space_gate_enabled = bool(_rr_space_cfg)
        # Back-compat for the first Forex-only fix.
        forex_rr_space_gate_enabled = (
            asset_type_lower == "forex"
            and bool(config.CONFIG.get("ENGINE_B_FOREX_RR_CAN_SATISFY_SPACE_GATE", False))
        )
        rr_space_gate_enabled = rr_space_gate_enabled or forex_rr_space_gate_enabled
        # Per-(group,style) YAML override: when engine_b_config.yaml sets
        # space_rr_substitute for a (group, style) row, the loader places it
        # in the merged style_profile. Honour it here as the final say for
        # this scoring call, replacing the global asset-scoped flag. Without
        # this wire-up the YAML field is inert (audit HIGH #1).
        _space_rr_substitute_override = profile.get("space_rr_substitute")
        _space_rr_substitute_active = _space_rr_substitute_override is not None
        if _space_rr_substitute_active:
            rr_space_gate_enabled = bool(_space_rr_substitute_override)
        space_gate_ok = space_ok if rr_space_gate_enabled else room_ok

        # Stage 2.8: Optional volume confirmation gate.
        # Contributes to gate_score (+1 bonus) but is NOT mandatory for pass.
        volume_ok = bool(res.get("bos_confirmed") and res.get("bos_volume_confirmed", False))

        gate_confirmations = [structure_ok, location_ok, entry_ok, space_gate_ok, rr_ok]
        if require_macro_align:
            gate_confirmations.append(macro_ok)
        confirmations = list(gate_confirmations)
        bonus_points = 0.0
        # Bonus confirmations — these add to the score but don't block if missing
        if bos_mtf:
            bonus_points += 1.0  # MTF BOS alignment = extra point
        if volume_ok:
            bonus_points += 1.0  # Volume confirmation = extra point
        if ob_at_zone:
            # Strong order block at the active zone = SMC confluence. Counted in
            # max_possible (FIX 4) and expected by Engine C ("may already count
            # these as extra checklist rows"), but the award itself was missing.
            bonus_points += 1.0

        # ── Breakout Follow-Through Bonus (config-gated) ────────────────────────
        _ft_cfg = config.CONFIG.get("ENGINE_B_FOLLOW_THROUGH", {}) or {}
        _ft_enabled = bool(_ft_cfg.get("ENABLED", False))
        _ft_diagnostics_enabled = bool(_ft_cfg.get("DIAGNOSTICS_ENABLED", True))
        _ft_bonus = 0.0
        _ft_detail = {
            "enabled": _ft_enabled,
            "diagnostics_enabled": _ft_diagnostics_enabled,
        }
        if _ft_enabled or _ft_diagnostics_enabled:
            # Locate the actual breakout bar (close crossing the broken BOS level)
            # so follow-through evaluates the bars after it. Defaulting to the
            # last bar always yields zero bars to check.
            _ft_bos = res.get("bos_data") or {}
            _ft_level = (
                _ft_bos.get("last_broken_high")
                if direction == "LONG"
                else _ft_bos.get("last_broken_low")
            )
            _ft_idx = _find_breakout_bar_index(entry_candles or [], direction, _ft_level)
            if _ft_idx is not None:
                _ft_result = _breakout_follow_through(
                    entry_candles or [], direction, atr_val, breakout_bar_index=_ft_idx
                )
                _ft_result["breakout_bar_index"] = _ft_idx
            else:
                _ft_result = {
                    "score": 0.0,
                    "confidence": "no_breakout_bar",
                    "bars_checked": 0,
                    "breakout_bar_index": None,
                }
            _ft_detail.update(_ft_result)
            _ft_max = abs(float(_ft_cfg.get("MAX_BONUS", 1.5)))
            _ft_min = -abs(float(_ft_cfg.get("MIN_PENALTY", -0.5)))
            _ft_bonus = max(_ft_min, min(_ft_max, _ft_result.get("score", 0.0)))
            if _ft_enabled:
                bonus_points += _ft_bonus
            else:
                _ft_bonus = 0.0
            _ft_detail["bonus_applied"] = round(_ft_bonus, 2)

        # FIX 1: gate_score is COUNT of true booleans — never modify it
        gate_score = float(sum(1 for passed in gate_confirmations if passed))
        gate_max_possible = float(len(gate_confirmations)) if gate_confirmations else 1.0
        total_score = gate_score + bonus_points
        # FIX 4: Dynamic max_possible
        _profile_vp_context = build_engine_b_profile_vp_context(
            asset_type_lower,
            score_group=profile.get("score_group"),
            profile_trusted_override=profile.get("profile_trusted"),
        )
        _profile_scoring_active = bool(_profile_vp_context.get("enabled"))
        _profile_points_max = 1.0 if _profile_scoring_active else 0.0
        _aggtrade_cvd_bonus_enabled = bool(
            _aggtrade_enabled
            and config.CONFIG.get("ENGINE_B_CRYPTO_AGGTRADE_CVD_SCORE_ENABLED", True)
        )
        try:
            _aggtrade_cvd_bonus_max = float(
                _naked_engine_cfg.get("aggtrade_cvd_bonus", 1.0)
            )
        except (TypeError, ValueError):
            _aggtrade_cvd_bonus_max = 1.0
        _aggtrade_cvd_bonus_max = max(0.0, _aggtrade_cvd_bonus_max)
        if not _aggtrade_cvd_bonus_enabled:
            _aggtrade_cvd_bonus_max = 0.0
        bonus_count = 3 + _profile_points_max + _aggtrade_cvd_bonus_max  # bos_mtf, ob_at_zone, volume_ok + profile + aggTrade CVD
        if _ft_enabled:
            bonus_count += abs(float(_ft_cfg.get("MAX_BONUS", 1.5)))
        max_possible = gate_max_possible + bonus_count
        _profile_points = 0.0
        _profile_ok = False
        _profile_alignment = "none"
        _profile_notes = str(res.get("profile_notes") or "")
        _aggtrade_orderflow_points = 0.0
        _aggtrade_cvd_alignment = "none"
        _profile_points_blocked_by_aggtrade = bool(_aggtrade_required and not _aggtrade_available)
        if _profile_scoring_active and not _profile_points_blocked_by_aggtrade:
            _profile_valid = bool(res.get("prev_session_profile_valid", False))
            _profile_in_play = bool(res.get("profile_in_play", False))
            try:
                _profile_react = float(res.get("profile_reaction_strength", 0.0) or 0.0)
            except (TypeError, ValueError):
                _profile_react = 0.0
            _profile_bias = str(res.get("profile_bias") or "neutral").lower()
            _profile_bias_aligned = (
                (_profile_bias == "bullish" and direction == "LONG")
                or (_profile_bias == "bearish" and direction == "SHORT")
            )
            if _profile_valid and _profile_in_play and _profile_react > 0:
                if _profile_bias_aligned and _profile_react >= 0.6:
                    _profile_points = min(1.0, _profile_react)
                    _profile_ok = True
                    _profile_alignment = "strong"
                elif _profile_bias_aligned and _profile_react >= 0.3:
                    _profile_points = round(_profile_react * 0.5, 2)
                    _profile_alignment = "moderate"
                else:
                    _profile_alignment = "weak"
                bonus_points += _profile_points
                total_score += _profile_points
        if _aggtrade_cvd_bonus_enabled:
            if _aggtrade_available and _aggtrade_cvd_aligned:
                _aggtrade_orderflow_points = _aggtrade_cvd_bonus_max
                _aggtrade_cvd_alignment = "aligned"
                bonus_points += _aggtrade_orderflow_points
                total_score += _aggtrade_orderflow_points
            elif _aggtrade_available and _aggtrade_cvd_opposed:
                _aggtrade_cvd_alignment = "opposed"
            elif _aggtrade_available and _aggtrade_cvd_direction:
                _aggtrade_cvd_alignment = "neutral"
        try:
            _aggtrade_missing_penalty = float(
                config.CONFIG.get("ENGINE_B_CRYPTO_AGGTRADE_MISSING_PENALTY", 0.5)
            )
        except (TypeError, ValueError):
            _aggtrade_missing_penalty = 0.5
        _aggtrade_missing_penalty_applied = (
            max(0.0, _aggtrade_missing_penalty)
            if (_aggtrade_required and not _aggtrade_available and _aggtrade_mode == "degraded")
            else 0.0
        )
        total_score = max(0.0, total_score - _aggtrade_missing_penalty_applied)

        # D1 PD-array conflict penalty (B-1).
        # FIX 1: Apply penalty ONLY to total_score, never to gate_score
        _d1_conflict = bool(res.get("d1_pd_array_conflict", False))
        _d1_penalty = float(
            config.CONFIG.get("NAKED_ENGINE", {}).get("d1_pd_array_penalty", 0.25)
        )
        _d1_penalty = _d1_penalty if _d1_conflict else 0.0
        total_score = max(0.0, total_score - _d1_penalty)

        # hard_counter soft penalty in "penalty" mode. In "veto" mode structure_ok
        # is already False and the signal won't pass, so the penalty has no effect.
        # H-3: when the structure gate is explicitly disabled via profile, the
        # hard_counter (a structure-quality measure) penalty is waived too — applying
        # it while forcing structure_ok=True is inconsistent.
        _hard_counter_penalty = (
            _hard_counter_penalty_cfg
            if (hard_counter and _hard_counter_mode != "veto" and not structure_gate_disabled)
            else 0.0
        )
        total_score = max(0.0, total_score - _hard_counter_penalty)
        # gate_score stays integer — never modified by penalty

        pct = min(100, max(0, round((total_score / max_possible) * 100)))
        # M-1: `pct` denominator includes bonus headroom, so passing all mandatory
        # gates compresses to <50%. Expose a separate gate-only percentage that
        # reflects mandatory-gate completion without the bonus distortion.
        gate_pct = (
            min(100, max(0, round((gate_score / gate_max_possible) * 100)))
            if gate_max_possible > 0
            else 0
        )
        if checklist_mode == "strict":
            passed = structure_ok and zone_ok and trigger_ok and space_gate_ok and rr_ok and macro_ok
        else:
            # Flexible but not free — require BOTH location AND a trigger/catalyst.
            # BOS alone is not enough. You need: structure + (zone OR breakout) + trigger + room/rr.
            # macro_ok is only a hard gate when require_macro_align=True (mirrors confirmations list).
            passed = (
                structure_ok
                and location_ok
                and entry_ok
                and space_gate_ok
                and rr_ok
                and (macro_ok if require_macro_align else True)
            )
        if _aggtrade_hard_required and not _aggtrade_available:
            passed = False

        failed_gate_names = []
        if not structure_ok:
            failed_gate_names.append("struct")
        if not location_ok:
            failed_gate_names.append("loc")
        if not entry_ok:
            failed_gate_names.append("trigger")
        if not space_gate_ok:
            failed_gate_names.append("space")
        if not rr_ok:
            failed_gate_names.append(f"rr={rr:.1f}(src={_exec_lvl['rr_source']})")
        if _exec_lvl.get("execution_level_reject_reason") == "max_sl_exceeded":
            failed_gate_names.append("max_sl_exceeded")
        if _aggtrade_hard_required and not _aggtrade_available:
            failed_gate_names.append(ENGINE_B_REASON_AGGTRADE_REQUIRED)

        lifecycle_state, lifecycle_reason = self._determine_lifecycle_state(
            res, current_price, direction, trigger_ok
        )

        # FIX 10: Per-gate failure histogram logging
        for gate_name, gate_result in [
            ("structure_ok", structure_ok),
            ("location_ok", location_ok),
            ("entry_ok", entry_ok),
            ("room_ok", space_gate_ok),
            ("rr_ok", rr_ok),
            ("macro_ok", macro_ok if require_macro_align else True),
        ]:
            if not gate_result:
                _log_gate_failure(gate_name, f"direction={direction}, asset={asset_type_lower}")

        if hard_counter:
            _diag_codes.append(ENGINE_B_REASON_SEQUENCE_COUNTER_TREND)
        if not trigger_ok and not breakout_ok:
            _diag_codes.append(ENGINE_B_REASON_NO_TRIGGER_PATTERN)
        elif breakout_ok and not res.get("bos_volume_confirmed", False):
            _diag_codes.append(ENGINE_B_REASON_BOS_WITHOUT_VOLUME)
        if _d1_conflict:
            _diag_codes.append(ENGINE_B_REASON_D1_PD_ARRAY_CONFLICT)
        if _aggtrade_required and not _aggtrade_available:
            _diag_codes.append(ENGINE_B_REASON_AGGTRADE_REQUIRED)
        elif _aggtrade_cvd_opposed:
            _diag_codes.append(ENGINE_B_REASON_AGGTRADE_CVD_OPPOSED)
        if not room_ok:
            if direction == "LONG":
                _diag_codes.append(ENGINE_B_REASON_RESISTANCE_TOO_CLOSE)
            elif direction == "SHORT":
                _diag_codes.append(ENGINE_B_REASON_SUPPORT_TOO_CLOSE)
        if _struct_sl and _struct_tp and not _exec_lvl["structural_tp_valid"]:
            _diag_codes.append(ENGINE_B_REASON_TP_WRONG_SIDE)
        if res.get("tp_structural_limited"):
            _diag_codes.append(ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE)
        # Forex ADX now derives regime only; do not emit the old block-style
        # diagnostic because low ADX is no longer a structure gate failure.

        _engine_b_diag_payload = {"reason_codes": _diag_codes}
        if asset_type_lower == "crypto" and isinstance(res.get("crypto_structure_diagnostics"), dict):
            _engine_b_diag_payload["crypto_structure"] = res.get("crypto_structure_diagnostics")
        if asset_type_lower == "crypto":
            _engine_b_diag_payload["aggtrade"] = {
                "required": _aggtrade_required,
                "mode": _aggtrade_mode,
                "available": _aggtrade_available,
                "reason": _aggtrade_reason,
                "cvd_direction": _aggtrade_cvd_direction,
                "cvd_alignment": _aggtrade_cvd_alignment,
                "orderflow_points": round(_aggtrade_orderflow_points, 2),
                "data_fidelity": _engine_b_data_fidelity,
            }
        if asset_type_lower == "forex" and isinstance(res.get("forex_session_structure"), dict):
            _engine_b_diag_payload["forex_session_structure"] = res.get("forex_session_structure")
        if isinstance(res.get("asset_class_structure_diagnostics"), dict):
            _engine_b_diag_payload["asset_class_structure"] = res.get("asset_class_structure_diagnostics")
        if isinstance(res.get("equity_session_structure"), dict):
            _engine_b_diag_payload["equity_session_structure"] = res.get("equity_session_structure")

        _hard_fail_reasons = []
        if not passed:
            for _gate_key, _gate_val in [
                ("structure_ok", structure_ok),
                ("location_ok", location_ok),
                ("entry_ok", entry_ok),
                ("room_ok", space_gate_ok),
                ("rr_ok", rr_ok),
                ("macro_ok", macro_ok if require_macro_align else True),
            ]:
                if not _gate_val:
                    _hard_fail_reasons.append(f"engine_b_{_gate_key}_false")
            _hard_fail_reasons.append("engine_b_confidence_passed_false")
            if res.get("structural_verdict") != "CLEAR":
                _hard_fail_reasons.append("engine_b_structural_verdict_not_clear")
            if _aggtrade_hard_required and not _aggtrade_available:
                _hard_fail_reasons.append("engine_b_aggtrade_required_false")
            _hard_fail_reasons = sorted(set(_hard_fail_reasons))

        return {
            "score": total_score,
            "gate_score": gate_score,
            "pct": pct,
            "gate_pct": gate_pct,
            "max_possible": round(max_possible, 2),
            "gate_max_possible": round(gate_max_possible, 2),
            "bonus_points": round(bonus_points, 2),
            "struct_points": 1.0 if structure_ok else 0.0,
            "rr_points": 1.0 if rr_ok else 0.0,
            "room_points": 1.0 if room_ok else 0.0,
            "catalyst_bonus": 1.0 if entry_ok else 0.0,
            "ai_adjustment": 0.0,
            "zone_points": 1.0 if location_ok else 0.0,
            "macro_points": 1.0 if macro_ok and require_macro_align else 0.0,
            "rr": round(rr, 2),
            "rr_used_for_gate": round(rr, 2),
            "rr_source": _exec_lvl["rr_source"],
            "rr_required": _exec_lvl.get("rr_required", min_rr),
            "rr_actual": _exec_lvl.get("rr_actual", round(rr, 2)),
            "rr_passed": _exec_lvl.get("rr_passed", rr_ok),
            "structural_sl": _exec_lvl["structural_sl"],
            "structural_tp": _exec_lvl["structural_tp"],
            "structural_rr": _exec_lvl["structural_rr"],
            "structural_tp_available": _exec_lvl.get("structural_tp_available"),
            "execution_sl": _exec_lvl["execution_sl"],
            "execution_tp": _exec_lvl["execution_tp"],
            "execution_tp1": _exec_lvl.get("execution_tp1"),
            "execution_tp2": _exec_lvl.get("execution_tp2"),
            "execution_rr": _exec_lvl["execution_rr"],
            "execution_rr1": _exec_lvl.get("execution_rr1"),
            "execution_rr2": _exec_lvl.get("execution_rr2"),
            "level_mode": _exec_lvl["level_mode"],
            "level_cohort": _exec_lvl.get("level_cohort", engine_b_level_cohort(_exec_lvl.get("level_mode"))),
            "sl_source": _exec_lvl.get("sl_source"),
            "tp_source": _exec_lvl.get("tp_source"),
            "tp1_source": _exec_lvl.get("tp1_source"),
            "tp2_source": _exec_lvl.get("tp2_source"),
            "exit_strategy": _exec_lvl.get("exit_strategy"),
            "runner_tp_requires_structural_break": _exec_lvl.get("runner_tp_requires_structural_break"),
            "execution_levels_valid": _exec_lvl["execution_levels_valid"],
            "execution_level_reject_reason": _exec_lvl["execution_level_reject_reason"],
            "fallback_tp_applied": _exec_lvl["fallback_tp_applied"],
            "fallback_tp_reason": _exec_lvl["fallback_tp_reason"],
            "synthetic_rr_tp_used": _exec_lvl.get("synthetic_rr_tp_used"),
            "structural_target_distance_atr": _exec_lvl.get("structural_target_distance_atr"),
            "stop_distance_atr": _exec_lvl.get("stop_distance_atr"),
            "passed": passed,
            "structure_ok": structure_ok,
            "structure_gate_original_ok": structure_gate_original_ok,
            "structure_gate_disabled": structure_gate_disabled,
            "macro_ok": macro_ok,
            "zone_ok": zone_ok,
            "breakout_ok": breakout_ok,
            "location_ok": location_ok,
            "trigger_ok": trigger_ok,
            "original_location_ok": original_location_ok,
            "original_trigger_ok": original_trigger_ok,
            "entry_ok": entry_ok,
            "room_ok": room_ok,
            "space_gate_ok": space_gate_ok,
            "rr_space_gate_enabled": rr_space_gate_enabled,
            "forex_rr_space_gate_enabled": forex_rr_space_gate_enabled,
            "space_rr_substitute_override_active": _space_rr_substitute_active,
            "space_rr_substitute_override_value": (
                bool(_space_rr_substitute_override)
                if _space_rr_substitute_active
                else None
            ),
            "rr_can_satisfy_space_gate": rr_can_satisfy_space,
            "rr_space_capped_tp_blocked": _rr_space_capped_tp_blocked,
            "rr_space_floor_atr": round(_rr_space_floor_atr, 4),
            "rr_space_floor_passed": bool(_rr_space_floor_ok),
            "min_room_atr_used": round(_effective_min_room_atr, 4),
            "rr_ok": rr_ok,
            "tp_side_ok": tp_side_ok,
            "space_ok": space_ok,
            "trigger_pattern": res.get("trigger_pattern", "NONE"),
            "ob_at_zone": ob_at_zone,
            "bos_mtf_confirmed": bos_mtf,
            "volume_bonus": 1.0 if volume_ok else 0.0,
            "breaker_active": bool(res.get("breaker_block")),
            "profile_points": round(_profile_points, 2),
            "profile_ok": _profile_ok,
            "profile_alignment": _profile_alignment,
            "profile_notes": _profile_notes,
            "profile_context": _profile_vp_context,
            "aggtrade_required": _aggtrade_required,
            "aggtrade_mode": _aggtrade_mode,
            "aggtrade_available": _aggtrade_available,
            "aggtrade_reason": _aggtrade_reason,
            "aggtrade_missing_penalty": round(_aggtrade_missing_penalty_applied, 2),
            "aggtrade_cvd_direction": _aggtrade_cvd_direction,
            "aggtrade_cvd_source": res.get("aggtrade_cvd_source"),
            "aggtrade_cvd_slope": res.get("aggtrade_cvd_slope"),
            "aggtrade_cvd_alignment": _aggtrade_cvd_alignment,
            "aggtrade_orderflow_points": round(_aggtrade_orderflow_points, 2),
            "engine_b_data_fidelity": _engine_b_data_fidelity,
            "lifecycle_state": lifecycle_state,
            "lifecycle_reason": lifecycle_reason,
            "d1_pd_conflict_penalty": round(_d1_penalty, 2),
            "d1_conflict_details": res.get("d1_conflict_details", []),
            "hard_counter_active": bool(hard_counter),
            "hard_counter_mode": _hard_counter_mode,
            "hard_counter_penalty": round(_hard_counter_penalty, 2),
            "location_passed": location_ok,
            "location_mode": location_mode,
            "location_distance_atr": round(location_distance_atr, 4)
            if location_distance_atr is not None
            else None,
            "trigger_passed": trigger_ok,
            "trigger_timeframe": res.get("trigger_timeframe"),
            "trigger_type": res.get("trigger_pattern", "NONE"),
            "failed_gate_names": failed_gate_names,
            "hard_fail_reasons": _hard_fail_reasons,
            "final_engine_b_passed": passed,
            "research_lab_entry_upgrade": research_entry_ok,
            "research_lab_location_upgrade": research_location_ok,
            "research_lab_detail": research_lab_detail,
            "follow_through_bonus": round(_ft_bonus, 2),
            "follow_through_detail": _ft_detail,
            "follow_through": _ft_detail,
            "engine_b_diagnostics": _engine_b_diag_payload,
            "_adx_derived_regime": res.get("_adx_derived_regime"),
        }

    def check_macro_correlation_detail(
        self, asset_close_series: list, dxy_close_series: list, direction: str
    ) -> tuple[bool, str | None]:
        """Same gate as check_macro_correlation; returns optional block reason code when False."""
        _corr_window = int(config.CONFIG.get("ENGINE_B_MACRO_CORRELATION_WINDOW", 60))
        if len(asset_close_series) < _corr_window or len(dxy_close_series) < _corr_window:
            return True, None

        min_len = min(len(asset_close_series), len(dxy_close_series), _corr_window)
        a_series = pd.Series(asset_close_series[-min_len:])
        d_series = pd.Series(dxy_close_series[-min_len:])

        correlation = a_series.corr(d_series)

        dxy_recent = d_series.iloc[-5:].mean()
        dxy_past = d_series.iloc[-15:-5].mean()
        dxy_rising = dxy_recent > dxy_past

        if correlation and not pd.isna(correlation) and correlation < -0.6:
            if direction == "LONG" and dxy_rising:
                return False, ENGINE_B_REASON_ADVERSE_DXY
            if direction == "SHORT" and not dxy_rising:
                return False, ENGINE_B_REASON_ADVERSE_DXY

        return True, None

    def check_macro_correlation(
        self, asset_close_series: list, dxy_close_series: list, direction: str
    ) -> bool:
        """
        Calculates 30-period rolling Pearson correlation.
        Returns False (Block) if dynamically inversely correlated AND DXY moving against the trade.
        """
        ok, _reason = self.check_macro_correlation_detail(
            asset_close_series, dxy_close_series, direction
        )
        return ok

    def simulate_trade(
        self,
        d1_candles,
        h4_candles,
        h1_candles,
        current_price,
        direction,
        atr,
        regime_label="RANGING",
        confidence_threshold=1.8,
        learning_ctx=None,
        style_profile=None,
        asset_type="",
        d1_snap=None,
        h4_snap=None,
        pair=None,
    ):
        """Backtest-friendly wrapper that returns entry/exit signals.
        Returns dict compatible with existing backtest reporting."""
        result = self.analyze_structure(
            d1_candles,
            h4_candles,
            h1_candles,
            current_price,
            direction,
            atr,
            regime_label,
            asset_type=asset_type,
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            pair=pair,
        )
        # Use entry_tf from style_profile to select entry candles (H4 for swing, H1 for intraday/scalp)
        _entry_tf = str((style_profile or {}).get("entry_tf", "H1")).upper() if style_profile else "H1"
        _entry_candles = h4_candles if _entry_tf == "H4" else h1_candles
        confidence = self.calculate_confidence(
            result,
            current_price,
            direction,
            learning_ctx,
            entry_candles=_entry_candles,
            style_profile=style_profile,
        )
        if isinstance(style_profile, dict):
            gate_ok, _ = engine_b_confidence_passes(
                confidence,
                style_profile,
                regime_label,
                asset_type,
                diagnostics_context={
                    "pair": (pair or {}).get("display") if isinstance(pair, dict) else None,
                    "symbol": (pair or {}).get("symbol") if isinstance(pair, dict) else None,
                    "asset_class": asset_type,
                    "score_group": style_profile.get("score_group") if isinstance(style_profile, dict) else None,
                    "style": style_profile.get("style") if isinstance(style_profile, dict) else None,
                },
            )
            if not gate_ok:
                return None  # No trade signal
        elif not confidence.get("passed") or confidence.get("pct", 0) < 40:
            # H-6: the legacy fallback keyed off score < 1.8 (~14% of a 12.5 max),
            # which let near-empty signals through. Require the checklist `passed`
            # flag AND a meaningful gate percentage. confidence_threshold retained
            # for API compatibility but no longer the primary gate.
            return None  # No trade signal

        return {
            "entry_price": current_price,
            "sl": confidence.get("execution_sl") or result.get("recommended_stop_loss"),
            "tp1": (
                confidence.get("execution_tp1")
                or confidence.get("execution_tp")
                or result.get("recommended_take_profit")
            ),
            "tp2": (
                confidence.get("execution_tp2")
                or confidence.get("execution_tp")
                or result.get("recommended_take_profit")
            ),
            "rr1": confidence.get("execution_rr1") or confidence.get("rr_used_for_gate"),
            "rr2": confidence.get("execution_rr2") or confidence.get("rr_used_for_gate"),
            "exit_strategy": confidence.get("exit_strategy"),
            "direction": direction,
            "score": confidence["score"],
            "confidence_pct": confidence["pct"],
            "fvg_overlap": result.get("fvg_overlap", False),
            "liquidity_sweep": result.get("liquidity_sweep", False),
            "structural_verdict": result["structural_verdict"],
            "ai_adjustment": confidence.get("ai_adjustment", 0.0),
            "trigger_pattern": confidence.get("trigger_pattern", "NONE"),
        }


# Singleton instance
engine = NakedEngine()
