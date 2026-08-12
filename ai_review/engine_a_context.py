"""Assemble server-trusted Engine A context for AI chart review."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from ai_review.engine_b_structure_contract import (
    describe_engine_b_structure_evidence,
    evaluate_tp_clears_resistance,
    has_engine_b_structure_evidence,
    resolve_nearest_levels,
)
from ai_review.engine_snapshots import extract_engine_snapshots
from ai_review.score_attribution import build_component_decomposition
from market_structure import build_engine_b_profile_vp_context, sanitize_engine_b_structure_profile_fields
from scoring import get_pair_score_group
from style_resolver import normalize_style, resolve_auto_style
from timeframe_policy import resolve_timeframe_policy


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _resolved_risk_geometry(
    engine_ctx: dict[str, Any],
    *,
    entry: Any,
    sl: Any,
    rr: Any,
    rr_required: Any = None,
) -> dict[str, Any]:
    """Project the same MAX_SL/RR geometry used by deterministic execution."""
    entry_f = _to_float(entry)
    sl_f = _to_float(sl)
    rr_f = _to_float(rr)
    required_f = _to_float(rr_required)
    max_sl_fraction = None
    max_sl_source = None
    try:
        from config import CONFIG
        from risk_engine import resolve_max_sl_pct

        signal_ctx = {
            "pair": engine_ctx.get("symbol"),
            "display": engine_ctx.get("symbol"),
            "symbol": engine_ctx.get("symbol"),
            "type": engine_ctx.get("asset_class"),
            "scoreGroup": engine_ctx.get("asset_group"),
        }
        max_sl_fraction, max_sl_source = resolve_max_sl_pct(
            signal_ctx,
            str(engine_ctx.get("asset_class") or ""),
            CONFIG,
        )
        if required_f is None:
            required_f = _to_float(CONFIG.get("ENGINE_C_EXEC_MIN_RR", 1.0))
    except Exception:
        pass

    sl_fraction = None
    if entry_f is not None and entry_f > 0 and sl_f is not None and sl_f > 0:
        sl_fraction = abs(entry_f - sl_f) / abs(entry_f)
    max_sl_passed = (
        sl_fraction <= float(max_sl_fraction) + 1e-12
        if sl_fraction is not None and max_sl_fraction is not None
        else None
    )
    rr_passed = (
        rr_f + 1e-12 >= required_f
        if rr_f is not None and required_f is not None
        else None
    )
    return {
        "slDistanceFraction": sl_fraction,
        "maxSlFraction": max_sl_fraction,
        "maxSlSource": max_sl_source,
        "maxSlPassed": max_sl_passed,
        "rr": rr_f,
        "rrRequired": required_f,
        "rrPassed": rr_passed,
    }


def _last_candle_ts(candles: list | None) -> str | None:
    if not candles:
        return None
    last = candles[-1]
    if isinstance(last, dict):
        ts = last.get("time") or last.get("t")
        return str(ts) if ts else None
    return None


def _last_candle_value(candles: list | None, key: str) -> float | None:
    if not candles:
        return None
    last = candles[-1]
    if not isinstance(last, dict):
        return None
    try:
        value = last.get(key)
        return float(value) if value is not None else None
    except (TypeError, ValueError):
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


def _funding_oi_block(signal: dict[str, Any]) -> dict[str, Any]:
    oi = signal.get("oiData") or signal.get("oi_data") or {}
    if not isinstance(oi, dict):
        oi = {}
    oi_ctx = signal.get("oiContext") or signal.get("oi_context") or {}
    if not isinstance(oi_ctx, dict):
        oi_ctx = {}
    return {
        "fundingRate": _first_present(signal.get("fundingRate"), signal.get("funding_rate")),
        "fundingRateZ": _first_present(signal.get("fundingRateZ"), signal.get("funding_rate_z")),
        "openInterest": _first_present(
            oi.get("oi"), oi.get("openInterest"), oi.get("open_interest")
        ),
        "openInterestDelta": _first_present(oi.get("oiDelta"), oi.get("openInterestDelta")),
        "openInterestDeltaPct": _first_present(
            oi.get("oiChange"),
            oi.get("openInterestDeltaPct"),
            oi_ctx.get("oi_change_pct"),
        ),
        "source": oi.get("source"),
        "timestamp": _first_present(oi.get("timestamp"), oi.get("ts")),
    }


def _asset_group_from(signal: dict[str, Any], engine_a_ctx: dict[str, Any]) -> str:
    return str(
        engine_a_ctx.get("asset_group")
        or engine_a_ctx.get("asset_class")
        or signal.get("scoreGroup")
        or signal.get("type")
        or ""
    ).lower()


def _volume_type_for(asset_class: str) -> str:
    ac = str(asset_class or "").lower()
    if ac in ("crypto", "stock", "index", "etf"):
        return "real"
    if ac == "forex":
        return "tick"
    return "mixed"


def _feed_status(factor_diag: dict[str, Any]) -> dict[str, Any]:
    feed = (
        factor_diag.get("feedStatus")
        or factor_diag.get("feed_status")
        or factor_diag.get("feed_statuses")
        or {}
    )
    return feed if isinstance(feed, dict) else {}


def _addon_context(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    feed = _feed_status(factor_diag)
    addon_type = _first_present(
        factor_diag.get("addon_type"),
        factor_diag.get("addonType"),
        signal.get("addon_type"),
        signal.get("addonType"),
    )
    addon_value = _to_float(
        _first_present(
            factor_diag.get("addon_value"),
            factor_diag.get("addonValue"),
            signal.get("addon_value"),
            signal.get("addonValue"),
            _factor_score(factor_diag, "addon"),
        )
    )
    addon_status = _first_present(
        factor_diag.get("addon_status"),
        factor_diag.get("addonStatus"),
        feed.get("addon"),
    )
    asset_group = _asset_group_from(signal, engine_a_ctx)
    applies = None
    if addon_type:
        at = str(addon_type).lower()
        if at in {"funding", "funding+oi"}:
            applies = asset_group.startswith("crypto")
        elif at == "carry":
            applies = asset_group == "forex"
        elif at in {"cot", "cot_proxy"}:
            applies = asset_group in {"commodity", "commodities", "stock", "stocks", "index", "indices", "etf"}
    unsupported = bool(
        factor_diag.get("addon_unsupported")
        or factor_diag.get("addonUnsupported")
        or str(addon_status or "").lower().endswith(":unsupported")
        or str(addon_status or "").lower() == "unsupported"
    )
    interpretation = "unavailable"
    if unsupported:
        interpretation = "unsupported_for_asset_or_feed"
    elif addon_value is None:
        interpretation = "missing"
    elif addon_value > 0:
        interpretation = "supportive"
    elif addon_value < 0:
        interpretation = "contradictory"
    else:
        interpretation = "neutral"
    return {
        "addonType": addon_type,
        "addonValue": addon_value,
        "addonStatus": addon_status,
        "addonUnsupported": unsupported,
        "feedStatus": feed,
        "appliesToAssetClass": applies,
        "interpretation": interpretation,
    }


def _derivatives_context(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    asset_group = _asset_group_from(signal, engine_a_ctx)
    raw = engine_a_ctx.get("funding_oi") if isinstance(engine_a_ctx.get("funding_oi"), dict) else None
    if raw is None:
        raw = _funding_oi_block(signal)
    has_value = any(
        _first_present(
            raw.get(key),
            raw.get(key[0].lower() + key[1:]) if isinstance(key, str) and key else None,
        )
        is not None
        for key in (
            "fundingRate",
            "fundingRateZ",
            "openInterest",
            "openInterestDelta",
            "openInterestDeltaPct",
        )
    )
    if not asset_group.startswith("crypto"):
        status = "not_applicable"
    elif has_value:
        status = "ok"
    else:
        status = "unavailable"
    return {
        "fundingRate": _to_float(_first_present(raw.get("fundingRate"), raw.get("funding_rate"))),
        "fundingRateZ": _to_float(_first_present(raw.get("fundingRateZ"), raw.get("funding_rate_z"))),
        "openInterest": _to_float(
            _first_present(raw.get("openInterest"), raw.get("open_interest"), raw.get("oi"))
        ),
        "openInterestDelta": _to_float(
            _first_present(raw.get("openInterestDelta"), raw.get("open_interest_delta"))
        ),
        "openInterestDeltaPct": _to_float(
            _first_present(
                raw.get("openInterestDeltaPct"),
                raw.get("open_interest_delta_pct"),
                raw.get("oiChange"),
                raw.get("oi_change_pct"),
            )
        ),
        "source": raw.get("source"),
        "timestamp": _first_present(raw.get("timestamp"), raw.get("ts"), raw.get("time")),
        "status": status,
    }


def _snap_from_signal_or_ctx(signal: dict[str, Any], engine_a_ctx: dict[str, Any], key: str) -> dict[str, Any]:
    for container in (signal, engine_a_ctx):
        block = container.get(key)
        if isinstance(block, dict):
            snap = block.get("snap")
            if isinstance(snap, dict):
                return snap
            return block
    return {}


def _microstructure_context(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    asset_group = _asset_group_from(signal, engine_a_ctx)
    snap = _snap_from_signal_or_ctx(signal, engine_a_ctx, "h4")
    source = _first_present(
        snap.get("microstructure_exchange"),
        signal.get("microstructure_exchange"),
        signal.get("microstructureSource"),
    )
    values = {
        "orderBookImbalance": _to_float(
            _first_present(snap.get("order_book_imbalance"), snap.get("orderBookImbalance"))
        ),
        "liquidityWallDetection": _to_float(
            _first_present(snap.get("liquidity_wall_detection"), snap.get("liquidityWallDetection"))
        ),
        "orderflowDelta": _to_float(
            _first_present(snap.get("orderflow_delta"), snap.get("orderflowDelta"))
        ),
        "liquidityPressure": _to_float(
            _first_present(snap.get("liquidity_pressure"), snap.get("liquidityPressure"))
        ),
        "volumeMomentumSpread": _to_float(
            _first_present(snap.get("volume_momentum_spread"), snap.get("volumeMomentumSpread"))
        ),
    }
    if not asset_group.startswith("crypto"):
        status = "not_applicable"
    elif any(v is not None for v in values.values()):
        status = "ok"
    else:
        status = "unavailable"
    return {
        **values,
        "source": source,
        "ageSeconds": _to_float(
            _first_present(snap.get("microstructure_age_sec"), snap.get("microstructureAgeSeconds"))
        ),
        "status": status,
    }


def _intermarket_context(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    raw = (
        signal.get("intermarketConfirmation")
        or factor_diag.get("intermarket")
        or factor_diag.get("intermarketConfirmation")
        or engine_a_ctx.get("intermarketConfirmation")
        or {}
    )
    if not isinstance(raw, dict):
        raw = {}
    flags = raw.get("contradictionFlags") if isinstance(raw.get("contradictionFlags"), dict) else {}
    severe = raw.get("severeContradiction")
    if severe is None:
        severe = flags.get("severeContradiction")
    return {
        "verdict": raw.get("verdict"),
        "score": _to_float(raw.get("score")),
        "engineADelta": _to_float(
            _first_present(
                raw.get("engineADelta"),
                raw.get("engine_a_delta"),
                factor_diag.get("intermarket_engine_a_delta"),
            )
        ),
        "supportDirection": raw.get("supportDirection"),
        "supportStrength": raw.get("supportStrength"),
        "stable": raw.get("stable") if raw.get("stable") is None else bool(raw.get("stable")),
        "flippedRecently": bool(raw.get("flippedRecently")),
        "activeWindow": raw.get("activeWindow"),
        "topSupporting": list(raw.get("topSupporting") or []),
        "topContradictory": list(raw.get("topContradictory") or []),
        "unavailablePriors": list(raw.get("unavailablePriors") or []),
        "severeContradiction": bool(severe),
        "explanation": raw.get("explanation"),
    }


def _news_context(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    summary = signal.get("newsSentimentSummary") or engine_a_ctx.get("newsSentimentSummary") or {}
    if not isinstance(summary, dict):
        summary = {}
    risk = signal.get("majorEventRisk") or summary.get("majorEventRisk") or {}
    if not isinstance(risk, dict):
        risk = {}
    return {
        "vote": _to_float(signal.get("newsSentimentVote") or engine_a_ctx.get("newsSentimentVote")),
        "sentimentScore": _to_float(
            _first_present(summary.get("sentiment_score"), summary.get("sentimentScore"))
        ),
        "confidence": _to_float(summary.get("confidence")),
        "direction": summary.get("direction"),
        "delta": _to_float(signal.get("newsSentimentDelta") or engine_a_ctx.get("newsSentimentDelta")),
        "rawDelta": _to_float(signal.get("newsSentimentRawDelta") or engine_a_ctx.get("newsSentimentRawDelta")),
        "articleCountUsed": summary.get("article_count_used") or summary.get("articleCountUsed"),
        "freshArticleCount": summary.get("fresh_article_count") or summary.get("freshArticleCount"),
        "majorEventDetected": bool(
            _first_present(
                summary.get("major_event_detected"),
                summary.get("majorEventDetected"),
                risk.get("majorEventDetected"),
            )
        ),
        "majorEventDescription": _first_present(
            summary.get("major_event_description"),
            summary.get("majorEventDescription"),
            risk.get("reason"),
        ),
        "keyThemes": list(summary.get("key_themes") or summary.get("keyThemes") or [])[:6],
        "reasoningSummary": summary.get("reasoning_summary") or summary.get("reasoningSummary"),
    }


def _macro_context(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    macro = factor_diag.get("macroContext") or factor_diag.get("macro_context") or {}
    if not isinstance(macro, dict):
        macro = {"value": macro} if macro not in (None, "") else {}
    usd = signal.get("usdRelativeStrength") or engine_a_ctx.get("usdRelativeStrength")
    if not isinstance(usd, dict):
        usd = {}
    intermarket = _intermarket_context(signal, factor_diag, engine_a_ctx)
    unavailable = intermarket.get("unavailablePriors") or []
    real_yield_unavailable = any(
        (
            item.get("driver") if isinstance(item, dict) else str(item)
        )
        == "US10Y_REAL_YIELD_PROXY"
        for item in unavailable
    )
    return {
        "macroContext": macro,
        "usdRelativeStrength": usd or None,
        "dxyStatus": macro.get("status") if str(macro.get("proxyLabel") or "").upper() == "DXY" else None,
        "realYieldStatus": "unavailable" if real_yield_unavailable else None,
    }


def build_score_attribution(
    signal: dict[str, Any],
    *,
    factor_diagnostics: dict[str, Any] | None = None,
    threshold: float | None = None,
    max_score: float | None = None,
) -> dict[str, Any]:
    """Build unified Engine A score attribution for scan payloads and AI review."""
    fd = factor_diagnostics if isinstance(factor_diagnostics, dict) else {}
    if not fd:
        raw_fd = signal.get("factorDiagnostics") or signal.get("factor_diagnostics")
        fd = raw_fd if isinstance(raw_fd, dict) else {}
    ctx = {
        "confluence_score": _to_float(
            signal.get("confluenceScore") or signal.get("score") or signal.get("final_score")
        ),
        "threshold": _to_float(threshold if threshold is not None else signal.get("threshold")),
        "max_score_override": _to_float(
            max_score if max_score is not None else signal.get("maxScore") or signal.get("maxScoreOverride")
        ),
    }
    return _score_attribution(signal, fd, ctx)


def _score_attribution(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    final_score = _to_float(
        _first_present(
            engine_a_ctx.get("confluence_score"),
            signal.get("confluenceScore"),
            signal.get("score"),
            signal.get("final_score"),
        )
    )
    intermarket = _intermarket_context(signal, factor_diag, engine_a_ctx)
    inter_delta = _to_float(intermarket.get("engineADelta")) or 0.0
    news_delta = _to_float(
        _first_present(signal.get("newsSentimentDelta"), engine_a_ctx.get("newsSentimentDelta"))
    )
    score_after_intermarket = _to_float(
        _first_present(
            signal.get("preNewsScore"),
            signal.get("pre_news_score"),
            engine_a_ctx.get("preNewsScore"),
            engine_a_ctx.get("pre_news_score"),
        )
    )
    if score_after_intermarket is None and final_score is not None and news_delta is not None:
        score_after_intermarket = round(final_score - news_delta, 6)
    technical_raw = None
    if score_after_intermarket is not None:
        technical_raw = round(float(score_after_intermarket) - float(inter_delta), 6)
    elif final_score is not None:
        technical_raw = round(float(final_score) - float(inter_delta) - float(news_delta or 0.0), 6)
    return {
        "technicalScoreRaw": technical_raw,
        "intermarketDelta": round(float(inter_delta), 6),
        "scoreAfterIntermarket": score_after_intermarket,
        "newsSentimentDelta": news_delta,
        "finalEngineAScore": final_score,
        "threshold": _to_float(engine_a_ctx.get("threshold") or signal.get("threshold")),
        "maxScore": _to_float(
            _first_present(engine_a_ctx.get("max_score_override"), signal.get("maxScore"), signal.get("maxScoreOverride"))
        ),
        "scoreSource": "backend_engine_a",
        "aiReviewCanMutateScore": False,
        # P2-6: all components, plus the residual the multiplicative formula
        # leaves unattributed, plus what "contribution" actually means.
        "componentDecomposition": build_component_decomposition(
            factor_diag,
            raw_score=technical_raw if technical_raw is not None else final_score,
        ),
    }


def _non_visual_context(
    signal: dict[str, Any],
    factor_diag: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    return {
        "addonContext": _addon_context(signal, factor_diag, engine_a_ctx),
        "derivativesContext": _derivatives_context(signal, factor_diag, engine_a_ctx),
        "microstructureContext": _microstructure_context(signal, factor_diag, engine_a_ctx),
        "intermarketContext": _intermarket_context(signal, factor_diag, engine_a_ctx),
        "newsContext": _news_context(signal, factor_diag, engine_a_ctx),
        "macroContext": _macro_context(signal, factor_diag, engine_a_ctx),
    }


def _ema_levels(signal: dict[str, Any], factor_diag: dict[str, Any]) -> dict[str, Any]:
    h4 = signal.get("h4")
    h4_snap = h4.get("snap") if isinstance(h4, dict) else {}
    if not isinstance(h4_snap, dict):
        h4_snap = {}
    trend = factor_diag.get("trendCoherence") or factor_diag.get("trend_coherence") or {}
    if not isinstance(trend, dict):
        trend = {}
    return {
        # "ema21" is the legacy snap key for the trend EMA; under per-group
        # calibration it holds the group's trend period (e.g. 18 crypto, 26 forex).
        "ema21": h4_snap.get("ema21") or trend.get("ema21") or trend.get("ema21_value"),
        "ema50": h4_snap.get("ema50") or trend.get("ema50") or trend.get("ema50_value"),
        "ema200": h4_snap.get("ema200") or trend.get("ema200") or trend.get("ema200_value"),
        "dema200": h4_snap.get("dema200") or trend.get("dema200") or trend.get("dema200_value"),
    }


def _engine_a_passed_basis(signal: dict[str, Any]) -> str:
    """Provenance for ``passed``: v3-contract signals gate on
    decision/qualified/freshness (a structural setup can promote WATCH -> TRADE
    below the headline confluence threshold); legacy signals gate on
    score >= threshold."""
    if (
        str(signal.get("engine") or "").upper() == "ENGINE_A_V3"
        and str(signal.get("contractVersion") or "").startswith("3.")
    ):
        return "engine_a_v3_decision"
    return "score_threshold"


def _engine_a_passed(signal: dict[str, Any]) -> bool:
    if _engine_a_passed_basis(signal) == "engine_a_v3_decision":
        freshness = signal.get("dataFreshness")
        return bool(
            signal.get("decision") == "TRADE"
            and signal.get("qualified") is True
            and signal.get("engineATradeEnabled") is True
            and isinstance(freshness, dict)
            and freshness.get("allowed") is True
        )
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


def _pair_lookup_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().upper()
    if not raw:
        return ""
    without_provider = raw.split(":")[-1]
    without_yahoo_fx_suffix = without_provider.replace("=X", "")
    return re.sub(r"[^A-Z0-9]", "", without_yahoo_fx_suffix)


def _default_resolve_pair(symbol: str) -> dict[str, Any] | None:
    from athena import ALL_PAIRS

    symbol = str(symbol or "").strip()
    pair_obj = next(
        (
            p
            for p in ALL_PAIRS
            if p.get("symbol") == symbol or p.get("display") == symbol
        ),
        None,
    )
    if not pair_obj:
        requested_key = _pair_lookup_key(symbol)
        if requested_key:
            pair_obj = next(
                (
                    p
                    for p in ALL_PAIRS
                    if requested_key
                    in {
                        _pair_lookup_key(p.get("symbol")),
                        _pair_lookup_key(p.get("display")),
                    }
                ),
                None,
            )
    return pair_obj


def _default_btc_bias() -> str:
    try:
        from athena import _current_btc_bias

        return _current_btc_bias()
    except Exception:
        return "neutral"


def _default_analyze_pair(
    pair: dict[str, Any],
    btc_bias: str,
    style: str,
    **kwargs: Any,
) -> dict[str, Any] | None:
    from scanner import analyze_pair

    # Capture candle fetch metadata from candles_cache before analyze_pair's own
    # fetch overwrites it, so AI-review payloads can surface cacheHit when the
    # cache already holds the bars. Diagnostics only — does not affect scoring.
    if "preloaded_fetch_meta" not in kwargs:
        from candles_cache import get_candle_fetch_meta
        from config import scan_candle_limits

        _lim = scan_candle_limits()
        kwargs["preloaded_fetch_meta"] = {
            tf: get_candle_fetch_meta(pair, tf, _lim[tf])
            for tf in ("D1", "H4", "H1")
        }

    return analyze_pair(
        pair,
        btc_bias,
        style=style,
        **kwargs,
    )


def resolve_chart_review_analyze_style(
    screenshot_meta: dict[str, Any] | None,
    pair: dict[str, Any] | None,
    candidate_signal: dict[str, Any] | None = None,
) -> str:
    """Resolve review style without treating the visible chart TF as trade style."""
    candidate = candidate_signal or {}
    for key in ("horizon", "style", "selectedStyle", "resolvedStyle"):
        explicit = normalize_style(candidate.get(key))
        if explicit != "auto":
            return explicit

    meta = screenshot_meta or {}
    for key in (
        "analyze_style",
        "signal_style",
        "scoring_style",
        "resolvedStyle",
        "style",
        "requestedStyle",
        "horizon",
        "tradeStyle",
    ):
        explicit = normalize_style(meta.get(key))
        if explicit != "auto":
            return explicit

    pair_data = pair or {}
    score_group = pair_data.get("scoreGroup") or pair_data.get("score_group")
    if not score_group and any(
        pair_data.get(key) for key in ("symbol", "display", "pair")
    ):
        try:
            score_group = get_pair_score_group(pair_data)
        except Exception:
            score_group = None
    return resolve_auto_style(
        "auto",
        pair_data,
        score_group=score_group,
        asset_type=pair_data.get("type") or pair_data.get("asset_type"),
    )


def _merge_factor_diagnostics(
    factor_diag: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(factor_diag)
    factor_scores = signal.get("factorScores") or signal.get("factor_scores")
    if isinstance(factor_scores, dict) and factor_scores:
        merged["factorScores"] = dict(factor_scores)
    return merged


def _rsi_from_signal(signal: dict[str, Any]) -> tuple[float | None, str | None]:
    """Return (rsi, timeframe) from the first TF snapshot that carries RSI."""
    for key in ("h4", "h1", "d1"):
        block = signal.get(key)
        if not isinstance(block, dict):
            continue
        snap = block.get("snap")
        if not isinstance(snap, dict):
            continue
        rsi = _to_float(snap.get("rsi"))
        if rsi is not None:
            return rsi, key.upper()
    return None, None


def _intermarket_block(signal: dict[str, Any], factor_diag: dict[str, Any]) -> dict[str, Any]:
    ic = signal.get("intermarketConfirmation")
    if isinstance(ic, dict) and ic:
        return {
            "verdict": ic.get("verdict"),
            "delta": _to_float(ic.get("engineADelta")),
            "correlations": ic.get("correlations"),
            "divergence": ic.get("divergence"),
        }
    fd_im = factor_diag.get("intermarket")
    if isinstance(fd_im, dict) and fd_im:
        return {
            "verdict": fd_im.get("verdict"),
            "delta": _to_float(fd_im.get("engineADelta")),
            "correlations": fd_im.get("correlations"),
            "divergence": fd_im.get("divergence"),
        }
    return {}


def _news_sentiment_block(signal: dict[str, Any]) -> dict[str, Any]:
    vote = signal.get("newsSentimentVote")
    delta = _to_float(signal.get("newsSentimentDelta"))
    summary = signal.get("newsSentimentSummary")
    if vote is None and delta is None and summary is None:
        return {}
    block: dict[str, Any] = {}
    if vote is not None:
        block["vote"] = vote
    if delta is not None:
        block["delta"] = delta
    if isinstance(summary, dict):
        for key in (
            "direction", "confidence", "sentiment_score",
            "article_count_used", "key_themes",
            "major_event_detected", "major_event_description",
            "reasoning_summary",
        ):
            val = summary.get(key)
            if val is not None:
                block[key] = val
    return block


def _engine_b_structure_evidence(payload: Any) -> bool:
    """True when a nested Engine B payload has real structure evidence.

    Delegates to the shared contract so this predicate cannot drift away from
    the overlay renderer again (P1-5).
    """
    return has_engine_b_structure_evidence(payload)


def _pick_engine_b_structure_payload(
    signal: dict[str, Any] | None,
    origin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort Engine B structure for Engine A chart-review context.

    Engine A scan rows often omit a full nested engine_b block. Prefer any
    real structure payload on the live re-analysis signal, then the origin
    candidate (scan/UI card). Empty dict is fine — diagnostics treat B
    structure as optional for Engine A-primary reviews.
    """
    nested_keys = (
        "engine_b",
        "naked_data",
        "engineB",
        "engine_b_result",
        "engineBResult",
    )
    for src in (signal, origin):
        if not isinstance(src, dict):
            continue
        for key in nested_keys:
            val = src.get(key)
            if _engine_b_structure_evidence(val):
                return dict(val)
        # Origin/scan row may itself be Engine B-shaped.
        if _engine_b_structure_evidence(src) and (
            src.get("is_naked")
            or str(src.get("engine") or "").upper() in {"B", "ENGINE_B", "NAKED"}
            or src.get("structural_verdict")
        ):
            return dict(src)
    # Last resort: return nested dict even if sparse so VP sanitize can stamp context.
    for src in (signal, origin):
        if not isinstance(src, dict):
            continue
        for key in nested_keys:
            val = src.get(key)
            if isinstance(val, dict) and val:
                return dict(val)
    return {}


