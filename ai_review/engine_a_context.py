"""Assemble server-trusted Engine A context for AI chart review."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from ai_review.engine_snapshots import extract_engine_snapshots
from market_structure import build_engine_b_profile_vp_context, sanitize_engine_b_structure_profile_fields
from scoring import get_pair_score_group


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
        "ema50": h4_snap.get("ema50") or trend.get("ema50") or trend.get("ema50_value"),
        "ema200": h4_snap.get("ema200") or trend.get("ema200") or trend.get("ema200_value"),
        "dema200": h4_snap.get("dema200") or trend.get("dema200") or trend.get("dema200_value"),
    }


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


def _default_analyze_pair(pair: dict[str, Any], btc_bias: str, style: str) -> dict[str, Any] | None:
    from scanner import analyze_pair

    return analyze_pair(pair, btc_bias, style=style)


def resolve_chart_review_analyze_style(
    timeframe: str,
    screenshot_meta: dict[str, Any] | None,
    pair: dict[str, Any] | None,
) -> str:
    """Map chart timeframe (or explicit meta) to analyze_pair style."""
    meta = screenshot_meta or {}
    explicit = str(meta.get("analyze_style") or "").strip().lower()
    if explicit in ("scalp", "intraday", "swing"):
        return explicit

    raw_tf = str(meta.get("chart_timeframe") or timeframe or "").strip()
    tf_upper = raw_tf.upper()
    if tf_upper in ("M1", "M2", "M3", "M5", "M15"):
        return "scalp"
    if tf_upper in ("M30", "H1", "H2", "H3", "H4"):
        return "intraday"
    if tf_upper in ("D1", "D2", "W1", "MN", "MONTHLY"):
        return "swing"

    lowered = raw_tf.lower()
    if lowered in ("scalp", "intraday", "swing"):
        return lowered

    ptype = str((pair or {}).get("type") or "").lower()
    if ptype in ("crypto", "forex"):
        return "intraday"
    return "swing"


def _merge_factor_diagnostics(
    factor_diag: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(factor_diag)
    factor_scores = signal.get("factorScores") or signal.get("factor_scores")
    if isinstance(factor_scores, dict) and factor_scores:
        merged["factorScores"] = dict(factor_scores)
    return merged


def _rsi_from_signal(signal: dict[str, Any]) -> float | None:
    for key in ("h4", "h1", "d1"):
        block = signal.get(key)
        if not isinstance(block, dict):
            continue
        snap = block.get("snap")
        if not isinstance(snap, dict):
            continue
        rsi = _to_float(snap.get("rsi"))
        if rsi is not None:
            return rsi
    return None


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


def select_ohlcv_bars_for_chart(
    signal: dict[str, Any],
    timeframe: str,
    screenshot_meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Pick candle series aligned to the chart timeframe for strategy facts."""
    tf = str((screenshot_meta or {}).get("chart_timeframe") or timeframe or "H4").upper()
    if tf in ("M1", "M2", "M3", "M5", "M15"):
        raw = signal.get("m1Candles") or signal.get("m5Candles") or signal.get("h1Candles")
    elif tf in ("M30", "H1", "H2", "H3"):
        raw = signal.get("h1Candles") or signal.get("h4Candles")
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
    struct = engine_a_ctx.get("structure_context") or {}
    if not isinstance(struct, dict):
        struct = {}

    def _zone_level(zone: Any) -> float | None:
        if isinstance(zone, dict):
            return _to_float(zone.get("level") or zone.get("price") or zone.get("top"))
        return _to_float(zone)

    return {
        "score": eb.get("score"),
        "maxScore": eb.get("maxScore"),
        "threshold": eb.get("threshold"),
        "normalizedScore": eb.get("normalizedScore"),
        "passed": eb.get("passed"),
        "direction": eb.get("direction"),
        "structuralVerdict": struct.get("structural_verdict"),
        "bosConfirmed": struct.get("bos_confirmed"),
        "chochConfirmed": struct.get("choch_confirmed"),
        "liquiditySweep": struct.get("liquidity_sweep"),
        "nearestSupport": _zone_level(struct.get("nearest_support_zone")),
        "nearestResistance": _zone_level(struct.get("nearest_resistance_zone")),
        "breakerLevel": _to_float(
            (struct.get("breaker_block") or {}).get("level")
            if isinstance(struct.get("breaker_block"), dict)
            else struct.get("breaker_block")
        ),
        "volumeProfileContext": struct.get("profile_vp_context")
        or build_engine_b_profile_vp_context(str(engine_a_ctx.get("asset_class") or "")),
    }


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

    style = resolve_chart_review_analyze_style(timeframe, screenshot_meta, pair)

    signal = analyze_pair(pair, btc_bias, style=style)
    if not signal:
        return None

    factor_diag = _merge_factor_diagnostics(dict(signal.get("factorDiagnostics") or {}), signal)
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
        "analyze_style": style,
        "chart_timeframe": (screenshot_meta or {}).get("chart_timeframe") or timeframe,
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
        "freshness": _build_freshness_block(signal, data_freshness, pair=pair),
        "chart_captured_at": (screenshot_meta or {}).get("captured_at"),
        "screenshot_overlays": list((screenshot_meta or {}).get("overlays") or []),
        "mismatch_warnings": [],
        "funding_oi": _funding_oi_block(signal),
        "structure_context": sanitize_engine_b_structure_profile_fields(
            signal.get("engine_b") if isinstance(signal.get("engine_b"), dict) else {},
            str(pair.get("type") or ""),
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
        "indicator_snapshots": {"rsi": _rsi_from_signal(signal)},
        "intermarket": _intermarket_block(signal, factor_diag),
        "news_sentiment": _news_sentiment_block(signal),
    }
    ctx["ohlcv_bars"] = select_ohlcv_bars_for_chart(signal, timeframe, screenshot_meta)
    ctx["engine_snapshots"] = extract_engine_snapshots(signal, ctx)
    ctx["non_visual_context"] = _non_visual_context(signal, factor_diag, ctx)
    ctx["engine_a_non_visual_context"] = ctx["non_visual_context"]
    ctx["score_attribution"] = _score_attribution(signal, factor_diag, ctx)
    return ctx


