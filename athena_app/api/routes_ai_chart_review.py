"""Flask route for read-only AI chart review."""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from typing import Any

from flask import jsonify, request
from config import get_ai_review_provider

from athena_ai.ai_review_payload_builder import (
    build_strategy_layer,
    render_strategy_block_for_prompt,
)

from ai_review.concordance import compute_engine_a_ai_concordance
from ai_review.context_diagnostics import (
    build_context_diagnostics,
    sanitize_ai_review_missing_context,
)
from ai_review.engine_a_context import (
    assemble_engine_a_context,
    build_engine_b_summary_for_strategy,
)
from ai_review.engine_a_verdict import build_engine_a_verdict_comparison
from ai_review.freshness import classify_atr_freshness
from ai_review.normalizer import normalize_chart_review_response
from ai_review.payload_schema import build_payload
from ai_review.persistence import (
    ensure_schema,
    find_recent_review_by_hash,
    record_review,
)
from ai_review.prompt_builder import build_chart_review_prompt
from ai_review.provider_meta import (
    ProviderChartReviewError,
    apply_parse_fallback,
    provider_error_response,
    provider_meta_from_persisted,
)
from ai_review.providers.router import run_chart_review
from ai_review.summary import build_ai_review_summary
from ai_review.timeframe_routing import resolve_timeframe_route
from ai_review.timestamp_contract import evaluate_timestamp_mismatch
from ai_review.validation import decode_screenshot_bytes, validate_request

log = logging.getLogger("sentinel.ai_chart_review")


def _resolve_provider_name(
    data: dict[str, Any],
    cfg: dict[str, Any],
    root_cfg: dict[str, Any] | None = None,
) -> str:
    provider = data.get("provider")
    if provider in (None, "", "default", "none"):
        provider = None
    return get_ai_review_provider(root_cfg or cfg, requested=provider)


def _attach_review_input_meta(
    response: dict[str, Any],
    *,
    engine_a_ctx: dict[str, Any],
) -> None:
    structure_context = engine_a_ctx.get("structure_context")
    route = response.get("timeframeRoute") or response.get("timeframe_route") or {}
    has_engine_a = "passed" in engine_a_ctx
    has_engine_b = isinstance(structure_context, dict) and bool(structure_context)
    response["reviewInputMeta"] = {
        "symbol": engine_a_ctx.get("symbol"),
        "signalEngine": "A" if has_engine_a else "B" if has_engine_b else "unknown",
        "signalTimeframe": engine_a_ctx.get("timeframe"),
        "chartTimeframe": engine_a_ctx.get("chart_timeframe") or engine_a_ctx.get("timeframe"),
        "hasEngineASignal": has_engine_a,
        "hasEngineBOverlay": has_engine_b,
        "hasChartImage": True,
        "timeframeRouteApplied": bool(isinstance(route, dict) and route.get("enabled")),
    }