def _build_structure_context(
    signal: dict[str, Any] | None,
    origin: dict[str, Any] | None,
    *,
    pair: dict[str, Any],
    structure_refetch: dict[str, Any] | None = None,
    sl: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
) -> dict[str, Any]:
    """Engine B structure for the review panel, with levels flattened.

    P1-5 populates ``nearest_support`` / ``nearest_resistance`` (previously
    ``n/a`` because only the zone dicts travelled) and records why a structure
    refetch failed instead of leaving the gap unexplained. P1-6 then evaluates
    ``tp_clears_resistance`` for TP1 and TP2 independently.
    """
    struct = sanitize_engine_b_structure_profile_fields(
        _pick_engine_b_structure_payload(signal, origin),
        str(pair.get("type") or ""),
    )
    if not isinstance(struct, dict):
        struct = {}

    evidence = describe_engine_b_structure_evidence(struct)
    struct["structure_evidence"] = evidence
    struct["structure_refetch"] = structure_refetch or {"attempted": False, "ok": None}

    levels = resolve_nearest_levels(struct)
    struct["nearest_support"] = levels["nearest_support"]
    struct["nearest_resistance"] = levels["nearest_resistance"]

    direction = str(
        (origin or {}).get("direction") or (signal or {}).get("direction") or ""
    ).upper()
    targets = {"tp1": tp1, "tp2": tp2}
    struct["tp_clears_resistance"] = evaluate_tp_clears_resistance(
        direction=direction,
        entry=_to_float((signal or {}).get("price")),
        targets=targets,
        structure=struct,
    )

    # P1-4: without structural levels the review layer must not narrate
    # structural claims about SL/TP placement.
    struct["structural_levels_available"] = bool(evidence.get("present"))
    struct["suppress_structural_language"] = not evidence.get("present")
    if sl is not None:
        struct["sl_reference"] = sl
    return struct


