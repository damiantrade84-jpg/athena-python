import pandas as pd
import numpy as np
import math
from scipy.signal import find_peaks
import logging
import threading
from typing import Any
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
    "LOW_VOLATILITY": 1.0,
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


def _asian_active_currencies() -> tuple[str, ...]:
    """Config-driven list (ENGINE_B_ASIAN_ACTIVE_CURRENCIES from engine_b_config.yaml)
    with the historical hardcoded tuple as fallback."""
    cfg = config.CONFIG.get("ENGINE_B_ASIAN_ACTIVE_CURRENCIES")
    if isinstance(cfg, (list, tuple)) and cfg:
        return tuple(str(c).upper() for c in cfg)
    return _ASIAN_ACTIVE_CURRENCIES


def _pair_has_asian_active_currency(display: str | None) -> bool:
    if not display:
        return False
    sym = str(display).upper()
    return any(ccy in sym for ccy in _asian_active_currencies())


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
        return h >= 22 or h < 7
    except Exception:
        # Fail closed: when Asian skip is enabled, unparseable bar times are skipped
        # rather than silently scoring thin-session structure.
        return True


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
        # Normalization scale for engine_b_quality._session_context_score —
        # without it the scorer fell back to a hardcoded 0.04.
        "max_abs_score_bonus": abs(
            _float_value_from_mapping(session_cfg, "MAX_ABS_SCORE_BONUS", 0.04)
        ),
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
        # Normalization scale for engine_b_quality._session_context_score —
        # equity uses 0.03, not the scorer's previous hardcoded 0.04 fallback.
        "max_abs_score_bonus": abs(
            _float_value_from_mapping(session_cfg, "MAX_ABS_SCORE_BONUS", 0.03)
        ),
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


# TFs that may use ENGINE_B_TRIGGER_TF_CALIBRATION (setup/trigger/execution only).
ENGINE_B_TRIGGER_TF_CALIBRATION_TFS = frozenset({"M5", "M15", "M30"})

_DEFAULT_TRIGGER_TF_CALIBRATION = {
    "trigger_atr": 14,
    "rejection_wick_body": 1.2,
    "engulfing_body_atr": 0.2,
    "strong_close_pct": 0.70,
}


def resolve_engine_b_trigger_tf_calibration(
    score_group: str | None = None,
    asset_type: str | None = None,
    tf: str | None = None,
) -> dict[str, float | int]:
    """Per-score-group trigger/setup TF calibration; missing → hardcoded defaults.

    Does not inherit a generic ``default`` row — absent groups keep ATR 14 and
    the historical wick/engulfing/close thresholds.

    A group row may additionally carry per-timeframe sub-rows keyed by the
    trigger TF (``M5``/``M15``/``M30``) which are merged over the flat group
    values when ``tf`` matches.  A group's trigger TF is not constant — symbol
    overrides and SLOW speed adaptation move it between M5/M15/M30 inside the
    same group — so candle geometry calibrated at one rung must not be assumed
    valid at another.  Absent sub-rows keep the flat values (no behaviour
    change until a TF row is configured).
    """
    out = dict(_DEFAULT_TRIGGER_TF_CALIBRATION)
    keyed = config.CONFIG.get("ENGINE_B_TRIGGER_TF_CALIBRATION") or {}
    if not isinstance(keyed, dict):
        return out
    row = None
    g = str(score_group or "").strip().lower()
    if g and g in keyed and isinstance(keyed[g], dict):
        row = keyed[g]
    if row is None:
        a = str(asset_type or "").strip().lower()
        if a and a in keyed and isinstance(keyed[a], dict):
            row = keyed[a]
    if not isinstance(row, dict):
        return out
    tf_u = str(tf or "").strip().upper()
    tf_row = row.get(tf_u) if tf_u else None
    layers = [row] if not isinstance(tf_row, dict) else [row, tf_row]
    for layer in layers:
        for key in (
            "trigger_atr",
            "rejection_wick_body",
            "engulfing_body_atr",
            "strong_close_pct",
        ):
            if key not in layer:
                continue
            try:
                if key == "trigger_atr":
                    out[key] = int(layer[key])
                else:
                    out[key] = float(layer[key])
            except (TypeError, ValueError):
                pass
    return out


def engine_b_trigger_tf_uses_calibration(tf: str | None) -> bool:
    return str(tf or "").strip().upper() in ENGINE_B_TRIGGER_TF_CALIBRATION_TFS


def engine_b_trigger_atr_period(
    score_group: str | None,
    asset_type: str | None,
    tf: str | None,
) -> int:
    """ATR period for trigger/setup TF candles; structural TFs always stay 14."""
    if not engine_b_trigger_tf_uses_calibration(tf):
        return 14
    cal = resolve_engine_b_trigger_tf_calibration(score_group, asset_type, tf)
    try:
        period = int(cal.get("trigger_atr", 14))
    except (TypeError, ValueError):
        period = 14
    return period if period > 0 else 14


_ENGINE_B_LEGACY_TF_MATRIX = {
    asset: {
        # (struct, zone, trigger, atr) — zone tracks structure (intraday H1 / swing H4)
        "scalp": ("H1", "H1", "H1", "H1"),
        "intraday": ("H1", "H1", "H1", "H1"),
        "swing": ("H4", "H4", "H4", "H4"),
    }
    for asset in ("forex", "crypto", "commodity", "index", "stock", "etf", "etf_bond")
}


def resolve_engine_b_tfs(
    asset_type: str,
    style: str,
    *,
    symbol: str = "",
    score_group: str | None = None,
    speed_state: Any | None = None,
) -> dict:
    """Resolve struct/zone/trigger/atr timeframes for an (asset_type, style) pair.

    Single source of truth used by athena, execution, scanner, and the v3 backtester.
    Replaces the legacy ``ENGINE_B_FOREX_STRUCTURE_TF`` flag and the per-style
    hardcoded TFs in ``_naked_scan_style_profile``.
    """
    from timeframe_policy import resolve_timeframe_policy

    a = (asset_type or "forex").lower()
    s = (style or "intraday").lower()
    if s == "auto":
        s = "intraday"
    policy = resolve_timeframe_policy(
        symbol,
        a,
        score_group,
        s,
        speed_state,
        engine_id="engine_b",
    )
    from timeframe_policy import policy_mode_is_authoritative

    if not policy_mode_is_authoritative(None, config.CONFIG):
        asset_table = _ENGINE_B_LEGACY_TF_MATRIX.get(a) or _ENGINE_B_LEGACY_TF_MATRIX["forex"]
        struct_tf, zone_tf, trigger_tf, atr_tf = asset_table.get(
            s, asset_table["intraday"]
        )
        return {
            "regime": "D1",
            "bias": struct_tf,
            "struct": struct_tf,
            "zone": zone_tf,
            "setup": trigger_tf,
            "trigger": trigger_tf,
            "execution": trigger_tf,
            "atr": atr_tf,
            "m5_policy": "disabled",
            "execution_prerequisite": None,
            "policy_version": policy.policy_version,
            "policy_profile": policy.profile,
            "policy_mode": str(config.CONFIG.get("TF_POLICY_MODE", "off")).lower(),
            "proposed": policy.payload(),
        }
    # Engine B's structural ATR remains tied to its principal structure horizon;
    # M5/M15 trigger noise must never size an H1/H4 structure trade.
    return {
        "regime": policy.regime_tf.value,
        "bias": policy.bias_tf.value,
        "struct": policy.structure_tf.value,
        # Room/space-gate walls and structural TP/location zones track structure
        # TF (intraday H1, swing H4). Bias remains one rung higher for MTF bias
        # only. Pair with ENGINE_B_SPACE_GATE_ENABLED when dense zone walls would
        # otherwise over-block; set zone back to bias_tf to restore H4/D1 walls.
        "zone": policy.structure_tf.value,
        "setup": policy.setup_tf.value,
        "trigger": policy.trigger_tf.value,
        "execution": policy.execution_tf.value,
        "atr": policy.structure_tf.value,
        # Conditional M5 is not an unconditional trigger promotion: the setup
        # rung must arm before M5 may fire. Both fields are carried so the
        # scoring layer can enforce that without re-resolving the policy.
        "m5_policy": policy.m5_policy.value,
        "execution_prerequisite": (
            policy.execution_prerequisite_tf.value
            if policy.execution_prerequisite_tf
            else None
        ),
        "policy_version": policy.policy_version,
        "policy_profile": policy.profile,
    }


def engine_b_candles_for_tf(
    tf: str,
    d1_candles: list,
    h4_candles: list,
    h1_candles: list,
    extra_by_tf: dict[str, list] | None = None,
) -> list:
    """Return the candle series for a role timeframe.

    Production path uses canonical D1/H4/H1 slots. Optional ``extra_by_tf``
    supplies lower TFs (M15/M30/…). Missing or empty lower-timeframe data
    always returns [] so a requested trigger can never silently use H1.
    """
    key = str(tf or "").upper()
    if key == "D1":
        return d1_candles or []
    if key == "H4":
        return h4_candles or []
    if key == "H1":
        return h1_candles or []
    if not isinstance(extra_by_tf, dict) or key not in extra_by_tf:
        return []
    series = extra_by_tf.get(key)
    return list(series) if series else []


def engine_b_low_volatility_gate(
    current_atr: float,
    atr_series: list,
    *,
    config_map: dict | None = None,
) -> tuple[bool, dict]:
    """Apply the shared Engine B low-volatility pre-score gate."""
    cfg = config_map if isinstance(config_map, dict) else config.CONFIG
    if not bool(cfg.get("ENGINE_B_LOW_VOLATILITY_GATE_ENABLED", True)):
        return True, {"reason": "disabled"}
    try:
        lookback = max(1, int(cfg.get("ENGINE_B_LOW_VOLATILITY_LOOKBACK", 50) or 50))
        ratio = float(cfg.get("ENGINE_B_LOW_VOLATILITY_MIN_ATR_RATIO", 0.6) or 0.6)
        atr = float(current_atr)
    except (TypeError, ValueError):
        return False, {"reason": "invalid_config_or_atr"}
    if len(atr_series or []) < lookback:
        return True, {"reason": "insufficient_history", "lookback": lookback}
    valid = []
    for value in list(atr_series)[-lookback:]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            valid.append(parsed)
    if not valid:
        return False, {"reason": "no_valid_atr_history", "lookback": lookback}
    average = sum(valid) / len(valid)
    threshold = average * ratio
    return atr >= threshold, {
        "reason": "pass" if atr >= threshold else "atr_below_average_ratio",
        "atr": atr,
        "average_atr": average,
        "minimum_ratio": ratio,
        "threshold": threshold,
        "lookback": lookback,
    }


# Trigger timeframes the live/diagnostic override may target. M5 is included
# because policy v4 resolves M5 as the trigger rung for the fast/conditional
# profiles (fast majors, GBP/JPY, XAU, oil, fast indices, crypto majors, single
# stocks). Omitting it did not disable M5 — it made those symbols fail closed on
# ``missing_required_trigger_timeframe:M5`` on every scan. M5 authority is
# constrained by the conditional-M5 setup-armed prerequisite, not by this set.
_DIAGNOSTIC_TRIGGER_TFS = frozenset({"H1", "M30", "M15", "M5"})


def engine_b_diagnostic_trigger_kwargs(
    style_profile: dict | None,
    role_candles: dict[str, list] | None,
    *,
    enabled: bool | None = None,
) -> dict:
    """Build analyze_structure kwargs for diagnostic trigger-TF override.

    Default-off via ``ENGINE_B_DIAGNOSTIC_TRIGGER_TF_ENABLED``. When enabled and
    ``style_profile.entry_tf`` is H1/M30/M15/M5, returns ``role_candles`` +
    ``trigger_tf_override`` so trigger detection runs on that series.
    """
    if enabled is None:
        enabled = bool(config.CONFIG.get("ENGINE_B_DIAGNOSTIC_TRIGGER_TF_ENABLED", False))
    if not enabled:
        return {}
    entry_tf = str((style_profile or {}).get("entry_tf") or "").strip().upper()
    if entry_tf not in _DIAGNOSTIC_TRIGGER_TFS:
        return {}
    return {
        "role_candles": role_candles if isinstance(role_candles, dict) else None,
        "trigger_tf_override": entry_tf,
    }


def engine_b_live_trigger_kwargs(
    style_profile: dict | None,
    role_candles: dict[str, list] | None,
) -> dict:
    """Build fail-closed trigger kwargs for an explicitly live style profile."""
    return engine_b_diagnostic_trigger_kwargs(
        style_profile,
        role_candles,
        enabled=True,
    )


def resolve_engine_b_h4_snap(
    h4_candles: list | None,
    asset_type: str,
) -> dict:
    """Build the H4 indicator snapshot Engine B should consume.

    Callers must pass the real H4 candle series — not zone candles, which can
    be D1 for swing style.
    """
    if not h4_candles:
        return {}
    try:
        from indicators import calc_indicators_with_normalized

        snap = (calc_indicators_with_normalized(h4_candles, asset_type) or {}).get("snap")
    except Exception:
        return {}
    return snap or {}


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
ENGINE_B_REASON_BOS_VOLUME_BELOW_THRESHOLD = "bos_volume_below_threshold"
ENGINE_B_REASON_D1_PD_ARRAY_CONFLICT = "d1_pd_array_conflict"
ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE = "structural_tp_too_close"
ENGINE_B_REASON_AGGTRADE_REQUIRED = "aggtrade_required_for_crypto"
ENGINE_B_REASON_AGGTRADE_CVD_OPPOSED = "aggtrade_cvd_opposed"
ENGINE_B_REASON_TP1_BLOCKED_BY_OPPOSING_ZONE = "tp1_blocked_by_opposing_zone"


def _float_cfg(key: str, default: float) -> float:
    try:
        return float(config.CONFIG.get(key, default))
    except (TypeError, ValueError):
        return float(default)


# ``VOLATILITY_SCALER_BANDS`` is calibrated on H4 ATR% (see the per-row comments
# in config.yaml). Engine B reads structural ATR from the policy structure rung
# — H1 for the fast groups, H4 only for the thin ones — and separately banded D1
# ATR for the multi-timeframe branch. Comparing a raw H1 ATR% against H4 bands
# makes the elevated/high branch unreachable, and a raw D1 ATR% pins it at
# high_volatility. Normalise to the H4 reference first (ATR scales ~sqrt(time)).
_TF_SECONDS_FOR_VOL_BANDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}
_VOL_BAND_REFERENCE_TF_SECONDS = 14400.0


def _volatility_band_atr_pct(
    atr_pct: float | None,
    timeframe: str | None,
) -> tuple[float | None, float]:
    """Rescale an ATR% to the H4 reference the volatility bands assume."""
    if atr_pct is None:
        return None, 1.0
    if not bool(
        config.CONFIG.get("ENGINE_B_VOLATILITY_BAND_TF_NORMALIZATION", True)
    ):
        return atr_pct, 1.0
    seconds = _TF_SECONDS_FOR_VOL_BANDS.get(str(timeframe or "").strip().upper())
    if not seconds or seconds <= 0:
        return atr_pct, 1.0
    scale = math.sqrt(_VOL_BAND_REFERENCE_TF_SECONDS / float(seconds))
    return atr_pct * scale, scale


