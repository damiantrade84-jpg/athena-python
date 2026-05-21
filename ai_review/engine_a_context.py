"""Assemble server-trusted Engine A context for AI chart review."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ai_review.engine_snapshots import extract_engine_snapshots
from scoring import get_pair_score_group


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_candle_ts(candles: list | None) -> str | None:
    if not candles:
        return None
    last = candles[-1]
    if isinstance(last, dict):
        ts = last.get("time") or last.get("t")
        return str(ts) if ts else None
    return None


def _equity_session_block(factor_diag: dict[str, Any]) -> dict[str, Any]:
    raw = factor_diag.get("equity_session")
    if not isinstance(raw, dict):
        raw = {}
    return {
        "applied": bool(raw.get("enabled") or raw.get("applied")),
        "reason": raw.get("reason") or raw.get("session"),
        "utc_hour": raw.get("utc_hour"),
        "multiplier": _to_float(
            raw.get("multiplier") or factor_diag.get("equity_session_multiplier")
        ),
    }


def _multiplier_diagnostics(factor_diag: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "sessionMultiplier",
        "equity_session_multiplier",
        "volatilityRegimeMultiplier",
        "directionalRampMult",
        "vwapFilter",
        "volatilityScaler",
    )
    out: dict[str, Any] = {}
    for key in keys:
        if key in factor_diag:
            out[key] = factor_diag.get(key)
    feed = factor_diag.get("feed_status")
    if isinstance(feed, dict):
        out["feed_status"] = feed
    return out


def _directional_alignment(factor_diag: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "directionalScore",
        "directionalAlignment",
        "directionalRampMult",
        "minDirectionalThreshold",
        "effectiveMinDirectional",
    )
    return {k: factor_diag.get(k) for k in keys if k in factor_diag}


def _engine_a_passed(signal: dict[str, Any]) -> bool:
    direction = str(signal.get("direction") or "NONE").upper()
    score = _to_float(signal.get("confluenceScore"))
    threshold = _to_float(
        signal.get("threshold")
        or signal.get("liveThreshold")
        or signal.get("scanThresholdEffective")
        or signal.get("scanThreshold")
    )
    if direction not in ("LONG", "SHORT"):
        return False
    if score is None or threshold is None:
        return False
    return score >= threshold


def _default_resolve_pair(symbol: str) -> dict[str, Any] | None:
    from athena import ALL_PAIRS, CONFIG

    symbol = str(symbol or "").strip()
    pair_obj = next(
        (
            p
            for p in ALL_PAIRS
            if p.get("symbol") == symbol or p.get("display") == symbol
        ),
        None,
    )
    if pair_obj:
        return pair_obj
    return {
        "symbol": symbol,
        "display": symbol,
        "type": "crypto",
        "source": CONFIG.get("EXCHANGE_SOURCE", "binance"),
    }


def _default_btc_bias() -> str:
    try:
        from athena import _current_btc_bias

        return _current_btc_bias()
    except Exception:
        return "neutral"


def _default_analyze_pair(pair: dict[str, Any], btc_bias: str, style: str) -> dict[str, Any] | None:
    from scanner import analyze_pair

    return analyze_pair(pair, btc_bias, style=style)


def assemble_engine_a_context(
    symbol: str,
    timeframe: str,
    *,
    screenshot_meta: dict[str, Any] | None = None,
    resolve_pair_fn: Callable[[str], dict[str, Any] | None] | None = None,
    analyze_pair_fn: Callable[..., dict[str, Any] | None] | None = None,
    btc_bias_fn: Callable[[], str] | None = None,
) -> dict[str, Any] | None:
    resolve_pair = resolve_pair_fn or _default_resolve_pair
    analyze_pair = analyze_pair_fn or _default_analyze_pair
    btc_bias = (btc_bias_fn or _default_btc_bias)()

    pair = resolve_pair(symbol)
    if not pair:
        return None

    style = str((screenshot_meta or {}).get("chart_timeframe") or timeframe or "swing").lower()
    if style not in ("scalp", "intraday", "swing"):
        style = "swing"

    signal = analyze_pair(pair, btc_bias, style=style)
    if not signal:
        return None

    factor_diag = dict(signal.get("factorDiagnostics") or {})
    atr_diag = dict(signal.get("atrDiagnostics") or {})
    data_freshness = dict(signal.get("dataFreshness") or {})
    candle_meta = dict(signal.get("candleFetchMeta") or {})

    price = _to_float(signal.get("price"))
    sl = _to_float(signal.get("sl"))
    tp = _to_float(signal.get("tp1"))
    risk_points = abs(price - sl) if price is not None and sl is not None else None
    reward_points = abs(tp - price) if price is not None and tp is not None else None
    rr = _to_float(signal.get("rr1"))

    chart_provider = (screenshot_meta or {}).get("provider") or (screenshot_meta or {}).get("chart_provider")
    engine_provider = candle_meta.get("pairSource") or pair.get("source")
    provider_mismatch = bool(
        chart_provider and engine_provider and str(chart_provider) != str(engine_provider)
    )

    h1_ts = _last_candle_ts(signal.get("h1Candles"))
    h4_ts = _last_candle_ts(signal.get("h4Candles"))
    d1_ts = _last_candle_ts(signal.get("d1Candles"))
    latest_candle_ts = h1_ts or h4_ts or d1_ts

    scan_timestamp = signal.get("timestamp") or datetime.now(timezone.utc).isoformat()

    ctx = {
        "symbol": signal.get("symbol") or pair.get("symbol"),
        "timeframe": timeframe,
        "asset_class": pair.get("type"),
        "asset_group": get_pair_score_group(pair),
        "direction": str(signal.get("direction") or "NONE").upper(),
        "regime": signal.get("regime") or signal.get("regimeName"),
        "scan_timestamp": scan_timestamp,
        "candidate_timestamp": scan_timestamp,
        "latest_candle_ts": latest_candle_ts,
        "d1_candle_ts": d1_ts,
        "h4_candle_ts": h4_ts,
        "h1_candle_ts": h1_ts,
        "engine_a_provider": engine_provider,
        "chart_provider_hint": chart_provider,
        "provider_mismatch": provider_mismatch,
        "confluence_score": _to_float(signal.get("confluenceScore")),
        "max_score_override": _to_float(signal.get("maxScore")),
        "threshold": _to_float(
            signal.get("threshold")
            or signal.get("liveThreshold")
            or signal.get("scanThreshold")
        ),
        "passed": _engine_a_passed(signal),
        "factor_diagnostics": factor_diag,
        "multiplier_diagnostics": _multiplier_diagnostics(factor_diag),
        "equity_session": _equity_session_block(factor_diag),
        "session_diagnostics": {
            "session": signal.get("session"),
            "sessionMultiplier": factor_diag.get("sessionMultiplier"),
        },
        "directional_alignment": _directional_alignment(factor_diag),
        "atr": {
            "atr_value": _to_float(atr_diag.get("atr_value") or signal.get("atr")),
            "atr_tf": atr_diag.get("atr_tf"),
            "atr_source": atr_diag.get("atr_source"),
            "atr_candle_last_ts": atr_diag.get("atr_candle_last_ts"),
            "atr_age_seconds": _to_float(atr_diag.get("atr_age_seconds")),
            "atr_confirmed_only": atr_diag.get("atr_confirmed_only", True),
            "atr_cache_hit": (candle_meta.get("H4") or {}).get("cacheHit")
            if isinstance(candle_meta.get("H4"), dict)
            else None,
            "atr_freshness_status": None,
            "max_expected_age_seconds": None,
        },
        "geometry": {
            "candidate_entry": price,
            "current_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "risk_points": risk_points,
            "reward_points": reward_points,
            "rr": rr,
            "price_displacement_from_candidate_entry": 0.0 if price is not None else None,
            "sl_tp_source": signal.get("entryMode") or "engine_a_levels",
        },
        "freshness": {
            "cache_hit": data_freshness.get("cacheHit"),
            "bucket_lag": data_freshness.get("bucketLag"),
            "stale_warnings": data_freshness.get("blocked") or data_freshness.get("warnings") or [],
        },
        "chart_captured_at": (screenshot_meta or {}).get("captured_at"),
        "mismatch_warnings": [],
    }
    ctx["engine_snapshots"] = extract_engine_snapshots(signal, ctx)
    return ctx