def select_ohlcv_bars_for_chart(
    signal: dict[str, Any],
    timeframe: str,
    screenshot_meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Pick candle series aligned to the chart timeframe for strategy facts."""
    tf = str((screenshot_meta or {}).get("chart_timeframe") or timeframe or "H4").upper()
    if tf in ("M1", "M2", "M3", "M5", "M15"):
        raw = (
            signal.get("m1Candles")
            or signal.get("m5Candles")
            or signal.get("m15Candles")
            or signal.get("h1Candles")
        )
    elif tf in ("M30", "H1", "H2", "H3"):
        raw = (
            signal.get("m30Candles")
            or signal.get("h1Candles")
            or signal.get("h4Candles")
        )
    elif tf == "H4":
        raw = signal.get("h4Candles") or signal.get("h1Candles")
    else:
        raw = signal.get("d1Candles") or signal.get("h4Candles")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def build_engine_b_summary_for_strategy(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    snapshots = engine_a_ctx.get("engine_snapshots") or {}
    eb = snapshots.get("engineB") or {}
    struct = engine_a_ctx.get("structure_context") or {}
    if not isinstance(struct, dict):
        struct = {}
    has_data = any(
        eb.get(key) is not None for key in ("score", "passed", "direction")
    ) or bool(struct)
    return {
        "available": bool(has_data),
        "passed": eb.get("passed"),
        "score": eb.get("score"),
        "max_score": eb.get("maxScore"),
        "threshold": eb.get("threshold"),
        "normalized_score": eb.get("normalizedScore"),
        "direction": eb.get("direction"),
        "structural_verdict": struct.get("structural_verdict"),
        "bos_confirmed": struct.get("bos_confirmed"),
        "choch_confirmed": struct.get("choch_confirmed"),
    }


def build_engine_b_prompt_context(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    """Compact Engine B block for Opus prompt (projection only)."""
    snapshots = engine_a_ctx.get("engine_snapshots") or {}
    eb = snapshots.get("engineB") or {}
    if not isinstance(eb, dict):
        eb = {}
    struct = engine_a_ctx.get("structure_context") or {}
    if not isinstance(struct, dict):
        struct = {}
    conf = engine_a_ctx.get("engine_b_confidence") or engine_a_ctx.get("confidence") or {}
    if not isinstance(conf, dict):
        conf = {}

    def _zone_level(zone: Any) -> float | None:
        if isinstance(zone, dict):
            return _to_float(
                zone.get("level")
                or zone.get("price")
                or zone.get("mid")
                or zone.get("center")
                or zone.get("top")
                or zone.get("upper")
            )
        return _to_float(zone)

    def _zone_bounds(zone: Any) -> dict[str, float | None] | None:
        if not isinstance(zone, dict):
            return None
        return {
            "lower": _to_float(zone.get("lower")),
            "upper": _to_float(zone.get("upper")),
            "center": _to_float(zone.get("center") or zone.get("mid")),
        }

    def _first_float(*values: Any) -> float | None:
        for value in values:
            parsed = _to_float(value)
            if parsed is not None:
                return parsed
        return None

    nearest_support_zone = struct.get("nearest_support_zone") or conf.get("nearest_support_zone")
    nearest_resistance_zone = struct.get("nearest_resistance_zone") or conf.get("nearest_resistance_zone")

    active_fvgs = struct.get("active_fvgs") or struct.get("activeFvgs") or []
    if not isinstance(active_fvgs, list):
        active_fvgs = []
    nearest_fvg_mid = None
    if active_fvgs:
        first = active_fvgs[0]
        if isinstance(first, dict):
            nearest_fvg_mid = _to_float(
                first.get("midpoint") or first.get("mid") or first.get("center")
            )
            if nearest_fvg_mid is None:
                top = _to_float(first.get("top") or first.get("upper"))
                bot = _to_float(first.get("bottom") or first.get("lower"))
                if top is not None and bot is not None:
                    nearest_fvg_mid = (top + bot) / 2.0

    geometry = engine_a_ctx.get("geometry") or {}
    if not isinstance(geometry, dict):
        geometry = {}

    available = bool(
        struct.get("structural_verdict")
        or struct.get("bos_confirmed") is True
        or struct.get("choch_confirmed") is True
        or struct.get("liquidity_sweep") is True
        or struct.get("nearest_support_zone")
        or struct.get("nearest_resistance_zone")
        or struct.get("ob_at_zone") is True
        or struct.get("fvg_overlap") is True
        or eb.get("score") is not None
        or eb.get("passed") is not None
        or eb.get("structuralVerdict")
    )

    entry_value = _first_float(
        geometry.get("candidate_entry"),
        geometry.get("current_price"),
        conf.get("current_price"),
        struct.get("current_price"),
    )
    execution_sl = _first_float(
        conf.get("execution_sl"),
        struct.get("execution_sl"),
        struct.get("recommended_stop_loss"),
        geometry.get("stop_loss"),
    )
    rr_used = _first_float(
        conf.get("rr_used_for_gate"),
        conf.get("rr"),
        struct.get("rr_used_for_gate"),
        struct.get("rr"),
        geometry.get("rr"),
    )
    rr_required = _first_float(
        conf.get("rr_required"),
        conf.get("style_min_rr"),
        struct.get("rr_required"),
        struct.get("style_min_rr"),
    )
    risk_geometry = _resolved_risk_geometry(
        engine_a_ctx,
        entry=entry_value,
        sl=execution_sl,
        rr=rr_used,
        rr_required=rr_required,
    )

    trigger_expected = _first_present(
        conf.get("trigger_timeframe_expected"),
        struct.get("trigger_timeframe_expected"),
        struct.get("entry_tf"),
    )
    trigger_actual = _first_present(
        conf.get("trigger_timeframe_actual"),
        conf.get("trigger_timeframe"),
        struct.get("trigger_timeframe_actual"),
        struct.get("trigger_timeframe"),
        struct.get("entry_tf"),
    )
    role_defaults: dict[str, Any] = {}
    try:
        from market_structure import resolve_engine_b_tfs

        role_defaults = resolve_engine_b_tfs(
            str(engine_a_ctx.get("asset_class") or ""),
            str(engine_a_ctx.get("analyze_style") or "intraday"),
        )
    except Exception:
        role_defaults = {}

    return {
        "available": available,
        "reviewMode": engine_a_ctx.get("review_mode"),
        "candidateOrigin": engine_a_ctx.get("candidate_origin"),
        "reviewDelta": engine_a_ctx.get("review_delta"),
        "score": eb.get("score"),
        "maxScore": eb.get("maxScore"),
        "threshold": eb.get("threshold"),
        "normalizedScore": eb.get("normalizedScore"),
        "passed": eb.get("passed"),
        "direction": eb.get("direction") or engine_a_ctx.get("direction"),
        "structTf": _first_present(
            conf.get("struct_tf"),
            struct.get("struct_tf"),
            struct.get("structure_timeframe"),
            role_defaults.get("struct"),
        ),
        "zoneTf": _first_present(
            conf.get("zone_tf"), struct.get("zone_tf"), role_defaults.get("zone")
        ),
        "triggerTf": trigger_actual or role_defaults.get("trigger"),
        "atrTf": _first_present(
            conf.get("atr_tf"), struct.get("atr_tf"), role_defaults.get("atr")
        ),
        "biasTf": _first_present(
            conf.get("bias_tf"),
            struct.get("bias_tf"),
            conf.get("macro_sequence_tf"),
            struct.get("macro_sequence_tf"),
        ),
        "triggerTimeframeExpected": trigger_expected,
        "triggerTimeframeActual": trigger_actual,
        "triggerTimeframeGateOk": conf.get(
            "trigger_timeframe_gate_ok",
            struct.get("trigger_timeframe_gate_ok"),
        ),
        "structuralVerdict": struct.get("structural_verdict") or eb.get("structuralVerdict"),
        "bosConfirmed": struct.get("bos_confirmed"),
        "chochConfirmed": struct.get("choch_confirmed"),
        "liquiditySweep": struct.get("liquidity_sweep"),
        "sweepDirection": struct.get("sweep_direction") or struct.get("sweepDirection"),
        "obAtZone": struct.get("ob_at_zone") if "ob_at_zone" in struct else struct.get("obAtZone"),
        "fvgOverlap": struct.get("fvg_overlap") if "fvg_overlap" in struct else struct.get("fvgOverlap"),
        "activeFvgCount": len(active_fvgs),
        "nearestFvgMid": nearest_fvg_mid,
        "fvgTimeframe": struct.get("fvg_timeframe") or struct.get("zone_tf"),
        "fvgReactionConfirmed": struct.get("fvg_reaction_confirmed"),
        "fvgContext": struct.get("fvg_context"),
        "bagState": struct.get("bag_state"),
        "bag": struct.get("bag"),
        "confirmedBagCount": struct.get("confirmed_bag_count", 0),
        "nearestSupport": _zone_level(nearest_support_zone),
        "nearestResistance": _zone_level(nearest_resistance_zone),
        "nearestSupportZone": _zone_bounds(nearest_support_zone),
        "nearestResistanceZone": _zone_bounds(nearest_resistance_zone),
        "breakerLevel": _to_float(
            (struct.get("breaker_block") or {}).get("level")
            if isinstance(struct.get("breaker_block"), dict)
            else struct.get("breaker_block")
        ),
        "structureOk": conf.get("structure_ok", struct.get("structure_ok")),
        "locationOk": conf.get("location_ok", struct.get("location_ok")),
        "entryOk": conf.get("entry_ok", struct.get("entry_ok")),
        "roomOk": conf.get("room_ok", struct.get("room_ok")),
        "rrOk": conf.get("rr_ok", struct.get("rr_ok")),
        "spaceGateOk": conf.get("space_gate_ok", struct.get("space_gate_ok")),
        "structuralSl": _first_float(conf.get("structural_sl"), struct.get("structural_sl")),
        "structuralTp": _first_float(conf.get("structural_tp"), struct.get("structural_tp")),
        "structuralRr": _first_float(conf.get("structural_rr"), struct.get("structural_rr")),
        "structuralSlValid": conf.get("structural_sl_valid", struct.get("structural_sl_valid")),
        "executionSl": execution_sl,
        "executionTp": _first_float(
            conf.get("execution_tp"),
            struct.get("execution_tp"),
            struct.get("recommended_take_profit"),
            geometry.get("take_profit"),
        ),
        "executionTp1": _first_float(conf.get("execution_tp1"), struct.get("execution_tp1")),
        "executionTp2": _first_float(conf.get("execution_tp2"), struct.get("execution_tp2")),
        "executionRr": _first_float(conf.get("execution_rr"), struct.get("execution_rr")),
        "executionRr1": _first_float(conf.get("execution_rr1"), struct.get("execution_rr1")),
        "executionRr2": _first_float(conf.get("execution_rr2"), struct.get("execution_rr2")),
        "rrUsedForGate": rr_used,
        "rrRequired": rr_required,
        "gateScore": _first_float(conf.get("gate_score"), struct.get("gate_score")),
        "gateMaxPossible": _first_float(
            conf.get("gate_max_possible"), struct.get("gate_max_possible")
        ),
        "qualityScore": _first_float(conf.get("quality_score"), struct.get("quality_score")),
        "qualityMaxPossible": _first_float(
            conf.get("quality_max_possible"), struct.get("quality_max_possible")
        ),
        "qualityComponents": conf.get("quality_components")
        or struct.get("quality_components")
        or {},
        **risk_geometry,
        "slSource": conf.get("sl_source") or struct.get("sl_source"),
        "tpSource": conf.get("tp_source") or struct.get("tp_source"),
        "tp1Source": conf.get("tp1_source") or struct.get("tp1_source"),
        "tp2Source": conf.get("tp2_source") or struct.get("tp2_source"),
        "levelMode": conf.get("level_mode") or struct.get("level_mode"),
        "exitStrategy": conf.get("exit_strategy") or struct.get("exit_strategy"),
        "fallbackTpApplied": conf.get("fallback_tp_applied", struct.get("fallback_tp_applied")),
        "fallbackTpReason": conf.get("fallback_tp_reason") or struct.get("fallback_tp_reason"),
        "syntheticRrTpUsed": conf.get("synthetic_rr_tp_used", struct.get("synthetic_rr_tp_used")),
        "runnerTpRequiresStructuralBreak": conf.get(
            "runner_tp_requires_structural_break",
            struct.get("runner_tp_requires_structural_break"),
        ),
        "scaleOutActive": conf.get("scale_out_active", struct.get("scale_out_active")),
        "scaleOutSpaceOk": conf.get("scale_out_space_ok", struct.get("scale_out_space_ok")),
        "scaleOutGuardReason": conf.get("scale_out_guard_reason") or struct.get("scale_out_guard_reason"),
        "tp1MinRr": _first_float(conf.get("tp1_min_rr"), struct.get("tp1_min_rr")),
        "styleMinRr": _first_float(conf.get("style_min_rr"), struct.get("style_min_rr")),
        "entryInsideOpposingZone": conf.get(
            "entry_inside_opposing_zone", struct.get("entry_inside_opposing_zone")
        ),
        "tp1BeforeOpposingZone": conf.get(
            "tp1_before_opposing_zone", struct.get("tp1_before_opposing_zone")
        ),
        "tp1PathClear": conf.get("tp1_path_clear", struct.get("tp1_path_clear")),
        "tp1PathBlockReason": conf.get("tp1_path_block_reason") or struct.get("tp1_path_block_reason"),
        "tp1ClampedToOpposingZone": conf.get(
            "tp1_clamped_to_opposing_zone", struct.get("tp1_clamped_to_opposing_zone")
        ),
        "tp1ClampRejectReason": conf.get("tp1_clamp_reject_reason")
        or struct.get("tp1_clamp_reject_reason"),
        "distanceToSupport": _first_float(conf.get("distance_to_sup"), struct.get("distance_to_sup")),
        "distanceToSupportPct": _first_float(
            conf.get("distance_to_sup_pct"), struct.get("distance_to_sup_pct")
        ),
        "distanceToSupportAtr": _first_float(
            conf.get("distance_to_sup_atr"), struct.get("distance_to_sup_atr")
        ),
        "distanceToResistance": _first_float(conf.get("distance_to_res"), struct.get("distance_to_res")),
        "distanceToResistancePct": _first_float(
            conf.get("distance_to_res_pct"), struct.get("distance_to_res_pct")
        ),
        "distanceToResistanceAtr": _first_float(
            conf.get("distance_to_res_atr"), struct.get("distance_to_res_atr")
        ),
        "executionLevelsValid": conf.get(
            "execution_levels_valid", struct.get("execution_levels_valid")
        ),
        "executionSlTighterThanStructural": conf.get(
            "execution_sl_tighter_than_structural",
            struct.get("execution_sl_tighter_than_structural"),
        ),
        "rr": rr_used,
        "volumeProfileContext": struct.get("profile_vp_context")
        or build_engine_b_profile_vp_context(str(engine_a_ctx.get("asset_class") or "")),
    }


def _review_style_diagnostic(
    style: str,
    screenshot_meta: dict[str, Any] | None,
    timeframe: str,
) -> dict[str, Any]:
    """Surface whether review scoring stayed on the candidate-selected style."""
    meta = screenshot_meta or {}
    candidate_style = (
        str(meta.get("signal_style") or meta.get("scoring_style") or "").strip().lower()
        or None
    )
    chart_tf = str(meta.get("chart_timeframe") or timeframe or "").upper() or None
    matches = candidate_style is None or candidate_style == str(style or "").lower()
    return {
        "review_analyze_style": style,
        "candidate_signal_style": candidate_style,
        "style_matches_candidate": matches,
        "chart_timeframe": chart_tf,
        "scoring_timeframes": list(meta.get("scoring_timeframes") or []),
        "note": (
            None
            if matches
            else (
                f"Engine A review style '{style}' differs from the candidate's "
                f"selected style '{candidate_style}'."
            )
        ),
    }


def _chart_indicator_parity(
    screenshot_meta: dict[str, Any] | None,
    ema_levels: dict[str, Any],
    engine_refs: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Compare chart-drawn (visible-TF) indicators against Engine A's basis.

    Engine A's trend EMAs come from the H4 snapshot, so the chart's visible-TF
    lines are only directly comparable when the chart is on H4. The chart sends
    emaTrend/ema50/ema200/rsi14/atr14/adx14 — drawn at the per-group periods
    Engine A scores with and computed on confirmed bars only — and they are
    compared against Engine A's H4 references here. The legacy "...14"/"50"
    key names are aliases; the actual periods are surfaced via
    engine_a_ema_periods / engine_a_rsi_period. Advisory only — never affects
    scoring.
    """
    refs = engine_refs or {}
    meta = screenshot_meta or {}
    snap = meta.get("chart_snapshot") if isinstance(meta.get("chart_snapshot"), dict) else {}
    chart_ind = snap.get("chartIndicators") if isinstance(snap.get("chartIndicators"), dict) else {}
    chart_tf = str(
        (chart_ind.get("timeframe") if chart_ind else None)
        or meta.get("chart_timeframe")
        or ""
    ).upper() or None
    engine_tf = "H4"
    comparable = bool(chart_tf and chart_tf == engine_tf)
    warnings: list[str] = []
    mismatches: dict[str, Any] = {}

    if chart_ind and comparable:
        def _cmp(name: str, chart_val: Any, eng_val: Any, tol: float) -> None:
            cv = _to_float(chart_val)
            ev = _to_float(eng_val)
            if cv is None or ev is None:
                return
            if abs(cv - ev) > tol:
                mismatches[name] = {"chart": cv, "engineA": ev}

        def _rel(eng_val: Any, frac: float) -> float:
            ev = _to_float(eng_val)
            return max(abs(ev) * frac, 1e-9) if ev is not None else 1e-9

        # Price-scale indicators: relative tolerance.
        _cmp("ema_trend", chart_ind.get("emaTrend"), ema_levels.get("ema21"), _rel(ema_levels.get("ema21"), 0.0025))
        _cmp("ema50", chart_ind.get("ema50"), ema_levels.get("ema50"), _rel(ema_levels.get("ema50"), 0.0025))
        _cmp("ema200", chart_ind.get("ema200"), ema_levels.get("ema200"), _rel(ema_levels.get("ema200"), 0.0025))
        _cmp("atr14", chart_ind.get("atr14"), refs.get("atr14"), _rel(refs.get("atr14"), 0.05))
        # Bounded 0-100 oscillators: absolute tolerance.
        _cmp("rsi14", chart_ind.get("rsi14"), refs.get("rsi14"), 2.0)
        _cmp("adx14", chart_ind.get("adx14"), refs.get("adx14"), 2.0)
        if mismatches:
            warnings.append("chart_indicators_differ_from_engine_a")

    if not chart_ind:
        status = "unavailable"
    elif mismatches:
        status = "values_differ"
    elif comparable:
        status = "ok"
    else:
        status = "not_comparable_timeframe"

    return (
        {
            "chart_timeframe": chart_tf,
            "engine_a_indicator_timeframe": engine_tf,
            "comparable": comparable,
            "chart_indicators_present": bool(chart_ind),
            "status": status,
            "mismatches": mismatches,
            "engine_a_ema_periods": refs.get("ema_periods"),
            "engine_a_rsi_period": refs.get("rsi_period"),
            "rsi_timeframe": refs.get("rsi_tf"),
        },
        warnings,
    )


def assemble_engine_a_context(
    symbol: str,
    timeframe: str,
    *,
    screenshot_meta: dict[str, Any] | None = None,
    resolve_pair_fn: Callable[[str], dict[str, Any] | None] | None = None,
    analyze_pair_fn: Callable[..., dict[str, Any] | None] | None = None,
    btc_bias_fn: Callable[[], str] | None = None,
    origin_signal: dict[str, Any] | None = None,
    naked_analysis_fn: Callable[..., tuple[Any, Any, str | None]] | None = None,
) -> dict[str, Any] | None:
    resolve_pair = resolve_pair_fn or _default_resolve_pair
    analyze_pair = analyze_pair_fn or _default_analyze_pair
    btc_bias = (btc_bias_fn or _default_btc_bias)()

    pair = resolve_pair(symbol)
    if not pair:
        return None

    origin = origin_signal if isinstance(origin_signal, dict) else {}
    style = resolve_chart_review_analyze_style(
        screenshot_meta,
        pair,
        candidate_signal=origin,
    )

    signal = analyze_pair(pair, btc_bias, style=style)
    if not signal:
        return None

    # P1-5: when Engine A scan seed has no real Engine B structure but the chart
    # enabled engine_b overlays (or structure is empty), refresh via naked
    # analysis so server structure_context matches the overlay renderer.
    structure_seed = _pick_engine_b_structure_payload(signal, origin)
    structure_refetch: dict[str, Any] = {"attempted": False, "ok": None}
    overlays = list((screenshot_meta or {}).get("overlays") or [])
    need_structure = not _engine_b_structure_evidence(structure_seed) and (
        "engine_b" in overlays or True  # always try once for review completeness
    )
    if need_structure and callable(naked_analysis_fn):
        try:
            direction = str(
                origin.get("direction") or signal.get("direction") or ""
            ).upper()
            if direction in ("LONG", "SHORT"):
                seed = {
                    "symbol": pair.get("symbol") or symbol,
                    "pair": pair.get("display") or symbol,
                    "display": pair.get("display") or symbol,
                    "type": pair.get("type"),
                    "direction": direction,
                    "style": style,
                    "is_naked": True,
                    "engine": "B",
                }
                res, _pair_obj, err = naked_analysis_fn(seed, overlay_only=True)
                if err:
                    structure_refetch = {"attempted": True, "ok": False, "reason": str(err)}
                elif not isinstance(res, dict):
                    structure_refetch = {
                        "attempted": True,
                        "ok": False,
                        "reason": "naked analysis returned no payload",
                    }
                elif not _engine_b_structure_evidence(res):
                    structure_refetch = {
                        "attempted": True,
                        "ok": False,
                        "reason": "payload carried no structure evidence under either contract",
                    }
                else:
                    signal = dict(signal)
                    signal["engine_b"] = res
                    structure_refetch = {
                        "attempted": True,
                        "ok": True,
                        "evidence": describe_engine_b_structure_evidence(res),
                    }
            else:
                structure_refetch = {
                    "attempted": False,
                    "ok": False,
                    "reason": f"direction {direction!r} is not LONG/SHORT",
                }
        except Exception as exc:  # noqa: BLE001 - never fail the review on structure
            # P1-5: this used to be a bare `pass`, which produced the reported
            # symptom exactly — bands drawn client-side, structure empty
            # server-side, no error surfaced anywhere.
            structure_refetch = {
                "attempted": True,
                "ok": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }

    factor_diag = _merge_factor_diagnostics(dict(signal.get("factorDiagnostics") or {}), signal)
    atr_diag = dict(signal.get("atrDiagnostics") or {})
    data_freshness = dict(signal.get("dataFreshness") or {})
    candle_meta = dict(signal.get("candleFetchMeta") or {})

    price = _to_float(signal.get("price"))
    origin_entry = _to_float(origin.get("entry"))
    if origin_entry is None:
        origin_entry = _to_float(origin.get("price"))
    if origin_entry is None and isinstance(origin.get("entryZone"), (list, tuple)):
        zone_values = [
            value
            for value in (_to_float(item) for item in origin.get("entryZone") or [])
            if value is not None
        ]
        if zone_values:
            origin_entry = sum(zone_values) / len(zone_values)
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

    review_timestamp = datetime.now(timezone.utc).isoformat()
    scan_timestamp = (
        origin.get("timestamp")
        or origin.get("decisionTime")
        or origin.get("scan_timestamp")
        or signal.get("timestamp")
        or review_timestamp
    )
    displacement = (
        abs(price - origin_entry)
        if price is not None and origin_entry is not None
        else None
    )
    origin_score = _to_float(origin.get("confluenceScore"))
    origin_max_score = _to_float(origin.get("maxScore"))
    review_score = _to_float(signal.get("confluenceScore"))
    stamped_policy_context = {
        "timeframePolicyVersion": signal.get("timeframePolicyVersion"),
        "timeframePolicyHash": signal.get("timeframePolicyHash"),
        "policyKey": signal.get("policyKey"),
        "regimeTf": signal.get("regimeTf") or signal.get("regimeTimeframe"),
        "biasTf": signal.get("biasTf") or signal.get("biasTimeframe"),
        "structureTf": signal.get("structureTf") or signal.get("structureTimeframe"),
        "setupTf": signal.get("setupTf") or signal.get("entryTimeframe"),
        "triggerTf": signal.get("triggerTf"),
        "executionTf": signal.get("executionTf") or signal.get("executionTimeframe"),
        "executionMode": signal.get("executionMode") or "live_quote",
        "m5Role": signal.get("m5Role"),
        "m5Policy": signal.get("m5Policy"),
    }
    resolved_policy_context = resolve_timeframe_policy(
        str(signal.get("symbol") or pair.get("symbol") or symbol),
        str(pair.get("type") or ""),
        get_pair_score_group(pair),
        style,
        engine_id="engine_a",
    ).payload()
    policy_context = {
        **resolved_policy_context,
        **{
            key: value
            for key, value in stamped_policy_context.items()
            if value not in (None, "")
        },
    }

    rsi_value, rsi_tf = _rsi_from_signal(signal)

    ctx = {
        # Keep the response bound to the chart request identity. For MT5 spot
        # commodities, ``pair.symbol`` can be a legacy Yahoo futures proxy
        # (for example GC=F) while the requested/displayed chart is XAU/USD.
        "symbol": str(symbol or "").strip() or signal.get("symbol") or pair.get("symbol"),
        "catalog_symbol": pair.get("symbol"),
        "timeframe": timeframe,
        "analyze_style": style,
        "primary_engine": "A",
        "review_type": "engine_a_chart",
        "chart_timeframe": (screenshot_meta or {}).get("chart_timeframe") or timeframe,
        "scoring_profile": signal.get("scoringProfile")
        or (screenshot_meta or {}).get("scoring_profile"),
        "scoring_timeframes": signal.get("scoringTimeframes")
        or (screenshot_meta or {}).get("scoring_timeframes"),
        "momentum_timeframe": signal.get("momentumTimeframe")
        or (screenshot_meta or {}).get("momentum_timeframe"),
        "regime_timeframe": signal.get("regimeTimeframe")
        or (screenshot_meta or {}).get("regime_timeframe"),
        "execution_timeframe": signal.get("executionTimeframe")
        or (screenshot_meta or {}).get("execution_timeframe"),
        "timeframe_policy": policy_context,
        "asset_class": pair.get("type"),
        "asset_group": get_pair_score_group(pair),
        "direction": str(signal.get("direction") or "NONE").upper(),
        "regime": signal.get("regime") or signal.get("regimeName"),
        "scan_timestamp": scan_timestamp,
        "candidate_timestamp": scan_timestamp,
        "review_timestamp": review_timestamp,
        "analysis_timestamp": signal.get("timestamp"),
        "review_mode": "candidate" if origin else "exploratory",
        "candidate_origin": {
            "candidate_id": origin.get("signalId") or origin.get("signal_id") or origin.get("id"),
            "revision": scan_timestamp,
            "direction": str(origin.get("direction") or "NONE").upper(),
            "style": origin.get("horizon") or origin.get("style"),
            "entry": origin_entry,
            "stop_loss": _to_float(origin.get("sl") or origin.get("stopLoss")),
            "take_profit": _to_float(
                origin.get("tp1") or origin.get("tp") or origin.get("takeProfit")
            ),
            "score": origin_score,
            "max_score": origin_max_score,
            "threshold": _to_float(
                origin.get("threshold")
                or origin.get("liveThreshold")
                or origin.get("scanThreshold")
            ),
            "passed": _engine_a_passed(origin),
            "passed_basis": _engine_a_passed_basis(origin),
        } if origin else None,
        "review_delta": {
            "entry_displacement": displacement,
            "score_delta": (
                review_score - origin_score
                if review_score is not None and origin_score is not None
                else None
            ),
            "direction_changed": (
                str(origin.get("direction") or "NONE").upper()
                != str(signal.get("direction") or "NONE").upper()
            ) if origin else None,
        },
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
        "passed_basis": _engine_a_passed_basis(signal),
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
            "atr_d1": _to_float(atr_diag.get("atr_d1") or atr_diag.get("atrD1")),
            "atr_h4": _to_float(atr_diag.get("atr_h4") or atr_diag.get("atrH4")),
            "atr_chart_tf": _to_float(atr_diag.get("atr_chart_tf") or atr_diag.get("atrChartTf")),
            "atr_cache_hit": (candle_meta.get("H4") or {}).get("cacheHit")
            if isinstance(candle_meta.get("H4"), dict)
            else None,
            "atr_freshness_status": None,
            "max_expected_age_seconds": None,
        },
        "geometry": {
            "candidate_entry": origin_entry if origin_entry is not None else price,
            "current_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "risk_points": risk_points,
            "reward_points": reward_points,
            "rr": rr,
            "price_displacement_from_candidate_entry": displacement,
            "sl_tp_source": signal.get("entryMode") or "engine_a_levels",
        },
        "freshness": _build_freshness_block(signal, data_freshness, pair=pair),
        "chart_captured_at": (screenshot_meta or {}).get("captured_at"),
        "screenshot_overlays": list((screenshot_meta or {}).get("overlays") or []),
        "chart_snapshot": dict((screenshot_meta or {}).get("chart_snapshot") or {})
        if isinstance((screenshot_meta or {}).get("chart_snapshot"), dict)
        else {},
        "mismatch_warnings": [],
        "funding_oi": _funding_oi_block(signal),
        "structure_context": _build_structure_context(
            signal,
            origin,
            pair=pair,
            structure_refetch=structure_refetch,
            sl=sl,
            tp1=tp,
            tp2=_to_float(signal.get("tp2")),
        ),
        "ema_levels": _ema_levels(signal, factor_diag),
        "htf_swing_highs": [
            value
            for value in (
                _last_candle_value(signal.get("d1Candles"), "high"),
                _last_candle_value(signal.get("h4Candles"), "high"),
            )
            if value is not None
        ],
        "indicator_snapshots": {"rsi": rsi_value, "rsi_tf": rsi_tf},
        "intermarket": _intermarket_block(signal, factor_diag),
        "news_sentiment": _news_sentiment_block(signal),
    }
    ctx["ohlcv_bars"] = select_ohlcv_bars_for_chart(signal, timeframe, screenshot_meta)
    ctx["engine_snapshots"] = extract_engine_snapshots(signal, ctx)
    ctx["non_visual_context"] = _non_visual_context(signal, factor_diag, ctx)
    ctx["engine_a_non_visual_context"] = ctx["non_visual_context"]
    ctx["score_attribution"] = _score_attribution(signal, factor_diag, ctx)
    ctx["review_style_diagnostic"] = _review_style_diagnostic(style, screenshot_meta, timeframe)
    h4_snap = (signal.get("h4") or {}).get("snap") or {}
    from factor_scoring import _resolve_ema_periods, _resolve_rsi_period

    engine_refs = {
        "rsi14": _to_float(h4_snap.get("rsi")),
        "atr14": _to_float(ctx["atr"].get("atr_h4")) if h4_snap.get("atr") is None else _to_float(h4_snap.get("atr")),
        "adx14": _to_float(factor_diag.get("adx_value") or factor_diag.get("adxValue") or h4_snap.get("adx")),
        "ema_periods": _resolve_ema_periods(ctx.get("asset_group"), str(pair.get("type") or "")),
        "rsi_period": _resolve_rsi_period(ctx.get("asset_group"), str(pair.get("type") or "")),
        "rsi_tf": "H4",
    }
    parity, parity_warnings = _chart_indicator_parity(
        screenshot_meta, ctx["ema_levels"], engine_refs
    )
    ctx["indicator_parity"] = parity
    if parity_warnings:
        ctx["mismatch_warnings"] = list(ctx.get("mismatch_warnings") or []) + parity_warnings
    if isinstance(signal, dict) and (
        signal.get("aseEngine") is True or signal.get("engine") == "ASE"
    ):
        ctx["aseSignal"] = signal
    return ctx