def _crypto_structure_adjustment(
    asset_type: str,
    candles: list,
    atr: float,
    timeframe: str | None = None,
) -> dict:
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
        "band_timeframe": str(timeframe or "").strip().upper() or None,
        "band_atr_pct": None,
        "band_tf_scale": 1.0,
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

    band_atr_pct, band_scale = _volatility_band_atr_pct(atr_pct, timeframe)

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

    if band_atr_pct is None:
        regime = "unknown"
    elif band_atr_pct >= high_band:
        regime = "high_volatility"
    elif band_atr_pct >= low_band:
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
        "band_atr_pct": round(band_atr_pct, 6) if band_atr_pct is not None else None,
        "band_tf_scale": round(band_scale, 4),
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
    timeframe: str | None = None,
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
        "band_timeframe": str(timeframe or "").strip().upper() or None,
        "band_atr_pct": None,
        "band_tf_scale": 1.0,
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

    band_atr_pct, band_scale = _volatility_band_atr_pct(atr_pct, timeframe)

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

    if band_atr_pct is None:
        regime = "unknown"
    elif band_atr_pct >= high_band:
        regime = "high_volatility"
    elif band_atr_pct >= low_band:
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
        "band_atr_pct": round(band_atr_pct, 6) if band_atr_pct is not None else None,
        "band_tf_scale": round(band_scale, 4),
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


_ENGINE_B_PIVOT_BUFFER_DEFAULTS = {
    "TRENDING": 0.35,
    "RANGING": 0.25,
    "HIGH_VOLATILITY": 0.45,
    "LOW_VOLATILITY": 0.25,
}


def _engine_b_structural_sl_pivot_buffer_atr(regime: str) -> tuple[float, str]:
    """ATR buffer placed BEYOND the structural swing pivot for the stop.

    The legacy path reused ``NAKED_ENGINE.zone_multipliers[regime].sl``
    (1.0 / 1.5 / 1.8) here. Those are *standalone* ATR stop multiples — the same
    magnitude as ``STYLE_ATR_MULTS`` (1.5 intraday / 1.8 swing) — so adding one
    on top of the pivot distance made the structural stop at least 1.5 ATR wide
    even with the pivot sitting exactly at price. Structural RR could then never
    reach ``min_rr``, the structural stop lost the tighter-of comparison in
    ``resolve_engine_b_execution_levels``, and ``sl_source`` came back ``atr`` on
    every signal whose pivot was more than ~0.25 ATR away. Zones, sweeps, order
    blocks and breakers had no influence on the stop at all.

    A pivot buffer is protection against a wick through the level, not a stop in
    its own right, so it is sized in fractions of an ATR. Regime ordering from
    the legacy table is preserved (HIGH_VOLATILITY > TRENDING > RANGING/LOW).

    Returns ``(buffer_atr_mult, source)``. Set
    ``ENGINE_B_STRUCTURAL_SL_PIVOT_BUFFER_ENABLED: false`` to restore the legacy
    ``zone_multipliers.sl`` behaviour.
    """
    naked_cfg = config.CONFIG.get("NAKED_ENGINE", {}) or {}
    raw = naked_cfg.get("structural_sl_pivot_buffer_atr")
    key = str(regime or "").upper()
    if isinstance(raw, dict):
        val = raw.get(key, raw.get("RANGING"))
        if val is not None:
            try:
                return max(0.0, float(val)), f"pivot_buffer:{key.lower() or 'ranging'}"
            except (TypeError, ValueError):
                pass
    elif raw is not None:
        try:
            return max(0.0, float(raw)), "pivot_buffer:scalar"
        except (TypeError, ValueError):
            pass
    return (
        _ENGINE_B_PIVOT_BUFFER_DEFAULTS.get(key, _ENGINE_B_PIVOT_BUFFER_DEFAULTS["RANGING"]),
        f"pivot_buffer_default:{key.lower() or 'ranging'}",
    )


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


def _engine_b_exec_sl_tighter_than_structural(
    direction: str, structural_sl, execution_sl, structural_sl_valid
) -> bool | None:
    """True when the execution SL is tighter than the structural SL.

    This is the normal Engine B design (the ATR/mechanical execution stop sits
    inside the structural invalidation level for RR) — informational, not a
    defect indicator.

    LONG:  execution_sl > structural_sl  (closer to entry from below)
    SHORT: execution_sl < structural_sl  (closer to entry from above)

    Returns None when either level is missing or the structural SL is invalid.
    """
    if not bool(structural_sl_valid):
        return None
    try:
        _ss = float(structural_sl)
        _es = float(execution_sl)
    except (TypeError, ValueError):
        return None
    _dir = str(direction or "").upper()
    if _dir == "LONG":
        return _es > _ss
    if _dir == "SHORT":
        return _es < _ss
    return None


def _engine_b_tp1_path_clear(
    *,
    direction: str,
    entry: float | None,
    tp1: float | None,
    opposing_zone: dict | None,
    opposing_zone_distance: float | None,
    tol: float = 1e-9,
) -> dict:
    """Check whether TP1 is reachable before the nearest opposing zone.

    For LONG the opposing zone is nearest resistance; TP1 is blocked when it
    lies above the resistance lower edge (reward exceeds available room).
    For SHORT the opposing zone is nearest support; TP1 is blocked when it
    lies below the support upper edge.

    When no opposing zone or distance is available the current no-wall
    behaviour is preserved (path clear).
    """
    _direction = str(direction or "").upper()

    def _sf(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    _entry = _sf(entry)
    _tp1 = _sf(tp1)
    _zone_lower = _sf(opposing_zone.get("lower")) if isinstance(opposing_zone, dict) else None
    _zone_upper = _sf(opposing_zone.get("upper")) if isinstance(opposing_zone, dict) else None

    _entry_inside = False
    if _entry is not None and _zone_lower is not None and _zone_upper is not None:
        lo, hi = (_zone_lower, _zone_upper) if _zone_lower <= _zone_upper else (_zone_upper, _zone_lower)
        _entry_inside = lo - tol <= _entry <= hi + tol

    _tp1_distance = abs(_tp1 - _entry) if (_tp1 is not None and _entry is not None) else None

    # No opposing zone or no usable distance → preserve no-wall behaviour.
    if (
        opposing_zone is None
        or opposing_zone_distance is None
        or _tp1 is None
        or _entry is None
    ):
        return {
            "entry_inside_opposing_zone": _entry_inside,
            "tp1_before_opposing_zone": None,
            "tp1_path_clear": True,
            "tp1_path_block_reason": None,
            "opposing_zone_distance": opposing_zone_distance,
            "tp1_distance": _tp1_distance,
        }

    if _direction == "LONG":
        _tp1_before = _zone_lower is not None and _tp1 <= _zone_lower + tol
    else:
        _tp1_before = _zone_upper is not None and _tp1 >= _zone_upper - tol

    if _tp1_before:
        return {
            "entry_inside_opposing_zone": _entry_inside,
            "tp1_before_opposing_zone": True,
            "tp1_path_clear": True,
            "tp1_path_block_reason": None,
            "opposing_zone_distance": opposing_zone_distance,
            "tp1_distance": _tp1_distance,
        }

    return {
        "entry_inside_opposing_zone": _entry_inside,
        "tp1_before_opposing_zone": False,
        "tp1_path_clear": False,
        "tp1_path_block_reason": ENGINE_B_REASON_TP1_BLOCKED_BY_OPPOSING_ZONE,
        "opposing_zone_distance": opposing_zone_distance,
        "tp1_distance": _tp1_distance,
    }


def _engine_b_clamp_tp1_to_opposing_zone(
    exec_lvl: dict,
    *,
    direction: str,
    entry,
    opposing_zone: dict | None,
    opposing_zone_distance,
    atr,
    min_rr,
    exec_style: str,
    asset_class: str | None,
) -> dict:
    """Re-target a TP1 that overshoots the nearest opposing wall to the wall's
    front edge (standard structural target price with the TP buffer).

    The target selector can skip a near zone (structural TP too close or
    wrong-side) and pick a deeper target whose TP1 sits beyond the nearer
    opposing wall. Instead of emitting an unreachable TP1, try the wall first:
    - scale-out plans: clamp execution_tp1 only; keep when the clamped RR1
      still clears ENGINE_B_TP1_MIN_RR (TP2/runner and the gate RR are untouched).
    - single-TP plans: clamp the trade TP; keep when the clamped RR still
      clears min_rr and exchange TP bounds pass.
    When the clamped target cannot clear its RR floor the levels are left
    unchanged and the TP1-path space gate blocks the signal.

    Mutates exec_lvl in place; returns {"tp1_clamped_to_opposing_zone",
    "tp1_clamp_reject_reason"}.
    """
    diag = {"tp1_clamped_to_opposing_zone": False, "tp1_clamp_reject_reason": None}
    _path = _engine_b_tp1_path_clear(
        direction=direction,
        entry=entry,
        tp1=exec_lvl.get("execution_tp1"),
        opposing_zone=opposing_zone,
        opposing_zone_distance=opposing_zone_distance,
    )
    if _path.get("tp1_path_clear"):
        return diag
    if not bool(exec_lvl.get("execution_levels_valid")):
        diag["tp1_clamp_reject_reason"] = "execution_levels_invalid"
        return diag
    try:
        _entry = float(entry)
        _sl = float(exec_lvl.get("execution_sl"))
        _atr = float(atr)
    except (TypeError, ValueError):
        diag["tp1_clamp_reject_reason"] = "levels_missing"
        return diag
    _risk = abs(_entry - _sl)
    if _risk <= 0 or _atr <= 0:
        diag["tp1_clamp_reject_reason"] = "zero_risk_or_atr"
        return diag
    try:
        _clamp_tp = _engine_b_structural_target_price(
            opposing_zone, direction, _atr, _engine_b_structural_tp_buffer_atr_mult()
        )
    except (TypeError, KeyError, ValueError):
        diag["tp1_clamp_reject_reason"] = "opposing_zone_malformed"
        return diag
    _side_ok = _clamp_tp > _entry if direction == "LONG" else _clamp_tp < _entry
    if not _side_ok:
        diag["tp1_clamp_reject_reason"] = "clamp_tp_wrong_side"
        return diag
    _clamp_rr = abs(_clamp_tp - _entry) / _risk
    _scale_out = (
        exec_lvl.get("exit_strategy") == "scale_out_structural_tp1_fallback_tp2"
        and exec_lvl.get("scale_out_guard_reason") is None
    )
    if _scale_out:
        _floor = _engine_b_style_threshold(
            config.CONFIG.get("ENGINE_B_TP1_MIN_RR"), exec_style
        )
        if _floor > 0 and _clamp_rr + 1e-12 < _floor:
            diag["tp1_clamp_reject_reason"] = "clamped_rr1_below_tp1_min_rr"
            return diag
    else:
        try:
            _min_rr = float(min_rr) if min_rr is not None else None
        except (TypeError, ValueError):
            _min_rr = None
        if _min_rr is not None and _clamp_rr + 1e-12 < _min_rr:
            diag["tp1_clamp_reject_reason"] = "clamped_rr_below_min_rr"
            return diag
    _ac = str(asset_class or "").lower()
    if _ac:
        try:
            from risk_engine import validate_tp_exchange_bounds

            _bounds_ok, _ = validate_tp_exchange_bounds(_entry, _clamp_tp, _ac, config.CONFIG)
        except Exception:
            _bounds_ok = True
        if not _bounds_ok:
            diag["tp1_clamp_reject_reason"] = "clamp_tp_exchange_bounds"
            return diag
    _clamp_rr_rounded = round(_clamp_rr, 4)
    if _scale_out:
        exec_lvl["execution_tp1"] = _clamp_tp
        exec_lvl["execution_rr1"] = _clamp_rr_rounded
    else:
        _mode = f"{exec_lvl.get('sl_source') or 'unknown'}_sl_structural_tp"
        exec_lvl["execution_tp"] = _clamp_tp
        exec_lvl["execution_tp1"] = _clamp_tp
        exec_lvl["execution_tp2"] = _clamp_tp
        exec_lvl["execution_rr"] = _clamp_rr_rounded
        exec_lvl["execution_rr1"] = _clamp_rr_rounded
        exec_lvl["execution_rr2"] = _clamp_rr_rounded
        exec_lvl["rr_used_for_gate"] = _clamp_rr_rounded
        exec_lvl["rr_actual"] = _clamp_rr_rounded
        exec_lvl["rr_passed"] = True
        exec_lvl["tp_source"] = "structural"
        exec_lvl["tp1_source"] = "structural"
        exec_lvl["tp2_source"] = "structural"
        exec_lvl["level_mode"] = _mode
        exec_lvl["rr_source"] = _mode
        exec_lvl["level_cohort"] = engine_b_level_cohort(_mode)
        exec_lvl["exit_strategy"] = "single_structural_tp"
        exec_lvl["synthetic_rr_tp_used"] = False
    diag["tp1_clamped_to_opposing_zone"] = True
    return diag


def _engine_b_adapt_execution_plan_for_venue(
    exec_lvl: dict,
    *,
    direction: str,
    entry: float,
    asset_type: str,
    exec_style: str,
) -> None:
    """Adapt an already wall-clamped canonical Engine B plan to venue capability.

    The resolver remains the source of canonical structural-TP1/synthetic-TP2
    diagnostics.  Only the emitted executable plan changes: crypto can retain
    the one-position scale-out, while non-crypto may use its validated
    structural TP1 as a single target when explicitly enabled.
    """
    if not isinstance(exec_lvl, dict):
        return

    canonical_keys = (
        "execution_sl", "execution_tp", "execution_tp1", "execution_tp2",
        "execution_rr", "execution_rr1", "execution_rr2", "rr_required",
        "rr_actual", "rr_passed", "exit_strategy", "execution_levels_valid",
    )
    for key in canonical_keys:
        exec_lvl[f"canonical_{key}"] = exec_lvl.get(key)
    is_canonical_scale_out = (
        exec_lvl.get("exit_strategy") == "scale_out_structural_tp1_fallback_tp2"
        and exec_lvl.get("scale_out_guard_reason") is None
    )
    exec_lvl["canonical_execution_plan"] = (
        "scale_out" if is_canonical_scale_out else "single_target"
    )
    exec_lvl["execution_plan"] = "single_target"
    exec_lvl["execution_plan_reason"] = "canonical_single_target"
    exec_lvl["single_target_rr_floor"] = None
    exec_lvl["single_target_execution_tp"] = None
    exec_lvl["single_target_valid"] = False
    if not is_canonical_scale_out:
        return

    try:
        from exit_policy import engine_b_scaleout_execution_supported

        scale_out_supported = engine_b_scaleout_execution_supported(
            asset_type, config.CONFIG
        )
    except Exception:
        scale_out_supported = False
    if scale_out_supported:
        exec_lvl["execution_plan"] = "scale_out"
        exec_lvl["execution_plan_reason"] = "crypto_scale_out_supported"
        return

    if not bool(config.CONFIG.get("ENGINE_B_SINGLE_TARGET_STRUCTURAL_FALLBACK_ENABLED", False)):
        exec_lvl["execution_plan"] = "scale_out_unavailable"
        exec_lvl["execution_plan_reason"] = "single_target_structural_fallback_disabled"
        return

    try:
        tp1 = float(exec_lvl.get("execution_tp1"))
        rr1 = float(exec_lvl.get("execution_rr1"))
        floor = float(_engine_b_style_threshold(
            config.CONFIG.get("ENGINE_B_TP1_MIN_RR"), exec_style
        ))
    except (TypeError, ValueError):
        exec_lvl["execution_plan"] = "scale_out_unavailable"
        exec_lvl["execution_plan_reason"] = "single_target_structural_tp1_malformed"
        return
    side_ok = (direction == "LONG" and tp1 > entry) or (
        direction == "SHORT" and tp1 < entry
    )
    structural_tp1 = (
        exec_lvl.get("structural_tp_valid") is True
        and str(exec_lvl.get("tp1_source") or "").lower() == "structural"
    )
    if not structural_tp1 or not side_ok or rr1 + 1e-12 < floor:
        exec_lvl["execution_plan"] = "scale_out_unavailable"
        exec_lvl["execution_plan_reason"] = "single_target_structural_tp1_invalid"
        return

    exec_lvl["execution_tp"] = tp1
    exec_lvl["execution_tp1"] = tp1
    exec_lvl["execution_tp2"] = tp1
    exec_lvl["execution_rr"] = rr1
    exec_lvl["execution_rr1"] = rr1
    exec_lvl["execution_rr2"] = rr1
    exec_lvl["rr_used_for_gate"] = rr1
    exec_lvl["rr_required"] = floor
    exec_lvl["rr_actual"] = rr1
    exec_lvl["rr_passed"] = True
    exec_lvl["exit_strategy"] = "single_structural_tp"
    exec_lvl["runner_tp_requires_structural_break"] = False
    exec_lvl["tp_source"] = "structural"
    exec_lvl["tp1_source"] = "structural"
    exec_lvl["tp2_source"] = "structural"
    exec_lvl["synthetic_rr_tp_used"] = False
    exec_lvl["execution_levels_valid"] = True
    exec_lvl["execution_level_reject_reason"] = None
    exec_lvl["level_mode"] = f"{exec_lvl.get('sl_source')}_sl_structural_tp"
    exec_lvl["rr_source"] = exec_lvl["level_mode"]
    exec_lvl["level_cohort"] = engine_b_level_cohort(exec_lvl["level_mode"])
    exec_lvl["execution_plan"] = "single_structural_tp1"
    exec_lvl["execution_plan_reason"] = "non_crypto_structural_tp1"
    exec_lvl["single_target_rr_floor"] = floor
    exec_lvl["single_target_execution_tp"] = tp1
    exec_lvl["single_target_valid"] = True


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
    historical_mode: bool = False,
) -> dict:
    """Read-only crypto aggTrade VP/CVD context for Engine B.

    ``historical_mode`` is for backtests: there is no historical aggTrade
    bucket store, and reading the live one would apply *current* CVD to past
    bars. In that mode the gate is disabled (not failed) and clearly labeled,
    so backtest results disclose that live crypto carries an extra CVD gate
    the backtest cannot reproduce.
    """
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
    if historical_mode:
        out["aggtrade_required"] = False
        out["aggtrade_reason"] = "aggtrade_unavailable_historical"
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
    apply_to_min = bool(
        config.CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS_APPLY_TO_MIN_SCORE", False)
    )
    floor_multiplier = regime_multiplier if (multipliers_enabled and apply_to_min) else 1.0
    scaled = base_min * floor_multiplier
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


