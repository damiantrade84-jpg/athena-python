"""ATR observability helpers (F2 audit output).

These pure helpers expose ATR provenance for Engine A / B / C / D signal
payloads. They are observability-only — they MUST NOT influence scoring,
SL/TP/RR, ATR multipliers, gates, or execution. The helpers live in their
own module so they can be unit-tested without loading the full ``athena.py``
Flask application (which has optional broker dependencies).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def normalize_style(style: str | None) -> str:
    """Match athena._normalize_style: lowercase + collapse auto -> swing."""
    raw = str(style or "swing").strip().lower()
    if raw in ("", "auto", "default"):
        return "swing"
    if raw in ("scalp", "intraday", "swing"):
        return raw
    # Treat unknown values as swing rather than failing — observability only.
    return "swing"


def resolve_atr_for_levels_with_tf(
    d1_snap: dict | None,
    h4_snap: dict | None,
    h1_snap: dict | None,
    pair_type: str | None,
    style: str | None,
    level_atr_priority: dict | None,
) -> tuple[float | None, str | None]:
    """Pure version of athena._atr_for_levels that also returns the winning TF.

    Mirrors the priority resolution logic exactly so ``athena.py`` can call this
    helper to derive both the ATR value AND the timeframe that produced it.
    """
    resolved_style = normalize_style(style)
    priority_cfg = level_atr_priority or {}
    style_map = priority_cfg.get(pair_type or "") or priority_cfg.get("default", {})
    order = style_map.get(resolved_style) if isinstance(style_map, dict) else None

    if not order:
        if resolved_style == "scalp":
            order = ["H1", "H4", "D1"]
        elif resolved_style == "intraday":
            order = ["H4", "H1", "D1"]
        elif (pair_type or "") == "crypto":
            order = ["H4", "D1", "H1"]
        else:
            order = ["D1", "H4", "H1"]

    snaps = {
        "D1": d1_snap or {},
        "H4": h4_snap or {},
        "H1": h1_snap or {},
    }
    for tf in order:
        atr_val = (snaps.get(tf) or {}).get("atr")
        if atr_val:
            return atr_val, tf
    return None, None


def derive_candle_age_seconds(candles: list | None) -> tuple[str | None, float | None]:
    """Return (iso_ts, age_seconds) for the last candle of a series.

    Accepts the OHLCV dicts produced by Athena's candle fetchers (with either
    ``time`` strings or epoch ints/floats in seconds or milliseconds). Returns
    ``(None, None)`` for empty / malformed input.
    """
    if not candles:
        return None, None
    last = candles[-1]
    if not isinstance(last, dict):
        return None, None
    ts_raw = last.get("time") or last.get("ts")
    if ts_raw is None:
        return None, None
    iso = str(ts_raw)
    age: float | None = None
    try:
        if isinstance(ts_raw, (int, float)):
            num = float(ts_raw)
            if num > 1e11:
                num = num / 1000.0
            last_dt = datetime.fromtimestamp(num, tz=timezone.utc)
        else:
            last_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last_dt).total_seconds()
    except Exception:
        age = None
    return iso, age


def build_engine_a_diagnostics(
    *,
    atr_value: float | None,
    atr_tf: str | None,
    atr_source: str | None,
    d1_candles: list | None,
    h4_candles: list | None,
    h1_candles: list | None,
) -> dict[str, Any]:
    """Build the Engine A ``atrDiagnostics`` block from the inputs that
    produced ``atr_value``.
    """
    series_by_tf = {
        "D1": d1_candles,
        "H4": h4_candles,
        "H1": h1_candles,
    }
    candles_for_tf = (
        series_by_tf.get(str(atr_tf or "").upper()) if atr_tf else None
    )
    iso, age = derive_candle_age_seconds(candles_for_tf)

    return {
        "atr_value": (
            round(float(atr_value), 6) if atr_value is not None else None
        ),
        "atr_tf": atr_tf,
        "atr_source": atr_source,
        "atr_source_engine": "engine_a",
        "atr_candle_last_ts": iso,
        "atr_age_seconds": round(float(age), 3) if age is not None else None,
        "atr_confirmed_only": True,
    }


def build_engine_b_diagnostics(
    *,
    atr_value: float | None,
    atr_tf: str | None,
    atr_source: str | None,
    atr_candles: list | None,
    bybit_atr_available: bool | None = None,
) -> dict[str, Any]:
    """Engine B ``atrDiagnostics`` for the structural ATR series."""
    iso, age = derive_candle_age_seconds(atr_candles)
    return {
        "atr_value": (
            round(float(atr_value), 6) if atr_value else None
        ),
        "atr_tf": atr_tf,
        "atr_source": atr_source,
        "atr_source_engine": "engine_b",
        "atr_candle_last_ts": iso,
        "atr_age_seconds": round(float(age), 3) if age is not None else None,
        "atr_confirmed_only": True,
        "bybit_atr_available": bybit_atr_available,
    }


def build_engine_c_diagnostics(
    *,
    atr_value: float | None,
    sig_a_diag: dict | None,
    sig_b_diag: dict | None,
    sl_method: str | None,
    tp_method: str | None,
) -> dict[str, Any]:
    """Engine C ``atrDiagnostics`` inherits provenance from the engine that
    produced the consensus ATR and records which method won."""
    a = sig_a_diag if isinstance(sig_a_diag, dict) else None
    b = sig_b_diag if isinstance(sig_b_diag, dict) else None
    return {
        "atr_value": (
            round(float(atr_value), 6) if atr_value is not None else None
        ),
        "atr_tf": (a or {}).get("atr_tf") if a else None,
        "atr_source": (a or {}).get("atr_source") if a else None,
        "atr_source_engine": "engine_c",
        "atr_candle_last_ts": (a or {}).get("atr_candle_last_ts") if a else None,
        "atr_age_seconds": (a or {}).get("atr_age_seconds") if a else None,
        "engine_a_atr_diagnostics": a,
        "engine_b_atr_diagnostics": b,
        "sl_method": sl_method,
        "tp_method": tp_method,
    }


DEFAULT_MAX_AGE_SECONDS: dict[str, float] = {
    "M1": 180.0,
    "M5": 600.0,
    "M15": 1800.0,
    "H1": 7200.0,
    "H4": 18000.0,
    "D1": 172800.0,
}


def _resolve_max_age(tf: str | None, overrides: dict | None) -> float | None:
    """Return the per-TF stale threshold in seconds, or None when not configured."""
    if not tf:
        return None
    key = str(tf).upper()
    if isinstance(overrides, dict):
        limit = overrides.get(key)
        if limit is None:
            return None
        try:
            return float(limit)
        except (TypeError, ValueError):
            return None
    return DEFAULT_MAX_AGE_SECONDS.get(key)


def evaluate_freshness(
    diagnostics: dict | None,
    *,
    enabled: bool,
    block_on_stale: bool,
    max_age_overrides: dict | None,
) -> dict[str, Any]:
    """Return a diagnostic-only freshness verdict for an ``atrDiagnostics`` block.

    The return shape is:
        ``{enabled, stale, reason, age_seconds, threshold_sec, would_block}``

    ``would_block`` is True only when ``block_on_stale`` is True AND the result
    is stale. Callers (risk_engine, executors) opt in to enforcement; observers
    just read ``stale`` / ``reason`` / ``threshold_sec``.
    """
    if not isinstance(diagnostics, dict):
        return {
            "enabled": bool(enabled),
            "stale": False,
            "reason": "no_atr_diagnostics",
            "age_seconds": None,
            "threshold_sec": None,
            "would_block": False,
        }
    tf = diagnostics.get("atr_tf")
    age = diagnostics.get("atr_age_seconds")
    threshold = _resolve_max_age(tf, max_age_overrides)
    if not enabled:
        return {
            "enabled": False,
            "stale": False,
            "reason": "freshness_disabled",
            "age_seconds": age,
            "threshold_sec": threshold,
            "would_block": False,
        }
    if diagnostics.get("atr_value") in (None, 0, 0.0):
        return {
            "enabled": True,
            "stale": True,
            "reason": "atr_value_missing",
            "age_seconds": age,
            "threshold_sec": threshold,
            "would_block": bool(block_on_stale),
        }
    if age is None:
        return {
            "enabled": True,
            "stale": True,
            "reason": "atr_age_unknown",
            "age_seconds": None,
            "threshold_sec": threshold,
            "would_block": bool(block_on_stale),
        }
    try:
        age_f = float(age)
    except (TypeError, ValueError):
        return {
            "enabled": True,
            "stale": True,
            "reason": "atr_age_invalid",
            "age_seconds": age,
            "threshold_sec": threshold,
            "would_block": bool(block_on_stale),
        }
    if threshold is None:
        return {
            "enabled": True,
            "stale": False,
            "reason": "no_threshold_for_tf",
            "age_seconds": age_f,
            "threshold_sec": None,
            "would_block": False,
        }
    stale = age_f > threshold
    return {
        "enabled": True,
        "stale": bool(stale),
        "reason": (
            f"atr_age_{int(age_f)}s_exceeds_{tf}_limit_{int(threshold)}s"
            if stale
            else "fresh"
        ),
        "age_seconds": age_f,
        "threshold_sec": threshold,
        "would_block": bool(stale and block_on_stale),
    }


def evaluate_freshness_from_config(
    diagnostics: dict | None,
    atr_freshness_cfg: dict | None,
) -> dict[str, Any]:
    """Convenience wrapper that reads CONFIG['ATR_FRESHNESS'] semantics.

    Expects the shape::

        {
            'ENABLED': bool,
            'BLOCK_EXECUTION_ON_STALE_ATR': bool,
            'MAX_AGE_SECONDS': {<TF>: <seconds>}
        }
    """
    cfg = atr_freshness_cfg or {}
    return evaluate_freshness(
        diagnostics,
        enabled=bool(cfg.get("ENABLED", False)),
        block_on_stale=bool(cfg.get("BLOCK_EXECUTION_ON_STALE_ATR", False)),
        max_age_overrides=(
            cfg.get("MAX_AGE_SECONDS")
            if isinstance(cfg.get("MAX_AGE_SECONDS"), dict)
            else None
        ),
    )


def build_engine_d_diagnostics(
    *,
    atr_value: float | None,
    m15_candles: list | None,
) -> dict[str, Any]:
    """Engine D ``atrDiagnostics`` for M15-derived scalp ATR."""
    iso, age = derive_candle_age_seconds(m15_candles)
    return {
        "atr_value": round(float(atr_value), 6) if atr_value else None,
        "atr_tf": "M15",
        "atr_source": "scalp_m15_candles",
        "atr_source_engine": "engine_d",
        "atr_candle_last_ts": iso,
        "atr_age_seconds": round(float(age), 3) if age is not None else None,
        "atr_confirmed_only": True,
    }