def _factor_score(fd: dict[str, Any], *keys: str) -> float | None:
    fs = fd.get("factorScores") or fd.get("factor_scores")
    if not isinstance(fs, dict):
        fs = fd
    for key in keys:
        val = _to_float(fs.get(key) if isinstance(fs, dict) else None)
        if val is not None:
            return val
        ortho = fs.get("ortho") if isinstance(fs, dict) else None
        val = _to_float(ortho.get(key) if isinstance(ortho, dict) else None)
        if val is not None:
            return val
    return None


def _timeframe_bias(engine_a_ctx: dict[str, Any]) -> dict[str, str | None]:
    fd = engine_a_ctx.get("factor_diagnostics") or {}
    if not isinstance(fd, dict):
        fd = {}
    regime = str(engine_a_ctx.get("regime") or "").strip() or None
    trend = fd.get("trendCoherence") or fd.get("trend_coherence")
    trend_detail = None
    if isinstance(trend, dict):
        trend_detail = trend.get("detail") or trend.get("votes") or trend.get("per_tf")
    trend_label = None
    if isinstance(trend, dict):
        trend_label = trend.get("label") or trend.get("state") or trend.get("alignment")
    elif trend is not None:
        trend_label = str(trend)

    def _tf_label(tf_key: str) -> str | None:
        if isinstance(trend_detail, dict):
            raw = trend_detail.get(tf_key) or trend_detail.get(tf_key.lower())
            if isinstance(raw, dict):
                return str(raw.get("label") or raw.get("state") or raw.get("alignment") or raw.get("direction") or raw)
            if raw is not None:
                return str(raw)
        return None

    directional = engine_a_ctx.get("directional_alignment") or {}
    dir_score = None
    if isinstance(directional, dict):
        ds = directional.get("directionalScore") or directional.get("directional_score")
        if ds is not None:
            dir_score = str(ds)
    base = trend_label or regime or dir_score
    return {
        "D1": _tf_label("d1") or _tf_label("D1") or base,
        "H4": _tf_label("h4") or _tf_label("H4") or base,
        "H1": _tf_label("h1") or _tf_label("H1") or dir_score or base,
    }