def _follow_through_candle_series(candles: list | None) -> list:
    """Return candle series for follow-through/trap evaluation.

    When ``ENGINE_B_FOLLOW_THROUGH_CONFIRMED_ONLY`` is true (default) and the
    trigger series may include a forming bar, drop the newest bar so trap
    scoring matches confirmed-only structure policy.
    """
    series = list(candles or [])
    if not series:
        return series
    if not bool(config.CONFIG.get("ENGINE_B_FOLLOW_THROUGH_CONFIRMED_ONLY", True)):
        return series
    if len(series) < 2:
        return series
    return series[:-1]


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

    Callers should pass ``_follow_through_candle_series(entry_candles)`` when
    ``ENGINE_B_FOLLOW_THROUGH_CONFIRMED_ONLY`` is enabled so trap scoring does
    not use a forming bar.

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


def engine_b_effective_min_score_floor(
    min_score_scaled: float,
    gate_max_possible: float,
) -> float:
    """Return the style/regime min_score floor for total-score gating.

    ``gate_max_possible`` is retained for diagnostics only; the floor applies to
    ``confidence.score`` (mandatory gates + quality bonuses), not ``gate_score``.
    """
    _ = gate_max_possible  # diagnostics callers still pass gate headroom
    try:
        scaled = float(min_score_scaled or 0.0)
    except (TypeError, ValueError):
        scaled = 0.0
    if scaled <= 0:
        return 0.0
    return scaled


def engine_b_min_score_basis() -> str:
    """``total`` (legacy, default) or ``quality_ratio``.

    See ENGINE_B_MIN_SCORE_BASIS in config.yaml. The legacy basis compares the
    style floor against ``confidence.score``, which for a passing signal is
    always at least ``gate_max_possible`` — so the configured floors (4.0/4.5/5.0
    vs a 5.0 gate floor) are effectively non-binding.
    """
    try:
        basis = str(config.CONFIG.get("ENGINE_B_MIN_SCORE_BASIS", "total") or "total")
    except Exception:
        basis = "total"
    basis = basis.strip().lower()
    return basis if basis in {"total", "quality_ratio"} else "total"


def _engine_b_min_quality_ratio(style_profile: dict | None) -> float:
    style = str((style_profile or {}).get("style") or "").strip().lower()
    try:
        ratios = config.CONFIG.get("ENGINE_B_MIN_QUALITY_RATIO_BY_STYLE", {}) or {}
        return max(0.0, min(1.0, float(ratios.get(style, 0.0) or 0.0)))
    except (TypeError, ValueError):
        return 0.0


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
        quality_score = float(conf.get("score", conf.get("gate_score", 0.0)) or 0.0)
    except (TypeError, ValueError):
        quality_score = 0.0
    try:
        gate_max_possible = float(conf.get("gate_max_possible", 0.0) or 0.0)
    except (TypeError, ValueError):
        gate_max_possible = 0.0
    effective_min = engine_b_effective_min_score_floor(
        min_score_scaled, gate_max_possible
    )
    basis = engine_b_min_score_basis()
    if basis == "quality_ratio":
        min_ratio = _engine_b_min_quality_ratio(style_profile)
        try:
            quality_ratio = float(conf.get("quality_pct") or 0.0) / 100.0
        except (TypeError, ValueError):
            quality_ratio = 0.0
        score_floor_ok = min_ratio <= 0 or quality_ratio >= min_ratio
    else:
        score_floor_ok = effective_min <= 0 or quality_score >= effective_min
    if isinstance(conf_data, dict):
        # Report whether the floor could reject anything for this signal. Under
        # the legacy basis a passing signal always clears it (gate_score ==
        # gate_max_possible >= the configured floor), so a "floor passed" badge
        # means nothing without this flag.
        conf_data["min_score_basis"] = basis
        conf_data["min_score_floor_binding"] = bool(
            basis == "quality_ratio"
            or (effective_min > 0 and gate_max_possible > 0 and effective_min > gate_max_possible)
        )
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
                scaled_min_score=effective_min,
                passed=passed,
                diagnostics_context=diagnostics_context,
            )
        )
    except Exception as exc:
        log.debug("[CALIBRATION-DIAG] Engine B diagnostic skipped: %s", exc)
    return passed, effective_min


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

# Refinement stages append a suffix to sl_source, so the resulting level_mode is
# not one of the four cohorts above and every refined row fell into
# "unknown_level_mode" — losing it from aggregate_engine_b_level_cohorts. The
# cohort answers "where did this stop come from", so each suffix maps to the
# origin the stop ENDS at:
#   _atr_clamp      -> moved to a mechanical ATR distance          => atr
#   _tp1_rr_retry   -> moved to min_sl_atr * ATR                   => atr
#   _max_sl_clamp   -> moved to a MAX_SL_PCT distance              => atr
#   _rr_tighten     -> placed at structural_tp_distance / min_rr,
#                      i.e. derived from the structural target     => base kept
_ENGINE_B_SL_SOURCE_ATR_SUFFIXES = ("_atr_clamp", "_tp1_rr_retry", "_max_sl_clamp")
_ENGINE_B_SL_SOURCE_NEUTRAL_SUFFIXES = ("_rr_tighten",)


def _engine_b_base_sl_source(sl_part: str) -> str:
    """Collapse refinement suffixes on an sl_source down to its cohort origin."""
    base = str(sl_part or "")
    became_atr = False
    changed = True
    while changed:
        changed = False
        for suffix in _ENGINE_B_SL_SOURCE_ATR_SUFFIXES:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                became_atr = True
                changed = True
        for suffix in _ENGINE_B_SL_SOURCE_NEUTRAL_SUFFIXES:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                changed = True
    if became_atr or base in ("", "max_sl_clamp"):
        return "atr"
    return base


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
    """Normalize execution-level source modes into stable reporting cohorts.

    Refined modes (``structural_atr_clamp_sl_structural_tp``,
    ``atr_rr_tighten_sl_structural_tp``, ...) collapse onto their origin cohort
    via ``_engine_b_base_sl_source`` instead of being discarded as unknown.
    """
    mode = str(level_mode or "")
    if mode in ENGINE_B_LEVEL_COHORTS:
        return mode
    marker = "_sl_"
    # rfind, not find: sl_source values can themselves contain "_sl_"
    # ("atr_max_sl_clamp"), while no tp_source does, so the real separator is
    # always the last occurrence.
    idx = mode.rfind(marker)
    if idx > 0 and mode.endswith("_tp"):
        tp_part = mode[idx + len(marker):]
        candidate = f"{_engine_b_base_sl_source(mode[:idx])}_sl_{tp_part}"
        if candidate in ENGINE_B_LEVEL_COHORTS:
            return candidate
    return "unknown_level_mode"


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


def _engine_b_style_threshold(cfg_val, style: str) -> float:
    """Resolve a per-style Engine B threshold from a scalar or ``{style: value}`` map.

    Returns 0.0 (disabled) for missing/malformed values so deployments without
    the new config keys keep current behavior.
    """
    raw = cfg_val
    if isinstance(cfg_val, dict):
        raw = cfg_val.get(style, cfg_val.get("default"))
    try:
        v = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, v)