def _factor_score(fd: dict[str, Any], *keys: str) -> float | None:
    fs = fd.get("factorScores") or fd.get("factor_scores")
    if not isinstance(fs, dict):
        fs = fd
    for key in keys:
        val = _to_float(fs.get(key) if isinstance(fs, dict) else None)
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
                return str(raw.get("label") or raw.get("state") or raw.get("alignment") or raw)
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
    trend = fd.get("trendCoherence") or fd.get("trend_coherence") or {}
    vwap_ext = None
    if isinstance(trend, dict):
        vwap_ext = trend.get("vwapExtended") or trend.get("vwap_extended")
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

    return {
        "direction": engine_a_ctx.get("direction"),
        "score": ea.get("score") if ea.get("score") is not None else engine_a_ctx.get("confluence_score"),
        "maxScore": ea.get("maxScore") if ea.get("maxScore") is not None else engine_a_ctx.get("max_score_override"),
        "threshold": ea.get("threshold") if ea.get("threshold") is not None else engine_a_ctx.get("threshold"),
        "normalizedScore": ea.get("normalizedScore"),
        "passed": ea.get("passed") if ea.get("passed") is not None else engine_a_ctx.get("passed"),
        "activeFactors": ea.get("activeFactors"),
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
            "volatilityScore": _factor_score(fd, "mean_reversion", "volatility"),
            "addonScore": addon_score,
            "volumeScore": volume_score,
            "volumeRatio": volume_ratio,
            "volumeType": _volume_type_for(engine_a_ctx.get("asset_class") or ""),
            "structureScore": _to_float(fd.get("structure_context_adjustment")),
            "vwapDistanceAtr": _to_float(
                trend.get("vwapDistanceAtr") if isinstance(trend, dict) else None
            ),
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