def _consistency_status(candle_consistency: dict[str, Any] | None, tf: str) -> str | None:
    if not isinstance(candle_consistency, dict):
        return None
    entry = candle_consistency.get(tf) or candle_consistency.get(tf.upper())
    if isinstance(entry, dict):
        return str(entry.get("status") or "") or None
    if isinstance(entry, str) and entry.strip():
        return entry.strip()
    return None


def _is_policy_normal_stale_1(
    *,
    severity: str | None,
    consistency_status: str | None,
) -> bool:
    sev = str(severity or "").lower()
    if sev != "stale_1_bucket":
        return False
    return consistency_status == "CONFIRMED_ONLY_OK"


def _build_candle_freshness_summary(
    signal: dict[str, Any],
    data_freshness: dict[str, Any],
) -> dict[str, Any]:
    candle_fresh = signal.get("candleFreshness") if isinstance(signal.get("candleFreshness"), dict) else {}
    candle_consistency = (
        signal.get("candleConsistency") if isinstance(signal.get("candleConsistency"), dict) else {}
    )
    per_tf: dict[str, dict[str, Any]] = {}
    for tf in ("D1", "H4", "H1"):
        diag = candle_fresh.get(tf) if isinstance(candle_fresh.get(tf), dict) else {}
        severity = diag.get("stalenessSeverity")
        cons_status = _consistency_status(candle_consistency, tf)
        policy_note = None
        if _is_policy_normal_stale_1(severity=str(severity or ""), consistency_status=cons_status):
            policy_note = "policy_ok_not_stale"
        elif str(severity or "").lower() == "fresh":
            policy_note = "live_fresh"
        elif str(severity or "").lower() in ("stale_multi_bucket", "missing_current_bucket"):
            policy_note = "execution_stale"
        per_tf[tf] = {
            "severity": severity,
            "bucketLag": diag.get("bucketLag"),
            "consistencyStatus": cons_status,
            "policyNote": policy_note,
        }
    return {
        "dataFreshnessAllowed": data_freshness.get("allowed"),
        "perTimeframe": per_tf,
    }