def _synthesize_tp_from_sl(
    entry: float,
    exec_sl: float,
    direction: str,
    fallback_rr,
    min_rr,
    *,
    basis: str | None = None,
) -> tuple[float | None, float]:
    """Compute synthetic TP from SL distance * target RR.

    Returns (tp, target_rr) on success, (None, 0.0) when inputs are invalid.

    Target RR basis (``basis``, else ENGINE_B_SYNTHETIC_TP_RR_BASIS):
      "min_rr"      (default) — clear the RR gate and stop there. Correct when
                    the synthetic TP *replaces* the structural target: projecting
                    past the gate to ``fallback_rr`` moves the target further out
                    than the trade needs, with no structural level behind the
                    extra distance. On swing forex the legacy basis replaced a
                    structural target 0.02 RR short of the 1.8 gate with one at
                    2.5 RR — 41% further out.
      "fallback_rr" — max(fallback_rr, min_rr). Correct for the scale-out runner
                    leg, where TP1 *is* the structural target and TP2 is a
                    deliberate stretch goal, so nothing real is being discarded.
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
    _basis = str(
        basis
        or config.CONFIG.get("ENGINE_B_SYNTHETIC_TP_RR_BASIS", "min_rr")
        or "min_rr"
    ).strip().lower()
    if _basis == "min_rr" and _mr > 0:
        _target_rr = _mr
    else:
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
            "scale_out_guard_reason": None,
            "tp1_sl_retry_applied": False,
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
    try:
        from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

        if tp_sl_rr_gates_disabled(config.CONFIG):
            _enforce_max_sl = False
    except Exception:
        pass
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
        # The minimum leg WIDENS the stop, so it can undo the _keep_structural_sl
        # decision made directly above: a structural stop at 0.30 ATR clearing
        # min_rr with RR 3.33 was pushed out to 0.75 ATR, cutting RR to 1.33 and
        # multiplying risk by 2.5 — the tightest, best-located setups were
        # penalised hardest. When structure has already cleared the RR gate,
        # only the maximum leg (a genuine risk cap) still applies.
        # ENGINE_B_ATR_SL_CLAMP_MIN_RESPECTS_STRUCTURAL: false restores legacy.
        #
        # The waiver is bounded: ENGINE_B_ABSOLUTE_MIN_SL_ATR always applies. The
        # 0.75 minimum is not only about RR — risk_engine sizes volume from stop
        # distance, so an arbitrarily tight structural stop (0.05 ATR was
        # reachable without this bound) both gets hit by ordinary bar noise and
        # inflates position size. Waiving the preferred minimum is safe; removing
        # every lower bound is not.
        _min_leg_applies = not (
            _keep_structural_sl
            and bool(
                config.CONFIG.get("ENGINE_B_ATR_SL_CLAMP_MIN_RESPECTS_STRUCTURAL", True)
            )
        )
        try:
            _abs_min_sl_atr = float(
                _group_clamps.get(
                    "absolute_min_sl_atr",
                    config.CONFIG.get("ENGINE_B_ABSOLUTE_MIN_SL_ATR", 0.35),
                )
            )
        except (TypeError, ValueError):
            _abs_min_sl_atr = 0.35
        _abs_min_sl_atr = max(0.0, min(_abs_min_sl_atr, _min_sl_atr))
        _effective_min_sl_atr = _min_sl_atr if _min_leg_applies else _abs_min_sl_atr
        _dist_atr = abs(_entry - _exec_sl) / _atr
        if _dist_atr < _effective_min_sl_atr or _dist_atr > _max_sl_atr:
            _target_dist_atr = min(max(_dist_atr, _effective_min_sl_atr), _max_sl_atr)
            _exec_sl = (
                _entry - (_atr * _target_dist_atr)
                if direction == "LONG"
                else _entry + (_atr * _target_dist_atr)
            )
            _sl_source = f"{_sl_source}_atr_clamp"
            _atr_sl_clamp_applied = {
                "before_atr": round(_dist_atr, 4),
                "after_atr": round(_target_dist_atr, 4),
                "min_sl_atr": round(_min_sl_atr, 4),
                "max_sl_atr": round(_max_sl_atr, 4),
                "min_leg_applied": bool(_min_leg_applies),
                "effective_min_sl_atr": round(_effective_min_sl_atr, 4),
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
    _sl_tightened_for_rr = None
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
            # Prefer risk reduction over target inflation. The structural target
            # is the one price action actually identified; extending it past that
            # level to satisfy the gate manufactures RR instead of measuring it.
            # Tightening the stop to the distance the structural target implies
            # keeps the real target and cuts risk, bounded below by the same
            # min_sl_atr floor the clamp uses so the stop stays sane, and only
            # ever moves the stop closer to entry.
            # ENGINE_B_PREFER_SL_TIGHTEN_OVER_TP_EXTENSION: false restores legacy.
            if (
                _need_synthetic
                and not _tp_only_failure
                and _tp_source == "structural"
                and _struct_tp_valid
                and _min_rr is not None
                and _min_rr > 0
                and _atr > 0
                and _exec_sl is not None
                and _exec_tp is not None
                and bool(
                    config.CONFIG.get(
                        "ENGINE_B_PREFER_SL_TIGHTEN_OVER_TP_EXTENSION", True
                    )
                )
            ):
                _tighten_group_clamps = (
                    (config.CONFIG.get("ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES") or {})
                    .get(str(score_group or "").lower(), {})
                )
                try:
                    _tighten_floor_atr = float(
                        _tighten_group_clamps.get(
                            "min_sl_atr",
                            config.CONFIG.get("ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75),
                        )
                    )
                except (TypeError, ValueError):
                    _tighten_floor_atr = 0.75
                _tighten_floor = _atr * max(0.0, _tighten_floor_atr)
                _needed_sl_dist = abs(_exec_tp - _entry) / _min_rr
                _cur_sl_dist = abs(_entry - _exec_sl)
                if (
                    _tighten_floor <= _needed_sl_dist < _cur_sl_dist
                    and _needed_sl_dist > 0
                ):
                    _tightened_sl = (
                        _entry - _needed_sl_dist
                        if direction == "LONG"
                        else _entry + _needed_sl_dist
                    )
                    if _engine_b_sl_within_max_sl_pct(_entry, _tightened_sl, _max_sl_pct):
                        _t_rr, _t_valid, _t_reject, _t_side = _compute_exec_rr(
                            _tightened_sl, _exec_tp
                        )
                        if _t_valid and _t_rr + 1e-12 >= _min_rr:
                            _exec_sl = _tightened_sl
                            _sl_source = f"{_sl_source}_rr_tighten"
                            _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                            _rr_source = _level_mode
                            _sl_tightened_for_rr = {
                                "before_atr": round(_cur_sl_dist / _atr, 4),
                                "after_atr": round(_needed_sl_dist / _atr, 4),
                                "target_rr": round(_min_rr, 4),
                            }
                            _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = (
                                _t_rr,
                                _t_valid,
                                _t_reject,
                                _t_side,
                            )
                            _need_synthetic = False
                            _fallback_reason = None
                            _structural_tp_below_min_rr = False
            if not _need_synthetic:
                pass
            elif bool(config.CONFIG.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False)):
                # The min_rr basis exists to stop a REACHABLE structural target
                # being discarded for a further-out synthetic one. When the TP is
                # missing or wrong-side there is no such target to preserve — the
                # level is being invented from nothing, which is exactly what
                # fallback_rr is the contract for.
                _synth_tp, _ = _synthesize_tp_from_sl(
                    _entry,
                    _exec_sl,
                    direction,
                    _fallback_rr,
                    _min_rr,
                    basis="fallback_rr" if _tp_only_failure else None,
                )
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
    _scale_out_guard_reason = None
    _tp1_sl_retry_applied = False
    if (
        _fallback_applied
        and _structural_tp_below_min_rr
        and _struct_tp_valid
        and _exec_sl is not None
        and _exec_tp is not None
    ):
        _tp1_rr, _tp1_valid, _, _ = _compute_exec_rr(_exec_sl, _struct_tp)
        if _tp1_valid:
            _scale_out_ok = True
            _plan_sl = _exec_sl
            _plan_sl_source = _sl_source
            _plan_tp2 = _exec_tp
            _plan_rr1 = _tp1_rr
            _plan_retry = False

            # Runner contract: TP1 is the structural target, so nothing real is
            # discarded by making TP2 a stretch goal — fallback_rr stays its
            # correct basis even when the single-TP synthesis now targets min_rr.
            # Only ever extends TP2, and re-validates exchange bounds because the
            # bounds check upstream ran against the shorter min_rr target.
            _runner_tp2, _ = _synthesize_tp_from_sl(
                _entry, _exec_sl, direction, fallback_rr, min_rr, basis="fallback_rr"
            )
            if _runner_tp2 is not None and abs(_runner_tp2 - _entry) > abs(_plan_tp2 - _entry):
                _runner_bounds_ok = True
                if _asset_class_lower:
                    try:
                        from risk_engine import validate_tp_exchange_bounds

                        _runner_bounds_ok, _ = validate_tp_exchange_bounds(
                            _entry, _runner_tp2, _asset_class_lower, config.CONFIG
                        )
                    except Exception:
                        _runner_bounds_ok = True
                if _runner_bounds_ok:
                    _plan_tp2 = _runner_tp2

            # Guard: TP1 too close relative to the stop. Retry with the
            # tightest allowed ATR-clamp SL before dropping the scale-out plan.
            # Tightening the stop only increases RR, so this never loosens the
            # RR gate; on failure we fall back to the legacy single-TP plan
            # (the trade still flows through the unchanged gate).
            _tp1_min_rr = _engine_b_style_threshold(
                config.CONFIG.get("ENGINE_B_TP1_MIN_RR"), _style
            )
            if _tp1_min_rr > 0 and _plan_rr1 + 1e-12 < _tp1_min_rr:
                _retry_ok = False
                if _atr > 0:
                    _retry_group_clamps = (
                        (config.CONFIG.get("ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES") or {})
                        .get(str(score_group or "").lower(), {})
                    )
                    try:
                        _retry_sl_atr = float(
                            _retry_group_clamps.get(
                                "min_sl_atr",
                                config.CONFIG.get("ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75),
                            )
                        )
                    except (TypeError, ValueError):
                        _retry_sl_atr = 0.75
                    _retry_dist = _atr * max(0.0, _retry_sl_atr)
                    _cur_sl_dist = abs(_entry - _plan_sl)
                    if 0 < _retry_dist < _cur_sl_dist:
                        _retry_sl = (
                            _entry - _retry_dist
                            if direction == "LONG"
                            else _entry + _retry_dist
                        )
                        _retry_rr1, _retry_rr1_valid, _, _ = _compute_exec_rr(
                            _retry_sl, _struct_tp
                        )
                        # Re-synthesize the runner TP from the tighter SL so
                        # TP2 keeps the fallback-RR contract; the new TP2 is
                        # strictly closer to entry than the old one, so it
                        # cannot newly violate exchange TP bounds.
                        _retry_tp2, _ = _synthesize_tp_from_sl(
                            _entry, _retry_sl, direction, fallback_rr, min_rr,
                            basis="fallback_rr",
                        )
                        if (
                            _retry_rr1_valid
                            and _retry_rr1 + 1e-12 >= _tp1_min_rr
                            and _retry_tp2 is not None
                            and _engine_b_sl_within_max_sl_pct(_entry, _retry_sl, _max_sl_pct)
                        ):
                            _plan_sl = _retry_sl
                            _plan_sl_source = f"{_sl_source}_tp1_rr_retry"
                            _plan_tp2 = _retry_tp2
                            _plan_rr1 = _retry_rr1
                            _plan_retry = True
                            _retry_ok = True
                if not _retry_ok:
                    _scale_out_ok = False
                    _scale_out_guard_reason = "tp1_rr_below_min_no_sane_sl"

            # Guard: runner TP2 must have minimum room (in ATR) for the
            # scale-out plan to be worth holding a runner for. Fail-closed
            # drops back to the single-TP plan; otherwise TP2 is floored to
            # the minimum room distance (subject to exchange TP bounds).
            if _scale_out_ok:
                _tp2_min_room = _engine_b_style_threshold(
                    config.CONFIG.get("ENGINE_B_TP2_MIN_ROOM_ATR"), _style
                )
                if _tp2_min_room > 0 and _atr > 0:
                    _tp2_room_atr = abs(_plan_tp2 - _entry) / _atr
                    if _tp2_room_atr + 1e-12 < _tp2_min_room:
                        if bool(config.CONFIG.get("ENGINE_B_TP2_ROOM_FAIL_CLOSED", True)):
                            _scale_out_ok = False
                            _scale_out_guard_reason = "tp2_room_below_min_atr"
                        else:
                            _floor_tp2 = (
                                _entry + (_atr * _tp2_min_room)
                                if direction == "LONG"
                                else _entry - (_atr * _tp2_min_room)
                            )
                            _floor_ok = True
                            if _asset_class_lower:
                                try:
                                    from risk_engine import validate_tp_exchange_bounds

                                    _floor_ok, _ = validate_tp_exchange_bounds(
                                        _entry, _floor_tp2, _asset_class_lower, config.CONFIG
                                    )
                                except Exception:
                                    _floor_ok = True
                            if _floor_ok:
                                _plan_tp2 = _floor_tp2
                            else:
                                _scale_out_ok = False
                                _scale_out_guard_reason = "tp2_room_floor_exchange_bounds"

            if _scale_out_ok:
                if _plan_retry or _plan_tp2 != _exec_tp:
                    _exec_sl = _plan_sl
                    _sl_source = _plan_sl_source
                    _exec_tp = _plan_tp2
                    _level_mode = f"{_sl_source}_sl_{_tp_source}_tp"
                    _rr_source = _level_mode
                    _exec_rr, _exec_valid, _exec_reject, _exec_tp_side_ok = _compute_exec_rr(
                        _exec_sl, _exec_tp
                    )
                _tp1_sl_retry_applied = _plan_retry
                _execution_tp1 = _struct_tp
                _execution_tp2 = _exec_tp
                _execution_rr1 = _plan_rr1
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
    try:
        from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

        if tp_sl_rr_gates_disabled(config.CONFIG) and _exec_sl is not None and _exec_tp is not None:
            _exec_valid = True
            _exec_reject = None
            _rr_passed = True
    except Exception:
        pass
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
        "scale_out_guard_reason": _scale_out_guard_reason,
        "tp1_sl_retry_applied": _tp1_sl_retry_applied,
        "execution_levels_valid": _exec_valid,
        "execution_level_reject_reason": _exec_reject,
        "execution_tp_side_ok": _exec_tp_side_ok,
        "fallback_tp_applied": _fallback_applied,
        "fallback_tp_reason": _fallback_reason,
        "synthetic_rr_tp_used": bool(_tp_source == "fallback_rr" and _fallback_applied),
        "atr_sl_clamp_applied": _atr_sl_clamp_applied,
        "sl_tightened_for_rr": _sl_tightened_for_rr,
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
        closes = np.array([float(c["close"]) for c in candles], dtype=float)

        res_zones = []
        for idx in peak_idx:
            peak_price = highs[idx]
            zone_upper = peak_price + (atr * buf.get("upper", 0.5))

            # A confirmed close through the far edge breaks resistance. Keeping
            # that historical peak as an opposing wall would block later
            # continuation/retest setups even though price already accepted
            # above it. The caller supplies confirmed zone-timeframe candles.
            if np.any(closes[idx + 1 :] > zone_upper):
                continue

            # Average vol of 3 bars around the peak
            zone_vol = float(zone_vol_means[idx]) if idx < len(zone_vol_means) else 0.0
            vol_strength = min(1.0, zone_vol / avg_volume_20)

            # Zone expands below the peak (ceiling)
            res_zones.append(
                {
                    "upper": zone_upper,  # slight overshoot tolerance
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
            zone_lower = trough_price - (atr * buf.get("upper", 0.5))

            # Symmetric support invalidation: once a confirmed close accepts
            # below the far edge, this trough is no longer a valid opposing wall.
            if np.any(closes[idx + 1 :] < zone_lower):
                continue

            # Average vol of 3 bars around the trough
            zone_vol = float(zone_vol_means[idx]) if idx < len(zone_vol_means) else 0.0
            vol_strength = min(1.0, zone_vol / avg_volume_20)

            # Zone expands above the trough (floor)
            sup_zones.append(
                {
                    "lower": zone_lower,
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
        try:
            _eq_atr = float(
                (config.CONFIG.get("NAKED_ENGINE") or {}).get(
                    "equal_extrema_atr_mult", 0.15
                )
                or 0.15
            )
        except (TypeError, ValueError):
            _eq_atr = 0.15
        equal_highs = (
            len(last_peaks) >= 2 and abs(last_peaks[-1] - last_peaks[-2]) < atr * _eq_atr
        )
        equal_lows = (
            len(last_troughs) >= 2
            and abs(last_troughs[-1] - last_troughs[-2]) < atr * _eq_atr
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
        volumes are real exchange contract volume (Bybit/Binance). MT5
        tick-volume gating is handled by the caller — see analyze_structure().
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
            bos_volume_available = False
            bos_bar_volume = None
            bos_average_volume_20 = None
            bos_volume_ratio = None
            bos_volume_status = "no_bos"
            multiplier = float(
                config.CONFIG.get("NAKED_ENGINE", {}).get("bos_volume_multiplier", 1.3)
            )
            if bos_bull or bos_bear:
                bos_volume_status = "unavailable"
                if volumes is not None and len(volumes) >= 20:
                    positive_vols = [float(v) for v in volumes[-20:] if float(v) > 0]
                    avg_vol_20 = float(np.mean(positive_vols)) if positive_vols else 0.0
                    ref_idx = bos_bull_vol_ref if bos_bull else bos_bear_vol_ref
                    if avg_vol_20 > 0:
                        bos_average_volume_20 = avg_vol_20
                        if ref_idx is not None and 0 <= ref_idx < len(volumes):
                            bos_bar_volume = float(volumes[ref_idx])
                        elif ref_idx is None and len(volumes):
                            bos_bar_volume = float(volumes[-1])
                    if bos_bar_volume is not None and bos_bar_volume > 0 and avg_vol_20 > 0:
                        bos_volume_available = True
                        bos_volume_ratio = bos_bar_volume / avg_vol_20
                        # C-3: when forming bars are NOT stripped from the structure TF,
                        # the last bar can be a forming (unconfirmed) candle whose volume
                        # is incomplete; do not gate BOS on partial volume. Default config
                        # strips forming bars, so the gate uses the confirmed last bar.
                        _strip_forming = bool(
                            config.CONFIG.get("ENGINE_B_STRIP_FORMING_STRUCT", True)
                        )
                        _bos_on_last_bar = ref_idx is None or ref_idx >= n - 1
                        if _bos_on_last_bar and not _strip_forming:
                            bos_volume_status = "forming_bar_unconfirmed"
                        else:
                            bos_volume_confirmed = bos_volume_ratio >= multiplier
                            bos_volume_status = (
                                "confirmed" if bos_volume_confirmed else "below_threshold"
                            )
                elif volumes is not None:
                    bos_volume_status = "insufficient_history"

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
                "bos_volume_available": bos_volume_available,
                "bos_bar_volume": round(bos_bar_volume, 4) if bos_bar_volume is not None else None,
                "bos_average_volume_20": (
                    round(bos_average_volume_20, 4)
                    if bos_average_volume_20 is not None
                    else None
                ),
                "bos_volume_ratio": round(bos_volume_ratio, 4) if bos_volume_ratio is not None else None,
                "bos_volume_threshold": multiplier,
                "bos_volume_status": bos_volume_status,
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
                n_closes = len(closes) if closes is not None else 0
                try:
                    choch_lookback = int(
                        config.CONFIG.get(
                            "ENGINE_B_CHOCH_INVALIDATION_BARS",
                            config.CONFIG.get("ENGINE_B_BOS_LOOKBACK_BARS", 5),
                        )
                        or 5
                    )
                except (TypeError, ValueError):
                    choch_lookback = 5
                choch_lookback = max(1, min(choch_lookback, n_closes)) if n_closes else 1

                def _choch_still_valid(break_idx: int, level: float, *, bullish: bool) -> bool:
                    """Fail closed if any subsequent close reclaims across the CHoCH level."""
                    if closes is None or n_closes <= 0:
                        return False
                    for j in range(break_idx + 1, n_closes):
                        cj = float(closes[j])
                        if bullish and cj < level:
                            return False
                        if (not bullish) and cj > level:
                            return False
                    return True

                if bos_data.get("bos_bear") and bos_data.get("bos_reference_high") is not None:
                    ref_high = float(bos_data["bos_reference_high"])
                    # Scan lookback for a CHoCH print (parity with BOS invalidation).
                    for k in range(1, choch_lookback + 1):
                        idx = n_closes - k
                        if float(closes[idx]) > ref_high + min_break_abs and _choch_still_valid(
                            idx, ref_high, bullish=True
                        ):
                            choch_bull = True
                            choch_level = ref_high
                            choch_events.append({"type": "bullish", "level": ref_high})
                            break
                if bos_data.get("bos_bull") and bos_data.get("bos_reference_low") is not None:
                    ref_low = float(bos_data["bos_reference_low"])
                    for k in range(1, choch_lookback + 1):
                        idx = n_closes - k
                        if float(closes[idx]) < ref_low - min_break_abs and _choch_still_valid(
                            idx, ref_low, bullish=False
                        ):
                            choch_bear = True
                            choch_level = ref_low
                            choch_events.append({"type": "bearish", "level": ref_low})
                            break
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
            "sweep_direction": None,
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
            sweep_direction = None
            try:
                wick_atr_mult = float(
                    (config.CONFIG.get("NAKED_ENGINE") or {}).get(
                        "sweep_wick_atr_mult", 0.3
                    )
                    or 0.3
                )
            except (TypeError, ValueError):
                wick_atr_mult = 0.3

            # Track best (most recent, then largest wick) candidate per side.
            best_bull: tuple[int, float, float] | None = None  # (bar_i, wick_depth, low)
            best_bear: tuple[int, float, float] | None = None

            for i in range(sweep_lookback):
                high = float(recent_highs[i])
                low = float(recent_lows[i])
                close = float(recent_closes[i])

                # Bullish sweep: wick below swing low, close above it (stop hunt below → reversal up)
                if low < ref_low - wick_atr_mult * atr and close > ref_low:
                    depth = float(ref_low) - low
                    if best_bull is None or i > best_bull[0] or (
                        i == best_bull[0] and depth > best_bull[1]
                    ):
                        best_bull = (i, depth, low)

                # Bearish sweep: wick above swing high, close below it (stop hunt above → reversal down)
                if high > ref_high + wick_atr_mult * atr and close < ref_high:
                    depth = high - float(ref_high)
                    if best_bear is None or i > best_bear[0] or (
                        i == best_bear[0] and depth > best_bear[1]
                    ):
                        best_bear = (i, depth, high)

            if best_bull is not None and best_bear is not None:
                # Mutual exclusion: keep the more recent sweep; tie-break on wick depth.
                if best_bull[0] > best_bear[0] or (
                    best_bull[0] == best_bear[0] and best_bull[1] >= best_bear[1]
                ):
                    best_bear = None
                else:
                    best_bull = None

            if best_bull is not None:
                bull_sweep = True
                sweep_low = best_bull[2]
                sweep_direction = "LONG"
            if best_bear is not None:
                bear_sweep = True
                sweep_high = best_bear[2]
                sweep_direction = "SHORT"

            return {
                "bull_sweep": bull_sweep,
                "bear_sweep": bear_sweep,
                "sweep_low": sweep_low,
                "sweep_high": sweep_high,
                "sweep_direction": sweep_direction,
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
        score_group: str | None = None,
        trigger_tf: str | None = None,
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

        # Class thresholds only on M5/M15/M30 — same gate as trigger ATR period.
        # H1/H4/D1 keep legacy NAKED_ENGINE / hardcoded thresholds.
        use_cal = engine_b_trigger_tf_uses_calibration(trigger_tf)
        if use_cal:
            cal = resolve_engine_b_trigger_tf_calibration(score_group, asset_type)
            keyed = config.CONFIG.get("ENGINE_B_TRIGGER_TF_CALIBRATION") or {}
            g = str(score_group or "").strip().lower()
            a = str(asset_type or "").strip().lower()
            has_cal_row = isinstance(keyed, dict) and (
                (g and isinstance(keyed.get(g), dict))
                or (a and isinstance(keyed.get(a), dict))
            )
        else:
            cal = None
            has_cal_row = False
        if has_cal_row and isinstance(cal, dict):
            rejection_ratio = float(cal.get("rejection_wick_body", 1.2))
            engulf_mult = float(cal.get("engulfing_body_atr", 0.2))
            strong_close_pct = float(cal.get("strong_close_pct", 0.70))
        else:
            rejection_ratio = _engine_b_rejection_wick_body_ratio()
            engulf_mult = _naked_engine_atr_mult(
                "engulfing_min_body_atr_mult", 0.2, asset_type
            )
            strong_close_pct = 0.70
        # In trending regimes, candles have larger bodies and smaller wicks —
        # reduce the wick/body requirement so rejection patterns still fire.
        # Cap applies AFTER the per-class base value is resolved.
        if is_trending:
            rejection_ratio = min(rejection_ratio, 1.0)
        bull_rejection = lower_wick >= max(body * rejection_ratio, atr * 0.08) and close >= low + (range_ * 0.6)
        bear_rejection = upper_wick >= max(body * rejection_ratio, atr * 0.08) and close <= high - (range_ * 0.6)

        # Engulfing displacement floor: a real engulfing trigger needs a body
        # that means something at this timeframe's volatility. Without it,
        # doji-engulfs-doji noise in quiet regimes fires the entry gate.
        # Per-class ENGINE_B_TRIGGER_TF_CALIBRATION overrides NAKED_ENGINE when
        # present AND trigger TF is M5/M15/M30.
        _engulf_min_body = atr * engulf_mult if atr and atr > 0 else 0.0
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

        bull_strong_close = close > open_ and close >= low + (range_ * strong_close_pct)
        bear_strong_close = close < open_ and close <= high - (range_ * strong_close_pct)

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

        # Direction from weighted score (threshold config-gated).
        try:
            _dir_vote_thr = abs(
                float(
                    config.CONFIG.get(
                        "ENGINE_B_INDEPENDENT_DIRECTION_SCORE_THRESHOLD", 0.15
                    )
                    or 0.15
                )
            )
        except (TypeError, ValueError):
            _dir_vote_thr = 0.15
        if score_ratio > _dir_vote_thr:
            direction = "LONG"
            reason = (
                f"Structural evidence bullish: {positive}/{len(active_votes)} indicators agree "
                f"(h4={h4_sequence}, choch={votes.get('choch', 0)}, sweep={votes.get('sweep', 0)})"
            )
        elif score_ratio < -_dir_vote_thr:
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
        trade_bucket_historical_mode: bool = False,
        role_candles: dict[str, list] | None = None,
        trigger_tf_override: str | None = None,
        struct_candles_confirmed: bool = False,
        dxy_h4_closes: list | None = None,
    ) -> dict:
        """Precompute all direction-independent structure analysis data.

        Returns a precompute context dict that can be passed to
        ``analyze_structure_direction()``. Call this once per bar, then
        iterate over directions with the lightweight direction-dependent step.

        Backward-compatible: the original ``analyze_structure()`` calls this
        internally.

        Diagnostic kwargs (default None — production path unchanged):
          * ``role_candles`` — optional map of TF → candle series (e.g. M15/M30)
          * ``trigger_tf_override`` — force trigger pattern detection onto that TF
          * ``struct_candles_confirmed`` — caller already supplied a confirmed-only
            historical structure window; avoids repeating the live forming-bar split
        """
        if not d1_candles or not h4_candles or not h1_candles or atr <= 0:
            return {"_error": "Missing data or valid ATR"}

        registry_symbol = self._consume_registry_symbol() if enable_zone_registry else None

        _pair_symbol = ""
        _pair_score_group = None
        if isinstance(pair, dict):
            _pair_symbol = str(pair.get("display") or pair.get("symbol") or "")
            _pair_score_group = pair.get("score_group")
            if not _pair_score_group:
                try:
                    from engine_a_groups import resolve_score_group_by_type

                    _pair_score_group = resolve_score_group_by_type(pair)
                except Exception:
                    _pair_score_group = None
        _tfs = dict(
            resolve_engine_b_tfs(
                asset_type,
                style,
                symbol=_pair_symbol,
                score_group=str(_pair_score_group) if _pair_score_group else None,
            )
        )
        _override_tf = str(trigger_tf_override or "").strip().upper() or None
        _extras = role_candles if isinstance(role_candles, dict) else None
        structure_tf = str(_tfs["struct"]).upper()
        # Structure must come from the rung the policy names. The previous
        # ``else: h1_candles`` fallback silently served H1 for any non-canonical
        # structure TF (Engine B scalp resolves M15), which is the one
        # substitution the fail-closed contract forbids.
        struct_candles = engine_b_candles_for_tf(
            structure_tf,
            d1_candles,
            h4_candles,
            h1_candles,
            extra_by_tf=_extras if _extras is not None else {},
        )
        if not struct_candles:
            return {
                "_error": f"missing_required_structure_timeframe:{structure_tf}",
            }
        forming_strip_diag: dict = {}
        if struct_candles_confirmed:
            if not bool(config.CONFIG.get("ENGINE_B_STRIP_FORMING_STRUCT", True)):
                forming_strip_diag["reason"] = "disabled"
            elif not struct_candles or len(struct_candles) < 2:
                forming_strip_diag.update(
                    {"reason": "insufficient_input", "candle_count": len(struct_candles or [])}
                )
            elif not pair or not isinstance(pair, dict):
                forming_strip_diag["reason"] = "missing_pair_context"
            else:
                min_bars = int(
                    config.CONFIG.get("ENGINE_B_STRUCT_CONFIRMED_MIN_BARS", 20) or 20
                )
                if len(struct_candles) >= min_bars:
                    forming_strip_diag.update(
                        {
                            "reason": "preconfirmed_by_caller",
                            "confirmed_count": len(struct_candles),
                            "min_bars": min_bars,
                        }
                    )
                else:
                    forming_strip_diag.update(
                        {
                            "reason": "insufficient_confirmed_bars",
                            "confirmed_count": len(struct_candles),
                            "min_bars": min_bars,
                        }
                    )
        else:
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

        if _override_tf:
            # Fail closed: never silently substitute H1 for a requested lower TF.
            if _override_tf in ("D1", "H4", "H1"):
                trigger_candles = engine_b_candles_for_tf(
                    _override_tf, d1_candles, h4_candles, h1_candles
                )
            else:
                trigger_candles = engine_b_candles_for_tf(
                    _override_tf,
                    d1_candles,
                    h4_candles,
                    h1_candles,
                    extra_by_tf=_extras if _extras is not None else {},
                )
            _tfs["trigger"] = _override_tf
        else:
            _resolved_trigger_tf = str(_tfs["trigger"]).upper()
            trigger_candles = engine_b_candles_for_tf(
                _resolved_trigger_tf,
                d1_candles,
                h4_candles,
                h1_candles,
                extra_by_tf=(
                    _extras
                    if _extras is not None
                    else {} if _resolved_trigger_tf not in {"D1", "H4", "H1"} else None
                ),
            )
        if not trigger_candles:
            return {
                "_error": f"missing_required_trigger_timeframe:{_tfs['trigger']}",
                "forming_strip_diagnostics": forming_strip_diag,
            }

        # Conditional M5 (policy v4 M5Policy.CONDITIONAL) is not an
        # unconditional trigger promotion: M5 supplies the entry event only
        # after the prerequisite setup rung (M15) has armed. Resolve that
        # series here so the direction pass can evaluate it; missing data fails
        # closed rather than granting bare M5 authority.
        _effective_trigger_tf = str(_tfs.get("trigger") or "").upper()
        _m5_prereq_tf = str(
            _tfs.get("execution_prerequisite") or _tfs.get("setup") or ""
        ).upper()
        _m5_conditional_required = bool(
            _effective_trigger_tf == "M5"
            and str(_tfs.get("m5_policy") or "").lower() == "conditional"
            and bool(config.CONFIG.get("ENGINE_B_M5_REQUIRE_SETUP_ARMED", True))
        )
        _m5_prereq_candles: list = []
        _m5_prereq_atr = 0.0
        if _m5_conditional_required:
            if not _m5_prereq_tf or _m5_prereq_tf == "M5":
                return {
                    "_error": "missing_m5_prerequisite_timeframe:UNRESOLVED",
                    "forming_strip_diagnostics": forming_strip_diag,
                }
            _m5_prereq_candles = engine_b_candles_for_tf(
                _m5_prereq_tf,
                d1_candles,
                h4_candles,
                h1_candles,
                extra_by_tf=_extras if _extras is not None else {},
            )
            if not _m5_prereq_candles:
                return {
                    "_error": f"missing_m5_prerequisite_timeframe:{_m5_prereq_tf}",
                    "forming_strip_diagnostics": forming_strip_diag,
                }
            _m5_prereq_atr = self._compute_atr_from_candles(
                _m5_prereq_candles,
                period=engine_b_trigger_atr_period(
                    _pair_score_group, asset_type, _m5_prereq_tf
                ),
                fallback=atr,
            )

        _zone_tf = str(_tfs.get("zone") or structure_tf or "H4").upper()
        _zone_fvg_candles = engine_b_candles_for_tf(
            _zone_tf, d1_candles, h4_candles, h1_candles, extra_by_tf=_extras
        )

        _bias_tf = str(_tfs.get("bias") or "").upper()
        _bias_candles = engine_b_candles_for_tf(
            _bias_tf, d1_candles, h4_candles, h1_candles, extra_by_tf=_extras
        )
        if not _bias_candles:
            return {
                "_error": f"missing_required_bias_timeframe:{_bias_tf or 'UNKNOWN'}",
                "forming_strip_diagnostics": forming_strip_diag,
            }

        h4_highs = np.array([float(c["high"]) for c in h4_candles])
        h4_lows = np.array([float(c["low"]) for c in h4_candles])

        struct_highs = np.array([float(c["high"]) for c in struct_candles])
        struct_lows = np.array([float(c["low"]) for c in struct_candles])
        struct_closes = np.array([float(c["close"]) for c in struct_candles])
        struct_atr = self._compute_atr_from_candles(struct_candles, fallback=atr)
        zone_atr = self._compute_atr_from_candles(_zone_fvg_candles, fallback=atr)
        h4_atr = self._compute_atr_from_candles(h4_candles, fallback=atr)
        # Trigger-local ATR scales candle-body/wick and follow-through checks on
        # the same lower timeframe. It never replaces the style ATR used for
        # structural zones, SL/TP, RR, or room gates.
        _score_group = (
            (pair or {}).get("score_group")
            or (pair or {}).get("group")
            or (pair or {}).get("asset_group")
            or _pair_score_group
        )
        _trigger_tf_key = str(_tfs.get("trigger") or "").upper()
        _trigger_cal = resolve_engine_b_trigger_tf_calibration(
            _score_group, asset_type, _trigger_tf_key
        )
        _trigger_atr_period = engine_b_trigger_atr_period(
            _score_group, asset_type, _trigger_tf_key
        )
        trigger_atr = self._compute_atr_from_candles(
            trigger_candles, period=_trigger_atr_period, fallback=atr
        )
        asset_struct_diag = _asset_class_structure_adjustment(
            asset_type, _score_group, struct_candles, struct_atr, timeframe=structure_tf
        )
        crypto_struct_diag = _crypto_structure_adjustment(
            asset_type, struct_candles, struct_atr, timeframe=structure_tf
        )
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

        sequence_data = self._determine_sequence(struct_highs, struct_lows, struct_atr, "LONG", swings=struct_swings)
        # Macro sequence tracks the policy bias role instead of a hardcoded H4
        # series. This preserves deliberate same-role policies while preventing
        # swing structure (H4) from being double-counted as macro bias (D1).
        if _bias_tf == "H4":
            _bias_highs, _bias_lows, _bias_atr = h4_highs, h4_lows, h4_atr
        else:
            _bias_highs = np.array([float(c["high"]) for c in _bias_candles])
            _bias_lows = np.array([float(c["low"]) for c in _bias_candles])
            _bias_atr = self._compute_atr_from_candles(_bias_candles, fallback=0.0)
            if _bias_atr <= 0:
                return {
                    "_error": f"invalid_required_bias_atr:{_bias_tf}",
                    "forming_strip_diagnostics": forming_strip_diag,
                }
        _bias_swings = self._swing_cache(_bias_highs, _bias_lows, _bias_atr)
        macro_seq_data = self._determine_sequence(_bias_highs, _bias_lows, _bias_atr, "LONG", swings=_bias_swings)

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
        d1_crypto_diag = _crypto_structure_adjustment(
            asset_type, d1_candles, d1_atr, timeframe="D1"
        )
        d1_asset_diag = _asset_class_structure_adjustment(
            asset_type, _score_group, d1_candles, d1_atr, timeframe="D1"
        )
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
            # FVG cache must use zone TF (D1 on swing), not hardcoded H4.
            fvg_registry_tf = str(_zone_tf or structure_tf or "H4").upper()
            zone_registry.upsert_zones(
                registry_symbol, structure_tf, order_blocks, [], atr=struct_atr, asset_type=asset_type
            )
            zone_registry.upsert_zones(
                registry_symbol, fvg_registry_tf, [], fvgs, atr=zone_atr, asset_type=asset_type
            )
            zone_registry.mark_mitigated(registry_symbol, structure_tf, current_price, atr)
            zone_registry.mark_mitigated(registry_symbol, fvg_registry_tf, current_price, atr)
            zone_registry.prune_old_zones()
            if zone_registry.has_zones(registry_symbol, structure_tf):
                order_blocks = self._registry_order_blocks(zone_registry.get_active_zones(registry_symbol, structure_tf))
            if zone_registry.has_zones(registry_symbol, fvg_registry_tf):
                active_fvgs = self._registry_fvgs(
                    zone_registry.get_active_zones(registry_symbol, fvg_registry_tf)
                )

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
            historical_mode=trade_bucket_historical_mode,
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
            "_trigger_tf_override": _override_tf,
            "_m5_prereq_candles": _m5_prereq_candles,
            "m5_prereq_atr": _m5_prereq_atr,
            "m5_prereq_tf": _m5_prereq_tf if _m5_conditional_required else None,
            "m5_conditional_required": _m5_conditional_required,
            "m5_policy": str(_tfs.get("m5_policy") or "disabled"),
            "_struct_candles": struct_candles,
            "_score_group": _score_group,
            "asset_type": asset_type,
            "atr": atr,
            "d1_atr": d1_atr,
            "struct_atr": struct_atr,
            "zone_atr": zone_atr,
            "h4_atr": h4_atr,
            "trigger_atr": trigger_atr,
            "trigger_atr_period": _trigger_atr_period,
            "trigger_tf_calibration": dict(_trigger_cal),
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
            "macro_sequence_tf": _bias_tf,
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
            # Backtest flag: lets calculate_confidence resolve point-in-time
            # subsystem data (carry/COT as-of the bar) instead of current values.
            "historical_mode": bool(trade_bucket_historical_mode),
            # DXY macro-correlation inputs (only populated when the caller
            # supplies DXY closes; consumed by the config-gated DXY gate).
            "_dxy_h4_closes": list(dxy_h4_closes) if dxy_h4_closes else None,
            "_pair_h4_closes": (
                [float(c["close"]) for c in h4_candles] if dxy_h4_closes else None
            ),
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
        h4_atr = precompute.get("h4_atr") or zone_atr
        trigger_atr = precompute.get("trigger_atr") or atr
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
        # sl_mult is the ATR buffer placed BEYOND the swing pivot, not a
        # standalone stop distance — see _engine_b_structural_sl_pivot_buffer_atr
        # for why the legacy zone_multipliers.sl values made the structural stop
        # unselectable. Legacy path stays available for A/B backtests.
        if bool(config.CONFIG.get("ENGINE_B_STRUCTURAL_SL_PIVOT_BUFFER_ENABLED", True)):
            sl_mult, sl_mult_source = _engine_b_structural_sl_pivot_buffer_atr(regime)
        else:
            sl_mult = float(buf.get("sl", 1.0))
            sl_mult_source = "zone_multipliers_legacy"
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

        try:
            _tp_min_atr = float(
                (config.CONFIG.get("NAKED_ENGINE") or {}).get(
                    "structural_tp_min_distance_atr", 0.5
                )
                or 0.5
            )
        except (TypeError, ValueError):
            _tp_min_atr = 0.5
        _tp_min_dist = atr * _tp_min_atr
        if direction == "LONG" and valid_res:
            for zone in sorted(valid_res, key=lambda z: z["lower"]):
                structural_tp = _engine_b_structural_target_price(zone, direction, atr, structural_tp_buffer_mult)
                target_distance = structural_tp - current_price
                structural_target_candidates.append({"target_type": "resistance_zone", "target_price": structural_tp, "zone": dict(zone), "correct_side": structural_tp > current_price, "distance_to_target": target_distance, "atr_multiple_to_target": (target_distance / atr) if atr > 0 else None, "selected": False, "rejected_reason": "too_close_or_wrong_side" if structural_tp <= current_price + _tp_min_dist else None})
                if structural_tp <= current_price + _tp_min_dist:
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
                structural_target_candidates.append({"target_type": "support_zone", "target_price": structural_tp, "zone": dict(zone), "correct_side": structural_tp < current_price, "distance_to_target": target_distance, "atr_multiple_to_target": (target_distance / atr) if atr > 0 else None, "selected": False, "rejected_reason": "too_close_or_wrong_side" if structural_tp >= current_price - _tp_min_dist else None})
                if structural_tp >= current_price - _tp_min_dist:
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
        # 2026-07-10 audit: OB-at-zone confluence must be direction-aligned — a
        # bearish OB overlapping the support zone is opposing evidence, not
        # confluence for a LONG. Config escape hatch restores legacy any-type
        # behaviour (ENGINE_B_OB_CONFLUENCE_DIRECTIONAL: false).
        _ob_directional = bool(
            config.CONFIG.get("ENGINE_B_OB_CONFLUENCE_DIRECTIONAL", True)
        )
        _aligned_ob_type = "bullish" if direction == "LONG" else "bearish"
        _ob_at_zone = False
        if active_zone and order_blocks:
            az_lower = active_zone.get("lower", 0)
            az_upper = active_zone.get("upper", 0)
            for ob in order_blocks:
                if _ob_directional and str(ob.get("type") or "").lower() != _aligned_ob_type:
                    continue
                if not (ob["top"] < az_lower or ob["bottom"] > az_upper):
                    if ob.get("strength", 0) >= _ob_min_strength:
                        _ob_at_zone = True
                        break
        trigger_ctx = self._price_action_trigger(
            trigger_candles, direction, trigger_atr,
            zone_ctx["zone_touched"] or zone_ctx["near_zone"],
            bos_confirmed,
            is_trending=(
                (direction == "LONG" and sequence_data["state"] == "HH_HL" and macro_seq_data["state"] == "HH_HL")
                or (direction == "SHORT" and sequence_data["state"] == "LH_LL" and macro_seq_data["state"] == "LH_LL")
            ),
            asset_type=asset_type,
            score_group=_zone_score_group,
            trigger_tf=str(tfs.get("trigger") or ""),
        )

        # Conditional-M5 prerequisite: the setup rung must have armed a
        # direction-aligned trigger before the M5 entry event counts. This is
        # the deterministic half of M5Policy.CONDITIONAL — the advisory
        # m5Eligibility stamp never gated anything.
        _m5_conditional_required = bool(precompute.get("m5_conditional_required"))
        _m5_prereq_tf = precompute.get("m5_prereq_tf")
        _m5_prereq_candles = precompute.get("_m5_prereq_candles") or []
        m5_setup_armed = None
        m5_setup_armed_pattern = None
        if _m5_conditional_required:
            _prereq_ctx = self._price_action_trigger(
                _m5_prereq_candles,
                direction,
                float(precompute.get("m5_prereq_atr") or trigger_atr or atr),
                zone_ctx["zone_touched"] or zone_ctx["near_zone"],
                bos_confirmed,
                is_trending=(
                    (direction == "LONG" and sequence_data["state"] == "HH_HL" and macro_seq_data["state"] == "HH_HL")
                    or (direction == "SHORT" and sequence_data["state"] == "LH_LL" and macro_seq_data["state"] == "LH_LL")
                ),
                asset_type=asset_type,
                score_group=_zone_score_group,
                trigger_tf=str(_m5_prereq_tf or ""),
            )
            m5_setup_armed = bool(_prereq_ctx.get("trigger_ok"))
            m5_setup_armed_pattern = _prereq_ctx.get("pattern")
            if not m5_setup_armed:
                trigger_ctx = dict(trigger_ctx)
                trigger_ctx.update(
                    {
                        "pattern": "NONE",
                        "trigger_ok": False,
                        "rejection": False,
                        "engulfing": False,
                        "inside_break": False,
                        "strong_close": False,
                    }
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
            "macro_sequence_tf": precompute.get("macro_sequence_tf"),
            **(
                {
                    "_dxy_h4_closes": precompute.get("_dxy_h4_closes"),
                    "_pair_h4_closes": precompute.get("_pair_h4_closes"),
                }
                if precompute.get("_dxy_h4_closes")
                else {}
            ),
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
            "distance_to_res_pct": (
                ((nearest_res["lower"] - current_price) / current_price * 100.0)
                if nearest_res and current_price > 0 else None
            ),
            "distance_to_sup_pct": (
                ((current_price - nearest_sup["upper"]) / current_price * 100.0)
                if nearest_sup and current_price > 0 else None
            ),
            "distance_to_res_atr": (
                ((nearest_res["lower"] - current_price) / atr)
                if nearest_res and atr > 0 else None
            ),
            "distance_to_sup_atr": (
                ((current_price - nearest_sup["upper"]) / atr)
                if nearest_sup and atr > 0 else None
            ),
            "atr": atr,
            "d1_atr": d1_atr,
            "struct_atr": struct_atr,
            "zone_atr": zone_atr,
            "h4_atr": h4_atr,
            "trigger_atr": trigger_atr,
            "trigger_atr_tf": tfs["trigger"],
            # Levels ATR provenance. STYLE_ATR_MULTS is documented against an
            # H4 (intraday) / D1 (swing) ATR reference, but Engine B feeds the
            # policy structure rung (H1 intraday / H4 swing). Emitting the TF
            # that actually produced `atr` makes the effective multiple
            # measurable instead of inferred from stale comments.
            "levels_atr_tf": tfs["atr"],
            "structural_sl_buffer_atr_mult": round(float(sl_mult), 4),
            "structural_sl_buffer_source": sl_mult_source,
            "trigger_atr_period": precompute.get("trigger_atr_period"),
            "trigger_tf_calibration": precompute.get("trigger_tf_calibration"),
            "bos_confirmed": bos_confirmed,
            # bos_mtf_confirmed stays direction-blind (an MTF break exists in
            # *some* direction) for backward compatibility; bos_mtf_aligned is
            # the direction-aware value scoring already uses, exported so UI and
            # AI consumers stop reading an opposing break as confirmation.
            "bos_mtf_confirmed": bos_mtf_confirmed,
            "bos_mtf_aligned": bool(bos_mtf_confirmed and bos_confirmed),
            "bos_volume_confirmed": bos_data.get("bos_volume_confirmed", False),
            "m5_policy": precompute.get("m5_policy"),
            "m5_conditional_required": _m5_conditional_required,
            "m5_prereq_tf": _m5_prereq_tf,
            "m5_setup_armed": m5_setup_armed,
            "m5_setup_armed_pattern": m5_setup_armed_pattern,
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
            "sweep_direction": sweep_data.get("sweep_direction"),
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
            "historical_mode": bool(precompute.get("historical_mode")),
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
        trade_bucket_historical_mode: bool = False,
        role_candles: dict[str, list] | None = None,
        trigger_tf_override: str | None = None,
        dxy_h4_closes: list | None = None,
    ) -> dict:
        """
        Analyzes raw candle data to find Support/Resistance zones and trend sequence.
        Returns structural verdict used by the Comparator in athena.py.

        Positional candle args are canonical D1/H4/H1 series (not style zone/entry
        roles). Role timeframes are resolved inside ``precompute_structure_data()``.

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
            trade_bucket_historical_mode=trade_bucket_historical_mode,
            role_candles=role_candles,
            trigger_tf_override=trigger_tf_override,
            dxy_h4_closes=dxy_h4_closes,
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
        try:
            trigger_atr_val = float(res.get("trigger_atr") or atr_val)
        except (TypeError, ValueError):
            trigger_atr_val = atr_val
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
        expected_trigger_tf = str(profile.get("entry_tf") or "").upper()
        actual_trigger_tf = str(res.get("trigger_timeframe") or "").upper()
        # M5 belongs in this set: it is a policy trigger rung for the
        # fast/conditional profiles, so a structure pass that ran on a
        # different series must not satisfy the entry gate.
        trigger_timeframe_gate_required = expected_trigger_tf in {"M5", "M15", "M30"}
        trigger_timeframe_gate_ok = (
            not trigger_timeframe_gate_required
            or actual_trigger_tf == expected_trigger_tf
        )
        # Conditional M5 additionally requires the setup rung to have armed.
        # ``m5_setup_armed`` is None when the prerequisite does not apply.
        m5_conditional_required = bool(res.get("m5_conditional_required"))
        m5_setup_armed = res.get("m5_setup_armed")
        if m5_conditional_required and not bool(m5_setup_armed):
            trigger_timeframe_gate_ok = False

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
        # bos_mtf_confirmed is direction-blind (an MTF BOS exists in *some*
        # direction). It must only satisfy the structure gate / earn bonus when
        # the break is in the trade direction, otherwise a bullish multi-TF
        # break can pass a SHORT (and vice versa). bos_confirmed is already
        # direction-aware, so (mtf-exists AND direction-aligned H1 BOS) == the
        # H1 and D1 BOS both breaking in the trade direction.
        bos_mtf_aligned = bos_mtf and bool(res.get("bos_confirmed", False))
        # When both micro and macro oppose, a lone BOS/sweep is not enough unless
        # MTF BOS confirms (config-gated; default ON).
        _require_align_or_mtf = bool(
            config.CONFIG.get("ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", True)
        )
        _both_oppose = (not micro_aligned) and (not macro_aligned)
        # Sweep only counts for structure_ok when aligned with trade direction.
        _sweep_dir = str(res.get("sweep_direction") or "").upper() or None
        _sweep_aligned = bool(res.get("liquidity_sweep", False)) and (
            _sweep_dir is None or _sweep_dir == direction
        )
        if _require_align_or_mtf and _both_oppose:
            structure_ok = (not _hard_counter_veto) and bos_mtf_aligned
        else:
            structure_ok = (not _hard_counter_veto) and (
                micro_aligned
                or macro_aligned
                or res.get("bos_confirmed", False)
                or _sweep_aligned
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
        # Trusted VP/CVD crypto score groups hard-require aggTrade when configured.
        _score_group_key = str(profile.get("score_group") or "").strip().lower()
        _trusted_crypto_groups = engine_b_profile_trusted_score_groups()
        _aggtrade_group_trusted = bool(
            _score_group_key
            and _score_group_key.startswith("crypto_")
            and _score_group_key in _trusted_crypto_groups
        )
        if _aggtrade_group_trusted:
            _trusted_mode = str(
                config.CONFIG.get("ENGINE_B_CRYPTO_AGGTRADE_TRUSTED_MODE", "required")
                or "required"
            ).lower()
            if _trusted_mode in {"required", "preferred", "degraded"}:
                _aggtrade_mode = _trusted_mode
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

        # DXY macro-correlation gate — globally opt-in for forex; a group-level
        # macro_required override also activates the same fail-closed gate.
        _macro_required_group = bool(profile.get("macro_required", False))
        _dxy_gate_enabled = asset_type_lower == "forex" and (
            bool(config.CONFIG.get("ENGINE_B_DXY_MACRO_GATE_ENABLED", False))
            or _macro_required_group
        )
        dxy_ok = True
        dxy_gate_reason = None
        if _dxy_gate_enabled:
            _dxy_closes = res.pop("_dxy_h4_closes", None)
            _pair_h4_closes = res.pop("_pair_h4_closes", None)
            if _dxy_closes and _pair_h4_closes:
                dxy_ok, dxy_gate_reason = self.check_macro_correlation_detail(
                    _pair_h4_closes,
                    _dxy_closes,
                    direction,
                    macro_required=True,
                )
            elif bool(
                config.CONFIG.get("ENGINE_B_MACRO_SHORT_HISTORY_FAIL_CLOSED", True)
            ):
                dxy_ok = False
                dxy_gate_reason = "dxy_series_unavailable"

        zone_ok = bool(res.get("zone_touched") or res.get("near_active_zone"))
        trigger_ok = bool(res.get("trigger_ok")) and trigger_timeframe_gate_ok
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
        nearest_res = res.get("nearest_resistance_zone")
        nearest_sup = res.get("nearest_support_zone")

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
        # TP1 wall clamp: when the selected TP1 overshoots the nearest opposing
        # zone, re-target it to the wall's front edge if the clamped RR still
        # clears its floor. Runs before rr/rr_ok extraction so all downstream
        # gates and the emitted payload see the clamped levels.
        _tp1_opposing_zone = nearest_res if direction == "LONG" else nearest_sup
        _tp1_clamp_diag = _engine_b_clamp_tp1_to_opposing_zone(
            _exec_lvl,
            direction=direction,
            entry=current_price,
            opposing_zone=_tp1_opposing_zone,
            opposing_zone_distance=room_dist,
            atr=atr_val,
            min_rr=min_rr,
            exec_style=exec_style,
            asset_class=_exec_asset_class,
        )
        _engine_b_adapt_execution_plan_for_venue(
            _exec_lvl,
            direction=direction,
            entry=current_price,
            asset_type=asset_type_lower,
            exec_style=exec_style,
        )
        # Structural TP side check — kept for diagnostics / ENGINE_B_REASON_TP_WRONG_SIDE
        sl = _exec_lvl["execution_sl"]
        tp = _exec_lvl["execution_tp"]
        rr = _exec_lvl["rr_used_for_gate"]
        rr_ok = bool(_exec_lvl["execution_levels_valid"]) and rr >= min_rr

        try:
            from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

            if tp_sl_rr_gates_disabled(
                config.CONFIG,
                historical=bool(res.get("historical_mode")),
            ):
                rr_ok = _exec_lvl.get("execution_sl") is not None and _exec_lvl.get("execution_tp") is not None
        except Exception:
            pass
        if not bool(config.CONFIG.get("ENGINE_B_RR_GATE_ENABLED", True)):
            rr_ok = (
                _exec_lvl.get("execution_sl") is not None
                and _exec_lvl.get("execution_tp") is not None
            )

        # FIX 7: Apply contextual room gate after rr is computed
        _effective_min_room_atr = _get_min_room_atr(rr, bool(res.get("bos_confirmed")), asset_type_lower, exec_style)
        if config.CONFIG.get("ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE", True):
            if room_dist is None:
                # Open-air room: require BOS plus MTF trend alignment (default ON)
                # to avoid passing with a single TF trend + BOS into invisible liquidity.
                _h1_trend = (direction == "LONG" and h1_seq == "HH_HL") or (
                    direction == "SHORT" and h1_seq == "LH_LL"
                )
                _h4_trend = (direction == "LONG" and h4_seq == "HH_HL") or (
                    direction == "SHORT" and h4_seq == "LH_LL"
                )
                _bos_ok = bool(res.get("bos_confirmed"))
                _require_mtf = bool(
                    config.CONFIG.get("ENGINE_B_ROOM_NO_WALL_REQUIRE_MTF", True)
                )
                if _require_mtf:
                    room_ok = _h1_trend and _h4_trend and _bos_ok
                else:
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
            atr_val=trigger_atr_val,
            profile=profile,
        )
        research_entry_ok = bool(research_lab_detail.get("entry_ok"))
        research_location_ok = bool(research_lab_detail.get("location_ok"))
        if research_location_ok and not location_ok:
            location_ok = True
            location_mode = "research_lab"
        if trigger_timeframe_gate_ok and research_entry_ok and not trigger_ok:
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
        ) and trigger_timeframe_gate_ok
        # Follow-through trap: when ENABLED, a suspected liquidity trap blocks entry_ok.
        _ft_cfg_early = config.CONFIG.get("ENGINE_B_FOLLOW_THROUGH", {}) or {}
        _ft_block_entry = bool(_ft_cfg_early.get("ENABLED", False)) and bool(
            _ft_cfg_early.get("BLOCK_ENTRY_ON_TRAP", True)
        )
        _ft_trap_blocked = False
        if _ft_block_entry and entry_ok:
            _ft_bos_early = res.get("bos_data") or {}
            _ft_level_early = (
                _ft_bos_early.get("last_broken_high")
                if direction == "LONG"
                else _ft_bos_early.get("last_broken_low")
            )
            _ft_series_early = _follow_through_candle_series(entry_candles)
            _ft_idx_early = _find_breakout_bar_index(
                _ft_series_early, direction, _ft_level_early
            )
            if _ft_idx_early is not None:
                _ft_early = _breakout_follow_through(
                    _ft_series_early,
                    direction,
                    trigger_atr_val,
                    breakout_bar_index=_ft_idx_early,
                )
                if float(_ft_early.get("score") or 0.0) < 0.0:
                    entry_ok = False
                    _ft_trap_blocked = True

        # Accept both structural TPs and synthetic-fallback TPs as a "meaningful"
        # target that can substitute for room. Raw ATR TPs do not qualify
        # because they carry no structural justification.
        _level_mode_str = str(_exec_lvl.get("level_mode") or "")
        _rr_space_tp_qualified = _level_mode_str.endswith("_structural_tp") or _level_mode_str.endswith(
            "_fallback_rr_tp"
        )
        # TP1 path geometry: prove TP1 is reachable before the nearest opposing
        # zone. Runs on post-clamp levels — a TP1 re-targeted to the wall's
        # front edge by _engine_b_clamp_tp1_to_opposing_zone reads as clear;
        # only unclampable overshoots (RR floor / wrong side / bounds) stay
        # blocked here.
        _tp1_path_diag = _engine_b_tp1_path_clear(
            direction=direction,
            entry=current_price,
            tp1=_exec_lvl.get("execution_tp1"),
            opposing_zone=_tp1_opposing_zone,
            opposing_zone_distance=room_dist,
        )
        _tp1_path_clear = bool(_tp1_path_diag.get("tp1_path_clear"))
        # Dual-TP scale-out keeps the structural wall as TP1; space at entry
        # is satisfied by the reachable partial target, not by synthetic TP2.
        _scale_out_active = (
            _exec_lvl.get("exit_strategy") == "scale_out_structural_tp1_fallback_tp2"
            and _exec_lvl.get("scale_out_guard_reason") is None
            and _exec_lvl.get("execution_tp1") is not None
            and _exec_lvl.get("execution_rr1") is not None
        )
        _tp1_min_rr_for_space = _engine_b_style_threshold(
            config.CONFIG.get("ENGINE_B_TP1_MIN_RR"), exec_style
        )
        try:
            _exec_rr1_for_space = float(_exec_lvl.get("execution_rr1"))
        except (TypeError, ValueError):
            _exec_rr1_for_space = None
        _scale_out_space_ok = bool(
            _scale_out_active
            and _tp1_path_clear
            and _exec_rr1_for_space is not None
            and (
                _tp1_min_rr_for_space <= 0
                or _exec_rr1_for_space + 1e-12 >= _tp1_min_rr_for_space
            )
        )
        # A synthetic fallback TP that replaced a structural TP capped by a nearby
        # opposing zone must not substitute for room: the wall that made the
        # structural target fail min_rr is still in front of price, and the
        # synthetic target sits beyond it. Substitution stays valid for structural
        # TPs and for synthetic TPs created because no structural target existed
        # (missing/wrong-side TP — no opposing wall was mapped). Active scale-out
        # plans are exempt ONLY when TP1 has a clear path before the opposing
        # zone; a blocked TP1 must not rescue the room gate.
        _rr_space_capped_tp_blocked = False
        if (
            _rr_space_tp_qualified
            and _level_mode_str.endswith("_fallback_rr_tp")
            and _exec_lvl.get("fallback_tp_reason") == "structural_tp_below_min_rr"
            and (not _scale_out_active or not _tp1_path_clear)
            and bool(
                config.CONFIG.get(
                    "ENGINE_B_SPACE_RR_SUBSTITUTE_BLOCK_CAPPED_STRUCTURAL_TP", True
                )
            )
        ):
            _rr_space_tp_qualified = False
            _rr_space_capped_tp_blocked = True
        _rr_space_floor_enabled = bool(
            config.CONFIG.get("ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED", False)
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
        space_ok = room_ok or rr_can_satisfy_space or _scale_out_space_ok
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
        # A blocked TP1 fails the space gate on every path (room, substitution,
        # scale-out): plain room distance must not emit a trade whose TP1 sits
        # beyond the nearest opposing wall. _tp1_path_clear is True when no
        # wall/TP1 data exists (no-wall behaviour preserved) and after a
        # successful wall clamp, so this only blocks unclampable overshoots.
        # The capped-structural-TP block flag doubles as the legacy escape
        # hatch: disabling it restores pre-TP1-path space-gate semantics.
        _tp1_path_gate_enabled = bool(
            config.CONFIG.get("ENGINE_B_SPACE_RR_SUBSTITUTE_BLOCK_CAPPED_STRUCTURAL_TP", True)
        )
        space_gate_ok = (
            space_ok
            if rr_space_gate_enabled
            else (room_ok or _scale_out_space_ok)
        ) and (_tp1_path_clear or not _tp1_path_gate_enabled)

        try:
            from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

            if tp_sl_rr_gates_disabled(
                config.CONFIG,
                historical=bool(res.get("historical_mode")),
            ):
                space_gate_ok = True
                room_ok = True
        except Exception:
            pass
        if not bool(config.CONFIG.get("ENGINE_B_SPACE_GATE_ENABLED", True)):
            space_gate_ok = True

        # Stage 2.8: Optional volume confirmation gate.
        # Contributes to quality/bonus scoring but is NOT mandatory for pass.
        # Centralized traded volume is not available for spot FX; tick volume
        # must not become a synthetic zero that lowers forex quality.
        _bos_data = res.get("bos_data") if isinstance(res.get("bos_data"), dict) else {}
        _volume_scoring_applicable = asset_type_lower != "forex"
        volume_ok = bool(
            _volume_scoring_applicable
            and res.get("bos_confirmed")
            and res.get("bos_volume_confirmed", False)
        )

        gate_confirmations = [structure_ok, location_ok, entry_ok, space_gate_ok, rr_ok]
        if require_macro_align:
            gate_confirmations.append(macro_ok)
        confirmations = list(gate_confirmations)
        bonus_points = 0.0
        _quality_score = 0.0
        _quality_max_possible = 0.0
        _quality_components: dict[str, float] = {}
        _use_weighted_scoring = False
        try:
            from engine_b_quality import weighted_scoring_enabled

            _use_weighted_scoring = weighted_scoring_enabled()
        except Exception:
            _use_weighted_scoring = False

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
            _ft_series = _follow_through_candle_series(entry_candles)
            _ft_idx = _find_breakout_bar_index(_ft_series, direction, _ft_level)
            if _ft_idx is not None:
                _ft_result = _breakout_follow_through(
                    _ft_series, direction, trigger_atr_val, breakout_bar_index=_ft_idx
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
            _ft_detail["confirmed_only"] = bool(
                config.CONFIG.get("ENGINE_B_FOLLOW_THROUGH_CONFIRMED_ONLY", True)
            )
            _ft_max = abs(float(_ft_cfg.get("MAX_BONUS", 1.5)))
            _ft_min = -abs(float(_ft_cfg.get("MIN_PENALTY", -0.5)))
            _ft_bonus = max(_ft_min, min(_ft_max, _ft_result.get("score", 0.0)))
            if not _ft_enabled:
                _ft_bonus = 0.0
            _ft_detail["bonus_applied"] = round(_ft_bonus, 2)

        if not _use_weighted_scoring:
            # Legacy binary bonus confirmations — add to score but don't block if missing.
            if bos_mtf_aligned:
                bonus_points += 1.0
            if volume_ok:
                bonus_points += 1.0
            if ob_at_zone:
                bonus_points += 1.0
            if _ft_enabled:
                bonus_points += _ft_bonus

        # FIX 1: gate_score is COUNT of true booleans — never modify it
        gate_score = float(sum(1 for passed in gate_confirmations if passed))
        gate_max_possible = float(len(gate_confirmations)) if gate_confirmations else 1.0
        total_score = gate_score + bonus_points
        # FIX 4: Dynamic max_possible / weighted quality headroom
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

        if _use_weighted_scoring:
            try:
                from engine_b_quality import (
                    aggregate_quality_score,
                    apply_regime_component_weights,
                    compute_confluence_subscores,
                    normalize_followthrough_bonus,
                    weighted_scoring_config,
                )

                _ft_max_for_norm = (
                    abs(float(_ft_cfg.get("MAX_BONUS", 1.5)))
                    if (_ft_enabled or _ft_diagnostics_enabled)
                    else 0.0
                )
                _ft_norm = normalize_followthrough_bonus(_ft_bonus, _ft_max_for_norm)
                # Backtest point-in-time subsystems: pass the bar date so
                # carry/COT z-scores resolve as-of the bar, not as-of today.
                # Live keeps as_of=None (mem-cached current snapshot).
                _subsystem_as_of = None
                if bool(res.get("historical_mode")):
                    _bar_time = str(
                        res.get("latest_confirmed_trigger_candle_time") or ""
                    )
                    if len(_bar_time) >= 10:
                        _subsystem_as_of = _bar_time[:10]
                _subscores = compute_confluence_subscores(
                    res,
                    direction,
                    atr_val,
                    bos_followthrough_norm=_ft_norm,
                    volume_ok=volume_ok,
                    aggtrade_available=_aggtrade_available,
                    aggtrade_cvd_aligned=_aggtrade_cvd_aligned,
                    aggtrade_cvd_opposed=_aggtrade_cvd_opposed,
                    aggtrade_cvd_direction=_aggtrade_cvd_direction,
                    asset_type=asset_type_lower,
                    pair_display=res.get("pair_display"),
                    as_of_date=_subsystem_as_of,
                )
                _regime_label = str(res.get("_adx_derived_regime") or "").upper()
                _weighted_subscores = apply_regime_component_weights(
                    _subscores, _regime_label, asset_type_lower
                )
                _quality_score, _quality_max_possible, _quality_components = aggregate_quality_score(
                    _weighted_subscores, weighted_scoring_config()
                )
                bonus_points = _quality_score
                max_possible = gate_max_possible + _quality_max_possible
            except Exception as exc:
                log.debug("[ENGINE_B] weighted scoring fallback to legacy bonuses: %s", exc)
                _use_weighted_scoring = False
                bonus_points = 0.0
                if bos_mtf_aligned:
                    bonus_points += 1.0
                if volume_ok:
                    bonus_points += 1.0
                if ob_at_zone:
                    bonus_points += 1.0
                if _ft_enabled:
                    bonus_points += _ft_bonus
                _legacy_bonus_capacity = 2 + int(_volume_scoring_applicable)
                max_possible = (
                    gate_max_possible
                    + _legacy_bonus_capacity
                    + _profile_points_max
                    + _aggtrade_cvd_bonus_max
                )
                if _ft_enabled:
                    max_possible += abs(float(_ft_cfg.get("MAX_BONUS", 1.5)))
        else:
            bonus_count = (
                2
                + int(_volume_scoring_applicable)
                + _profile_points_max
                + _aggtrade_cvd_bonus_max
            )
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
                if not _use_weighted_scoring:
                    bonus_points += _profile_points
                    total_score += _profile_points
            elif _use_weighted_scoring:
                _profile_points = round(
                    float(_quality_components.get("profile_reaction", 0.0) or 0.0), 2
                )
                _profile_ok = _profile_points > 0
                _profile_alignment = "weighted"
        if not _use_weighted_scoring and _aggtrade_cvd_bonus_enabled:
            if _aggtrade_available and _aggtrade_cvd_aligned:
                _aggtrade_orderflow_points = _aggtrade_cvd_bonus_max
                _aggtrade_cvd_alignment = "aligned"
                bonus_points += _aggtrade_orderflow_points
                total_score += _aggtrade_orderflow_points
            elif _aggtrade_available and _aggtrade_cvd_opposed:
                _aggtrade_cvd_alignment = "opposed"
            elif _aggtrade_available and _aggtrade_cvd_direction:
                _aggtrade_cvd_alignment = "neutral"
        elif _use_weighted_scoring and asset_type_lower == "crypto":
            _aggtrade_orderflow_points = round(
                float(_quality_components.get("orderflow", 0.0) or 0.0), 2
            )
            if _aggtrade_available and _aggtrade_cvd_aligned:
                _aggtrade_cvd_alignment = "aligned"
            elif _aggtrade_available and _aggtrade_cvd_opposed:
                _aggtrade_cvd_alignment = "opposed"
            elif _aggtrade_available and _aggtrade_cvd_direction:
                _aggtrade_cvd_alignment = "neutral"
        # 2026-07-10 audit: the legacy bonus-scaling by ENGINE_B_REGIME_MULTIPLIERS
        # was removed — the table's min_score semantics (RANGING 1.10 = stricter
        # floor) inverted when applied to bonus points (RANGING 1.10 = inflated
        # score). Regime now influences weighted components only via
        # ENGINE_B_WEIGHTED_SCORING.REGIME_COMPONENT_MULT, and the min_score
        # floor only via ENGINE_B_REGIME_MULTIPLIERS_APPLY_TO_MIN_SCORE (opt-in).
        total_score = gate_score + bonus_points

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
        # Quality-only percentage (0-100, full range).
        #
        # `passed` requires every entry in `gate_confirmations`, so gate_score ==
        # gate_max_possible for EVERY passing signal. `pct` therefore starts at
        # gate_max/(gate_max+quality_max) — ~83% under weighted scoring — and the
        # whole discriminating range of an executable Engine B signal is the last
        # ~17 points. That is the gate_pct saturation problem in a softer form:
        # it made `pct` near-useless for ranking, saturated the A/B conviction
        # blend (B nominally carries 0.60 weight but ~0.50 of it was constant),
        # and left the style min_score floors below the guaranteed gate floor.
        # quality_pct measures only the earned quality layer, net of penalties,
        # so it spans 0-100 for passing signals and can actually rank them.
        _quality_denominator = (
            _quality_max_possible
            if _use_weighted_scoring
            else max(0.0, max_possible - gate_max_possible)
        )
        _quality_points_net = max(0.0, total_score - gate_score)
        quality_pct = (
            min(100.0, max(0.0, round((_quality_points_net / _quality_denominator) * 100.0, 1)))
            if _quality_denominator > 0
            else None
        )
        lifecycle_state, lifecycle_reason = self._determine_lifecycle_state(
            res, current_price, direction, trigger_ok
        )
        terminal_lifecycle = lifecycle_state in {"invalidated", "expired"}
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
        if terminal_lifecycle:
            passed = False
        if _aggtrade_hard_required and not _aggtrade_available:
            passed = False
        if not dxy_ok:
            passed = False

        failed_gate_names = []
        if not structure_ok:
            failed_gate_names.append("struct")
        if not location_ok:
            failed_gate_names.append("loc")
        if not entry_ok:
            failed_gate_names.append("trigger")
        if not trigger_timeframe_gate_ok:
            failed_gate_names.append(
                f"trigger_tf={actual_trigger_tf or 'missing'}(expected={expected_trigger_tf})"
            )
        if not space_gate_ok:
            failed_gate_names.append("space")
        if not rr_ok:
            failed_gate_names.append(f"rr={rr:.1f}(src={_exec_lvl['rr_source']})")
        if _exec_lvl.get("execution_level_reject_reason") == "max_sl_exceeded":
            failed_gate_names.append("max_sl_exceeded")
        if _aggtrade_hard_required and not _aggtrade_available:
            failed_gate_names.append(ENGINE_B_REASON_AGGTRADE_REQUIRED)
        if not dxy_ok:
            failed_gate_names.append(dxy_gate_reason or ENGINE_B_REASON_ADVERSE_DXY)
        if terminal_lifecycle:
            failed_gate_names.append(f"lifecycle_{lifecycle_state}")

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
        if not dxy_ok and dxy_gate_reason:
            _diag_codes.append(dxy_gate_reason)
        if not trigger_ok and not breakout_ok:
            _diag_codes.append(ENGINE_B_REASON_NO_TRIGGER_PATTERN)
        elif (
            breakout_ok
            and _volume_scoring_applicable
            and not res.get("bos_volume_confirmed", False)
        ):
            if _bos_data.get("bos_volume_status") == "below_threshold":
                _diag_codes.append(ENGINE_B_REASON_BOS_VOLUME_BELOW_THRESHOLD)
            else:
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
        if not _tp1_path_clear:
            _diag_codes.append(ENGINE_B_REASON_TP1_BLOCKED_BY_OPPOSING_ZONE)
        # Forex ADX now derives regime only; do not emit the old block-style
        # diagnostic because low ADX is no longer a structure gate failure.

        _engine_b_diag_payload = {"reason_codes": _diag_codes}
        if _bos_data:
            _engine_b_diag_payload["bos_volume"] = {
                "scoring_applicable": _volume_scoring_applicable,
                "available": _bos_data.get("bos_volume_available"),
                "status": _bos_data.get("bos_volume_status"),
                "bar_volume": _bos_data.get("bos_bar_volume"),
                "average_volume_20": _bos_data.get("bos_average_volume_20"),
                "ratio": _bos_data.get("bos_volume_ratio"),
                "threshold": _bos_data.get("bos_volume_threshold"),
                "confirmed": bool(res.get("bos_volume_confirmed", False)),
            }
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
            if not trigger_timeframe_gate_ok:
                _hard_fail_reasons.append("engine_b_trigger_timeframe_false")
            if terminal_lifecycle:
                _hard_fail_reasons.append(f"engine_b_lifecycle_{lifecycle_state}")
            _hard_fail_reasons = sorted(set(_hard_fail_reasons))

        _conf_result = {
            "score": total_score,
            "gate_score": gate_score,
            "pct": pct,
            "gate_pct": gate_pct,
            # Ranking / conviction basis — see quality_pct above. gate_pct is 100
            # for every pass and pct floors near 83%; only this field varies.
            "quality_pct": quality_pct,
            "quality_points_net": round(_quality_points_net, 4),
            "quality_denominator": round(_quality_denominator, 4),
            "max_possible": round(max_possible, 2),
            "gate_max_possible": round(gate_max_possible, 2),
            "bonus_points": round(bonus_points, 2),
            "quality_score": round(_quality_score, 2),
            "quality_max_possible": round(_quality_max_possible, 2),
            "quality_components": _quality_components,
            "weighted_scoring_enabled": bool(_use_weighted_scoring),
            "volume_scoring_applicable": _volume_scoring_applicable,
            "struct_points": 1.0 if structure_ok else 0.0,
            "rr_points": 1.0 if rr_ok else 0.0,
            "room_points": 1.0 if room_ok else 0.0,
            "catalyst_bonus": 1.0 if entry_ok else 0.0,
            "ai_adjustment": 0.0,
            "zone_points": 1.0 if location_ok else 0.0,
            "macro_points": 1.0 if macro_ok and require_macro_align else 0.0,
            "dxy_gate_enabled": _dxy_gate_enabled,
            "dxy_ok": dxy_ok,
            "dxy_gate_reason": dxy_gate_reason,
            "rr": round(rr, 2),
            "rr_used_for_gate": round(rr, 2),
            "rr_source": _exec_lvl["rr_source"],
            "rr_required": _exec_lvl.get("rr_required", min_rr),
            "rr_actual": _exec_lvl.get("rr_actual", round(rr, 2)),
            "rr_passed": _exec_lvl.get("rr_passed", rr_ok),
            "execution_plan": _exec_lvl.get("execution_plan"),
            "execution_plan_reason": _exec_lvl.get("execution_plan_reason"),
            "single_target_rr_floor": _exec_lvl.get("single_target_rr_floor"),
            "single_target_execution_tp": _exec_lvl.get("single_target_execution_tp"),
            "single_target_valid": _exec_lvl.get("single_target_valid"),
            "structural_sl": _exec_lvl["structural_sl"],
            "structural_tp": _exec_lvl["structural_tp"],
            "structural_rr": _exec_lvl["structural_rr"],
            "structural_sl_valid": _exec_lvl.get("structural_sl_valid"),
            "structural_tp_available": _exec_lvl.get("structural_tp_available"),
            # SL/TP geometry provenance — lets a backtest attribute stop width to
            # the pivot buffer, the ATR clamp, or the RR-tighten path, and shows
            # which timeframe's ATR the STYLE_ATR_MULTS multiple was applied to.
            "levels_atr_tf": res.get("levels_atr_tf"),
            "structural_sl_buffer_atr_mult": res.get("structural_sl_buffer_atr_mult"),
            "structural_sl_buffer_source": res.get("structural_sl_buffer_source"),
            "atr_sl_clamp_applied": _exec_lvl.get("atr_sl_clamp_applied"),
            "sl_tightened_for_rr": _exec_lvl.get("sl_tightened_for_rr"),
            "execution_sl": _exec_lvl["execution_sl"],
            "execution_tp": _exec_lvl["execution_tp"],
            "execution_tp1": _exec_lvl.get("execution_tp1"),
            "execution_tp2": _exec_lvl.get("execution_tp2"),
            "canonical_execution_plan": _exec_lvl.get("canonical_execution_plan"),
            "canonical_execution_sl": _exec_lvl.get("canonical_execution_sl"),
            "canonical_execution_tp": _exec_lvl.get("canonical_execution_tp"),
            "canonical_execution_tp1": _exec_lvl.get("canonical_execution_tp1"),
            "canonical_execution_tp2": _exec_lvl.get("canonical_execution_tp2"),
            "canonical_execution_rr": _exec_lvl.get("canonical_execution_rr"),
            "canonical_execution_rr1": _exec_lvl.get("canonical_execution_rr1"),
            "canonical_execution_rr2": _exec_lvl.get("canonical_execution_rr2"),
            "canonical_rr_required": _exec_lvl.get("canonical_rr_required"),
            "canonical_rr_passed": _exec_lvl.get("canonical_rr_passed"),
            "canonical_exit_strategy": _exec_lvl.get("canonical_exit_strategy"),
            "execution_rr": _exec_lvl["execution_rr"],
            "execution_rr1": _exec_lvl.get("execution_rr1"),
            "execution_rr2": _exec_lvl.get("execution_rr2"),
            "execution_sl_tighter_than_structural": _engine_b_exec_sl_tighter_than_structural(
                direction,
                _exec_lvl.get("structural_sl"),
                _exec_lvl.get("execution_sl"),
                _exec_lvl.get("structural_sl_valid"),
            ),
            "level_mode": _exec_lvl["level_mode"],
            "level_cohort": _exec_lvl.get("level_cohort", engine_b_level_cohort(_exec_lvl.get("level_mode"))),
            "sl_source": _exec_lvl.get("sl_source"),
            "tp_source": _exec_lvl.get("tp_source"),
            "tp1_source": _exec_lvl.get("tp1_source"),
            "tp2_source": _exec_lvl.get("tp2_source"),
            "exit_strategy": _exec_lvl.get("exit_strategy"),
            "runner_tp_requires_structural_break": _exec_lvl.get("runner_tp_requires_structural_break"),
            "scale_out_guard_reason": _exec_lvl.get("scale_out_guard_reason"),
            "tp1_sl_retry_applied": _exec_lvl.get("tp1_sl_retry_applied"),
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
            "scale_out_active": _scale_out_active,
            "scale_out_space_ok": _scale_out_space_ok,
            "tp1_path_clear": _tp1_path_clear,
            "tp1_path_diag": _tp1_path_diag,
            "tp1_clamped_to_opposing_zone": _tp1_clamp_diag.get("tp1_clamped_to_opposing_zone"),
            "tp1_clamp_reject_reason": _tp1_clamp_diag.get("tp1_clamp_reject_reason"),
            "entry_inside_opposing_zone": _tp1_path_diag.get("entry_inside_opposing_zone"),
            "tp1_before_opposing_zone": _tp1_path_diag.get("tp1_before_opposing_zone"),
            "tp1_path_block_reason": _tp1_path_diag.get("tp1_path_block_reason"),
            "tp1_min_rr": _tp1_min_rr_for_space,
            "style_min_rr": min_rr,
            "nearest_support_zone": res.get("nearest_support_zone"),
            "nearest_resistance_zone": res.get("nearest_resistance_zone"),
            "distance_to_res": res.get("distance_to_res"),
            "distance_to_sup": res.get("distance_to_sup"),
            "distance_to_res_pct": res.get("distance_to_res_pct"),
            "distance_to_sup_pct": res.get("distance_to_sup_pct"),
            "distance_to_res_atr": res.get("distance_to_res_atr"),
            "distance_to_sup_atr": res.get("distance_to_sup_atr"),
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
            "bos_volume_confirmed": bool(res.get("bos_volume_confirmed", False)),
            "bos_volume_available": _bos_data.get("bos_volume_available"),
            "bos_bar_volume": _bos_data.get("bos_bar_volume"),
            "bos_average_volume_20": _bos_data.get("bos_average_volume_20"),
            "bos_volume_ratio": _bos_data.get("bos_volume_ratio"),
            "bos_volume_threshold": _bos_data.get("bos_volume_threshold"),
            "bos_volume_status": _bos_data.get("bos_volume_status"),
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
            "trigger_timeframe_gate_required": trigger_timeframe_gate_required,
            "trigger_timeframe_gate_ok": trigger_timeframe_gate_ok,
            "m5_policy": res.get("m5_policy"),
            "m5_conditional_required": m5_conditional_required,
            "m5_prereq_tf": res.get("m5_prereq_tf"),
            "m5_setup_armed": m5_setup_armed,
            "trigger_timeframe_expected": expected_trigger_tf or None,
            "trigger_timeframe_actual": actual_trigger_tf or None,
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
            "follow_through_trap_blocked": _ft_trap_blocked,
            "engine_b_diagnostics": _engine_b_diag_payload,
            "_adx_derived_regime": res.get("_adx_derived_regime"),
        }
        try:
            from engine_b_canonical_actionability import attach_canonical_actionability

            attach_canonical_actionability(
                _conf_result,
                res,
                direction=direction,
                current_price=current_price,
                learning_ctx=learning_ctx,
                style_profile=style_profile,
                pair=res.get("display") or res.get("pair"),
            )
        except Exception as _canon_exc:
            # Fail closed for canonical routing fields — do not silently leave
            # passed=True without canonical_* diagnostics for UI/consumers.
            _conf_result["canonical_attach_error"] = type(_canon_exc).__name__
            _conf_result["canonical_trade_ok"] = False
            log.warning(
                "[ENGINE_B] attach_canonical_actionability failed: %s",
                _canon_exc,
            )
        return _conf_result

    def check_macro_correlation_detail(
        self, asset_close_series: list, dxy_close_series: list, direction: str,
        *,
        macro_required: bool = False,
    ) -> tuple[bool, str | None]:
        """Same gate as check_macro_correlation; returns optional block reason code when False."""
        _corr_window = int(config.CONFIG.get("ENGINE_B_MACRO_CORRELATION_WINDOW", 60))
        if len(asset_close_series) < _corr_window or len(dxy_close_series) < _corr_window:
            # Fail closed when macro is required for the group; otherwise allow.
            if macro_required and bool(
                config.CONFIG.get("ENGINE_B_MACRO_SHORT_HISTORY_FAIL_CLOSED", True)
            ):
                return False, "macro_history_insufficient"
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
        self,
        asset_close_series: list,
        dxy_close_series: list,
        direction: str,
        *,
        macro_required: bool = False,
    ) -> bool:
        """
        Calculates 30-period rolling Pearson correlation.
        Returns False (Block) if dynamically inversely correlated AND DXY moving against the trade.
        When macro_required and history is short, fails closed (config-gated).
        """
        ok, _reason = self.check_macro_correlation_detail(
            asset_close_series,
            dxy_close_series,
            direction,
            macro_required=macro_required,
        )
        return ok

    # simulate_trade was removed 2026-07-10 (audit): it had no callers and
    # diverged from the real backtest contract (always style="intraday",
    # live aggTrade mode on historical bars). The Engine B backtests call
    # precompute_structure_data + analyze_structure_direction directly.


# Singleton instance
engine = NakedEngine()