def _attach_review_summary(
    response: dict[str, Any],
    *,
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    concordance: dict[str, Any],
    provider_meta: dict[str, Any],
    mismatch_warnings: list[str],
    diagnostic_ai_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshots = engine_a_ctx.get("engine_snapshots")
    diagnostic_source = diagnostic_ai_review or ai_review
    clean_ai_review = sanitize_ai_review_missing_context(engine_a_ctx, diagnostic_source)
    response["ai_review"] = clean_ai_review
    structured = clean_ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        structured = {}
    model_summary = structured.get("aiReviewSummary") or structured.get("ai_review_summary")
    model_comparison = structured.get("engineAVerdictComparison") or structured.get(
        "engine_a_verdict_comparison"
    )
    summary = build_ai_review_summary(
        engine_a_ctx,
        clean_ai_review,
        concordance,
        provider_meta,
        engine_snapshots=snapshots,
        mismatch_warnings=mismatch_warnings,
        model_summary=model_summary if isinstance(model_summary, dict) else None,
    )
    verdict_comparison = build_engine_a_verdict_comparison(
        engine_a_ctx,
        clean_ai_review,
        model_comparison=model_comparison if isinstance(model_comparison, dict) else None,
        engine_snapshots=snapshots,
    )
    response["aiReviewSummary"] = summary
    response["ai_review_summary"] = summary
    response["engineAVerdictComparison"] = verdict_comparison
    response["engine_a_verdict_comparison"] = verdict_comparison
    ctx_diag = build_context_diagnostics(engine_a_ctx, diagnostic_source)
    response.update(ctx_diag)
    response["derivativesContext"] = ctx_diag.get("fundingOi")
    response["derivatives_context"] = ctx_diag.get("fundingOi")
    response["nonVisualContext"] = ctx_diag.get("nonVisualContext")
    response["engineANonVisualContext"] = ctx_diag.get("engineANonVisualContext")
    response["scoreAttribution"] = ctx_diag.get("scoreAttribution")
    response["engineAScoreAttribution"] = ctx_diag.get("engineAScoreAttribution")
    response["engine_a_non_visual_context"] = ctx_diag.get("engineANonVisualContext")
    response["engine_a_score_attribution"] = ctx_diag.get("engineAScoreAttribution")
    plan = clean_ai_review.get("suggestedTradePlan") or clean_ai_review.get("suggested_trade_plan")
    if isinstance(plan, dict):
        response["suggestedTradePlan"] = plan
        response["suggested_trade_plan"] = plan
    return response


def _attach_timeframe_route(
    response: dict[str, Any],
    *,
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    routing_cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    response_ctx = response.get("engine_a_context")
    if not isinstance(response_ctx, dict):
        response_ctx = engine_a_ctx
        response["engine_a_context"] = response_ctx
    comparison = response.get("engineAVerdictComparison") or response.get(
        "engine_a_verdict_comparison"
    )
    route = resolve_timeframe_route(
        asset_group=response_ctx.get("asset_group"),
        context_tf=response_ctx.get("timeframe") or response_ctx.get("chart_timeframe"),
        ai_review=ai_review,
        verdict_comparison=comparison if isinstance(comparison, dict) else None,
        cfg=routing_cfg,
    )
    response_ctx["timeframe_route"] = route
    response["timeframeRoute"] = route
    response["timeframe_route"] = route
    _attach_review_input_meta(response, engine_a_ctx=response_ctx)
    return response


def _build_strategy_layer_safely(
    *,
    cfg: dict[str, Any],
    engine_a_ctx: dict[str, Any],
    symbol: str,
    timeframe: str,
    screenshot_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the additive strategy-playbook layer behind STRATEGY_LAYER_ENABLED.

    Read-only and best-effort: any failure here returns None and leaves the
    rest of the AI chart-review pipeline untouched. Never executes trades,
    never mutates engine_a_ctx.
    """
    if not cfg.get("STRATEGY_LAYER_ENABLED", True):
        return None
    try:
        limit = int(cfg.get("STRATEGY_LAYER_OHLCV_LIMIT") or 80)
        ohlcv_window = engine_a_ctx.get("ohlcv_bars")
        if not isinstance(ohlcv_window, list) or not ohlcv_window:
            ohlcv_window = None
        overlays = (screenshot_meta or {}).get("overlays") or []
        engine_b_summary = build_engine_b_summary_for_strategy(engine_a_ctx)
        if isinstance(overlays, list) and "engine_b" not in overlays:
            engine_b_summary = {**engine_b_summary, "available": False}
        return build_strategy_layer(
            engine_a_ctx=engine_a_ctx,
            ohlcv_window=ohlcv_window,
            engine_b_summary=engine_b_summary,
            engine_d_summary=None,
            symbol=symbol,
            timeframe=timeframe,
            asset_group=engine_a_ctx.get("asset_group"),
            direction=engine_a_ctx.get("direction"),
            ohlcv_limit=limit,
        )
    except Exception:
        log.exception(
            "Strategy layer build failed for %s %s; continuing without it",
            symbol,
            timeframe,
        )
        return None


def register_ai_chart_review_routes(app, runtime: SimpleNamespace) -> None:
    ensure_schema(getattr(runtime, "AUDIT_DB", None))

    @app.post("/api/ai/chart-review")
    def api_ai_chart_review():
        cfg = runtime.CONFIG["AI_CHART_REVIEW"]
        if not cfg.get("ENABLED"):
            return jsonify({"error": "AI chart review disabled"}), 503

        data = request.get_json(silent=True) or {}
        err = validate_request(data, cfg)
        if err:
            return jsonify({"error": err.message}), err.status

        symbol = str(data["symbol"]).strip()
        timeframe = str(data["timeframe"]).strip()
        screenshot_meta = dict(data.get("screenshot_meta") or {})
        provider = _resolve_provider_name(data, cfg, runtime.CONFIG)

        engine_a_ctx = assemble_engine_a_context(
            symbol,
            timeframe,
            screenshot_meta=screenshot_meta,
            resolve_pair_fn=getattr(runtime, "resolve_pair_fn", None),
            analyze_pair_fn=getattr(runtime, "analyze_pair_fn", None),
            btc_bias_fn=getattr(runtime, "btc_bias_fn", None),
        )
        if engine_a_ctx is None:
            return jsonify({"error": "Engine A returned no result"}), 422

        atr = engine_a_ctx.setdefault("atr", {})
        freshness = classify_atr_freshness(
            atr.get("atr_tf"),
            atr.get("atr_age_seconds"),
            atr.get("atr_confirmed_only"),
        )
        atr["atr_freshness_status"] = freshness.get("status")
        atr["max_expected_age_seconds"] = freshness.get("max_expected_age_seconds")

        mismatch_warnings = evaluate_timestamp_mismatch(engine_a_ctx, screenshot_meta, cfg)
        overlays = screenshot_meta.get("overlays") or []
        if isinstance(overlays, list) and "engine_b" in overlays:
            struct = engine_a_ctx.get("structure_context") or {}
            if not isinstance(struct, dict) or not any(
                struct.get(k)
                for k in (
                    "structural_verdict",
                    "nearest_support_zone",
                    "nearest_resistance_zone",
                    "bos_data",
                    "choch_data",
                )
            ):
                mismatch_warnings.append(
                    "engine_b_overlays_enabled_but_server_structure_context_empty"
                )
        engine_a_ctx["mismatch_warnings"] = mismatch_warnings
        engine_a_ctx["chart_captured_at"] = screenshot_meta.get("captured_at")

        screenshot_bytes = decode_screenshot_bytes(str(data["screenshot_base64"]))
        screenshot_hash = hashlib.sha256(screenshot_bytes).hexdigest()[:16]

        dedup = find_recent_review_by_hash(
            symbol,
            timeframe,
            screenshot_hash,
            int(cfg.get("DEDUP_WINDOW_SECONDS") or 60),
            audit_db=getattr(runtime, "AUDIT_DB", None),
        )
        if dedup:
            dedup["dedup_hit"] = True
            dedup_engine_ctx = dedup.get("engine_a_context") or engine_a_ctx
            dedup_ai_raw = dedup.get("ai_review") or {}
            dedup_ai = sanitize_ai_review_missing_context(
                dedup_engine_ctx,
                dedup_ai_raw,
            )
            dedup["ai_review"] = dedup_ai
            dedup["concordance"] = compute_engine_a_ai_concordance(
                dedup_engine_ctx,
                dedup_ai,
                cfg=cfg,
            )
            pmeta = provider_meta_from_persisted(
                provider=str(dedup.get("provider") or provider),
                model=str(dedup.get("model") or ""),
                ai_review=dedup_ai,
            )
            dedup["selectedProvider"] = provider
            dedup["fallbackUsed"] = bool(pmeta.get("fallback_used"))
            dedup["fallback_used"] = dedup["fallbackUsed"]
            _attach_review_summary(
                dedup,
                engine_a_ctx=dedup_engine_ctx,
                ai_review=dedup_ai,
                concordance=dedup.get("concordance") or {},
                provider_meta=pmeta,
                mismatch_warnings=list(dedup.get("mismatch_warnings") or []),
                diagnostic_ai_review=dedup_ai_raw,
            )
            _attach_timeframe_route(
                dedup,
                engine_a_ctx=dedup_engine_ctx,
                ai_review=dedup_ai,
                routing_cfg=runtime.CONFIG.get("TIMEFRAME_ROUTING"),
            )
            return jsonify(runtime.json_safe(dedup))

        prompt = build_chart_review_prompt(engine_a_ctx)
        strategy_layer = _build_strategy_layer_safely(
            cfg=cfg,
            engine_a_ctx=engine_a_ctx,
            symbol=symbol,
            timeframe=timeframe,
            screenshot_meta=screenshot_meta,
        )
        if strategy_layer:
            try:
                strategy_block = render_strategy_block_for_prompt(strategy_layer)
                if strategy_block:
                    prompt = prompt + "\n" + strategy_block
            except Exception:
                log.exception("Failed to render strategy block; using unmodified prompt")
        payload = build_payload(
            data,
            engine_a_ctx,
            prompt=prompt,
            mismatch_warnings=mismatch_warnings,
            strategy_layer=strategy_layer,
        )

        try:
            raw = run_chart_review(provider, payload)
        except (
            ProviderChartReviewError,
            PermissionError,
            RuntimeError,
            NotImplementedError,
            ValueError,
        ) as exc:
            body, status = provider_error_response(exc, provider=provider)
            return jsonify(body), status

        normalized_raw = normalize_chart_review_response(raw.get("raw_text") or "")
        normalized = sanitize_ai_review_missing_context(engine_a_ctx, normalized_raw)
        concordance = compute_engine_a_ai_concordance(
            engine_a_ctx, normalized, cfg=cfg
        )

        provider_meta = apply_parse_fallback(
            {
                "provider": raw.get("provider") or provider,
                "model": raw.get("model"),
                "provider_status": raw.get("provider_status") or "success",
                "fallback_used": bool(raw.get("fallback_used")),
                "latency_ms": raw.get("latency_ms"),
            },
            normalized,
        )

        response: dict[str, Any]
        if cfg.get("PERSIST_REVIEWS", True):
            response = record_review(
                symbol=symbol,
                timeframe=timeframe,
                asset_group=engine_a_ctx.get("asset_group"),
                provider=str(provider_meta.get("provider") or provider),
                model=str(provider_meta.get("model") or cfg.get("ANTHROPIC_MODEL") or ""),
                latency_ms=raw.get("latency_ms"),
                screenshot_hash=screenshot_hash,
                screenshot_bytes=len(screenshot_bytes),
                screenshot_meta=screenshot_meta,
                engine_a_context=engine_a_ctx,
                ai_review=normalized,
                concordance=concordance,
                mismatch_warnings=mismatch_warnings,
                audit_db=getattr(runtime, "AUDIT_DB", None),
            )
        else:
            response = {
                "review_id": None,
                "provider": provider_meta.get("provider") or provider,
                "model": provider_meta.get("model"),
                "latency_ms": raw.get("latency_ms"),
                "engine_a_context": engine_a_ctx,
                "ai_review": normalized,
                "concordance": concordance,
                "timestamps": {
                    "scan_timestamp": engine_a_ctx.get("scan_timestamp"),
                    "chart_captured_at": engine_a_ctx.get("chart_captured_at"),
                    "latest_candle_ts": engine_a_ctx.get("latest_candle_ts"),
                },
                "mismatch_warnings": mismatch_warnings,
                "dedup_hit": False,
            }

        response["selectedProvider"] = raw.get("selectedProvider") or provider
        response["fallbackUsed"] = bool(raw.get("fallbackUsed") or provider_meta.get("fallback_used"))
        response["fallback_used"] = response["fallbackUsed"]
        if raw.get("providerFailure"):
            response["providerFailure"] = raw.get("providerFailure")
            response["provider_failure"] = raw.get("providerFailure")

        _attach_review_summary(
            response,
            engine_a_ctx=engine_a_ctx,
            ai_review=normalized,
            concordance=concordance,
            provider_meta=provider_meta,
            mismatch_warnings=mismatch_warnings,
            diagnostic_ai_review=normalized_raw,
        )
        _attach_timeframe_route(
            response,
            engine_a_ctx=engine_a_ctx,
            ai_review=normalized,
            routing_cfg=runtime.CONFIG.get("TIMEFRAME_ROUTING"),
        )

        if strategy_layer is not None:
            response["strategy_layer"] = strategy_layer
            response["strategyLayer"] = strategy_layer

        return jsonify(runtime.json_safe(response))