def _execution_abort_reasons(
    data_freshness: dict[str, Any],
    candle_consistency: dict[str, Any] | None,
) -> list[str]:
    """Human-readable abort reasons for AI prompt — execution blocks only."""
    reasons: list[str] = []
    for item in data_freshness.get("blocked") or []:
        if not isinstance(item, dict):
            text = str(item or "").strip()
            if text:
                reasons.append(text)
            continue
        tf = str(item.get("timeframe") or "").upper()
        sev = str(item.get("severity") or "").lower()
        if _is_policy_normal_stale_1(
            severity=sev,
            consistency_status=_consistency_status(candle_consistency, tf),
        ):
            continue
        reasons.append(f"{tf}:{sev}" if tf else sev)
    if not reasons and data_freshness.get("allowed") is False:
        fallback = str(data_freshness.get("reason") or "").strip()
        if fallback:
            reasons.append(fallback)
    return reasons


def _build_freshness_block(
    signal: dict[str, Any],
    data_freshness: dict[str, Any],
    *,
    pair: dict[str, Any],
) -> dict[str, Any]:
    candle_consistency = (
        signal.get("candleConsistency") if isinstance(signal.get("candleConsistency"), dict) else {}
    )
    return {
        "cache_hit": data_freshness.get("cacheHit"),
        "bucket_lag": data_freshness.get("bucketLag"),
        "data_freshness_allowed": data_freshness.get("allowed"),
        "execution_blocked": _execution_abort_reasons(data_freshness, candle_consistency),
        "candleFreshnessSummary": _build_candle_freshness_summary(signal, data_freshness),
        "asset_class": pair.get("type"),
    }


def _abort_reasons(engine_a_ctx: dict[str, Any]) -> list[str]:
    fresh = engine_a_ctx.get("freshness") or {}
    blocked = fresh.get("execution_blocked") if isinstance(fresh, dict) else None
    if isinstance(blocked, list):
        return [str(w) for w in blocked if w]
    warnings = fresh.get("stale_warnings") if isinstance(fresh, dict) else None
    if isinstance(warnings, list):
        return [str(w) for w in warnings if w]
    if isinstance(warnings, str) and warnings.strip():
        return [warnings.strip()]
    return []


def freshness_is_policy_ok(engine_a_ctx: dict[str, Any]) -> bool:
    """True when execution freshness allows and HTF lag is policy-normal only."""
    fresh = engine_a_ctx.get("freshness") or {}
    if not isinstance(fresh, dict):
        return True
    if fresh.get("data_freshness_allowed") is False:
        return False
    summary = fresh.get("candleFreshnessSummary") or {}
    per_tf = summary.get("perTimeframe") if isinstance(summary, dict) else {}
    if not isinstance(per_tf, dict):
        return True
    for tf_entry in per_tf.values():
        if not isinstance(tf_entry, dict):
            continue
        note = tf_entry.get("policyNote")
        severity = str(tf_entry.get("severity") or "").lower()
        if note == "execution_stale":
            return False
        if severity in ("stale_multi_bucket", "missing_current_bucket"):
            return False
        if severity == "stale_1_bucket" and note != "policy_ok_not_stale":
            return False
    return True


def build_engine_a_prompt_context(engine_a_ctx: dict[str, Any]) -> dict[str, Any]:
    """Compact structured Engine A block for the Opus prompt (projection only, no scoring)."""
    snapshots = engine_a_ctx.get("engine_snapshots") or {}
    ea = snapshots.get("engineA") or {}
    fd = engine_a_ctx.get("factor_diagnostics") or {}
    if not isinstance(fd, dict):
        fd = {}
    atr = engine_a_ctx.get("atr") or {}
    geometry = engine_a_ctx.get("geometry") or {}
    ema_levels = engine_a_ctx.get("ema_levels") or {}
    if not isinstance(ema_levels, dict):
        ema_levels = {}
    # VWAP extension lives in the crypto late-trend diagnostics, exposed as
    # cryptoEngineADiagnostics with snake-case sub-keys — not in trendCoherence.
    # Crypto-only; null for other asset classes.
    crypto_diag = fd.get("cryptoEngineADiagnostics") or fd.get("crypto_engine_a_diagnostics") or {}
    if not isinstance(crypto_diag, dict):
        crypto_diag = {}
    vwap_ext = crypto_diag.get("vwap_extended")
    if vwap_ext is None:
        vwap_ext = crypto_diag.get("vwapExtended")
    adx_capture = fd.get("regimeLabelsDualCapture") or {}
    if not isinstance(adx_capture, dict):
        adx_capture = {}
    addon_score = _factor_score(fd, "addon")
    volume_score = _factor_score(fd, "volume", "volumeScore", "volume_score")
    volume_ratio = _to_float(
        _first_present(
            engine_a_ctx.get("volRatio"),
            engine_a_ctx.get("volumeRatio"),
            engine_a_ctx.get("volume_ratio"),
            fd.get("volumeRatio"),
            fd.get("volume_ratio"),
        )
    )
    indicators = engine_a_ctx.get("indicator_snapshots") or {}
    fresh = engine_a_ctx.get("freshness") or {}
    non_visual = engine_a_ctx.get("non_visual_context") or engine_a_ctx.get("engine_a_non_visual_context")
    if not isinstance(non_visual, dict):
        non_visual = _non_visual_context({}, fd, engine_a_ctx)
    score_attribution = engine_a_ctx.get("score_attribution")
    if not isinstance(score_attribution, dict):
        score_attribution = _score_attribution({}, fd, engine_a_ctx)

    # Per-group indicator periods Engine A actually scored with. The chart draws
    # its EMA/RSI lines at these same per-group periods (e.g. forex 26/60/rsi18,
    # crypto 18/40/rsi12) — the legacy ema50/rsi14 field names are aliases — so
    # the model reconciles values against these periods, not the literal names.
    from factor_scoring import _resolve_ema_periods, _resolve_rsi_period

    score_group = engine_a_ctx.get("asset_group")
    asset_type = str(engine_a_ctx.get("asset_class") or "")
    ema_periods = _resolve_ema_periods(score_group, asset_type)
    rsi_period = _resolve_rsi_period(score_group, asset_type)
    components = fd.get("components") if isinstance(fd.get("components"), dict) else {}
    timeframe_policy = engine_a_ctx.get("timeframe_policy") or {}
    if not isinstance(timeframe_policy, dict):
        timeframe_policy = {}
    entry_timeframe = _first_present(
        fd.get("entryTimeframe"),
        fd.get("entry_timeframe"),
        timeframe_policy.get("triggerTf"),
        engine_a_ctx.get("execution_timeframe"),
    )
    risk_geometry = _resolved_risk_geometry(
        engine_a_ctx,
        entry=geometry.get("candidate_entry"),
        sl=geometry.get("stop_loss"),
        rr=geometry.get("rr"),
    )

    return {
        "direction": engine_a_ctx.get("direction"),
        "reviewMode": engine_a_ctx.get("review_mode"),
        "candidateOrigin": engine_a_ctx.get("candidate_origin"),
        "reviewDelta": engine_a_ctx.get("review_delta"),
        "score": ea.get("score") if ea.get("score") is not None else engine_a_ctx.get("confluence_score"),
        "maxScore": ea.get("maxScore") if ea.get("maxScore") is not None else engine_a_ctx.get("max_score_override"),
        "threshold": ea.get("threshold") if ea.get("threshold") is not None else engine_a_ctx.get("threshold"),
        "normalizedScore": ea.get("normalizedScore"),
        "passed": ea.get("passed") if ea.get("passed") is not None else engine_a_ctx.get("passed"),
        "activeFactors": ea.get("activeFactors"),
        "timeframePolicy": {
            key: timeframe_policy.get(key)
            for key in (
                "timeframePolicyVersion",
                "timeframePolicyHash",
                "policyKey",
                "regimeTf",
                "biasTf",
                "structureTf",
                "setupTf",
                "triggerTf",
                "executionTf",
                "executionMode",
                "m5Role",
                "m5Policy",
            )
        },
        "entryTimeframe": entry_timeframe,
        "entryTfOverride": fd.get("entryTfOverride"),
        "entryUsesActiveCandle": fd.get("entryUsesActiveCandle"),
        "activeEntryGate": fd.get("activeEntryGate"),
        "triggerConfirmation": fd.get("triggerConfirmation"),
        "componentScores": components,
        "riskGeometry": risk_geometry,
        "conviction": _to_float(fd.get("conviction") or fd.get("combinedConviction")),
        "abortReasons": _abort_reasons(engine_a_ctx),
        "candleFreshnessSummary": fresh.get("candleFreshnessSummary"),
        "dataFreshnessAllowed": fresh.get("data_freshness_allowed"),
        "timeframeBias": _timeframe_bias(engine_a_ctx),
        "nonVisualContext": non_visual,
        "engineANonVisualContext": non_visual,
        "scoreAttribution": score_attribution,
        "diagnostics": {
            "trendScore": _factor_score(fd, "trend"),
            "momentumScore": _factor_score(fd, "momentum"),
            "locationScore": _factor_score(fd, "location"),
            "volatilityScore": _factor_score(fd, "mean_reversion", "volatility"),
            "addonScore": addon_score,
            "volumeScore": volume_score,
            "volumeRatio": volume_ratio,
            "volumeType": _volume_type_for(engine_a_ctx.get("asset_class") or ""),
            "structureScore": _to_float(fd.get("structure_context_adjustment")),
            "ema50": _to_float(ema_levels.get("ema50")),
            "ema200": _to_float(ema_levels.get("ema200")),
            "dema200": _to_float(ema_levels.get("dema200")),
            "emaTimeframe": "H4",
            "emaTrendPeriod": ema_periods.get("trend"),
            "emaMomentumPeriod": ema_periods.get("momentum"),
            "emaLongPeriod": ema_periods.get("long"),
            "rsiPeriod": rsi_period,
            "rsiTimeframe": indicators.get("rsi_tf"),
            "vwapDistanceAtr": _to_float(crypto_diag.get("vwap_distance_atr")),
            "vwapExtended": vwap_ext if isinstance(vwap_ext, bool) else None,
            "adxD1": _to_float(adx_capture.get("trendStateAdxValue")),
            "adxH4": _to_float(fd.get("adx_value") or fd.get("adxValue")),
            "rsi": _to_float(indicators.get("rsi")),
            "atrD1": _to_float(atr.get("atr_d1")),
            "atrH4": _to_float(atr.get("atr_h4")),
            "rr": _to_float(geometry.get("rr")),
            "sl": _to_float(geometry.get("stop_loss")),
            "tp": _to_float(geometry.get("take_profit")),
            "entry": _to_float(geometry.get("candidate_entry")),
            "provider": engine_a_ctx.get("engine_a_provider"),
            "latestCandleTimestamp": engine_a_ctx.get("latest_candle_ts"),
            "freshnessStatus": atr.get("atr_freshness_status"),
        },
        "intermarket": engine_a_ctx.get("intermarket") or {},
        "newsSentiment": engine_a_ctx.get("news_sentiment") or {},
    }
